"""Beyond (formerly Beyond Pricing) as a pricing-tool source. Read-only. DOCS-ONLY.

Every endpoint and field here is cited in references/beyond.md (Beyond API docs,
developers.beyondpricing.com/full-documentation.md and /api/v1/schema/, read 2026-09-25).
There is no Beyond test account, so none of this has been run against a live account.

What the docs pin down, and this module relies on:
  - Base https://developers.beyondpricing.com/api/v1/ with a trailing slash on every path
    (without it Beyond answers 301, which the no-redirect transport turns into an error).
  - Auth `Authorization: Bearer <personal access token>` (BEYOND_TOKEN), and JSON:API:
    `Accept: application/vnd.api+json`, responses {"data": ...}, dasherized field names.
  - ALL prices are MAJOR currency units in the listing's own currency. The calendar says it
    outright: "integers in whole currency units ... never expressed in cents, do not divide
    by 100". Nothing here converts units.
  - Calendar: GET listings/{id}/calendar/?filter[start-date]&filter[end-date]; page size
    default 366, max 731. The filter names are exact: any other filter key is IGNORED with a
    200 and the default window comes back, so the returned dates are always checked.
  - Customizations: GET listings/{id}/customizations/ returns base-price, min-max-prices,
    min-stays, extra-guest-fees and time-based-adjustments in one resource (NOT
    manual-overrides, which is keyed by date and read from its own endpoint).
  - Manual overrides: one row per overridden date (start-date == end-date), each with at
    most one of `price` (fixed) or `percentage-adjustment`. Default window today..+365.
  - `max-price` null means no ceiling. `in-active-market` false predicts a calendar 400.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from _mvp_store import CannotAnalyze

HOST = "developers.beyondpricing.com"
BASE = f"https://{HOST}/api/v1"
MEDIA = "application/vnd.api+json"
UA = "RevenueManager/1.0"
PMS_NAME = "beyond"  # what an envelope's target.pms carries for a Beyond listing
AGGREGATE_KEYS = ("base-price", "min-max-prices", "min-stays", "extra-guest-fees",
                  "time-based-adjustments")
_LID = re.compile(r"^\d{1,20}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LISTINGS_PAGE = 100   # documented maximum for /listings/
CALENDAR_PAGE = 366   # documented default (maximum 731)


class BeyondError(CannotAnalyze):
    """A Beyond response this code will not stand behind."""


def listing_id(value) -> str:
    """Beyond listing ids are integers in the path (OpenAPI: listing_id integer)."""
    text = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    if not _LID.match(text):
        raise BeyondError(f"{value!r} is not a Beyond listing id (a whole number)")
    return text


def number(value, what, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or value is None:
        raise BeyondError(f"Beyond {what} is not a number: {value!r}")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise BeyondError(f"Beyond {what} is not a number: {value!r}") from None
    if not math.isfinite(out):
        raise BeyondError(f"Beyond {what} is not a finite number")
    return out


def whole(value, what, *, nullable=False):
    """An integer field (min-stay). A float that is not whole is refused, never rounded."""
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise BeyondError(f"Beyond {what} is not a whole number: {value!r}")
    return int(value)


def _resource(raw, rtype, lid=None):
    """The single JSON:API resource object of `rtype`, with its attributes."""
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or data.get("type") != rtype:
        raise BeyondError(f"Beyond did not return a {rtype} resource")
    if lid is not None and str(data.get("id")) != lid:
        raise BeyondError(f"Beyond returned the {rtype} of another listing")
    attrs = data.get("attributes")
    if not isinstance(attrs, dict):
        raise BeyondError(f"Beyond {rtype} carries no attributes")
    return attrs


def _pagination(raw):
    meta = (raw.get("meta") or {}).get("pagination") if isinstance(raw, dict) else None
    if not isinstance(meta, dict) or not all(type(meta.get(k)) is int for k in ("page", "pages", "count")):
        raise BeyondError("Beyond pagination metadata is missing or unreadable")
    return meta


# ------------------------------------------------------------------------------ pure parsers
# Shared by the reader below and the writer (_beyond_write.py): one reading of every shape.

def parse_listing(raw, lid) -> dict:
    a = _resource(raw, "listings", lid)
    return listing_row(lid, a)


def listing_row(lid, a) -> dict:
    currency = a.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise BeyondError("Beyond listing has no ISO currency")
    sync = a.get("sync-status") if isinstance(a.get("sync-status"), dict) else {}
    return {
        "id": lid, "pms": PMS_NAME, "name": a.get("title"), "currency": currency,
        "timezone": a.get("timezone"),
        "enabled": a.get("enabled") if isinstance(a.get("enabled"), bool) else None,
        "in_active_market": a.get("in-active-market") if isinstance(a.get("in-active-market"), bool) else None,
        # listing-level copies; the customization endpoints are authoritative for writes
        "base": number(a.get("base-price"), "base-price", nullable=True),
        "min": number(a.get("min-price"), "min-price", nullable=True),
        "max": number(a.get("max-price"), "max-price", nullable=True),
        "min_stay": whole(a.get("min-stay"), "min-stay", nullable=True),
        "no_of_bedrooms": a.get("bedrooms"),
        "latitude": a.get("latitude"), "longitude": a.get("longitude"),
        "sync_state": sync.get("state"), "last_sync_at": sync.get("last-successful-sync-at"),
        "channels": [{"channel": c.get("channel"), "channel_id": str(c.get("channel-id"))}
                     for c in a.get("channel-listings") or [] if isinstance(c, dict)],
    }


def parse_customizations(raw, lid) -> dict:
    """GET customizations/: every aggregate family, verbatim (dasherized), plus the three
    numbers the writer owns. Every family must be present: absence is not "no rules"."""
    a = _resource(raw, "listing-customizations", lid)
    missing = [k for k in AGGREGATE_KEYS if not isinstance(a.get(k), dict)]
    if missing:
        raise BeyondError(f"Beyond customizations are missing {', '.join(missing)}")
    base = number(a["base-price"].get("base-price"), "base-price", nullable=True)
    mm = a["min-max-prices"]
    return {
        "raw": {k: a[k] for k in AGGREGATE_KEYS},
        "base": base,
        "min": number(mm.get("min-price"), "min-price", nullable=True),
        "max": number(mm.get("max-price"), "max-price", nullable=True),
        "min_stay": whole(a["min-stays"].get("min-stay"), "min-stay", nullable=True),
    }


def parse_overrides(raw, lid) -> dict:
    """GET customizations/manual-overrides/ -> {date: {"date", "price"} | {"date",
    "percentage_adjustment"}}. Documented: one row per date, start == end, at most one value.
    Anything else is refused: this code cannot put back a shape it does not understand."""
    a = _resource(raw, "manual-override-customizations", lid)
    rows = a.get("overrides")
    if not isinstance(rows, list):
        raise BeyondError("Unreadable Beyond overrides; absence cannot be assumed")
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            raise BeyondError("A Beyond override row is not an object")
        d = r.get("start-date")
        if not isinstance(d, str) or not _DATE.match(d) or r.get("end-date") != d:
            raise BeyondError("A Beyond override row spans more than one date; the docs say a "
                              "read returns one row per date")
        if r.get("days-of-week"):
            raise BeyondError(f"The Beyond override on {d} carries days-of-week on a read")
        if d in out:
            raise BeyondError(f"Beyond returned two overrides for {d}")
        price, pct = r.get("price"), r.get("percentage-adjustment")
        if price is not None and pct is not None:
            raise BeyondError(f"The Beyond override on {d} has both a price and a percentage")
        if price is not None:
            out[d] = {"date": d, "price": number(price, f"override price on {d}")}
        elif pct is not None:
            out[d] = {"date": d, "percentage_adjustment": whole(pct, f"override percentage on {d}")}
        else:
            raise BeyondError(f"The Beyond override on {d} carries no value; the docs say "
                              "dates with no override are omitted")
    return out


def parse_calendar(raw, start: date, end: date) -> dict:
    """GET calendar/ for [start, end] inclusive, exactly those dates, one page. `price` is
    what Beyond prices the night at and pushes (a fixed override shows as that amount);
    `price_posted` is the last price that reached the channel."""
    rows = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise BeyondError("Beyond calendar has no entries list")
    page = _pagination(raw)
    if page["pages"] != 1:
        raise BeyondError("Beyond calendar came back in more than one page")
    out = {}
    for r in rows:
        a = r.get("attributes") if isinstance(r, dict) and r.get("type") == "calendar-entries" else None
        if not isinstance(a, dict) or not isinstance(a.get("date"), str):
            raise BeyondError("A Beyond calendar entry is unreadable")
        d = a["date"]
        if d in out:
            raise BeyondError(f"Beyond calendar repeats {d}")
        ot = a.get("price-override-type")
        if ot not in (None, "fixed", "percentage"):
            raise BeyondError(f"Beyond calendar override type {ot!r} is undocumented")
        out[d] = {
            "date": d,
            "price": number(a.get("price"), f"calendar price on {d}"),
            "price_posted": number(a.get("price-posted"), f"price-posted on {d}", nullable=True),
            "effective_min_price": number(a.get("effective-min-price"), f"effective-min-price on {d}",
                                          nullable=True),
            "effective_max_price": number(a.get("effective-max-price"), f"effective-max-price on {d}",
                                          nullable=True),
            "override_type": ot,
            "availability": a.get("availability"),
        }
    want = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    if sorted(out) != want:
        # A misspelt filter silently returns the default window (docs warning), so this is
        # the check that the dates asked for are the dates received.
        raise BeyondError(f"Beyond calendar does not cover exactly {start} to {end}")
    return out


# ------------------------------------------------------------------------------ reader

class BeyondSource:
    """GET-only reads through the metered ReadClient (which refuses any non-GET for this
    provider). Each read is normalized and validated before it is stored."""

    def __init__(self, client, connections):
        self.client = client
        self.connections = connections

    def _get(self, path, params=None):
        body, _ = self.client.request(
            "beyond", path.strip("/").split("/")[-1] or "listings",
            BASE + path + ("?" + urlencode(params) if params else ""),
            headers={"Authorization": "Bearer " + self.connections.key("beyond"),
                     "Accept": MEDIA, "User-Agent": UA},
        )
        return body

    def _account(self):
        return self.connections.account("beyond")

    def listings(self) -> list:
        """Every listing the token can see. Listings without an active channel connection are
        invisible to the whole API (docs), so absence here is not proof a listing is gone."""
        def load():
            rows, seen, total = [], set(), None
            for n in range(1, 101):
                raw = self._get("/listings/", {"page[number]": n, "page[size]": LISTINGS_PAGE})
                data = raw.get("data") if isinstance(raw, dict) else None
                if not isinstance(data, list):
                    raise BeyondError("Beyond listings response has no data list")
                page = _pagination(raw)
                if page["page"] != n or (total is not None and page["count"] != total):
                    raise BeyondError("Beyond listings pagination is inconsistent; rerun")
                total = page["count"]
                for item in data:
                    if not isinstance(item, dict) or item.get("type") != "listings":
                        raise BeyondError("Beyond listings response carries a non-listing")
                    lid = listing_id(item.get("id"))
                    if lid in seen:
                        raise BeyondError("Beyond listings repeat an id across pages")
                    seen.add(lid)
                    rows.append(listing_row(lid, item.get("attributes") or {}))
                if n >= page["pages"]:
                    if len(rows) != total:
                        raise BeyondError("Beyond listings count does not match its total")
                    return rows
            raise BeyondError("Beyond listings pagination exceeds the safety limit")

        return self.client.fetch("prices.beyond.listings", [self._account()], load)

    def listing(self, lid) -> dict:
        lid = listing_id(lid)
        return self.client.fetch(
            "prices.beyond.listing", [self._account(), lid],
            lambda: parse_listing(self._get(f"/listings/{quote(lid)}/"), lid))

    def customizations(self, lid) -> dict:
        lid = listing_id(lid)
        return self.client.fetch(
            "rules.beyond", [self._account(), lid],
            lambda: parse_customizations(self._get(f"/listings/{quote(lid)}/customizations/"), lid))

    def calendar(self, lid, start: date, days: int) -> dict:
        """{"last_refreshed_at": listing sync time, "data": [one row per date]}. Beyond has no
        per-date min stay on its calendar; `min_stay` is None, never guessed."""
        lid = listing_id(lid)
        if not 1 <= days <= CALENDAR_PAGE:
            raise BeyondError(f"Beyond calendar reads are 1-{CALENDAR_PAGE} days here")
        end = start + timedelta(days=days - 1)

        def load():
            cal = parse_calendar(self._get(f"/listings/{quote(lid)}/calendar/", {
                "filter[start-date]": start.isoformat(), "filter[end-date]": end.isoformat(),
                "sort": "date", "page[size]": CALENDAR_PAGE}), start, end)
            return {"data": [dict(cal[d], min_stay=None) for d in sorted(cal)]}

        out = self.client.fetch("prices.beyond", [self._account(), lid, start.isoformat(), days], load)
        return {"last_refreshed_at": self.listing(lid).get("last_sync_at"), **out}

    def overrides(self, lid, start: date, days: int) -> list:
        lid = listing_id(lid)
        end = start + timedelta(days=days - 1)

        def load():
            got = parse_overrides(self._get(
                f"/listings/{quote(lid)}/customizations/manual-overrides/",
                {"filter[start-date]": start.isoformat(), "filter[end-date]": end.isoformat()}), lid)
            outside = [d for d in got if not start.isoformat() <= d <= end.isoformat()]
            if outside:
                raise BeyondError("Beyond returned overrides outside the requested window")
            return [got[d] for d in sorted(got)]

        return self.client.fetch("overrides.beyond", [self._account(), lid, start.isoformat(), days], load)
