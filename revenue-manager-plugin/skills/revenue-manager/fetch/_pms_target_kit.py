"""Shared pieces for PMS calendar write targets (docs/WRITE-TARGETS.md).

Each target builds its OWN transport from this class with its own host and ALLOWED list; the
class only makes sure every target's door behaves the same way:
  - refuses any (method, path) not in ALLOWED, and any path containing "..";
  - talks to exactly one host, never follows a redirect, 60s timeout, call budget;
  - ONE attempt per call: nothing is retried here, so a write is never resent;
  - an error names the vendor, method, path and HTTP status, never a response body (vendor
    bodies can echo credentials or guest data).
The input checks (validate_changes) are the same for every target so a plan that one target
refuses is refused by all of them for the same reason.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlsplit

from _mvp_write import CannotWrite


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a credential never follows a redirect off its origin


class HTTPStatus(CannotWrite):
    """A refused call, with its status code kept so a target can react to e.g. an auth 403."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class Transport:
    def __init__(self, label, host, allowed, opener=None, max_calls=40):
        self.label, self.host, self.allowed, self.max_calls = label, host, allowed, max_calls
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.calls = []

    def request(self, method, path, *, headers, params=None, json_body=None, form=None):
        if ".." in path or not any(m == method and rx.fullmatch(path) for m, rx in self.allowed):
            raise CannotWrite(f"The {self.label} target refuses {method} {path}")
        if len(self.calls) >= self.max_calls:
            raise CannotWrite(f"{self.label} HTTP call budget ({self.max_calls}) reached")
        url = f"https://{self.host}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        if urlsplit(url).netloc != self.host:
            raise CannotWrite(f"The {self.label} target only talks to {self.host}")
        data = (json.dumps(json_body).encode() if json_body is not None
                else urllib.parse.urlencode(form).encode() if form is not None else None)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        self.calls.append({"method": method, "path": path})
        try:
            with self.opener.open(req, timeout=60) as resp:
                raw, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            exc.close()
            self.calls[-1]["status"] = exc.code
            raise HTTPStatus(f"{self.label} {method} {path}: HTTP {exc.code}", exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CannotWrite(f"{self.label} {method} {path}: no readable response") from None
        self.calls[-1]["status"] = status
        if not raw or not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise CannotWrite(f"{self.label} {method} {path}: response is not JSON") from None


def price_value(value, what):
    """A positive finite price in major units, rounded half-up to the cent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise CannotWrite(f"{what} must be a positive number")
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_changes(changes, max_dates=366) -> list:
    """{'YYYY-MM-DD': {'price'?, 'min_stay'?}} -> [(date, clean_change), ...] sorted, or CannotWrite."""
    if not isinstance(changes, dict) or not changes:
        raise CannotWrite("No calendar changes to write")
    if len(changes) > max_dates:
        raise CannotWrite(f"At most {max_dates} dates per write")
    out = []
    for day, change in changes.items():
        try:
            when = date.fromisoformat(day)
        except (TypeError, ValueError):
            raise CannotWrite(f"Not a date: {day!r}") from None
        if when.isoformat() != day or not isinstance(change, dict) or not change or set(change) - {"price", "min_stay"}:
            raise CannotWrite(f"{day}: only price and min_stay can be written")
        clean = {}
        if "price" in change:
            clean["price"] = price_value(change["price"], f"{day} price")
        if "min_stay" in change:
            n = change["min_stay"]
            if isinstance(n, bool) or not isinstance(n, int) or n < 1:
                raise CannotWrite(f"{day} min_stay must be a whole number of nights, at least 1")
            clean["min_stay"] = n
        out.append((when, clean))
    return sorted(out, key=lambda item: item[0])


def currency_code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise CannotWrite("currency must be a 3-letter code")
    return value
