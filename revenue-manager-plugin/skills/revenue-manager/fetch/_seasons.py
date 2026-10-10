"""Each night's min / base / max under a PriceLabs custom seasonal profile (S2-007).

S2-007, a Summit student report of 2026-09-29: with full-year seasons the listing-level min/base/max never apply,
yet the card and the writer read only the listing-level values. This module says which value
prices each night. Pure: no network, no clock.

The rules it applies, and where each one comes from:
  - A season covers its month/day range every year (start on or before end; a season never
    wraps the year end). A one-off season covers real calendar dates.       [customer-api.json]
  - `fixed`: the season's lowest/base/highest price IS that night's min/base/max.
    `percentage`: a signed adjustment, so min = listing min x (1 + v/100), base from the
    listing base, max from the listing max.                     [customer-api.json + PriceLabs KB]
  - An empty (null) field leaves that night on the listing-level value.       [PriceLabs KB]
  - A date no season covers uses the listing-level values.                    [PriceLabs KB]
  - A one-off season beats a repeating one on a date both cover.              [PriceLabs KB]
  DOCS-ONLY: "PriceLabs KB" is PriceLabs' knowledge-base answer (get_knowledge, 2026-10-10),
  not a measurement on a live account with a profile switched on; the read-only probe of the
  same day saw only profiles switched OFF (Boho Bliss: two percentage seasons, stored, off).

A profile switched off is not applied (its seasons stay stored and read back with
toggled_on=false). Which level's profile applies (listing, group, account) is the same rule
stack as every other customization: rules_first.resolve_stack.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

FIELDS = {"min": "lowest_price", "base": "base_price", "max": "highest_price"}
MONTHS = calendar.month_abbr


class SeasonUnreadable(ValueError):
    """The stored profile cannot be read without guessing which value prices a night."""


def toggle_is_on(raw) -> bool:
    """`bool("false")` is True; a toggle can come back as the string."""
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    return bool(raw)


def _int(value, what):
    if isinstance(value, bool):
        raise SeasonUnreadable(f"{what} is {value!r}, not a number")
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise SeasonUnreadable(f"{what} is {value!r}, not a number") from None
    if not math.isfinite(number) or number != int(number):
        raise SeasonUnreadable(f"{what} is {value!r}, not a whole number")
    return int(number)


def _price(value, what):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise SeasonUnreadable(f"{what} is {value!r}, not a price")
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise SeasonUnreadable(f"{what} is {value!r}, not a price") from None
    if not math.isfinite(number):
        raise SeasonUnreadable(f"{what} is {value!r}, not a price")
    return number


@dataclass(frozen=True)
class Season:
    name: str
    one_off: bool
    start: tuple          # (month, day) for a repeating season, a date for a one-off
    end: tuple
    price_type: str       # "fixed" or "percentage"
    values: dict = field(default_factory=dict)   # min/base/max -> number or None

    def covers(self, day: date) -> bool:
        if self.one_off:
            return self.start <= day <= self.end
        return self.start <= (day.month, day.day) <= self.end

    @property
    def label(self) -> str:
        # Dates, not the season's name: the name is free text, and the analysis cache drops it.
        if self.one_off:
            return f"one-off season {self.start.isoformat()} to {self.end.isoformat()}"
        return (f"season {MONTHS[self.start[0]]} {self.start[1]} to "
                f"{MONTHS[self.end[0]]} {self.end[1]}")

    def shown(self, key: str) -> str:
        v = self.values.get(key)
        if v is None:
            return "listing value"
        return f"{v:g}" if self.price_type == "fixed" else f"{v:+g}% of the listing {key}"


def _season(raw, index, kind, price_type, one_off):
    where = f"{kind}[{index}]"
    if not isinstance(raw, dict):
        raise SeasonUnreadable(f"{where} is not an object")
    sm, sd = _int(raw.get("start_month"), f"{where} start_month"), _int(raw.get("start_day"), f"{where} start_day")
    em, ed = _int(raw.get("end_month"), f"{where} end_month"), _int(raw.get("end_day"), f"{where} end_day")
    try:
        if one_off:
            start = date(_int(raw.get("start_year"), f"{where} start_year"), sm, sd)
            end = date(_int(raw.get("end_year"), f"{where} end_year"), em, ed)
        else:
            date(2000, sm, sd), date(2000, em, ed)  # leap-year calendar, so Feb 29 is valid
            start, end = (sm, sd), (em, ed)
    except ValueError:
        raise SeasonUnreadable(f"{where} has a date that does not exist "
                               f"({sm}/{sd} to {em}/{ed})") from None
    if start > end:
        raise SeasonUnreadable(f"{where} ends before it starts; PriceLabs does not wrap a "
                               "season across the year end, so which nights it covers is unknown")
    values = {k: _price(raw.get(f), f"{where} {f}") for k, f in FIELDS.items()}
    return Season(str(raw.get("season_name") or "").strip(), one_off, start, end, price_type, values)


class Profile:
    """The seasons that apply to one listing, ready to answer per-night questions."""

    def __init__(self, seasons, level="listing"):
        self.seasons = list(seasons)
        self.level = level
        for kind in (False, True):
            group = sorted((s for s in self.seasons if s.one_off == kind), key=lambda s: s.start)
            for a, b in zip(group, group[1:]):
                if b.start <= a.end:
                    raise SeasonUnreadable(f"{a.label} and {b.label} overlap; PriceLabs does not "
                                           "allow that, so which one prices those nights is unknown")

    def covering(self, day: date):
        one_off = next((s for s in self.seasons if s.one_off and s.covers(day)), None)
        return one_off or next((s for s in self.seasons if not s.one_off and s.covers(day)), None)

    def night(self, day: date, listing: dict) -> dict:
        """{'min','base','max'} in force on `day`, plus 'set_by' (the season's label per field,
        or None where the listing-level value applies) and 'fixed' (True where a FIXED season
        value replaces the listing value, so changing the listing value does not move it)."""
        s = self.covering(day)
        out = {"set_by": {}, "fixed": {}}
        for key in FIELDS:
            base = listing.get(key)
            v = s.values.get(key) if s else None
            if v is None:
                out[key], out["set_by"][key], out["fixed"][key] = base, None, False
            elif s.price_type == "fixed":
                out[key], out["set_by"][key], out["fixed"][key] = v, s.label, True
            else:
                out[key] = None if base is None else round(base * (1 + v / 100), 2)
                out["set_by"][key], out["fixed"][key] = s.label, False
        return out

    def reach(self, key: str, start: date, days: int) -> dict:
        """How many of the next `days` nights a change to the listing-level `key` reaches.
        moves = nights it prices (no season, an empty season field, or a percentage season,
        which scales from it); season_set = nights a fixed season value replaces it."""
        moves, kept = 0, {}
        for i in range(days):
            s = self.covering(start + timedelta(days=i))
            v = s.values.get(key) if s else None
            if v is not None and s.price_type == "fixed":
                kept.setdefault(s.label, [0, v])[0] += 1
            else:
                moves += 1
        return {"horizon_days": days, "moves": moves,
                "season_set": sum(n for n, _ in kept.values()),
                "seasons": [{"season": label, "nights": n, "value": v}
                            for label, (n, v) in sorted(kept.items(), key=lambda x: -x[1][0])]}

    def summary(self) -> list:
        """One line per season, every number shown (the reducer and the card print these)."""
        lines = []
        for s in sorted(self.seasons, key=lambda s: (s.one_off, s.start)):
            lines.append(f"{s.label} ({s.price_type}): min {s.shown('min')}, "
                         f"base {s.shown('base')}, max {s.shown('max')}")
        return lines


def from_rule(cfg, level="listing"):
    """The profile in one customization entry, or None when it is off or holds no season.
    Raises SeasonUnreadable for a profile that is ON but cannot be read without guessing."""
    if not isinstance(cfg, dict) or not toggle_is_on(cfg.get("custom_seasonal_profile_on")):
        return None
    inner = cfg.get("custom_seasonal_profile")
    if not isinstance(inner, dict):
        raise SeasonUnreadable("the profile is switched on but its seasons are missing")
    seasons = []
    for kind, type_key, one_off in (("seasons", "price_type", False),
                                    ("non_repeating_seasons", "price_type_non_repeating", True)):
        rows = inner.get(kind)
        if rows in (None, []):
            continue
        if not isinstance(rows, list):
            raise SeasonUnreadable(f"{kind} is not a list")
        price_type = inner.get(type_key)
        if price_type not in ("fixed", "percentage"):
            raise SeasonUnreadable(f"{type_key} is {price_type!r}; without it a season's prices "
                                   "cannot be read as amounts or percentages")
        seasons.extend(_season(r, i, kind, price_type, one_off) for i, r in enumerate(rows))
    for flag in ("inherit_baseprice",):
        if toggle_is_on(inner.get(flag)):
            raise SeasonUnreadable(f"{flag} is on: season base prices come from the group or "
                                   "account, which this tool does not resolve")
    return Profile(seasons, level) if seasons else None


def from_stack(listing, group=None, account=None):
    """(profile or None, level) for the custom seasonal profile the listing actually uses:
    listing first, then group, then account (rules_first.resolve_stack's order)."""
    for level, block in (("listing", listing), ("group", group), ("account", account)):
        cfg = (block or {}).get("custom_seasonal_profile") if isinstance(block, dict) else None
        if isinstance(cfg, dict) and toggle_is_on(cfg.get("custom_seasonal_profile_on")):
            return from_rule(cfg, level), level
    return None, None


def fingerprint(cfg, level) -> str:
    """What the writer's floor checks rested on. Apply refuses if it moved."""
    blob = json.dumps({"level": level, "cfg": cfg}, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
