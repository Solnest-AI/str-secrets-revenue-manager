"""Guesty (Open API v1) as a PMS source for the runner, plus GuestyCalendarTarget (the calendar
WRITE target for _calendar_write, at the bottom; endpoints cited in references/guesty.md).

Measured live 2026-09-24 on a real account (read-only):
  - prices are WHOLE currency units (151 = $151), so they become cents here
  - calendar `status` is `available` / `booked` (plus `unavailable` for blocks); every booked
    night carries a reservationId
  - the Airbnb listing id lives at integrations[platform=airbnb2].airbnb2.id
  - reservations carry money.fareAccommodationAdjusted (accommodation after discounts),
    createdAt and confirmedAt; guest objects are never read past this module
  - reviews carry the raw channel review; Airbnb's overall_rating is out of 5
  - the account object carries per-channel `markups` (setup can read them instead of asking)

Token cap: Guesty allows FIVE access tokens per 24 hours per clientId. A token is only minted
when no fresh cached one exists, and it is written back to the shared cache the connections
kit's checker and Guesty server read (`<kit>/.cache/guesty.token`, raw, trusted 23h). A JSON
cache ({access_token, expires_at}) is also understood, via GUESTY_TOKEN_CACHE.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _mvp_store import CannotAnalyze

BASE = "https://open-api.guesty.com/v1"
TOKEN_URL = "https://open-api.guesty.com/oauth2/token"
UA = "Mozilla/5.0 (revenue-manager)"
RAW_CACHE_MAX_AGE = 23 * 3600
STATUS = {"confirmed": "accepted", "canceled": "cancelled", "cancelled": "cancelled",
          "declined": "not accepted", "expired": "not accepted", "closed": "not accepted",
          "inquiry": "inquiry", "reserved": "request", "awaiting_payment": "request",
          "pending": "request"}
PLATFORM = {"airbnb2": "airbnb", "airbnb": "airbnb", "homeaway2": "vrbo", "homeaway": "vrbo",
            "bookingcom": "booking", "manual": "direct", "direct": "direct"}
CATEGORIES = ("cleanliness", "accuracy", "checkin", "communication", "location", "value")


class GuestyError(CannotAnalyze):
    pass


def _cents(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(Decimal(str(value)).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _platform(value):
    return PLATFORM.get(str(value or "").lower().replace(".", ""), None)


# ------------------------------------------------------------------------------ pure mappers

def property_row(raw: dict) -> dict:
    prices = raw.get("prices") or {}
    currency = prices.get("currency")
    if not isinstance(currency, str) or len(currency) != 3:
        raise GuestyError("Guesty listing has no currency; prices cannot be read safely")
    listings = []
    for item in raw.get("integrations") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("platform")
        ext = (item.get(key) or {}) if isinstance(key, str) else {}
        if _platform(key) and ext.get("id"):
            listings.append({"platform": _platform(key), "platform_id": str(ext["id"])})
    return {
        "id": raw.get("_id"), "name": raw.get("nickname") or raw.get("title"),
        "public_name": raw.get("title"), "timezone": raw.get("timezone"), "currency": currency.upper(),
        "listed": raw.get("active") is True and raw.get("listed", True) is not False,
        "capacity": {"max": raw.get("accommodates"), "bedrooms": raw.get("bedrooms"),
                     "beds": raw.get("beds"), "bathrooms": raw.get("bathrooms")},
        "address": {k: (raw.get("address") or {}).get(k) for k in ("city", "country")},
        "listings": listings,
    }


def day_row(raw: dict) -> dict:
    status = str(raw.get("status") or "").lower()
    reason = {"available": "AVAILABLE", "booked": "RESERVED", "reserved": "RESERVED",
              "unavailable": "BLOCKED", "blocked": "BLOCKED"}.get(status, "UNKNOWN")
    return {
        "date": raw.get("date"), "price_cents": _cents(raw.get("price")), "currency": raw.get("currency"),
        "min_stay": raw.get("minNights") if isinstance(raw.get("minNights"), int) else None,
        "available": {"AVAILABLE": True, "RESERVED": False, "BLOCKED": False}.get(reason),
        "status_reason": reason,
        "closed_for_checkin": raw.get("cta") if isinstance(raw.get("cta"), bool) else None,
        "closed_for_checkout": raw.get("ctd") if isinstance(raw.get("ctd"), bool) else None,
    }


def reservation_row(raw: dict) -> dict:
    money = raw.get("money") or {}
    status = STATUS.get(str(raw.get("status") or "").lower(), "unknown")
    return {
        "id": raw.get("_id"), "platform": _platform((raw.get("integration") or {}).get("platform")),
        "status": status, "check_in": raw.get("checkInDateLocalized"), "check_out": raw.get("checkOutDateLocalized"),
        "nights": raw.get("nightsCount"), "booking_date": raw.get("createdAt"),
        "property_ids": [raw["listingId"]] if isinstance(raw.get("listingId"), str) else [],
        "financials": {"currency": money.get("currency"),
                       "host_accommodation_cents": _cents(money.get("fareAccommodationAdjusted")),
                       "host_discounts": []},
    }


def review_row(raw: dict) -> dict:
    rr = raw.get("rawReview") or {}
    platform = _platform(raw.get("channelId"))
    airbnb = platform == "airbnb"
    return {
        "id": raw.get("_id"), "platform": platform,
        "reviewed_at": rr.get("submitted_at") or raw.get("createdAt"),
        # Only Airbnb's 1-5 scale; other channels use other scales and would corrupt the mean.
        "rating": rr.get("overall_rating") if airbnb else None,
        "detailed_ratings": [{"type": c, "rating": rr.get(f"category_ratings_{c}")}
                             for c in CATEGORIES if airbnb and rr.get(f"category_ratings_{c}") is not None],
    }


def reservation_query(pid: str, fields: str) -> dict:
    """MEASURED TRAP (2026-09-24): Guesty IGNORES the `listingId` query parameter on
    /reservations. Asking for one listing returned 73 rows, 61 of them from 12 other listings.
    Only the `filters` JSON narrows it (162 rows, all on the requested listing). The per-row
    scope check in reservations() stays as the second guard."""
    return {"filters": json.dumps([{"field": "listingId", "operator": "$eq", "value": pid}]),
            "fields": fields, "sort": "-checkIn"}


# ------------------------------------------------------------------------------ token

def token_from_cache(path) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8-sig").strip()
    if text.startswith("{"):
        try:
            d = json.loads(text)
        except ValueError:
            return None
        ok = d.get("access_token") and isinstance(d.get("expires_at"), (int, float)) and d["expires_at"] - time.time() > 300
        return d["access_token"] if ok else None
    return text if text and time.time() - p.stat().st_mtime < RAW_CACHE_MAX_AGE else None


def _cache_paths(connections) -> list:
    out = []
    if os.environ.get("GUESTY_TOKEN_CACHE"):
        out.append(Path(os.environ["GUESTY_TOKEN_CACHE"]).expanduser())
    for d in (getattr(connections, "paths", {}) or {}).get("guesty", []):
        out.append(Path(d) / ".cache" / "guesty.token")
    return out


def get_token(connections) -> str:
    paths = _cache_paths(connections)
    for p in paths:
        tok = token_from_cache(p)
        if tok:
            return tok
    cid, secret = connections.values.get("GUESTY_CLIENT_ID"), connections.values.get("GUESTY_CLIENT_SECRET")
    if not cid or not secret:
        raise GuestyError("No fresh Guesty token cached and no GUESTY_CLIENT_ID/SECRET to mint one")
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "open-api",
                                   "client_id": cid, "client_secret": secret}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            tok = json.loads(r.read()).get("access_token")
    except urllib.error.HTTPError as exc:
        exc.close()
        why = ("the client id or secret is expired or wrong" if exc.code == 401
               else "today's 5-token cap may be spent")
        raise GuestyError(f"Guesty token request refused (HTTP {exc.code}); {why}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise GuestyError("Guesty token endpoint unreachable") from None
    if not tok:
        raise GuestyError("Guesty returned no access token")
    kit = next((p for p in paths if p.name == "guesty.token"), None)
    if kit:
        kit.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        kit.write_text(tok, encoding="utf-8")
        kit.chmod(0o600)
    return tok


# ------------------------------------------------------------------------------ loader

class GuestySource:
    """The four PMS reads the runner needs, returned in the same envelope as Hospitable."""

    def __init__(self, client, connections):
        self.client, self.connections, self._token = client, connections, None

    def _get(self, path, params=None, op=None):
        if not self._token:
            self._token = get_token(self.connections)
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body, _ = self.client.request("guesty", op or path.strip("/").split("/")[0], url, headers={
            "Authorization": "Bearer " + self._token, "Accept": "application/json", "User-Agent": UA})
        if not isinstance(body, dict):
            raise GuestyError("Guesty returned an unreadable body")
        return body

    def _paged(self, path, params, key, mapper, limit=100, max_pages=200):
        rows, skip, total = [], 0, None
        for _ in range(max_pages):
            raw = self._get(path, {**params, "limit": limit, "skip": skip})
            page = raw.get(key)
            if not isinstance(page, list):
                raise GuestyError(f"Guesty {path} has no {key} list")
            count = raw.get("count")
            if total is None and isinstance(count, int):
                total = count
            rows += [mapper(x) for x in page if isinstance(x, dict)]
            skip += len(page)
            if not page or len(page) < limit or (total is not None and skip >= total):
                complete = total is None or len(rows) == total
                return {"data": rows, "total": total if total is not None else len(rows), "complete": complete}
        raise GuestyError(f"Guesty {path} pagination exceeds the safety limit")

    def inventory(self):
        fields = "_id title nickname active listed timezone prices accommodates bedrooms beds bathrooms address integrations"
        return self._paged("/listings", {"fields": fields}, "results", lambda r: normalize_property(property_row(r)))

    def property(self, selector):
        def load():
            rows = self.inventory()["data"]
            hits = [r for r in rows if r["id"] == selector or str(r.get("name", "")).casefold() == str(selector).casefold()]
            if len(hits) != 1:
                raise GuestyError("Property must match exactly one Guesty listing by id or name")
            if hits[0].get("listed") is False:
                raise GuestyError("The selected Guesty listing is not active")
            return hits[0]
        return self.client.fetch("pms.property", [self.connections.account_or("guesty"), selector], load)

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        def load():
            raw = self._get(f"/availability-pricing/api/calendar/listings/{urllib.parse.quote(pid)}",
                            {"startDate": start.isoformat(), "endDate": end.isoformat()}, op="calendar")
            rows = ((raw.get("data") or {}).get("days"))
            if not isinstance(rows, list):
                raise GuestyError("Guesty calendar has no day rows")
            if any(r.get("listingId") not in (None, pid) for r in rows):
                raise GuestyError("Guesty calendar belongs to another listing")
            return normalize_calendar([day_row(r) for r in rows])
        return self.client.fetch("pms.calendar", [self.connections.account_or("guesty"), pid, start.isoformat(), days], load)

    def reservations(self, pid, start, days):
        fields = ("_id status listingId checkInDateLocalized checkOutDateLocalized nightsCount createdAt "
                  "confirmedAt integration.platform money.currency money.fareAccommodationAdjusted")
        def mapper(raw):
            row = normalize_reservation(reservation_row(raw))
            if row["property_ids"] != [pid]:
                raise GuestyError("Reservation property scope could not be verified")
            return row
        return self.client.fetch("pms.reservations", [self.connections.account_or("guesty"), pid, start.isoformat(), days],
                                 lambda: self._paged("/reservations", reservation_query(pid, fields), "results", mapper))

    def reviews(self, pid):
        # Same trap as reservations: the bare listingId param is not trusted to narrow the
        # account-wide collection, so use the filters JSON, then keep ONLY rows that say they
        # belong to this listing. A row with no listingId cannot be attributed and is dropped.
        def load():
            q = reservation_query(pid, "")
            raw = self._get("/reviews", {"filters": q["filters"], "limit": 100})
            page = raw.get("data")
            if not isinstance(page, list):
                raise GuestyError("Guesty reviews have no data list")
            rows = [normalize_review(review_row(r)) for r in page if isinstance(r, dict) and r.get("listingId") == pid]
            return {"data": rows, "total": len(rows), "complete": len(page) < 100}
        return self.client.fetch("pms.reviews", [self.connections.account_or("guesty"), pid], load)


# ------------------------------------------------------------------------------ write target

class GuestyCalendarTarget:
    """Guesty calendar writes for _calendar_write (references/guesty.md).

      GET /v1/availability-pricing/api/calendar/listings/{id}?startDate&endDate
          data.days[]: date, listingId, currency, price (WHOLE units, measured), minNights, status
      PUT /v1/availability-pricing/api/calendar/listings
          [{"listingId", "startDate", "endDate", "price"?, "minNights"?}, ...] -> 200 "ok"
          One request, one listing, one period per date: Guesty's docs say to send a single
          listing per request and never update one listing in parallel.

    Prices are sent in whole currency units only (price_step): every price read live on a real
    account was whole, and Guesty documents only "a number in the listing's currency". A
    fractional price is refused at plan time instead of being guessed at.
    """

    name = "guesty"
    label = "Guesty"
    host = "open-api.guesty.com"
    ALLOWED = (
        ("GET", re.compile(r"/v1/availability-pricing/api/calendar/listings/[A-Za-z0-9._:-]{1,128}")),
        ("PUT", re.compile(r"/v1/availability-pricing/api/calendar/listings")),
    )
    LIVE_WRITE_VERIFIED = False
    SETTLE_SECONDS = (0, 5, 15)  # no documented lag; two extra READS cover a slow commit
    EXPLAIN = {401: "the Guesty token is invalid or expired", 403: "the Guesty token cannot write calendars",
               404: "Guesty has no listing with this id", 422: "Guesty rejected the request as invalid",
               429: "Guesty rate limit (15/s, 120/min, 5000/hr account-wide)"}

    def __init__(self, connections, opener=None, max_calls: int = 30):
        from _calendar_write import CalendarHTTP
        self.connections, self._token = connections, None
        self.http = CalendarHTTP(self.label, self.host, self.ALLOWED, self._auth, opener, max_calls,
                                 self.EXPLAIN)

    def _auth(self):
        from _mvp_write import CannotWrite
        if not self._token:
            try:
                self._token = get_token(self.connections)  # cached token first; mints only if none
            except CannotAnalyze as exc:
                raise CannotWrite(str(exc)) from None
        return {"Authorization": "Bearer " + self._token}

    def read_calendar(self, listing_id, start, end) -> dict:
        from _mvp_write import CannotWrite, _num
        raw = self.http.request(
            "GET", "/v1/availability-pricing/api/calendar/listings/" + urllib.parse.quote(str(listing_id), safe=""),
            {"startDate": start.isoformat(), "endDate": end.isoformat()})
        rows = ((raw or {}).get("data") or {}).get("days") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            raise CannotWrite("Guesty calendar has no day rows")
        days, currencies = {}, set()
        for r in rows:
            if not isinstance(r, dict) or not isinstance(r.get("date"), str):
                raise CannotWrite("Guesty calendar has an unreadable day")
            if r.get("listingId") not in (None, listing_id):
                raise CannotWrite("Guesty calendar belongs to another listing")
            if r["date"] in days:
                raise CannotWrite(f"Guesty returned {r['date']} twice")
            if r.get("currency"):
                currencies.add(str(r["currency"]).upper())
            price, mn = r.get("price"), r.get("minNights")
            status = str(r.get("status") or "").lower()
            days[r["date"]] = {
                "price": None if price is None else _num(price, f"Guesty price on {r['date']}"),
                "min_stay": mn if isinstance(mn, int) and not isinstance(mn, bool) else None,
                "available": True if status == "available" else
                             False if status in ("unavailable", "booked", "reserved", "blocked") else None,
            }
        if len(currencies) != 1:
            raise CannotWrite("Guesty calendar does not carry exactly one currency")
        return {"currency": currencies.pop(), "days": days}

    def write_calendar(self, listing_id, changes: dict, currency: str) -> None:
        periods = []
        for d in sorted(changes):
            c, row = changes[d], {"listingId": listing_id, "startDate": d, "endDate": d}
            if "price" in c:
                p = float(c["price"])
                row["price"] = int(p) if p == int(p) else p
            if "min_stay" in c:
                row["minNights"] = int(c["min_stay"])
            periods.append(row)
        self.http.request("PUT", "/v1/availability-pricing/api/calendar/listings", body=periods)

    def price_step(self, currency):
        return 1  # whole currency units (see the class docstring)

    def floor(self, listing_id):
        return None  # no documented listing min price is read here; property_config min_price applies

    def pricing_managed(self, listing_id):
        return None  # the Open API documents no dynamic-pricing owner flag; property_config decides
