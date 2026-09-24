"""OwnerRez (API v2) as a PMS source for the runner. Read-only.

Measured live 2026-09-24 on a real account (read-only):
  - HTTP Basic (login email + `pt_` token) and a User-Agent, or OwnerRez answers 403
  - NO endpoint returns nightly rates or min-stay (rates are write-only: PATCH /v2/spotrates).
    The calendar is therefore built from bookings and blocks, with price and min-stay unknown;
    the runner's no-rates mode then reconciles bookings and availability only, and says so.
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


def calendar_rows(bookings: list, currency: str, start: date, days: int) -> list:
    """One row per night: RESERVED under a live booking, BLOCKED under a block, else AVAILABLE.
    Price and min-stay are None because OwnerRez does not expose them."""
    live = [b for b in bookings if isinstance(b, dict) and not _cancelled(b)]
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        over = [b for b in live if str(b.get("arrival")) <= d < str(b.get("departure"))]
        if any(not b.get("is_block") and b.get("type") != "block" for b in over):
            reason = "RESERVED"
        elif over:
            reason = "BLOCKED"
        else:
            reason = "AVAILABLE"
        out.append({"date": d, "price_cents": None, "currency": currency, "min_stay": None,
                    "available": reason == "AVAILABLE", "status_reason": reason,
                    "closed_for_checkin": None, "closed_for_checkout": None})
    return out


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
        def load():
            cur = self._detail(pid)["currency"]
            return normalize_calendar(calendar_rows(self._bookings(pid), cur, start, days))
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
