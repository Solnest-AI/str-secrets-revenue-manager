#!/usr/bin/env python3
"""Build and check a PriceLabs customization write. Never send one.

Every function here is a guard against a measured failure that returns HTTP 200:

  merge_dow           days omitted from a write default to 0, they do NOT keep their
                      previous value, so a Fri/Sat write wipes Mon-Thu. Also refuses to
                      guess: an absent toggle or an unrecognized `changes` key raises
                      rather than silently assuming or dropping it (fix round 1, D1/D3)
  validate            one invalid value rejects the whole request, so ranges are checked
                      before the payload is built, not after a partial mental model of it.
                      Range checks run on whatever fields are PRESENT; the on/off toggle
                      no longer gates whether a check runs at all (fix round 1, A1-A4).
                      Presence is the wrong gate for a COMPLETENESS check, though --
                      "only check what's given" and "flag what's missing" are different
                      questions, and conflating them in round 1 silently turned six
                      required-field omissions into a pass (fix round 2). Every rule's
                      own toggle is itself a required field, checked independent of
                      what else the payload states; last_minute_prices/far_out_premium
                      count re-enabling with the toggle alone as incomplete too, the
                      same shape as day-of-week; seasonality/demand_factor now get real
                      validation instead of none; and all four type enums are checked
                      against their OWN authoritative set, not a shared superset that
                      happened to accept values three of the four fields reject
                      (fix round 3, F1/F3/F4/F5)
  destructive_warnings separate state-replacement risks from payload validation.
                      Toggling last_minute_prices or far_out_premium off resets their
                      stored config rather than preserving it, so the operator loses
                      the prior setting with an HTTP 200 and no error to catch it
                      (fix round 3, F2)
  snapshot_payload    the rollback. The exact object needed to re-POST the prior state.
                      Deep-copies `current` so a later mutation of the caller's dict can
                      never reach back into an already-taken snapshot (fix round 1, C1)
  write_snapshot      persists the snapshot atomically and never overwrites one, even
                      under a same-instant collision (fix round 1, C1/C2)
  echo_diff           the sign is accepted either way, because a premium is a legitimate
                      setting. Only the effective block proves which way it went. A
                      day-of-week block mixes signs across seven days in one string, so an
                      unscoped substring test is blind to a single day inverting -- pass
                      `intent["day"]` to scope the comparison. Without a day, a block that
                      mixes discount and premium is refused rather than guessed at
                      (fix round 1, B1/B2)
  signed_from_action  get_actions uses two conventions in one object: current.discount_pct
                      is the stored SIGNED value, recommended.discount_pct is a positive
                      MAGNITUDE. Copying the recommendation writes a premium where a
                      discount was meant

The POST itself is the operator's approved action in Step 8. This module has no network code.

A note on the parser used throughout: attribution.py exposes two of them. to_number() is the
PRICE-field parser -- it filters PriceLabs' -1/-2 "no value" sentinels and must never see a
customization setting. to_setting() is the CONFIG-field parser -- no sentinel filtering,
because a day-of-week/last-minute/far-out percentage is documented -75..1000, so -1% and -2%
are ordinary, real values there, not "no data". Every value this module touches is a
customization config value, so everything here goes through to_setting(). Using to_number()
anywhere in this file -- merge_dow in particular -- would silently zero a live -1% or -2%
setting on the next read-modify-write. (An earlier draft of this module's interface named
attribution._num, which has never existed in shipped code; the two real parsers are
to_number() and to_setting(), and this module uses to_setting() exclusively. See
references/pricelabs-gotchas.md and attribution.py's own module docstring.)
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from attribution import DOW_KEYS, to_setting  # noqa: E402
from _cache import cache_dir  # noqa: E402

VALID_RULES = {"seasonality", "last_minute_prices", "far_out_premium",
               "day_of_week_adjustment", "demand_factor", "custom_seasonal_profile"}

RANGES = {
    "dow": (-75.0, 1000.0),
    "last_min_discount": (0.0, 75.0),      # magnitude when the value is negative
    "last_min_premium": (0.0, 500.0),      # magnitude when the value is positive
    "last_min_dfd": (1.0, 90.0),
    # `fixed` is an ABSOLUTE nightly price, not a percentage, so the two magnitude
    # bounds above do not apply to it. This bound is deliberately wide: it exists to
    # reject nonsense (a negative price, a 999999 typo), not to second-guess a real
    # nightly rate. NOT taken from the vendor spec -- the spec states no range for this
    # field -- so widen it if a legitimate write is ever refused.
    "last_min_fixed": (0.0, 100000.0),
    "far_out_value": (-30.0, 500.0),
    "far_out_start": (1.0, 999.0),
    "far_out_step": (1.0, 999.0),
}

# Fields the account may not be entitled to. Including one rejects the WHOLE request
# with ERR-FEATURE-NOT-ENABLED, so they are refused locally rather than at the API.
# Every field the vendor spec marks feature-gated. Read from
# docs/pricelabs/customer-api.json: each `inherit_*` description says
# "(feature-gated; listing/group level only)", non_repeating_seasons says
# "(feature-gated)", price_type_non_repeating is required alongside it. They are NOT all
# top-level -- the six season-related ones sit inside custom_seasonal_profile's nested
# object -- which is why gated_fields() walks the payload instead of scanning one level.
FEATURE_GATED = {"hotel_compset_type", "hotel_wt", "non_repeating_seasons",
                 "price_type_non_repeating", "inherit_baseprice", "inherit_cico",
                 "inherit_minstay", "inherit_priceprofile"}

# Fix round 3 (F5): the authoritative per-field enums, read directly from the vendor's
# OpenAPI spec at docs/pricelabs/customer-api.json (resolving
# CapiLastMinutePricesLastMinFactorType, CapiFarOutPremiumFarOutPremiumType,
# CapiSeasonalitySeasonalityType, CapiDemandFactorToneDemandFactor). NOT
# attribution.MARKET_DRIVEN: that set is a superset across all four fields, and
# moderately_conservative/moderately_aggressive exist ONLY on seasonality and demand
# factor -- validate() briefly accepted them on last_min_factor_type and
# far_out_premium_type too (round 2 tests asserted it as fact; it was not), which
# PriceLabs would have rejected outright, killing the whole all-or-nothing write.
LAST_MIN_FACTOR_TYPES = {"none", "recommended", "conservative", "aggressive",
                         "linear", "linear_gradual", "fixed"}
FAR_OUT_PREMIUM_TYPES = {"none", "recommended", "conservative", "aggressive",
                         "linear", "fix"}
SEASONALITY_TYPES = {"no_seasonality", "conservative", "moderately_conservative",
                     "recommended", "moderately_aggressive", "aggressive"}
TONE_DEMAND_FACTOR_TYPES = {"conservative", "moderately_conservative", "recommended",
                            "moderately_aggressive", "aggressive", "no demand factor"}

# Fix round 3 (F4): every one of these toggles is marked `req = Y` in the POST body
# tables of references/pricelabs-api/customizations.md -- a rule object sent without
# its own toggle is a malformed request, not merely an incomplete one. Same map as
# reduce_customizations.TOGGLE_KEY; kept as its own copy rather than imported, so this
# write-side safety module has no dependency on a read-side reporting module -- matches
# how RANGES/VALID_RULES/FEATURE_GATED are already self-contained constants here.
TOGGLE_KEY = {
    "seasonality": "seasonality_customization_on",
    "last_minute_prices": "last_min_factor_on",
    "far_out_premium": "far_out_premium_on",
    "day_of_week_adjustment": "dow_factor_on",
    "demand_factor": "tone_demand_factor_on",
    "custom_seasonal_profile": "custom_seasonal_profile_on",
}

# Runtime cache, outside the plugin tree (see _cache.py) so `claude plugin update` never
# duplicates it. Kept in its OWN subfolder rather than reduce_customizations.CACHE_DIR:
# that directory is a TTL'd, disposable fetch cache that gets overwritten on every re-pull,
# while a snapshot is the rollback kill switch and write_snapshot() below refuses to ever
# overwrite one. Mixing the two would let a future cache-clear silently take the rollback
# data with it. Callers may pass any out_dir to write_snapshot(); this is just the default.
SNAPSHOT_DIR = cache_dir("customization_snapshots")


def merge_dow(current: dict, changes: dict) -> dict:
    """Return a FULL seven-day object. Never send a partial day-of-week write.

    Raises ValueError rather than guessing in two places (fix round 1, D1/D3):
      - `current` has no `dow_factor_on`: defaulting it (either way) would silently
        enable or disable a guest-facing rule with no evidence. The caller must read the
        real toggle state from PriceLabs first.
      - `changes` has a key outside DOW_KEYS: a typo'd key (`_monday` vs `_mon`) used to
        vanish silently -- not applied, not flagged, and invisible to validate() too, so
        the operator got a "successful" write that changed nothing on the day they meant
        to change.
    """
    if "dow_factor_on" not in current:
        raise ValueError(
            "merge_dow: current has no dow_factor_on -- the toggle state must come from "
            "a real PriceLabs read, never assumed. Defaulting it either way would "
            "silently enable or disable a guest-facing rule with no evidence.")
    unknown = sorted(set(changes) - set(DOW_KEYS))
    if unknown:
        raise ValueError(
            f"merge_dow: changes has unrecognized key(s) {unknown}; expected only "
            f"{DOW_KEYS}. A typo'd key is silently dropped otherwise -- not applied, not "
            "flagged, and invisible to validate() too.")
    out = {"dow_factor_on": toggle_is_on(current["dow_factor_on"])}
    unreadable = sorted(k for k in DOW_KEYS
                        if k not in changes and k in current
                        and to_setting(current.get(k)) is None)
    if unreadable:
        raise ValueError(
            f"merge_dow: current has unreadable value(s) for {unreadable}. Carrying "
            "them forward as 0.0 would WIPE a live guest-facing setting on a day you "
            "did not intend to touch, and validate() would then accept the zero as a "
            "legitimate value. Re-read the rule rather than guessing.")
    for key in DOW_KEYS:
        if key in changes:
            out[key] = changes[key]
        else:
            # Explicit None-check, not `to_setting(...) or 0.0`: a legitimate 0, 0.0 or
            # False must never be silently replaced by the fallback just because it is
            # falsy. Matches reduce_customizations.clean_text's `is None or == ""` idiom.
            # A key ABSENT from current is a genuine 0 (PriceLabs omits unset days); a
            # key PRESENT but unparseable was refused above.
            value = to_setting(current.get(key))
            out[key] = 0.0 if value is None else value
    return out


def _in_range(value, bounds, label, errors):
    number = to_setting(value)
    if number is None:
        errors.append(f"{label}: {value!r} is not a number")
        return
    low, high = bounds
    if not (low <= number <= high):
        errors.append(f"{label}: {number:g} is outside {low:g}..{high:g}")


def validate(customizations: dict) -> list[str]:
    """Return every validation problem found within the supported checks.

    Fix round 1 (A1-A4): every check below now runs on whatever fields are PRESENT.
    The on/off toggle no longer gates whether a rule gets checked at all -- it only
    gates whether a value is REQUIRED. The original gating meant an operator who only
    restated the days/fields they were actually changing (the normal, minimal case) got
    zero validation, because the toggle key itself is exactly the field they had no
    reason to resend.

    Fix round 2: presence is the wrong gate for a COMPLETENESS check, though --
    "only check what's given" and "flag what's missing" are different questions.

    Fix round 3, the same family of bug in three more places:
      - F1: re-enabling last_minute_prices or far_out_premium with ONLY the toggle
        restated (no type) is the exact shape of day-of-week's zero-days hole, and just
        as dangerous -- references/pricelabs-gotchas.md: toggling either OFF resets
        their stored config to a zeroed state (last-minute to type linear/value 0,
        far-out to value 0/start 999), so re-enabling needs the full configuration
        again, not just the toggle. `touched` now includes the toggle for both.
      - F3/F5: seasonality and demand_factor previously had no rule-specific
        validation at all (only the FEATURE_GATED scan below), and last_minute_prices/
        far_out_premium validated their type against attribution.MARKET_DRIVEN, which
        is a SUPERSET across all four type fields --
        moderately_conservative/moderately_aggressive are real values on seasonality
        and demand_factor ONLY, not on last-minute or far-out (round 2's own tests
        asserted otherwise; they were wrong). All four now check their own
        authoritative enum: LAST_MIN_FACTOR_TYPES / FAR_OUT_PREMIUM_TYPES /
        SEASONALITY_TYPES / TONE_DEMAND_FACTOR_TYPES.
      - F4: every rule's own toggle is a required body field (TOGGLE_KEY, checked
        first, below), independent of anything else in the payload. Day-of-week's
        completeness check uses toggle TRUTHINESS (`cfg.get(...)`), not presence
        (`... in cfg`), on purpose: `{"dow_factor_on": False}` with zero days is a
        legitimate, minimal "just turn it off" write, because day-of-week keeps its
        stored per-day values on toggle-off (last-minute/far-out do NOT -- they reset
        destructively; see destructive_warnings()). Presence-based would wrongly flag
        that legitimate write as incomplete with a fully green suite -- this exact
        module has already shipped that class of bug twice. Do not "simplify" this to
        `in cfg` without re-reading this paragraph; the locking test is in Axis 1 of
        the fix-round-3 test block.

    custom_seasonal_profile checks the required nested object, at least one season
    when enabled, season-array shape, price-type enum, required season fields and
    declared scalar types. These checks follow CapiCustomSeasonalProfile/CapiCspSeason
    in the bundled API schema. They
    do not validate date validity/order/overlap, price ranges or
    relationships, profile ownership, or account-level restrictions. An empty error
    list is not a complete vendor validation of a seasonal profile.
    """
    errors: list[str] = []
    for rule, cfg in customizations.items():
        if rule not in VALID_RULES:
            errors.append(f"{rule}: not a customization name PriceLabs accepts")
            continue
        cfg = cfg or {}
        # validate()'s contract is "return every problem found". A cfg that is a string,
        # a list or an int used to raise AttributeError/TypeError out of the function
        # instead, which the caller has no reason to expect from a validator.
        if not isinstance(cfg, dict):
            errors.append(f"{rule}: config must be an object, got "
                          f"{type(cfg).__name__}")
            continue
        for field in gated_fields(cfg):
            errors.append(f"{rule}.{field}: feature-gated, rejects the whole request")

        # F4: every rule's toggle is `req = Y` on the wire. Checked once, here, for
        # every rule that appears in the payload at all -- independent of the
        # rule-specific branches below, which is why this can be a flat presence check
        # (unlike the day-of-week completeness check below, which needs truthiness).
        toggle_key = TOGGLE_KEY.get(rule)
        if toggle_key and toggle_key not in cfg:
            errors.append(f"{rule}: {toggle_key} is required on every write that "
                          "includes this rule")

        if rule == "day_of_week_adjustment":
            present = [k for k in DOW_KEYS if k in cfg]
            # `present` alone still catches 1-6 days regardless of the toggle
            # (unchanged since round 1). The `or cfg.get("dow_factor_on")` term is
            # what catches zero-days-plus-toggle-on specifically (round 2's CRITICAL
            # fix) -- see the docstring above for why this is truthiness, not presence.
            if (present or toggle_is_on(cfg.get("dow_factor_on"))) and len(present) != 7:
                errors.append("day_of_week_adjustment: all seven days must be sent; "
                              f"got {len(present)}. Omitted days reset to 0")
            for key in present:
                _in_range(cfg[key], RANGES["dow"], f"day_of_week_adjustment.{key}", errors)
            # A stray dow_factor_value_* key (a typo like `_monday` for `_mon`) is
            # invisible to `present` above -- it's simply not one of the seven canonical
            # names -- so it needs its own check. merge_dow (D3) refuses to build this
            # shape in the normal pipeline; this is the second layer, for anyone who
            # calls validate() directly on a hand-built or already-merged dict.
            stray = sorted(k for k in cfg
                           if k.startswith("dow_factor_value_") and k not in DOW_KEYS)
            if stray:
                errors.append(f"day_of_week_adjustment: unrecognized day key(s) {stray}; "
                              f"expected one of {DOW_KEYS}")

        elif rule == "last_minute_prices":
            kind = cfg.get("last_min_factor_type")
            # F1: the toggle counts as "touched" too now -- see the docstring above.
            touched = (kind is not None or "last_min_factor_value" in cfg
                      or "last_min_factor_dfd" in cfg
                      or toggle_is_on(cfg.get("last_min_factor_on")))
            if touched:
                if kind == "fix":
                    # the mirror of the far_out_premium enum trap below. Far-out uses
                    # `fix`, last-minute uses `fixed` -- not interchangeable, and
                    # PriceLabs rejects the whole request on the wrong one.
                    errors.append("last_minute_prices: the enum is `fixed`, not `fix` "
                                  "(far_out_premium uses `fix`; they differ)")
                elif kind is None:
                    errors.append("last_minute_prices: last_min_factor_type is "
                                  "required when the rule is touched (toggle on, or "
                                  "last_min_factor_value/_dfd present)")
                elif kind in ("linear", "linear_gradual", "fixed"):
                    # Both fields are "Required for linear/linear_gradual/fixed" per
                    # references/pricelabs-api/customizations.md, independent of the
                    # toggle.
                    if "last_min_factor_value" not in cfg:
                        errors.append("last_minute_prices: last_min_factor_value is "
                                      f"required for type {kind}")
                    else:
                        value = to_setting(cfg.get("last_min_factor_value"))
                        if value is None:
                            errors.append("last_minute_prices: last_min_factor_value is "
                                          f"not a number for type {kind}")
                        elif kind == "fixed":
                            # `fixed` is an absolute nightly price, not a percentage, so
                            # the percentage bounds below do not apply -- but "no bounds
                            # at all" let -10 and 999999 both validate clean. A negative
                            # absolute price is never a real setting.
                            if value < 0:
                                errors.append(
                                    "last_minute_prices.last_min_factor_value: "
                                    f"{value} is negative, and type `fixed` is an "
                                    "absolute nightly price, not a discount")
                            else:
                                _in_range(value, RANGES["last_min_fixed"],
                                          "last_minute_prices.last_min_factor_value",
                                          errors)
                        else:
                            bounds = (RANGES["last_min_discount"] if value < 0
                                     else RANGES["last_min_premium"])
                            _in_range(abs(value), bounds,
                                      "last_minute_prices.last_min_factor_value", errors)
                    if "last_min_factor_dfd" not in cfg:
                        errors.append("last_minute_prices: last_min_factor_dfd is "
                                      f"required for type {kind}")
                    else:
                        _in_range(cfg.get("last_min_factor_dfd"), RANGES["last_min_dfd"],
                                  "last_minute_prices.last_min_factor_dfd", errors)
                elif kind not in LAST_MIN_FACTOR_TYPES:
                    errors.append("last_minute_prices: unknown last_min_factor_type "
                                  f"{kind!r}")
                # else: kind is a valid non-concrete type (none/recommended/
                # conservative/aggressive) -- no numeric companion fields to check.

        elif rule == "far_out_premium":
            kind = cfg.get("far_out_premium_type")
            # F1: the toggle counts as "touched" too now -- see the docstring above.
            touched = (kind is not None or "far_out_premium_value" in cfg
                      or "far_out_premium_start" in cfg or "far_out_premium_step" in cfg
                      or toggle_is_on(cfg.get("far_out_premium_on")))
            if touched:
                if kind == "fixed":
                    errors.append("far_out_premium: the enum is `fix`, not `fixed` "
                                  "(last_minute_prices uses `fixed`; they differ)")
                elif kind is None:
                    errors.append("far_out_premium: far_out_premium_type is required "
                                  "when the rule is touched (toggle on, or "
                                  "_value/_start/_step present)")
                elif kind in ("linear", "fix"):
                    # value and start are "Required for linear/fix"; step is
                    # "Required for linear; ignored for fix" per
                    # references/pricelabs-api/customizations.md.
                    if "far_out_premium_value" not in cfg:
                        errors.append("far_out_premium: far_out_premium_value is "
                                      f"required for type {kind}")
                    else:
                        _in_range(cfg.get("far_out_premium_value"), RANGES["far_out_value"],
                                  "far_out_premium.far_out_premium_value", errors)
                    if "far_out_premium_start" not in cfg:
                        errors.append("far_out_premium: far_out_premium_start is "
                                      f"required for type {kind}")
                    else:
                        _in_range(cfg.get("far_out_premium_start"), RANGES["far_out_start"],
                                  "far_out_premium.far_out_premium_start", errors)
                    if kind == "linear":
                        if "far_out_premium_step" not in cfg:
                            errors.append("far_out_premium: far_out_premium_step is "
                                          "required for type linear")
                        else:
                            _in_range(cfg.get("far_out_premium_step"), RANGES["far_out_step"],
                                      "far_out_premium.far_out_premium_step", errors)
                elif kind not in FAR_OUT_PREMIUM_TYPES:
                    errors.append("far_out_premium: unknown far_out_premium_type "
                                  f"{kind!r}")
                # else: kind is a valid non-concrete type (none/recommended/
                # conservative/aggressive) -- no numeric companion fields to check.

        elif rule == "seasonality":
            # F3: previously only the FEATURE_GATED scan above ran on this rule -- no
            # enum check, no required-type check, despite the spec marking
            # seasonality_type "Required when the toggle is on". A bogus type
            # validated clean and would have killed the whole request at the API.
            kind = cfg.get("seasonality_type")
            touched = kind is not None or toggle_is_on(cfg.get("seasonality_customization_on"))
            if touched:
                if kind is None:
                    errors.append("seasonality: seasonality_type is required when "
                                  "the rule is touched (toggle on, or the type given)")
                elif kind not in SEASONALITY_TYPES:
                    errors.append(f"seasonality: unknown seasonality_type {kind!r}")

        elif rule == "demand_factor":
            # F3, same shape as seasonality. hotel_compset_type/hotel_wt are
            # feature-gated fields with their own enums (already covered by the
            # FEATURE_GATED scan above when the account lacks the feature); their
            # positive-case validation is out of scope for this round.
            kind = cfg.get("tone_demand_factor")
            touched = kind is not None or toggle_is_on(cfg.get("tone_demand_factor_on"))
            if touched:
                if kind is None:
                    errors.append("demand_factor: tone_demand_factor is required "
                                  "when the rule is touched (toggle on, or the type "
                                  "given)")
                elif kind not in TONE_DEMAND_FACTOR_TYPES:
                    errors.append(f"demand_factor: unknown tone_demand_factor {kind!r}")

        elif rule == "custom_seasonal_profile":
            key = "custom_seasonal_profile"
            if key not in cfg:
                if toggle_is_on(cfg.get("custom_seasonal_profile_on")):
                    errors.append(f"{rule}: {key} object is required when the toggle is on")
                continue
            profile = cfg[key]
            label = f"{rule}.{key}"
            if not isinstance(profile, dict):
                errors.append(f"{label}: must be an object")
                continue
            if toggle_is_on(cfg.get("custom_seasonal_profile_on")) and not any(
                    isinstance(profile.get(array), list) and profile[array]
                    for array in ("seasons", "non_repeating_seasons")):
                errors.append(f"{label}: at least one season is required when the toggle is on")
            for array_key, type_key in (("seasons", "price_type"),
                                        ("non_repeating_seasons", "price_type_non_repeating")):
                seasons = profile.get(array_key, [])
                if type_key in profile and profile[type_key] not in ("percentage", "fixed"):
                    errors.append(f"{label}.{type_key}: must be percentage or fixed")
                if not isinstance(seasons, list):
                    errors.append(f"{label}.{array_key}: must be an array of objects")
                    continue
                if seasons and type_key not in profile:
                    errors.append(f"{label}.{type_key}: required when {array_key} is non-empty")
                required = ("season_name", "start_month", "start_day", "end_month", "end_day")
                if array_key == "non_repeating_seasons":
                    required += ("start_year", "end_year")
                for index, season in enumerate(seasons):
                    season_label = f"{label}.{array_key}[{index}]"
                    if not isinstance(season, dict):
                        errors.append(f"{season_label}: must be an object")
                        continue
                    for field in required:
                        if field not in season:
                            errors.append(f"{season_label}.{field}: required")
                        elif not isinstance(season[field], str):
                            errors.append(f"{season_label}.{field}: must be a string")
                    for field in ("lowest_price", "base_price", "highest_price",
                                  "minstay_profile_id", "pricing_profile_id", "checkincheckout_profile_id"):
                        value = season.get(field)
                        # The schema allows null and coerces integer strings. Booleans
                        # and non-finite/fractional values are not integer settings.
                        integer = (type(value) is int or
                                   (isinstance(value, float) and value.is_integer()) or
                                   (isinstance(value, str) and
                                    re.fullmatch(r"[+-]?\d+", value.strip()) is not None))
                        if value is not None and not integer:
                            errors.append(f"{season_label}.{field}: must be an integer or null")
    return errors


def toggle_is_on(raw) -> bool:
    """Read a toggle that may come back as a bool OR as a string.

    `bool("false")` is True in Python. A toggle-off arriving as the STRING "false" read
    as ON, which skipped the destructive-write warning on exactly the write that needed
    it. Mirrors reduce_customizations.toggle_is_on(); kept as its own copy so this
    write-side safety module imports nothing from a read-side reporting module.
    """
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    return bool(raw)


def gated_fields(value, path: str = "") -> list[str]:
    """Every FEATURE_GATED key anywhere in a rule payload, as a dotted path.

    A flat `for field in cfg` scan only ever saw the top level, which is where none of
    the season-related gated fields live: `custom_seasonal_profile` nests a dict under
    its own name holding `seasons` and `price_type`, so `non_repeating_seasons`,
    `price_type_non_repeating` and the four `inherit_*` keys sat one or two levels down
    and passed validation clean -- and a single gated field rejects the WHOLE request.
    """
    found = []
    if isinstance(value, dict):
        for key, sub in value.items():
            here = f"{path}.{key}" if path else key
            if key in FEATURE_GATED:
                found.append(here)
            found.extend(gated_fields(sub, here))
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            found.extend(gated_fields(sub, f"{path}[{i}]"))
    return found


def destructive_warnings(customizations: dict) -> list[str]:
    """Return state-replacement warnings independently of payload validation.

    Fix round 3, F2. A destructive but valid write needs a separate warning because
    PriceLabs may accept it and return 200. A warning is not evidence that a payload
    is valid: callers must also check validate(), including its stated limitations.

    Per references/pricelabs-gotchas.md: toggling last_minute_prices or
    far_out_premium OFF RESETS their stored configuration (to type linear/value 0, and
    value 0/start 999, respectively) rather than preserving it the way
    day_of_week_adjustment and seasonality do. Call this alongside validate() before
    any write that might be a toggle-off for either rule; validate() alone cannot see
    this class of problem because the write is not invalid.
    """
    warnings: list[str] = []
    for rule, toggle_key in (("last_minute_prices", "last_min_factor_on"),
                             ("far_out_premium", "far_out_premium_on")):
        cfg = customizations.get(rule)
        if isinstance(cfg, dict) and toggle_key in cfg and not toggle_is_on(cfg[toggle_key]):
            warnings.append(
                f"{rule}: toggling {toggle_key} off RESETS its stored configuration "
                "(legal, PriceLabs returns 200 -- but re-enabling later needs the "
                "full configuration again, not just the toggle)")

    # EVERY write to custom_seasonal_profile is destructive: there is no partial-season
    # write, the payload replaces the entire stored set. A toggle-off is not the only
    # dangerous shape here, so this is keyed on the rule appearing at all.
    csp = customizations.get("custom_seasonal_profile")
    if isinstance(csp, dict):
        inner = csp.get("custom_seasonal_profile")
        src = inner if isinstance(inner, dict) else csp
        seasons = src.get("seasons")
        # non_repeating_seasons is the other half of the stored set, and a write that
        # carries only that half replaced it while raising nothing. reduce_customizations
        # already counts both lists, so the codebase knew both existed.
        one_off = src.get("non_repeating_seasons")
        if one_off is not None and seasons is None:
            seasons = one_off
        elif one_off is not None and seasons is not None:
            seasons = list(seasons) + list(one_off)
        if seasons is not None:
            if not seasons:
                warnings.append(
                    "custom_seasonal_profile: seasons is EMPTY. An enabled profile "
                    "requires at least one season across the two arrays; validate "
                    "the payload and compare it with the season set from the fresh read")
            else:
                warnings.append(
                    f"custom_seasonal_profile: a write REPLACES the whole season set "
                    f"({len(seasons)} season(s) in this payload). There is no "
                    "partial-season write -- any season missing here is deleted")
    return warnings


def snapshot_payload(listing_id: str, pms: str, current: dict) -> dict:
    """The exact object that restores the prior state. This is the kill switch.

    Deep-copies `current` (fix round 1, C1): without this, the returned payload held a
    live reference, and mutating the caller's `current` dict AFTER snapshotting silently
    changed the snapshot too. A rollback built from that snapshot would then restore the
    mutated state, not the state that was actually live at snapshot time -- the exact
    failure this function exists to prevent, in the one place it can never be wrong.
    """
    return {"listing_id": listing_id, "pms_name": pms, "customizations": copy.deepcopy(current)}


def write_snapshot(payload: dict, out_dir: str) -> str:
    """Persist the rollback payload and return its path. Never overwrite a snapshot.

    Write a complete temporary file, then publish it with an atomic no-clobber link.
    An exists-check followed by os.replace has a race: another caller can publish the
    same destination after the check and have its rollback silently overwritten.
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    listing_raw = str(payload.get("listing_id", "unknown"))
    listing = re.sub(r"[^A-Za-z0-9_-]+", "_", listing_raw)[:8] or "unknown"
    path = os.path.join(out_dir, f"snapshot_{listing}_{stamp}.json")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out_dir,
                                         prefix=".snapshot-", suffix=".tmp", delete=False) as handle:
            tmp_path = handle.name
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp_path, path)
    except FileExistsError as exc:
        raise FileExistsError(
            f"a snapshot already exists for listing {listing!r} at this microsecond "
            f"({path!r}); the caller must not proceed without a confirmed snapshot, and "
            "must not catch this and write anyway"
        ) from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return path


# The three-letter day stems, derived from the one list that defines them, so a rename
# in attribution cannot leave these two out of step.
DAYS = tuple(k.rsplit("_", 1)[-1] for k in DOW_KEYS)

# Market-driven TYPES on the two echo-checkable rules, taken from the per-field enums
# above rather than attribution.MARKET_DRIVEN (a superset that includes the two
# `moderately_*` values, which exist only on seasonality and demand factor).
MARKET_DRIVEN = {"recommended", "conservative", "aggressive"}
assert MARKET_DRIVEN <= (LAST_MIN_FACTOR_TYPES | FAR_OUT_PREMIUM_TYPES), \
    "every market-driven type must exist on at least one echo-checkable rule"

ECHO_FIELDS = {
    # rule -> (toggle key, type key or None, value key or None; dow is per-day)
    "day_of_week_adjustment": ("dow_factor_on", None, None),
    "last_minute_prices": ("last_min_factor_on", "last_min_factor_type",
                           "last_min_factor_value"),
    "far_out_premium": ("far_out_premium_on", "far_out_premium_type",
                        "far_out_premium_value"),
}
# Rules whose config carries a season set or a tone, never one readable number.
ECHO_UNCHECKABLE = {"seasonality", "demand_factor", "custom_seasonal_profile"}


def echo_diff(intent: dict, after: dict) -> list[str]:
    """Compare what you meant against the rule's CONFIG FIELDS on a fresh re-read.

    `intent` is {"rule": <rule name>, "direction": "down"|"up"|"none",
    "magnitude": <positive number, optional>, "day": <required for
    day_of_week_adjustment, e.g. "Fri">}. `after` is the raw per-rule config dict from a
    re-read of GET /v1/customizations/listing -- the same shape
    reduce_customizations.py --json emits, NOT a rendered table row.

    Fix round 2 (Critical 4): this used to read `after["effective"]` and parse English out
    of it. Measured against the live API, `GET /v1/customizations/listing` returns NO
    `effective` key at all -- zero occurrences anywhere in the payload -- so the check
    returned "no effective block returned; the write cannot be confirmed" on every real
    re-read. Step 8's mandatory echo check could never pass, which means the guard that
    catches a discount written as a premium was inoperable against the live API. The
    offline suite passed only because the fixture contained a field the API does not
    return: a field was invented, then tested against the invention.

    Field values are what the API actually returns and what the next read-modify-write
    will merge from, so they are also the right thing to confirm against.

    Returns a list of problems. Empty means the re-read matches the intent.
    """
    rule = intent.get("rule")
    if rule in ECHO_UNCHECKABLE:
        return [f"{rule} carries a season set or a tone, not one signed number: "
                "confirm it in the dashboard, do not report it as echo-checked"]
    if rule not in ECHO_FIELDS:
        return [f"intent must name the rule it wrote (got {rule!r}); "
                f"echo-checkable rules are {sorted(ECHO_FIELDS)}"]

    direction = intent.get("direction")
    if direction not in ("down", "up", "none"):
        return [f"unrecognized direction {direction!r}; expected 'down', 'up' or 'none'"]

    if not isinstance(after, dict) or not after:
        return ["no rule config returned from the re-read; the write cannot be confirmed"]

    toggle_key, type_key, value_key = ECHO_FIELDS[rule]
    problems = []

    # A truthy STRING is not a toggle. PriceLabs has returned "false", and `if "false":`
    # is True in Python, which reads an OFF rule as live.
    raw_toggle = after.get(toggle_key)
    if isinstance(raw_toggle, str):
        is_on = raw_toggle.strip().lower() in ("true", "1", "yes")
    else:
        is_on = bool(raw_toggle)
    if not is_on:
        problems.append(f"{toggle_key} reads {raw_toggle!r} after the write: the rule is "
                        "OFF, so the adjustment is not live")

    # A market-driven or suppressed type means the numeric value carries no sign.
    kind = after.get(type_key) if type_key else None
    if type_key:
        if rule == "last_minute_prices" and kind == "fixed":
            return problems + ["last_min_factor_type is 'fixed': the value is an absolute "
                               "nightly price, not a signed percentage. Confirm the "
                               "absolute amount and its currency separately."]
        if kind in MARKET_DRIVEN:
            # Including direction == "none": a market-driven type is NOT suppression.
            # The rule is still live, PriceLabs just owns the number. Returning clean
            # here told the operator a suppression took when the adjustment was still
            # running.
            problems.append(f"{type_key} reads {kind!r} (market-driven): the adjustment "
                            + ("is still live and set by PriceLabs, not suppressed"
                               if direction == "none" else
                               f"is set by PriceLabs, not by the {direction} you intended"))
            return problems
        if kind == "none":
            # The suppression write the runbook recommends: type `none`, toggle ON.
            if direction != "none":
                problems.append(f"{type_key} reads 'none': the rule is suppressed, not "
                                f"moved {direction}")
            return problems
        known = (LAST_MIN_FACTOR_TYPES if rule == "last_minute_prices"
                 else FAR_OUT_PREMIUM_TYPES)
        if kind not in known:
            # An unrecognized type means we do not know how PriceLabs reads the number,
            # so reading a sign off it is a guess. Say so instead of confirming.
            return problems + [f"{type_key} reads {kind!r}, which is not a type this "
                               "build knows; the value cannot be interpreted"]
    if type_key and direction == "none":
        problems.append(f"intended a suppression, but {type_key} reads {kind!r}")
        return problems

    if rule == "day_of_week_adjustment":
        day = str(intent.get("day") or "").strip().lower()[:3]
        if day not in DAYS:
            return problems + [f"day_of_week_adjustment needs intent['day'] naming one "
                               f"of {DAYS}; got {intent.get('day')!r}. The seven days are "
                               "seven independent settings and confirming the wrong one "
                               "confirms nothing."]
        value_key = f"dow_factor_value_{day}"

    if value_key not in after:
        return problems + [f"{value_key} is missing from the re-read; the write cannot "
                           "be confirmed"]
    value = to_setting(after.get(value_key))
    if value is None:
        return problems + [f"{value_key} reads {after.get(value_key)!r}, which is not a "
                           "number; the write cannot be confirmed"]

    saw = "down" if value < 0 else "up" if value > 0 else "none"
    if saw != direction:
        if saw == "none":
            problems.append(f"{value_key} reads 0 after the write: the adjustment was "
                            "cleared, not set")
        elif direction == "none":
            problems.append(f"intended to CLEAR this day, but {value_key} still reads "
                            f"{value:+g}")
        else:
            problems.append(f"SIGN INVERTED: intended a {direction} move, "
                            f"{value_key} reads {value:+g}")

    magnitude = intent.get("magnitude")
    if magnitude is not None:
        wanted = to_setting(magnitude)
        if wanted is None:
            problems.append(f"intent['magnitude'] is {magnitude!r}, which is not a "
                            "number; the magnitude cannot be confirmed")
        elif abs(abs(value) - abs(wanted)) > 0.51:
            problems.append(f"intended {magnitude}%, {value_key} reads {value:+g}")
    return problems


def signed_from_action(action: dict) -> tuple[float, bool]:
    """Convert an action's recommendation into a signed customization value.

    Returns (value, confirmed). `confirmed` is always False: the convention is inferred
    from the stored value matching `current` exactly plus the meaning of the action type,
    and it has not been proven with a write. A caller must not auto-apply an unconfirmed
    value. See references/pricelabs-gotchas.md, "two sign conventions".
    """
    meta = action.get("metadata") or {}
    recommended = to_setting((meta.get("recommended") or {}).get("discount_pct"))
    if recommended is None:
        raise ValueError("action has no recommended.discount_pct")
    kind = str(action.get("action_type", ""))
    if "last_minute" in kind:
        return -abs(recommended), False
    return recommended, False
