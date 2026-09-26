"""Hostfully (API v3.3) as a PMS source for the runner, and as a calendar write target.
Hostfully calls reservations "leads".

DOCS-ONLY. No Hostfully account was available; nothing here has touched the live API. Every
endpoint is cited in references/hostfully.md (dev.hostfully.com OpenAPI pages, read 2026-09-25).
What the docs say, and what this module relies on:
  - Base https://api.hostfully.com/api/v3.3/ ; header `X-HOSTFULLY-APIKEY: <agency key>`.
    A wrong version under /api/ answers 401, not 404, so a 401 can be a bad path, not a bad key.
  - agencyUid is a query parameter on the agency-wide LIST only (GET /properties). Every other
    call here is scoped by propertyUid (leads, orders, reviews, calendar, pricing periods).
  - Pagination is cursor based: `_cursor` in, `_paging._nextCursor` out, `_metadata.totalCount`.
    No maximum `_limit` is documented, so none is sent (the server default is used).
  - GET /property-calendar/{uid}?from&to -> calendar.entries[]: pricing.value + pricing.currency,
    availability.unavailable + unavailabilityReason (BOOKING | INQUIRY | BLOCK_BY_OWNER | BLOCK |
    PROPERTY_AVAILABILITY_SETTINGS | OTHER), minimumStayLength, availableForCheckIn/Out.
  - GET /leads?propertyUid carries no money. Room revenue comes from GET /orders?propertyUid:
    rent.rentNetPrice (no field description in the schema) and rent.rentBreakdowns[] per night.
  - GET/POST /pricing-periods: one period per DATE {propertyUid, date, price, minimumStay,
    availableForCheckIn, availableForCheckOut, name}; POST {operation: SET | REMOVE, pricingPeriod}.
    (The older build brief's startDate/endDate/amount shape is not what v3.3 documents.)
  - Reviews: GET /reviews?propertyUid; `rating` is an integer with no documented scale.
  - The property has `useMinimumPriceRule` (a boolean) but no documented minimum price value.
  - Limits: 10,000 calls an hour per client (the FAQ says 1,000; plan for the lower).
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _mvp_store import CannotAnalyze
from _pms_write_http import (
    CannotWrite, TargetHTTP, inclusive_dates, number, same_currency, validate_changes, whole,
)

HOST = "api.hostfully.com"
PREFIX = "/api/v3.3"
BASE = f"https://{HOST}{PREFIX}"
UA = "RevenueManager/1.0"
HISTORY_DAYS = 730
STATUS = {"BOOKED": "accepted", "CANCELLED": "cancelled", "DECLINED": "not accepted",
          "IGNORED": "not accepted", "CLOSED": "not accepted", "DUPLICATE": "not accepted",
          "ON_HOLD": "request", "PENDING": "request", "PENDING_APPROVED": "request"}
SKIP_STATUS = {"BLOCKED", "SAMPLE"}
CHANNELS = {"AIRBNB": "airbnb", "BOOKING_COM": "booking", "VRBO": "vrbo", "HOSTFULLY": "direct"}
REVIEW_SOURCES = {"AIRBNB": "airbnb", "BOOKING_DOT_COM": "booking", "VRBO": "vrbo", "HOSTFULLY": "direct"}
CATEGORIES = {"ACCURACY": "accuracy", "CLEANLINESS": "cleanliness", "CHECKIN": "checkin",
              "COMMUNICATION": "communication", "LOCATION": "location", "VALUE": "value"}
BLOCKING = {"BLOCK_BY_OWNER", "BLOCK", "PROPERTY_AVAILABILITY_SETTINGS", "OTHER", "INQUIRY"}


class HostfullyError(CannotAnalyze):
    pass


def _cents(value):
    v = number(value)
    return None if v is None else int(Decimal(str(value)).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _currency(value):
    ok = isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value) and value.upper() != "NONE"
    return value.upper() if ok else None


def _utc(value):
    """The field is named ...UtcDateTime; a stamp that carries no offset gets its documented Z."""
    if not isinstance(value, str) or len(value) <= 10:
        return value
    return value if re.search(r"(Z|[+-]\d{2}:?\d{2})$", value) else value + "Z"


# ------------------------------------------------------------------------------ pure mappers

def property_row(raw: dict) -> dict:
    currency = _currency((raw.get("pricing") or {}).get("currency"))
    if not currency:
        raise HostfullyError("Hostfully property has no currency; prices cannot be read safely")
    airbnb = (raw.get("airbnbData") or {}).get("airbnbId")
    addr = raw.get("address") or {}
    return {
        "id": raw.get("uid"), "name": raw.get("name"), "public_name": None, "timezone": raw.get("timeZone"),
        "currency": currency, "listed": raw.get("isActive") if isinstance(raw.get("isActive"), bool) else None,
        "capacity": {"max": (raw.get("availability") or {}).get("maxGuests"), "bedrooms": raw.get("bedrooms"),
                     "beds": raw.get("beds"), "bathrooms": raw.get("bathrooms")},
        "address": {"city": addr.get("city"), "country": addr.get("countryCode")},
        "listings": [{"platform": "airbnb", "platform_id": str(airbnb)}] if airbnb else [],
    }


def day_row(raw: dict, currency: str) -> dict:
    avail, pricing = raw.get("availability") or {}, raw.get("pricing") or {}
    unavailable, why = avail.get("unavailable"), avail.get("unavailabilityReason")
    reason = ("AVAILABLE" if unavailable is False else
              "RESERVED" if unavailable is True and why == "BOOKING" else
              "BLOCKED" if unavailable is True and why in BLOCKING else "UNKNOWN")
    cin, cout = avail.get("availableForCheckIn"), avail.get("availableForCheckOut")
    return {
        "date": raw.get("date"), "price_cents": _cents(pricing.get("value")),
        "currency": _currency(pricing.get("currency")) or currency,
        "min_stay": whole(avail.get("minimumStayLength")),
        "available": (not unavailable) if isinstance(unavailable, bool) else None, "status_reason": reason,
        "closed_for_checkin": (not cin) if isinstance(cin, bool) else None,
        "closed_for_checkout": (not cout) if isinstance(cout, bool) else None,
    }


def lead_status(raw: dict) -> str | None:
    """None = not a stay at all (a block or a sample lead), so it is left out."""
    status, kind = str(raw.get("status") or "").upper(), str(raw.get("type") or "").upper()
    if kind == "BLOCK" or status in SKIP_STATUS:
        return None
    if status == "NEW":
        return "inquiry" if kind == "INQUIRY" else "request"
    return STATUS.get(status, "unknown")


def reservation_row(raw: dict, order: dict | None) -> dict:
    status = lead_status(raw)
    if status is None:
        raise HostfullyError("A block is not a reservation")
    check_in = str(raw.get("checkInLocalDateTime") or raw.get("checkInZonedDateTime") or "")[:10] or None
    check_out = str(raw.get("checkOutLocalDateTime") or raw.get("checkOutZonedDateTime") or "")[:10] or None
    try:
        nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
    except (TypeError, ValueError):
        nights = None
    rent = (order or {}).get("rent") or {}
    breakdown = [{"date": b.get("nightlyDate"), "amount_cents": _cents(b.get("nightlyAmount"))}
                 for b in rent.get("rentBreakdowns") or [] if isinstance(b, dict)]
    meta = raw.get("metadata") or {}
    return {
        "id": raw.get("uid"), "platform": CHANNELS.get(str(raw.get("channel") or "").upper()),
        "status": status, "check_in": check_in, "check_out": check_out, "nights": nights,
        "booking_date": _utc(raw.get("bookedUtcDateTime") or meta.get("createdUtcDateTime")),
        "property_ids": [raw["propertyUid"]] if isinstance(raw.get("propertyUid"), str) else [],
        "financials": {"currency": (order or {}).get("currency"),
                       "host_accommodation_cents": _cents(rent.get("rentNetPrice")),
                       "host_discounts": [], "host_accommodation_breakdown": breakdown},
    }


def review_row(raw: dict) -> dict:
    source = str(raw.get("source") or "").upper()
    rating = raw.get("rating")
    # No scale is documented; only a 1-5 value from a 5-star channel is kept, never mixed in.
    ok = whole(rating) is not None and 1 <= rating <= 5 and source != "BOOKING_DOT_COM"
    return {
        "id": raw.get("uid"), "platform": REVIEW_SOURCES.get(source), "reviewed_at": raw.get("date"),
        "rating": rating if ok else None,
        "detailed_ratings": [{"type": CATEGORIES[c["category"]], "rating": c.get("rate")}
                             for c in raw.get("ratingCategories") or []
                             if ok and isinstance(c, dict) and c.get("category") in CATEGORIES],
    }


def orders_by_lead(orders) -> dict:
    """leadUid -> its one order. A lead with two orders is ambiguous and gets none (value unknown)."""
    out, twice = {}, set()
    for o in orders:
        lead = o.get("leadUid")
        if not isinstance(lead, str):
            continue
        if lead in out:
            twice.add(lead)
        out[lead] = o
    return {k: v for k, v in out.items() if k not in twice}


# ------------------------------------------------------------------------------ read adapter

class HostfullySource:
    """The four PMS reads the runner needs, same envelope as Hospitable. GET only, through the
    metered ReadClient."""

    def __init__(self, client, connections):
        self.client, self.connections = client, connections
        self._key = (connections.values.get("HOSTFULLY_API_KEY") or "").strip()
        self._agency = (connections.values.get("HOSTFULLY_AGENCY_UID") or "").strip()
        if not self._key or not self._agency:
            raise HostfullyError("HOSTFULLY_API_KEY and HOSTFULLY_AGENCY_UID are required")

    def _get(self, path, params=None, op=None):
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body, _ = self.client.request("hostfully", op or path.strip("/").split("/")[0], url, headers={
            "X-HOSTFULLY-APIKEY": self._key, "Accept": "application/json", "User-Agent": UA})
        if not isinstance(body, dict):
            raise HostfullyError("Hostfully returned an unreadable body")
        return body

    def _paged(self, path, params, key, max_pages=200):
        """Cursor pages -> (rows, total or None). Stops when no next cursor comes back."""
        rows, cursor, seen_cursors, total = [], None, set(), None
        for _ in range(max_pages):
            raw = self._get(path, {**params, **({"_cursor": cursor} if cursor else {})})
            page = raw.get(key)
            if not isinstance(page, list):
                raise HostfullyError(f"Hostfully {path} has no {key} list")
            rows += [x for x in page if isinstance(x, dict)]
            count = (raw.get("_metadata") or {}).get("totalCount")
            if total is None and whole(count) is not None:
                total = count
            cursor = (raw.get("_paging") or {}).get("_nextCursor")
            if not cursor or not page:
                return rows, total
            if cursor in seen_cursors:
                raise HostfullyError(f"Hostfully {path} returned the same cursor twice")
            seen_cursors.add(cursor)
        raise HostfullyError(f"Hostfully {path} pagination exceeds the safety limit")

    def _properties(self):
        rows, total = self._paged("/properties", {"agencyUid": self._agency}, "properties")
        return [normalize_property(property_row(r)) for r in rows], total

    def inventory(self):
        rows, total = self._properties()
        return {"data": rows, "total": len(rows), "complete": total is None or total == len(rows)}

    def property(self, selector):
        def load():
            rows, _ = self._properties()
            s = str(selector).casefold()
            hits = [r for r in rows if r["id"] == str(selector) or str(r.get("name") or "").casefold() == s]
            if len(hits) != 1:
                raise HostfullyError("Property must match exactly one Hostfully property by uid or name")
            if hits[0].get("listed") is False:
                raise HostfullyError("The selected Hostfully property is not active")
            return hits[0]
        return self.client.fetch("pms.property", [self.connections.account("hostfully"), selector], load)

    def reservations(self, pid, start, days):
        def load():
            leads, total = self._paged("/leads", {
                "propertyUid": pid, "checkInFrom": (start - timedelta(days=HISTORY_DAYS)).isoformat(),
                "checkInTo": (start + timedelta(days=max(days, 365))).isoformat()}, "leads")
            if any(lead.get("propertyUid") != pid for lead in leads):
                raise HostfullyError("Hostfully returned leads for another property")
            orders, _ = self._paged("/orders", {"propertyUid": pid}, "orders")
            money = orders_by_lead(orders)
            rows = [normalize_reservation(reservation_row(lead, money.get(lead.get("uid"))))
                    for lead in leads if lead_status(lead) is not None]
            return {"data": rows, "total": len(rows), "complete": total is None or total == len(leads)}
        return self.client.fetch("pms.reservations", [self.connections.account("hostfully"), pid, start.isoformat(), days], load)

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        def load():
            currency = self.property(pid)["currency"]
            # One extra night asked for and trimmed: the docs do not say whether `to` is inclusive.
            raw = self._get(f"/property-calendar/{urllib.parse.quote(pid)}",
                            {"from": start.isoformat(), "to": (end + timedelta(days=1)).isoformat()}, op="calendar")
            cal = raw.get("calendar") or {}
            if cal.get("propertyUid") not in (None, pid):
                raise HostfullyError("Hostfully calendar belongs to another property")
            entries = cal.get("entries")
            if not isinstance(entries, list):
                raise HostfullyError("Hostfully calendar has no entries list")
            return normalize_calendar([day_row(e, currency) for e in entries if isinstance(e, dict)
                                       and isinstance(e.get("date"), str)
                                       and start.isoformat() <= e["date"] <= end.isoformat()])
        return self.client.fetch("pms.calendar", [self.connections.account("hostfully"), pid, start.isoformat(), days], load)

    def reviews(self, pid):
        def load():
            rows, total = self._paged("/reviews", {"propertyUid": pid}, "reviews")
            mine = [normalize_review(review_row(r)) for r in rows if r.get("propertyUid") == pid]
            return {"data": mine, "total": len(mine), "complete": total is None or total == len(rows)}
        return self.client.fetch("pms.reviews", [self.connections.account("hostfully"), pid], load)


# ------------------------------------------------------------------------------ write target

_UID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_SEG = r"[A-Za-z0-9-]{1,64}"


class HostfullyCalendarTarget:
    """Per-date nightly price and min stay as Hostfully pricing periods (SET only, never REMOVE),
    one POST per date. DOCS-ONLY: the first live write to Hostfully has not happened yet."""

    name = "hostfully"
    host = HOST
    ALLOWED = (
        ("GET", re.compile(rf"{PREFIX}/properties/{_SEG}")),
        ("GET", re.compile(rf"{PREFIX}/property-calendar/{_SEG}")),
        ("GET", re.compile(rf"{PREFIX}/pricing-periods")),
        ("POST", re.compile(rf"{PREFIX}/pricing-periods")),
    )
    # Fields of an existing period that a SET carries forward untouched. Whether SET clears a
    # field it is not sent is not documented, so nothing already on the date is left to chance.
    CARRY = ("price", "minimumStay", "availableForCheckIn", "availableForCheckOut", "name")
    FLOOR_NOTE = ("Hostfully documents useMinimumPriceRule (a boolean) but no minimum price value; "
                  "the floor cannot be read")
    PRICING_TOOL_NOTE = "Hostfully's API does not say whether a pricing tool manages a property"

    def __init__(self, connections, opener=None):
        key = ((getattr(connections, "values", None) or {}).get("HOSTFULLY_API_KEY") or "").strip()
        if not key:
            raise CannotWrite("No Hostfully API key; put HOSTFULLY_API_KEY in the connector .env")
        self.http = TargetHTTP("Hostfully", HOST, self.ALLOWED, lambda m, p, q, b: {
            "X-HOSTFULLY-APIKEY": key, "Accept": "application/json", "User-Agent": UA},
            opener=opener, max_calls=400)

    @staticmethod
    def _id(listing_id) -> str:
        uid = str(listing_id)
        if not _UID.match(uid):
            raise CannotWrite(f"Hostfully: {listing_id!r} is not a Hostfully property uid")
        return uid

    def _currency(self, uid) -> str:
        _, body = self.http.request("GET", f"{PREFIX}/properties/{uid}")
        prop = body.get("property") if isinstance(body, dict) else None
        if not isinstance(prop, dict) or prop.get("uid") != uid:
            raise CannotWrite("Hostfully did not return exactly this property")
        cur = _currency((prop.get("pricing") or {}).get("currency"))
        if not cur:
            raise CannotWrite("Hostfully property has no readable currency")
        return cur

    def read_calendar(self, listing_id: str, start: date, end: date) -> dict:
        uid = self._id(listing_id)
        want = inclusive_dates(start, end)
        currency = self._currency(uid)
        _, body = self.http.request("GET", f"{PREFIX}/property-calendar/{uid}",
                                    {"from": start.isoformat(), "to": (end + timedelta(days=1)).isoformat()})
        cal = (body or {}).get("calendar") if isinstance(body, dict) else None
        if not isinstance(cal, dict) or not isinstance(cal.get("entries"), list):
            raise CannotWrite("Hostfully calendar has no entries list")
        if cal.get("propertyUid") not in (None, uid):
            raise CannotWrite("Hostfully calendar belongs to another property")
        days = {}
        for e in cal["entries"]:
            d = e.get("date") if isinstance(e, dict) else None
            if d not in want:
                continue
            if d in days:
                raise CannotWrite(f"Hostfully returned {d} twice")
            pricing, avail = e.get("pricing") or {}, e.get("availability") or {}
            if _currency(pricing.get("currency")) not in (None, currency):
                raise CannotWrite(f"Hostfully {d} is priced in {pricing.get('currency')}, the property in {currency}")
            unavailable = avail.get("unavailable")
            days[d] = {"price": number(pricing.get("value")), "min_stay": whole(avail.get("minimumStayLength")),
                       "available": (not unavailable) if isinstance(unavailable, bool) else None}
        missing = [d for d in want if d not in days]
        if missing:
            raise CannotWrite(f"Hostfully calendar is missing {len(missing)} of {len(want)} dates "
                              f"(first {missing[0]}); refusing a partial read")
        return {"currency": currency, "days": days}

    def _periods(self, uid, first: str, last: str) -> dict:
        _, body = self.http.request("GET", f"{PREFIX}/pricing-periods", {
            "propertyUid": uid, "from": first, "to": (date.fromisoformat(last) + timedelta(days=1)).isoformat()})
        rows = body.get("pricingPeriods") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise CannotWrite("Hostfully pricing periods are unreadable; absence cannot be assumed")
        out = {}
        for r in rows:
            if not isinstance(r, dict) or r.get("propertyUid") not in (None, uid):
                raise CannotWrite("Hostfully returned a pricing period for another property")
            if r.get("date") in out:
                raise CannotWrite(f"Hostfully returned two pricing periods for {r.get('date')}")
            out[r.get("date")] = r
        return out

    def write_calendar(self, listing_id: str, changes: dict, currency: str) -> None:
        uid = self._id(listing_id)
        ops = validate_changes("Hostfully", changes)
        same_currency("Hostfully", self._currency(uid), currency)
        existing = self._periods(uid, ops[0][0], ops[-1][0])
        bodies = []
        for d, price, min_stay in ops:  # build and check EVERY date before sending any
            period = {"propertyUid": uid, "date": d}
            period.update({k: v for k, v in (existing.get(d) or {}).items() if k in self.CARRY and v is not None})
            if price is None and number(period.get("price")) is None:
                raise CannotWrite(f"Hostfully: {d} has no pricing period, and the docs do not say what price "
                                  "a period created with only a min stay would carry. Nothing was sent; "
                                  "include a price for that date.")
            if price is not None:
                period["price"] = price
            if min_stay is not None:
                period["minimumStay"] = min_stay
            bodies.append((d, {"operation": "SET", "pricingPeriod": period}))
        for sent, (d, body) in enumerate(bodies):
            try:
                _, reply = self.http.request("POST", f"{PREFIX}/pricing-periods", body=body)
            except CannotWrite as exc:
                raise CannotWrite(f"{exc} ({sent} of {len(bodies)} dates were sent before this one; "
                                  "nothing is retried, the re-read shows what landed)") from None
            echo = reply.get("pricingPeriod") if isinstance(reply, dict) else None
            if not isinstance(echo, dict) or echo.get("date") != d or echo.get("propertyUid") not in (None, uid):
                raise CannotWrite(f"Hostfully POST {PREFIX}/pricing-periods: the reply did not echo {d} "
                                  f"({sent + 1} of {len(bodies)} dates sent); re-read before trusting it")

    def floor(self, listing_id: str):
        return None  # FLOOR_NOTE: the core falls back to property_config.settings.min_price

    def pricing_managed(self, listing_id: str):
        return None
