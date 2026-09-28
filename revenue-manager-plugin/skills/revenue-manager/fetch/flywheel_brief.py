"""The FLYWHEEL block at the top of every card: where the listing stands at each stage, in
plain English, each number next to its benchmark (similar listings, the market, or last year).

Six rows in flywheel order: Visibility, Views, Bookings, Reviews, Ranking, Pacing. Every row
carries one verdict, OK / AHEAD / BEHIND / NO DATA, and the block ends by naming the first
stage that breaks. A stage with nothing to compare says so instead of showing a bare number
(Ryan, 2026-09-28: "tell the users where they're at").
"""

from __future__ import annotations

from flywheel import SHORTFALL

AHEAD_MARGIN = 0.10  # more than 10% above the benchmark reads as ahead, not just OK

VERDICT = {"OK": "✅ OK", "AHEAD": "✅ AHEAD", "BEHIND": "⚠️ BEHIND", "NO DATA": "❌ NO DATA"}

# What each break means, as a host would say it.
BREAK_MEANING = {
    "first_page_impressions": "Guests are not seeing it in search.",
    "click_through_rate": "Guests see it in search but do not click it.",
    "view": "Guests click but leave the page.",
    "wishlist": "Guests look but do not save it.",
    "booking_rate": "Guests look and save, but do not book. That points at price or the listing itself, not traffic.",
    "conversion_rate": "Guests start booking and do not finish.",
    "occupancy": "Fewer nights booked than the market around it.",
    "reviews": "The rating is holding it back; framework 6.9 calls that a ranking problem, not a pricing one.",
    "ranking": "It sits too deep in search results for price to be the first problem.",
    "pacing": "Booking slower than at this point last year.",
}


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _int(value) -> str:
    return f"{int(round(value)):,}"


def _nights(value) -> str:
    return f"{_int(value)} night{'' if int(round(value)) == 1 else 's'}"


def _pct(value) -> str:
    return f"{value:.1f}%".replace(".0%", "%")


def _compare(mine, theirs) -> str:
    """OK / AHEAD / BEHIND against a benchmark, the same shortfall the funnel diagnosis uses."""
    if mine is None or theirs is None:
        return "NO DATA"
    if theirs > 0 and mine < theirs * (1.0 - SHORTFALL):
        return "BEHIND"
    if theirs > 0 and mine > theirs * (1.0 + AHEAD_MARGIN):
        return "AHEAD"
    return "OK"


def _worst(verdicts) -> str:
    known = [v for v in verdicts if v != "NO DATA"]
    if not known:
        return "NO DATA"
    for v in ("BEHIND", "OK", "AHEAD"):
        if v in known:
            return v
    return "OK"


def _funnel_stages(pack) -> tuple[dict, str | None, bool]:
    """{stage: (listing, similar)} for every stage the diagnosis walked, the stage it broke at
    (if any), and whether the visibility spoke could be read at all."""
    spoke = (pack.get("flywheel") or {}).get("spokes", {}).get("visibility") or {}
    diag = spoke.get("diagnosis") or {}
    stages = {s["stage"]: (_num(s.get("listing")), _num(s.get("similar")))
              for s in diag.get("stages") or [] if isinstance(s, dict) and s.get("stage")}
    broke_at = diag.get("stage") if diag.get("verdict") == "break" else None
    return stages, broke_at, bool(spoke.get("ok"))


def _funnel_row(name, stages, broke_at, readable, why_unreadable, parts):
    """One row from funnel stages: parts = [(stage, phrase(mine, theirs))]. A stage that was
    never walked because the funnel broke earlier says so, never a bare number."""
    if not readable:
        return VERDICT["NO DATA"], why_unreadable or "no funnel data for this listing"
    texts, verdicts = [], []
    for stage, phrase in parts:
        if stage in stages:
            mine, theirs = stages[stage]
            verdicts.append(_compare(mine, theirs))
            texts.append(phrase(mine, theirs))
    if not texts:
        return VERDICT["NO DATA"], (f"not measured: the funnel breaks earlier, at {broke_at.replace('_', ' ')}"
                                    if broke_at else "no benchmark for this stage")
    return VERDICT[_worst(verdicts)], " ".join(texts)


def rows(pack) -> list[tuple[str, str, str]]:
    """[(stage label, verdict with emoji, plain sentence)] in flywheel order."""
    stages, broke_at, readable = _funnel_stages(pack)
    vis_spoke = (pack.get("flywheel") or {}).get("spokes", {}).get("visibility") or {}
    why = vis_spoke.get("detail") if not readable else None
    out = []

    out.append(("Visibility",) + _funnel_row("Visibility", stages, broke_at, readable, why, [
        ("first_page_impressions", lambda m, t: f"Seen in search {_int(m)} times vs {_int(t)} for similar listings."),
        ("click_through_rate", lambda m, t: f"{_pct(m)} of them click it vs {_pct(t)}."),
    ]))
    out.append(("Views",) + _funnel_row("Views", stages, broke_at, readable, why, [
        ("view", lambda m, t: f"{_int(m)} page views vs {_int(t)} for similar listings."),
        ("wishlist", lambda m, t: f"Saved to a wishlist {_int(m)} times vs {_int(t)}."),
    ]))

    # Bookings: the PMS calendar is the ground truth (next 30 nights vs the market); the
    # funnel's booking rate is the second number when it was measured.
    w30 = next((w for w in pack.get("windows") or [] if w.get("days") == 30), None)
    occ = _num(w30.get("occupancy_pct")) if w30 else None
    market = _num(w30.get("market_occupancy_pct")) if w30 else None
    parts, verdicts = [], []
    if occ is not None and market is not None:
        parts.append(f"Next 30 nights: {_pct(occ)} booked vs the market's {_pct(market)}.")
        verdicts.append(_compare(occ, market))
    elif occ is not None:
        parts.append(f"Next 30 nights: {_pct(occ)} booked (no market figure to compare).")
    if readable and "booking_rate" in stages:
        m, t = stages["booking_rate"]
        rate_verdict = _compare(m, t)
        # The calendar decides the verdict. The similar-listing booking rate swings day to day,
        # so it is shown only when it agrees with the calendar, never next to a verdict it fights.
        if not verdicts or (rate_verdict == "BEHIND") == (verdicts[0] == "BEHIND"):
            parts.append(f"{_pct(m)} of lookers book vs {_pct(t)} for similar listings.")
        if not verdicts:
            verdicts.append(rate_verdict)
    out.append(("Bookings", VERDICT[_worst(verdicts)] if parts else VERDICT["NO DATA"],
                " ".join(parts) or "the PMS calendar could not be read"))

    rev = (pack.get("flywheel") or {}).get("spokes", {}).get("reviews") or {}
    rating, count = _num(rev.get("rating")), rev.get("count")
    if not rev.get("ok"):
        out.append(("Reviews", VERDICT["NO DATA"], rev.get("detail") or "reviews could not be read"))
    elif rating is None:
        out.append(("Reviews", VERDICT["NO DATA"], f"{count} reviews, no overall rating available."))
    else:
        verdict = "BEHIND" if rating < 4.6 else "OK"
        note = "" if rating >= 4.8 else (" Target is 4.8 or better." if rating >= 4.6 else " Below 4.6 hurts ranking.")
        out.append(("Reviews", VERDICT[verdict], f"{rating:g} stars across {count} reviews.{note}"))

    rank = (pack.get("flywheel") or {}).get("spokes", {}).get("ranking") or {}
    page = rank.get("worst_page")
    if not rank.get("ok") or page is None:
        out.append(("Ranking", VERDICT["NO DATA"], rank.get("detail") or "no ranking data"))
    else:
        pos = rank.get("best_position")
        where = f"Shows on page {page} of search" + (f", best position {int(pos)}" if pos else "") + "."
        verdict = "OK" if page <= 2 else "BEHIND"
        out.append(("Ranking", VERDICT[verdict], where + (" Page 5 or deeper is a visibility problem before a pricing one." if page >= 5 else "")))

    pms = pack.get("pms") or {}
    lead = next((w for w in (pms.get("same_lead") or {}).get("windows") or [] if w.get("days") == 30), None)
    cur = _num(((lead or {}).get("current") or {}).get("reconstructed_accepted_nights"))
    prior = _num(((lead or {}).get("prior_same_calendar") or {}).get("reconstructed_accepted_nights"))
    pick7 = ((pms.get("pickup") or {}).get("last_7d") or {}).get("confirmed_positive_value_bookings")
    pickup = f" {pick7} new booking{'' if pick7 == 1 else 's'} in the last 7 days." if pick7 is not None else ""
    unknown = sum(int(((lead or {}).get(side) or {}).get("unknown_status_records") or 0)
                  for side in ("current", "prior_same_calendar"))
    if cur is None or prior is None:
        out.append(("Pacing", VERDICT["NO DATA"], "no bookings on record for this time last year to compare." + pickup))
    elif unknown:
        out.append(("Pacing", VERDICT["NO DATA"],
                    f"{unknown} booking{'' if unknown == 1 else 's'} in the PMS carry no status history, "
                    "so when they were booked cannot be rebuilt; a count would be wrong." + pickup))
    elif prior == 0:
        out.append(("Pacing", VERDICT["NO DATA"], f"{_nights(cur)} booked for the next 30 days; nothing was on the books at this point last year, so there is no year to compare." + pickup))
    else:
        out.append(("Pacing", VERDICT[_compare(cur, prior)],
                    f"{_nights(cur)} booked for the next 30 days vs {_int(prior)} at this point last year." + pickup))
    return out


def break_line(pack, table) -> str:
    """The first stage, in flywheel order, that is BEHIND, and what that means."""
    stages, broke_at, readable = _funnel_stages(pack)
    for label, verdict, _ in table:
        if not verdict.endswith("BEHIND"):
            continue
        if label in ("Visibility", "Views"):
            # the diagnosed stage, else the first funnel stage in this row that trails
            stage = broke_at if broke_at in BREAK_MEANING else next(
                (s for s in ("first_page_impressions", "click_through_rate") if label == "Visibility"
                 and s in stages and _compare(*stages[s]) == "BEHIND"), None) or next(
                (s for s in ("view", "wishlist") if s in stages and _compare(*stages[s]) == "BEHIND"), None)
            return f"Where it breaks: {label}. {BREAK_MEANING.get(stage, 'Fewer guests reach this stage than for similar listings.')}"
        key = {"Bookings": "booking_rate" if (broke_at == "booking_rate") else "occupancy",
               "Reviews": "reviews", "Ranking": "ranking", "Pacing": "pacing"}.get(label)
        return f"Where it breaks: {label}. {BREAK_MEANING.get(key, '')}".rstrip()
    if any(v.endswith("NO DATA") for _, v, _ in table):
        missing = ", ".join(l for l, v, _ in table if v.endswith("NO DATA"))
        return f"Nothing breaks where it could be measured. Not measured: {missing}."
    return "Nothing breaks: every stage is at or ahead of its benchmark."


def render(pack) -> list[str]:
    table = rows(pack)
    width = max(len(v) for _, v, _ in table)
    lines = ["FLYWHEEL: where this listing stands (Visibility > Bookings > Reviews > Ranking)"]
    for label, verdict, text in table:
        lines.append(f"  {verdict:<{width}}  {label:<10} {text}")
    lines.append(f"  {break_line(pack, table)}")
    return lines
