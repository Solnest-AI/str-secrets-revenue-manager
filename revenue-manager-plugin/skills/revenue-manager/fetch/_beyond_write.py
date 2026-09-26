"""The Beyond price writer: plan a change, show it, apply it on a plain yes, prove it. DOCS-ONLY.

Same contract as the PriceLabs writer (_mvp_write.py) and docs/WRITE-TARGETS.md, with Beyond's
own semantics. Every endpoint is cited in references/beyond.md (read 2026-09-25). No Beyond
account has ever been written by this code: the card says so on every plan.

  plan      FRESH read (never cache) -> operations with live before and requested after ->
            plain-language warnings -> saved under the content hash of what would be written.
  apply     hash-checked plan -> fresh read -> REFUSE on drift -> rollback snapshot on disk
            BEFORE sending -> send (never retried) -> re-read -> check every field written AND
            every field on those resources that was not.
  rollback  the reverse change, built from the journal, planned and shown like any other.

What it will write, and nothing else (WriteClient refuses the rest, per listing):
  PATCH /api/v1/listings/{id}/customizations/base-price/        base-price only
  PATCH /api/v1/listings/{id}/customizations/min-max-prices/    min-price / max-price only
  PATCH /api/v1/listings/{id}/customizations/min-stays/         min-stay (annual) only
  PATCH /api/v1/listings/{id}/customizations/manual-overrides/  one row per date
Reads: GET listings/{id}/, customizations/, customizations/manual-overrides/, calendar/.

Beyond facts this writer is built around (docs, not measured):
  - Prices are MAJOR units. base-price is an integer >= 10; min-price a number >= 5; a fixed
    override price a number >= 1; percentage-adjustment an integer -100..1000. Beyond prices
    and pushes whole units, so this writer only accepts whole-unit base and fixed prices.
  - A fixed override is accepted BELOW the listing minimum ("an explicit override outranks the
    floor"). Beyond will not protect the floor, so this writer refuses it.
  - Manual-overrides PATCH is additive; a row with neither value clears the date. The GET
    returns one row per date that can be sent straight back, which is what the undo does.
  - max-price null is "no ceiling". PATCH updates only the attributes sent.
Refused because the docs do not show a safe way (said on the refusal, never guessed):
  - a per-date min stay (manual overrides carry none; seasonal min-stay rules are a list whose
    PATCH replace-or-merge behaviour is undocumented), stacked percentages, multi-date or
    weekday-restricted overrides, activation / refresh / other customization families.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import _beyond as B
from _mvp_store import CannotAnalyze
from _mvp_write import (  # the PriceLabs writer's hardened helpers, reused as-is
    CannotWrite, _NoRedirect, _check_fresh, _date, _money, _num, _over, _parse_date, _pct,
    _write_new, canonical, content_hash, load_envelope, load_plan, max_delta_fraction, plan_id,
    save_envelope,
)

__all__ = ["CannotWrite", "WriteClient", "Live", "live_for", "plan_change", "describe",
           "apply_envelope", "apply_batch", "rollback_change", "plan_id", "content_hash",
           "save_envelope", "load_envelope", "load_plan"]

VERSION = 1
TARGET = B.PMS_NAME
LISTING_FIELDS = ("min", "base", "max")
TOP_KEYS = {"listing_id", "pms", "reason", "listing_prices", "listing_min_stay", "overrides_set",
            "overrides_delete", "overrides_restore"}
OVERRIDE_INPUT = {"date", "price", "price_type"}
OVERRIDE_STORED = {"date", "price", "percentage_adjustment"}
PCT_RANGE = (-100, 1000)     # OpenAPI ManualOverrideRequest.percentage-adjustment
BASE_MIN, MIN_MIN, FIXED_MIN = 10, 5, 1
OVERRIDE_WINDOW = 365        # the documented default manual-overrides read window
CALENDAR_DAYS = 90
FIRST_LIVE = ("First live write for Beyond: read the after-values carefully. This writer has only "
              "run against a fake Beyond API built from the docs, never a live account.")
_PREFIX = "/api/v1/listings/{lid}/"
PATHS = {("GET", ""), ("GET", "customizations/"), ("GET", "customizations/manual-overrides/"),
         ("GET", "calendar/"), ("PATCH", "customizations/base-price/"),
         ("PATCH", "customizations/min-max-prices/"), ("PATCH", "customizations/min-stays/"),
         ("PATCH", "customizations/manual-overrides/")}
STEP = {"base": ("customizations/base-price/", "base-price-customizations"),
        "minmax": ("customizations/min-max-prices/", "min-max-price-customizations"),
        "minstay": ("customizations/min-stays/", "min-stay-customizations"),
        "overrides": ("customizations/manual-overrides/", "manual-override-customizations")}


def _lid(value) -> str:
    try:
        return B.listing_id(value)
    except CannotAnalyze as exc:
        raise CannotWrite(str(exc)) from None


# ------------------------------------------------------------------------------ transport

class WriteClient:
    """The only transport that can change a Beyond price. Bound to ONE listing: it knows the
    eight exact method+path pairs above for that id and refuses everything else, including
    every other Beyond write (activation, refresh, users, webhooks, other customizations).
    No redirects, 60s timeout, a call budget, one attempt per call, no response bodies."""

    def __init__(self, token: str, listing_id, opener=None, max_calls: int = 30):
        if not token:
            raise CannotWrite("No Beyond token; put BEYOND_TOKEN in the connector .env")
        self._token = token
        self.lid = _lid(listing_id)
        self.allowed = {(m, _PREFIX.format(lid=self.lid) + s) for m, s in PATHS}
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.max_calls = max_calls
        self.calls = []

    def request(self, method: str, path: str, params=None, body=None):
        if (method, path) not in self.allowed:
            raise CannotWrite(f"The Beyond write transport refuses {method} {path}")
        if len(self.calls) >= self.max_calls:
            raise CannotWrite(f"HTTP call budget ({self.max_calls}) reached")
        url = f"https://{B.HOST}{path}" + ("?" + urlencode(params) if params else "")
        if urlsplit(url).netloc != B.HOST:
            raise CannotWrite("The Beyond write transport only talks to Beyond")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": B.MEDIA, "User-Agent": B.UA}
        if body is not None:
            headers["Content-Type"] = B.MEDIA
        req = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        self.calls.append({"method": method, "path": path})
        try:
            with self.opener.open(req, timeout=60) as resp:
                raw, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            exc.close()  # Beyond error details can echo channel credentials; never surface them
            raise CannotWrite(f"Beyond {method} {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CannotWrite(f"Beyond {method} {path}: no readable response") from None
        self.calls[-1]["status"] = status
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise CannotWrite(f"Beyond {method} {path}: response is not JSON") from None


# ------------------------------------------------------------------------------ live reads

class Live:
    """Fresh reads for one Beyond listing. Nothing is cached, by construction."""

    def __init__(self, client: WriteClient, listing_id):
        lid = _lid(listing_id)
        if client.lid != lid:
            raise CannotWrite("The Beyond transport is bound to another listing")
        self.client, self.lid, self.pms = client, lid, TARGET
        self.base = _PREFIX.format(lid=lid)

    def _read(self, fn):
        try:
            return fn()
        except CannotAnalyze as exc:
            raise CannotWrite(str(exc)) from None

    def listing(self) -> dict:
        info = self._read(lambda: B.parse_listing(self.client.request("GET", self.base), self.lid))
        cust = self._read(lambda: B.parse_customizations(
            self.client.request("GET", self.base + "customizations/"), self.lid))
        if cust["base"] is None or cust["base"] <= 0:
            raise CannotWrite("Beyond has no base price set for this listing; set one in Beyond first")
        if cust["min"] is None or cust["min"] <= 0:
            raise CannotWrite("Beyond has no minimum price set for this listing, so there is no floor "
                              "to check nights against. Set one in Beyond first.")
        if cust["max"] is not None and cust["max"] <= 0:
            raise CannotWrite(f"live max is {cust['max']}; refusing to plan against it")
        return {"name": info["name"], "currency": info["currency"], "timezone": info["timezone"],
                "enabled": info["enabled"], "in_active_market": info["in_active_market"],
                "min": cust["min"], "base": cust["base"], "max": cust["max"],
                "min_stay": cust["min_stay"], "raw": cust["raw"]}

    def overrides(self, start: date) -> dict:
        end = start + timedelta(days=OVERRIDE_WINDOW)
        return self._read(lambda: B.parse_overrides(self.client.request(
            "GET", self.base + "customizations/manual-overrides/",
            {"filter[start-date]": start.isoformat(), "filter[end-date]": end.isoformat()}), self.lid))

    def calendar(self, start: date, end: date) -> dict:
        return self._read(lambda: B.parse_calendar(self.client.request(
            "GET", self.base + "calendar/",
            {"filter[start-date]": start.isoformat(), "filter[end-date]": end.isoformat(),
             "sort": "date", "page[size]": B.CALENDAR_PAGE}), start, end))


def live_for(connections, listing_id, pms=None) -> Live:
    """Same call shape as apply_change.live_for; `pms` is accepted and must be beyond or None."""
    if pms not in (None, TARGET):
        raise CannotWrite(f"A Beyond listing is addressed by its Beyond id alone, not pms {pms!r}")
    try:
        token = connections.key("beyond")
    except CannotAnalyze as exc:
        raise CannotWrite(str(exc)) from None
    lid = _lid(listing_id)
    return Live(WriteClient(token, lid), lid)


# ------------------------------------------------------------------------------ small pieces

def _same(a, b) -> bool:
    """None-aware numeric equality (max-price and min-stay can be null)."""
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < 0.005


def _field_same(k, x, y) -> bool:
    """One field of an override: equal, both blank (None and "" are the same: PriceLabs stores a
    missing note as "", live 2026-09-25), or the same number. Text that is not a number compares
    as text instead of raising, because this runs AFTER a write was sent."""
    if x == y or (x in (None, "") and y in (None, "")):
        return True
    if k == "date" or x is None or y is None:
        return False
    try:
        return _same(x, y)
    except (TypeError, ValueError):
        return str(x) == str(y)


def _same_override(a, b) -> list:
    if a is None or b is None:
        return [] if a is b else ["<presence>"]
    return sorted(k for k in set(a) | set(b) if not _field_same(k, a.get(k), b.get(k)))


def _bounds_ok(mn, base, mx) -> bool:
    return mn <= base and (mx is None or base <= mx)


def _show_money(v) -> str:
    return "no ceiling" if v is None else _money(v)


def _show(o) -> str:
    if o is None:
        return "no override"
    if "price" in o:
        return f"fixed {_money(o['price'])}"
    return f"{o['percentage_adjustment']:+d}% on Beyond's price"


def _whole(value, what, low) -> float:
    v = _num(value, what)
    if v != int(v):
        raise CannotWrite(f"{what} {value!r} is not a whole number: Beyond prices and pushes whole "
                          "currency units (docs: integers, never cents)")
    if v < low:
        raise CannotWrite(f"{what} must be at least {low} (Beyond API minimum)")
    return float(int(v))


def _listing_value(field, value) -> float:
    if field == "base":
        return _whole(value, "base", BASE_MIN)
    v = round(_num(value, field), 2)
    if field == "min" and v < MIN_MIN:
        raise CannotWrite(f"min must be at least {MIN_MIN} (Beyond API minimum)")
    if v <= 0:  # checked after rounding
        raise CannotWrite(f"{field} must be above zero (it would be sent as {_money(v)})")
    return v


def _pct_value(value, what) -> int:
    v = _num(value, what)
    if v != int(v):
        raise CannotWrite(f"{what} must be a whole percent (Beyond: integer)")
    if not PCT_RANGE[0] <= v <= PCT_RANGE[1]:
        raise CannotWrite(f"{what} must be between {PCT_RANGE[0]} and {PCT_RANGE[1]}")
    return int(v)


def _stored_override(item, d) -> dict:
    """An override as this writer stores it: {"date", "price"} or {"date",
    "percentage_adjustment"}. Used for undo input, which comes from a saved before-image."""
    bad = set(item) - OVERRIDE_STORED
    if bad:
        raise CannotWrite(f"{sorted(bad)} is not a field a Beyond override carries")
    has_p, has_q = item.get("price") is not None, item.get("percentage_adjustment") is not None
    if has_p == has_q:
        raise CannotWrite(f"{d}: a saved override carries exactly one of price or percentage")
    if has_p:
        return {"date": d, "price": round(_num(item["price"], f"{d} price"), 2)}
    return {"date": d, "percentage_adjustment": _pct_value(item["percentage_adjustment"], f"{d} percent")}


def _listing_today(listing, now: datetime) -> date:
    """Beyond rejects override dates already past in the LISTING's timezone. Use the later of
    this machine's date and the listing's, so a plan never offers a date Beyond will refuse."""
    today = date.today()
    try:
        return max(today, now.astimezone(ZoneInfo(str(listing.get("timezone")))).date())
    except (ValueError, KeyError, TypeError, OSError):
        return today


def seen_digest(listing) -> str:
    """The price rules a plan checked against: base price and every min/max rule family
    (annual, day-of-week, seasonal). A change to any of them since the plan is drift."""
    raw = listing["raw"]
    return hashlib.sha256(canonical({"base-price": raw["base-price"],
                                     "min-max-prices": raw["min-max-prices"]})).hexdigest()[:16]


def send_order(before: dict, after: dict) -> list:
    """min-max and base are two separate PATCHes. Pick the order that keeps min <= base <= max
    true after EACH one, so a failure between them never leaves Beyond with a broken stack."""
    mm = not (_same(before["min"], after["min"]) and _same(before["max"], after["max"]))
    base = not _same(before["base"], after["base"])
    if not (mm and base):
        return ["minmax"] if mm else ["base"] if base else []
    if _bounds_ok(after["min"], before["base"], after["max"]):
        return ["minmax", "base"]
    if _bounds_ok(before["min"], after["base"], before["max"]):
        return ["base", "minmax"]
    raise CannotWrite("No order of Beyond's two calls (min/max, then base, or the reverse) keeps "
                      "min <= base <= max true in between. Split it into two changes: move base "
                      "with a wider min/max first, then tighten.")


# ------------------------------------------------------------------------------ plan

def plan_change(change: dict, live: Live, *, today: date | None = None, now: datetime | None = None,
                max_delta=None, max_delta_pct=None, rollback: bool = False) -> dict:
    """rollback=True is set ONLY by the undo path, which builds the change from a journal or
    snapshot this writer saved; it is the only way overrides_restore, a null max (no ceiling)
    or a null min stay is accepted. Items already back are skipped and past dates dropped."""
    now = now or datetime.now(timezone.utc)
    delta = max_delta_fraction(max_delta if max_delta is not None else max_delta_pct)
    over = _over(delta)
    if not isinstance(change, dict):
        raise CannotWrite("The change must be a JSON object")
    unknown = set(change) - TOP_KEYS
    if unknown:
        raise CannotWrite(f"Unsupported change keys {sorted(unknown)}: the Beyond writer does listing "
                          "min/base/max, the annual min stay and date overrides only")
    if change.get("overrides_restore") and not rollback:
        raise CannotWrite("overrides_restore is only built by the undo command (apply_change.py "
                          "rollback --journal <journal or snapshot>). A change file cannot carry it; "
                          "use overrides_set for the value you want on a date.")
    if change.get("pms") not in (None, TARGET):
        raise CannotWrite(f"This change names pms {change.get('pms')!r}; a Beyond change carries "
                          "pms \"beyond\" or none (Beyond listings are addressed by Beyond's id)")
    if _lid(change.get("listing_id")) != live.lid:
        raise CannotWrite("The change names a different listing than the one being read")
    reason = change.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise CannotWrite("Every change needs a reason; it goes in the audit trail")
    prices = change.get("listing_prices") or {}
    o_set = change.get("overrides_set") or []
    o_del = change.get("overrides_delete") or []
    o_res = change.get("overrides_restore") or []
    has_ms = "listing_min_stay" in change
    if not isinstance(prices, dict) or not all(isinstance(x, list) for x in (o_set, o_del, o_res)):
        raise CannotWrite("listing_prices is an object; the overrides_* keys are lists")
    if not (prices or o_set or o_del or o_res or has_ms):
        raise CannotWrite("The change has nothing to write")

    listing = live.listing()
    today = today or _listing_today(listing, now)
    currency = listing["currency"]
    ops, warnings, already, past = [], [], 0, []

    # listing min / base / max
    merged = {f: listing[f] for f in LISTING_FIELDS}
    for field, value in prices.items():
        if field not in LISTING_FIELDS:
            raise CannotWrite(f"{field!r} is not a field this writer sets (min, base, max only)")
        if value is None:
            if not (rollback and field == "max"):
                raise CannotWrite(f"{field} cannot be cleared by a change file"
                                  + ("; only the undo of a ceiling this writer added removes one"
                                     if field == "max" else ""))
            after = None
        else:
            after = _listing_value(field, value)
        if _same(after, listing[field]):
            if rollback:
                already += 1
                continue
            raise CannotWrite(f"{field} is already {_show_money(after)}")
        before = listing[field]
        merged[field] = after
        ops.append({"kind": "listing_price", "field": field, "before": before, "after": after})
        if before is None:
            warnings.append(f"ADDS A CEILING of {_money(after)} where there was none. Beyond's docs "
                            "advise against limiting its prices with a maximum.")
        elif after is None:
            warnings.append(f"REMOVES the ceiling of {_money(before)}; Beyond prices without a max.")
        elif _pct(before, after) > delta + 1e-9:
            warnings.append(f"{over}: {field} {_money(before)} -> {_money(after)} "
                            f"({(after - before) / before:+.1%}). Extra scrutiny (D8).")
    if not _bounds_ok(merged["min"], merged["base"], merged["max"]):
        raise CannotWrite(f"After this change min <= base <= max would not hold ({merged['min']}, "
                          f"{merged['base']}, {_show_money(merged['max'])})")
    send_order(listing, merged)  # refuses now, not at apply, if no safe order exists
    min_raised = any(op["field"] == "min" and op["after"] > op["before"] for op in ops)
    max_cut = any(op["field"] == "max" and op["after"] is not None
                  and (op["before"] is None or op["after"] < op["before"]) for op in ops)
    if any(op["field"] == "base" for op in ops):
        warnings.append("Beyond accepts its own base-price recommendations automatically by default "
                        "(docs: Recommendations), so a later recommendation can replace this base.")

    # annual min stay
    if has_ms:
        v = change["listing_min_stay"]
        if v is None and not rollback:
            raise CannotWrite("listing_min_stay must be a whole number of nights, 1 or more")
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            raise CannotWrite("listing_min_stay must be a whole number of nights, 1 or more")
        if v == listing["min_stay"]:
            if not rollback:
                raise CannotWrite(f"the annual min stay is already {v}")
            already += 1
        else:
            ops.append({"kind": "listing_min_stay", "field": "min_stay",
                        "before": listing["min_stay"], "after": v})
            warnings.append("The annual min stay applies wherever no day-of-week, seasonal, "
                            "lead-time or gap-fill min-stay rule in Beyond overrides it.")

    # date overrides
    horizon = today + timedelta(days=OVERRIDE_WINDOW)

    def when(value, undo=False):
        if undo and _parse_date(value) < today:
            past.append(value)
            return None
        d = _date(value, today)
        if _parse_date(d) > horizon:
            raise CannotWrite(f"{d} is past {horizon}: Beyond's override read covers a year ahead, so "
                              "a later date could not be re-read to prove the write")
        return d

    seen, restored = set(), set()

    def once(d):
        if d in seen:
            raise CannotWrite(f"{d} appears twice in one change")
        seen.add(d)

    existing = live.overrides(today) if (o_set or o_del or o_res or min_raised or max_cut) else {}
    for item in o_set:
        if not isinstance(item, dict):
            raise CannotWrite("Each overrides_set entry is an object")
        if "min_stay" in item:
            raise CannotWrite("Beyond cannot take a min stay on a single date through its API: manual "
                              "overrides carry only a price or a percentage, and per-date min stays "
                              "live in the seasonal min-stay rules, a list whose PATCH behaviour "
                              "(replace or merge) Beyond does not document. Refused rather than "
                              "guessed; set it in Beyond's calendar.")
        bad = set(item) - OVERRIDE_INPUT
        if bad:
            raise CannotWrite(f"{sorted(bad)} is not a field this writer sets on a Beyond date "
                              "(price with price_type fixed or percent)")
        d = when(item.get("date"))
        once(d)
        ptype = item.get("price_type")
        if "price" not in item or ptype is None:
            raise CannotWrite(f"{d}: an override needs price and price_type (fixed or percent)")
        if ptype == "fixed":
            after = {"date": d, "price": _whole(item["price"], f"{d} fixed price", FIXED_MIN)}
        elif ptype == "percent":
            after = {"date": d, "percentage_adjustment": _pct_value(item["price"], f"{d} percent")}
        elif ptype == "percent_stacked":
            raise CannotWrite("Beyond has no stacked percentage; its percentage is applied after "
                              "every other pricing factor. Use percent.")
        else:
            raise CannotWrite("price_type must be fixed or percent")
        before = existing.get(d)
        if not _same_override(before, after):
            raise CannotWrite(f"{d} already has exactly this override")
        ops.append({"kind": "override", "date": d, "before": before, "after": after})
        if before:
            warnings.append(f"REPLACES the override on {d} (was {_show(before)}).")
    for item in o_res:
        if not isinstance(item, dict):
            raise CannotWrite("Each overrides_restore entry is an object")
        d = when(item.get("date"), undo=True)
        if d is None:
            continue
        once(d)
        before, after = existing.get(d), _stored_override(item, d)
        if not _same_override(before, after):
            already += 1
            continue
        ops.append({"kind": "override", "date": d, "before": before, "after": after})
        restored.add(d)
        warnings.append(f"RESTORES the override on {d} exactly as it was before ({_show(after)}).")
    for d in o_del:
        d = when(d, undo=rollback)
        if d is None:
            continue
        once(d)
        if d not in existing:
            if rollback:
                already += 1
                continue
            raise CannotWrite(f"{d} has no override to delete")
        ops.append({"kind": "override", "date": d, "before": existing[d], "after": None})
        warnings.append(f"REMOVES the override on {d} (was {_show(existing[d])}); that night goes "
                        "back to Beyond's own modeled price.")

    if rollback:
        if past:
            warnings.append(f"{len(past)} date(s) are in the past now and were left out of the undo: "
                            f"{', '.join(sorted(past))}.")
        if already:
            warnings.append(f"{already} item{'s' if already != 1 else ''} already back to before; "
                            "left out of the undo.")
        if not ops:
            raise CannotWrite(f"Nothing to undo: {already} item(s) already back to before"
                              + (f", {len(past)} date(s) in the past" if past else "") + ".")

    # the calendar: what Beyond prices each night at now (and pushes)
    priced = [op for op in ops if op["kind"] == "override" and op["after"]]
    calendar = {}
    if min_raised or max_cut or priced:
        if listing["in_active_market"] is False:
            raise CannotWrite("Beyond says this listing is not in an active market (in-active-market "
                              "false), so its calendar cannot be read and the nights this change "
                              "moves cannot be checked. Nothing was planned.")
        last = max([today + timedelta(days=CALENDAR_DAYS - 1)]
                   + [_parse_date(op["date"]) for op in priced])
        calendar = live.calendar(today, last)

    touched = {op["date"] for op in ops if op["kind"] == "override"}
    for moved, below, bound, word, pick in (
            (min_raised, True, merged["min"], "below the new min", "lifted to"),
            (max_cut, False, merged["max"], "above the new max", "cut to")):
        if not moved:
            continue
        hit = {d: c["price"] for d, c in calendar.items()
               if c["override_type"] is None and (c["price"] < bound if below else c["price"] > bound)}
        pinned = sorted(d for d, c in calendar.items()
                        if c["override_type"] and d not in touched
                        and (c["price"] < bound if below else c["price"] > bound))
        if pinned:
            warnings.append(f"{len(pinned)} night(s) with a manual override are priced {word} "
                            f"{_money(bound)} and Beyond keeps an override as set (a fixed price "
                            f"outranks the floor), so they are NOT {pick} it: "
                            f"{', '.join(pinned[:8])}{' ...' if len(pinned) > 8 else ''}.")
        if not hit:
            continue
        line = (f"{len(hit)} of the next {CALENDAR_DAYS} nights are priced {word} "
                f"{_money(bound)} today and will be {pick} it.")
        moves = sorted(((bound - p) / p, d, p) for d, p in hit.items() if p > 0)
        zero = sorted(d for d, p in hit.items() if p <= 0)
        if moves:
            m, d, p = max(moves, key=lambda x: abs(x[0]))
            biggest = f"{d} {_money(p)} -> {_money(bound)} ({m:+.1%})"
            if abs(m) > delta + 1e-9:
                warnings += [line, f"{over}: the biggest single-night move is {biggest}. "
                                   "Extra scrutiny (D8)."]
            else:
                warnings.append(line + f" Biggest single-night move: {biggest}.")
        else:
            warnings.append(line)
        if zero:
            warnings.append(f"{len(zero)} of those nights show 0 in Beyond, so their move could not be "
                            f"measured ({', '.join(zero[:5])}).")

    # every override: never below the floor (Beyond itself would allow it), the 15% flag
    for op in priced:
        d, a, prev = op["date"], op["after"], op["before"] or {}
        cal = calendar.get(d)
        if cal is not None and cal["price"] <= 0:
            raise CannotWrite(f"{d}: Beyond shows 0 for that night, so this change cannot be measured "
                              "against it. Nothing was planned; check that date in Beyond first.")
        floor, floor_word = merged["min"], "your min"
        eff = cal["effective_min_price"] if cal else None
        if eff is not None and eff > listing["min"] + 0.005 and eff > floor:
            floor, floor_word = eff, "that night's own floor in Beyond (a seasonal, day-of-week or " \
                                     "Beyond-set minimum)"
        if "price" in a:
            night = a["price"]
            ref = prev["price"] if "price" in prev else (cal["price"] if cal else None)
        else:
            p = a["percentage_adjustment"]
            if cal is None:
                underlying = None
            elif not prev:
                underlying = cal["price"]
            elif "percentage_adjustment" in prev and prev["percentage_adjustment"] > -100:
                underlying = cal["price"] / (1 + prev["percentage_adjustment"] / 100)
            else:
                underlying = None  # a fixed override hides Beyond's modeled number for that night
            night = underlying * (1 + p / 100) if underlying is not None else None
            ref = cal["price"] if cal else None
        if night is None:
            warnings.append(f"{d}: no modeled price for that night, so the {a['percentage_adjustment']:+d}% "
                            "could not be checked against your min and max.")
        elif night < floor - 0.005 and d in restored:
            # An undo puts back exactly what the operator had. Refusing it would leave the change
            # it undoes stuck in place, so the undo says it loudly instead of refusing.
            warnings.append(f"UNDO RESTORES A NIGHT BELOW {floor_word.upper()}: {d} goes back to "
                            f"{_money(night)} (floor {_money(floor)}), exactly as it was before.")
        elif night < floor - 0.005:
            raise CannotWrite(f"{d}: that night would be {_money(night)}, below {floor_word} of "
                              f"{_money(floor)}. Beyond accepts an override under the floor, so this "
                              "writer refuses it. Nothing was planned.")
        elif merged["max"] is not None and night > merged["max"] + 0.005:
            warnings.append(f"ABOVE YOUR MAX: {d} would be {_money(night)}, above your max of "
                            f"{_money(merged['max'])}. A fixed price is charged as written.")
        if ref is None or night is None:
            if "price" in a:
                warnings.append(f"{d}: no current price to compare against; the 15% check could "
                                "not run.")
            elif abs(a["percentage_adjustment"]) > delta * 100 + 1e-9:
                warnings.append(f"{over}: {d} override is {a['percentage_adjustment']:+d}% on "
                                "Beyond's price (D8).")
        elif ref > 0 and _pct(ref, night) > delta + 1e-9:
            warnings.append(f"{over}: {d} {_show(a)} is {_money(night)} vs {_money(ref)} now "
                            f"({(night - ref) / ref:+.1%}). Extra scrutiny (D8).")

    if listing["enabled"] is False:
        warnings.append("Price syncing is OFF for this listing in Beyond (enabled false): this "
                        "changes Beyond's settings, but nothing reaches the channel until syncing "
                        "is turned on.")
    warnings.append(FIRST_LIVE)
    return {
        "version": VERSION,
        "target": {"listing_id": live.lid, "pms": TARGET, "currency": currency},
        "listing_name": listing["name"],
        "reason": reason.strip(),
        "operations": ops,
        "warnings": warnings,
        "seen": seen_digest(listing),
        "read_at": now.isoformat(timespec="seconds"),
        "created_at": now.isoformat(timespec="seconds"),
    }


def describe(envelope: dict) -> str:
    """The plain-language card the operator says yes or no to."""
    t = envelope["target"]
    lines = [f"PROPOSED CHANGE for {envelope.get('listing_name') or t['listing_id']} "
             f"(Beyond listing {t['listing_id']}, {t.get('currency') or 'no currency'})",
             f"Reason: {envelope['reason']}", ""]
    for op in envelope["operations"]:
        if op["kind"] == "listing_price":
            lines.append(f"  {op['field']:>8}: {_show_money(op['before'])} -> {_show_money(op['after'])}")
        elif op["kind"] == "listing_min_stay":
            lines.append(f"  min stay: {op['before'] or 'not set'} -> {op['after'] or 'not set'} nights")
        else:
            lines.append(f"  {op['date']}: {_show(op['before'])}  ->  {_show(op['after'])}")
    if envelope["warnings"]:
        lines += ["", "READ BEFORE SAYING YES:"] + [f"  ! {w}" for w in envelope["warnings"]]
    lines += ["", f"Plan {plan_id(envelope)}"]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ apply

def rollback_change(journal_or_envelope: dict) -> dict:
    """The change that puts every field back to its before-image. Accepts a journal, an
    envelope, or a rollback snapshot (which is already this change). Plan the result with
    plan_change(..., rollback=True)."""
    if not isinstance(journal_or_envelope, dict):
        raise CannotWrite("That is not a journal or snapshot this writer saved")
    if "envelope" not in journal_or_envelope and "operations" not in journal_or_envelope:
        snap = copy.deepcopy(journal_or_envelope)
        if not snap.get("listing_id") or set(snap) - TOP_KEYS or snap.get("pms") != TARGET:
            raise CannotWrite("That is not a journal or snapshot the Beyond writer saved")
        return snap
    env = journal_or_envelope.get("envelope", journal_or_envelope)
    t = env.get("target") or {}
    if t.get("pms") != TARGET:
        raise CannotWrite("That journal is not a Beyond change")
    out = {"listing_id": t["listing_id"], "pms": TARGET,
           "reason": f"ROLLBACK of plan {plan_id(env)}: {env.get('reason', '')}".strip()}
    prices = {op["field"]: op["before"] for op in env["operations"] if op["kind"] == "listing_price"}
    restore, delete = [], []
    for op in env["operations"]:
        if op["kind"] == "listing_min_stay":
            out["listing_min_stay"] = op["before"]
        elif op["kind"] == "override":
            if op["before"] is None:
                delete.append(op["date"])
            else:
                restore.append(copy.deepcopy(op["before"]))
    if prices:
        out["listing_prices"] = prices
    if restore:
        out["overrides_restore"] = restore
    if delete:
        out["overrides_delete"] = delete
    return out


def _check_bounds(listing: dict, envelopes: list) -> dict:
    merged = {f: listing[f] for f in LISTING_FIELDS}
    for env in envelopes:
        for op in env["operations"]:
            if op["kind"] == "listing_price":
                merged[op["field"]] = op["after"]
    if not _bounds_ok(merged["min"], merged["base"], merged["max"]):
        raise CannotWrite(f"Live values plus the approved change(s) would leave min <= base <= max "
                          f"broken (min {_money(merged['min'])}, base {_money(merged['base'])}, max "
                          f"{_show_money(merged['max'])}). Nothing was sent; plan again.")
    return merged


def _doc(rtype, lid, attrs) -> dict:
    return {"data": {"type": rtype, "id": lid, "attributes": attrs}}


def _override_row(op) -> dict:
    """One manual-overrides row. Neither value = clear the date (docs)."""
    row = {"start-date": op["date"], "end-date": op["date"]}
    a = op["after"]
    if a and "price" in a:
        row["price"] = int(a["price"]) if a["price"] == int(a["price"]) else a["price"]
    elif a:
        row["percentage-adjustment"] = a["percentage_adjustment"]
    return row


OWNED = {("base-price", "base-price"), ("min-max-prices", "min-price"),
         ("min-max-prices", "max-price"), ("min-stays", "min-stay")}


def _rules_diff(before_raw, after_raw) -> list:
    """Every customization family compared whole, apart from the four scalars this writer owns
    (those are checked field by field against their wanted value). Seasonal, day-of-week,
    lead-time, gap-fill, extra-guest and adjustment rules must be exactly what they were."""
    out = []
    for fam in B.AGGREGATE_KEYS:
        b, a = dict(before_raw.get(fam) or {}), dict(after_raw.get(fam) or {})
        for f, key in OWNED:
            if f == fam:
                b.pop(key, None)
                a.pop(key, None)
        out += [f"{fam}.{k}" for k in sorted(set(a) | set(b))
                if canonical(a.get(k)) != canonical(b.get(k))]
    return out


def apply_envelope(envelope: dict, live: Live, *, state_dir, today: date | None = None,
                   now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    t = envelope.get("target") or {}
    if t.get("pms") != TARGET or str(t.get("listing_id")) != live.lid:
        raise CannotWrite("The plan is not a Beyond plan for the listing connected")
    listing = live.listing()
    today = today or _listing_today(listing, now)
    _check_fresh(envelope, now, today)
    h12 = content_hash(envelope)[:12]
    state = Path(state_dir)
    ops = envelope["operations"]

    # 1. fresh read; everything the plan saw must still be true
    touches_overrides = any(op["kind"] == "override" for op in ops)
    overrides = live.overrides(today) if touches_overrides else {}
    if listing["currency"] != t.get("currency"):
        raise CannotWrite("The listing currency changed since the plan. Nothing was sent; plan again.")
    for op in ops:
        if op["kind"] == "listing_price":
            if not _same(listing[op["field"]], op["before"]):
                raise CannotWrite(f"{op['field']} changed since the plan ({_show_money(op['before'])} -> "
                                  f"{_show_money(listing[op['field']])} live). Nothing was sent; plan again.")
        elif op["kind"] == "listing_min_stay":
            if listing["min_stay"] != op["before"]:
                raise CannotWrite("The annual min stay changed since the plan. Nothing was sent; "
                                  "plan again.")
        elif _same_override(overrides.get(op["date"]), op["before"]):
            raise CannotWrite(f"The override on {op['date']} changed since the plan. Nothing was "
                              "sent; plan again.")
    if envelope.get("seen") != seen_digest(listing):
        raise CannotWrite("A price rule in Beyond (base price, or a min/max rule: annual, day-of-week "
                          "or seasonal) changed since the plan, so its floor checks are stale. "
                          "Nothing was sent; plan again.")
    merged = _check_bounds(listing, [envelope])
    steps = send_order(listing, merged)
    ms_op = next((op for op in ops if op["kind"] == "listing_min_stay"), None)
    over_ops = [op for op in ops if op["kind"] == "override"]
    steps += (["minstay"] if ms_op else []) + (["overrides"] if over_ops else [])

    # 2. rollback snapshot, on disk before anything leaves the machine
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + os.urandom(3).hex()
    snap = _write_new(state / "snapshots" / f"{stamp}-{h12}.json", rollback_change(envelope))
    journal = {"envelope": envelope, "plan_id": plan_id(envelope), "status": "failed-before-send",
               "snapshot_path": str(snap), "sent": [], "verification": [], "applied_at": None}
    jpath = state / "journal" / f"{stamp}-{h12}.json"
    undo = f"apply_change.py rollback --journal {jpath.name}"
    written = False

    def finish(status, problem=None):
        nonlocal written
        journal["status"] = status
        journal["journal_path"] = str(jpath)
        if problem:
            journal["problem"] = problem
        if not written:
            _write_new(jpath, journal)
            written = True
        return journal

    prices = {op["field"]: op["after"] for op in ops if op["kind"] == "listing_price"}
    bodies = {
        "base": lambda: {"base-price": int(prices["base"])},
        "minmax": lambda: {**({"min-price": prices["min"]} if "min" in prices else {}),
                           **({"max-price": prices["max"]} if "max" in prices else {})},
        "minstay": lambda: {"min-stay": ms_op["after"]},
        "overrides": lambda: {"overrides": [_override_row(op) for op in over_ops]},
    }
    try:
        # 3. send, one call at a time, each journalled BEFORE it goes out: a call that errored
        #    may still have landed, so anything attempted is re-read, never assumed.
        send_error = None
        try:
            for step in steps:
                suffix, rtype = STEP[step]
                attrs = bodies[step]()
                journal["sent"].append({"call": f"PATCH {suffix}",
                                        "fields": sorted(attrs) if step != "overrides"
                                        else [op["date"] for op in over_ops]})
                resp = live.client.request("PATCH", live.base + suffix, body=_doc(rtype, live.lid, attrs))
                data = resp.get("data") if isinstance(resp, dict) else None
                if isinstance(resp, dict) and resp.get("errors"):
                    raise CannotWrite(f"Beyond reported an error on PATCH {suffix}")
                if not isinstance(data, dict) or data.get("type") != rtype:
                    journal["response_note"] = f"Beyond's reply to PATCH {suffix} was unreadable"
                    raise CannotWrite(journal["response_note"])
        except CannotWrite as exc:
            send_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - after a send nothing may escape unjournalled
            send_error = f"unexpected {type(exc).__name__} while sending"
        if not journal["sent"]:
            finish("failed-before-send", send_error)
            raise CannotWrite(f"{send_error}. Status: failed-before-send; nothing left this machine. "
                              f"The rollback snapshot is {snap.name}.")
        journal["applied_at"] = now.isoformat(timespec="seconds")

        # 4. re-read and prove it, field by field, including every field that should NOT move
        problems, reread_error = [], None
        try:
            after = live.listing()
            if after["currency"] != listing["currency"]:
                problems.append(f"currency is {after['currency']} live, expected {listing['currency']}")
            for f in LISTING_FIELDS:
                want = merged[f]
                ok = _same(after[f], want)
                journal["verification"].append({"field": f, "want": want, "live": after[f], "ok": ok})
                if not ok:
                    problems.append(f"{f} is {_show_money(after[f])} live, expected {_show_money(want)}")
            want_ms = ms_op["after"] if ms_op else listing["min_stay"]
            ok = after["min_stay"] == want_ms
            journal["verification"].append({"field": "min_stay", "want": want_ms,
                                             "live": after["min_stay"], "ok": ok})
            if not ok:
                problems.append(f"min stay is {after['min_stay']} live, expected {want_ms}")
            diff = _rules_diff(listing["raw"], after["raw"])
            journal["verification"].append({"field": "other Beyond rules", "ok": not diff,
                                            "differs_on": diff})
            if diff:
                problems.append("rules nobody asked to change moved: " + ", ".join(diff[:6]))
            if touches_overrides:
                after_over = live.overrides(today)
                wanted = dict(overrides)
                for op in over_ops:
                    if op["after"] is None:
                        wanted.pop(op["date"], None)
                    else:
                        wanted[op["date"]] = op["after"]
                for d in sorted(set(wanted) | set(after_over)):
                    dd = _same_override(after_over.get(d), wanted.get(d))
                    journal["verification"].append({"date": d, "ok": not dd, "differs_on": dd})
                    if dd:
                        problems.append(f"override {d} differs on {', '.join(dd)}")
        except CannotWrite as exc:
            reread_error = f"the re-read after the write failed ({exc})"
        except Exception as exc:  # noqa: BLE001
            reread_error = f"the re-read after the write failed (unexpected {type(exc).__name__})"
        errors = ([send_error] if send_error and not journal.get("response_note") else []) \
            + ([reread_error] if reread_error else [])
        if errors or problems:
            finish("sent-unverified", "; ".join(errors + problems))
            what = "could not be verified" if reread_error else "did not take as approved"
            raise CannotWrite(f"The write was SENT but {what}: " + "; ".join(errors + problems)
                              + f". Read the listing in Beyond. Journal: {jpath}. Undo with: {undo}")
        return finish("verified")
    finally:
        if not written:  # any other way out (a crash, an interrupt) still leaves the journal
            finish("sent-unverified" if journal["sent"] else "failed-before-send",
                   journal.get("problem") or "stopped before the result was known")


def apply_batch(envelopes: list, live_for, *, state_dir, on_verified=None, today=None, now=None) -> list:
    """Apply plans in order, stopping at the first that is not verified (later plans are
    reported as not attempted). `live_for(listing_id, pms)` returns a Live, the same callback
    shape as the PriceLabs writer. Before anything is sent, each listing's live min/base/max is
    re-read with the whole batch's after-values laid on top."""
    groups = {}
    for env in envelopes:
        t = env.get("target") or {}
        groups.setdefault((t.get("listing_id"), t.get("pms")), []).append(env)
    for (lid, pms), envs in groups.items():
        if len(envs) > 1 and any(op["kind"] == "listing_price" for e in envs for op in e["operations"]):
            _check_bounds(live_for(lid, pms).listing(), envs)
    journals = []
    for i, env in enumerate(envelopes):
        t = env.get("target") or {}
        try:
            journal = apply_envelope(env, live_for(t.get("listing_id"), t.get("pms")),
                                     state_dir=state_dir, today=today, now=now)
        except CannotWrite as exc:
            rest = [plan_id(e) for e in envelopes[i + 1:]]
            raise CannotWrite(f"plan {plan_id(env)} ({env.get('listing_name') or t.get('listing_id')}): "
                              f"{exc}" + (f" NOT ATTEMPTED: {', '.join(rest)}." if rest else "")) from None
        journals.append(journal)
        if on_verified:
            on_verified(journal)
    return journals
