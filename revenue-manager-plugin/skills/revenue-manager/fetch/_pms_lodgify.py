"""Lodgify (Public API v1 + v2, one host) as a PMS source for the runner, and its calendar write target.

DOCS-ONLY. Every fact below was read from docs.lodgify.com (the OpenAPI definitions behind each
reference page, fetched as .md) on 2026-09-25. Nothing here has touched a live Lodgify account;
citations are in revenue-manager-plugin/references/lodgify.md.

  - Auth: header `X-ApiKey: <key>` on every call. Limits 600/min (v1), 750/min (v2).
  - Properties: GET /v2/properties (page, size max 50, includeCount) -> {count, items}; each has
    id, name, internal_name, city, country, currency_code, rooms [{id, name}], is_active ("linked
    to a valid website", NOT the same as listed, so it is not used as `listed`). No timezone, no
    Airbnb id. GET /v2/properties/{id}/rooms -> room types with max_people, bedrooms, bathrooms, units.
  - Nightly rates: GET /v2/rates/calendar?houseId&roomTypeId&startDate&endDate, both dates
    inclusive -> {calendar_items: [{date, is_default, prices: [{min_stay, max_stay, price_per_day,
    price_per_additional_guest, additional_guests_starts_from}]}], rate_settings: {currency_code}}.
    Several price entries on one date are length-of-stay tiers: no single nightly price exists.
  - Availability: GET /v1/availability/{propertyId}/{roomTypeId}?periodStart&periodEnd -> periods
    with period_start, period_end ("inclusive"), available, total_units, is_available ("not closed
    and has unoccupied units"), booking_ids, closed_period_id.
  - Bookings: GET /v2/reservations/bookings (page, size, includeCount, stayFilter) has NO property
    filter: read account-wide and keep property_id matches. status Open|Tentative|Booked|Declined,
    canceled_at, is_deleted, source enum, currency_code, subtotals.stay. The money type in this
    schema ("Money1") is an object with NO documented fields, so only a plain number is read.
  - Rates write: POST /v1/rates/savewithoutavailability {property_id, room_type_id, rates: [{
    is_default, start_date, end_date (EXCLUSIVE), price_per_day (>= 1), min_stay, max_stay,
    price_per_additional_guest, additional_guests_starts_from}]} -> `true` on success.

Not documented, so never assumed: reviews (no endpoint exists in the index), closed-to-arrival /
closed-to-departure per night, a listing floor (min_price is a display summary, "always given in
euros"), and any field naming a dynamic pricing tool.
"""

from __future__ import annotations

import math
import re
import urllib.parse
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation
from _mvp_store import CannotAnalyze
from _mvp_write import CannotWrite
from _pms_target_kit import Transport, currency_code, validate_changes

HOST = "api.lodgify.com"
BASE = f"https://{HOST}"
UA = "RevenueManager/1.0"
STATUS = {"booked": "accepted", "tentative": "request", "open": "inquiry", "declined": "not accepted"}
SOURCES = {"airbnb": "airbnb", "airbnbintegration": "airbnb", "homeaway": "vrbo", "bookingcom": "booking",
           "expedia": "expedia", "manual": "direct", "oh": "direct", "publicapi": "direct"}
NO_REVIEWS = ("Lodgify's Public API has no reviews endpoint (docs.lodgify.com index, read 2026-09-25); "
              "the reviews spoke is a named gap for Lodgify")


class LodgifyError(CannotAnalyze):
    pass


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _cents(value):
    value = _number(value)
    return None if value is None else int(Decimal(str(value)).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _day(value):
    """'2026-10-05' or '2026-10-05T00:00:00' -> '2026-10-05'; anything else -> None."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


# ------------------------------------------------------------------------------ pure mappers

def single_room(rooms) -> dict | None:
    """The one room type of a whole-home listing, or None when there is not exactly one
    single-unit room type (multi-room / multi-unit properties are not read or written)."""
    if not isinstance(rooms, list) or len(rooms) != 1 or not isinstance(rooms[0], dict):
        return None
    room = rooms[0]
    if _int(room.get("id")) is None or _int(room.get("units")) not in (None, 1):
        return None
    return room


def property_row(raw: dict, room: dict | None = None) -> dict:
    currency = raw.get("currency_code")
    room = room or {}
    return {
        "id": str(raw["id"]) if _int(raw.get("id")) is not None else None,
        "name": raw.get("internal_name") or raw.get("name"), "public_name": raw.get("name"),
        "timezone": None,  # not in the documented property schema
        "currency": currency.upper() if isinstance(currency, str) else None,
        "listed": None,    # is_active means "linked to a valid website", not "listed"
        "capacity": {"max": room.get("max_people"), "bedrooms": room.get("bedrooms"), "beds": None,
                     "bathrooms": room.get("bathrooms")},
        "address": {"city": raw.get("city"), "country": raw.get("country") or raw.get("country_code")},
        "listings": [],    # no channel listing ids in the documented property schema
    }


def rate_entry(item: dict):
    """(price, min_stay, entry) for a date with exactly one price entry, (None, None, None) for
    none; raises for length-of-stay tiers (several entries), which have no single nightly price."""
    prices = item.get("prices")
    if prices in (None, []):
        return None, None, None
    if not isinstance(prices, list) or len(prices) != 1 or not isinstance(prices[0], dict):
        raise LodgifyError(f"Lodgify {_day(item.get('date'))} has {len(prices) if isinstance(prices, list) else 'unreadable'} "
                           "rate tiers; one nightly price cannot be read")
    entry = prices[0]
    return _number(entry.get("price_per_day")), _int(entry.get("min_stay")), entry


def period_status(period: dict) -> str:
    if _int(period.get("total_units")) not in (None, 1):
        return "UNKNOWN"  # multi-unit: a count, not a yes/no night
    if period.get("is_available") is True:
        return "AVAILABLE"
    if period.get("closed_period_id") is not None:
        return "BLOCKED"
    if period.get("is_available") is False and period.get("available") == 0:
        return "RESERVED"
    return "UNKNOWN"


def night_statuses(periods, start: date, end: date) -> dict:
    """{date: AVAILABLE|RESERVED|BLOCKED|UNKNOWN} for every night in [start, end]. period_end is
    documented inclusive. A night two periods both claim is UNKNOWN, never a pick."""
    out = {}
    for period in periods if isinstance(periods, list) else []:
        if not isinstance(period, dict):
            continue
        first, last = _day(period.get("period_start")), _day(period.get("period_end"))
        if not first or not last:
            continue
        status = period_status(period)
        d = max(date.fromisoformat(first), start)
        while d <= min(date.fromisoformat(last), end):
            key = d.isoformat()
            out[key] = "UNKNOWN" if key in out else status
            d += timedelta(days=1)
    return out


def day_row(item: dict, status: str, currency) -> dict:
    try:
        price, stay, _ = rate_entry(item)
    except LodgifyError:
        price, stay = None, None  # tiers: the night is read, its single price is unknown
    return {
        "date": _day(item.get("date")), "price_cents": _cents(price), "currency": currency, "min_stay": stay,
        "available": {"AVAILABLE": True, "RESERVED": False, "BLOCKED": False}.get(status),
        "status_reason": status,
        # Lodgify documents no closed-to-arrival / closed-to-departure per night: unknown, not False.
        "closed_for_checkin": None, "closed_for_checkout": None,
    }


def accommodation_cents(raw: dict):
    """subtotals.stay when it is a plain number and no promotion (sign undocumented) applies."""
    sub = raw.get("subtotals") if isinstance(raw.get("subtotals"), dict) else {}
    stay, promo = sub.get("stay"), sub.get("promotions")
    if promo not in (None, 0) and _number(promo) != 0:
        return None
    return _cents(stay)


def reservation_row(raw: dict) -> dict:
    arrival, departure = _day(raw.get("arrival")), _day(raw.get("departure"))
    try:
        nights = (date.fromisoformat(departure) - date.fromisoformat(arrival)).days
    except (TypeError, ValueError):
        nights = None
    status = "cancelled" if raw.get("canceled_at") else STATUS.get(str(raw.get("status") or "").lower(), "unknown")
    return {
        "id": str(raw["id"]) if _int(raw.get("id")) is not None else None,
        "platform": SOURCES.get(str(raw.get("source") or "").lower()),
        "status": status, "check_in": arrival, "check_out": departure, "nights": nights,
        "booking_date": raw.get("created_at"),
        "property_ids": [str(raw["property_id"])] if _int(raw.get("property_id")) is not None else [],
        "financials": {"currency": raw.get("currency_code"), "host_accommodation_cents": accommodation_cents(raw),
                       "host_discounts": []},
    }


# ------------------------------------------------------------------------------ read adapter

class LodgifySource:
    """The four PMS reads the runner needs, in the same envelope as Hospitable."""

    def __init__(self, client, connections):
        self.client, self.connections = client, connections
        self._key = connections.values.get("LODGIFY_API_KEY")
        if not self._key:
            raise LodgifyError("LODGIFY_API_KEY is required")
        self._rooms = {}

    def _get(self, path, params=None, op=None):
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body, _ = self.client.request("lodgify", op or path.strip("/").split("/")[1], url, headers={
            "X-ApiKey": self._key, "Accept": "application/json", "User-Agent": UA})
        return body

    def _room(self, pid):
        pid = str(pid)
        if pid not in self._rooms:
            rooms = self._get(f"/v2/properties/{urllib.parse.quote(pid)}/rooms", op="rooms")
            if not isinstance(rooms, list):
                raise LodgifyError("Lodgify rooms answer is not a list")
            self._rooms[pid] = single_room(rooms)
        return self._rooms[pid]

    def _pages(self, path, params, size, max_pages=500):
        rows, total = [], None
        for page in range(1, max_pages + 1):
            raw = self._get(path, {**params, "page": page, "size": size, "includeCount": "true"})
            items = raw.get("items") if isinstance(raw, dict) else None
            if not isinstance(items, list):
                raise LodgifyError(f"Lodgify {path} has no items list")
            if total is None and _int(raw.get("count")) is not None:
                total = raw["count"]
            rows += [x for x in items if isinstance(x, dict)]
            if not items or len(items) < size or (total is not None and len(rows) >= total):
                return rows, total is not None and len(rows) == total
        raise LodgifyError(f"Lodgify {path} pagination exceeds the safety limit")

    def inventory(self):
        rows, complete = self._pages("/v2/properties", {}, 50)
        data = [normalize_property(property_row(r, self._room(r["id"]) if _int(r.get("id")) is not None else None))
                for r in rows]
        return {"data": data, "total": len(data), "complete": complete}

    def property(self, selector):
        def load():
            # Match on the property list first, then read ONE property's rooms (not every one).
            rows, _ = self._pages("/v2/properties", {}, 50)
            want = str(selector).casefold()
            hits = [r for r in rows if str(r.get("id")) == str(selector)
                    or want in (str(r.get("internal_name") or r.get("name") or "").casefold(), str(r.get("name") or "").casefold())]
            if len(hits) != 1 or _int(hits[0].get("id")) is None:
                raise LodgifyError("Property must match exactly one Lodgify property by id or name")
            return normalize_property(property_row(hits[0], self._room(hits[0]["id"])))
        return self.client.fetch("pms.property", [self.connections.account("lodgify"), selector], load)

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        def load():
            room = self._room(pid)
            if not room:
                raise LodgifyError("Lodgify property is not one single-unit room type; the runner reads whole-home listings only")
            rates = self._get("/v2/rates/calendar", {"houseId": pid, "roomTypeId": room["id"], "startDate": start.isoformat(),
                                                     "endDate": end.isoformat()}, op="rates")
            items = rates.get("calendar_items") if isinstance(rates, dict) else None
            if not isinstance(items, list):
                raise LodgifyError("Lodgify rates calendar has no calendar_items")
            currency = (rates.get("rate_settings") or {}).get("currency_code")
            periods = self._get(f"/v1/availability/{urllib.parse.quote(str(pid))}/{room['id']}",
                                {"periodStart": start.isoformat(), "periodEnd": end.isoformat()}, op="availability")
            if not isinstance(periods, list):
                raise LodgifyError("Lodgify availability answer is not a list")
            if any(isinstance(p, dict) and p.get("room_type_id") not in (None, room["id"]) for p in periods):
                raise LodgifyError("Lodgify availability belongs to another room type")
            status = night_statuses(periods, start, end)
            window = [i for i in items if isinstance(i, dict) and _day(i.get("date")) and start.isoformat() <= _day(i.get("date")) <= end.isoformat()]
            return normalize_calendar([day_row(i, status.get(_day(i["date"]), "UNKNOWN"), currency) for i in window])
        return self.client.fetch("pms.calendar", [self.connections.account("lodgify"), pid, start.isoformat(), days], load)

    def reservations(self, pid, start, days):
        def load():
            rows, complete = self._pages("/v2/reservations/bookings", {"stayFilter": "All"}, 50)
            mine = [normalize_reservation(reservation_row(b)) for b in rows
                    if str(b.get("property_id")) == str(pid) and b.get("is_deleted") is not True]
            return {"data": mine, "total": len(mine), "complete": complete}
        return self.client.fetch("pms.reservations", [self.connections.account("lodgify"), pid, start.isoformat(), days], load)

    def reviews(self, pid):
        raise LodgifyError(NO_REVIEWS)


# ------------------------------------------------------------------------------ write target

class LodgifyCalendarTarget:
    """Per-date nightly price and min stay, written straight to Lodgify (docs/WRITE-TARGETS.md)."""

    name = "lodgify"
    host = HOST
    _ID = r"\d{1,10}"
    ALLOWED = (
        ("GET", re.compile(rf"/v2/properties/{_ID}/rooms")),
        ("GET", re.compile(r"/v2/rates/calendar")),
        ("GET", re.compile(rf"/v1/availability/{_ID}/{_ID}")),
        ("POST", re.compile(r"/v1/rates/savewithoutavailability")),
    )

    def __init__(self, connections, opener=None, max_calls=40):
        self._key = connections.values.get("LODGIFY_API_KEY")
        if not self._key:
            raise CannotWrite("No Lodgify API key; put LODGIFY_API_KEY in the connector .env")
        self.transport = Transport("Lodgify", HOST, self.ALLOWED, opener, max_calls)

    def _call(self, method, path, params=None, json_body=None):
        headers = {"X-ApiKey": self._key, "Accept": "application/json", "User-Agent": UA}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        return self.transport.request(method, path, headers=headers, params=params, json_body=json_body)

    def _room(self, listing_id):
        lid = str(listing_id)
        if not re.fullmatch(self._ID, lid):
            raise CannotWrite("Lodgify property ids are numbers")
        room = single_room(self._call("GET", f"/v2/properties/{lid}/rooms"))
        if not room:
            raise CannotWrite("Lodgify property is not one single-unit room type; per-night writes are refused")
        return lid, room["id"]

    def _rates(self, lid, rid, start, end):
        body = self._call("GET", "/v2/rates/calendar", params={"houseId": lid, "roomTypeId": rid,
                                                               "startDate": start.isoformat(), "endDate": end.isoformat()})
        items = body.get("calendar_items") if isinstance(body, dict) else None
        currency = ((body or {}).get("rate_settings") or {}).get("currency_code") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise CannotWrite("Lodgify rates calendar has no calendar_items")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
            raise CannotWrite("Lodgify rates calendar does not state its currency")
        by_day = {}
        for item in items:
            day = _day(item.get("date")) if isinstance(item, dict) else None
            if day is None:
                raise CannotWrite("Lodgify rates calendar has an unreadable date")
            if not (start.isoformat() <= day <= end.isoformat()):
                continue
            if day in by_day:
                raise CannotWrite(f"Lodgify rates calendar repeats {day}")
            try:
                price, stay, entry = rate_entry(item)
            except LodgifyError as exc:
                raise CannotWrite(str(exc)) from None
            if entry is not None and entry.get("price_per_day") is not None and price is None:
                raise CannotWrite(f"Lodgify {day} price is unreadable")
            by_day[day] = (price, stay, entry)
        want = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
        if set(by_day) != want:
            raise CannotWrite(f"Lodgify rates calendar is missing {len(want - set(by_day))} of {len(want)} dates")
        return currency.upper(), by_day

    def read_calendar(self, listing_id, start: date, end: date) -> dict:
        if end < start:
            raise CannotWrite("Calendar window ends before it starts")
        lid, rid = self._room(listing_id)
        currency, by_day = self._rates(lid, rid, start, end)
        periods = self._call("GET", f"/v1/availability/{lid}/{rid}",
                             params={"periodStart": start.isoformat(), "periodEnd": end.isoformat()})
        if not isinstance(periods, list):
            raise CannotWrite("Lodgify availability answer is not a list")
        status = night_statuses(periods, start, end)
        days = {day: {"price": None if price is None else float(price), "min_stay": stay,
                      "available": {"AVAILABLE": True, "RESERVED": False, "BLOCKED": False}.get(status.get(day))}
                for day, (price, stay, _) in sorted(by_day.items())}
        return {"currency": currency, "days": days}

    def write_calendar(self, listing_id, changes: dict, currency: str) -> None:
        items = validate_changes(changes)
        currency = currency_code(currency)
        lid, rid = self._room(listing_id)
        live_currency, by_day = self._rates(lid, rid, items[0][0], items[-1][0])
        if live_currency != currency:
            raise CannotWrite(f"Lodgify property is priced in {live_currency}, the plan is in {currency}")
        rates = []
        for when, change in items:
            price, stay, entry = by_day[when.isoformat()]
            new_price = change.get("price", price)
            new_stay = change.get("min_stay", stay)
            if new_price is None or new_price < 1:
                raise CannotWrite(f"Lodgify {when.isoformat()}: price_per_day must be at least 1 (documented minimum)")
            rate = {"is_default": False, "start_date": when.isoformat(),
                    # documented: end_date is EXCLUSIVE, so one night ends on the next day
                    "end_date": (when + timedelta(days=1)).isoformat(),
                    "price_per_day": float(new_price)}
            if new_stay is not None:
                rate["min_stay"] = new_stay
            # Carry the night's other rate fields forward so the write moves only what was asked.
            for key in ("max_stay", "additional_guests_starts_from"):
                if _int((entry or {}).get(key)) is not None:
                    rate[key] = entry[key]
            if _number((entry or {}).get("price_per_additional_guest")) is not None:
                rate["price_per_additional_guest"] = entry["price_per_additional_guest"]
            rates.append(rate)
        body = self._call("POST", "/v1/rates/savewithoutavailability",
                          json_body={"property_id": int(lid), "room_type_id": rid, "rates": rates})
        if body is not True:
            raise CannotWrite("Lodgify POST /v1/rates/savewithoutavailability: did not confirm (expected true)")

    def floor(self, listing_id):
        """Lodgify documents no listing floor. PropertyDto.min_price / RoomDetailsDto.min_price is a
        display summary ("always given in euros"), not a minimum-price setting. None: the core
        uses property_config.settings.min_price."""
        return None

    def pricing_managed(self, listing_id):
        """No documented Lodgify field names a dynamic pricing tool. None: the core relies on
        property_config.settings.pricing_tool."""
        return None
