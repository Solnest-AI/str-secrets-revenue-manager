"""The price writer: plan a PriceLabs price change, show it, apply it on a plain yes, prove it.

PRD D1 as amended 2026-09-23: the operator approves with a plain yes in chat, one change or
one batch shown together. No typed codes, no permission-prompt rules (Ryan: "never code a
project with these restrictions ever again"). D8 (15% flag), S2-S4 unchanged. The shape:

  plan      FRESH read (never cache, S2) -> envelope of operations, each with the live
            before-image and the requested after-image -> plain-language warnings (S4, S7)
            -> saved under its plan id, the content hash of what would be written.
  apply     load the plan by id and REFUSE if the file no longer hashes to that id -> fresh
            read again -> REFUSE if anything the plan saw has moved -> write the rollback
            snapshot BEFORE sending (S3) -> send -> re-read -> check every field it wrote
            AND every field it did not.
  rollback  the reverse change, built from the journal, planned and shown like any other.

What the plan id covers. Target (listing, pms, currency) and every operation's before and
after. NOT the reason text, timestamps or warnings: a rebuilt plan keeps its id, one
changed field gets a new one, and a live value that drifted since the plan is refused.

What it will write, and nothing else (WriteClient refuses the rest):
  POST   /v1/listings                      min / base / max only, never tags, sync or groups
  POST   /v1/listings/{id}/overrides       with update_children false, always
  DELETE /v1/listings/{id}/overrides       with update_children false, always
Reads it needs: GET /v1/listings/{id}, GET /v1/listings/{id}/overrides, POST
/v1/listing_prices (a read that happens to be a POST). Customization rules and nudge
acceptance are separate operations with their own measured traps and are NOT here yet.

Measured-behaviour guards, each one a way PriceLabs returns HTTP 200 while doing the wrong
thing: an `errors` array on the listings response, an error envelope in place of the
listing, a POST that silently does nothing, a write that moves a field nobody asked for, an
override POST that replaces the whole date instead of merging. Every one is caught by the
re-read, not by trusting the response.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from reduce_prices import payload_matches, split_payload

PL_HOST = "api.pricelabs.co"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
VERSION = 1

LISTING_FIELDS = ("min", "base", "max")
# What a person may ask to set on a date. Everything else on an existing override is
# carried forward untouched (and re-verified), never edited by this writer.
OVERRIDE_SETTABLE = {"price", "price_type", "min_stay"}
# Every field POST /v1/listings/{id}/overrides accepts (customer-api.json, 2026-09-23).
# A GET field outside this set cannot be sent back, so a rollback could not restore it.
OVERRIDE_POSTABLE = {"date", "price", "price_type", "currency", "min_stay", "min_price",
                     "min_price_type", "max_price", "max_price_type", "base_price",
                     "check_in_check_out_enabled", "check_in", "check_out", "reason",
                     "lead_time_expiry"}
OVERRIDE_META = {"created_at", "updated_at"}
PRICE_TYPES = {"fixed", "percent", "percent_stacked"}
NUMERIC = {"price", "min_price", "max_price", "base_price", "min_stay", "lead_time_expiry"}
MAX_DELTA = 0.15            # D8
HORIZON_DAYS = 90           # D6
PCT_RANGE = (-75.0, 1000.0)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TOP_KEYS = {"listing_id", "pms", "reason", "listing_prices", "overrides_set", "overrides_delete",
            "overrides_restore"}


class CannotWrite(Exception):
    """Exit 2 upstream: the writer cannot produce a change it can stand behind."""


# ------------------------------------------------------------------------------ transport

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # the API key never follows a redirect off its origin


def _allowed(method: str, path: str) -> bool:
    seg = r"[A-Za-z0-9._:-]+"
    reads = {("GET", rf"/v1/listings/{seg}"), ("GET", rf"/v1/listings/{seg}/overrides"),
             ("POST", r"/v1/listing_prices")}
    writes = {("POST", r"/v1/listings"), ("POST", rf"/v1/listings/{seg}/overrides"),
              ("DELETE", rf"/v1/listings/{seg}/overrides")}
    return any(m == method and re.fullmatch(p, path) for m, p in reads | writes)


class WriteClient:
    """The only transport in this skill that can change a price. It knows exactly six
    calls and refuses everything else, including every other PriceLabs write. No retries:
    a POST that failed is reported, never resent behind the operator's back."""

    def __init__(self, key: str, opener=None, max_calls: int = 30):
        if not key:
            raise CannotWrite("No PriceLabs API key; put PRICELABS_API_KEY in the connector .env")
        self._key = key
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.max_calls = max_calls
        self.calls = []

    def request(self, method: str, path: str, params=None, body=None):
        if not _allowed(method, path) or ".." in path:
            raise CannotWrite(f"The write transport refuses {method} {path}")
        if len(self.calls) >= self.max_calls:
            raise CannotWrite(f"HTTP call budget ({self.max_calls}) reached")
        url = f"https://{PL_HOST}{path}" + ("?" + urlencode(params) if params else "")
        if urlsplit(url).netloc != PL_HOST:
            raise CannotWrite("The write transport only talks to PriceLabs")
        req = urllib.request.Request(
            url, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": self._key, "User-Agent": UA, "Accept": "application/json",
                     "Content-Type": "application/json"})
        self.calls.append({"method": method, "path": path})
        try:
            with self.opener.open(req, timeout=60) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            exc.close()  # the body can echo the request; never surface it
            raise CannotWrite(f"PriceLabs {method} {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CannotWrite(f"PriceLabs {method} {path}: no readable response") from None
        self.calls[-1]["status"] = status
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise CannotWrite(f"PriceLabs {method} {path}: response is not JSON") from None


# ------------------------------------------------------------------------------ live reads

def _num(value, what: str) -> float:
    if isinstance(value, bool) or value is None:
        raise CannotWrite(f"{what} is not a number: {value!r}")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise CannotWrite(f"{what} is not a number: {value!r}") from None
    if not math.isfinite(out):
        raise CannotWrite(f"{what} is not a finite number: {value!r}")
    return out


class Live:
    """Fresh reads for one listing. Nothing here is cached, by construction (S2)."""

    def __init__(self, client: WriteClient, listing_id: str, pms: str):
        for value, what in ((listing_id, "listing_id"), (pms, "pms")):
            if not isinstance(value, str) or not _ID.match(value):
                raise CannotWrite(f"{what} {value!r} is not a plain identifier")
        self.client, self.lid, self.pms = client, listing_id, pms

    def listing(self) -> dict:
        raw = self.client.request("GET", "/v1/listings/" + quote(self.lid), {"pms": self.pms})
        rows = raw.get("listings") if isinstance(raw, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise CannotWrite("PriceLabs did not return exactly this listing")
        item = rows[0]
        if str(item.get("id")) != self.lid or item.get("pms") != self.pms:
            raise CannotWrite("PriceLabs returned a different listing or PMS")
        out = {"name": item.get("name"), "currency": item.get("currency") or None}
        for f in LISTING_FIELDS:
            v = _num(item.get(f), f"live {f}")
            if v <= 0:
                raise CannotWrite(f"live {f} is {v}; refusing to plan against it")
            out[f] = v
        return out

    def overrides(self) -> dict:
        raw = self.client.request("GET", f"/v1/listings/{quote(self.lid)}/overrides",
                                  {"pms": self.pms, "start_date": "2000-01-01"})
        rows = raw.get("overrides") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            raise CannotWrite("Unreadable overrides; absence cannot be assumed")
        out = {}
        for r in rows:
            if not isinstance(r, dict) or not isinstance(r.get("date"), str):
                raise CannotWrite("An override row has no date")
            if r["date"] in out:
                raise CannotWrite(f"PriceLabs returned two overrides for {r['date']}")
            extra = set(r) - OVERRIDE_POSTABLE - OVERRIDE_META
            if extra:
                raise CannotWrite(f"Override {r['date']} carries {sorted(extra)}, which cannot be "
                                  "written back; a rollback could not restore it")
            out[r["date"]] = {k: v for k, v in r.items() if k not in OVERRIDE_META and v is not None}
        return out

    def prices(self, start: date, days: int, currency) -> dict:
        end = start + timedelta(days=days - 1)
        raw = self.client.request("POST", "/v1/listing_prices", body={"listings": [
            {"id": self.lid, "pms": self.pms, "dateFrom": start.isoformat(), "dateTo": end.isoformat()}]})
        if not payload_matches(raw, [(self.lid, self.pms)]):
            raise CannotWrite("PriceLabs price response belongs to another listing")
        env = raw[0] if isinstance(raw, list) else raw
        if currency and env.get("currency") != currency:
            raise CannotWrite("PriceLabs price currency does not match the listing")
        by_id, errors = split_payload(raw)
        if errors or self.lid not in by_id:
            raise CannotWrite("PriceLabs did not return a price calendar")
        out = {}
        for r in by_id[self.lid]:
            try:
                out[str(r["date"])] = _num(r.get("price"), "calendar price")
            except (CannotWrite, KeyError):
                continue
        return out


# ------------------------------------------------------------------------------ hashing

def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def content_hash(envelope: dict) -> str:
    """What the envelope would write: target plus every operation, sorted. Nothing else."""
    t = envelope.get("target") or {}
    ops = sorted(canonical(o).decode() for o in envelope.get("operations") or [])
    return hashlib.sha256(canonical({
        "v": envelope.get("version"),
        "target": {k: t.get(k) for k in ("listing_id", "pms", "currency")},
        "operations": ops})).hexdigest()


def plan_id(envelope: dict) -> str:
    return content_hash(envelope)[:12]


# ------------------------------------------------------------------------------ plan

def _date(value, today: date) -> str:
    if not isinstance(value, str) or not _DATE.match(value):
        raise CannotWrite(f"{value!r} is not a YYYY-MM-DD date")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise CannotWrite(f"{value!r} is not a real date") from None
    if d < today:
        raise CannotWrite(f"{value} is in the past")
    return value


def _price_str(value) -> str:
    v = _num(value, "override price")
    return str(int(v)) if v == int(v) else repr(round(v, 2))


def _same(field: str, a, b) -> bool:
    if field in NUMERIC or field in LISTING_FIELDS:
        try:
            return abs(float(a) - float(b)) < 0.005
        except (TypeError, ValueError):
            return a == b
    return a == b


def _same_override(a, b) -> list:
    """The fields on which two override objects differ (None = no override)."""
    if a is None or b is None:
        return [] if a is b else ["<presence>"]
    return sorted(k for k in set(a) | set(b) if not _same(k, a.get(k), b.get(k)))


def _pct(before: float, after: float) -> float:
    return abs(after - before) / before


def _money(v) -> str:
    return f"{float(v):,.2f}"


def _show(o) -> str:
    """An override in words. Real overrides can carry no price at all (min-stay only)."""
    if o is None:
        return "no override"
    parts = [f"{k}={o[k]}" for k in sorted(o) if k not in ("date", "reason")]
    if "price" not in o:
        parts.insert(0, "no price set")
    return " ".join(parts) + (f' (note: "{o["reason"]}")' if o.get("reason") else "")


def plan_change(change: dict, live: Live, *, today: date | None = None,
                now: datetime | None = None) -> dict:
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    if not isinstance(change, dict):
        raise CannotWrite("The change must be a JSON object")
    unknown = set(change) - TOP_KEYS
    if unknown:
        raise CannotWrite(f"Unsupported change keys {sorted(unknown)}: this writer does listing "
                          "min/base/max and date overrides only")
    if change.get("listing_id") != live.lid or change.get("pms") != live.pms:
        raise CannotWrite("The change names a different listing than the one being read")
    reason = change.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise CannotWrite("Every change needs a reason; it goes in the audit trail")
    prices = change.get("listing_prices") or {}
    o_set = change.get("overrides_set") or []
    o_del = change.get("overrides_delete") or []
    o_res = change.get("overrides_restore") or []
    if not isinstance(prices, dict) or not all(isinstance(x, list) for x in (o_set, o_del, o_res)):
        raise CannotWrite("listing_prices is an object; the overrides_* keys are lists")
    if not (prices or o_set or o_del or o_res):
        raise CannotWrite("The change has nothing to write")

    listing = live.listing()
    currency = listing["currency"]
    ops, warnings = [], []

    # listing min / base / max
    merged = {f: listing[f] for f in LISTING_FIELDS}
    for field, value in prices.items():
        if field not in LISTING_FIELDS:
            raise CannotWrite(f"{field!r} is not a field this writer sets (min, base, max only)")
        after = _num(value, field)
        if after <= 0:
            raise CannotWrite(f"{field} must be above zero")
        after = round(after, 2)
        if _same(field, after, listing[field]):
            raise CannotWrite(f"{field} is already {_money(after)}")
        merged[field] = after
        ops.append({"kind": "listing_price", "field": field, "before": listing[field], "after": after})
        if _pct(listing[field], after) > MAX_DELTA + 1e-9:
            warnings.append(f"OVER 15%: {field} {_money(listing[field])} -> {_money(after)} "
                            f"({(after - listing[field]) / listing[field]:+.1%}). Extra scrutiny (D8).")
    if not merged["min"] <= merged["base"] <= merged["max"]:
        raise CannotWrite(f"After this change min <= base <= max would not hold "
                          f"({merged['min']}, {merged['base']}, {merged['max']})")

    calendar = None
    needs_calendar = (prices.get("min") is not None and merged["min"] > listing["min"]) or \
                     (prices.get("max") is not None and merged["max"] < listing["max"])

    # overrides
    seen = set()
    existing = live.overrides() if (o_set or o_del or o_res) else {}
    for item in o_set:
        if not isinstance(item, dict):
            raise CannotWrite("Each overrides_set entry is an object")
        bad = set(item) - OVERRIDE_SETTABLE - {"date"}
        if bad:
            raise CannotWrite(f"{sorted(bad)} is not a field this writer sets on a date "
                              "(price, price_type, min_stay)")
        d = _date(item.get("date"), today)
        if d in seen:
            raise CannotWrite(f"{d} appears twice in one change")
        seen.add(d)
        before = existing.get(d)
        after = {k: v for k, v in (before or {}).items()}
        after["date"] = d
        if "price" in item or "price_type" in item:
            ptype = item.get("price_type")
            if ptype not in PRICE_TYPES:
                raise CannotWrite(f"price_type must be one of {sorted(PRICE_TYPES)}")
            price = _num(item.get("price"), f"{d} price")
            if ptype == "fixed":
                if price <= 0:
                    raise CannotWrite(f"{d} fixed price must be above zero")
                if not currency:
                    raise CannotWrite("The listing has no currency; a fixed override needs one "
                                      "that exactly matches the PMS")
                after["currency"] = currency
            else:
                if not PCT_RANGE[0] <= price <= PCT_RANGE[1]:
                    raise CannotWrite(f"{d} percent must be between -75 and 1000")
                after.pop("currency", None)
            after["price"], after["price_type"] = _price_str(price), ptype
        if "min_stay" in item:
            ms = item["min_stay"]
            if isinstance(ms, bool) or not isinstance(ms, int) or ms < 1:
                raise CannotWrite(f"{d} min_stay must be a whole number of nights, 1 or more")
            after["min_stay"] = ms
        if not _same_override(before, after):
            raise CannotWrite(f"{d} already has exactly this override")
        ops.append({"kind": "override", "date": d, "before": before, "after": after})
        if before:
            warnings.append(f"REPLACES the existing override on {d} (was {_show(before)}); "
                            "fields not mentioned are carried forward and re-checked.")
        if after.get("price_type") == "fixed":
            needs_calendar = needs_calendar or not (before and before.get("price_type") == "fixed")
        elif abs(float(after.get("price", 0))) > MAX_DELTA * 100:
            warnings.append(f"OVER 15%: {d} override is {after['price']}% on PriceLabs' price (D8).")
    for item in o_res:
        # A rollback puts back a whole override exactly as it was read. Any postable field
        # is allowed, and every one of them is shown on the card and covered by the plan id.
        if not isinstance(item, dict):
            raise CannotWrite("Each overrides_restore entry is an object")
        bad = set(item) - OVERRIDE_POSTABLE
        if bad:
            raise CannotWrite(f"{sorted(bad)} is not a field PriceLabs accepts on an override")
        d = _date(item.get("date"), today)
        if d in seen:
            raise CannotWrite(f"{d} appears twice in one change")
        seen.add(d)
        before, after = existing.get(d), {k: v for k, v in item.items() if v is not None}
        if not _same_override(before, after):
            raise CannotWrite(f"{d} already has exactly this override")
        ops.append({"kind": "override", "date": d, "before": before, "after": after})
        warnings.append(f"RESTORES the override on {d} exactly as it was before.")
    for d in o_del:
        d = _date(d, today)
        if d in seen:
            raise CannotWrite(f"{d} appears twice in one change")
        seen.add(d)
        if d not in existing:
            raise CannotWrite(f"{d} has no override to delete")
        before = existing[d]
        ops.append({"kind": "override", "date": d, "before": before, "after": None})
        warnings.append(f"REMOVES the override on {d} (was {_show(before)}); that night goes "
                        "back to PriceLabs' own price and settings.")

    for op in ops:
        if op["kind"] == "override" and op["before"] and op["after"]:
            dropped = sorted(set(op["before"]) - set(op["after"]))
            if dropped:
                # A merging POST cannot remove a field, so this date is deleted and re-posted.
                op["replace"] = True
                warnings.append(f"{op['date']}: {', '.join(dropped)} is removed, so the override is "
                                "deleted and written fresh in the same step.")

    if needs_calendar:
        calendar = live.prices(today, HORIZON_DAYS, currency)
        if prices.get("min") is not None and merged["min"] > listing["min"]:
            n = sum(1 for p in calendar.values() if p < merged["min"])
            if n:
                warnings.append(f"{n} of the next {HORIZON_DAYS} nights are priced below the new min "
                                f"{_money(merged['min'])} today and will be lifted to it.")
        if prices.get("max") is not None and merged["max"] < listing["max"]:
            n = sum(1 for p in calendar.values() if p > merged["max"])
            if n:
                warnings.append(f"{n} of the next {HORIZON_DAYS} nights are priced above the new max "
                                f"{_money(merged['max'])} today and will be cut to it.")
        for op in ops:
            if op["kind"] != "override" or not op["after"] or op["after"].get("price_type") != "fixed":
                continue
            prev = op["before"]
            ref = float(prev["price"]) if prev and prev.get("price_type") == "fixed" else calendar.get(op["date"])
            new = float(op["after"]["price"])
            if ref is None:
                warnings.append(f"{op['date']}: no current price to compare against; the 15% check "
                                "could not run.")
            elif _pct(ref, new) > MAX_DELTA + 1e-9:
                warnings.append(f"OVER 15%: {op['date']} fixed {_money(new)} vs {_money(ref)} now "
                                f"({(new - ref) / ref:+.1%}). Extra scrutiny (D8).")
    for op in ops:
        if op["kind"] == "override" and op["after"] and op["after"].get("price_type") == "fixed" \
                and op["before"] and op["before"].get("price_type") == "fixed":
            ref, new = float(op["before"]["price"]), float(op["after"]["price"])
            if _pct(ref, new) > MAX_DELTA + 1e-9:
                warnings.append(f"OVER 15%: {op['date']} fixed {_money(new)} vs {_money(ref)} now "
                                f"({(new - ref) / ref:+.1%}). Extra scrutiny (D8).")

    return {
        "version": VERSION,
        "target": {"listing_id": live.lid, "pms": live.pms, "currency": currency},
        "listing_name": listing["name"],
        "reason": reason.strip(),
        "operations": ops,
        "warnings": warnings,
        "read_at": now.isoformat(timespec="seconds"),
        "created_at": now.isoformat(timespec="seconds"),
    }


def describe(envelope: dict) -> str:
    """The plain-language card the operator says yes or no to."""
    t = envelope["target"]
    lines = [f"PROPOSED CHANGE for {envelope.get('listing_name') or t['listing_id']} "
             f"({t['pms']}, {t.get('currency') or 'no currency'})",
             f"Reason: {envelope['reason']}", ""]
    for op in envelope["operations"]:
        if op["kind"] == "listing_price":
            lines.append(f"  {op['field']:>4}: {_money(op['before'])} -> {_money(op['after'])}")
        else:
            lines.append(f"  {op['date']}: {_show(op['before'])}  ->  {_show(op['after'])}")
    if envelope["warnings"]:
        lines += ["", "READ BEFORE SAYING YES:"] + [f"  ! {w}" for w in envelope["warnings"]]
    lines += ["", f"Plan {plan_id(envelope)}"]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ files

def _write_new(path: Path, data) -> Path:
    """Atomic create that never overwrites: temp file, fsync, then link into place."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, path)  # fails if the destination exists
    finally:
        tmp.unlink(missing_ok=True)
    return path


def save_envelope(envelope: dict, state_dir) -> str:
    h = content_hash(envelope)[:12]
    path = Path(state_dir) / "plans" / f"{h}.json"
    if path.exists():
        path.unlink()  # same hash = same operations; the newer read_at replaces it
    return str(_write_new(path, envelope))


def load_envelope(path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise CannotWrite(f"Cannot read the saved plan {Path(path).name}") from None


def load_plan(state_dir, pid: str) -> dict:
    """The saved plan with this id, refused if its contents no longer hash to the id: what
    gets applied is exactly what was shown."""
    pid = str(pid or "").strip()
    if not re.fullmatch(r"[0-9a-f]{12}", pid):
        raise CannotWrite(f"{pid!r} is not a plan id (12 hex characters)")
    path = Path(state_dir) / "plans" / f"{pid}.json"
    if not path.is_file():
        raise CannotWrite(f"There is no saved plan {pid}; run plan again")
    env = load_envelope(path)
    if plan_id(env) != pid:
        raise CannotWrite(f"Saved plan {pid} was edited after it was shown. Nothing was sent; "
                          "plan again.")
    return env


# ------------------------------------------------------------------------------ apply

def rollback_change(journal_or_envelope: dict) -> dict:
    """The change that puts every field back to its before-image."""
    env = journal_or_envelope.get("envelope", journal_or_envelope)
    t = env["target"]
    out = {"listing_id": t["listing_id"], "pms": t["pms"],
           "reason": f"ROLLBACK of plan {plan_id(env)}: {env.get('reason', '')}".strip()}
    prices = {op["field"]: op["before"] for op in env["operations"] if op["kind"] == "listing_price"}
    restore, delete = [], []
    for op in env["operations"]:
        if op["kind"] != "override":
            continue
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


def apply_envelope(envelope: dict, live: Live, *, state_dir, today: date | None = None,
                   now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    t = envelope["target"]
    if (t["listing_id"], t["pms"]) != (live.lid, live.pms):
        raise CannotWrite("The plan is for a different listing than the one connected")
    h12 = content_hash(envelope)[:12]
    state = Path(state_dir)

    # 1. fresh read; everything the plan saw must still be true
    listing = live.listing()
    touches_overrides = any(op["kind"] == "override" for op in envelope["operations"])
    overrides = live.overrides() if touches_overrides else {}
    if (listing["currency"] or None) != t.get("currency"):
        raise CannotWrite("The listing currency changed since the plan. Nothing was sent; plan again.")
    for op in envelope["operations"]:
        if op["kind"] == "listing_price":
            if not _same(op["field"], listing[op["field"]], op["before"]):
                raise CannotWrite(f"{op['field']} changed since the plan ({_money(op['before'])} -> "
                                  f"{_money(listing[op['field']])} live). Nothing was sent; plan again.")
        elif _same_override(overrides.get(op["date"]), op["before"]):
            raise CannotWrite(f"The override on {op['date']} changed since the plan. Nothing was "
                              "sent; plan again.")

    # 2. rollback snapshot, on disk before anything leaves the machine (S3)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    snap = _write_new(state / "snapshots" / f"{stamp}-{h12}.json", rollback_change(envelope))
    journal = {"envelope": envelope, "plan_id": plan_id(envelope), "status": "failed-before-send",
               "snapshot_path": str(snap), "sent": [], "verification": [], "applied_at": None}
    jpath = state / "journal" / f"{stamp}-{h12}.json"

    def finish(status, problem=None):
        journal["status"] = status
        journal["journal_path"] = str(jpath)
        if problem:
            journal["problem"] = problem
        _write_new(jpath, journal)
        return journal

    # 3. send
    try:
        prices = {op["field"]: op["after"] for op in envelope["operations"] if op["kind"] == "listing_price"}
        if prices:
            body = {"listings": [{"id": live.lid, "pms": live.pms, **prices}]}
            resp = live.client.request("POST", "/v1/listings", body=body)
            journal["sent"].append({"call": "POST /v1/listings", "fields": sorted(prices)})
            rows = resp.get("listings") if isinstance(resp, dict) else None
            if not isinstance(rows, list) or len(rows) != 1:
                raise CannotWrite("PriceLabs did not confirm the listing update")
            if rows[0].get("errors"):
                raise CannotWrite("PriceLabs reported errors on the listing update: "
                                  + "; ".join(map(str, rows[0]["errors"]))[:300])
        over_ops = [op for op in envelope["operations"] if op["kind"] == "override"]
        dels = [op["date"] for op in over_ops if op["after"] is None or op.get("replace")]
        if dels:
            live.client.request("DELETE", f"/v1/listings/{quote(live.lid)}/overrides",
                                body={"pms": live.pms, "update_children": False,
                                      "overrides": [{"date": d} for d in dels]})
            journal["sent"].append({"call": "DELETE overrides", "dates": dels})
        sets = [op["after"] for op in over_ops if op["after"]]
        if sets:
            live.client.request("POST", f"/v1/listings/{quote(live.lid)}/overrides",
                                body={"pms": live.pms, "update_children": False, "overrides": sets})
            journal["sent"].append({"call": "POST overrides", "dates": [o["date"] for o in sets]})
    except CannotWrite as exc:
        status = "sent-unverified" if journal["sent"] else "failed-before-send"
        finish(status, str(exc))
        raise CannotWrite(f"{exc}. Status: {status}. The rollback snapshot is {snap.name}; "
                          "re-read the listing, and if anything moved run rollback on this journal "
                          f"({jpath.name}).") from None
    journal["applied_at"] = now.isoformat(timespec="seconds")

    # 4. re-read and prove it, field by field, including every field that should NOT move
    problems = []
    after_listing = live.listing()
    for f in LISTING_FIELDS:
        want = prices.get(f, listing[f])
        ok = _same(f, after_listing[f], want)
        journal["verification"].append({"field": f, "want": want, "live": after_listing[f], "ok": ok})
        if not ok:
            problems.append(f"{f} is {_money(after_listing[f])} live, expected {_money(want)}")
    if touches_overrides:
        after_over = live.overrides()
        wanted = dict(overrides)
        for op in envelope["operations"]:
            if op["kind"] == "override":
                if op["after"] is None:
                    wanted.pop(op["date"], None)
                else:
                    wanted[op["date"]] = op["after"]
        for d in sorted(set(wanted) | set(after_over)):
            diff = _same_override(after_over.get(d), wanted.get(d))
            journal["verification"].append({"date": d, "ok": not diff, "differs_on": diff})
            if diff:
                problems.append(f"override {d} differs on {', '.join(diff)}")
    if problems:
        finish("sent-unverified", "; ".join(problems))
        raise CannotWrite("The write did not take as approved: " + "; ".join(problems)
                          + f". Journal {jpath.name}; run rollback on it after reading the live listing.")
    return finish("verified")


def apply_batch(envelopes: list, live_for, *, state_dir, on_verified=None, today=None, now=None) -> list:
    """Apply plans in order, stopping at the first one that is not verified. One yes can
    cover a batch, but a failure never lets the rest ride on it: later plans are reported
    as not attempted, and the caller decides again with the facts in front of them."""
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
