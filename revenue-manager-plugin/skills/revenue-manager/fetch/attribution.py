#!/usr/bin/env python3
"""Attribute an observed price to the layer that produced it.

`ce = price / uncustomized_price` is the total effect of the customization stack on a
date. Verified against live data: a listing with every rule off returns ce == 1.000 on
all seven weekdays.

The ratio is exact in TOTAL and does NOT decompose per rule. Several rules overlap the
same date and the far-out premium covers most of a 365-day window, so this module never
claims a rule contributed a specific percentage. It answers a narrower question: which
rules COULD explain the affected dates, and can any of them be pinned down.

  confirmed  the rule covers every affected date and no unaffected date, and its
             direction matches the direction of the effect
  candidate  it covers some affected dates in the right direction, but also covers
             dates that were not affected, so it cannot be separated from its neighbours
  excluded   it covers none of the affected dates

A market-driven rule type (recommended / conservative / aggressive) has no readable sign
in its config, so its direction is "unknown" and it can never reach "confirmed".

A rule that is toggled OFF is also market-driven: switching a rule off does not disable
an adjustment, it hands the date to the algorithm's market-driven default. An off rule
must be treated the same as a market-driven type: direction is "unknown" and it can never
be confirmed.

Pure functions. No network, no file I/O, no API key.
"""
from __future__ import annotations

from datetime import date

# PriceLabs "no value" markers. Never let these reach arithmetic.
SENTINELS = {-1, -2, "-1", "-2", "-1.0", "-2.0"}
# -1 and -2 collapse to the same set members as -1.0 and -2.0 (in Python -1 == -1.0), so
# writing both floats and ints was 6 literals for 4 real members and read as wider
# coverage than it had. The STRING forms "-1.0"/"-2.0" are the ones that were genuinely
# missing: PriceLabs returns them in text fields, and factcheck.SENTINELS already
# included them, so the two modules disagreed about the same marker.

# Types whose effect is derived from live market data, so the config cannot tell you
# which way the price moved.
MARKET_DRIVEN = {"recommended", "conservative", "aggressive", "moderately_conservative",
                 "moderately_aggressive"}

DOW_KEYS = ["dow_factor_value_mon", "dow_factor_value_tue", "dow_factor_value_wed",
            "dow_factor_value_thu", "dow_factor_value_fri", "dow_factor_value_sat",
            "dow_factor_value_sun"]


def toggle_is_on(raw) -> bool:
    """Read a rule's on/off toggle, which may arrive as a bool OR as a string.

    `bool("false")` is True in Python, and an OFF rule is the dangerous one here: it is
    market-driven, so its direction is unknowable and it must never reach `confirmed`.
    Read raw, a toggle of "false" made a stale -10% Mon/Tue read as a CONFIRMED discount,
    contradicting this module's own docstring above. The same guard already existed in
    reduce_customizations, customization_write and factcheck; the module that actually
    issues the verdicts was the one missing it.
    """
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    return bool(raw)


def to_number(value):
    """Return a float, or None for a sentinel or an unparseable value.

    PRICE-field parser only. -1 and -2 are PriceLabs' "no value" marker on a price (e.g.
    a booked date's user_price) -- use this for `price` / `uncustomized_price` and nothing
    else. A customization config value (a percentage, a days-out threshold) is a different
    domain where -1 and -2 are ordinary in-range numbers; use to_setting() for those.
    """
    if value in SENTINELS:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out in SENTINELS else out


def to_setting(value):
    """Return a float for a customization CONFIG value (a percentage adjustment, or a
    days-out / days-from-departure threshold), or None only for a genuinely missing or
    unparseable value.

    Deliberately does NOT apply to_number()'s sentinel filter. -1 and -2 are PriceLabs'
    "no value" marker on a PRICE field, not on a customization setting: a day-of-week,
    last-minute or far-out percentage is documented -75..1000, so -1% and -2% are ordinary,
    real values here. Filtering them out (reusing the price parser on config data) makes a
    live -1%/-2% rule read as absent, and a read-modify-write built on that reading would
    silently zero it on write. Keep this split -- see rule_covers()/rule_direction() below
    and reduce_customizations.rule_value()/rule_window(), which are the only intended
    callers.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ce_rows(price_rows: list[dict], today: str) -> list[dict]:
    """One row per usable date: the customization effect and the axes to group it by."""
    base = date.fromisoformat(today)
    out = []
    for row in price_rows:
        raw_date = str(row.get("date") or "")
        price = to_number(row.get("price"))
        unc = to_number(row.get("uncustomized_price"))
        if not raw_date or price is None or unc is None or unc <= 0:
            continue
        try:
            when = date.fromisoformat(raw_date)
        except ValueError:
            continue
        out.append({
            "date": raw_date,
            "ce": price / unc,
            "dow": when.weekday(),            # 0 = Monday
            "days_out": (when - base).days,
            "month": raw_date[:7],
        })
    return out


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def group_ce(rows: list[dict], key: str) -> dict[str, dict]:
    """Aggregate ce by weekday, lead-time bucket, or month."""
    if key not in ("dow", "lead", "month"):
        raise ValueError(f"group key must be dow, lead or month, got {key!r}")
    buckets: dict[str, list[float]] = {}
    for row in rows:
        if key == "dow":
            label = str(row["dow"])
        elif key == "month":
            label = row["month"]
        else:
            label = _lead_bucket(row["days_out"])
        buckets.setdefault(label, []).append(row["ce"])
    return {k: {"n": len(v), "median_ce": _median(v), "min_ce": min(v), "max_ce": max(v)}
            for k, v in sorted(buckets.items())}


def _lead_bucket(days_out: int) -> str:
    for edge, label in ((0, "0"), (3, "1-3"), (7, "4-7"), (14, "8-14"),
                        (30, "15-30"), (60, "31-60"), (90, "61-90"), (180, "91-180")):
        if days_out <= edge:
            return label
    return "180+"


def rule_covers(rule: str, cfg: dict, row: dict) -> bool:
    """Does this rule's window include this date?

    A rule that is toggled OFF still covers its window. Switching a rule off does not
    mean "no adjustment": it hands the date back to the algorithm's market-driven
    default, which is still an effect that has to be explained.
    """
    if rule == "day_of_week_adjustment":
        value = to_setting(cfg.get(DOW_KEYS[row["dow"]])) or 0.0
        return value != 0.0 or not toggle_is_on(cfg.get("dow_factor_on"))
    if rule == "last_minute_prices":
        dfd = to_setting(cfg.get("last_min_factor_dfd"))
        return row["days_out"] <= dfd if dfd is not None else True
    if rule == "far_out_premium":
        start = to_setting(cfg.get("far_out_premium_start"))
        return row["days_out"] >= start if start is not None else True
    # seasonality, demand_factor and custom_seasonal_profile have no date window in
    # their config: they apply across the whole horizon.
    return True


def rule_direction(rule: str, cfg: dict, row: dict | None = None) -> str:
    """down, up, none, or unknown. A market-driven type is always unknown."""
    if rule == "day_of_week_adjustment":
        if not toggle_is_on(cfg.get("dow_factor_on")):
            return "unknown"
        if row is None:
            return "unknown"
        value = to_setting(cfg.get(DOW_KEYS[row["dow"]])) or 0.0
        return "down" if value < 0 else "up" if value > 0 else "none"
    if rule == "last_minute_prices":
        if not toggle_is_on(cfg.get("last_min_factor_on")):
            return "unknown"
        kind = cfg.get("last_min_factor_type")
        if kind == "fixed":
            # An absolute nightly price can be above or below the baseline.
            # Its positive currency amount does not imply an upward adjustment.
            return "unknown"
        if kind in MARKET_DRIVEN:
            return "unknown"
        if kind == "none":
            return "none"
        value = to_setting(cfg.get("last_min_factor_value"))
        return "unknown" if value is None else "down" if value < 0 else "up" if value > 0 else "none"
    if rule == "far_out_premium":
        if not toggle_is_on(cfg.get("far_out_premium_on")):
            return "unknown"
        kind = cfg.get("far_out_premium_type")
        if kind in MARKET_DRIVEN:
            return "unknown"
        if kind == "none":
            return "none"
        value = to_setting(cfg.get("far_out_premium_value"))
        return "unknown" if value is None else "down" if value < 0 else "up" if value > 0 else "none"
    # seasonality / demand_factor / custom_seasonal_profile: the config carries a tone
    # or a season set, never a single readable sign for the whole horizon.
    return "unknown"


def classify(affected_dates: set, rows: list[dict], rules: dict) -> list[dict]:
    """The co-incidence test. One verdict per rule, never a per-rule percentage.

    Every verdict carries a `coverage` dict: `affected_requested` is how many dates were
    asked about, `affected_with_ce` is how many of those produced a usable ce row. They
    differ whenever a date is booked or blocked, because reduce_prices renders a
    sentinel price as an empty string and to_number("") is None.

    When `affected_with_ce` is 0 every verdict is `unknown`, never `excluded`. Six rules
    each claiming "covers none of the affected dates" from zero evidence is six confident
    claims of non-involvement built on nothing, and it routes the operator to base/min for
    a move a customization actually made. Same doctrine as exit 2: "cannot produce a
    trustworthy answer" is never "there is none".
    """
    affected_rows = [r for r in rows if r["date"] in affected_dates]
    other_rows = [r for r in rows if r["date"] not in affected_dates]
    coverage = {"affected_requested": len(affected_dates),
                "affected_with_ce": len(affected_rows)}

    if not affected_rows:
        return [{"rule": rule, "verdict": "unknown", "direction": "n/a",
                 "covered_affected": 0, "covered_unaffected": 0, "coverage": dict(coverage),
                 "why": f"no usable ce row for any of the {len(affected_dates)} requested "
                        "dates (booked, blocked or missing): cannot attribute"}
                for rule in rules]

    # Direction of the effect itself, from the affected dates. An exactly-neutral
    # median is not an "up" effect: there is no move to attribute, and treating the
    # tie as `up` let a positive rule be CONFIRMED as the cause of nothing happening.
    _med = _median([r["ce"] for r in affected_rows])
    effect = "down" if _med < 1.0 else "up" if _med > 1.0 else "none"

    # `confirmed` is a claim about EVERY date that was asked about. When some of them
    # produced no usable ce row, the evidence covers a subset, so the strongest honest
    # verdict is `candidate`. Reporting coverage alongside a `confirmed` is not enough:
    # the verdict is what gets read and acted on.
    full_coverage = coverage["affected_with_ce"] == coverage["affected_requested"]

    out = []
    for rule, cfg in rules.items():
        hit = [r for r in affected_rows if rule_covers(rule, cfg, r)]
        # An unaffected date only counts against a rule when the rule pushes that date
        # the SAME way as the effect being explained. A day-of-week rule that discounts
        # Mon/Tue and adds a premium on Fri/Sat is not made ambiguous by the Fri/Sat
        # dates: it moves them the other way, so they are not a competing explanation.
        miss = [r for r in other_rows
                if rule_covers(rule, cfg, r)
                and rule_direction(rule, cfg, r) in (effect, "unknown")]
        if not hit:
            out.append({"rule": rule, "verdict": "excluded", "direction": "n/a",
                        "covered_affected": 0, "covered_unaffected": len(miss), "coverage": dict(coverage),
                        "why": "covers none of the affected dates"})
            continue
        directions = {rule_direction(rule, cfg, r) for r in hit}
        direction = directions.pop() if len(directions) == 1 else "unknown"
        if effect == "none":
            # Nothing moved on these dates, so no rule can be shown to have moved them.
            out.append({"rule": rule, "verdict": "candidate", "direction": direction,
                        "covered_affected": len(hit), "covered_unaffected": len(miss),
                        "coverage": dict(coverage),
                        "why": "the measured effect on these dates is exactly neutral "
                               "(ce == 1.0): there is no move to attribute"})
            continue
        if direction == "unknown":
            out.append({"rule": rule, "verdict": "candidate", "direction": "unknown",
                        "covered_affected": len(hit), "covered_unaffected": len(miss), "coverage": dict(coverage),
                        "why": "market-driven or mixed: the config carries no readable sign"})
        elif direction != effect:
            out.append({"rule": rule, "verdict": "excluded", "direction": direction,
                        "covered_affected": len(hit), "covered_unaffected": len(miss), "coverage": dict(coverage),
                        "why": f"moves prices {direction}, the effect is {effect}"})
        elif len(hit) == len(affected_rows) and not miss and full_coverage:
            out.append({"rule": rule, "verdict": "confirmed", "direction": direction,
                        "covered_affected": len(hit), "covered_unaffected": 0, "coverage": dict(coverage),
                        "why": "covers every affected date and no unaffected date"})
        elif len(hit) == len(affected_rows) and not miss:
            out.append({"rule": rule, "verdict": "candidate", "direction": direction,
                        "covered_affected": len(hit), "covered_unaffected": 0, "coverage": dict(coverage),
                        "why": f"covers every date with a readable price, but only "
                               f"{coverage['affected_with_ce']} of "
                               f"{coverage['affected_requested']} requested dates could "
                               "be measured"})
        else:
            out.append({"rule": rule, "verdict": "candidate", "direction": direction,
                        "covered_affected": len(hit), "covered_unaffected": len(miss), "coverage": dict(coverage),
                        "why": f"also covers {len(miss)} unaffected dates, cannot be separated"})
    return out


# ---------------------------------------------------------------------------
# Rule effectiveness (PRD D14c, Ryan-stated 2026-09-21)
# ---------------------------------------------------------------------------

# Fewer dates than this on either side and no verdict is issued. A rule judged on
# three nights is a coin flip wearing a conclusion.
MIN_DATES_PER_SIDE = 7

# Subject-minus-market occupancy gap, in points. The rule's window is compared to the
# dates outside it on the SAME yardstick, so lead time cancels out.
EFFECT_TOLERANCE_PP = 5.0


def _occ(rows: list[dict]) -> float | None:
    """Occupancy over bookable nights. Blocked nights are not in the denominator."""
    bookable = [r for r in rows if not r.get("blocked")]
    if not bookable:
        return None
    return 100.0 * sum(1 for r in bookable if r.get("booked")) / len(bookable)


def _market_occ(rows: list[dict]) -> float | None:
    vals = [to_number(r.get("market_occ")) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def rule_effectiveness(rules: dict, daily: list[dict]) -> list[dict]:
    """Is each configured rule doing its job? One verdict per rule, never a percentage.

    `daily` rows carry date, days_out, dow, booked, blocked and (optionally) market_occ
    for the same date. For each rule the horizon is split into the dates the rule's
    window covers and the dates it does not (rule_covers), and the SUBJECT'S GAP TO THE
    MARKET is measured on each side. Comparing raw occupancy inside vs outside a window
    is confounded by lead time (a far-out window is always emptier), so the market on
    the same dates is the yardstick. When no market occupancy is available the raw
    comparison is reported and labelled confounded.

    framework.md 5.5: "Compare occupancy inside the window against outside it. If those
    dates are booking fine, the rule is working."

    Verdicts:
      working        the window is holding up as well as, or better than, the rest
      underperforming the window trails the rest by more than EFFECT_TOLERANCE_PP
      off            the rule is toggled off and hands its dates to the market default
      no_window      the rule has no date window (whole-horizon rules), nothing to split
      unknown        too few dates on one side, or no readable occupancy
    """
    out = []
    for rule, cfg in rules.items():
        cfg = cfg if isinstance(cfg, dict) else {}
        toggle_key = {"day_of_week_adjustment": "dow_factor_on",
                      "last_minute_prices": "last_min_factor_on",
                      "far_out_premium": "far_out_premium_on",
                      "seasonality": "seasonality_customization_on",
                      "demand_factor": "tone_demand_factor_on",
                      "custom_seasonal_profile": "custom_seasonal_profile_on"}.get(rule)
        on = toggle_is_on(cfg.get(toggle_key)) if toggle_key else False
        entry = {"rule": rule, "on": on}
        if not on:
            entry.update(verdict="off",
                         why="toggled off; its dates run on the market-driven default, "
                             "which is still an effect but not one this rule owns")
            out.append(entry)
            continue
        if rule not in ("day_of_week_adjustment", "last_minute_prices", "far_out_premium"):
            entry.update(verdict="no_window",
                         why="applies across the whole horizon; there is no inside/outside "
                             "to compare. Judge it from the attribution and the market curve.")
            out.append(entry)
            continue
        inside = [r for r in daily if rule_covers(rule, cfg, r)]
        outside = [r for r in daily if not rule_covers(rule, cfg, r)]
        entry.update(inside_dates=len(inside), outside_dates=len(outside))
        if len(inside) < MIN_DATES_PER_SIDE or len(outside) < MIN_DATES_PER_SIDE:
            entry.update(verdict="unknown",
                         why=f"only {len(inside)} dates inside the window and "
                             f"{len(outside)} outside; fewer than {MIN_DATES_PER_SIDE} on a "
                             "side is a coin flip, not a verdict")
            out.append(entry)
            continue
        occ_in, occ_out = _occ(inside), _occ(outside)
        mkt_in, mkt_out = _market_occ(inside), _market_occ(outside)
        entry.update(occ_inside=occ_in, occ_outside=occ_out,
                     market_inside=mkt_in, market_outside=mkt_out)
        if occ_in is None or occ_out is None:
            entry.update(verdict="unknown", why="no bookable nights on one side")
            out.append(entry)
            continue
        if mkt_in is not None and mkt_out is not None:
            gap_in, gap_out = occ_in - mkt_in, occ_out - mkt_out
            entry.update(gap_to_market_inside=round(gap_in, 1),
                         gap_to_market_outside=round(gap_out, 1), yardstick="market")
            delta = gap_in - gap_out
            basis = (f"vs the market on the same dates: inside the window the listing runs "
                     f"{gap_in:+.1f} pts, outside {gap_out:+.1f} pts")
        else:
            delta = occ_in - occ_out
            entry.update(yardstick="raw (confounded by lead time)")
            basis = (f"raw occupancy {occ_in:.1f}% inside vs {occ_out:.1f}% outside; NO market "
                     "yardstick, so lead time is not controlled for")
        if delta < -EFFECT_TOLERANCE_PP:
            entry.update(verdict="underperforming",
                         why=f"the window trails by {abs(delta):.1f} pts. {basis}")
        else:
            entry.update(verdict="working",
                         why=f"the window is holding ({delta:+.1f} pts). {basis}")
        out.append(entry)
    return out
