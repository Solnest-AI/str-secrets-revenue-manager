"""Smoobu as a PMS source for the runner, and as a calendar write target. Smoobu calls
properties "apartments".

DOCS-ONLY. No Smoobu account was available; nothing here has touched the live API. Every endpoint
is cited in references/smoobu.md (docs.smoobu.com, read 2026-09-25).

AUTH IS HMAC FROM DAY ONE. The legacy single `Api-Key` header is being switched off. Smoobu's
changelog first said "removed on September 25, 2026" (2026-05-29 entry), then on 2026-09-23 moved
the end of support to October 31, 2026; the help centre says "add signing before 30 October 2026".
No legacy header exists anywhere in this module. Every request, reads included, carries four headers:
  X-API-Key    the Key
  X-Timestamp  UTC now, ISO 8601 (2026-04-01T12:00:00Z), within 5 minutes of Smoobu's clock
  X-Nonce      a fresh UUID v4 per physical request; a reused nonce is a 401
  X-Signature  base64(HMAC-SHA256(secret, canonical)), where canonical is
               METHOD \\n PATH \\n QUERY \\n TIMESTAMP \\n NONCE \\n sha256hex(body) \\n API_KEY
               QUERY = the params sorted, keys and values RFC 3986 encoded (space %20, [] %5B%5D);
               body hash of a GET is sha256 of the empty string.
The signed query string is exactly the one sent (built once, see _pms_write_http.encode_query).

What the docs say, and what this module relies on:
  - GET /api/apartments -> {apartments: [{id, name}]}; GET /api/apartments/{id} -> location, timeZone,
    rooms.maxOccupancy/bedrooms/bathrooms, currency, price.minimal/maximal (the detail has no id).
  - GET /api/rates?apartments[]=&start_date=&end_date= (all mandatory) -> data[<apartment id>][<date>]
    = {price (null if none), min_length_of_stay, available (0 = not available)}. No currency: the
    apartment's currency applies. No reason for a closed night: joined from bookings.
  - POST /api/rates {apartments: [id], operations: [{dates: [...], daily_price, min_length_of_stay}]}
    -> {"success": true}. Validation errors come back as HTTP 500. A min stay "can only be set if
    this date has a price or together with price".
  - GET /api/reservations?apartmentId&from&to&page&pageSize(<=100)&excludeBlocked&includePriceElements
    -> page_count, total_items, bookings[]. `type` is reservation | modification of booking |
    cancellation; cancellations are left out unless showCancellation. Room revenue is the
    `basePrice` price element(s); `created-at` carries no timezone.
  - No reviews endpoint. `price.minimal` is website-builder content ("You can create and edit this
    content in the Smoobu website builder"), not an enforced floor, so floor() is None.
  - 700 requests per minute.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation
from _mvp_store import CannotAnalyze
from _pms_write_http import (
    CannotWrite, TargetHTTP, encode_query, inclusive_dates, number, same_currency, validate_changes, whole,
)

HOST = "login.smoobu.com"
BASE = f"https://{HOST}"
UA = "RevenueManager/1.0"
HISTORY_DAYS = 730
PAGE_SIZE = 100  # documented maximum
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
# Smoobu's rates object documents price, min_length_of_stay and available only. No closed-to-
# arrival/departure field exists in its API, so the flags are unknown (None), never assumed False.
# _mvp_pms treats a PMS that exposes the flags on NO night as "rules not exposed" and names that
# gap on the card instead of marking every night unknown.
NO_ARRIVAL_RULES_EXPOSED = None
STATUS = {"reservation": "accepted", "modification of booking": "accepted", "cancellation": "cancelled"}
CHANNELS = {"airbnb": "airbnb", "booking.com": "booking", "vrbo": "vrbo", "homeaway": "vrbo"}


class SmoobuError(CannotAnalyze):
    pass


# ------------------------------------------------------------------------------ HMAC signing

def timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(method: str, path: str, query: str, ts: str, nonce: str, body: bytes, api_key: str) -> str:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return "\n".join([method.upper(), path, query, ts, nonce, body_hash, api_key])


def signature(secret: str, canonical_string: str) -> str:
    mac = hmac.new(secret.encode(), canonical_string.encode(), hashlib.sha256).digest()
    return base64.b64encode(mac).decode()


def signed_headers(api_key: str, secret: str, method: str, path: str, query: str = "", body: bytes = b"",
                   now: datetime | None = None, nonce: str | None = None) -> dict:
    """The four documented headers for ONE physical request. A new nonce every call unless the
    caller pins one (tests only)."""
    ts, nonce = timestamp(now), nonce or str(uuid.uuid4())
    return {"X-API-Key": api_key, "X-Timestamp": ts, "X-Nonce": nonce,
            "X-Signature": signature(secret, canonical(method, path, query, ts, nonce, body, api_key))}


def _keys(connections):
    values = getattr(connections, "values", None) or {}
    key, secret = (values.get("SMOOBU_API_KEY") or "").strip(), (values.get("SMOOBU_API_SECRET") or "").strip()
    return key, secret


# ------------------------------------------------------------------------------ pure mappers

def _cents(value):
    v = number(value)
    return None if v is None else int(Decimal(str(value)).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _currency(value):
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value) else None


def property_row(summary: dict, detail: dict) -> dict:
    currency = _currency(detail.get("currency"))
    if not currency:
        raise SmoobuError("Smoobu apartment has no currency; prices cannot be read safely")
    rooms, loc = detail.get("rooms") or {}, detail.get("location") or {}
    return {
        "id": str(summary["id"]) if summary.get("id") is not None else None, "name": summary.get("name"),
        "public_name": None, "timezone": detail.get("timeZone"), "currency": currency, "listed": None,
        "capacity": {"max": rooms.get("maxOccupancy"), "bedrooms": rooms.get("bedrooms"), "beds": None,
                     "bathrooms": rooms.get("bathrooms")},
        "address": {"city": loc.get("city"), "country": loc.get("country")},
        "listings": [],
    }


def reservation_row(raw: dict, currency: str | None = None) -> dict:
    """Room revenue = the basePrice price element(s) not already included in another element.
    Long-stay discounts and coupons are not netted: their sign is not documented."""
    elements = raw.get("priceElements")
    base = [e for e in elements if isinstance(e, dict) and e.get("type") == "basePrice"
            and e.get("priceIncludedInId") is None] if isinstance(elements, list) else []
    amounts = [_cents(e.get("amount")) for e in base]
    accommodation = sum(amounts) if base and all(a is not None for a in amounts) else None
    codes = {_currency(e.get("currencyCode")) for e in base} - {None}
    arrival, departure = raw.get("arrival"), raw.get("departure")
    try:
        nights = (date.fromisoformat(departure) - date.fromisoformat(arrival)).days
    except (TypeError, ValueError):
        nights = None
    channel = str(((raw.get("channel") or {}).get("name")) or "").strip().lower()
    return {
        "id": str(raw["id"]) if raw.get("id") is not None else None,
        "platform": CHANNELS.get(channel),
        "status": STATUS.get(str(raw.get("type") or "").strip().lower(), "unknown"),
        "check_in": arrival, "check_out": departure, "nights": nights,
        "booking_date": raw.get("created-at"),
        "property_ids": [str((raw.get("apartment") or {}).get("id"))] if (raw.get("apartment") or {}).get("id") is not None else [],
        "financials": {"currency": codes.pop() if len(codes) == 1 else (currency if not codes else None),
                       "host_accommodation_cents": accommodation, "host_discounts": []},
    }


def booked_nights(reservations) -> set:
    nights = set()
    for r in reservations:
        if r.get("status") != "accepted":
            continue
        try:
            d, end = date.fromisoformat(r["check_in"]), date.fromisoformat(r["check_out"])
        except (TypeError, ValueError, KeyError):
            continue
        while d < end:
            nights.add(d.isoformat())
            d += timedelta(days=1)
    return nights


def day_row(d: str, raw: dict, currency: str, booked=frozenset()) -> dict:
    count = whole(raw.get("available"))
    reason = ("AVAILABLE" if count is not None and count > 0 else
              ("RESERVED" if d in booked else "BLOCKED") if count == 0 else "UNKNOWN")
    return {"date": d, "price_cents": _cents(raw.get("price")), "currency": currency,
            "min_stay": whole(raw.get("min_length_of_stay")),
            "available": None if count is None else count > 0, "status_reason": reason,
            "closed_for_checkin": NO_ARRIVAL_RULES_EXPOSED, "closed_for_checkout": NO_ARRIVAL_RULES_EXPOSED}


def rates_for(body, apartment_id) -> dict:
    """data[<apartment id>] -> {date: {...}}. Any other apartment in the reply is refused."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise SmoobuError("Smoobu rates have no data object")
    if set(map(str, data)) - {str(apartment_id)}:
        raise SmoobuError("Smoobu rates belong to another apartment")
    dates = data.get(str(apartment_id))
    if not isinstance(dates, dict):
        raise SmoobuError("Smoobu returned no rates for this apartment")
    return dates


def rates_params(apartment_id, start: date, end: date) -> dict:
    return {"apartments[]": [str(apartment_id)], "start_date": start.isoformat(), "end_date": end.isoformat()}


# ------------------------------------------------------------------------------ read adapter

class SmoobuSource:
    """The four PMS reads the runner needs, same envelope as Hospitable. GET only, through the
    metered ReadClient, each physical attempt signed afresh."""

    def __init__(self, client, connections):
        self.client, self.connections = client, connections
        self._key, self._secret = _keys(connections)
        if not self._key or not self._secret:
            raise SmoobuError("SMOOBU_API_KEY and SMOOBU_API_SECRET are required (Smoobu signs every "
                              "request with HMAC; the legacy single-key header is being switched off)")

    def _get(self, path, params=None, op=None):
        query = encode_query(params) if params else ""
        url = BASE + path + (f"?{query}" if query else "")
        def headers():  # re-run per attempt: a fresh timestamp and nonce every time
            return {**signed_headers(self._key, self._secret, "GET", path, query),
                    "Accept": "application/json", "User-Agent": UA}
        body, _ = self.client.request("smoobu", op or path.strip("/").split("/")[1], url, headers=headers)
        if not isinstance(body, dict):
            raise SmoobuError("Smoobu returned an unreadable body")
        return body

    def _apartments(self):
        raw = self._get("/api/apartments", op="apartments")
        rows = raw.get("apartments")
        if not isinstance(rows, list):
            raise SmoobuError("Smoobu /api/apartments has no apartments list")
        return [r for r in rows if isinstance(r, dict) and r.get("id") is not None]

    def _detail(self, summary):
        detail = self._get(f"/api/apartments/{int(summary['id'])}", op="apartment")
        return normalize_property(property_row(summary, detail))

    def inventory(self):
        rows = [self._detail(s) for s in self._apartments()]
        return {"data": rows, "total": len(rows), "complete": True}

    def property(self, selector):
        def load():
            s = str(selector).casefold()
            hits = [a for a in self._apartments()
                    if str(a["id"]) == str(selector) or str(a.get("name") or "").casefold() == s]
            if len(hits) != 1:
                raise SmoobuError("Property must match exactly one Smoobu apartment by id or name")
            return self._detail(hits[0])
        return self.client.fetch("pms.property", [self.connections.account("smoobu"), selector], load)

    def _bookings(self, pid, start, days, max_pages=200):
        params = {"apartmentId": str(pid), "from": (start - timedelta(days=HISTORY_DAYS)).isoformat(),
                  "to": (start + timedelta(days=max(days, 365))).isoformat(), "pageSize": PAGE_SIZE,
                  "excludeBlocked": True, "includePriceElements": True}
        rows, seen, total = [], set(), None
        for page in range(1, max_pages + 1):
            raw = self._get("/api/reservations", {**params, "page": page}, op="reservations")
            items, pages, count = raw.get("bookings"), raw.get("page_count"), raw.get("total_items")
            if not isinstance(items, list) or whole(pages) is None or whole(count) is None:
                raise SmoobuError("Smoobu /api/reservations has no bookings list or page counts")
            if total is not None and count != total:
                raise SmoobuError("Smoobu bookings changed during pagination; rerun")
            total = count
            for b in items:
                if not isinstance(b, dict):
                    continue
                if str((b.get("apartment") or {}).get("id")) != str(pid):
                    raise SmoobuError("Smoobu returned bookings for another apartment")
                if b.get("id") in seen:
                    raise SmoobuError("Smoobu returned the same booking twice across pages")
                seen.add(b.get("id"))
                rows.append(b)
            if page >= pages or not items:
                return rows, total
        raise SmoobuError("Smoobu /api/reservations pagination exceeds the safety limit")

    def reservations(self, pid, start, days):
        def load():
            currency = self.property(pid)["currency"]
            raw, total = self._bookings(pid, start, days)
            rows = [normalize_reservation(reservation_row(b, currency)) for b in raw
                    if b.get("is-blocked-booking") is not True]  # a block is not a stay
            return {"data": rows, "total": len(rows), "complete": len(raw) == total}
        return self.client.fetch("pms.reservations", [self.connections.account("smoobu"), pid, start.isoformat(), days], load)

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        def load():
            currency = self.property(pid)["currency"]
            # One extra night asked for and trimmed: the docs do not say whether end_date is inclusive.
            dates = rates_for(self._get("/api/rates", rates_params(pid, start, end + timedelta(days=1)), op="rates"), pid)
            booked = booked_nights(self.reservations(pid, start, days)["data"])
            rows = [day_row(d, v, currency, booked) for d, v in dates.items()
                    if isinstance(v, dict) and start.isoformat() <= d <= end.isoformat()]
            return normalize_calendar(rows)
        return self.client.fetch("pms.calendar", [self.connections.account("smoobu"), pid, start.isoformat(), days], load)

    def reviews(self, pid):
        raise SmoobuError("Smoobu's API documents no reviews endpoint; reviews are not read from Smoobu")


# ------------------------------------------------------------------------------ write target

_ID = re.compile(r"^[0-9]{1,20}$")


class SmoobuCalendarTarget:
    """Per-date nightly price and min stay through POST /api/rates, HMAC-signed. DOCS-ONLY: the
    first live write to Smoobu has not happened yet."""

    name = "smoobu"
    host = HOST
    ALLOWED = (
        ("GET", re.compile(r"/api/apartments/[0-9]{1,20}")),
        ("GET", re.compile(r"/api/rates")),
        ("POST", re.compile(r"/api/rates")),
    )
    FLOOR_NOTE = ("Smoobu's price.minimal is website-builder content, not an enforced minimum; "
                  "no enforced per-apartment floor is documented")
    PRICING_TOOL_NOTE = "Smoobu's API does not say whether a pricing tool manages an apartment"

    def __init__(self, connections, opener=None, clock=None):
        key, secret = _keys(connections)
        if not key or not secret:
            raise CannotWrite("Smoobu needs SMOOBU_API_KEY and SMOOBU_API_SECRET (HMAC signing); "
                              "put both in the connector .env")
        self._clock = clock  # tests pin (now, nonce); production signs with the real clock
        def sign(method, path, query, body):
            now, nonce = self._clock() if self._clock else (None, None)
            return {**signed_headers(key, secret, method, path, query, body, now=now, nonce=nonce),
                    "Accept": "application/json", "User-Agent": UA}
        self.http = TargetHTTP("Smoobu", HOST, self.ALLOWED, sign, opener=opener)

    @staticmethod
    def _id(listing_id) -> str:
        lid = str(listing_id)
        if not _ID.match(lid):
            raise CannotWrite(f"Smoobu: {listing_id!r} is not a Smoobu apartment id")
        return lid

    def _currency(self, lid) -> str:
        _, body = self.http.request("GET", f"/api/apartments/{lid}")
        cur = _currency(body.get("currency")) if isinstance(body, dict) else None
        if not cur:
            raise CannotWrite("Smoobu apartment has no readable currency")
        return cur

    def _rates(self, lid, start, end) -> dict:
        _, body = self.http.request("GET", "/api/rates", rates_params(lid, start, end))
        try:
            return rates_for(body, lid)
        except SmoobuError as exc:
            raise CannotWrite(str(exc)) from None

    def read_calendar(self, listing_id: str, start: date, end: date) -> dict:
        lid = self._id(listing_id)
        want = inclusive_dates(start, end)
        currency = self._currency(lid)
        dates = self._rates(lid, start, end + timedelta(days=1))
        days = {}
        for d in want:
            raw = dates.get(d)
            if not isinstance(raw, dict):
                missing = [x for x in want if not isinstance(dates.get(x), dict)]
                raise CannotWrite(f"Smoobu rates are missing {len(missing)} of {len(want)} dates "
                                  f"(first {missing[0]}); refusing a partial read")
            count = whole(raw.get("available"))
            days[d] = {"price": number(raw.get("price")), "min_stay": whole(raw.get("min_length_of_stay")),
                       "available": None if count is None else count > 0}
        return {"currency": currency, "days": days}

    def write_calendar(self, listing_id: str, changes: dict, currency: str) -> None:
        lid = self._id(listing_id)
        ops = validate_changes("Smoobu", changes)
        same_currency("Smoobu", self._currency(lid), currency)
        stay_only = [d for d, price, min_stay in ops if price is None]
        if stay_only:
            live = self._rates(lid, date.fromisoformat(stay_only[0]), date.fromisoformat(stay_only[-1]))
            bare = [d for d in stay_only if number((live.get(d) or {}).get("price")) is None]
            if bare:
                raise CannotWrite(f"Smoobu: {', '.join(bare)} has no nightly price, and Smoobu only takes a "
                                  "min stay on a date that has a price (or together with one). Nothing was "
                                  "sent; include a price for those dates.")
        groups = {}
        for d, price, min_stay in ops:
            groups.setdefault((price, min_stay), []).append(d)
        operations = []
        for (price, min_stay), dates in groups.items():
            op = {"dates": dates}
            if price is not None:
                op["daily_price"] = price
            if min_stay is not None:
                op["min_length_of_stay"] = min_stay
            operations.append(op)
        _, body = self.http.request("POST", "/api/rates", body={"apartments": [int(lid)], "operations": operations})
        if not (isinstance(body, dict) and body.get("success") is True):
            raise CannotWrite("Smoobu POST /api/rates: the reply did not confirm success; re-read before trusting it")

    def floor(self, listing_id: str):
        return None  # FLOOR_NOTE: the core falls back to property_config.settings.min_price

    def pricing_managed(self, listing_id: str):
        return None
