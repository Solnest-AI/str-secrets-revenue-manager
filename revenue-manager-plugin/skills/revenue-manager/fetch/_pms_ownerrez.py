"""OwnerRez (API v2) as a PMS source for the runner. Read-only.

Measured live 2026-09-24 on a real account (read-only):
  - HTTP Basic (login email + `pt_` token) and a User-Agent, or OwnerRez answers 403
  - NIGHTLY RATES ARE READABLE: GET /v2/calendar/{property_id}?from&to (up to 366 days) returns
    per night `status` (available|booked|blocked|gap|unavailable), `rate.rent` / `rate.amount`,
    `rate.is_spot_rate`, `rules.min_nights`, and `rules.is_arrival_disallowed` /
    `is_departure_disallowed` (present only when true). Measured on 8 properties x 90 nights:
    every rate was a spot rate (what PriceLabs pushes) and amount == rent on all 720.
    (An earlier note, from the connections kit's build doc, said there was no rate GET. OwnerRez's
    own docs and the live API say otherwise; that note was wrong.)
  - /bookings: int ids, `arrival`/`departure` dates (check_in/check_out are TIMES), `type`
    booking|block, `status` (`active` measured), `booked_utc`, `listing_site`. The property
    filter `property_ids` WORKS here. `include_charges=true` puts charges on the list rows;
    charges[type=rent] is the room revenue (surcharges and tax are separate lines).
  - /reviews IGNORES `property_ids` (returned other properties) and `property_id` returns
    nothing: fetch account-wide and filter by property_id locally.
  - The Airbnb id is only on the property DETAIL: listing_numbers.Airbnb.
  - Pagination: `next_page_url` (offset/limit); only /properties returns a `count`.
  - An EMPTY collection comes back as `{limit, offset}` with NO `items` key. Only that exact
    shape reads as empty; any other body without `items` is refused (an error is never zero).
"""

from __future__ import annotations

import base64
import urllib.parse
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _mvp_store import CannotAnalyze

BASE = "https://api.ownerrez.com/v2"
UA = "RevenueManager/1.0"
STATUS = {"active": "accepted", "confirmed": "accepted", "canceled": "cancelled", "cancelled": "cancelled",
          "tentative": "request", "hold": "request", "pending": "request", "inquiry": "inquiry",
          "declined": "not accepted", "expired": "not accepted"}
SITES = {"airbnb": "airbnb", "vrbo": "vrbo", "homeaway": "vrbo", "booking.com": "booking", "booking": "booking",
         "direct": "direct", "ownerrez": "direct", "website": "direct"}


class OwnerRezError(CannotAnalyze):
    pass


def _cents(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(Decimal(str(value)).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _site(value):
    return SITES.get(str(value or "").strip().lower())


def _cancelled(b):
    return str(b.get("status") or "").lower() in ("canceled", "cancelled", "declined", "expired")


# ------------------------------------------------------------------------------ pure mappers

def property_row(raw: dict) -> dict:
    listings = [{"platform": _site(k), "platform_id": str(v)}
                for k, v in (raw.get("listing_numbers") or {}).items() if _site(k) and v]
    return {
        "id": str(raw.get("id")) if raw.get("id") is not None else None, "name": raw.get("name"),
        "public_name": raw.get("external_name"), "timezone": raw.get("time_zone"),
        "currency": raw.get("currency_code"), "listed": raw.get("active") is True and not raw.get("is_snoozed"),
        "capacity": {"max": raw.get("max_guests"), "bedrooms": raw.get("bedrooms"), "beds": raw.get("beds"),
                     "bathrooms": raw.get("bathrooms")},
        "address": {k: (raw.get("address") or {}).get(k) for k in ("city", "country")},
        "listings": listings,
    }


def day_row(raw: dict, currency: str) -> dict:
    status = str(raw.get("status") or "").lower()
    rules = raw.get("rules") or {}
    reason = {"available": "AVAILABLE", "gap": "AVAILABLE", "booked": "RESERVED",
              "blocked": "BLOCKED", "unavailable": "BLOCKED"}.get(status, "UNKNOWN")
    if reason == "AVAILABLE" and rules.get("is_stay_disallowed") is True:
        reason = "BLOCKED"  # documented: Available with this rule is still not bookable
    rent = (raw.get("rate") or {}).get("rent")
    min_n = rules.get("min_nights")
    return {
        "date": str(raw.get("date") or "")[:10], "price_cents": _cents(rent), "currency": currency,
        "min_stay": min_n if isinstance(min_n, int) and not isinstance(min_n, bool) else None,
        "available": {"AVAILABLE": True, "RESERVED": False, "BLOCKED": False}.get(reason),
        "status_reason": reason,
        # documented optional booleans, present only when the rule is set
        "closed_for_checkin": rules.get("is_arrival_disallowed") is True,
        "closed_for_checkout": rules.get("is_departure_disallowed") is True,
    }


def reservation_row(raw: dict) -> dict:
    if raw.get("is_block") or raw.get("type") == "block":
        raise OwnerRezError("A block is not a reservation")
    charges = raw.get("charges")
    rent = [c for c in charges if isinstance(c, dict) and c.get("type") == "rent"] if isinstance(charges, list) else []
    amounts = [_cents(c.get("amount")) for c in rent]
    accommodation = sum(amounts) if rent and all(a is not None for a in amounts) else None
    arrival, departure = raw.get("arrival"), raw.get("departure")
    try:
        nights = (date.fromisoformat(departure) - date.fromisoformat(arrival)).days
    except (TypeError, ValueError):
        nights = None
    return {
        "id": str(raw.get("id")) if raw.get("id") is not None else None, "platform": _site(raw.get("listing_site")),
        "status": STATUS.get(str(raw.get("status") or "").lower(), "unknown"),
        "check_in": arrival, "check_out": departure, "nights": nights,
        "booking_date": raw.get("booked_utc") or raw.get("created_utc"),
        "property_ids": [str(raw["property_id"])] if raw.get("property_id") is not None else [],
        "financials": {"currency": raw.get("currency_code"), "host_accommodation_cents": accommodation, "host_discounts": []},
    }


def review_row(raw: dict) -> dict:
    stars = raw.get("stars")
    site = _site(raw.get("listing_site"))
    ok = isinstance(stars, (int, float)) and not isinstance(stars, bool) and 0 < stars <= 5 and site != "booking"
    return {"id": str(raw.get("id")) if raw.get("id") is not None else None, "platform": site,
            "reviewed_at": raw.get("date") or raw.get("created_utc"), "rating": stars if ok else None,
            "detailed_ratings": []}


# ------------------------------------------------------------------------------ loader

class OwnerRezSource:
    def __init__(self, client, connections):
        self.client, self.connections = client, connections
        email, token = connections.values.get("OWNERREZ_EMAIL"), connections.values.get("OWNERREZ_TOKEN")
        if not email or not token:
            raise OwnerRezError("OWNERREZ_EMAIL and OWNERREZ_TOKEN are required")
        self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    def _get(self, path, params=None, op=None):
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body, _ = self.client.request("ownerrez", op or path.strip("/").split("/")[0], url, headers={
            "Authorization": self._auth, "User-Agent": UA, "Accept": "application/json"})
        if not isinstance(body, dict):
            raise OwnerRezError("OwnerRez returned an unreadable body")
        return body

    def _paged(self, path, params, max_pages=200):
        rows, offset = [], 0
        for _ in range(max_pages):
            raw = self._get(path, {**params, "limit": 100, "offset": offset})
            page = raw.get("items")
            if page is None and {"limit", "offset"} <= set(raw) <= {"limit", "offset", "next_page_url"}:
                page = []  # measured: an EMPTY collection is {limit, offset} with no items key
            if not isinstance(page, list):
                raise OwnerRezError(f"OwnerRez {path} has no items list")
            rows += [x for x in page if isinstance(x, dict)]
            if not raw.get("next_page_url") or not page:
                return rows, raw.get("count")
            offset += len(page)
        raise OwnerRezError(f"OwnerRez {path} pagination exceeds the safety limit")

    def _detail(self, pid):
        return normalize_property(property_row(self._get(f"/properties/{urllib.parse.quote(str(pid))}", op="property")))

    def inventory(self):
        rows, count = self._paged("/properties", {})
        if isinstance(count, int) and count != len(rows):
            raise OwnerRezError("OwnerRez property count does not match the rows returned")
        detailed = [self._detail(r["id"]) for r in rows]
        return {"data": detailed, "total": len(detailed), "complete": True}

    def property(self, selector):
        def load():
            rows, _ = self._paged("/properties", {})
            hits = [r for r in rows if str(r.get("id")) == str(selector) or str(r.get("name", "")).casefold() == str(selector).casefold()]
            if len(hits) != 1:
                raise OwnerRezError("Property must match exactly one OwnerRez property by id or name")
            p = self._detail(hits[0]["id"])
            if p.get("listed") is False:
                raise OwnerRezError("The selected OwnerRez property is inactive or snoozed")
            return p
        return self.client.fetch("pms.property", [self.connections.account("ownerrez"), selector], load)

    def _bookings(self, pid):
        rows, _ = self._paged("/bookings", {"property_ids": pid, "since_utc": "2000-01-01T00:00:00Z", "include_charges": "true"})
        if any(str(r.get("property_id")) != str(pid) for r in rows):
            raise OwnerRezError("OwnerRez returned bookings for another property")
        return rows

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        def load():
            raw = self._get(f"/calendar/{urllib.parse.quote(str(pid))}", {"from": start.isoformat(), "to": end.isoformat()}, op="calendar")
            if str(raw.get("property_id")) != str(pid):
                raise OwnerRezError("OwnerRez calendar belongs to another property")
            nights = raw.get("days")
            if not isinstance(nights, list):
                raise OwnerRezError("OwnerRez calendar has no days list")
            return normalize_calendar([day_row(d, raw.get("currency_code")) for d in nights])
        return self.client.fetch("pms.calendar", [self.connections.account("ownerrez"), pid, start.isoformat(), days], load)

    def reservations(self, pid, start, days):
        def load():
            rows = [normalize_reservation(reservation_row(b)) for b in self._bookings(pid)
                    if not (b.get("is_block") or b.get("type") == "block")]
            return {"data": rows, "total": len(rows), "complete": True}
        return self.client.fetch("pms.reservations", [self.connections.account("ownerrez"), pid, start.isoformat(), days], load)

    def reviews(self, pid):
        def load():
            rows, _ = self._paged("/reviews", {})
            mine = [normalize_review(review_row(r)) for r in rows if str(r.get("property_id")) == str(pid)]
            return {"data": mine, "total": len(mine), "complete": True}
        return self.client.fetch("pms.reviews", [self.connections.account("ownerrez"), pid], load)
