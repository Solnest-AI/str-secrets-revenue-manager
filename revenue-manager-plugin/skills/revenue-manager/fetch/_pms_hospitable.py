"""Hospitable (Public API v2) as a PMS calendar WRITE target for _calendar_write.

Reads for the runner stay in _mvp_sources (Sources.calendar); this module is only the writer's
target. Every endpoint is cited in references/hospitable.md ("Calendar write target").

  GET /v2/properties/{uuid}/calendar?start_date&end_date   data.days[]: date, min_stay,
      status.available, price.amount (MINOR units, e.g. cents) + price.currency
  PUT /v2/properties/{uuid}/calendar   {"dates": [{"date", "price": {"amount": <int minor>},
      "min_stay"}]} -> 202 {"status": "accepted"}. Scope calendar:write. Documented as
      ASYNCHRONOUS: "successful writes may not appear in the read endpoint immediately", so the
      core re-READS on SETTLE_SECONDS; the PUT is never repeated.
  GET /v2/properties/{uuid}    `calendar_restricted: true` means the PUT will be refused
      (Airbnb connected with the limited Operations scope); checked before planning.

Minor units follow ISO 4217 (Hospitable's Currencies page: JPY and VND have no decimals), so
USD 150.25 is sent as 15025 and JPY 15000 as 15000.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from _calendar_write import CalendarHTTP, from_minor, to_minor
from _mvp_store import CannotAnalyze
from _mvp_write import CannotWrite

HOST = "public.api.hospitable.com"
_SEG = r"[A-Za-z0-9._:-]{1,128}"
EXPLAIN = {
    401: "the Hospitable token is invalid or expired",
    403: "the Hospitable token lacks the calendar:write scope",
    404: "Hospitable has no property with this id on this account",
    422: "Hospitable documents two causes: the calendar is restricted (Airbnb on the limited "
         "Operations scope), or Hospitable Dynamic Pricing is on for this property",
    429: "Hospitable rate limit (1000 requests per minute)",
}


class HospitableCalendarTarget:
    name = "hospitable"
    label = "Hospitable"
    # Written live 2026-09-25 (The Urban Nest, 2026-12-04, 126 -> 127 CAD, 202 then re-read
    # verified; undo applied and verified), so cards drop the "first live write" line.
    LIVE_WRITE_VERIFIED = True
    host = HOST
    ALLOWED = (
        ("GET", re.compile(rf"/v2/properties/{_SEG}/calendar")),
        ("PUT", re.compile(rf"/v2/properties/{_SEG}/calendar")),
        ("GET", re.compile(rf"/v2/properties/{_SEG}")),
    )
    # Never written live by this writer yet: the card says so (docs/WRITE-TARGETS.md).
    LIVE_WRITE_VERIFIED = False
    # Documented asynchronous: "successful writes may not appear in the read endpoint immediately".
    APPLIES_ASYNC = True
    # Seconds to wait before each verification READ after the one PUT (asynchronous writes).
    SETTLE_SECONDS = (0, 5, 15, 30, 60)

    def __init__(self, connections, opener=None, max_calls: int = 30):
        try:
            token = connections.key("hospitable")
        except CannotAnalyze as exc:
            raise CannotWrite(str(exc)) from None
        self.http = CalendarHTTP(self.label, HOST, self.ALLOWED,
                                 {"Authorization": "Bearer " + token}, opener, max_calls, EXPLAIN)

    @staticmethod
    def _path(listing_id: str, tail: str = "") -> str:
        return "/v2/properties/" + quote(str(listing_id), safe="") + tail

    def read_calendar(self, listing_id, start, end) -> dict:
        raw = self.http.request("GET", self._path(listing_id, "/calendar"),
                                {"start_date": start.isoformat(), "end_date": end.isoformat()})
        data = raw.get("data") if isinstance(raw, dict) else None
        rows = data.get("days") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise CannotWrite("Hospitable calendar has no days list")
        currencies, parsed = set(), []
        for r in rows:
            if not isinstance(r, dict) or not isinstance(r.get("date"), str):
                raise CannotWrite("Hospitable calendar has an unreadable day")
            price = r.get("price")
            if price is not None and not isinstance(price, dict):
                raise CannotWrite(f"Hospitable price on {r['date']} is unreadable")
            if price and price.get("currency"):
                currencies.add(str(price["currency"]).upper())
            parsed.append((r, price))
        if len(currencies) != 1:
            raise CannotWrite("Hospitable calendar does not carry exactly one currency"
                              if currencies else "Hospitable calendar carries no currency")
        currency = currencies.pop()
        days = {}
        for r, price in parsed:
            d = r["date"]
            if d in days:
                raise CannotWrite(f"Hospitable returned {d} twice")
            amount = (price or {}).get("amount")
            status = r.get("status") if isinstance(r.get("status"), dict) else {}
            ms = r.get("min_stay")
            days[d] = {
                "price": None if amount is None else from_minor(amount, currency),
                "min_stay": ms if isinstance(ms, int) and not isinstance(ms, bool) else None,
                "available": status.get("available") if isinstance(status.get("available"), bool) else None,
            }
        return {"currency": currency, "days": days}

    def write_calendar(self, listing_id, changes: dict, currency: str) -> None:
        dates = []
        for d in sorted(changes):
            row = {"date": d}
            c = changes[d]
            if "price" in c:
                row["price"] = {"amount": to_minor(c["price"], currency)}
            if "min_stay" in c:
                row["min_stay"] = int(c["min_stay"])
            dates.append(row)
        # 202 {"status": "accepted"} is the documented success. The reply is not trusted
        # either way: the core's re-read decides.
        self.http.request("PUT", self._path(listing_id, "/calendar"), body={"dates": dates})

    def floor(self, listing_id):
        return None  # the documented Property model has no min price field

    def pricing_managed(self, listing_id):
        # The API exposes no dynamic-pricing flag. Hospitable Dynamic Pricing shows up only as a
        # documented 422 on the PUT, which EXPLAIN names.
        return None

    def write_blocker(self, listing_id):
        raw = self.http.request("GET", self._path(listing_id))
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict) or str(data.get("id")) != str(listing_id):
            raise CannotWrite("Hospitable did not return this property")
        if data.get("calendar_restricted") is True:
            return ("Hospitable marks this property calendar_restricted, so its calendar cannot be "
                    "changed through the API (Airbnb is connected with the limited Operations scope). "
                    "Change it on Airbnb directly.")
        return None
