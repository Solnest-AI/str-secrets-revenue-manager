#!/usr/bin/env python3
"""The flywheel gate. Runs on EVERY call, before any pricing opinion is formed.

Ryan-stated 2026-09-19 (PRD D3): the flywheel is how we do revenue management. All four
spokes are walked in the framework's order, every run.

**RULING REVERSED 2026-09-20 (PRD D12, supersedes D4).** A missing spoke used to skip the
listing outright. It no longer does: the run prices anyway and marks the missing spokes
LOUDLY at the top of the card, before any number. The reason is the teaching build.
RankBreeze supplies two of the four spokes, it is a paid tool, and most of a 100-person
class will not have it. Under the old rule every one of those people got four lines of
explanation and no recommendation at all.

DO NOT "fix" this back to skipping. The old rule is one day old and was deliberately
overturned. A transcript from 2026-09-19 that says skip is stale.

ONE SPOKE STILL BLOCKS, and it is not a policy choice. Without the PMS calendar there is
no calendar to price: `bookings` missing is an absence of input, not a withheld opinion.
Everything else degrades.

    Visibility -> Bookings -> Reviews -> Ranking

framework.md 6.1: "Visibility comes BEFORE pricing. You cannot charge premium rates if
nobody sees the listing." 6.8: when a property is not booking, "check ranking FIRST ...
If it's on page 5+, pricing isn't the primary problem."

A recommendation issued without those checks contradicts the logic it claims to apply,
which is why an unreadable spoke is a refusal and not a warning.

THE DIAGNOSIS IS THE BENCHMARK, NOT THE NUMBER (PRD FW3)
--------------------------------------------------------
RankBreeze returns every funnel metric beside its similar-listings value. The raw
number cannot tell you anything; the gap can. Measured on a live listing 2026-09-19:

    impressions  1,465 vs 1,389  +76      healthy
    CTR          16.32% vs 16.04% +0.28   healthy
    views        242 vs 228      +14      healthy
    booking rate 4.17% vs 29.87% -25.7    BREAK
    conversion   0.61% vs 4.86%  -4.25    (downstream of the break)

Read as "low visibility" that listing gets a price cut. Read correctly it is seen,
clicked and not booked, which framework 6.9 calls a pricing or content problem. The
opposite prescription. So we walk the funnel in order and report the FIRST stage that
trails its benchmark: everything upstream of it is proven healthy.

Pure functions. No network, no file I/O, no API key. The caller fetches; this decides.
"""
from __future__ import annotations

import math

from _calendar import pms_status, validate_calendar

# The funnel in order. A break at stage N means every stage before it is healthy.
# Keys are the fields RankBreeze's similar_listings_comparison returns.
FUNNEL = [
    ("first_page_impressions", "nobody sees it in search"),
    ("click_through_rate", "they see it and do not click"),
    ("view", "they click and the page does not hold them"),
    ("wishlist", "they look and do not save it"),
    ("booking_rate", "they engage and do not book"),
    ("conversion_rate", "they reach the end and do not convert"),
]

# A stage trails its benchmark when it is more than this far below it, relative.
# 0.25 keeps ordinary noise out: the live example above moved +5% on impressions and
# -86% on booking rate, so the signal is not subtle when it is real.
SHORTFALL = 0.25

SPOKES = ("visibility", "bookings", "reviews", "ranking")


def _num(value):
    """A metric value. Strings arrive with commas and percent signs."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").strip()
        try:
            number = float(cleaned)
            return number if math.isfinite(number) else None
        except ValueError:
            return None
    return None


def funnel_diagnosis(comparison: dict, stages=None) -> dict:
    """Walk the funnel in order and name the FIRST stage that trails its benchmark.

    `comparison` is RankBreeze's `similar_listings_comparison`: each key carries
    {"listing": x, "similar_listings": y, "difference": d}.

    Returns the break stage, what it means, and the stages proven healthy before it.
    A stage with no benchmark is `unknown` and stops the walk, because we cannot claim
    the stages after it are the problem when we could not check this one.
    """
    # A source may declare which stages it measures (IntelliHost has impressions, click rate and
    # click-to-book, no views or wishlists). Declared stages are walked in funnel order; with no
    # declaration all six are required, which is RankBreeze's contract, unchanged.
    walk = [(k, m) for k, m in FUNNEL if stages is None or k in stages]
    healthy, stages = [], []
    for key, meaning in walk:
        row = comparison.get(key) if isinstance(comparison, dict) else None
        row = row if isinstance(row, dict) else {}
        mine = _num(row.get("listing"))
        theirs = _num(row.get("similar_listings"))
        if mine is None or theirs is None:
            return {"verdict": "unknown", "stage": key, "meaning": meaning,
                    "healthy_through": healthy, "stages": stages,
                    "why": f"no benchmark for {key}; cannot say whether the stages "
                           "after it are the problem"}
        stages.append({"stage": key, "listing": mine, "similar": theirs,
                       "delta": mine - theirs})
        # A benchmark of zero cannot be trailed. Both at zero is not a break.
        if theirs > 0 and mine < theirs * (1.0 - SHORTFALL):
            return {"verdict": "break", "stage": key, "meaning": meaning,
                    "healthy_through": healthy, "stages": stages,
                    "why": f"{key} is {mine:g} against a comp-set {theirs:g}"}
        healthy.append(key)
    return {"verdict": "healthy", "stage": None, "meaning": None,
            "healthy_through": healthy, "stages": stages,
            "why": "every funnel stage is at or above its comp set"}


def spoke_visibility(summary_row: dict | None) -> dict:
    """RankBreeze get_listing_metrics_summary. The funnel plus its benchmarks.

    NOT get_listing_metrics: that one returns empty funnel arrays even when the
    integration is active, which is how the funnel was reported dead for a whole day.
    """
    if not summary_row:
        return _fail("visibility", "no RankBreeze summary row for this listing")
    status = str(summary_row.get("integration_status") or "").lower()
    if status != "active":
        return _fail("visibility",
                     f"RankBreeze integration_status is {status or 'missing'!r}, so the "
                     "funnel is not being collected. Empty is NOT zero.")
    comparison = summary_row.get("similar_listings_comparison") or {}
    if not comparison:
        return _fail("visibility", "no similar_listings_comparison; the benchmark IS "
                                   "the diagnosis, so a bare number cannot replace it")
    diag = funnel_diagnosis(comparison, summary_row.get("stages"))
    if diag["verdict"] == "unknown":
        return _fail("visibility", diag["why"])
    return {"spoke": "visibility", "ok": True, "diagnosis": diag,
            "detail": diag["why"]}


def spoke_bookings(pms_rows, pl_rows=None) -> dict:
    """Hospitable is ground truth (PRD D9). PriceLabs cannot see a pending direct
    booking that holds the PMS calendar: measured 8 of 15 nights against 3 of 15."""
    if pms_rows is None:
        return _fail("bookings", "the PMS calendar could not be read, and the PMS is "
                                 "the ground truth for occupancy")
    if not pms_rows:
        return _fail("bookings", "the PMS returned zero calendar rows for this window; "
                                 "that is a read failure, not an empty calendar")
    try:
        validate_calendar(pms_rows, "PMS", pms_status)
    except (TypeError, ValueError) as exc:
        return _fail("bookings", str(exc))
    return {"spoke": "bookings", "ok": True, "nights": len(pms_rows),
            "detail": f"{len(pms_rows)} PMS calendar nights"}


def spoke_reviews(reviews, rating=None) -> dict:
    """framework 6.1 reviews spoke, 6.9 targets 4.8+ and calls below 4.6 a ranking
    problem. Sourced from the PMS (hospitable_list_reviews)."""
    if not isinstance(reviews, list):
        return _fail("reviews", "the reviews spoke could not be read")
    score = _num(rating)
    out = {"spoke": "reviews", "ok": True, "count": len(reviews), "rating": score}
    if score is None:
        out["detail"] = f"{len(reviews)} reviews, no overall rating available"
    elif score < 4.6:
        out["detail"] = (f"rating {score:g} is below 4.6, which framework 6.9 calls a "
                         "RANKING problem, not a pricing one")
        out["flag"] = "rating_below_ranking_threshold"
    else:
        out["detail"] = f"rating {score:g} across {len(reviews)} reviews"
    return out


def spoke_ranking(rows) -> dict:
    """RankBreeze get_listing_rankings. Works even when the Airbnb integration is off.
    framework 6.8: on page 5+, pricing is not the primary problem."""
    if rows is None:
        return _fail("ranking", "the ranking spoke could not be read")
    if not rows:
        return _fail("ranking", "no ranking rows returned; absence of rows is not "
                                "evidence of a good position")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        return _fail("ranking", "ranking rows are not readable objects")
    page_numbers = [_num(r.get("page")) for r in rows]
    if any(p is None or p < 1 or not p.is_integer() for p in page_numbers):
        return _fail("ranking", "one or more ranking rows have no readable positive page number")
    pages = [int(p) for p in page_numbers]
    positions = [_num(r.get("position")) for r in rows if _num(r.get("position")) is not None]
    worst = max(pages) if pages else None
    out = {"spoke": "ranking", "ok": True, "rows": len(rows),
           "worst_page": worst,
           "best_position": min(positions) if positions else None}
    if worst is not None and worst >= 5:
        out["detail"] = (f"appears as deep as page {worst}. framework 6.8: visibility "
                         "is the problem before pricing is")
        out["flag"] = "buried_in_search"
    else:
        out["detail"] = f"pages {min(pages) if pages else '?'}-{worst}, {len(rows)} rows"
    return out


def _fail(spoke: str, why: str) -> dict:
    return {"spoke": spoke, "ok": False, "detail": why}


# The only spoke whose absence removes the INPUT rather than the context.
BLOCKING_SPOKE = "bookings"

MARKET_ABSENT = {"ok": False,
                 "detail": "PriceLabs Market Research not available on this account; "
                           "seasonality and lead-time bands are the framework defaults"}


def gate(listing_label: str, visibility: dict, bookings: dict, reviews: dict,
         ranking: dict, market: dict | None = None) -> dict:
    """The gate. All four spokes, framework order, every run.

    Verdicts:
      analysable  every spoke read
      degraded    one or more of visibility / reviews / ranking could not be read. The
                  recommendation still goes out, with the gaps named FIRST (PRD D12).
      blocked     the PMS calendar could not be read, so there is nothing to price.

    `market` is the optional PriceLabs Market Research layer (PRD D13). It is NOT a
    spoke and never gates anything: on if present, one line when absent.
    """
    spokes = {"visibility": visibility, "bookings": bookings,
              "reviews": reviews, "ranking": ranking}
    missing = [name for name in SPOKES if not (spokes[name] or {}).get("ok")]
    result = {"listing": listing_label, "spokes": spokes,
              "order": list(SPOKES), "failed": missing,
              "market": market if market else dict(MARKET_ABSENT)}
    if BLOCKING_SPOKE in missing:
        result["verdict"] = "blocked"
        result["why"] = ("cannot price: "
                         f"{(spokes[BLOCKING_SPOKE] or {}).get('detail', 'the PMS calendar could not be read')}. "
                         "Without the calendar there are no dates and no occupancy to "
                         "price against.")
        return result
    if missing:
        result["verdict"] = "degraded"
        result["why"] = ("PRICED WITHOUT " + ", ".join(
            f"{n} ({(spokes[n] or {}).get('detail', 'unreadable')})" for n in missing))
    else:
        result["verdict"] = "analysable"
    diag = ((visibility or {}).get("diagnosis") or {})
    flags = [s.get("flag") for s in spokes.values() if s and s.get("flag")]
    result["flags"] = flags
    if not diag:
        result["headline"] = ("no funnel data, so nothing is known about whether guests "
                              "see this listing. Any pricing move is unvalidated by the "
                              "visibility spoke.")
    elif diag.get("verdict") == "break":
        result["headline"] = (f"funnel breaks at {diag['stage']}: {diag['meaning']}. "
                              f"{len(diag['healthy_through'])} stage(s) upstream are "
                              "healthy.")
    else:
        result["headline"] = ("funnel healthy against its comp set; a pricing question "
                              "is legitimate here")
    return result


def render(result: dict) -> str:
    """The flywheel chain, printed for every listing (PRD FW4)."""
    lines = [f"## flywheel {result['listing']}  [{result['verdict']}]"]
    # A degraded or blocked run leads with what is MISSING, before any number. The
    # operator sees the gap before the recommendation, not after it.
    if result["verdict"] in ("degraded", "blocked"):
        lines.append(f"  !! {result['why']}")
    for name in result["order"]:
        s = result["spokes"].get(name) or {}
        mark = "ok  " if s.get("ok") else "FAIL"
        lines.append(f"  {mark} {name:<11} {s.get('detail', '')}")
    market = result.get("market") or {}
    lines.append(f"  {'ok  ' if market.get('ok') else '--  '} market      "
                 f"{market.get('detail', '')}")
    if result["verdict"] != "blocked":
        lines.append(f"  -> {result.get('headline', '')}")
        if result.get("flags"):
            lines.append(f"  -> flags: {', '.join(result['flags'])}")
    return "\n".join(lines)
