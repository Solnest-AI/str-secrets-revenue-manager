"""Join verified snapshots once; expose compact facts and dated review candidates."""

from __future__ import annotations

import math
from datetime import date as Date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean

import attribution
import flywheel
from _calendar import pricelabs_status
from _mvp_store import CannotAnalyze


def number(value):
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out >= 0 else None


def rounded(value, places=2):
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))


def average(values):
    vals = [v for v in values if v is not None]
    return rounded(mean(vals)) if vals else None


def markups(context, as_of):
    settings = context.get("settings", {})
    values = settings.get("channel_markup_pct")
    source = settings.get("channel_markup_source", {})
    verified_types = {
        "user-confirmed screenshot",
        "operator_confirmed",
        "operator-confirmed",
        "hospitable_listing_markups",
    }
    if (
        not isinstance(values, dict)
        or "airbnb" not in values
        or not isinstance(source, dict)
        or source.get("source_type") not in verified_types
    ):
        raise CannotAnalyze(
            "Confirmed channel markups are missing; calendar sync ratios are not markups"
        )
    try:
        confirmed = datetime.fromisoformat(source["confirmed_at"].replace("Z", "+00:00"))
        if confirmed.tzinfo is None or confirmed > as_of:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise CannotAnalyze(
            "Channel markup confirmation is missing, undated or future-dated"
        ) from None
    if any(number(v) is None or number(v) > 500 for v in values.values()):
        raise CannotAnalyze("Channel markup percentages are invalid")
    return {k: float(v) for k, v in values.items()}


def build(pms, listing, prices, market, overrides, rules, funnel, rankings, context, as_of,
          pile=None):
    """Full daily coverage is stored; only rollups and actionable exceptions are emitted."""
    markup = markups(context, as_of)
    multiplier = 1 + markup["airbnb"] / 100
    start = pms["window"]["start_date"]
    window = pms["window"]["days"]
    expected_dates = [
        (Date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(window)
    ]
    if [r.get("date") for r in pms["daily"]] != expected_dates:
        raise CannotAnalyze("PMS daily analysis does not cover the exact requested horizon")
    if listing.get("currency") != pms["property"]["currency"]:
        raise CannotAnalyze("PMS and listing currencies disagree")
    bounds = {k: number(listing.get(k)) for k in ("min", "base", "max")}
    if (
        any(v is None or v <= 0 for v in bounds.values())
        or not bounds["min"] <= bounds["base"] <= bounds["max"]
    ):
        raise CannotAnalyze("Listing min/base/max are missing or inconsistent")
    price_map = {r["date"]: r for r in prices["data"]}
    market_map = {r["date"]: r for r in market["data"]}
    if (
        len(price_map) != window
        or len(market_map) != window
        or len(prices["data"]) != window
        or len(market["data"]) != window
    ):
        raise CannotAnalyze("Price or market calendar has missing or duplicate dates")
    override_map = {r["date"]: r for r in overrides}
    if len(override_map) != len(overrides):
        raise CannotAnalyze("Duplicate overrides prevent trustworthy attribution")
    blockers, notes, mismatches, held_gaps, rows = [], [], [], [], []
    age = None
    try:
        stamp = datetime.fromisoformat(prices["last_refreshed_at"].replace("Z", "+00:00"))
        age = (as_of - stamp).total_seconds() / 3600
        if not 0 <= age <= 24:
            blockers.append("PriceLabs calculated prices are stale or future-dated")
    except (KeyError, ValueError, TypeError):
        blockers.append("PriceLabs calculation timestamp is unreadable")
    if not pms["coverage"]["analysable"]:
        blockers.append("PMS inventory or reservation evidence is incomplete")
    if pms["coverage"].get("pms_rates_exposed") is False:
        notes.append("Your PMS does not expose nightly prices or min-stay, so reconciliation checked "
                     "bookings and availability only; the prices shown are PriceLabs'.")
    settings_delta = context.get("settings", {}).get("max_delta_pct")
    if settings_delta is not None and settings_delta != 0.15:
        notes.append("Saved movement threshold differs; this analysis uses 15% scrutiny")
    try:
        sync_date = Date.fromisoformat(funnel["last_sync_date"])
        fresh_funnel = (
            0 <= (Date.fromisoformat(start) - sync_date).days <= 3
            and funnel.get("current_month") == start[:7]
        )
    except (KeyError, TypeError, ValueError):
        fresh_funnel = False
    vis = flywheel.spoke_visibility(
        funnel.get("visibility_row") if funnel.get("status") == "ok" and fresh_funnel else None
    )
    if not vis["ok"] and funnel.get("reason"):
        vis["detail"] = funnel["reason"]
    canonical = [
        {"date": r["date"], "status": {"reason": r["status_reason"], "available": r["available"]}}
        for r in pms["daily"]
    ]
    review_window = next((r for r in pms["reviews"]["windows"] if r["trailing_days"] == 90), {})
    review = flywheel.spoke_reviews(
        [{}] * review_window.get("count", 0), review_window.get("mean_rating_out_of_5")
    )
    if pms["reviews"]["source_records"] and not pms["reviews"]["included_records"]:
        review = {
            "spoke": "reviews",
            "ok": False,
            "detail": "Every fetched review has an unreadable date or rating",
        }
    elif pms["reviews"].get("unreadable_sample"):
        review = {
            "spoke": "reviews",
            "ok": False,
            "detail": "Review sample is unreadable or absent, not empty",
        }
    rank = flywheel.spoke_ranking(rankings)
    if any(r.get("date") != start for r in rankings):
        rank = {
            "spoke": "ranking",
            "ok": False,
            "detail": "Ranking rows are not current for this run",
        }
    wheel = flywheel.gate(
        pms["property"].get("name", "property"),
        vis,
        flywheel.spoke_bookings(canonical),
        review,
        rank,
    )
    # PRD D12 (2026-09-20, supersedes D4): a missing visibility / reviews / ranking
    # spoke DEGRADES the run and is named loudly, it does not block it. Only a missing
    # PMS calendar blocks, because then there is nothing to price. Do not route
    # `degraded` back into blockers; that is the old rule and it was overturned.
    if wheel["verdict"] == "blocked":
        blockers.append(wheel["why"])
    elif wheel["verdict"] == "degraded":
        notes.insert(0, wheel["why"])
    for i, day in enumerate(pms["daily"]):
        date = day["date"]
        if date not in price_map or date not in market_map:
            raise CannotAnalyze("Daily dates differ across source calendars")
        price, comp = price_map[date], market_map[date]
        net = number(price.get("price"))
        if net is None or net <= 0:
            raise CannotAnalyze("PriceLabs contains an unusable daily price")
        values = {k: number(comp.get(k)) for k in ("p25", "p50", "p75", "p90", "occ", "occ_stly")}
        if any(values[k] is None for k in ("p50", "p75", "p90", "occ")):
            raise CannotAnalyze("A required daily comp value is missing")
        if not 0 < values["p50"] <= values["p75"] <= values["p90"] or values["occ"] > 100:
            raise CannotAnalyze("Daily comp percentiles or occupancy are inconsistent")
        pl_status = pricelabs_status(price)
        classification = day["classification"]
        opened = classification == "open"
        # With no PMS nightly price or min-stay (see pms_rates_exposed), only the booking half of
        # the check can run; the price/stay half is skipped for that night, never guessed.
        drift = opened and (
            (day["price_cents"] is not None and abs(day["price_cents"] / 100 - net) > 0.011)
            or (day["min_stay"] is not None and day["min_stay"] != price.get("min_stay"))
        )
        paid_gap = classification == "confirmed_paid" and pl_status != "RESERVED"
        unexpected_booked = opened and pl_status == "RESERVED"
        if drift or paid_gap or unexpected_booked:
            mismatches.append(
                {
                    "date": date,
                    "price_or_stay_drift": bool(drift),
                    "paid_booking_missing": paid_gap,
                    "unexpected_pl_booking": unexpected_booked,
                }
            )
        if classification == "pending_hold" and pl_status != "RESERVED":
            held_gaps.append(date)
        over = override_map.get(date, {})
        layer = "fixed_override" if over.get("price_type") == "fixed" else "customization_stack"
        if over.get("price") is not None and over.get("price_type") != "fixed":
            layer = "relative_override"
        row = {
            "date": date,
            "days_out": i,
            "status": classification,
            "net": net,
            "airbnb": rounded(net * multiplier),
            "min_stay": day["min_stay"] if day["min_stay"] is not None else price.get("min_stay"),
            "p50": values["p50"],
            "p75": values["p75"],
            "p90": values["p90"],
            "market_occ": values["occ"],
            "market_occ_stly": values["occ_stly"],
            "layer": layer,
            "override": over or None,
            "at_floor": net == bounds["min"],
            "at_ceiling": net == bounds["max"],
            "action": "hold_unavailable" if not opened else "monitor",
            "flags": [],
            "demand": price.get("demand_desc"),
        }
        if opened:
            if row["airbnb"] > values["p90"]:
                row["flags"].append("above_market_p90")
            if drift or unexpected_booked:
                row["action"] = "resolve_sync"
            elif (
                pl_status != "AVAILABLE"
                or day.get("closed_for_checkin")
                or day.get("closed_for_checkout")
            ):
                row["action"] = "review_restrictions"
            elif i < 14 and row["airbnb"] > values["p75"]:
                row["action"] = "review_price"
            if (row["min_stay"] or 1) > 1 and i < 14:
                row["flags"].append("near_term_min_stay")
        rows.append(row)
    if mismatches:
        blockers.append("Unexplained PMS/PriceLabs price, min-stay or booking mismatch")
    if wheel.get("flags"):
        notes.append(
            "Resolve the visibility/review ranking flags before treating price as the primary lever"
        )
    for i, row in enumerate(rows):
        if (
            row["status"] == "open"
            and 0 < i < len(rows) - 1
            and rows[i - 1]["status"] != "open"
            and rows[i + 1]["status"] != "open"
        ):
            row["flags"].append("isolated_open_night_age_unknown")
    candidates = []
    for row in rows:
        if row["action"] == "review_price" and not blockers:
            # This is a review range, not a provider operation or an automatic recommendation.
            lower = max(bounds["min"], math.ceil(row["net"] * 0.85))
            upper = min(bounds["max"], math.floor(row["net"] * 0.95))
            if lower > upper:
                row["action"] = "review_bounds"
                row["flags"].append("no_reduction_range_within_bounds_and_15pct")
                continue
            row["review_net_range"] = [lower, upper]
            row["review_airbnb_range"] = [rounded(lower * multiplier), rounded(upper * multiplier)]
            candidates.append(row)
        elif row["action"] == "review_price":
            row["action"] = "pricing_opinion_withheld"
    affected = {r["date"] for r in candidates}
    attribution_rows = attribution.ce_rows(prices["data"], start)
    attribution_result = (
        attribution.classify(affected, attribution_rows, rules["raw"]) if affected else []
    )
    # PRD D14c: is each configured rule doing its job? Accepted stays count as booked;
    # holds, blocks, conflicts and unknowns leave the denominator, because a night the
    # market could not buy says nothing about the rule that priced it.
    effect_rows = [{
        "date": r["date"], "days_out": r["days_out"],
        "dow": Date.fromisoformat(r["date"]).weekday(),
        "booked": r["status"] in ("confirmed_paid", "zero_value_accepted",
                                  "accepted_unknown_value"),
        "blocked": r["status"] in ("blocked", "pending_hold", "conflict", "unknown"),
        "market_occ": r.get("market_occ"),
    } for r in rows]
    rule_effect = attribution.rule_effectiveness(rules["raw"], effect_rows)
    rollups = []
    for win in pms["windows"]:
        group = rows[: win["days"]]
        open_rows = [r for r in group if r["status"] == "open"]
        rollups.append(
            {
                "days": win["days"],
                "confirmed": win["confirmed_paid_nights"],
                "held": win["pending_held_nights"],
                "open": win["open_nights"],
                "zero_value": win["zero_value_accepted_nights"],
                "blocked": win["blocked_nights"],
                "occupancy_pct": win["confirmed_occupancy_pct"],
                "market_occupancy_pct": average(r["market_occ"] for r in group),
                "open_net_mean": average(r["net"] for r in open_rows),
                "open_airbnb_mean": average(r["airbnb"] for r in open_rows),
                "matched_open_p50": average(r["p50"] for r in open_rows),
                "matched_open_p90": average(r["p90"] for r in open_rows),
                "floor_open": sum(r["at_floor"] for r in open_rows),
                "ceiling_open": sum(r["at_ceiling"] for r in open_rows),
            }
        )
    months = []
    for month in pms["forward_months"]:
        group = [r for r in rows if r["date"].startswith(month["month"])]
        opened = [r for r in group if r["status"] == "open"]
        months.append(
            {
                "month": month["month"],
                "days": len(group),
                "confirmed": month["confirmed_paid_nights"],
                "held": month["pending_held_nights"],
                "open": month["open_nights"],
                "occupancy_pct": month["confirmed_occupancy_pct"],
                "market_occupancy_pct": average(r["market_occ"] for r in group),
                "open_airbnb_mean": average(r["airbnb"] for r in opened),
                "matched_open_p90": average(r["p90"] for r in opened),
            }
        )
    return {
        # D12: "degraded" is a real top-level state. It prices, and it says what it
        # priced without. It is not "blocked" and it is not silently "analysable".
        "status": ("blocked" if blockers
                   else "degraded" if wheel["verdict"] == "degraded"
                   else "analysable"),
        "blockers": blockers,
        "notes": notes,
        "window": pms["window"],
        "property": pms["property"],
        "currency": listing["currency"],
        "markups": markup,
        "markup_source": context["settings"]["channel_markup_source"],
        "bounds": bounds,
        "movement_scrutiny_pct": 15,
        "flywheel": wheel,
        "visibility": {
            "month": funnel.get("current_month"),
            "last_sync": funnel.get("last_sync_date"),
            "comparison": (funnel.get("visibility_row") or {}).get("similar_listings_comparison"),
        },
        "reconciliation": {
            "mismatches": mismatches,
            "held_dates_absent_from_pl": held_gaps,
            "open_dates_checked": sum(r["status"] == "open" for r in rows),
        },
        "windows": rollups,
        "months": months,
        "daily": rows,
        "candidates": candidates,
        "attribution": attribution_result,
        "rule_effectiveness": rule_effect,
        "pile": pile_summary(pile),
        "rules": rules["summary"],
        "pms": {k: v for k, v in pms.items() if k not in {"daily", "property"}},
        "comp_count": market["listings_used"],
        "price_freshness": {
            "calculated_at": prices.get("last_refreshed_at"),
            "age_hours": rounded(age),
            "maximum_age_hours": 24,
        },
        "recent_decisions": context.get("decisions", []),
        "recent_changes": context.get("changes", []),
        "market_base_percentiles": market["base_percentiles"],
        "limitations": [
            "Airbnb asks apply confirmed markup only. Cleaning, taxes, guest fees and "
            "promotions require a checkout quote.",
            "Review ranges are 5-15% scenarios within current bounds, not approved operations.",
            "Historical pace uses current stay dates and timestamped status history.",
            "Events and named qualitative comps are separate evidence; no automatic event premium.",
        ],
    }


def pile_summary(pile):
    """A compact view of this run's PriceLabs actions and nudges (PRD D14).

    One input, never the basis: the brief shows what PriceLabs is suggesting for THIS
    listing so the operator can weigh it, and says how much of the pile belonged to
    other properties so nobody mistakes the account for the listing.
    """
    if not isinstance(pile, dict):
        return None
    c = pile.get("counts") or {}
    mine = pile.get("this_listing") or {}
    items = []
    for a in mine.get("actions") or []:
        items.append(f"action {a.get('action_type')}: {a.get('title')} "
                     f"(current {a.get('current')}, PriceLabs suggests {a.get('recommended')})")
    for n in mine.get("nudges") or []:
        items.append(f"nudge {n.get('nudge_type')}: {n.get('current')} -> {n.get('suggested')} "
                     f"({n.get('direction')}, expires {str(n.get('expiration') or '')[:10]})")
    return {
        "summary": (f"{len(mine.get('actions') or [])} action(s) and "
                    f"{len(mine.get('nudges') or [])} nudge(s) for this listing; "
                    f"{c.get('actions_other', 0) + c.get('nudges_other', 0)} in the pile "
                    "belong to other listings and are stored but not shown"),
        "this_listing": items,
        "counts": c,
    }


def render(pack, run_id, metrics):
    if "windows" not in pack:
        return (
            f"90-day analysis {run_id}: {pack['status']}\n"
            + "\n".join(pack.get("blockers", []) + pack.get("notes", []))
            + f"\nHTTP calls: {metrics['http_calls']}. External writes: 0.\n"
        )
    lines = [
        f"{pack['property']['name']} | {pack['window']['start_date']} to "
        f"{pack['window']['end_date_exclusive']} (checkout boundary) | "
        f"{pack['currency']} | {pack['status']}",
        f"Run {run_id}. Bounds min/base/max: {pack['bounds']['min']:g}/"
        f"{pack['bounds']['base']:g}/{pack['bounds']['max']:g}. "
        f"Airbnb markup {pack['markups']['airbnb']:g}%, confirmed listing setting.",
        f"PriceLabs calculated {pack['price_freshness']['age_hours']} hours ago; "
        f"market comps: {pack['comp_count']}. Full source timestamps are in --details.",
        "",
        "days | confirmed | held | open | occ% | market% | open Airbnb | matched p50/p90",
    ]
    for w in pack["windows"]:
        lines.append(
            f"{w['days']} | {w['confirmed']} | {w['held']} | {w['open']} | {w['occupancy_pct']} | "
            f"{w['market_occupancy_pct']} | {w['open_airbnb_mean']} | "
            f"{w['matched_open_p50']}/{w['matched_open_p90']}"
        )
    lines.extend(["", "Calendar-month slices (partial first/last month):"])
    for m in pack["months"]:
        lines.append(
            f"{m['month']}: {m['days']} nights, {m['confirmed']} confirmed, "
            f"{m['held']} held, {m['open']} open; "
            f"occupancy {m['occupancy_pct']}% vs market {m['market_occupancy_pct']}%; "
            f"open Airbnb {m['open_airbnb_mean']} vs matched p90 {m['matched_open_p90']}."
        )
    lines.extend(["", "Flywheel: Visibility > Bookings > Reviews > Ranking"])
    for key in pack["flywheel"]["order"]:
        s = pack["flywheel"]["spokes"][key]
        lines.append(f"{key}: {'ok' if s['ok'] else 'unreadable'}, {s['detail']}")
    market = pack["flywheel"].get("market") or {}
    lines.append(f"market layer: {'ok' if market.get('ok') else 'absent'}, {market.get('detail', '')}")
    lines.extend(["", "Rule effectiveness (D14c; the window vs the rest, market as yardstick):"])
    for e in pack.get("rule_effectiveness", []):
        lines.append(f"{e['rule']}: {e['verdict']}. {e['why']}")
    pile = pack.get("pile") or {}
    if pile:
        lines.extend(["", f"PriceLabs pile (one input, not the basis): {pile['summary']}"])
        for item in pile.get("this_listing", [])[:6]:
            lines.append(f"  {item}")
    pms = pack["pms"]
    lines.extend(["", "Same-lead reconstructed accepted nights (current/prior calendar date):"])
    for w in pms["same_lead"]["windows"]:
        cur, prior = w["current"], w["prior_same_calendar"]
        label = (
            "partial"
            if cur["unknown_status_records"] or prior["unknown_status_records"]
            else "reconstructed"
        )
        lines.append(
            f"{w['days']}d {cur['reconstructed_accepted_nights']}/"
            f"{prior['reconstructed_accepted_nights']} ({label})"
        )
    ytd = pms["ytd"]
    current_cents = ytd["current"].get("accommodation_cents")
    prior_cents = ytd["prior_same_elapsed_days"].get("accommodation_cents")
    current_money = rounded(current_cents / 100) if current_cents is not None else "unknown"
    prior_money = rounded(prior_cents / 100) if prior_cents is not None else "unknown"
    lines.append(
        f"YTD accommodation: {current_money}; "
        f"prior same elapsed days: {prior_money}. "
        "Before fees/taxes; not payout or profit."
    )
    booking = pms["completed_bookings"]
    lines.append(
        f"Completed-stay cohort: median booking lead {booking['median_lead_days']}d; "
        f"one-night bookings {booking['one_night_bookings_pct']}%; "
        f"mean stay {booking['mean_los']} nights."
    )
    lines.append(
        f"Pickup: {pms['pickup']['last_24h']['confirmed_positive_value_bookings']} "
        "confirmed creations in 24h; "
        f"{pms['pickup']['last_7d']['confirmed_positive_value_bookings']} in 7d."
    )
    lines.extend(
        ["", "Dated price review scenarios, requiring framework/event review and approval:"]
    )
    for r in pack["candidates"]:
        lines.append(
            f"{r['date']}: net {r['net']:g}, Airbnb {r['airbnb']:g}, "
            f"comp p75/p90 {r['p75']:g}/{r['p90']:g}; "
            f"review net {r['review_net_range'][0]}-{r['review_net_range'][1]}; layer {r['layer']}."
        )
    if not pack["candidates"]:
        lines.append(
            "None emitted." if not pack["blockers"] else "Withheld because a required gate failed."
        )
    lines.append(
        f"Reconciliation: {pack['reconciliation']['open_dates_checked']} open dates checked; "
        f"{len(pack['reconciliation']['mismatches'])} unexplained mismatches; "
        f"{len(pack['reconciliation']['held_dates_absent_from_pl'])} pending-held dates "
        "absent from PriceLabs, excluded from candidates."
    )
    lines.extend(pack["blockers"] + pack["notes"])
    comps = pack.get("named_comps", {})
    if comps.get("status") == "ok":
        summary, capacity = comps["summary"], comps["capacity_subset"]
        lines.append(
            f"Named comps: {summary['comp_count']} bedroom/bath matches, "
            f"{capacity['count']} also fit guest capacity; trailing-year ADR median "
            f"{capacity['summary'].get('adr_median')} for that subset. Not forward pace."
        )
    else:
        lines.append("Named comps unavailable: " + comps.get("reason", "not loaded"))
    if pack["recent_changes"]:
        lines.append(
            f"Prior audit: {len(pack['recent_changes'])} recent changes loaded; "
            f"latest {pack['recent_changes'][0].get('created_at')}."
        )
    lines.extend(
        [
            "",
            *pack["limitations"],
            f"HTTP calls: {metrics['http_calls']} {metrics['by_provider']}; "
            f"cache hits {metrics['cache_hits']}; "
            f"received bytes {metrics['response_bytes']}; external writes 0.",
            "Daily rows and source timestamps are saved in the workbench; "
            "--show RUN_ID --details reads them offline.",
        ]
    )
    return "\n".join(lines) + "\n"
