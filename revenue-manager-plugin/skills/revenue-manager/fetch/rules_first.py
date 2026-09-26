"""Rules first, then date overrides (DSOs).

Ryan, 2026-09-25: "make sure the revenue manager is looking at all the rules on top of the
DSOs, and we want to adjust the rules first before the DSOs." Design 2026-09-18: a price
complaint is a layer question before it is a number question.

The runner flags nights for review (the review candidates: an open night priced above the comp
p75 inside 14 days, or under the comp p25). This module asks, for every PriceLabs rule that is ON
with a number it can read, whether THAT rule explains those nights. When it does, it proposes ONE
change to the rule, sized from the nights' own gap to the comps and capped by the movement cap,
checked with customization_write's guards, and FOLDS those nights into it. Only the nights no rule
explains stay as per-date DSO suggestions. Existing DSOs are listed and flagged.

Pure: no network, no files. _mvp_analysis.build calls it; the writer (_mvp_write `rules_set`)
carries a proposal out on a plain yes, rules before DSOs.

Thresholds, named so the card can say which one a rule missed:

  RULE_MIN_NIGHTS     3     a pattern needs at least three open nights the rule can move.
                            One bad Tuesday is not a day-of-week problem.
  RULE_PATTERN_SHARE  0.50  MORE than half of the open nights inside the rule's window must want
                            the same move ("most open nights in the window": a strict majority).
  RULE_CONTRAST_PP    20    the co-incidence test: that share must beat the share on comparable
                            open nights OUTSIDE the window by 20 points, or the rule cannot be
                            told apart from whatever else moves those nights. With no comparable
                            night outside, the test cannot run and the card says so.
  GRADED_PRICE_SHARE  0.60  a rule graded `underperforming` (attribution.rule_effectiveness:
                            occupancy inside its window trails the market by more than outside)
                            is a PRICE lever only when 60% of its open nights sit above the comp
                            median, AND the listing books under the market inside the window
                            (a window that still out-books the market, only by less than the
                            other days, is not cut). Otherwise the card says price is not the
                            lever there.
  BOOKING_GUARD_PP    5     the booking check on a pattern, from the same market yardstick the
                            rule grading uses (gap to the market inside the window): a RAISE is
                            not proposed where the listing books more than 5 pts UNDER the market
                            (cheap and still not booking reads as visibility, not price), and a
                            CUT is not proposed where it books more than 5 pts OVER it (it is
                            selling). With no market occupancy the guard cannot run; the card
                            says so.
  CUT_SCOPE_DAYS      14    the runner only reviews cuts inside 14 days (lead-time logic), so a
                            cut pattern is measured on that scope, inside and outside alike.
  DSO_STALE_DAYS      30    an existing DSO last set more than 30 days ago is flagged stale.

A rule OFF, market-driven (recommended / conservative / aggressive) or `none` has no number to
resize; it is shown as a layer and never proposed. Seasonality, the custom seasonal profile and
the demand factor cover every date, so no co-incidence test can single them out; they are shown
as layers too. Group- and account-level rules are shown with their level, and a proposal on one
is display-only: "this changes every listing in the group/account; change it in PriceLabs".
"""

from __future__ import annotations

import math
from datetime import date as Date
from statistics import median

from attribution import DOW_KEYS, rule_covers as _rule_covers, to_setting, toggle_is_on

RULE_MIN_NIGHTS = 3
RULE_PATTERN_SHARE = 0.50
RULE_CONTRAST_PP = 20.0
GRADED_PRICE_SHARE = 0.60
BOOKING_GUARD_PP = 5.0
CUT_SCOPE_DAYS = 14
DSO_STALE_DAYS = 30

THRESHOLDS = {
    "RULE_MIN_NIGHTS": RULE_MIN_NIGHTS, "RULE_PATTERN_SHARE": RULE_PATTERN_SHARE,
    "RULE_CONTRAST_PP": RULE_CONTRAST_PP, "GRADED_PRICE_SHARE": GRADED_PRICE_SHARE,
    "BOOKING_GUARD_PP": BOOKING_GUARD_PP, "CUT_SCOPE_DAYS": CUT_SCOPE_DAYS,
    "DSO_STALE_DAYS": DSO_STALE_DAYS,
}

ALL_RULES = ["seasonality", "last_minute_prices", "far_out_premium",
             "day_of_week_adjustment", "demand_factor", "custom_seasonal_profile"]
TOGGLE_KEY = {
    "seasonality": "seasonality_customization_on",
    "last_minute_prices": "last_min_factor_on",
    "far_out_premium": "far_out_premium_on",
    "day_of_week_adjustment": "dow_factor_on",
    "demand_factor": "tone_demand_factor_on",
    "custom_seasonal_profile": "custom_seasonal_profile_on",
}
# The rules a proposal (and the writer) may resize: each carries one signed number the re-read
# can prove. The other three carry a tone or a season set.
WRITABLE_RULES = ("last_minute_prices", "day_of_week_adjustment", "far_out_premium")
LEVEL_REFUSAL = {
    "group": "this changes every listing in the group; change it in PriceLabs",
    "account": "this changes every listing in the account; change it in PriceLabs",
}
MARKET_TYPES = {"recommended", "conservative", "aggressive", "moderately_conservative",
                "moderately_aggressive"}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LABEL = {"last_minute_prices": "last-minute", "far_out_premium": "far-out premium",
         "day_of_week_adjustment": "day-of-week", "seasonality": "seasonality",
         "demand_factor": "demand factor", "custom_seasonal_profile": "custom seasonal profile"}


def rule_covers(rule: str, cfg: dict, row: dict) -> bool:
    """attribution.rule_covers on a runner row, which carries no `dow`."""
    if "dow" not in row:
        row = dict(row, dow=Date.fromisoformat(row["date"]).weekday())
    return _rule_covers(rule, cfg, row)


def rule_on(rule: str, cfg) -> bool:
    return isinstance(cfg, dict) and toggle_is_on(cfg.get(TOGGLE_KEY.get(rule, "")))


def resolve_stack(listing, group=None, account=None):
    """(effective rules, level per rule).

    PriceLabs' documented hierarchy (help.pricelabs.co, "Understanding Customization and
    Date-Specific Override Hierarchy", read 2026-09-25): a listing-level rule wins, then
    subgroup, then group, then account. A rule switched OFF at the listing level is read as not
    set there, so an ON group or account rule applies (DOCS-ONLY: how PriceLabs treats a listing
    rule that is present but OFF is not measured on a live account). A rule set nowhere is
    `default`: PriceLabs' own market-driven behaviour.
    """
    effective, levels = {}, {}
    for rule in ALL_RULES:
        chosen = None
        for level, block in (("listing", listing), ("group", group), ("account", account)):
            cfg = (block or {}).get(rule) if isinstance(block, dict) else None
            if rule_on(rule, cfg):
                chosen = (level, cfg)
                break
        if chosen is None:
            cfg = (listing or {}).get(rule) if isinstance(listing, dict) else None
            chosen = ("listing", cfg) if isinstance(cfg, dict) else ("default", {})
        levels[rule], effective[rule] = chosen
    return effective, levels


def adjustable(rule: str, cfg) -> bool:
    """ON, with a number this module can resize and the writer can prove by re-reading."""
    if not rule_on(rule, cfg):
        return False
    if rule == "day_of_week_adjustment":
        return all(to_setting(cfg.get(k)) is not None or cfg.get(k) is None for k in DOW_KEYS)
    if rule == "last_minute_prices":
        return (cfg.get("last_min_factor_type") in ("linear", "linear_gradual")
                and to_setting(cfg.get("last_min_factor_value")) is not None
                and to_setting(cfg.get("last_min_factor_dfd")) is not None)
    if rule == "far_out_premium":
        return (cfg.get("far_out_premium_type") in ("linear", "fix")
                and to_setting(cfg.get("far_out_premium_value")) is not None
                and to_setting(cfg.get("far_out_premium_start")) is not None)
    return False


def _pct(v) -> str:
    return f"{v:+g}%"


def describe_rule(rule: str, cfg) -> str:
    """One short phrase for a rule's current setting."""
    cfg = cfg if isinstance(cfg, dict) else {}
    if not rule_on(rule, cfg):
        return "OFF (PriceLabs' market-driven default runs these dates)"
    if rule == "day_of_week_adjustment":
        days = [f"{DAY_NAMES[i]} {_pct(to_setting(cfg.get(k)) or 0)}" for i, k in enumerate(DOW_KEYS)
                if (to_setting(cfg.get(k)) or 0) != 0]
        return "on, " + (", ".join(days) if days else "every day 0%")
    if rule == "last_minute_prices":
        kind = cfg.get("last_min_factor_type")
        if kind in MARKET_TYPES:
            return f"on, market-driven ({kind}): PriceLabs sets the number"
        if kind == "none":
            return "on, type none (suppressed)"
        return (f"on, {kind} {_pct(to_setting(cfg.get('last_min_factor_value')) or 0)} over 0-"
                f"{int(to_setting(cfg.get('last_min_factor_dfd')) or 0)} days out")
    if rule == "far_out_premium":
        kind = cfg.get("far_out_premium_type")
        if kind in MARKET_TYPES:
            return f"on, market-driven ({kind}): PriceLabs sets the number"
        if kind == "none":
            return "on, type none (suppressed)"
        return (f"on, {kind} {_pct(to_setting(cfg.get('far_out_premium_value')) or 0)} from "
                f"{int(to_setting(cfg.get('far_out_premium_start')) or 0)} days out")
    if rule == "seasonality":
        return f"on, {cfg.get('seasonality_type')} (market-driven curve)"
    if rule == "demand_factor":
        return f"on, {cfg.get('tone_demand_factor')} (market-driven)"
    if rule == "custom_seasonal_profile":
        seasons = ((cfg.get("custom_seasonal_profile") or {}).get("seasons") or [])
        return f"on, {len(seasons)} season(s)"
    return "on"


def stack_rows(effective: dict, levels: dict) -> list:
    return [{"rule": r, "level": levels.get(r, "listing"), "setting": describe_rule(r, effective.get(r))}
            for r in ALL_RULES if r in effective or r in levels]


# ------------------------------------------------------------------------------ layers

def _in_season(season: dict, when: Date) -> bool:
    try:
        sm, sd = int(season["start_month"]), int(season["start_day"])
        em, ed = int(season["end_month"]), int(season["end_day"])
    except (KeyError, TypeError, ValueError):
        return False
    here, start, end = (when.month, when.day), (sm, sd), (em, ed)
    return start <= here <= end if start <= end else (here >= start or here <= end)


def night_layers(row: dict, rules: dict, levels: dict) -> list:
    """The layers active on one night: every rule that is ON and reaches it, and its DSO."""
    out = []
    when = Date.fromisoformat(row["date"])
    for rule in ALL_RULES:
        cfg = rules.get(rule)
        if not rule_on(rule, cfg):
            continue
        tag = "" if levels.get(rule, "listing") == "listing" else f" [{levels[rule]}]"
        if rule == "day_of_week_adjustment":
            v = to_setting(cfg.get(DOW_KEYS[when.weekday()])) or 0
            if v:
                out.append(f"day-of-week {DAY_NAMES[when.weekday()]} {_pct(v)}{tag}")
        elif rule in ("last_minute_prices", "far_out_premium"):
            kind = cfg.get("last_min_factor_type" if rule == "last_minute_prices" else "far_out_premium_type")
            if kind == "none" or not rule_covers(rule, cfg, row):
                continue
            if kind in MARKET_TYPES:
                out.append(f"{LABEL[rule]} (market-driven {kind}){tag}")
            elif rule == "last_minute_prices":
                out.append(f"last-minute {_pct(to_setting(cfg.get('last_min_factor_value')) or 0)} "
                           f"(0-{int(to_setting(cfg.get('last_min_factor_dfd')) or 0)}d){tag}")
            else:
                out.append(f"far-out {_pct(to_setting(cfg.get('far_out_premium_value')) or 0)} "
                           f"(from {int(to_setting(cfg.get('far_out_premium_start')) or 0)}d){tag}")
        elif rule == "custom_seasonal_profile":
            for s in ((cfg.get("custom_seasonal_profile") or {}).get("seasons") or []):
                if isinstance(s, dict) and _in_season(s, when):
                    out.append(f"custom season base {s.get('base_price')}{tag}")
        else:
            out.append(f"{LABEL[rule]} ({cfg.get('seasonality_type') or cfg.get('tone_demand_factor')}, "
                       f"market-driven){tag}")
    over = row.get("override") or {}
    if over:
        if over.get("price") is None:
            out.append("DSO (no price, min-stay/limits only)")
        else:
            out.append(f"DSO {over.get('price')}{'%' if over.get('price_type') != 'fixed' else ' fixed'}")
    return out


# ------------------------------------------------------------------------------ proposals

def _sides(rule: str, cfg: dict):
    """(label, days, predicate) for each part of the rule that can be resized on its own."""
    if rule == "day_of_week_adjustment":
        values = [to_setting(cfg.get(k)) or 0.0 for k in DOW_KEYS]
        for side, pick in (("discount days", lambda v: v < 0), ("premium days", lambda v: v > 0)):
            days = [i for i, v in enumerate(values) if pick(v)]
            if days:
                label = f"{side} ({', '.join(DAY_NAMES[i] for i in days)})"
                yield label, days, (lambda r, d=frozenset(days): Date.fromisoformat(r["date"]).weekday() in d)
    elif rule == "last_minute_prices":
        dfd = int(to_setting(cfg.get("last_min_factor_dfd")))
        yield f"0-{dfd} days out", None, (lambda r, c=cfg: rule_covers(rule, c, r))
    elif rule == "far_out_premium":
        start = int(to_setting(cfg.get("far_out_premium_start")))
        yield f"{start}+ days out", None, (lambda r, c=cfg: rule_covers(rule, c, r))


def _round_pct(value: float) -> int:
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


def _resize(before: float, move: float) -> float:
    """The new signed percentage that moves the price by `move` (a fraction):
    price * (1 + v/100) -> price * (1 + v'/100), so v' = ((1 + v/100)(1 + move) - 1) * 100.

    Deliberately the flat formula for every type. On a gradual ramp (last-minute
    linear_gradual) the full value lands only on check-in day and mid-window nights move less,
    so this under-corrects them rather than amplifying the change by the ramp: the smallest
    change the evidence supports, measured on the next run, not the biggest one it could
    justify."""
    return ((1 + before / 100) * (1 + move) - 1) * 100


def _clamp_sign(before: float, after: float) -> tuple[float, bool]:
    """A discount is never resized into a premium (or back): that is a different rule."""
    if before < 0 < after or after < 0 < before:
        return 0.0, True
    return after, False


def _gap_moves(nights, ref_key):
    moves = []
    for r in nights:
        ref, ask = r.get(ref_key), r.get("airbnb")
        if ref and ask:
            moves.append(ref / ask - 1)
    return moves


def recommend(rows, candidates, rules, levels, effect, bounds, max_delta, overrides=None,
              today=None) -> dict:
    """Rules first, then DSOs. See the module docstring for the thresholds.

    rows        the runner's daily rows (date, days_out, status, airbnb, p25/p50/p75, layer,
                override, action, at_floor)
    candidates  the review nights (direction cut or raise)
    rules       effective rule config per rule (resolve_stack)
    levels      where each rule lives: listing / group / account / default
    effect      attribution.rule_effectiveness output
    """
    import customization_write as cw  # the write-side guards; imported here to keep this module light

    rules = rules if isinstance(rules, dict) else {}
    levels = levels if isinstance(levels, dict) else {}
    wants = {c["date"]: c.get("direction", "cut") for c in candidates}
    cand_by_date = {c["date"]: c for c in candidates}
    movable = [r for r in rows if r.get("status") == "open"
               and r.get("action") != "pricing_opinion_withheld"
               and r.get("layer") != "fixed_override"]
    graded = {e.get("rule"): e for e in (effect or []) if isinstance(e, dict)}
    evaluations, proposals, notes = [], [], []

    def in_scope(direction):
        return (lambda r: r["days_out"] < CUT_SCOPE_DAYS) if direction == "cut" else (lambda r: True)

    for rule in WRITABLE_RULES:
        cfg = rules.get(rule)
        if not rule_on(rule, cfg):
            continue
        if not adjustable(rule, cfg):
            by_date = {r["date"]: r for r in movable}
            covered = [d for d in wants if d in by_date and rule_covers(rule, cfg, by_date[d])]
            if covered and rule != "day_of_week_adjustment":
                evaluations.append({"rule": rule, "side": None, "qualifies": False, "dates": covered,
                                    "why": f"{LABEL[rule]} is {describe_rule(rule, cfg)}; there is no "
                                           "number to resize, so it is not proposed"})
            continue
        for side, days, covers in _sides(rule, cfg):
            inside_all = [r for r in movable if covers(r)]
            found, wanted = {}, {}
            for direction in ("cut", "raise"):
                scope = in_scope(direction)
                in_s = [r for r in inside_all if scope(r)]
                want_in = [r for r in in_s if wants.get(r["date"]) == direction]
                wanted[direction] = want_in
                out_s = [r for r in movable if not covers(r) and scope(r)]
                want_out = [r for r in out_s if wants.get(r["date"]) == direction]
                share_in = len(want_in) / len(in_s) if in_s else 0.0
                share_out = len(want_out) / len(out_s) if out_s else None
                contrast = None if share_out is None else 100 * (share_in - share_out)
                ok = (len(want_in) >= RULE_MIN_NIGHTS and share_in > RULE_PATTERN_SHARE
                      and (contrast is None or contrast >= RULE_CONTRAST_PP))
                word = "above comp p75" if direction == "cut" else "under comp p25"
                scope_txt = f" inside {CUT_SCOPE_DAYS} days" if direction == "cut" else ""
                why = (f"{len(want_in)} of {len(in_s)} open nights{scope_txt} in the window are priced "
                       f"{word}" + (f" vs {len(want_out)} of {len(out_s)} outside it"
                                    if share_out is not None else
                                    "; no comparable open night outside the window, so the "
                                    "co-incidence test could not run"))
                if want_in and not ok:
                    miss = (f"needs at least {RULE_MIN_NIGHTS} nights" if len(want_in) < RULE_MIN_NIGHTS
                            else f"needs more than {RULE_PATTERN_SHARE:.0%} of the window"
                            if share_in <= RULE_PATTERN_SHARE
                            else f"needs {RULE_CONTRAST_PP:g} points more inside than outside")
                    evaluations.append({"rule": rule, "side": side, "direction": direction,
                                        "qualifies": False, "dates": [r["date"] for r in want_in],
                                        "why": f"{why} ({miss})"})
                if ok:
                    found[direction] = {"want": want_in, "why": why, "contrast": contrast,
                                        "in_scope": len(in_s)}
            # the grading trigger: occupancy inside the window trails the market
            e = graded.get(rule) or {}
            side_key = None
            if rule == "day_of_week_adjustment" and days is not None:
                side_key = "discount_days" if side.startswith("discount") else "premium_days"
                grade = e.get(side_key) or {}
            else:
                grade = e
            graded_bad = grade.get("verdict") == "underperforming"
            grade_why = grade.get("why")
            gap_in = grade.get("gap_to_market_inside")
            if graded_bad and gap_in is not None and gap_in >= 0 and "raise" not in found:
                notes.append(f"{LABEL[rule]} {side}: graded underperforming only against the rest "
                             f"of the calendar; inside it the listing still books {gap_in:+.1f} pts vs "
                             "the market, so it is not cut")
                graded_bad = False
            if graded_bad:
                above = [r for r in inside_all if r.get("p50") and r["airbnb"] > r["p50"]]
                share = len(above) / len(inside_all) if inside_all else 0.0
                if "raise" in found:
                    why_c = (f"{LABEL[rule]} {side}: graded underperforming (a cut), but its open "
                             f"nights sit under the comp p25 (a raise); the evidence disagrees, so "
                             "no rule change is proposed")
                    notes.append(why_c)
                    evaluations.append({"rule": rule, "side": side, "direction": "raise",
                                        "qualifies": False,
                                        "dates": [r["date"] for r in found.pop("raise")["want"]],
                                        "why": why_c})
                elif len(inside_all) >= RULE_MIN_NIGHTS and share >= GRADED_PRICE_SHARE:
                    # graded-only: sized from the comp median, and it folds whatever cut
                    # candidates sit in the window (too few for a pattern of their own)
                    base = found.get("cut") or {"want": [], "fold": wanted.get("cut", []), "why": "",
                                                "contrast": None, "in_scope": len(inside_all)}
                    base["graded"] = {"why": grade_why, "above": above, "share": share}
                    found["cut"] = base
                elif inside_all:
                    notes.append(f"{LABEL[rule]} {side}: graded underperforming, but only {len(above)} "
                                 f"of {len(inside_all)} open nights sit above the comp median; price is "
                                 "not the lever there (check visibility and the listing)")
            # the booking guard: the pattern must not contradict how the window books
            for direction in list(found):
                if found[direction].get("graded") and not found[direction]["want"]:
                    continue  # graded-only already requires booking under the market
                if gap_in is None:
                    found[direction]["why"] += ("; booking guard not run (no market occupancy for "
                                                "this window)")
                    continue
                against = ((direction == "raise" and gap_in < -BOOKING_GUARD_PP)
                           or (direction == "cut" and gap_in > BOOKING_GUARD_PP))
                if not against:
                    found[direction]["why"] += f"; the window books {gap_in:+.1f} pts vs the market"
                    continue
                why_g = (f"{LABEL[rule]} {side}: {found[direction]['why']}, "
                         + (f"yet these nights book {gap_in:+.1f} pts vs the market: cheap and still "
                            "not booking reads as visibility or the listing, not the rule; no raise "
                            "proposed" if direction == "raise" else
                            f"yet these nights book {gap_in:+.1f} pts vs the market: they are selling, "
                            "so no cut is proposed"))
                notes.append(why_g)
                evaluations.append({"rule": rule, "side": side, "direction": direction,
                                    "qualifies": False, "caution": True,
                                    "dates": [r["date"] for r in found.pop(direction)["want"]],
                                    "why": why_g})
            if len(found) > 1:  # cannot happen at a majority each; refuse to guess if it does
                notes.append(f"{LABEL[rule]} {side}: evidence points both ways; no change proposed")
                continue
            for direction, ev in found.items():
                proposals.append(_size(rule, cfg, side, days, direction, ev, inside_all, rows, covers,
                                       levels.get(rule, "listing"), max_delta, cw))

    # one lever per diagnosis: strongest evidence first, each night folds into one rule only
    proposals = [p for p in proposals if p]
    proposals.sort(key=lambda p: (0 if p["trigger"].startswith("pattern") else 1,
                                  -(p["contrast"] if p["contrast"] is not None else 0),
                                  -len(p["folded"]), p["touches_open"]))
    taken, chosen, used_rules = {}, [], set()
    for p in proposals:
        if p["rule"] in used_rules:
            notes.append(f"{LABEL[p['rule']]} {p['side']} also qualifies; one change per rule per run, "
                         "so it waits for the next run")
            continue
        clash = [d for d in p["folded"] if d in taken]
        if clash:
            p["folded"] = [d for d in p["folded"] if d not in taken]
            if p["trigger"].startswith("pattern") and len(p["folded"]) < RULE_MIN_NIGHTS:
                notes.append(f"{LABEL[p['rule']]} {p['side']}: its nights are already explained by "
                             f"{LABEL[taken[clash[0]]]}; one lever per diagnosis")
                continue
        if p.get("dropped"):
            notes.append(p["dropped"])
            continue
        used_rules.add(p["rule"])
        for d in p["folded"]:
            taken[d] = p["rule"]
        chosen.append(p)

    residual, why_dso, layer_map, guard_withheld = [], {}, {}, {}
    row_by_date = {r["date"]: r for r in rows}
    for c in candidates:
        layer_map[c["date"]] = night_layers(row_by_date.get(c["date"], c), rules, levels)
        if c["date"] in taken:
            continue
        hits = [ev for ev in evaluations if c["date"] in ev.get("dates", [])]
        # The booking guard holds one layer down too: if it refused a rule move in THIS night's
        # direction (cheap and still not booking -> no raise; selling -> no cut), a DSO making the
        # same move contradicts it. Live 2026-09-25 (The Urban Nest, Mon-Wed): the rule layer said
        # "no raise" while the DSO layer suggested raises on the same nights. Withheld, reason kept.
        guard = next((ev for ev in hits if ev.get("caution")
                      and ev.get("direction") == (c.get("direction") or "cut")), None)
        if guard and c.get("layer") != "fixed_override":
            guard_withheld[c["date"]] = guard["why"]
            continue
        residual.append(c["date"])
        if c.get("layer") == "fixed_override":
            why_dso[c["date"]] = "a fixed DSO sets this night, so no rule reaches it: the DSO is the lever"
        elif hits and hits[0].get("caution"):
            why_dso[c["date"]] = (f"no rule change: {hits[0]['why']}. The same caution applies to this "
                                  "date's scenario")
        elif hits:
            why_dso[c["date"]] = "no rule qualifies: " + hits[0]["why"]
        else:
            why_dso[c["date"]] = "no resizable rule covers this night"
    return {
        "rule_changes": chosen,
        "dso_dates": residual,
        "folded": taken,
        "layers": layer_map,
        "why_dso": why_dso,
        "guard_withheld": guard_withheld,
        "existing_dsos": existing_dsos(overrides or [], rows, rules, levels, bounds, today, chosen),
        "notes": notes,
        "thresholds": dict(THRESHOLDS),
    }


def _size(rule, cfg, side, days, direction, ev, inside_all, rows, covers, level, max_delta, cw):
    """Turn the evidence into one proposed rule change, or None / a dropped note."""
    want = ev["want"]
    graded = ev.get("graded")
    if want:
        moves = _gap_moves(want, "p75" if direction == "cut" else "p25")
        trigger = "pattern" + (" + graded underperforming" if graded else "")
    else:
        moves = _gap_moves(graded["above"], "p50")
        trigger = "graded underperforming"
    if not moves:
        return None
    needed = median(moves)
    cap = max_delta
    move = max(-cap, min(cap, needed))
    capped = abs(needed) > cap + 1e-9
    changes, before_after = {}, []
    crossed = False
    if rule == "day_of_week_adjustment":
        for i in days:
            key = DOW_KEYS[i]
            v = to_setting(cfg.get(key)) or 0.0
            nv, hit = _clamp_sign(v, _resize(v, move))
            crossed |= hit
            nv = _round_pct(nv)
            if nv != v:
                changes[key] = nv
                before_after.append(f"{DAY_NAMES[i]} {_pct(v)} -> {_pct(nv)}")
    else:
        vkey = "last_min_factor_value" if rule == "last_minute_prices" else "far_out_premium_value"
        v = to_setting(cfg.get(vkey))
        nv = _resize(v, move)
        nv, crossed = _clamp_sign(v, nv)
        nv = _round_pct(nv)
        if nv != v:
            changes[vkey] = nv
            before_after.append(f"{_pct(v)} -> {_pct(nv)}")
    label = LABEL[rule]
    if not changes:
        return {"rule": rule, "side": side, "trigger": trigger, "contrast": ev.get("contrast"),
                "folded": [], "touches_open": 0,
                "dropped": f"{label} {side}: the evidence asks for less than a 1-point change; nothing proposed"}
    # the full rule object, merged and checked exactly as the writer will
    if rule == "day_of_week_adjustment":
        full = cw.merge_dow(cfg, changes)
    else:
        full = {k: v for k, v in cfg.items() if v is not None}
        full.update(changes)
    errors = cw.validate({rule: full})
    warnings = cw.destructive_warnings({rule: full})
    if errors:
        return {"rule": rule, "side": side, "trigger": trigger, "contrast": ev.get("contrast"),
                "folded": [], "touches_open": 0,
                "dropped": f"{label} {side}: the sized change fails PriceLabs' ranges ({'; '.join(errors)}); "
                           "nothing proposed"}
    window_all = [r for r in rows if covers(r)]
    open_in = [r for r in window_all if r.get("status") == "open"]
    fixed_in = [r["date"] for r in open_in if r.get("layer") == "fixed_override"]
    folded = sorted(r["date"] for r in (want or ev.get("fold") or []))
    at_floor = sum(1 for r in (want or ev.get("fold") or []) if r.get("at_floor"))
    kind = cfg.get("last_min_factor_type") or cfg.get("far_out_premium_type")
    summary = (f"{label} {side}: " + ", ".join(before_after)
               + (f" ({kind})" if kind and rule != "day_of_week_adjustment" else ""))
    ref = "p75" if direction == "cut" else "p25"
    evidence = ev["why"] if want else f"graded underperforming: {graded['why']}"
    if want and graded:
        evidence += f"; also graded underperforming ({graded['why']})"
    sized = (f"sized from the median gap to comp {ref if want else 'p50'} ({needed * 100:+.1f}%)"
             + (f", capped at {cap * 100:g}% (LARGE MOVE, confirm)" if capped else "")
             + ("; stops at 0%: a discount never flips into a premium on a resize" if crossed else ""))
    if rule == "last_minute_prices" and cfg.get("last_min_factor_type") == "linear_gradual":
        sized += ("; linear_gradual applies the full value on check-in day and tapers to 0 at the "
                  "window's edge, so mid-window nights move less than this: rerun after PriceLabs "
                  "recalculates and resize again if they are still out of band")
    floor_note = (f"; {at_floor} of these nights sit at your min, so the min can still hold them "
                  "(see the min price line)" if at_floor else "")
    out = {
        "rule": rule, "level": level, "side": side, "direction": direction, "trigger": trigger,
        "writable": level == "listing",
        "refusal": LEVEL_REFUSAL.get(level),
        "summary": summary,
        "change": {rule: changes},
        "full_after": full,
        "evidence": evidence,
        "sizing": sized,
        "needed_pct": round(needed * 100, 1),
        "applied_pct": round(move * 100, 1),
        "capped": capped,
        "contrast": ev.get("contrast"),
        "folded": folded,
        "touches_open": len(open_in) - len(fixed_in),
        "touches_nights": len(window_all),
        "blocked_by_fixed_dso": fixed_in,
        "at_floor": at_floor,
        "warnings": warnings,
        "why": f"{evidence}; {sized}{floor_note}",
    }
    return out


# ------------------------------------------------------------------------------ existing DSOs

def existing_dsos(overrides, rows, rules, levels, bounds, today, changes=()) -> list:
    """Every DSO in the window, flagged when it fights the rule stack, sits below the min, or is
    past or stale. A DSO is listing-level here; group and account DSOs are not read."""
    today = today if isinstance(today, Date) else (Date.fromisoformat(today) if today else None)
    lo = (bounds or {}).get("min")
    by_date = {r["date"]: r for r in rows}
    reach = {}
    for ch in changes or ():
        for d in ch.get("blocked_by_fixed_dso") or []:
            reach.setdefault(d, []).append(LABEL[ch["rule"]])
    out = []
    for o in overrides or []:
        d = o.get("date")
        if not d:
            continue
        flags = []
        try:
            when = Date.fromisoformat(d)
        except ValueError:
            flags.append("unreadable date")
            when = None
        if when and today and when < today:
            flags.append("past: the date is gone, PriceLabs should drop it")
        row = by_date.get(d)
        sets_price = o.get("price") not in (None, "")
        live_night = row is not None and row.get("status") == "open"
        stamp = o.get("updated_at") or o.get("created_at")
        # stale matters where the DSO sets a price on a night that can still sell
        if stamp and today and sets_price and live_night:
            try:
                age = (today - Date.fromisoformat(str(stamp)[:10])).days
                if age > DSO_STALE_DAYS:
                    flags.append(f"stale: last set {age} days ago")
            except ValueError:
                pass
        kind, price = o.get("price_type"), to_setting(o.get("price"))
        if lo is not None and kind == "fixed" and price is not None and price < lo - 0.005:
            flags.append(f"below the min: fixed {price:g} under min {lo:g}")
        mn = to_setting(o.get("min_price"))
        if lo is not None and mn is not None and mn < lo - 0.005 and str(o.get("min_price_type") or "fixed") == "fixed":
            flags.append(f"below the min: its own min {mn:g} under the listing min {lo:g}")
        if row and kind in ("percent", "percent_stacked") and price:
            fights = []
            for rule in ("day_of_week_adjustment", "last_minute_prices", "far_out_premium"):
                cfg = rules.get(rule)
                if not adjustable(rule, cfg) or not rule_covers(rule, cfg, row):
                    continue
                if rule == "day_of_week_adjustment":
                    rv = (to_setting(cfg.get(DOW_KEYS[when.weekday()])) or 0) if when else 0
                else:
                    rv = to_setting(cfg.get("last_min_factor_value" if rule == "last_minute_prices"
                                            else "far_out_premium_value")) or 0
                if rv and (rv > 0) != (price > 0):
                    fights.append(f"{LABEL[rule]} {_pct(rv)}")
            if fights:
                flags.append(f"fights the rule stack: {_pct(price)} against " + ", ".join(fights))
        if d in reach:
            flags.append("a fixed price here blocks the proposed " + ", ".join(reach[d]) + " change")
        shown = ("no price (limits/min-stay only)" if o.get("price") in (None, "") else
                 f"{o.get('price')} {'fixed' if kind == 'fixed' else '%'}")
        extras = [f"{k.replace('_', ' ')} {o[k]}" for k in ("min_price", "max_price", "min_stay") if o.get(k) not in (None, "")]
        out.append({"date": d, "shown": shown + (f" ({', '.join(extras)})" if extras else ""), "flags": flags})
    return out
