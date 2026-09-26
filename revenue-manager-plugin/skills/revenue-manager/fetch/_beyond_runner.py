"""Beyond as the 90-day runner's pricing tool: a DEGRADED mode where every input Beyond does
not supply is a named gap on the card. Never a crash, never a silent guess.

What the PriceLabs path has that Beyond's API does not (references/beyond.md), and what the
runner does instead, each said on the card:

  market percentiles  Beyond market insights give per-date benchmark AVERAGES (posted rate,
                      booked rate, occupancy), no p25-p90, in the owner's billing currency.
                      Used when that currency is the listing's; otherwise "market comparison
                      unavailable in Beyond's API" (no exchange rate is documented). AirROI
                      comps, when connected, are the reference where Beyond's is missing.
  rule attribution    PriceLabs-only; Beyond's rules are not graded.
  per-date min stay   not on Beyond's calendar: the PMS sync check runs its price and booking
                      halves only.
  calculation time    none: freshness is the time the calendar was READ.
  max price           may be blank: treated as no ceiling, never invented.
  suggestions pile    none like PriceLabs' actions/nudges: skipped.

The min price is still an OUTPUT (SKILL.md 2.1), from the SAME rule as PriceLabs
(_mvp_analysis.min_price_recommendation): Beyond's current min, nights at the floor, pace
against Beyond's benchmark occupancy, and AirROI's ADR p25 as the named lower quartile.
Review scenarios: a CUT is measured against Beyond's benchmark average (else AirROI ADR p75);
a RAISE needs a lower reference, which only AirROI's ADR p25 is (an average is not one).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from _beyond import BeyondSource
from _calendar import pricelabs_status, validate_calendar
from _mvp_store import CannotAnalyze

TOOL = "beyond"
# Beyond availability -> the status words the runner's calendar check already reads.
# `unavailable` ("cannot be booked for an unknown reason", docs) is not bookable: BLOCKED.
STATUS = {"available": "", "booked": "booked", "blocked": "blocked", "unavailable": "blocked"}

GAP_RULES = "Rule check is PriceLabs-only; Beyond rules not graded."
GAP_MIN_STAY = ("Beyond's calendar has no per-night min stay, so the PMS sync check compared "
                "prices and bookings only; the min-stay half was skipped.")
GAP_PILE = ("Beyond has no suggestions list like PriceLabs' actions and nudges; skipped "
            "(Beyond's base-price recommendations are not read by this runner).")
GAP_CEILING = "No max price is set in Beyond: treated as no ceiling. None was invented."
GAP_NO_MARKET = "market comparison unavailable in Beyond's API"
GAP_NO_RAISE = ("No raise scenarios: they need a lower reference and Beyond gives averages, not a "
                "lower quartile; AirROI's ADR p25 would supply one when connected.")


# ------------------------------------------------------------------------------ adapters

def runner_listing(row: dict) -> dict:
    """The runner's listing shape. Beyond's min/base/max are major units; max may be None."""
    return {"id": row["id"], "pms": TOOL, "name": row.get("name"), "currency": row["currency"],
            "min": row.get("min"), "base": row.get("base"), "max": row.get("max"),
            "no_of_bedrooms": row.get("no_of_bedrooms"), "latitude": row.get("latitude"),
            "longitude": row.get("longitude"), "enabled": row.get("enabled"),
            "in_active_market": row.get("in_active_market")}


def runner_prices(cal: dict, start: date, days: int) -> dict:
    """Beyond calendar -> the runner's price rows. `price` is what guests are quoted: Beyond's
    `price-posted`, falling back to `price` when never posted (docs tip). The modeled price is
    kept as `suggested` for floor pinning. `min_stay` stays None: Beyond has none per night."""
    rows = []
    for r in cal["data"]:
        quoted = r["price_posted"] if r.get("price_posted") is not None else r["price"]
        rows.append({
            "date": r["date"], "price": quoted, "suggested": r["price"],
            "min_stay": None,
            "booking_status": STATUS.get(r.get("availability"), f"undocumented:{r.get('availability')}"),
            "unbookable": 0,
            "effective_min_price": r.get("effective_min_price"),
            "effective_max_price": r.get("effective_max_price"),
            "override_type": r.get("override_type"),
        })
    end = start + timedelta(days=days - 1)
    validate_calendar(rows, "Beyond", pricelabs_status, start.isoformat(), end.isoformat())
    return {"read_at": cal["read_at"], "data": rows}


def runner_overrides(rows: list) -> list:
    """Beyond overrides -> the runner's override shape (price + price_type)."""
    out = []
    for r in rows:
        if "price" in r:
            out.append({"date": r["date"], "price": r["price"], "price_type": "fixed"})
        else:
            out.append({"date": r["date"], "price": r["percentage_adjustment"], "price_type": "percent"})
    return out


def read_market(source: BeyondSource, lid, start: date, days: int) -> dict:
    """Market insights, or a named reason why not. Never raises: market is optional here."""
    try:
        got = source.market(lid, start, days)
    except CannotAnalyze as exc:
        return {"status": "unavailable", "reason": f"{GAP_NO_MARKET} (market insights could not be read: {exc})"}
    return {"status": "read", **got}


def usable_market(market, listing_currency) -> dict:
    """The market insights the analysis may use, or a named gap. Rates in another currency
    than the listing's are NOT converted: Beyond documents no exchange rate."""
    market = market or {"status": "unavailable", "reason": GAP_NO_MARKET}
    if market.get("status") == "unavailable":
        return {"status": "unavailable", "reason": market.get("reason") or GAP_NO_MARKET, "data": []}
    if market.get("currency") != listing_currency:
        return {"status": "unavailable", "data": [], "reason": (
            f"{GAP_NO_MARKET}: its market insights are priced in your billing currency "
            f"{market.get('currency')}, your listing in {listing_currency}, and Beyond documents no "
            "exchange rate, so nothing was converted")}
    if not market.get("benchmark_available"):
        return {"status": "unavailable", "data": [], "reason": (
            f"{GAP_NO_MARKET}: Beyond has no benchmark coverage for this listing's cohort "
            "(neighborhood or whole market)")}
    missing = sum(r["posted_avg"] is None for r in market["data"])
    return {"status": "ok", "currency": market["currency"], "compare_to": market.get("compare_to"),
            "source": f"Beyond market insights ({market.get('compare_to') or 'cluster'} benchmark, "
                      "averages)",
            "missing_dates": missing, "data": market["data"], "listings_used": None}


def comp_reference(comps) -> dict | None:
    """AirROI's trailing-12-month ADR quartiles, when the runner read them. The capacity
    subset is used when it has at least 3 comps, else the bedroom/bath set."""
    if not isinstance(comps, dict) or comps.get("status") != "ok":
        return None
    sub = comps.get("capacity_subset") or {}
    summary, scope = ((sub.get("summary"), f"{sub.get('count')} comps that fit your guest count")
                      if (sub.get("count") or 0) >= 3
                      else (comps.get("summary"), f"{(comps.get('summary') or {}).get('comp_count')} "
                                                  "bedroom/bath comps"))
    summary = summary or {}
    if summary.get("adr_p25") is None and summary.get("adr_p75") is None:
        return None
    return {"source": f"AirROI trailing-12-month ADR, {scope}", "adr_p25": summary.get("adr_p25"),
            "adr_median": summary.get("adr_median"), "adr_p75": summary.get("adr_p75")}


def gaps(market, listing, prices, comps) -> list:
    """Every input the Beyond card priced without, in plain words, for the top of the card."""
    out = []
    if market["status"] == "ok":
        line = ("Market percentiles: Beyond gives averages, not p25-p90. Nights are compared with "
                f"{market['source']}")
        if market["missing_dates"]:
            line += f"; {market['missing_dates']} night(s) have no benchmark"
        out.append(line + ".")
    else:
        out.append(f"Market percentiles: {market['reason']}.")
    ref = comp_reference(comps)
    if ref:
        out.append(f"Comp source: {ref['source']} (historical booked rates, not forward asks)"
                   + (" also stands in on nights with no Beyond benchmark." if market["status"] == "ok"
                      else " is the only market reference on this card."))
    elif market["status"] != "ok":
        out.append("No comp source: AirROI is not connected or returned no usable comps, so no night is "
                   "compared with a market and no dated price review is emitted.")
    if not (ref and ref.get("adr_p25") is not None):
        out.append(GAP_NO_RAISE)
    out.append(GAP_RULES)
    out.append(GAP_MIN_STAY)
    out.append(f"Freshness: Beyond gives no price-calculation time; prices are as read at "
               f"{prices.get('read_at')}.")
    if listing.get("max") is None:
        out.append(GAP_CEILING)
    out.append(GAP_PILE)
    if listing.get("enabled") is False:
        out.append("Price syncing is OFF for this listing in Beyond: its prices are not reaching the "
                   "channel, so the quoted price may not be Beyond's.")
    return out


def low_reference(comps_ref):
    """(value in Airbnb terms, source code) that a RAISE scenario is measured against: AirROI's
    ADR p25 only. Beyond's benchmark is an average, which is not a lower bound of anything."""
    if comps_ref and comps_ref.get("adr_p25") is not None:
        return comps_ref["adr_p25"], "airroi_adr_p25"
    return None, None


def reference_for(date_key, market_map, comps_ref, multiplier):
    """(value in Airbnb terms, source code) for one night: Beyond's benchmark average posted
    rate on that date, else AirROI's ADR p75 (flat), else (None, None). Both are guest-facing
    rates, compared with the listing's Airbnb price (net x markup)."""
    row = market_map.get(date_key) or {}
    if row.get("posted_avg") is not None:
        return row["posted_avg"], "beyond_benchmark_avg"
    if comps_ref and comps_ref.get("adr_p75") is not None:
        return comps_ref["adr_p75"], "airroi_adr_p75"
    return None, None


# ------------------------------------------------------------------------------ live jobs

def beyond_jobs(settings, client, connections, start: date, days: int) -> dict:
    """The Beyond reads for one run. The listing id comes from setup (settings), never from
    a name lookup at run time."""
    bid = settings.get("beyond_listing_id")
    if not bid:
        raise CannotAnalyze("This property has no Beyond listing on record; run "
                            "setup_properties.py --pricing beyond first")
    src = BeyondSource(client, connections)
    return {
        "listing": lambda: runner_listing(src.listing(bid)),
        "prices": lambda: runner_prices(src.calendar(bid, start, days), start, days),
        "overrides": lambda: runner_overrides(src.overrides(bid, start, days)),
        "market": lambda: read_market(src, bid, start, days),
    }


def read_at_age(prices, as_of: datetime):
    stamp = datetime.fromisoformat(str(prices["read_at"]).replace("Z", "+00:00"))
    return (as_of - stamp).total_seconds() / 3600


def mean(values):
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return round(sum(vals) / len(vals), 2) if vals else None
