"""Join verified snapshots once; expose compact facts and dated review candidates."""

from __future__ import annotations

import json
import math
from datetime import date as Date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean

import attribution
import flywheel
import rules_first
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


def _cell(value):
    """A card never prints Python's None: an empty window has no mean, so say n/a."""
    return "n/a" if value is None else value


def average(values):
    vals = [v for v in values if v is not None]
    return rounded(mean(vals)) if vals else None


DEFAULT_MAX_DELTA = 0.15
# A whole-run block only when mismatches are this share of open nights or more. Below it,
# only the mismatched dates lose their pricing opinion; the rest are still priced.
MISMATCH_BLOCK_SHARE = 0.20
STALE_WARN_HOURS = 24   # PriceLabs recalculates about daily
STALE_BLOCK_HOURS = 48  # past this a card blocks (Ryan 2026-09-25)
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
    # "Next 30 nights" is the table's 30-night window: the first NEAR_TERM_DAYS rows of the
    # window. After the evening UTC rollover the window starts tomorrow and days_out starts at
    # 1 (it counts from the property-local today), so `0 <= days_out < 30` kept only 29 nights
    # and printed a second occupancy under the table's (live 2026-09-25: Boho 31% vs 30.0,
    # Sunburst 46% vs 44.44, Olde Town 56% vs 53.85). Anchor on the window's first night.
    first = min((r["days_out"] for r in rows), default=0)
    near = [r for r in rows if 0 <= r["days_out"] - first < NEAR_TERM_DAYS]
    # ONE occupancy definition on the card: the table's (paid nights over non-blocked nights,
    # _mvp_pms.forward). Live 2026-09-25 (The Apres Arcade) this function counted four $0
    # nights as demand, printed "13% booked vs the market's 13%" under a table saying 0%, and
    # kept a floor that 26 of 26 open nights were pinned to. A $0 stay did not sell at the min.
    open_near = [r for r in near if r["status"] == "open"]
    floor_open = sum(1 for r in open_near if r["at_floor"])
    booked = [r for r in near if r["status"] == "confirmed_paid"]
    floor_sold = sum(1 for r in booked if r["at_floor"])
    bookable = [r for r in near if r["status"] != "blocked"]
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
          pile=None, today=None, pricing="pricelabs", comps=None, rank_gap=None):
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
    pending_pushes, unseen_bookings = [], []
    try:
        pl_refreshed = datetime.fromisoformat(str(prices.get("last_refreshed_at")).replace("Z", "+00:00"))
        pl_refreshed = pl_refreshed if pl_refreshed.tzinfo else None
    except ValueError:
        pl_refreshed = None
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
            # PriceLabs recalculates each listing about once a day, so a card read just before
            # the nightly run is 23-25 hours old (measured live 2026-09-25: six of seven Solnest
            # listings at 23.6-23.8h). Up to 48h prices with a loud warning at the top; past 48h,
            # or future-dated, it blocks. Ryan 2026-09-25.
            if age < 0:
                blockers.append("PriceLabs calculated prices are future-dated")
            elif age > STALE_BLOCK_HOURS:
                blockers.append(f"PriceLabs calculated prices are more than {STALE_BLOCK_HOURS} hours old "
                                f"({rounded(age):g}h); hit Sync Now in PriceLabs, then run again")
            elif age > STALE_WARN_HOURS:
                notes.append(f"STALE PRICELABS DATA: PriceLabs last recalculated {rounded(age):g} hours ago "
                             "(it normally does this daily). Hit Sync Now in PriceLabs for the freshest "
                             "numbers; this card is priced from that last recalculation.")
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
    if not rank["ok"] and rank_gap:
        rank["detail"] = rank_gap  # why it is missing, same as the funnel reason above

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
        pms_price = day["price_cents"] / 100 if day["price_cents"] is not None else None
        price_drift = pms_price is not None and abs(pms_price - net) > 0.011
        stay_drift = (not beyond  # Beyond has no per-night min stay: that half is a named gap
                      and day["min_stay"] is not None and day["min_stay"] != price.get("min_stay"))
        # MEASURED LIVE 2026-09-25 (Azure Palms): PriceLabs recalculated and reported a push at
        # 01:36Z; 82 minutes later, on 21 of 27 open nights the PMS still equalled PriceLabs
        # `user_price` (its previous price) to the cent, 1-3% off the new `price`. Whether the
        # push lands later is NOT known, so this is not called "working". It is scoped to the
        # dates (withheld, named at the top) instead of blocking the whole card as "sync
        # broken", because the PMS provably holds a PriceLabs price. SKILL Step 5: both fields.
        pushed = number(price.get("user_price"))
        pending_push = bool(opened and price_drift and not stay_drift and pushed is not None
                            and pushed > 0 and abs(pms_price - pushed) <= 0.011)
        if pending_push:
            pending_pushes.append({"date": date, "pms": pms_price, "pushed": pushed, "recalculated": net})
        drift = opened and not pending_push and (price_drift or stay_drift)
        paid_gap = classification == "confirmed_paid" and pl_status != "RESERVED"
        # MEASURED LIVE 2026-09-25 (Boho Bliss): an Airbnb booking created 03:06Z for 13 nights;
        # PriceLabs last refreshed 07:48Z the day before, so it could not show it, and the whole
        # card blocked as "the sync itself is broken". A booking made AFTER PriceLabs' last
        # refresh is one PriceLabs has not seen yet: named, withheld, not a broken sync.
        if paid_gap and pl_refreshed and day.get("booked_at"):
            try:
                unseen = datetime.fromisoformat(day["booked_at"].replace("Z", "+00:00")) > pl_refreshed
            except ValueError:
                unseen = False
            if unseen:
                paid_gap = False
                unseen_bookings.append(date)
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
        if pending_push:
            row["withheld_reason"] = (
                f"PriceLabs' newer price {net:g} is not in the PMS, which still shows PriceLabs' "
                f"previous price {pushed:g}; rerun after the next sync")
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
            if mismatched or pending_push:
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
    # Rules first, then DSOs (Ryan 2026-09-25). A blocked run proposes nothing: no candidates,
    # and no grading-only rule change either.
    rules_plan = None
    if not beyond:
        rules_plan = rules_first.recommend(
            rows, [] if blockers else candidates, rules.get("raw") or {}, rules.get("levels") or {},
            [] if blockers else rule_effect, bounds, max_delta, overrides, today_date)
        for c in candidates:
            c["layers"] = rules_plan["layers"].get(c["date"], [])
            if c["date"] in rules_plan["folded"]:
                c["folded_into"] = rules_plan["folded"][c["date"]]
            elif c["date"] in rules_plan["why_dso"]:
                c["why_dso"] = rules_plan["why_dso"][c["date"]]
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
                "zero_value": month.get("zero_value_accepted_nights", 0),
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
            "pending_push": pending_pushes,
            "booked_after_pricelabs_refresh": unseen_bookings,
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
        "rules_first": None if beyond else {
            "stack": rules.get("stack") or rules_first.stack_rows(
                rules.get("raw") or {}, rules.get("levels") or {}),
            "gaps": list(rules.get("gaps") or []),
            "rule_changes": rules_plan["rule_changes"],
            "dso_dates": rules_plan["dso_dates"],
            "existing_dsos": rules_plan["existing_dsos"],
            "notes": rules_plan["notes"],
            "thresholds": rules_plan["thresholds"],
            "target": {"listing_id": listing.get("id"), "pms": listing.get("pms")},
        },
        "pms": {k: v for k, v in pms.items() if k not in {"daily", "property"}},
        "comp_count": market.get("listings_used"),
        "custom_comp_set": market.get("custom_comp_set"),
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


EXISTING_DSO_LINES = 10


def _candidate_line(r, beyond):
    ref_words = {"beyond_benchmark_avg": "Beyond benchmark avg", "airroi_adr_p75": "AirROI ADR p75",
                 "airroi_adr_p25": "AirROI ADR p25"}
    line = (f"{r['date']}: net {r['net']:g}, Airbnb {r['airbnb']:g}, "
            + (f"vs {ref_words.get(r.get('reference_source'), 'reference')} {r['reference']:g}; "
               if beyond else f"comp p75/p90 {r['p75']:g}/{r['p90']:g}; ")
            + f"review {r.get('direction', 'cut')} net {r['review_net_range'][0]}-"
            f"{r['review_net_range'][1]}; layer {r['layer']}")
    if r.get("layers") is not None:
        line += f"; layers: {', '.join(r['layers']) or 'none active'}"
    if r.get("why_dso"):
        line += f". Why a DSO: {r['why_dso']}"
    return line + "."


def render_rules_first(pack, beyond):
    """Rule changes first, then the DSO suggestions no rule explains, then the DSOs already set.
    Each with its reason. Beyond has no rule stack in the runner: its section says so."""
    lines = [""]
    rf = pack.get("rules_first")
    residual = [r for r in pack["candidates"] if not r.get("folded_into")]
    if beyond or not rf:
        lines.append("Dated price review scenarios, requiring framework/event review and approval "
                     "(Beyond: no rule stack is read, so there is no rules-first step; a named gap):")
        lines += [_candidate_line(r, beyond) for r in residual]
        if not residual:
            lines.append("None emitted." if not pack["blockers"] else "Withheld because a required gate failed.")
        return lines
    lines.append("RULES FIRST, THEN DATE OVERRIDES. Rule changes are applied before any DSO; the DSO "
                 "list holds only the nights no rule explains.")
    lines.append("Rule stack (what PriceLabs applies here; listing > group > account):")
    for s in rf["stack"]:
        lines.append(f"  {s['rule']} [{s['level']}]: {s['setting']}")
    for g in rf["gaps"]:
        lines.append(f"  GAP: {g}")
    changes = rf["rule_changes"]
    days = pack["window"]["days"]
    lines.append(f"1) Rule changes ({len(changes)}):")
    target = rf.get("target") or {}
    for i, ch in enumerate(changes, 1):
        lines.append(f"  R{i}. {ch['summary']} [{ch['level']} level]. Touches {ch['touches_open']} open "
                     f"night(s) in the next {days} days ({ch['touches_nights']} nights in its window), "
                     "and every later date in the window until changed.")
        lines.append(f"      Why: {ch['why']}.")
        if ch["folded"]:
            shown = ", ".join(ch["folded"][:8]) + (" ..." if len(ch["folded"]) > 8 else "")
            lines.append(f"      Folds {len(ch['folded'])} review night(s) into this change (no DSO for "
                         f"them): {shown}")
        if ch.get("blocked_by_fixed_dso"):
            lines.append(f"      Not reached: {len(ch['blocked_by_fixed_dso'])} open night(s) in the "
                         "window carry a fixed DSO, which the rule cannot move.")
        for w in ch.get("warnings") or []:
            lines.append(f"      ! {w}")
        if ch["writable"] and target.get("listing_id") and target.get("pms"):
            spec = {"listing_id": target["listing_id"], "pms": target["pms"],
                    "reason": f"rules first: {ch['summary']}", "rules_set": ch["change"]}
            lines.append(f"      Change file: {json.dumps(spec, separators=(', ', ': '))}")
        elif ch["writable"]:
            lines.append(f"      rules_set: {json.dumps(ch['change'])}")
        else:
            lines.append(f"      NOT WRITABLE BY THE WRITER: {ch['refusal']}.")
    if not changes:
        lines.append("  No rule change." + ("" if not pack["blockers"] else " Withheld because a required gate failed.")
                     + " No rule that is ON with a readable number explains a pattern "
                     f"(needs {rf['thresholds']['RULE_MIN_NIGHTS']}+ open nights, more than "
                     f"{rf['thresholds']['RULE_PATTERN_SHARE']:.0%} of the window, and "
                     f"{rf['thresholds']['RULE_CONTRAST_PP']:g} points more than outside it).")
    for n in rf["notes"]:
        lines.append(f"  note: {n}")
    lines.append(f"2) DSO suggestions: the nights no rule explains ({len(residual)}):")
    lines += [f"  {_candidate_line(r, beyond)}" for r in residual]
    if not residual:
        lines.append("  No DSO suggestions." if not pack["blockers"] else "  Withheld because a required gate failed.")
    existing = rf["existing_dsos"]
    flagged = [e for e in existing if e["flags"]]
    counts = {}
    for e in flagged:
        for f in e["flags"]:
            key = f.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
    lines.append(f"3) Existing DSOs in the window ({len(existing)}; {len(flagged)} flagged"
                 + (": " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) if counts else "")
                 + "):")
    for e in flagged[:EXISTING_DSO_LINES]:
        lines.append(f"  {e['date']}: {e['shown']}. " + "; ".join(e["flags"]) + ".")
    if len(flagged) > EXISTING_DSO_LINES:
        lines.append(f"  ... {len(flagged) - EXISTING_DSO_LINES} more flagged; all are in --details.")
    lines.append("  GAP: group- and account-level DSOs are not read here (PriceLabs applies a group or "
                 "account % override ahead of the listing's own); check the Group view in PriceLabs.")
    return lines


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
    # SKILL: a degraded card names its gaps at the top. Beyond has its GAPS block; on the
    # PriceLabs path the flywheel's "PRICED WITHOUT ..." sat ~50 lines down (live 2026-09-25).
    top_gaps = ([n for n in pack.get("notes", []) if str(n).startswith("PRICED WITHOUT")]
                if not beyond and pack.get("status") == "degraded" else [])
    top_gaps = [n for n in pack.get("notes", []) if str(n).startswith("STALE PRICELABS DATA")] + top_gaps
    lines.extend(top_gaps)
    mism = pack.get("reconciliation", {}).get("mismatches") or []
    if mism:
        dates = ", ".join(m["date"] for m in mism[:8]) + (" ..." if len(mism) > 8 else "")
        lines.append(
            f"SYNC MISMATCH on {len(mism)} date(s), pricing withheld on those dates only: "
            f"{dates}. PMS and {tool} disagree; fix the sync, then rerun."
        )
    unseen = pack.get("reconciliation", {}).get("booked_after_pricelabs_refresh") or []
    if unseen:
        lines.append(
            f"NEW BOOKING NOT IN PRICELABS YET: {len(unseen)} night(s) ({unseen[0]} to {unseen[-1]}) were "
            f"booked after PriceLabs last refreshed ({pack['price_freshness'].get('calculated_at')}). They "
            "are booked, so nothing is priced there; PriceLabs' other prices may move once it syncs.")
    pend = pack.get("reconciliation", {}).get("pending_push") or []
    if pend:
        dates = ", ".join(m["date"] for m in pend[:8]) + (" ..." if len(pend) > 8 else "")
        lines.append(
            f"PRICELABS UPDATE NOT IN THE PMS on {len(pend)} date(s), pricing withheld on those "
            f"dates only: {dates}. The PMS still shows PriceLabs' previous price. If a rerun after "
            "the next PriceLabs sync still shows this, check the PriceLabs to PMS connection."
        )
    lines += [
        f"Run {run_id}. Bounds min/base/max: {pack['bounds']['min']:g}/"
        f"{pack['bounds']['base']:g}/{ceiling}. "
        f"Airbnb markup {pack['markups']['airbnb']:g}%, confirmed listing setting.",
        (f"Pricing tool Beyond: calendar read at {pack['price_freshness']['read_at']} (Beyond gives no "
         f"calculation time); market: {pack.get('market_source') or 'unavailable'}. Full source "
         "timestamps are in --details." if beyond else
         f"PriceLabs calculated {pack['price_freshness']['age_hours']} hours ago; "
         f"market comps: {comp_txt}"
         + (f" (your PriceLabs custom comp set '{pack['custom_comp_set']}', not a bedroom bucket)"
            if pack.get("custom_comp_set") else "")
         + ". Full source timestamps are in --details."),
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
            + f". {str(mp['reason']).rstrip('. ')}."
        )
    lines += [
        "",
        "occ% = confirmed nights / bookable nights (owner-blocked nights excluded).",
        "days | confirmed | $0 stay | held | open | blocked | occ% | market% | open Airbnb | "
        + ("market reference" if beyond else "matched p50/p90"),
        # every night lands in exactly one column now: a $0 stay used to vanish from the table
        # while still sitting in the occupancy denominator (live 2026-09-25).
    ]
    for w in pack["windows"]:
        lines.append(
            f"{w['days']} | {w['confirmed']} | {w['zero_value']} | {w['held']} | {w['open']} | "
            f"{w['blocked']} | {_cell(w['occupancy_pct'])} | "
            f"{_cell(w['market_occupancy_pct'])} | {_cell(w['open_airbnb_mean'])} | "
            + (f"{_cell(w.get('open_reference_mean'))}" if beyond
               else f"{_cell(w['matched_open_p50'])}/{_cell(w['matched_open_p90'])}")
        )
    lines.extend(["", "Calendar-month slices (partial first/last month):"])
    for m in pack["months"]:
        lines.append(
            f"{m['month']}: {m['days']} nights, {m['confirmed']} confirmed, "
            f"{m['zero_value']} at $0, {m['held']} held, {m['open']} open; "
            f"occupancy {_cell(m['occupancy_pct'])}% of {m['bookable']} bookable "
            f"({m['blocked']} owner-blocked) vs market {m['market_occupancy_pct']}%; "
            + ("no open nights to price." if not m["open"] else
               f"open Airbnb {m['open_airbnb_mean']}." if beyond else
               f"open Airbnb {m['open_airbnb_mean']} vs matched p90 {m['matched_open_p90']}.")
        )
    lines.extend(["", "Flywheel: Visibility > Bookings > Reviews > Ranking"])
    for key in pack["flywheel"]["order"]:
        s = pack["flywheel"]["spokes"][key]
        # "ok" used to mean only "readable", so a funnel breaking at booking_rate 0.99 vs a
        # comp-set 34.19 printed as "visibility: ok" (live, 2026-09-25). A readable spoke
        # that FAILS its own diagnosis or carries a flag says so.
        broken = (s.get("diagnosis") or {}).get("verdict") == "break"
        state = ("unreadable" if not s["ok"] else "BREAK" if broken
                 else "FLAG" if s.get("flag") else "ok")
        lines.append(f"{key}: {state}, {s['detail']}")
    if pack["flywheel"].get("headline"):
        # flywheel.gate computes this every run; the card never printed it.
        lines.append(f"diagnosis: {pack['flywheel']['headline']}")
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
        lines.extend(["", "Pricing opinion withheld (this date only):"])
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
    lines.extend(render_rules_first(pack, beyond))
    lines.append(
        f"Reconciliation: {pack['reconciliation']['open_dates_checked']} open dates checked; "
        f"{len(pack['reconciliation']['mismatches'])} unexplained mismatches "
        "(withheld date by date; the whole run blocks above 20% of open nights); "
        f"{len(pack['reconciliation']['held_dates_absent_from_pl'])} pending-held dates "
        f"absent from {tool}, excluded from candidates."
        + (" Min stay not compared (Beyond has none per night)." if beyond else "")
    )
    pending = pack["reconciliation"].get("pending_push") or []
    if pending:
        lines.append(
            f"{len(pending)} date(s) where the PMS shows PriceLabs' previous price, not its newer "
            "one: withheld date by date, not counted as a broken sync.")
    lines.extend(pack["blockers"] + [n for n in pack["notes"] if n not in top_gaps])
    comps = pack.get("named_comps", {})
    if comps.get("status") == "ok":
        summary, capacity = comps["summary"], comps["capacity_subset"]
        lines.append(
            f"Named comps: {summary['comp_count']} bedroom/bath matches, "
            f"{capacity['count']} also fit guest capacity; trailing-year ADR median "
            f"{_cell(capacity['summary'].get('adr_median'))} for that subset. Not forward pace."
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
