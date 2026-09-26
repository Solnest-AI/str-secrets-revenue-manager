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


DEFAULT_MAX_DELTA = 0.15
# A whole-run block only when mismatches are this share of open nights or more. Below it,
# only the mismatched dates lose their pricing opinion; the rest are still priced.
MISMATCH_BLOCK_SHARE = 0.20
THIN_COMPS = 20
NEAR_TERM_DAYS = 30
PACE_BAND_PP = 5.0


def movement_cap(settings):
    """The saved max_delta_pct as a fraction. Accepts 15 (percent) or 0.15 (fraction).

    Returns (fraction, note). An unreadable value falls back to 15% and says so."""
    raw = (settings or {}).get("max_delta_pct")
    if raw is None:
        return DEFAULT_MAX_DELTA, None
    value = number(raw.strip() if isinstance(raw, str) else raw)
    if value is None or value <= 0 or value > 100:
        return DEFAULT_MAX_DELTA, (f"Saved max_delta_pct {raw!r} is unreadable; "
                                   "this analysis uses the 15% default")
    return (value / 100 if value >= 1 else value), None


def _pct_label(fraction):
    return f"{rounded(fraction * 100, 1):g}%"


def min_price_recommendation(bounds, rows, multiplier, max_delta, comp_p25=None, comp_source=None):
    """What the listing's min SHOULD be (SKILL 2.1). An output, never a question.

    Inputs are all things the run already has: the current min, how many open near-term
    nights sit at the floor, how many booked nights sold at the floor, the comp set's
    lower quartile (p25, Airbnb-facing, so divided by the markup to compare with a net
    min) and pace vs the market over the next NEAR_TERM_DAYS. Never asks for a breakeven.

      lower  many open near-term nights at the floor AND pace lags the market; target the
             comp lower quartile (net), never below it
      raise  nights are selling at the floor AND pace runs ahead of the market; target the
             comp lower quartile or +10%, whichever is higher, never above base
      keep   anything else

    Any move is capped at max_delta; a target beyond it is flagged `large_move`.

    `comp_p25` (Airbnb-facing) stands in for the lower quartile only when the rows carry no
    market p25 (Beyond: AirROI's trailing-12-month ADR p25), and `comp_source` names it in
    the reason. The rule itself is the same for every pricing tool. The raise never passes
    base; no ceiling (max None) is never read here.
    """
    current = bounds["min"]
    near = [r for r in rows if 0 <= r["days_out"] < NEAR_TERM_DAYS]
    booked_states = ("confirmed_paid", "zero_value_accepted", "accepted_unknown_value")
    open_near = [r for r in near if r["status"] == "open"]
    floor_open = sum(1 for r in open_near if r["at_floor"])
    booked = [r for r in near if r["status"] in booked_states]
    floor_sold = sum(1 for r in booked if r["at_floor"])
    bookable = [r for r in near if r["status"] not in ("blocked", "pending_hold", "conflict",
                                                       "unknown")]
    occ = 100.0 * len(booked) / len(bookable) if bookable else None
    mkts = [r["market_occ"] for r in near if r.get("market_occ") is not None]
    mkt = mean(mkts) if mkts else None
    p25s = sorted(r["p25"] for r in near if r.get("p25") is not None)
    if not p25s:
        p25s = sorted(r["p25"] for r in rows if r.get("p25") is not None)
    borrowed = not p25s and comp_p25 is not None
    if borrowed:
        p25s = [comp_p25]
    quartile = (f"the lower quartile of {comp_source}" if borrowed and comp_source
                else "the comp set's lower quartile")
    p25_net = (p25s[len(p25s) // 2] / multiplier) if p25s else None
    pace = ("unknown" if occ is None or mkt is None
            else "lags" if occ < mkt - PACE_BAND_PP
            else "ahead" if occ > mkt + PACE_BAND_PP else "even")
    pace_txt = (f"your next {NEAR_TERM_DAYS} nights are {occ:.0f}% booked vs the market's "
                f"{mkt:.0f}%" if pace != "unknown" else "pace vs the market is unreadable")
    many_floor = floor_open >= 3 and open_near and floor_open / len(open_near) >= 0.30
    lo, hi = current * (1 - max_delta), current * (1 + max_delta)
    action, target, reason = "keep", current, None
    if many_floor and pace == "lags":
        if p25_net is not None and p25_net >= current:
            reason = (f"{floor_open} of {len(open_near)} open nights in the next "
                      f"{NEAR_TERM_DAYS} days sit at your min and {pace_txt}, but your min is "
                      f"already at or below {quartile} "
                      f"({rounded(p25_net):g} net), so a lower floor would undercut the "
                      "market. Look at visibility and the listing before the floor.")
        else:
            action = "lower"
            target = p25_net if p25_net is not None else current * (1 - max_delta)
            reason = (f"{floor_open} of {len(open_near)} open nights in the next "
                      f"{NEAR_TERM_DAYS} days sit at your min and {pace_txt}"
                      + (f"; {quartile} is {rounded(p25_net):g} net, "
                         "and the min never goes below it" if p25_net is not None
                         else "; comp lower quartile unavailable, so the step is the cap"))
    elif floor_sold >= 3 and pace == "ahead":
        action = "raise"
        target = max(current * 1.10, p25_net or 0)
        reason = (f"{floor_sold} booked nights in the next {NEAR_TERM_DAYS} days sold at your "
                  f"min and {pace_txt}; the floor is leaving money on the table")
    if action == "keep" and reason is None:
        reason = (f"{floor_open} of {len(open_near)} open near-term nights sit at your min and "
                  f"{pace_txt}; nothing says the floor is the problem")
    recommended = current
    large = False
    if action != "keep":
        capped = min(max(target, lo), hi)
        large = not (lo - 1e-9 <= target <= hi + 1e-9)
        if action == "raise":
            capped = min(capped, bounds["base"])
            recommended = math.floor(capped + 1e-9)
        else:
            recommended = math.ceil(capped - 1e-9)
            if p25_net is not None:
                recommended = max(recommended, math.ceil(p25_net - 1e-9))
        if large:
            reason += (f". The data points to {rounded(target):g}, a "
                       f"{_pct_label(abs(target - current) / current)} move; capped at "
                       f"{_pct_label(max_delta)}, large move, confirm")
        if recommended == current:
            action = "keep"
    return {
        "current": current,
        "recommended": recommended,
        "action": action,
        "reason": reason,
        "large_move": large,
        "uncapped_target": rounded(target),
        "floor_open_near": floor_open,
        "open_near": len(open_near),
        "floor_sold_near": floor_sold,
        "pace": pace,
        "comp_p25_net": rounded(p25_net) if p25_net is not None else None,
    }


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
          pile=None, today=None, pricing="pricelabs", comps=None):
    """Full daily coverage is stored; only rollups and actionable exceptions are emitted.

    `today` is the PROPERTY-LOCAL current date. After the evening rollover the window
    starts tomorrow, and days_out must still count from today, or every lead-time rule
    (the 14-day review cut-off, last-minute windows) is off by one.

    pricing="beyond" is the DEGRADED Beyond mode (_beyond_runner): no market percentiles,
    no rule attribution, no per-night min stay, no calculation time, maybe no ceiling, no
    pile. Each is a named gap on the card; the PriceLabs path is unchanged."""
    beyond = pricing == "beyond"
    tool = "Beyond" if beyond else "PriceLabs"
    markup = markups(context, as_of)
    multiplier = 1 + markup["airbnb"] / 100
    if beyond:
        import _beyond_runner as BR
        market = BR.usable_market(market, listing.get("currency"))
        comps_ref = BR.comp_reference(comps)
    start = pms["window"]["start_date"]
    window = pms["window"]["days"]
    if today is None:
        today_date = Date.fromisoformat(start)
    elif isinstance(today, str):
        today_date = Date.fromisoformat(today)
    else:
        today_date = today
    expected_dates = [
        (Date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(window)
    ]
    if [r.get("date") for r in pms["daily"]] != expected_dates:
        raise CannotAnalyze("PMS daily analysis does not cover the exact requested horizon")
    if listing.get("currency") != pms["property"]["currency"]:
        raise CannotAnalyze("PMS and listing currencies disagree")
    bounds = {k: number(listing.get(k)) for k in ("min", "base", "max")}
    no_ceiling = beyond and listing.get("max") is None  # Beyond: blank max = no ceiling
    ceiling = math.inf if no_ceiling else bounds["max"]
    if (
        any(v is None or v <= 0 for k, v in bounds.items() if not (no_ceiling and k == "max"))
        or not bounds["min"] <= bounds["base"] <= ceiling
    ):
        raise CannotAnalyze("Listing min/base/max are missing or inconsistent")
    price_map = {r["date"]: r for r in prices["data"]}
    market_map = {r["date"]: r for r in market["data"]}
    if (
        len(price_map) != window
        or len(prices["data"]) != window
        or (beyond and len(market_map) != len(market["data"]))
        or (not beyond and (len(market_map) != window or len(market["data"]) != window))
    ):
        raise CannotAnalyze("Price or market calendar has missing or duplicate dates")
    override_map = {r["date"]: r for r in overrides}
    if len(override_map) != len(overrides):
        raise CannotAnalyze("Duplicate overrides prevent trustworthy attribution")
    blockers, notes, mismatches, held_gaps, rows = [], [], [], [], []
    age = None
    if beyond:
        try:
            age = BR.read_at_age(prices, as_of)
            if not -0.25 <= age <= 24:
                blockers.append("The Beyond calendar read is stale or future-dated")
        except (KeyError, ValueError, TypeError):
            blockers.append("The Beyond calendar read time is unreadable")
    else:
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
                     f"bookings and availability only; the prices shown are {tool}'.")
    if pms["coverage"].get("pms_arrival_rules_exposed") is False:
        notes.append("Your PMS does not expose check-in or check-out day rules, so nights are read as "
                     "having none. If you block arrivals on certain days, check those nights yourself.")
    max_delta, delta_note = movement_cap(context.get("settings", {}))
    if delta_note:
        notes.append(delta_note)
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

    def _stale(r):
        # A source may declare how old a scrape it accepts (IntelliHost scrapes every few days:
        # max_age_days 7). Without a declaration the row must be today's, as before.
        try:
            age = (Date.fromisoformat(start) - Date.fromisoformat(str(r.get("date")))).days
        except ValueError:
            return True
        return not 0 <= age <= (r.get("max_age_days") or 0)

    if any(_stale(r) for r in rankings):
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
        if date not in price_map or (not beyond and date not in market_map):
            raise CannotAnalyze("Daily dates differ across source calendars")
        price, comp = price_map[date], market_map.get(date, {})
        net = number(price.get("price"))
        if net is None or net <= 0:
            raise CannotAnalyze(f"{tool} contains an unusable daily price")
        values = {k: number(comp.get(k)) for k in ("p25", "p50", "p75", "p90", "occ", "occ_stly")}
        days_out = (Date.fromisoformat(date) - today_date).days
        ref, ref_source, low, low_source = None, None, None, None
        if beyond:
            # averages, not percentiles: each night's references are named (see _beyond_runner)
            if values["occ"] is not None and values["occ"] > 100:
                raise CannotAnalyze("Beyond benchmark occupancy is above 100%")
            ref, ref_source = BR.reference_for(date, market_map, comps_ref, multiplier)
            low, low_source = BR.low_reference(comps_ref)
        elif any(values[k] is None for k in ("p50", "p75", "p90", "occ")):
            raise CannotAnalyze("A required daily comp value is missing")
        elif not 0 < values["p50"] <= values["p75"] <= values["p90"] or values["occ"] > 100:
            raise CannotAnalyze("Daily comp percentiles or occupancy are inconsistent")
        pl_status = pricelabs_status(price)
        classification = day["classification"]
        opened = classification == "open"
        # With no PMS nightly price or min-stay (see pms_rates_exposed), only the booking half of
        # the check can run; the price/stay half is skipped for that night, never guessed.
        drift = opened and (
            (day["price_cents"] is not None and abs(day["price_cents"] / 100 - net) > 0.011)
            or (not beyond  # Beyond has no per-night min stay: that half is a named gap
                and day["min_stay"] is not None and day["min_stay"] != price.get("min_stay"))
        )
        paid_gap = classification == "confirmed_paid" and pl_status != "RESERVED"
        unexpected_booked = opened and pl_status == "RESERVED"
        mismatched = bool(drift or paid_gap or unexpected_booked)
        if mismatched:
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
            "days_out": days_out,
            "status": classification,
            "net": net,
            "airbnb": rounded(net * multiplier),
            "min_stay": day["min_stay"] if day["min_stay"] is not None else price.get("min_stay"),
            "p25": values["p25"],
            "p50": values["p50"],
            "p75": values["p75"],
            "p90": values["p90"],
            "market_occ": values["occ"],
            "market_occ_stly": values["occ_stly"],
            "layer": layer,
            "override": over or None,
            "at_floor": (number(price.get("suggested")) or net) <= (
                number(price.get("effective_min_price")) or bounds["min"]) + 0.005
            if beyond else net == bounds["min"],
            "at_ceiling": (not no_ceiling and net >= bounds["max"] - 0.005) if beyond
            else net == bounds["max"],
            "action": "hold_unavailable" if not opened else "monitor",
            "flags": [],
            "demand": price.get("demand_desc"),
        }
        if mismatched:
            # Scoped: THIS date loses its pricing opinion, the rest of the run does not.
            row["withheld_reason"] = (
                f"PMS and {tool} disagree on "
                + " and ".join(
                    part for part, hit in (("price" if beyond else "price or min-stay", drift),
                                           (f"a paid booking {tool} does not show", paid_gap),
                                           (f"a booking {tool} shows but the PMS does not",
                                            unexpected_booked)) if hit)
                + "; fix the sync before pricing this date")
        if beyond:
            row["reference"], row["reference_source"] = ref, ref_source
        if opened:
            if beyond and ref is not None and row["airbnb"] > ref:
                row["flags"].append("above_market_reference")
            elif not beyond and row["airbnb"] > values["p90"]:
                row["flags"].append("above_market_p90")
            if mismatched:
                row["action"] = "pricing_opinion_withheld"
            elif (
                pl_status != "AVAILABLE"
                or day.get("closed_for_checkin")
                or day.get("closed_for_checkout")
            ):
                row["action"] = "review_restrictions"
            elif beyond and days_out < 14 and ref is not None and row["airbnb"] > ref:
                row["action"] = "review_price"
                row["direction"] = "cut"
            elif beyond and low is not None and row["airbnb"] < low:
                # raise scenarios need a LOWER reference; Beyond's average is not one, so only
                # AirROI's ADR p25 triggers them (named on the card)
                row["action"] = "review_price"
                row["direction"] = "raise"
                row["reference"], row["reference_source"] = low, low_source
            elif not beyond and days_out < 14 and row["airbnb"] > values["p75"]:
                row["action"] = "review_price"
                row["direction"] = "cut"
            elif not beyond and values["p25"] is not None and row["airbnb"] < values["p25"]:
                # Symmetric review: an open night priced under the comp set's lower quartile
                # gets a raise scenario, not silence. Same 15% cap, never below min.
                row["action"] = "review_price"
                row["direction"] = "raise"
            if (row["min_stay"] or 1) > 1 and days_out < 14:
                row["flags"].append("near_term_min_stay")
        rows.append(row)
    open_count = sum(r["status"] == "open" for r in rows)
    if mismatches and len(mismatches) > MISMATCH_BLOCK_SHARE * max(open_count, 1):
        blockers.append(
            f"PMS/{tool} price{'' if beyond else ', min-stay'} or booking mismatches on "
            f"{len(mismatches)} dates, "
            f"more than 20% of the {open_count} open nights: the sync itself is broken, so "
            "no date is priced this run")
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
            if row.get("direction") == "raise":
                lower = max(bounds["min"], math.ceil(row["net"] * 1.05))
                upper = min(ceiling, math.floor(row["net"] * (1 + max_delta)))
            else:
                lower = max(bounds["min"], math.ceil(row["net"] * (1 - max_delta)))
                upper = min(ceiling, math.floor(row["net"] * 0.95))
            if lower > upper:
                row["action"] = "review_bounds"
                row["flags"].append(
                    f"no_{'raise' if row.get('direction') == 'raise' else 'reduction'}"
                    f"_range_within_bounds_and_{rounded(max_delta * 100, 1):g}pct")
                continue
            row["review_net_range"] = [lower, upper]
            row["review_airbnb_range"] = [rounded(lower * multiplier), rounded(upper * multiplier)]
            candidates.append(row)
        elif row["action"] == "review_price":
            row["action"] = "pricing_opinion_withheld"
    affected = {r["date"] for r in candidates}
    attribution_rows = [] if beyond else attribution.ce_rows(prices["data"], today_date.isoformat())
    attribution_result = (
        attribution.classify(affected, attribution_rows, rules["raw"]) if affected and not beyond
        else []
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
    rule_effect = [] if beyond else attribution.rule_effectiveness(rules["raw"], effect_rows)
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
                "bookable": win.get("bookable_nights"),
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
        if beyond:
            rollups[-1]["open_reference_mean"] = average(
                r["reference"] for r in open_rows if r.get("direction") != "raise")
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
                "blocked": month["blocked_nights"],
                "bookable": month.get("bookable_nights"),
                "occupancy_pct": month["confirmed_occupancy_pct"],
                "market_occupancy_pct": average(r["market_occ"] for r in group),
                "open_airbnb_mean": average(r["airbnb"] for r in opened),
                "matched_open_p90": average(r["p90"] for r in opened),
            }
        )
    min_price = min_price_recommendation(
        bounds, rows, multiplier, max_delta,
        **({"comp_p25": (comps_ref or {}).get("adr_p25"),
            "comp_source": (comps_ref or {}).get("source")} if beyond else {}))
    result = {
        # D12: "degraded" is a real top-level state. It prices, and it says what it
        # priced without. It is not "blocked" and it is not silently "analysable".
        # Beyond mode is always at least degraded: its gaps are named at the top.
        "status": ("blocked" if blockers
                   else "degraded" if wheel["verdict"] == "degraded" or beyond
                   else "analysable"),
        "blockers": blockers,
        "notes": notes,
        "window": pms["window"],
        "property": pms["property"],
        "currency": listing["currency"],
        "markups": markup,
        "markup_source": context["settings"]["channel_markup_source"],
        "bounds": bounds,
        "movement_scrutiny_pct": rounded(max_delta * 100, 1),
        "min_price": min_price,
        "flywheel": wheel,
        "visibility": {
            "month": funnel.get("current_month"),
            "last_sync": funnel.get("last_sync_date"),
            "comparison": (funnel.get("visibility_row") or {}).get("similar_listings_comparison"),
        },
        "reconciliation": {
            "mismatches": mismatches,
            "held_dates_absent_from_pl": held_gaps,
            "open_dates_checked": open_count,
            "whole_run_block_share": MISMATCH_BLOCK_SHARE,
        },
        "windows": rollups,
        "months": months,
        "daily": rows,
        "candidates": candidates,
        "attribution": attribution_result,
        "rule_effectiveness": rule_effect,
        "pile": pile_summary(pile),
        "rules": [] if beyond else rules["summary"],
        "pms": {k: v for k, v in pms.items() if k not in {"daily", "property"}},
        "comp_count": market.get("listings_used"),
        "price_freshness": {
            "calculated_at": prices.get("last_refreshed_at"),
            "age_hours": rounded(age),
            "maximum_age_hours": 24,
        },
        "recent_decisions": context.get("decisions", []),
        "recent_changes": context.get("changes", []),
        "market_base_percentiles": market.get("base_percentiles"),
        "limitations": [
            "Airbnb asks apply confirmed markup only. Cleaning, taxes, guest fees and "
            "promotions require a checkout quote.",
            f"Review ranges are 5-{_pct_label(max_delta)} scenarios (cuts and raises) within "
            "current bounds, not approved operations.",
            "Historical pace uses current stay dates and timestamped status history.",
            "Events and named qualitative comps are separate evidence; no automatic event premium.",
        ],
    }
    if beyond:
        result.update({
            "pricing_tool": "beyond",
            "gaps": BR.gaps(market, listing, prices, comps),
            "market_source": market.get("source") if market["status"] == "ok" else None,
            "market_reason": market.get("reason"),
            "comp_reference": comps_ref,
            "comp_count": ((comps or {}).get("summary") or {}).get("comp_count") if comps_ref else None,
            "price_freshness": {"read_at": prices.get("read_at"), "age_hours": rounded(age),
                                "maximum_age_hours": 24, "kind": "read at"},
            "limitations": result["limitations"][:2]
            + ["Market references are averages or historical comps, not percentiles; named per night."]
            + result["limitations"][2:],
        })
    return result


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
    beyond = pack.get("pricing_tool") == "beyond"
    tool = "Beyond" if beyond else "PriceLabs"
    comp_n = pack.get("comp_count")
    comp_txt = "unknown" if comp_n is None else comp_n
    ceiling = "none" if pack["bounds"]["max"] is None else f"{pack['bounds']['max']:g}"
    lines = [
        f"{pack['property']['name']} | {pack['window']['start_date']} to "
        f"{pack['window']['end_date_exclusive']} (checkout boundary) | "
        f"{pack['currency']} | {pack['status']}",
    ]
    mism = pack.get("reconciliation", {}).get("mismatches") or []
    if mism:
        dates = ", ".join(m["date"] for m in mism[:8]) + (" ..." if len(mism) > 8 else "")
        lines.append(
            f"SYNC MISMATCH on {len(mism)} date(s), pricing withheld on those dates only: "
            f"{dates}. PMS and {tool} disagree; fix the sync, then rerun."
        )
    lines += [
        f"Run {run_id}. Bounds min/base/max: {pack['bounds']['min']:g}/"
        f"{pack['bounds']['base']:g}/{ceiling}. "
        f"Airbnb markup {pack['markups']['airbnb']:g}%, confirmed listing setting.",
        (f"Pricing tool Beyond: calendar read at {pack['price_freshness']['read_at']} (Beyond gives no "
         f"calculation time); market: {pack.get('market_source') or 'unavailable'}. Full source "
         "timestamps are in --details." if beyond else
         f"PriceLabs calculated {pack['price_freshness']['age_hours']} hours ago; "
         f"market comps: {comp_txt}. Full source timestamps are in --details."),
    ]
    if beyond:
        # Beyond's benchmark reports no comp count; AirROI's count is the only one there is.
        lines.append(
            "Heads up: comps behind Beyond's benchmark: unknown"
            + (f" ({pack['market_source']})" if pack.get("market_source") else " (no usable benchmark)")
            + ("; AirROI comps: none" if comp_n is None
               else f"; AirROI comps: {comp_n}" + (" (thin)" if comp_n < THIN_COMPS else ""))
            + ". Treat the market figures as a rough guide.")
        lines.extend(["", "GAPS (priced without these; each named, none guessed):"]
                     + [f"  - {g}" for g in pack.get("gaps", [])] + [""])
    elif comp_n is None:
        lines.append("Heads up: the comp count is unknown, treat the market figures as a "
                     "rough guide.")
    elif comp_n < THIN_COMPS:
        lines.append(f"Heads up: based on only {comp_n} comps, treat as a rough guide.")
    mp = pack.get("min_price")
    if mp:
        verb = {"lower": "lower to", "raise": "raise to", "keep": "keep at"}[mp["action"]]
        lines.append(
            f"Recommended min price: {verb} {mp['recommended']:g} net "
            f"(currently {mp['current']:g})"
            + (", LARGE MOVE, confirm" if mp["large_move"] else "")
            + f". {mp['reason']}."
        )
    lines += [
        "",
        "occ% = confirmed nights / bookable nights (owner-blocked nights excluded).",
        "days | confirmed | held | open | blocked | occ% | market% | open Airbnb | "
        + ("market reference" if beyond else "matched p50/p90"),
    ]
    for w in pack["windows"]:
        lines.append(
            f"{w['days']} | {w['confirmed']} | {w['held']} | {w['open']} | {w['blocked']} | "
            f"{w['occupancy_pct']} | "
            f"{w['market_occupancy_pct']} | {w['open_airbnb_mean']} | "
            + (f"{w.get('open_reference_mean')}" if beyond
               else f"{w['matched_open_p50']}/{w['matched_open_p90']}")
        )
    lines.extend(["", "Calendar-month slices (partial first/last month):"])
    for m in pack["months"]:
        lines.append(
            f"{m['month']}: {m['days']} nights, {m['confirmed']} confirmed, "
            f"{m['held']} held, {m['open']} open; "
            f"occupancy {m['occupancy_pct']}% of {m['bookable']} bookable "
            f"({m['blocked']} owner-blocked) vs market {m['market_occupancy_pct']}%; "
            + (f"open Airbnb {m['open_airbnb_mean']}." if beyond else
               f"open Airbnb {m['open_airbnb_mean']} vs matched p90 {m['matched_open_p90']}.")
        )
    lines.extend(["", "Flywheel: Visibility > Bookings > Reviews > Ranking"])
    for key in pack["flywheel"]["order"]:
        s = pack["flywheel"]["spokes"][key]
        lines.append(f"{key}: {'ok' if s['ok'] else 'unreadable'}, {s['detail']}")
    market = pack["flywheel"].get("market") or {}
    if beyond:
        from _beyond_runner import GAP_PILE, GAP_RULES
        lines.append("market layer: PriceLabs Market Research does not apply to Beyond; the market "
                     "reference used is named in GAPS.")
        lines.extend(["", f"Rule effectiveness: {GAP_RULES}", f"Suggestions pile: {GAP_PILE}"])
    else:
        lines.append(f"market layer: {'ok' if market.get('ok') else 'absent'}, {market.get('detail', '')}")
        lines.extend(["", "Rule effectiveness (D14c; the window vs the rest, market as yardstick):"])
    for e in pack.get("rule_effectiveness", []):
        lines.append(f"{e['rule']}: {e['verdict']}. {e['why']}")
    withheld = [r for r in pack.get("daily", []) if r.get("withheld_reason")]
    if withheld:
        lines.extend(["", "Pricing opinion withheld (sync mismatch, this date only):"])
        for r in withheld[:10]:
            lines.append(f"  {r['date']}: {r['withheld_reason']}")
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

    def known(value, unit=""):
        return "unknown" if value is None else f"{value}{unit}"

    lines.append(
        f"Completed-stay cohort: median booking lead {known(booking['median_lead_days'], 'd')}; "
        f"one-night bookings {known(booking['one_night_bookings_pct'], '%')}; "
        f"mean stay {known(booking['mean_los'], ' nights')}."
    )
    lines.append(
        f"Pickup: {pms['pickup']['last_24h']['confirmed_positive_value_bookings']} "
        "confirmed creations in 24h; "
        f"{pms['pickup']['last_7d']['confirmed_positive_value_bookings']} in 7d."
    )
    lines.extend(
        ["", "Dated price review scenarios, requiring framework/event review and approval:"]
    )
    ref_words = {"beyond_benchmark_avg": "Beyond benchmark avg", "airroi_adr_p75": "AirROI ADR p75",
                 "airroi_adr_p25": "AirROI ADR p25"}
    for r in pack["candidates"]:
        lines.append(
            f"{r['date']}: net {r['net']:g}, Airbnb {r['airbnb']:g}, "
            + (f"vs {ref_words.get(r.get('reference_source'), 'reference')} {r['reference']:g}; "
               if beyond else f"comp p75/p90 {r['p75']:g}/{r['p90']:g}; ")
            + f"review {r.get('direction', 'cut')} net {r['review_net_range'][0]}-"
            f"{r['review_net_range'][1]}; layer {r['layer']}."
        )
    if not pack["candidates"]:
        lines.append(
            "None emitted." if not pack["blockers"] else "Withheld because a required gate failed."
        )
    lines.append(
        f"Reconciliation: {pack['reconciliation']['open_dates_checked']} open dates checked; "
        f"{len(pack['reconciliation']['mismatches'])} unexplained mismatches "
        "(withheld date by date; the whole run blocks above 20% of open nights); "
        f"{len(pack['reconciliation']['held_dates_absent_from_pl'])} pending-held dates "
        f"absent from {tool}, excluded from candidates."
        + (" Min stay not compared (Beyond has none per night)." if beyond else "")
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
