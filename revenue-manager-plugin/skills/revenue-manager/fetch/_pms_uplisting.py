"""Uplisting (part of AirDNA) as a PMS source for the runner, and as a calendar write target.

DOCS-ONLY. No Uplisting account was available, so nothing here has touched the live API. Every
endpoint is cited in references/uplisting.md (Postman collection 1320372/SWTBfdW6, read
2026-09-25). What the docs say, and what this module relies on:
  - Auth: `Authorization: Basic <base64 of the API key ALONE>` (no `key:` colon) and
    `Content-Type: application/json` on every call. Host https://connect.uplisting.io.
  - GET /properties and /properties/:id are JSON:API (`data` + `included`); the address sits in
    `included` (type addresses). No Airbnb listing id and no active/listed flag are documented,
    so `listings` is [] and `listed` is unknown. No pagination is documented on /properties: a
    body that signals a next page is refused rather than silently read as the whole account.
  - GET /bookings/:id?from&to&page: 50 per page, `page` is 0-based, `meta.total` and
    `meta.total_pages`. ALL bookings come back, cancelled included. Money is
    `accomodation_total` (Uplisting's spelling) in the property currency. No guest field is
    ever read past this module.
  - GET /calendar/:id?from&to: up to 12 months; per day `available` (bool), `day_rate` (property
    currency, whole units), `minimum_length_of_stay`, `closed_for_arrival/departure`. It does
    NOT say why a night is unavailable, so a closed night inside a live booking is RESERVED and
    any other closed night is BLOCKED (joined from /bookings, never guessed).
  - POST /calendar/:id with {"calendar": {"days": [{date, day_rate, minimum_length_of_stay}]}}
    answers 202 + request_id and applies ASYNCHRONOUSLY, "typically less than 1 minute". A
    re-read straight after the 202 can still show the before-values. `day_rate` is the
    commission-free base rate; Uplisting adds each channel's markup when it syncs.
  - No reviews endpoint and no per-listing minimum price in the REST API.
  - Limits: 5 req/s and 100 req/min per IP, 15 req/min per property.
"""

from __future__ import annotations

import base64
import re
import urllib.parse
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation
from _mvp_store import CannotAnalyze
from _pms_write_http import (
    CannotWrite, TargetHTTP, inclusive_dates, number, same_currency, validate_changes, whole,
)

HOST = "connect.uplisting.io"
BASE = f"https://{HOST}"
UA = "RevenueManager/1.0"
HISTORY_DAYS = 730      # bookings from two years back cover same-time-last-year pace
STATUS = {"confirmed": "accepted", "checked_in": "accepted", "checked_out": "accepted",
          "needs_check_in": "accepted", "needs_check_out": "accepted", "cancelled": "cancelled"}
CHANNELS = {"airbnb_official": "airbnb", "airbnb": "airbnb", "booking_dot_com": "booking",
            "home_away": "vrbo", "vrbo": "vrbo", "uplisting": "direct", "direct": "direct"}


class UplistingError(CannotAnalyze):
    pass


def auth_header(key: str) -> str:
    """Base64 of the key alone. Uplisting: "Encode the key on its own, not in the usual
    key:password format", and a trailing newline breaks it."""
    return "Basic " + base64.b64encode(key.strip().encode()).decode()


def _cents(value):
    v = number(value)
    return None if v is None else int(Decimal(str(value)).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _currency(value):
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value) else None


# ------------------------------------------------------------------------------ pure mappers

def property_row(resource: dict, included=()) -> dict:
    attrs = resource.get("attributes") or {}
    currency = _currency(attrs.get("currency"))
    if not currency:
        raise UplistingError("Uplisting property has no currency; prices cannot be read safely")
    ref = ((resource.get("relationships") or {}).get("address") or {}).get("data") or {}
    address = next((i.get("attributes") or {} for i in included or ()
                    if isinstance(i, dict) and i.get("type") == "addresses" and str(i.get("id")) == str(ref.get("id"))), {})
    return {
        "id": str(resource["id"]) if resource.get("id") is not None else None,
        "name": attrs.get("nickname") or attrs.get("name"), "public_name": attrs.get("name"),
        "timezone": attrs.get("time_zone"), "currency": currency, "listed": None,
        "capacity": {"max": attrs.get("maximum_capacity"), "bedrooms": attrs.get("bedrooms"),
                     "beds": attrs.get("beds"), "bathrooms": attrs.get("bathrooms")},
        "address": {"city": address.get("city"), "country": address.get("country")},
        "listings": [],
    }


def reservation_row(raw: dict) -> dict:
    check_in, check_out = raw.get("check_in"), raw.get("check_out")
    return {
        "id": str(raw["id"]) if raw.get("id") is not None else None,
        "platform": CHANNELS.get(str(raw.get("channel") or "").lower()),
        "status": STATUS.get(str(raw.get("status") or "").lower(), "unknown"),
        "check_in": check_in, "check_out": check_out, "nights": whole(raw.get("number_of_nights")),
        "booking_date": raw.get("booked_at"),
        "property_ids": [str(raw["property_id"])] if raw.get("property_id") is not None else [],
        "financials": {"currency": raw.get("currency"),
                       "host_accommodation_cents": _cents(raw.get("accomodation_total")),
                       "host_discounts": []},
    }


def booked_nights(reservations) -> set:
    """Nights held by a live (not cancelled) booking: the only way to tell RESERVED from BLOCKED."""
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


def day_row(raw: dict, currency: str, booked=frozenset()) -> dict:
    available = raw.get("available")
    reason = ("AVAILABLE" if available is True else
              ("RESERVED" if raw.get("date") in booked else "BLOCKED") if available is False else "UNKNOWN")
    min_stay = whole(raw.get("minimum_length_of_stay"))
    return {
        "date": raw.get("date"), "price_cents": _cents(raw.get("day_rate")), "currency": currency,
        "min_stay": min_stay, "available": available if isinstance(available, bool) else None,
        "status_reason": reason,
        "closed_for_checkin": raw.get("closed_for_arrival") if isinstance(raw.get("closed_for_arrival"), bool) else None,
        "closed_for_checkout": raw.get("closed_for_departure") if isinstance(raw.get("closed_for_departure"), bool) else None,
    }


def calendar_days(body) -> list:
    rows = ((body or {}).get("calendar") or {}).get("days") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise UplistingError("Uplisting calendar has no days list")
    return [r for r in rows if isinstance(r, dict)]


# ------------------------------------------------------------------------------ read adapter

class UplistingSource:
    """The four PMS reads the runner needs, in the same envelope as Hospitable. GET only,
    through the metered ReadClient."""

    def __init__(self, client, connections):
        self.client, self.connections = client, connections
        key = connections.values.get("UPLISTING_API_KEY")
        if not key:
            raise UplistingError("UPLISTING_API_KEY is required")
        self._auth = auth_header(key)

    def _get(self, path, params=None, op=None):
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body, _ = self.client.request("uplisting", op or path.strip("/").split("/")[0], url, headers={
            "Authorization": self._auth, "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": UA})
        if not isinstance(body, dict):
            raise UplistingError("Uplisting returned an unreadable body")
        return body

    def _properties(self):
        raw = self._get("/properties", op="properties")
        data = raw.get("data")
        if not isinstance(data, list):
            raise UplistingError("Uplisting /properties has no data list")
        meta, links = raw.get("meta") or {}, raw.get("links") or {}
        if links.get("next") or (isinstance(meta.get("total_pages"), int) and meta["total_pages"] > 1):
            raise UplistingError("Uplisting /properties is paginated, which its docs do not describe; "
                                 "refusing to read one page as the whole account")
        included = raw.get("included") if isinstance(raw.get("included"), list) else []
        return [normalize_property(property_row(r, included)) for r in data if isinstance(r, dict)]

    def inventory(self):
        rows = self._properties()
        return {"data": rows, "total": len(rows), "complete": True}

    def property(self, selector):
        def load():
            s = str(selector).casefold()
            hits = [r for r in self._properties()
                    if r["id"] == str(selector) or s in {str(r.get("name") or "").casefold(),
                                                          str(r.get("public_name") or "").casefold()}]
            if len(hits) != 1:
                raise UplistingError("Property must match exactly one Uplisting property by id or name")
            return hits[0]
        return self.client.fetch("pms.property", [self.connections.account("uplisting"), selector], load)

    def _bookings(self, pid, start, days, max_pages=200):
        params = {"from": (start - timedelta(days=HISTORY_DAYS)).isoformat(),
                  "to": (start + timedelta(days=max(days, 365))).isoformat()}
        rows, seen, total = [], set(), None
        for page in range(max_pages):
            raw = self._get(f"/bookings/{urllib.parse.quote(str(pid))}", {**params, "page": page}, op="bookings")
            items, meta = raw.get("bookings"), raw.get("meta")
            if not isinstance(items, list) or not isinstance(meta, dict):
                raise UplistingError("Uplisting /bookings has no bookings list or meta")
            pages, count = meta.get("total_pages"), meta.get("total")
            if whole(pages) is None or whole(count) is None:
                raise UplistingError("Uplisting /bookings pagination metadata is unreadable")
            if total is not None and count != total:
                raise UplistingError("Uplisting bookings changed during pagination; rerun")
            total = count
            for b in items:
                if not isinstance(b, dict):
                    continue
                if str(b.get("property_id")) != str(pid):
                    raise UplistingError("Uplisting returned bookings for another property")
                if b.get("id") in seen:
                    raise UplistingError("Uplisting returned the same booking twice across pages")
                seen.add(b.get("id"))
                rows.append(b)
            if page + 1 >= pages or not items:
                return rows, total
        raise UplistingError("Uplisting /bookings pagination exceeds the safety limit")

    def reservations(self, pid, start, days):
        def load():
            raw, total = self._bookings(pid, start, days)
            rows = [normalize_reservation(reservation_row(b)) for b in raw]
            return {"data": rows, "total": len(rows), "complete": len(raw) == total}
        return self.client.fetch("pms.reservations", [self.connections.account("uplisting"), pid, start.isoformat(), days], load)

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        def load():
            prop = self.property(pid)
            # One extra night asked for and trimmed: the docs do not say whether `to` is inclusive.
            raw = self._get(f"/calendar/{urllib.parse.quote(str(pid))}",
                            {"from": start.isoformat(), "to": (end + timedelta(days=1)).isoformat()}, op="calendar")
            booked = booked_nights(self.reservations(pid, start, days)["data"])
            rows = [day_row(r, prop["currency"], booked) for r in calendar_days(raw)
                    if isinstance(r.get("date"), str) and start.isoformat() <= r["date"] <= end.isoformat()]
            return normalize_calendar(rows)
        return self.client.fetch("pms.calendar", [self.connections.account("uplisting"), pid, start.isoformat(), days], load)

    def reviews(self, pid):
        # Unreadable is not zero: raising lets the runner degrade the reviews spoke honestly.
        raise UplistingError("Uplisting's REST API documents no reviews endpoint (only its hosted MCP "
                             "has reviews:read); reviews are not read from Uplisting")


# ------------------------------------------------------------------------------ write target

_ID = re.compile(r"^[0-9]{1,20}$")


class UplistingCalendarTarget:
    """Per-date nightly price and min stay, straight to Uplisting's calendar. DOCS-ONLY: the first
    live write to Uplisting has not happened yet."""

    name = "uplisting"
    host = HOST
    ALLOWED = (
        ("GET", re.compile(r"/properties/[0-9]{1,20}")),
        ("GET", re.compile(r"/calendar/[0-9]{1,20}")),
        ("POST", re.compile(r"/calendar/[0-9]{1,20}")),
    )
    # The 202 means "queued", not "applied": the core should wait this long before its re-read,
    # and a re-read that still shows the before-values inside the window is not yet a failure.
    APPLIES_ASYNC = True
    SETTLE_SECONDS = 60
    FLOOR_NOTE = "Uplisting's API documents no per-listing minimum price"
    PRICING_TOOL_NOTE = "Uplisting's API does not say whether a pricing tool manages a listing"
    MAX_WINDOW_DAYS = 365

    def __init__(self, connections, opener=None):
        key = (getattr(connections, "values", None) or {}).get("UPLISTING_API_KEY")
        if not key:
            raise CannotWrite("No Uplisting API key; put UPLISTING_API_KEY in the connector .env")
        auth = auth_header(key)
        self.http = TargetHTTP("Uplisting", HOST, self.ALLOWED, lambda m, p, q, b: {
            "Authorization": auth, "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": UA}, opener=opener)
        self.last_request_id = None

    @staticmethod
    def _id(listing_id) -> str:
        lid = str(listing_id)
        if not _ID.match(lid):
            raise CannotWrite(f"Uplisting: {listing_id!r} is not an Uplisting property id")
        return lid

    def _currency(self, lid) -> str:
        _, body = self.http.request("GET", f"/properties/{lid}")
        data = (body or {}).get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or str(data.get("id")) != lid:
            raise CannotWrite("Uplisting did not return exactly this property")
        cur = _currency((data.get("attributes") or {}).get("currency"))
        if not cur:
            raise CannotWrite("Uplisting property has no readable currency")
        return cur

    def read_calendar(self, listing_id: str, start: date, end: date) -> dict:
        lid = self._id(listing_id)
        want = inclusive_dates(start, end)
        if len(want) > self.MAX_WINDOW_DAYS:
            raise CannotWrite("Uplisting returns at most 12 months of calendar per call")
        currency = self._currency(lid)
        _, body = self.http.request("GET", f"/calendar/{lid}",
                                    {"from": start.isoformat(), "to": (end + timedelta(days=1)).isoformat()})
        try:
            rows = calendar_days(body)
        except UplistingError as exc:
            raise CannotWrite(str(exc)) from None
        days = {}
        for r in rows:
            d = r.get("date")
            if d not in want:
                continue
            if d in days:
                raise CannotWrite(f"Uplisting returned {d} twice")
            available = r.get("available")
            days[d] = {"price": number(r.get("day_rate")), "min_stay": whole(r.get("minimum_length_of_stay")),
                       "available": available if isinstance(available, bool) else None}
        missing = [d for d in want if d not in days]
        if missing:
            raise CannotWrite(f"Uplisting calendar is missing {len(missing)} of {len(want)} dates "
                              f"(first {missing[0]}); refusing a partial read")
        return {"currency": currency, "days": days}

    def write_calendar(self, listing_id: str, changes: dict, currency: str) -> None:
        lid = self._id(listing_id)
        ops = validate_changes("Uplisting", changes)
        same_currency("Uplisting", self._currency(lid), currency)
        days = []
        for d, price, min_stay in ops:
            day = {"date": d}
            if price is not None:
                day["day_rate"] = price
            if min_stay is not None:
                day["minimum_length_of_stay"] = min_stay
            days.append(day)  # never `available`: this writer cannot open or close a night
        status, body = self.http.request("POST", f"/calendar/{lid}", body={"calendar": {"days": days}})
        rid = body.get("request_id") if isinstance(body, dict) else None
        if status != 202 or not isinstance(rid, str) or not rid:
            raise CannotWrite(f"Uplisting POST /calendar/{lid}: HTTP {status} without the documented "
                              "202 + request_id; re-read before trusting it")
        self.last_request_id = rid

    def floor(self, listing_id: str):
        return None  # FLOOR_NOTE: the core falls back to property_config.settings.min_price

    def pricing_managed(self, listing_id: str):
        return None  # PRICING_TOOL_NOTE: the core relies on property_config.settings.pricing_tool
