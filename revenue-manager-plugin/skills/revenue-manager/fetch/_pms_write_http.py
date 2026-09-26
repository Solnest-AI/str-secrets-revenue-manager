"""The transport and input checks shared by the PMS calendar write targets that have no MCP of
their own (Uplisting, Smoobu, Hostfully). See docs/WRITE-TARGETS.md for the contract.

TargetHTTP is the ONLY way those targets reach their vendor. It knows one host and a fixed list
of (method, path regex) pairs, and refuses everything else before anything leaves the machine.
No redirects (a key never follows a 3xx off its origin), 60s timeout, a hard call budget, and
NO RETRIES of any kind: a write that failed is reported, never resent behind the operator's back.
Errors name the vendor, method, path and HTTP code only. A vendor response body is never echoed
(it can carry guest data or the request's own credentials).

The query string is built here, once, in RFC 3986 form with the pairs sorted, and that exact
string is both sent and handed to the signer. Smoobu's HMAC scheme signs the sorted, RFC 3986
encoded query, so building it in one place is what keeps the signature and the URL in step.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlsplit

from _mvp_write import CannotWrite

TIMEOUT = 60
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CHANGE_KEYS = {"price", "min_stay"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a 3xx surfaces as an HTTPError; the credential never follows it


def rfc3986(value) -> str:
    """Percent-encode everything but the RFC 3986 unreserved set (space -> %20, [] -> %5B%5D)."""
    return urllib.parse.quote(str(value), safe="-_.~")


def encode_query(params) -> str:
    """`params` is a dict or a list of (key, value) pairs; a list value repeats its key.
    Pairs are encoded, then sorted by (key, value), and joined with '&'."""
    items = params.items() if isinstance(params, dict) else params
    pairs = []
    for key, value in items or ():
        for v in (value if isinstance(value, (list, tuple)) else [value]):
            if v is None:
                continue
            if isinstance(v, bool):
                v = "true" if v else "false"
            pairs.append((rfc3986(key), rfc3986(v)))
    return "&".join(f"{k}={v}" for k, v in sorted(pairs))


class TargetHTTP:
    """One vendor, one host, a closed list of calls. `sign(method, path, query, body_bytes)`
    returns the headers for that one physical request (a fresh nonce each time for Smoobu)."""

    def __init__(self, vendor: str, host: str, allowed, sign, opener=None, max_calls: int = 40):
        self.vendor, self.host, self.allowed, self._sign = vendor, host, tuple(allowed), sign
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.max_calls = max_calls
        self.calls = []

    def permits(self, method: str, path: str) -> bool:
        return (isinstance(path, str) and path.startswith("/") and ".." not in path and "//" not in path
                and any(m == method and rx.fullmatch(path) for m, rx in self.allowed))

    def request(self, method: str, path: str, params=None, body=None):
        if not self.permits(method, path):
            raise CannotWrite(f"The {self.vendor} write transport refuses {method} {path}")
        if len(self.calls) >= self.max_calls:
            raise CannotWrite(f"{self.vendor} HTTP call budget ({self.max_calls}) reached")
        query = encode_query(params) if params else ""
        url = f"https://{self.host}{path}" + (f"?{query}" if query else "")
        if urlsplit(url).netloc != self.host or urlsplit(url).scheme != "https":
            raise CannotWrite(f"The {self.vendor} write transport only talks to {self.host}")
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        headers = dict(self._sign(method, path, query, data or b""))
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        self.calls.append({"method": method, "path": path})
        try:
            with self.opener.open(req, timeout=TIMEOUT) as resp:
                raw, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            exc.close()  # the body can echo the request or guest data; never surface it
            self.calls[-1]["status"] = exc.code
            raise CannotWrite(f"{self.vendor} {method} {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CannotWrite(f"{self.vendor} {method} {path}: no readable response") from None
        self.calls[-1]["status"] = status
        if not raw:
            return status, {}
        try:
            return status, json.loads(raw)
        except ValueError:
            raise CannotWrite(f"{self.vendor} {method} {path}: response is not JSON") from None


# ------------------------------------------------------------------------------ inputs

def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def inclusive_dates(start: date, end: date) -> list:
    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        raise CannotWrite("The calendar window must be two dates, end on or after start")
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def major(value) -> float:
    """Major currency units rounded half-up to the cent, as the vendors take them."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def whole(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def validate_changes(vendor: str, changes, today: date | None = None) -> list:
    """{"YYYY-MM-DD": {"price": float?, "min_stay": int?}} -> sorted [(date, price|None, min_stay|None)].
    Refuses unknown fields, empty changes, bad numbers and any date before today (UTC)."""
    today = today or utc_today()
    if not isinstance(changes, dict) or not changes:
        raise CannotWrite(f"{vendor}: there is nothing to write")
    out = []
    for d in sorted(changes):
        change = changes[d]
        if not isinstance(d, str) or not _DATE.match(d):
            raise CannotWrite(f"{vendor}: {d!r} is not a YYYY-MM-DD date")
        try:
            when = date.fromisoformat(d)
        except ValueError:
            raise CannotWrite(f"{vendor}: {d!r} is not a real date") from None
        if when < today:
            raise CannotWrite(f"{vendor}: {d} is in the past; nothing was sent")
        if not isinstance(change, dict) or not change or set(change) - CHANGE_KEYS:
            raise CannotWrite(f"{vendor}: {d} may only set price and min_stay")
        price, min_stay = change.get("price"), change.get("min_stay")
        if "price" in change:
            price = number(price)
            if price is None or price <= 0:
                raise CannotWrite(f"{vendor}: {d} price must be a number above zero")
            price = major(price)
        if "min_stay" in change and (whole(min_stay) is None or min_stay < 1):
            raise CannotWrite(f"{vendor}: {d} min_stay must be a whole number of nights, 1 or more")
        out.append((d, price, min_stay))
    return out


def same_currency(vendor: str, live, planned) -> str:
    live = live.upper() if isinstance(live, str) and re.fullmatch(r"[A-Za-z]{3}", live) else None
    if live is None:
        raise CannotWrite(f"{vendor}: the listing has no readable currency; nothing was sent")
    if not isinstance(planned, str) or planned.upper() != live:
        raise CannotWrite(f"{vendor}: the listing currency is {live}, the change says {planned!r}; nothing was sent")
    return live
