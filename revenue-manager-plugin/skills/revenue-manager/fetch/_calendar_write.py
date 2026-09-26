"""The PMS calendar writer: plan a nightly price / min-stay change on any PMS calendar, show
it, apply it on a plain yes, prove it, undo it in one step. docs/WRITE-TARGETS.md is the
contract; this module is the core every `<Name>CalendarTarget` plugs into.

The same eight guarantees as the PriceLabs writer (_mvp_write.py), for every vendor:

  1. plan on a FRESH read of the target's calendar (never a cache); before and after per date
  2. never below the floor: target.floor() else property_config `min_price`. With neither, a
     price CUT is refused ("set your min first"); nothing is guessed. Any per-night move over
     max_delta_pct (15% default, D8) is a loud warning, never a block
  3. the plan id is the content hash of what would be written; apply refuses an edited plan,
     a plan older than 24h, or a date that has passed
  4. apply re-reads fresh and refuses the WHOLE batch if any date moved (drift)
  5. the rollback snapshot is on disk before anything is sent; the journal is always written
  6. one send per plan, never retried, never a vendor response body echoed
  7. re-read and compare every field written AND every field on those dates not written
     (price, min stay, availability). An empty or unreadable re-read is not success. Targets
     whose vendor documents a propagation lag (Hospitable: asynchronous; OwnerRez: "can lag")
     are re-READ a few times on a fixed schedule; the write itself is never repeated
  8. one-step undo from the journal or snapshot, skipping dates already back and past dates

A PMS price write is REFUSED when a pricing tool owns the listing (property_config
`pricing_tool`, or the target's own API when it says so): the tool would overwrite it on the
next sync. A min-stay-only change is allowed there with a warning.

Change file shape (one listing per file):
    {"listing_id": "...", "target": "hospitable", "reason": "why, in one line",
     "calendar_set": [{"date": "2026-10-03", "price": 260, "min_stay": 2},
                      {"date": "2026-10-04", "price": 240}]}
Prices are MAJOR units (dollars, not cents); each target converts to its vendor's unit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from _mvp_write import (
    CannotWrite,
    _check_fresh,
    _money,
    _num,
    _over,
    _parse_date,
    _write_new,
    canonical,
    load_envelope,
    max_delta_fraction,
)

VERSION = 1
KIND = "pms_calendar"
FIELDS = ("price", "min_stay")              # what a person may set on a night
READ_FIELDS = ("price", "min_stay", "available")
MAX_DATES = 60        # one plan = one vendor call; Hospitable's schema caps a PUT near 60
HORIZON_DAYS = 366
TOP_KEYS = {"listing_id", "target", "reason", "calendar_set", "calendar_restore", "unrestorable"}
PRICING_TOOLS = {"pricelabs": "PriceLabs", "beyond": "Beyond"}
UA = "Mozilla/5.0 (revenue-manager)"
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# ISO 4217 minor units (shared with the readers). Everything not listed has two decimals.
from _money import THREE_DECIMAL, ZERO_DECIMAL  # noqa: E402


# ------------------------------------------------------------------------------ money units

def currency_decimals(currency: str) -> int:
    cur = str(currency or "").upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise CannotWrite(f"{currency!r} is not a currency code")
    return 0 if cur in ZERO_DECIMAL else 3 if cur in THREE_DECIMAL else 2


def _exact(price, currency: str) -> Decimal:
    """The price as an exact Decimal in major units, refused if the currency cannot carry it
    (JPY 150.5, USD 150.004): rounding a price someone approved is a guess."""
    try:
        d = Decimal(str(_num(price, "price")))
    except InvalidOperation:
        raise CannotWrite(f"{price!r} is not a price") from None
    places = currency_decimals(currency)
    q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if q != d:
        raise CannotWrite(f"{price} cannot be written in {currency.upper()}, which has "
                          f"{places} decimal place{'s' if places != 1 else ''}")
    return q


def to_minor(price, currency: str) -> int:
    """Major units -> the currency's smallest unit (USD 150.25 -> 15025, JPY 15000 -> 15000)."""
    return int(_exact(price, currency).scaleb(currency_decimals(currency)))


def from_minor(amount, currency: str) -> float:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise CannotWrite(f"minor-unit amount {amount!r} is not a whole number")
    return float(Decimal(amount).scaleb(-currency_decimals(currency)))


def _tol(currency: str) -> float:
    return 0.5 / 10 ** currency_decimals(currency)


def _price_eq(a, b, currency: str) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < _tol(currency)


# ------------------------------------------------------------------------------ transport

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a credential never follows a redirect off its origin


class CalendarHTTP:
    """The only way a calendar target talks to its vendor. One host, an explicit list of
    (method, path regex) it may call, no redirects, a 60s timeout, a call budget, and no
    retries: a write that failed is reported, never resent. Every call is recorded BEFORE it
    goes out, so the core can tell "nothing left this machine" from "sent, result unknown".
    Error text names the vendor, method, path and HTTP status only, never a response body."""

    def __init__(self, label: str, host: str, allowed, headers, opener=None, max_calls: int = 30,
                 explain=None):
        self.label, self.host, self.allowed = label, host, tuple(allowed)
        self._headers = headers
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.max_calls = max_calls
        self.explain = explain or {}
        self.calls = []

    def request(self, method: str, path: str, params=None, body=None):
        if ".." in path or not any(m == method and rx.fullmatch(path) for m, rx in self.allowed):
            raise CannotWrite(f"The {self.label} write transport refuses {method} {path}")
        if len(self.calls) >= self.max_calls:
            raise CannotWrite(f"{self.label}: HTTP call budget ({self.max_calls}) reached")
        url = f"https://{self.host}{path}" + ("?" + urlencode(params) if params else "")
        if urlsplit(url).netloc != self.host:
            raise CannotWrite(f"The {self.label} write transport only talks to {self.host}")
        headers = {"User-Agent": UA, "Accept": "application/json",
                   **(self._headers() if callable(self._headers) else self._headers)}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, allow_nan=False).encode()
        req = urllib.request.Request(url, method=method, data=data, headers=headers)
        self.calls.append({"method": method, "path": path})
        try:
            with self.opener.open(req, timeout=60) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            exc.close()  # the body can echo the request or a token; never surface it
            self.calls[-1]["status"] = exc.code
            why = self.explain.get(exc.code)
            raise CannotWrite(f"{self.label} {method} {path}: HTTP {exc.code}"
                              + (f" ({why})" if why else "")) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CannotWrite(f"{self.label} {method} {path}: no readable response") from None
        self.calls[-1]["status"] = status
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None  # a write's reply is never trusted anyway; a read then fails its shape check

    def writes(self, since: int = 0) -> list:
        return [c for c in self.calls[since:] if c["method"] != "GET"]


# ------------------------------------------------------------------------------ reads

def _day(value) -> dict:
    if not isinstance(value, dict):
        raise CannotWrite("A calendar day is unreadable")
    out = {}
    p = value.get("price")
    out["price"] = None if p is None else _num(p, "calendar price")
    ms = value.get("min_stay")
    if ms is not None and (isinstance(ms, bool) or not isinstance(ms, int)):
        raise CannotWrite(f"calendar min stay {ms!r} is not a whole number")
    out["min_stay"] = ms
    av = value.get("available")
    if av is not None and not isinstance(av, bool):
        raise CannotWrite(f"calendar availability {av!r} is not true/false")
    out["available"] = av
    return out


def fresh_calendar(target, listing_id: str, dates: list) -> tuple:
    """(currency, {date: day}) for exactly these dates, read fresh. A date the vendor did not
    return is refused, never assumed."""
    first, last = min(dates), max(dates)
    cal = target.read_calendar(listing_id, date.fromisoformat(first), date.fromisoformat(last))
    if not isinstance(cal, dict) or not isinstance(cal.get("days"), dict):
        raise CannotWrite(f"{_label(target)} returned no readable calendar")
    currency = cal.get("currency")
    currency_decimals(currency)  # refuses anything that is not a 3-letter code
    days = {}
    for d in dates:
        if d not in cal["days"]:
            raise CannotWrite(f"{d} did not come back in the fresh {_label(target)} read; "
                              "nothing can be planned or checked against it")
        days[d] = _day(cal["days"][d])
    return currency.upper(), days


def _label(target) -> str:
    return getattr(target, "label", None) or str(getattr(target, "name", "PMS")).title()


# ------------------------------------------------------------------------------ policy

def managed_message(tool: str) -> str:
    label = PRICING_TOOLS.get(str(tool).lower(), str(tool))
    return (f"{label} sets this listing's prices, so a PMS change would be overwritten on the next "
            f"sync. Change it in {label} instead.")


def pricing_tool(target, listing_id: str, settings: dict):
    """(tool, where it was learned) or (None, None). property_config first, then the live API."""
    s = (settings or {}).get("pricing_tool")
    if s:
        return str(s).lower(), "property_config"
    live = target.pricing_managed(listing_id) if hasattr(target, "pricing_managed") else None
    if live:
        return str(live).lower(), f"{_label(target)} says so"
    return None, None


def floor_for(target, listing_id: str, settings: dict):
    """(floor in major units, source) or (None, None). The target's own min first."""
    f = target.floor(listing_id) if hasattr(target, "floor") else None
    if f is not None:
        v = _num(f, f"{_label(target)} min price")
        if v <= 0:
            raise CannotWrite(f"{_label(target)} reports a min price of {v}; refusing to plan against it")
        return v, f"{_label(target)} min price"
    s = (settings or {}).get("min_price")
    if s is not None:
        v = _num(s, "property_config min_price")
        if v <= 0:
            raise CannotWrite(f"property_config min_price is {v}; it must be above zero")
        return v, "property_config min_price"
    return None, None


def _blocker(target, listing_id):
    check = getattr(target, "write_blocker", None)
    reason = check(listing_id) if callable(check) else None
    if reason:
        raise CannotWrite(f"{reason} Nothing was planned.")


def _step_ok(target, price: Decimal, currency: str):
    step = getattr(target, "price_step", None)
    if not callable(step):
        return
    s = step(currency)
    if s is None:
        return
    s = Decimal(str(s))
    if s > 0 and price % s != 0:
        raise CannotWrite(f"{_label(target)} takes prices in steps of {s:f} {currency} here; "
                          f"{price:f} cannot be sent as approved")


# ------------------------------------------------------------------------------ hashing

def content_hash(envelope: dict) -> str:
    t = envelope.get("target") or {}
    ops = sorted(canonical(o).decode() for o in envelope.get("operations") or [])
    return hashlib.sha256(canonical({
        "v": envelope.get("version"), "kind": envelope.get("kind"), "undo": bool(envelope.get("undo")),
        "target": {k: t.get(k) for k in ("target", "listing_id", "currency")},
        "operations": ops})).hexdigest()


def plan_id(envelope: dict) -> str:
    return content_hash(envelope)[:12]


# ------------------------------------------------------------------------------ plan

def _items(change: dict, rollback: bool) -> list:
    if change.get("calendar_restore") and not rollback:
        raise CannotWrite("calendar_restore is only built by the undo command (apply_change.py "
                          "rollback --target <name> --journal <file>). A change file uses "
                          "calendar_set.")
    if change.get("unrestorable") and not rollback:
        raise CannotWrite("'unrestorable' is only written by the undo command")
    items = change.get("calendar_restore" if rollback else "calendar_set")
    if rollback and change.get("calendar_set"):
        raise CannotWrite("An undo carries calendar_restore only")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise CannotWrite("calendar_set is a list of {date, price?, min_stay?}")
    return items


def plan_calendar(change: dict, target, settings: dict | None = None, *, today: date | None = None,
                  now: datetime | None = None, max_delta=None, rollback: bool = False) -> dict:
    """rollback=True is set ONLY by the undo path, which builds calendar_restore from a journal
    or snapshot this writer saved. Past dates are dropped and dates already back are skipped,
    both said on the card; a price cut is allowed without a floor there because it puts back a
    value that was live before, and the floor (when one exists) still holds."""
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    settings = settings or {}
    delta = max_delta_fraction(max_delta if max_delta is not None else settings.get("max_delta_pct"))
    over, label = _over(delta), _label(target)
    if not isinstance(change, dict):
        raise CannotWrite("The change must be a JSON object")
    unknown = set(change) - TOP_KEYS
    if unknown:
        raise CannotWrite(f"Unsupported change keys {sorted(unknown)}: a calendar change sets "
                          "price and min_stay per date (calendar_set)")
    if change.get("target", target.name) != target.name:
        raise CannotWrite(f"The change is for {change.get('target')!r} but the writer is {target.name!r}")
    lid = change.get("listing_id")
    if not isinstance(lid, str) or not _ID.match(lid):
        raise CannotWrite(f"listing_id {lid!r} is not a plain identifier")
    reason = change.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise CannotWrite("Every change needs a reason; it goes in the audit trail")

    wanted, past, seen = {}, [], set()
    for item in _items(change, rollback):
        if not isinstance(item, dict):
            raise CannotWrite("Each calendar_set entry is an object")
        bad = set(item) - set(FIELDS) - {"date"}
        if bad:
            raise CannotWrite(f"{sorted(bad)} is not a field this writer sets on a date (price, min_stay)")
        d = item.get("date")
        when = _parse_date(d)
        if d in seen:
            raise CannotWrite(f"{d} appears twice in one change")
        seen.add(d)
        if when < today:
            if rollback:
                past.append(d)
                continue
            raise CannotWrite(f"{d} is in the past")
        if when > today + timedelta(days=HORIZON_DAYS):
            raise CannotWrite(f"{d} is more than {HORIZON_DAYS} days out")
        req = {}
        if "price" in item:
            p = _num(item["price"], f"{d} price")
            if p <= 0:
                raise CannotWrite(f"{d} price must be above zero")
            req["price"] = p
        if "min_stay" in item:
            ms = item["min_stay"]
            if isinstance(ms, bool) or not isinstance(ms, int) or not 1 <= ms <= 365:
                raise CannotWrite(f"{d} min_stay must be a whole number of nights, 1 to 365")
            req["min_stay"] = ms
        if not req:
            raise CannotWrite(f"{d} sets nothing (give a price, a min_stay, or both)")
        wanted[d] = req
    if not wanted:
        if rollback:
            lost = "; ".join(change.get("unrestorable") or [])
            raise CannotWrite((f"Nothing to undo: every date is in the past ({', '.join(sorted(past))})."
                               if past else "Nothing to undo.")
                              + (f" Could not be put back: {lost}." if lost else ""))
        raise CannotWrite("The change has nothing to write")
    if len(wanted) > MAX_DATES:
        raise CannotWrite(f"{len(wanted)} dates in one plan; the limit is {MAX_DATES} (one vendor call). "
                          "Split it into two change files.")

    warnings = []
    prices_asked = any("price" in r for r in wanted.values())
    tool, tool_src = pricing_tool(target, lid, settings)
    if tool and prices_asked:
        raise CannotWrite(managed_message(tool) + f" ({tool_src}.) Nothing was planned.")
    if tool:
        warnings.append(f"{PRICING_TOOLS.get(tool, tool)} manages this listing's prices. If it also "
                        "syncs min stay, it will overwrite this on its next sync.")
    _blocker(target, lid)

    currency, days = fresh_calendar(target, lid, sorted(wanted))
    floor, floor_src = floor_for(target, lid, settings) if prices_asked else (None, None)
    ops, already = [], 0
    for d in sorted(wanted):
        day, req = days[d], wanted[d]
        before = {f: day[f] for f in READ_FIELDS}
        after = {f: day[f] for f in FIELDS}
        if "price" in req:
            exact = _exact(req["price"], currency)
            _step_ok(target, exact, currency)
            after["price"] = float(exact)
        if "min_stay" in req:
            after["min_stay"] = req["min_stay"]
        changed = sorted(f for f in FIELDS if f in req and (
            not _price_eq(after[f], before[f], currency) if f == "price" else after[f] != before[f]))
        if not changed:
            if rollback:
                already += 1
                continue
            raise CannotWrite(f"{d} already has exactly this price and min stay")
        op = {"kind": "night", "date": d, "before": before, "after": after, "set": changed}
        if "price" in changed:
            new, old = after["price"], before["price"]
            if floor is not None and new < floor - _tol(currency):
                raise CannotWrite(f"{d}: {currency} {_money(new)} is below your min of {_money(floor)} "
                                  f"({floor_src}). Nothing was planned.")
            if floor is None and not rollback and (old is None or new < old):
                raise CannotWrite(f"{d}: this listing has no minimum price, so a cut cannot be checked "
                                  "against a floor. Set your min first (setup_properties.py --min-price "
                                  "\"<property>=<amount>\", or the PMS's own min), then plan again. "
                                  "Nothing was planned.")
            if old is None or old <= 0:
                warnings.append(f"{d}: {label} shows no current price, so the {delta * 100:g}% check "
                                "could not run.")
            elif abs(new - old) / old > delta + 1e-9:
                warnings.append(f"{over}: {d} {_money(old)} -> {_money(new)} {currency} "
                                f"({(new - old) / old:+.1%}). Extra scrutiny (D8).")
            if old is None:
                warnings.append(f"{d}: there is no price to put back, so an undo cannot restore this night's price.")
        if "min_stay" in changed and before["min_stay"] is None:
            warnings.append(f"{d}: no min stay is readable now, so an undo cannot put 'none' back.")
        if before["available"] is False:
            warnings.append(f"{d} is booked or blocked now; the change only matters if that night opens.")
        ops.append(op)

    if rollback:
        for note in change.get("unrestorable") or []:
            warnings.append(f"CANNOT PUT BACK: {note}")
        if past:
            warnings.append(f"{len(past)} date(s) are in the past now and were left out of the undo: "
                            f"{', '.join(sorted(past))}.")
        if already:
            warnings.append(f"{already} date{'s' if already != 1 else ''} already back to before; "
                            "left out of the undo.")
        if not ops:
            raise CannotWrite(f"Nothing to undo: {already} date(s) already back to before"
                              + (f", {len(past)} in the past" if past else "") + ".")
    if not getattr(target, "LIVE_WRITE_VERIFIED", False):
        warnings.insert(0, f"first live write for {label}: read the after-values carefully.")
    return {
        "version": VERSION,
        "kind": KIND,
        "undo": bool(rollback),
        "target": {"target": target.name, "listing_id": lid, "currency": currency},
        "reason": reason.strip(),
        "floor": {"value": floor, "source": floor_src},
        "operations": ops,
        "warnings": warnings,
        "read_at": now.isoformat(timespec="seconds"),
        "created_at": now.isoformat(timespec="seconds"),
    }


def _fmt(v, currency) -> str:
    return "none" if v is None else _money(v) if currency_decimals(currency) else f"{float(v):,.0f}"


def describe(envelope: dict, label: str | None = None) -> str:
    """The plain-language card the operator says yes or no to."""
    t = envelope["target"]
    cur = t["currency"]
    label = label or str(t["target"]).title()
    lines = [f"PROPOSED {'UNDO' if envelope.get('undo') else 'CHANGE'} on the {label} calendar for "
             f"listing {t['listing_id']} ({cur})", f"Reason: {envelope['reason']}"]
    fl = envelope.get("floor") or {}
    if any("price" in op["set"] for op in envelope["operations"]):
        lines.append(f"Floor: {cur} {_fmt(fl['value'], cur)} ({fl['source']})" if fl.get("value") is not None
                     else "Floor: none set")
    lines.append("")
    for op in envelope["operations"]:
        b, a = op["before"], op["after"]
        parts = []
        for f, word in (("price", "price"), ("min_stay", "min stay")):
            if f in op["set"]:
                bb, aa = (_fmt(b[f], cur), _fmt(a[f], cur)) if f == "price" else (b[f], a[f])
                move = ""
                if f == "price" and b[f]:
                    move = f" ({(a[f] - b[f]) / b[f]:+.1%})"
                parts.append(f"{word} {bb} -> {aa}{move}")
            else:
                parts.append(f"{word} {_fmt(b[f], cur) if f == 'price' else b[f]} (unchanged)")
        lines.append(f"  {op['date']}: " + ", ".join(parts)
                     + ("" if b.get("available") is not False else "  [booked/blocked]"))
    if envelope["warnings"]:
        lines += ["", "READ BEFORE SAYING YES:"] + [f"  ! {w}" for w in envelope["warnings"]]
    lines += ["", f"Plan {plan_id(envelope)}"]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ files

def save_envelope(envelope: dict, state_dir) -> str:
    path = Path(state_dir) / "plans" / f"{plan_id(envelope)}.json"
    if path.exists():
        path.unlink()  # same hash = same operations; the newer read_at replaces it
    return str(_write_new(path, envelope))


def load_plan(state_dir, pid: str) -> dict:
    pid = str(pid or "").strip()
    if not re.fullmatch(r"[0-9a-f]{12}", pid):
        raise CannotWrite(f"{pid!r} is not a plan id (12 hex characters)")
    path = Path(state_dir) / "plans" / f"{pid}.json"
    if not path.is_file():
        raise CannotWrite(f"There is no saved calendar plan {pid} for this target; run plan again")
    env = load_envelope(path)
    if env.get("kind") != KIND or plan_id(env) != pid:
        raise CannotWrite(f"Saved plan {pid} was edited after it was shown. Nothing was sent; plan again.")
    return env


def rollback_change(obj: dict) -> dict:
    """The change that puts every field a plan set back to its before-value. Accepts a journal,
    an envelope, or a rollback snapshot (which already is this change)."""
    if not isinstance(obj, dict):
        raise CannotWrite("That is not a journal or snapshot this writer saved")
    if "envelope" not in obj and "operations" not in obj:
        if not obj.get("listing_id") or not obj.get("target") or set(obj) - TOP_KEYS \
                or "calendar_restore" not in obj:
            raise CannotWrite("That is not a journal or snapshot this writer saved")
        return copy.deepcopy(obj)
    env = obj.get("envelope", obj)
    if env.get("kind") != KIND:
        raise CannotWrite("That journal belongs to another writer (use the matching --target)")
    t = env["target"]
    restore, lost = [], []
    for op in env["operations"]:
        item = {"date": op["date"]}
        for f in op["set"]:
            if op["before"].get(f) is None:
                lost.append(f"{op['date']} {f.replace('_', ' ')} (there was none before)")
            else:
                item[f] = op["before"][f]
        if len(item) > 1:
            restore.append(item)
    out = {"listing_id": t["listing_id"], "target": t["target"],
           "reason": f"ROLLBACK of plan {plan_id(env)}: {env.get('reason', '')}".strip(),
           "calendar_restore": restore}
    if lost:
        out["unrestorable"] = lost
    return out


# ------------------------------------------------------------------------------ apply

def _verify(days: dict, envelope: dict, currency: str) -> tuple:
    """(rows, problems): every field on every planned date, written or not."""
    rows, problems = [], []
    for op in envelope["operations"]:
        d = op["date"]
        live = days.get(d)
        want = {**op["after"], "available": op["before"]["available"]}
        if live is None:
            rows.append({"date": d, "ok": False, "differs_on": ["<missing>"]})
            problems.append(f"{d} did not come back in the re-read")
            continue
        diff = [f for f in READ_FIELDS if not (
            _price_eq(live[f], want[f], currency) if f == "price" else live[f] == want[f])]
        rows.append({"date": d, "ok": not diff, "differs_on": diff,
                     "live": {f: live[f] for f in READ_FIELDS}})
        if diff:
            problems.append(f"{d} differs on {', '.join(diff)}")
    return rows, problems


POLL_SECONDS = 10


def settle_schedule(target) -> tuple:
    """Seconds to wait before each verification READ after the one send. A target gives either
    an explicit schedule (a tuple, e.g. Hospitable's (0, 5, 15, 30, 60)) or, when it applies
    writes asynchronously (APPLIES_ASYNC), a total in seconds that is polled every 10s with a
    final read at the end (Uplisting: 60 -> reads at 0, 10, ..., 60). Reads only, never a resend."""
    s = getattr(target, "SETTLE_SECONDS", None)
    if isinstance(s, (list, tuple)) and s:
        return tuple(float(x) for x in s)
    if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0:
        polls, rest = divmod(float(s), POLL_SECONDS)
        return (0.0,) + (float(POLL_SECONDS),) * int(polls) + ((rest,) if rest else ())
    return (0.0,)


def _attempted(http, mark: int):
    """True if a non-GET request was attempted since `mark`, False if none was, None if this
    transport's call log cannot tell (then the core assumes it was sent)."""
    calls = getattr(http, "calls", None)
    if not isinstance(calls, list):
        return None
    new = calls[mark:]
    if any(not isinstance(c, dict) or "method" not in c for c in new):
        return None
    return any(str(c["method"]).upper() != "GET" for c in new)


def _reread(target, lid, dates):
    cal = target.read_calendar(lid, date.fromisoformat(min(dates)), date.fromisoformat(max(dates)))
    if not isinstance(cal, dict) or not isinstance(cal.get("days"), dict):
        raise CannotWrite("the re-read returned no calendar")
    return str(cal.get("currency") or "").upper(), {d: _day(v) for d, v in cal["days"].items() if d in dates}


def apply_envelope(envelope: dict, target, settings: dict | None = None, *, state_dir,
                   today: date | None = None, now: datetime | None = None, sleep=time.sleep) -> dict:
    now = now or datetime.now(timezone.utc)
    today = today or date.today()
    settings = settings or {}
    if envelope.get("kind") != KIND:
        raise CannotWrite("That plan is not a PMS calendar plan")
    t = envelope["target"]
    if t["target"] != target.name:
        raise CannotWrite(f"The plan is for {t['target']}, not {target.name}")
    lid, currency = t["listing_id"], t["currency"]
    _check_fresh(envelope, now, today)
    for op in envelope["operations"]:
        if _parse_date(op["date"]) < today:
            raise CannotWrite(f"{op['date']} is in the past now. Nothing was sent; plan again for fresh numbers.")
    h12 = content_hash(envelope)[:12]
    state = Path(state_dir)
    label = _label(target)
    dates = sorted(op["date"] for op in envelope["operations"])

    # 1. the policy again, on fresh settings: a pricing tool or a raised floor since the plan
    price_ops = [op for op in envelope["operations"] if "price" in op["set"]]
    if price_ops:
        tool, src = pricing_tool(target, lid, settings)
        if tool:
            raise CannotWrite(managed_message(tool) + f" ({src}.) Nothing was sent.")
        floor, fsrc = floor_for(target, lid, settings)
        for op in price_ops:
            new, old = op["after"]["price"], op["before"]["price"]
            if floor is not None and new < floor - _tol(currency):
                raise CannotWrite(f"{op['date']}: {_money(new)} is below your min of {_money(floor)} "
                                  f"({fsrc}) now. Nothing was sent; plan again.")
            if floor is None and not envelope.get("undo") and (old is None or new < old):
                raise CannotWrite(f"{op['date']}: this listing has no minimum price now, so the cut "
                                  "cannot be checked. Set your min first. Nothing was sent.")
    _blocker(target, lid)

    # 2. fresh read; every date must be exactly as the plan saw it, or nothing is sent
    live_cur, live = fresh_calendar(target, lid, dates)
    if live_cur != currency:
        raise CannotWrite(f"The listing currency is {live_cur} now, not {currency}. Nothing was sent; plan again.")
    for op in envelope["operations"]:
        d, b, now_d = op["date"], op["before"], live[op["date"]]
        moved = [f for f in READ_FIELDS if not (
            _price_eq(now_d[f], b[f], currency) if f == "price" else now_d[f] == b[f])]
        if moved:
            what = ", ".join(f"{f} {b[f]} -> {now_d[f]}" for f in moved)
            raise CannotWrite(f"{d} changed since the plan ({what} live). Nothing was sent; plan again.")

    # 3. rollback snapshot on disk before anything leaves the machine
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + os.urandom(3).hex()
    snap = _write_new(state / "snapshots" / f"{stamp}-{h12}.json", rollback_change(envelope))
    journal = {"envelope": envelope, "plan_id": plan_id(envelope), "status": "failed-before-send",
               "snapshot_path": str(snap), "sent": [], "http": [], "verification": [],
               "applied_at": None}
    jpath = state / "journal" / f"{stamp}-{h12}.json"
    undo = f"apply_change.py rollback --target {target.name} --journal {jpath.name}"
    journal["undo"] = undo
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

    http = getattr(target, "http", None)
    mark = len(http.calls) if isinstance(getattr(http, "calls", None), list) else 0
    try:
        # 4. send, once. Journalled before it goes out; never retried.
        changes = {op["date"]: {f: op["after"][f] for f in op["set"]} for op in envelope["operations"]}
        attempt = {"call": f"{target.name}.write_calendar", "dates": dates,
                   "fields": {d: sorted(c) for d, c in changes.items()}}
        journal["sent"].append(attempt)
        send_error = None
        try:
            target.write_calendar(lid, changes, currency)
        except CannotWrite as exc:
            send_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - after a send nothing may escape unjournalled
            send_error = f"unexpected {type(exc).__name__} while sending"
        sent = _attempted(http, mark)
        if sent is not None:
            journal["http"] = [dict(c) for c in http.calls[mark:]]
            if sent is False:
                journal["sent"].clear()  # the target refused before any request left
                finish("failed-before-send", send_error or "the target sent nothing")
                raise CannotWrite(f"{send_error or 'the target sent nothing'}. Status: failed-before-send; "
                                  f"nothing left this machine. The rollback snapshot is {snap.name}.")
        journal["applied_at"] = now.isoformat(timespec="seconds")

        # 5. re-read and prove it, on the target's documented settle schedule (reads only)
        rows, problems, reread_error = [], [], None
        # a refused send (HTTP 4xx/5xx) gets one read, not the settle schedule
        waits = (0.0,) if send_error else settle_schedule(target)
        for i, wait in enumerate(waits):
            if wait:
                sleep(wait)
            try:
                cur, days = _reread(target, lid, set(dates))
                reread_error = None
                if cur != currency:
                    rows, problems = [], [f"the currency reads {cur or 'nothing'} now, not {currency}"]
                else:
                    rows, problems = _verify(days, envelope, currency)
            except CannotWrite as exc:
                reread_error = f"the re-read after the write failed ({exc})"
            except Exception as exc:  # noqa: BLE001
                reread_error = f"the re-read after the write failed (unexpected {type(exc).__name__})"
            journal["verification"] = rows
            journal["reads"] = i + 1
            if not reread_error and not problems:
                break
        errors = ([send_error] if send_error else []) + ([reread_error] if reread_error else [])
        if errors or problems:
            finish("sent-unverified", "; ".join(errors + problems))
            check = f"apply_change.py verify --target {target.name} --journal {jpath.name}"
            if not errors and getattr(target, "APPLIES_ASYNC", False):
                raise CannotWrite(f"{label} accepted the change but hadn't applied it after "
                                  f"{sum(waits):g}s ({'; '.join(problems)}). Check again in a minute with "
                                  f"`{check}` (read-only). Journal: {jpath}. Undo with: {undo}")
            what = "could not be verified" if reread_error and not rows else "did not take as approved"
            raise CannotWrite(f"The {label} write was SENT but {what}: " + "; ".join(errors + problems)
                              + f". Read the live calendar in {label}, or re-check with `{check}`. "
                              f"Journal: {jpath}. Undo with: {undo}")
        return finish("verified")
    finally:
        if not written:  # a crash or an interrupt still leaves the journal
            finish("sent-unverified" if journal["sent"] else "failed-before-send",
                   journal.get("problem") or "stopped before the result was known")


def verify_journal(journal: dict, target) -> dict:
    """Re-run the post-send comparison for a journal, READ-ONLY: one fresh read, every field on
    every date the plan touched. Nothing is sent and nothing on disk changes. Returns
    {"ok", "rows", "problems"}; ok is True only when every field matches what was approved."""
    env = journal.get("envelope") if isinstance(journal, dict) else None
    if not isinstance(env, dict) or env.get("kind") != KIND:
        raise CannotWrite("That is not a calendar journal this writer saved")
    t = env["target"]
    if t["target"] != target.name:
        raise CannotWrite(f"That journal is for {t['target']}; use --target {t['target']}")
    dates = {op["date"] for op in env["operations"]}
    cur, days = _reread(target, t["listing_id"], dates)
    if cur != t["currency"]:
        return {"ok": False, "rows": [], "problems": [f"the currency reads {cur or 'nothing'} now, not {t['currency']}"]}
    rows, problems = _verify(days, env, t["currency"])
    return {"ok": not problems, "rows": rows, "problems": problems}


def apply_batch(envelopes: list, target_for, settings_for, *, state_dir, on_verified=None,
                today=None, now=None, sleep=time.sleep) -> list:
    """Apply plans in order, stopping at the first one that is not verified. Later plans are
    reported as not attempted; the operator decides again with the facts in front of them."""
    journals = []
    for i, env in enumerate(envelopes):
        lid = (env.get("target") or {}).get("listing_id")
        try:
            journal = apply_envelope(env, target_for(lid), settings_for(lid), state_dir=state_dir,
                                     today=today, now=now, sleep=sleep)
        except CannotWrite as exc:
            rest = [plan_id(e) for e in envelopes[i + 1:]]
            raise CannotWrite(f"plan {plan_id(env)} (listing {lid}): {exc}"
                              + (f" NOT ATTEMPTED: {', '.join(rest)}." if rest else "")) from None
        journals.append(journal)
        if on_verified:
            on_verified(journal)
    return journals
