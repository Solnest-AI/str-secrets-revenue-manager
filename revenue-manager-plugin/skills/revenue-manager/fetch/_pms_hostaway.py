"""Hostaway (Public API v1) as a PMS source for the runner, and its calendar write target.

DOCS-ONLY. Every fact below was read from api.hostaway.com/documentation on 2026-09-25 (its
changelog runs to 2026-09-10). Nothing here has touched a live Hostaway account yet; the
citations are in revenue-manager-plugin/references/hostaway.md.

  - Auth: POST /v1/accessTokens, form body grant_type=client_credentials, client_id=<account
    id>, client_secret=<API key>, scope=general -> {token_type, expires_in, access_token}.
    "The token will be valid 1 second after being returned", so a fresh mint waits 1s. A 403
    means the token is no longer valid: mint once more (reads only; a write is never resent).
  - Every body is {status: success|fail, result, count, limit, offset}. status=fail is an
    error even on HTTP 200.
  - "boolean type should be considered as integer 0 or 1 value"; "all time values should be
    specified in UTC timezone", so reservationDate / submittedAt become +00:00 moments here.
  - Listing object: currencyCode, timeZoneName, personCapacity, bedroomsNumber, bedsNumber,
    bathroomsNumber, city, country, airbnbListingUrl, specialStatus (archived|null). It also
    carries door codes and wifi passwords: property_row() allowlists, nothing else leaves.
  - Calendar day: price (float, listing currency, whole units), minimumStay, isAvailable,
    closedOnArrival / closedOnDeparture, status available|blocked|mblocked|hardBlock|conflicted|
    reserved|pending|mreserved, plus unit counts that are non-null only on multi-unit listings.
    Whether GET ...?endDate is inclusive is NOT documented: ask for one extra day, keep the window.
  - Reservations: listingId filter, afterId cursor (offset deprecated 2026-03-31), statuses
    new|modified|cancelled|ownerStay|pending|awaitingPayment|awaitingGuestVerification|
    unconfirmed|declined|expired|inquiry*|unknown. isDatesUnspecified=1 means the dates are fake.
  - Reviews: GET /v1/reviews (limit max 500), rating on a 0-10 scale.

Not documented, so never assumed: a listing floor (min price) and any field naming a dynamic
pricing tool. floor() and pricing_managed() say so and return None; the core then uses
property_config.settings.min_price / pricing_tool, as the contract (docs/WRITE-TARGETS.md) says.
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
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from _mvp_pms import normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _mvp_store import CannotAnalyze
from _mvp_write import CannotWrite
from _pms_target_kit import HTTPStatus, NoRedirect, Transport, currency_code, validate_changes

HOST = "api.hostaway.com"
BASE = f"https://{HOST}/v1"
TOKEN_PATH = "/v1/accessTokens"
UA = "RevenueManager/1.0"
RAW_CACHE_MAX_AGE = 23 * 3600  # the kit's probe trusts its raw token cache for 23h
TOKEN_WAIT_SECONDS = 1
STATUS = {"new": "accepted", "modified": "accepted", "ownerstay": "accepted",
          "cancelled": "cancelled", "canceled": "cancelled",
          "pending": "request", "awaitingpayment": "request", "awaitingguestverification": "request",
          "unconfirmed": "request",
          "declined": "not accepted", "expired": "not accepted", "unknown": "not accepted",
          "inquiry": "inquiry", "inquirypreapproved": "inquiry", "inquirydenied": "inquiry",
          "inquirytimedout": "inquiry", "inquirynotpossible": "inquiry"}
CHANNELS = {2018: "airbnb", 2002: "vrbo", 2009: "vrbo", 2010: "vrbo", 2005: "booking",
            2007: "expedia", 2000: "direct", 2013: "direct"}
DAY_STATUS = {"available": "AVAILABLE", "reserved": "RESERVED", "mreserved": "RESERVED",
              "pending": "RESERVED", "blocked": "BLOCKED", "mblocked": "BLOCKED", "hardblock": "BLOCKED"}
UNIT_FIELDS = ("countAvailableUnits", "availableUnitsToSell", "countPendingUnits", "countBlockedUnits")
_AIRBNB_ROOM = re.compile(r"airbnb\.[a-z.]+/rooms/(?:plus/)?(\d{5,25})", re.I)


class HostawayError(CannotAnalyze):
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


def _flag(raw, key):
    """Documented as 0/1. The docs' own calendar samples send null on an ordinary night, so a
    PRESENT null reads as 'not set' (False). An ABSENT key stays unknown (None)."""
    if key not in raw:
        return None
    value = raw[key]
    if value in (1, True):
        return True
    if value in (0, False, None):
        return False
    return None


def _utc(value):
    """'2017-06-10 10:41:10' (documented UTC) -> '2017-06-10T10:41:10+00:00'."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value.strip()):
        return None
    return value.strip().replace(" ", "T") + "+00:00"


def _platform(channel_id):
    return CHANNELS.get(channel_id) if _int(channel_id) is not None else None


# ------------------------------------------------------------------------------ pure mappers

def property_row(raw: dict) -> dict:
    currency = raw.get("currencyCode")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
        raise HostawayError("Hostaway listing has no currency; prices cannot be read safely")
    listings = []
    match = _AIRBNB_ROOM.search(str(raw.get("airbnbListingUrl") or ""))
    if match:
        listings.append({"platform": "airbnb", "platform_id": match.group(1)})
    return {
        "id": str(raw["id"]) if _int(raw.get("id")) is not None else None,
        "name": raw.get("internalListingName") or raw.get("name"),
        "public_name": raw.get("externalListingName") or raw.get("name"),
        "timezone": raw.get("timeZoneName"), "currency": currency.upper(),
        "listed": raw.get("specialStatus") != "archived",
        "capacity": {"max": raw.get("personCapacity"), "bedrooms": raw.get("bedroomsNumber"),
                     "beds": raw.get("bedsNumber"), "bathrooms": raw.get("bathroomsNumber")},
        "address": {"city": raw.get("city"), "country": raw.get("country") or raw.get("countryCode")},
        "listings": listings,
    }


def day_status(raw: dict) -> str:
    reason = DAY_STATUS.get(str(raw.get("status") or "").lower(), "UNKNOWN")
    flag = raw.get("isAvailable")
    if flag in (0, 1) and (reason == "AVAILABLE") is not (flag == 1) and reason != "UNKNOWN":
        return "UNKNOWN"  # status and isAvailable disagree: refuse to pick one
    return reason


def is_multi_unit(raw: dict) -> bool:
    return any(raw.get(k) is not None for k in UNIT_FIELDS)


def day_row(raw: dict, currency: str) -> dict:
    reason = day_status(raw)
    return {
        "date": str(raw.get("date") or "")[:10], "price_cents": _cents(raw.get("price")), "currency": currency,
        "min_stay": _int(raw.get("minimumStay")),
        "available": {"AVAILABLE": True, "RESERVED": False, "BLOCKED": False}.get(reason),
        "status_reason": reason,
        "closed_for_checkin": _flag(raw, "closedOnArrival"),
        "closed_for_checkout": _flag(raw, "closedOnDeparture"),
    }


def accommodation_cents(raw: dict):
    """Room revenue = the one live `baseRate` finance line (docs: use `total`). A discount line
    has no documented sign, so its presence makes the value unknown rather than guessed."""
    if str(raw.get("status") or "").lower() == "ownerstay":
        return 0  # an owner block earns nothing; reported as zero-value accepted, never as paid
    lines = raw.get("financeField")
    if not isinstance(lines, list):
        return None
    live = [x for x in lines if isinstance(x, dict) and x.get("isDeleted") not in (1, True)]
    if any(str(x.get("type") or "").lower() == "discount" and _number(x.get("total")) for x in live):
        return None
    base = [x for x in live if str(x.get("name") or "").lower() == "baserate"]
    return _cents(base[0].get("total")) if len(base) == 1 else None


def reservation_row(raw: dict) -> dict:
    status = str(raw.get("status") or "").lower()
    fake_dates = raw.get("isDatesUnspecified") in (1, True)
    owner = status == "ownerstay"
    return {
        "id": str(raw["id"]) if _int(raw.get("id")) is not None else None,
        "platform": _platform(raw.get("channelId")),
        "status": STATUS.get(status, "unknown"),
        "stay_type": "owner_stay" if owner else "guest_stay", "owner_stay": owner,
        "check_in": None if fake_dates else raw.get("arrivalDate"),
        "check_out": None if fake_dates else raw.get("departureDate"),
        "nights": _int(raw.get("nights")), "booking_date": _utc(raw.get("reservationDate")),
        "property_ids": [str(raw["listingMapId"])] if _int(raw.get("listingMapId")) is not None else [],
        "financials": {"currency": raw.get("currency"), "host_accommodation_cents": accommodation_cents(raw),
                       "host_discounts": []},
    }


def review_row(raw: dict) -> dict:
    rating = _number(raw.get("rating"))
    ok = rating is not None and 0 < rating <= 10
    return {"id": str(raw["id"]) if _int(raw.get("id")) is not None else None,
            "platform": _platform(raw.get("channelId")),
            "reviewed_at": _utc(raw.get("submittedAt")),
            # Hostaway stores every channel on one documented 0-10 scale; the runner's is 1-5.
            "rating": float(Decimal(str(rating)) / 2) if ok else None,
            "rating_platform_original": rating if ok else None,
            "detailed_ratings": []}


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
    return [Path(d) / ".cache" / "hostaway.token" for d in (getattr(connections, "paths", {}) or {}).get("hostaway", [])]


def _mint_direct(account_id, api_key, opener=None) -> str:
    """The read path's mint. It is a POST, so it runs outside the read-only ReadClient."""
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": account_id,
                                   "client_secret": api_key, "scope": "general"}).encode()
    req = urllib.request.Request(BASE + "/accessTokens", data=body, method="POST", headers={
        "Content-type": "application/x-www-form-urlencoded", "Cache-control": "no-cache",
        "Accept": "application/json", "User-Agent": UA})
    try:
        with (opener or urllib.request.build_opener(NoRedirect())).open(req, timeout=60) as r:
            parsed = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        exc.close()
        why = "the account id or API key is wrong or revoked: create a new pair" if exc.code == 401 else "refused"
        raise HostawayError(f"Hostaway token request refused (HTTP {exc.code}); {why}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise HostawayError("Hostaway token endpoint unreachable") from None
    tok = parsed.get("access_token") if isinstance(parsed, dict) else None
    if not isinstance(tok, str) or not tok:
        raise HostawayError("Hostaway returned no access token")
    return tok


def get_token(connections, mint=None, *, fresh=False, sleep=time.sleep) -> tuple[str, bool]:
    """(token, came_from_cache). A mint waits the documented 1 second before first use and is
    written back to the kit's existing .cache folder (never creates folders)."""
    paths = _cache_paths(connections)
    if not fresh:
        for p in paths:
            tok = token_from_cache(p)
            if tok:
                return tok, True
    aid, key = connections.values.get("HOSTAWAY_ACCOUNT_ID"), connections.values.get("HOSTAWAY_API_KEY")
    if not aid or not key:
        raise HostawayError("No fresh Hostaway token cached and no HOSTAWAY_ACCOUNT_ID/HOSTAWAY_API_KEY to mint one")
    tok = (mint or _mint_direct)(aid, key)
    sleep(TOKEN_WAIT_SECONDS)
    target = next((p for p in paths if p.parent.is_dir()), None)
    if target:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never world-readable, even briefly
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tok)
        target.chmod(0o600)
    return tok, False


# ------------------------------------------------------------------------------ read adapter

class HostawaySource:
    """The four PMS reads the runner needs, in the same envelope as Hospitable."""

    def __init__(self, client, connections, sleep=time.sleep):
        self.client, self.connections, self._sleep = client, connections, sleep
        self._token, self._cached, self._currency = None, False, {}

    def _auth(self, fresh=False):
        self._token, self._cached = get_token(self.connections, fresh=fresh, sleep=self._sleep)

    def _get(self, path, params=None, op=None):
        if not self._token:
            self._auth()
        for attempt in (0, 1):
            url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
            try:
                body, _ = self.client.request("hostaway", op or path.strip("/").split("/")[0], url, headers={
                    "Authorization": "Bearer " + self._token, "Cache-control": "no-cache",
                    "Accept": "application/json", "User-Agent": UA})
            except CannotAnalyze as exc:
                # documented: 403 = the token is no longer valid. One fresh mint, then give up.
                if attempt == 0 and self._cached and str(exc).endswith("HTTP 403"):
                    self._auth(fresh=True)
                    continue
                raise
            if not isinstance(body, dict) or body.get("status") != "success":
                raise HostawayError(f"Hostaway {path} did not answer status=success")
            return body
        raise HostawayError(f"Hostaway {path}: token refused twice")

    def _offset_paged(self, path, params, limit, max_pages=200):
        rows, offset, total = [], 0, None
        for _ in range(max_pages):
            raw = self._get(path, {**params, "limit": limit, "offset": offset})
            page = raw.get("result")
            if not isinstance(page, list):
                raise HostawayError(f"Hostaway {path} has no result list")
            if total is None and _int(raw.get("count")) is not None:
                total = raw["count"]
            rows += [x for x in page if isinstance(x, dict)]
            offset += len(page)
            if not page or len(page) < limit or (total is not None and offset >= total):
                return rows, (total is None or len(rows) == total), (total if total is not None else len(rows))
        raise HostawayError(f"Hostaway {path} pagination exceeds the safety limit")

    def inventory(self):
        rows, complete, total = self._offset_paged("/listings", {}, 100)
        return {"data": [normalize_property(property_row(r)) for r in rows], "total": total, "complete": complete}

    def property(self, selector):
        def load():
            rows = self.inventory()["data"]
            hits = [r for r in rows if r["id"] == str(selector) or str(r.get("name", "")).casefold() == str(selector).casefold()]
            if len(hits) != 1:
                raise HostawayError("Property must match exactly one Hostaway listing by id or name")
            if hits[0].get("listed") is False:
                raise HostawayError("The selected Hostaway listing is archived")
            return hits[0]
        return self.client.fetch("pms.property", [self.connections.account_or("hostaway"), selector], load)

    def _listing_currency(self, pid):
        if pid not in self._currency:
            raw = self._get(f"/listings/{urllib.parse.quote(str(pid))}", op="listing").get("result")
            if not isinstance(raw, dict) or str(raw.get("id")) != str(pid):
                raise HostawayError("Hostaway returned another listing")
            self._currency[pid] = property_row(raw)["currency"]
        return self._currency[pid]

    def calendar(self, pid, start, days):
        end = start + timedelta(days=days - 1)
        def load():
            currency = self._listing_currency(pid)
            raw = self._get(f"/listings/{urllib.parse.quote(str(pid))}/calendar",
                            {"startDate": start.isoformat(), "endDate": (end + timedelta(days=1)).isoformat()},
                            op="calendar").get("result")
            if not isinstance(raw, list):
                raise HostawayError("Hostaway calendar has no day list")
            if any(isinstance(r, dict) and is_multi_unit(r) for r in raw):
                raise HostawayError("Hostaway multi-unit listing: availability is a unit count, not read by the runner")
            window = [r for r in raw if isinstance(r, dict) and start.isoformat() <= str(r.get("date"))[:10] <= end.isoformat()]
            return normalize_calendar([day_row(r, currency) for r in window])
        return self.client.fetch("pms.calendar", [self.connections.account_or("hostaway"), pid, start.isoformat(), days], load)

    def reservations(self, pid, start, days):
        def load():
            rows, seen, total, after = [], set(), None, None
            for _ in range(500):
                params = {"listingId": pid, "limit": 100, **({"afterId": after} if after is not None else {})}
                raw = self._get("/reservations", params)
                page = raw.get("result")
                if not isinstance(page, list):
                    raise HostawayError("Hostaway /reservations has no result list")
                if total is None and _int(raw.get("count")) is not None:
                    total = raw["count"]
                before = len(rows)
                for r in page:
                    if not isinstance(r, dict) or _int(r.get("id")) is None:
                        raise HostawayError("Hostaway reservation without an id")
                    if str(r.get("listingMapId")) != str(pid):
                        raise HostawayError("Reservation property scope could not be verified")
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        rows.append(normalize_reservation(reservation_row(r)))
                # A full page that added nothing new means the cursor did not move: stop, and let
                # the count check below report the collection as incomplete.
                if len(page) < 100 or len(rows) == before:
                    break
                after = page[-1]["id"]
            else:
                raise HostawayError("Hostaway /reservations pagination exceeds the safety limit")
            complete = total is None or len(rows) == total
            return {"data": rows, "total": total if total is not None else len(rows), "complete": complete}
        return self.client.fetch("pms.reservations", [self.connections.account_or("hostaway"), pid, start.isoformat(), days], load)

    def reviews(self, pid):
        # The listingMapIds[] array encoding is not documented, so read account-wide (documented
        # limit/offset) and keep only this listing's guest-to-host reviews.
        def load():
            rows, complete, _ = self._offset_paged("/reviews", {"type": "guest-to-host"}, 500)
            mine = [normalize_review(review_row(r)) for r in rows
                    if str(r.get("listingMapId")) == str(pid) and r.get("isCancelled") not in (1, True)]
            return {"data": mine, "total": len(mine), "complete": complete}
        return self.client.fetch("pms.reviews", [self.connections.account_or("hostaway"), pid], load)


# ------------------------------------------------------------------------------ write target

class HostawayCalendarTarget:
    """Per-date nightly price and min stay, written straight to Hostaway (docs/WRITE-TARGETS.md)."""

    name = "hostaway"
    host = HOST
    _ID = r"\d{1,12}"
    ALLOWED = (
        ("POST", re.compile(r"/v1/accessTokens")),
        ("GET", re.compile(rf"/v1/listings/{_ID}")),
        ("GET", re.compile(rf"/v1/listings/{_ID}/calendar")),
        ("PUT", re.compile(rf"/v1/listings/{_ID}/calendarIntervals")),
    )
    MAX_INTERVALS = 200  # documented: "Number of intervals to send in one request can't be more than 200"

    def __init__(self, connections, opener=None, sleep=time.sleep, max_calls=40):
        self.connections, self._sleep = connections, sleep
        self.transport = Transport("Hostaway", HOST, self.ALLOWED, opener, max_calls)
        self._token, self._cached = None, False

    # -- auth and calls

    def _mint(self, account_id, api_key):
        try:
            body = self.transport.request("POST", TOKEN_PATH, form={
                "grant_type": "client_credentials", "client_id": account_id, "client_secret": api_key,
                "scope": "general"}, headers={"Content-type": "application/x-www-form-urlencoded",
                                              "Cache-control": "no-cache", "Accept": "application/json",
                                              "User-Agent": UA})
        except HTTPStatus as exc:
            why = "; the account id or API key is wrong or revoked: create a new pair" if exc.code == 401 else ""
            raise CannotWrite(f"{exc}{why}") from None
        tok = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(tok, str) or not tok:
            raise CannotWrite("Hostaway returned no access token")
        return tok

    def _auth(self, fresh=False):
        try:
            self._token, self._cached = get_token(self.connections, self._mint, fresh=fresh, sleep=self._sleep)
        except HostawayError as exc:
            raise CannotWrite(str(exc)) from None

    def _call(self, method, path, params=None, json_body=None):
        if not self._token:
            self._auth()
        headers = {"Authorization": "Bearer " + self._token, "Cache-control": "no-cache",
                   "Accept": "application/json", "User-Agent": UA}
        if json_body is not None:
            headers["Content-type"] = "application/json"
        try:
            body = self.transport.request(method, path, headers=headers, params=params, json_body=json_body)
        except HTTPStatus as exc:
            # documented 403 = token no longer valid. Reads may mint once and retry; a write never.
            if exc.code == 403 and method == "GET" and self._cached:
                self._auth(fresh=True)
                return self._call(method, path, params)
            raise
        if isinstance(body, dict) and body.get("status") not in (None, "success"):
            raise CannotWrite(f"Hostaway {method} {path}: did not answer status=success")
        return body

    def _listing(self, listing_id):
        lid = str(listing_id)
        if not re.fullmatch(self._ID, lid):
            raise CannotWrite("Hostaway listing ids are numbers")
        raw = (self._call("GET", f"/v1/listings/{lid}") or {}).get("result")
        if not isinstance(raw, dict) or str(raw.get("id")) != lid:
            raise CannotWrite("Hostaway returned another listing (or none)")
        currency = raw.get("currencyCode")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
            raise CannotWrite("Hostaway listing has no currency")
        return lid, raw, currency.upper()

    # -- the contract

    def read_calendar(self, listing_id, start: date, end: date) -> dict:
        if end < start:
            raise CannotWrite("Calendar window ends before it starts")
        lid, _, currency = self._listing(listing_id)
        raw = (self._call("GET", f"/v1/listings/{lid}/calendar", params={
            "startDate": start.isoformat(), "endDate": (end + timedelta(days=1)).isoformat()}) or {}).get("result")
        if not isinstance(raw, list):
            raise CannotWrite("Hostaway calendar has no day list")
        days = {}
        for row in raw:
            if not isinstance(row, dict):
                raise CannotWrite("Hostaway calendar has an unreadable day")
            day = str(row.get("date") or "")[:10]
            if not (start.isoformat() <= day <= end.isoformat()):
                continue
            if day in days:
                raise CannotWrite(f"Hostaway calendar repeats {day}")
            if is_multi_unit(row):
                raise CannotWrite("Hostaway multi-unit listing: per-night writes are not supported")
            price = row.get("price")
            if price is not None and _number(price) is None:
                raise CannotWrite(f"Hostaway {day} price is unreadable")
            stay = row.get("minimumStay")
            if stay is not None and _int(stay) is None:
                raise CannotWrite(f"Hostaway {day} minimum stay is unreadable")
            reason = day_status(row)
            days[day] = {"price": None if price is None else float(price), "min_stay": stay,
                         "available": {"AVAILABLE": True, "RESERVED": False, "BLOCKED": False}.get(reason)}
        want = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
        if set(days) != want:
            raise CannotWrite(f"Hostaway calendar is missing {len(want - set(days))} of {len(want)} dates")
        return {"currency": currency, "days": dict(sorted(days.items()))}

    def write_calendar(self, listing_id, changes: dict, currency: str) -> None:
        items = validate_changes(changes)
        currency = currency_code(currency)
        lid, raw, live_currency = self._listing(listing_id)
        if live_currency != currency:
            raise CannotWrite(f"Hostaway listing is priced in {live_currency}, the plan is in {currency}")
        if raw.get("specialStatus") == "archived":
            raise CannotWrite("Hostaway listing is archived; Hostaway refuses changes to it")
        # One single-day interval per date (startDate == endDate). Whether endDate is inclusive
        # is not documented; a one-day interval can never spill onto a neighbouring night, and
        # if Hostaway reads it as empty the core's re-read reports the date as not applied.
        intervals = []
        for when, change in items:
            body = {"startDate": when.isoformat(), "endDate": when.isoformat()}
            if "price" in change:
                body["price"] = change["price"]
            if "min_stay" in change:
                body["minimumStay"] = change["min_stay"]
            intervals.append(body)
        sent = 0
        for i in range(0, len(intervals), self.MAX_INTERVALS):
            chunk = intervals[i:i + self.MAX_INTERVALS]
            try:
                self._call("PUT", f"/v1/listings/{lid}/calendarIntervals", json_body=chunk)
            except CannotWrite as exc:
                note = f"; {sent} of {len(intervals)} dates were already sent" if sent else ""
                raise CannotWrite(f"{exc}{note}") from None
            sent += len(chunk)

    def floor(self, listing_id):
        """Hostaway documents no listing-level minimum price (the Listing object has `price`,
        the base rate, and no floor). None: the core uses property_config.settings.min_price."""
        return None

    def pricing_managed(self, listing_id):
        """No documented Hostaway field names the dynamic pricing tool that controls a listing's
        rates. None: the core relies on property_config.settings.pricing_tool."""
        return None
