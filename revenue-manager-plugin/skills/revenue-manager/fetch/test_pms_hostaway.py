"""Offline contracts for the Hostaway read adapter and calendar write target. No live calls:
fixtures follow the shapes in api.hostaway.com/documentation (read 2026-09-25), values made up.
Nothing here proves live behaviour; the target has never written to a real account."""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from _mvp_pms import analyze, normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _mvp_store import CannotAnalyze
from _mvp_write import CannotWrite
from _pms_hostaway import (
    HostawayCalendarTarget, HostawayError, HostawaySource, day_row, get_token, property_row,
    reservation_row, review_row, token_from_cache,
)
from _pms_target_kit import NoRedirect

LISTING = {"id": 40160, "name": "Lake House", "internalListingName": "LH-1", "externalListingName": "Lake House",
           "currencyCode": "USD", "timeZoneName": "America/Denver", "personCapacity": 6, "bedroomsNumber": 2,
           "bedsNumber": 3, "bathroomsNumber": 2, "city": "Boulder", "country": "United States",
           "airbnbListingUrl": "https://www.airbnb.com/rooms/1234567890123", "specialStatus": None,
           "doorSecurityCode": "SECRET-DOOR", "wifiPassword": "SECRET-WIFI", "contactEmail": "SECRET@MAIL"}
RES = {"id": 13, "listingMapId": 40160, "channelId": 2018, "status": "new", "arrivalDate": "2026-10-05",
       "departureDate": "2026-10-08", "nights": 3, "reservationDate": "2026-09-01 12:00:00", "currency": "USD",
       "totalPrice": 900.0, "guestName": "SHOULD NEVER APPEAR", "guestEmail": "g@x", "doorCode": "SECRET-DOOR",
       "financeField": [{"type": "price", "name": "baseRate", "total": 600.0, "isDeleted": 0},
                        {"type": "fee", "name": "cleaningFee", "total": 150.0, "isDeleted": 0},
                        {"type": "tax", "name": "vat", "total": 90.0, "isDeleted": 0}]}


def cday(d, status="available", price=225, stay=2, **extra):
    row = {"id": 1, "date": d, "isAvailable": 0 if status != "available" else 1, "status": status, "price": price,
           "minimumStay": stay, "maximumStay": 1125, "closedOnArrival": None, "closedOnDeparture": None,
           "countAvailableUnits": None, "availableUnitsToSell": None, "countPendingUnits": None,
           "countBlockedUnits": None, "reservations": []}
    row.update(extra)
    return row


def ok(result, **meta):
    return {"status": "success", "result": result, **meta}


class Property(unittest.TestCase):
    def test_maps_to_the_runner_shape_and_drops_secrets(self):
        p = normalize_property(property_row(LISTING))
        self.assertEqual((p["id"], p["name"], p["currency"], p["timezone"]), ("40160", "LH-1", "USD", "America/Denver"))
        self.assertEqual(p["capacity"], {"max": 6, "bedrooms": 2, "beds": 3, "bathrooms": 2})
        self.assertIn({"platform": "airbnb", "platform_id": "1234567890123"}, p["listings"])
        self.assertTrue(p["listed"])
        self.assertNotIn("SECRET", json.dumps(property_row(LISTING)))

    def test_archived_is_not_listed_and_missing_currency_refused(self):
        self.assertFalse(normalize_property(property_row(dict(LISTING, specialStatus="archived")))["listed"])
        with self.assertRaises(HostawayError):
            property_row(dict(LISTING, currencyCode=None))


class Calendar(unittest.TestCase):
    def test_whole_units_become_cents_and_status_maps(self):
        rows = normalize_calendar([day_row(cday("2026-10-01"), "USD"), day_row(cday("2026-10-02", "reserved"), "USD"),
                                   day_row(cday("2026-10-03", "blocked"), "USD"), day_row(cday("2026-10-04", "pending"), "USD"),
                                   day_row(cday("2026-10-05", price=151.255), "USD")])
        self.assertEqual([r["status_reason"] for r in rows], ["AVAILABLE", "RESERVED", "BLOCKED", "RESERVED", "AVAILABLE"])
        self.assertEqual([r["price_cents"] for r in rows], [22500, 22500, 22500, 22500, 15126])
        self.assertEqual([r["available"] for r in rows], [True, False, False, False, True])
        self.assertEqual({r["currency"] for r in rows}, {"USD"})

    def test_closed_flags_documented_null_is_not_closed_absent_is_unknown(self):
        self.assertEqual(day_row(cday("2026-10-01", closedOnArrival=1), "USD")["closed_for_checkin"], True)
        self.assertEqual(day_row(cday("2026-10-01"), "USD")["closed_for_checkin"], False)
        raw = cday("2026-10-01"); del raw["closedOnDeparture"]
        self.assertIsNone(day_row(raw, "USD")["closed_for_checkout"])

    def test_status_and_isavailable_disagreeing_is_unknown(self):
        self.assertEqual(day_row(cday("2026-10-01", isAvailable=0), "USD")["status_reason"], "UNKNOWN")
        self.assertEqual(day_row(cday("2026-10-01", "conflicted"), "USD")["status_reason"], "UNKNOWN")

    def test_missing_price_is_unknown_not_zero(self):
        self.assertIsNone(day_row(cday("2026-10-01", price=None), "USD")["price_cents"])


class Reservations(unittest.TestCase):
    def test_base_rate_is_the_room_revenue_and_no_guest_data(self):
        r = normalize_reservation(reservation_row(RES))
        self.assertEqual(r["financials"]["host_accommodation_cents"], 60000)
        self.assertEqual((r["status"], r["platform"], r["property_ids"]), ("accepted", "airbnb", ["40160"]))
        self.assertEqual(r["booking_date"], "2026-09-01T12:00:00+00:00")
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(reservation_row(RES)))
        self.assertNotIn("SECRET", json.dumps(reservation_row(RES)))

    def test_unknown_money_stays_unknown(self):
        disc = RES["financeField"] + [{"type": "discount", "name": "weeklyDiscount", "total": 50.0, "isDeleted": 0}]
        for ff in (None, [], disc, [{"type": "price", "name": "baseRate", "total": "x"}]):
            with self.subTest(ff=ff):
                self.assertIsNone(normalize_reservation(reservation_row(dict(RES, financeField=ff)))["financials"]["host_accommodation_cents"])
        deleted = [{"type": "price", "name": "baseRate", "total": 1.0, "isDeleted": 1}, RES["financeField"][0]]
        self.assertEqual(reservation_row(dict(RES, financeField=deleted))["financials"]["host_accommodation_cents"], 60000)

    def test_status_words(self):
        for theirs, ours in (("new", "accepted"), ("modified", "accepted"), ("cancelled", "cancelled"),
                             ("pending", "request"), ("awaitingPayment", "request"), ("inquiry", "inquiry"),
                             ("inquiryPreapproved", "inquiry"), ("declined", "not accepted"), ("expired", "not accepted"),
                             ("somethingNew", "unknown")):
            with self.subTest(theirs=theirs):
                self.assertEqual(normalize_reservation(reservation_row(dict(RES, status=theirs)))["status"], ours)

    def test_owner_stay_is_zero_value_accepted(self):
        r = normalize_reservation(reservation_row(dict(RES, status="ownerStay", financeField=[])))
        self.assertEqual((r["status"], r["owner_stay"], r["financials"]["host_accommodation_cents"]), ("accepted", True, 0))

    def test_unspecified_dates_are_not_trusted(self):
        r = reservation_row(dict(RES, isDatesUnspecified=1))
        self.assertIsNone(r["check_in"])


class Reviews(unittest.TestCase):
    def test_zero_to_ten_scale_becomes_five_and_text_dropped(self):
        r = normalize_review(review_row({"id": 1, "listingMapId": 40160, "channelId": 2018, "rating": 9,
                                         "submittedAt": "2026-09-04 10:00:00", "publicReview": "SECRET TEXT"}))
        self.assertEqual((r["rating"], r["rating_platform_original"], r["platform"]), (4.5, 9, "airbnb"))
        self.assertEqual(r["reviewed_at"], "2026-09-04T10:00:00+00:00")
        self.assertNotIn("SECRET", json.dumps(review_row({"id": 1, "rating": 10, "publicReview": "SECRET"})))
        self.assertIsNone(review_row({"id": 2, "rating": None})["rating"])


# ------------------------------------------------------------------------------ source with a fake ReadClient

class FakeClient:
    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def request(self, provider, op, url, headers=None):
        self.urls.append(url)
        handler = self.routes[urlsplit(url).path]
        body = handler(parse_qs(urlsplit(url).query)) if callable(handler) else handler
        if isinstance(body, Exception):
            raise body
        return body, {}

    def fetch(self, source, ident, loader, ttl_seconds=0):
        return loader()


class Conn:
    def __init__(self, values=None, paths=None):
        self.values = values if values is not None else {"HOSTAWAY_ACCOUNT_ID": "12345", "HOSTAWAY_API_KEY": "k"}
        self.paths = paths or {}

    def account(self, p):
        return "acct"

    account_or = account


def source(routes):
    src = HostawaySource(FakeClient(routes), Conn(), sleep=lambda s: None)
    src._token = "t"
    return src


class SourceReads(unittest.TestCase):
    def test_listings_paginate_until_count(self):
        pages = {"0": ok([dict(LISTING, id=1), dict(LISTING, id=2)], count=3), "2": ok([dict(LISTING, id=3)], count=3)}
        src = source({"/v1/listings": lambda q: pages[q["offset"][0]]})
        inv = src._offset_paged("/listings", {}, 2)
        self.assertEqual(([r["id"] for r in inv[0]], inv[1], inv[2]), ([1, 2, 3], True, 3))

    def test_status_fail_envelope_is_refused(self):
        src = source({"/v1/listings": {"status": "fail", "result": "nope"}})
        with self.assertRaises(HostawayError):
            src.inventory()

    def test_reservations_use_the_afterid_cursor_and_refuse_foreign_rows(self):
        first = [dict(RES, id=1000 - i) for i in range(100)]
        pages = {None: ok(first, count=101), "901": ok([dict(RES, id=5)], count=101)}
        client = FakeClient({"/v1/reservations": lambda q: pages[q.get("afterId", [None])[0]]})
        src = HostawaySource(client, Conn(), sleep=lambda s: None); src._token = "t"
        out = src.reservations("40160", date(2026, 10, 1), 30)
        self.assertEqual((len(out["data"]), out["total"], out["complete"]), (101, 101, True))
        self.assertTrue(all("listingId=40160" in u for u in client.urls))
        bad = source({"/v1/reservations": ok([dict(RES, listingMapId=999)], count=1)})
        with self.assertRaisesRegex(HostawayError, "scope"):
            bad.reservations("40160", date(2026, 10, 1), 30)

    def test_a_cursor_that_does_not_move_stops_and_reports_incomplete(self):
        stuck = ok([dict(RES, id=1000 - i) for i in range(100)], count=250)
        client = FakeClient({"/v1/reservations": stuck})
        src = HostawaySource(client, Conn(), sleep=lambda s: None); src._token = "t"
        out = src.reservations("40160", date(2026, 10, 1), 30)
        self.assertEqual((len(client.urls), out["complete"]), (2, False))

    def test_reservation_count_mismatch_is_incomplete(self):
        src = source({"/v1/reservations": ok([RES], count=5)})
        self.assertFalse(src.reservations("40160", date(2026, 10, 1), 30)["complete"])

    def test_calendar_asks_one_extra_day_and_keeps_only_the_window(self):
        days = [cday(f"2026-10-{d:02d}") for d in range(1, 5)]
        client = FakeClient({"/v1/listings/40160": ok(LISTING), "/v1/listings/40160/calendar": ok(days)})
        src = HostawaySource(client, Conn(), sleep=lambda s: None); src._token = "t"
        rows = src.calendar("40160", date(2026, 10, 1), 3)
        self.assertEqual([r["date"] for r in rows], ["2026-10-01", "2026-10-02", "2026-10-03"])
        self.assertIn("endDate=2026-10-04", client.urls[-1])

    def test_multi_unit_calendar_is_refused(self):
        src = source({"/v1/listings/40160": ok(LISTING),
                      "/v1/listings/40160/calendar": ok([cday("2026-10-01", countAvailableUnits=3)])})
        with self.assertRaisesRegex(HostawayError, "multi-unit"):
            src.calendar("40160", date(2026, 10, 1), 1)

    def test_reviews_are_filtered_to_this_listing_locally(self):
        rows = [{"id": 1, "listingMapId": 40160, "rating": 10, "submittedAt": "2026-09-01 00:00:00", "isCancelled": 0},
                {"id": 2, "listingMapId": 999, "rating": 2, "submittedAt": "2026-09-01 00:00:00", "isCancelled": 0},
                {"id": 3, "listingMapId": 40160, "rating": 6, "submittedAt": "2026-09-01 00:00:00", "isCancelled": 1}]
        out = source({"/v1/reviews": ok(rows, count=3)}).reviews("40160")
        self.assertEqual([r["id"] for r in out["data"]], ["1"])

    def test_a_cached_token_refused_with_403_is_minted_once(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".cache").mkdir()
            (Path(d) / ".cache" / "hostaway.token").write_text("stale")
            calls = []
            def listings(q):
                calls.append(1)
                return CannotAnalyze("hostaway listings: HTTP 403") if len(calls) == 1 else ok([LISTING], count=1)
            import _pms_hostaway as mod
            real = mod._mint_direct
            mod._mint_direct = lambda aid, key, opener=None: "fresh"
            try:
                src = HostawaySource(FakeClient({"/v1/listings": listings}), Conn(paths={"hostaway": [d]}), sleep=lambda s: None)
                self.assertEqual(len(src.inventory()["data"]), 1)
            finally:
                mod._mint_direct = real
            self.assertEqual((Path(d) / ".cache" / "hostaway.token").read_text(), "fresh")


class TokenCache(unittest.TestCase):
    def test_raw_cache_used_while_fresh_json_uses_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "hostaway.token"; p.write_text("tok-raw")
            self.assertEqual(token_from_cache(p), "tok-raw")
            old = time.time() - 24 * 3600; os.utime(p, (old, old))
            self.assertIsNone(token_from_cache(p))
            p.write_text(json.dumps({"access_token": "tok-json", "expires_at": time.time() + 3600}))
            self.assertEqual(token_from_cache(p), "tok-json")

    def test_mint_waits_the_documented_second_and_never_creates_folders(self):
        waits = []
        with tempfile.TemporaryDirectory() as d:
            tok, cached = get_token(Conn(paths={"hostaway": [d]}), lambda a, k: "minted", sleep=waits.append)
            self.assertEqual((tok, cached, waits), ("minted", False, [1]))
            self.assertFalse((Path(d) / ".cache").exists())
        with self.assertRaises(HostawayError):
            get_token(Conn(values={}), lambda a, k: "x", sleep=waits.append)


class EndToEnd(unittest.TestCase):
    def test_cancelled_booking_is_excluded_from_the_real_analysis(self):
        start = date(2026, 10, 4)
        cal = [day_row(cday(f"2026-10-{d:02d}", "reserved" if d in (5, 6, 7) else "available"), "USD") for d in range(4, 14)]
        cancelled = dict(RES, id=14, status="cancelled", arrivalDate="2026-10-10", departureDate="2026-10-12", nights=2)
        facts = analyze(property_row(LISTING), cal, [reservation_row(RES), reservation_row(cancelled)], [], start, 10,
                        datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertTrue(facts["coverage"]["analysable"], facts["warnings"])
        full = facts["windows"][-1]
        self.assertEqual((full["confirmed_paid_nights"], full["open_nights"]), (3, 7))
        self.assertEqual(full["on_books_accommodation_cents"], 60000)


# ------------------------------------------------------------------------------ write target with fake HTTP

class Resp:
    def __init__(self, status, raw):
        self.status, self._raw = status, raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Routes (METHOD, path) -> (status, body) or callable(request_record) -> (status, body)."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, req, timeout=None):
        u = urlsplit(req.full_url)
        rec = {"method": req.get_method(), "path": u.path, "query": parse_qs(u.query), "timeout": timeout,
               "headers": {k.lower(): v for k, v in req.header_items()},
               "body": json.loads(req.data) if req.data and req.data[:1] in (b"{", b"[") else req.data}
        self.requests.append(rec)
        route = self.routes.get((rec["method"], rec["path"]))
        if route is None:
            raise AssertionError(f"unexpected {rec['method']} {rec['path']}")
        status, body = route(rec) if callable(route) else route
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(b'{"echo":"BODY-SECRET"}'))
        return Resp(status, body if isinstance(body, bytes) else json.dumps(body).encode())


def target(routes, values=None, paths=None):
    base = {("POST", "/v1/accessTokens"): (200, {"token_type": "Bearer", "expires_in": 15897600, "access_token": "tok"}),
            ("GET", "/v1/listings/40160"): (200, ok(LISTING))}
    opener = FakeOpener({**base, **routes})
    return HostawayCalendarTarget(Conn(values, paths), opener=opener, sleep=lambda s: None), opener


class TargetRead(unittest.TestCase):
    def test_fresh_read_in_major_units_with_availability(self):
        days = [cday("2026-10-01", price=225.5), cday("2026-10-02", "reserved"), cday("2026-10-03", "blocked", price=None),
                cday("2026-10-04")]
        t, op = target({("GET", "/v1/listings/40160/calendar"): (200, ok(days))})
        cal = t.read_calendar("40160", date(2026, 10, 1), date(2026, 10, 3))
        self.assertEqual(cal["currency"], "USD")
        self.assertEqual(cal["days"], {"2026-10-01": {"price": 225.5, "min_stay": 2, "available": True},
                                       "2026-10-02": {"price": 225.0, "min_stay": 2, "available": False},
                                       "2026-10-03": {"price": None, "min_stay": 2, "available": False}})
        self.assertEqual(op.requests[-1]["query"]["endDate"], ["2026-10-04"])
        self.assertEqual(op.requests[0]["path"], "/v1/accessTokens")
        self.assertEqual(op.requests[1]["headers"]["authorization"], "Bearer tok")

    def test_missing_or_unreadable_days_are_refused_not_partial(self):
        for days in ([cday("2026-10-01")], [cday("2026-10-01"), cday("2026-10-02", price="x")],
                     [cday("2026-10-01"), cday("2026-10-01"), cday("2026-10-02")]):
            with self.subTest(days=len(days)):
                t, _ = target({("GET", "/v1/listings/40160/calendar"): (200, ok(days))})
                with self.assertRaises(CannotWrite):
                    t.read_calendar("40160", date(2026, 10, 1), date(2026, 10, 2))

    def test_multi_unit_is_refused(self):
        t, _ = target({("GET", "/v1/listings/40160/calendar"): (200, ok([cday("2026-10-01", countAvailableUnits=2)]))})
        with self.assertRaisesRegex(CannotWrite, "multi-unit"):
            t.read_calendar("40160", date(2026, 10, 1), date(2026, 10, 1))

    def test_403_on_a_cached_token_read_mints_once(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".cache").mkdir(); (Path(d) / ".cache" / "hostaway.token").write_text("stale")
            seen = []
            def listing(rec):
                seen.append(rec["headers"]["authorization"])
                return (403, {}) if len(seen) == 1 else (200, ok(LISTING))
            t, op = target({("GET", "/v1/listings/40160"): listing,
                            ("GET", "/v1/listings/40160/calendar"): (200, ok([cday("2026-10-01")]))}, paths={"hostaway": [d]})
            t.read_calendar("40160", date(2026, 10, 1), date(2026, 10, 1))
            self.assertEqual(seen, ["Bearer stale", "Bearer tok"])


class TargetWrite(unittest.TestCase):
    def put_ok(self):
        return {("PUT", "/v1/listings/40160/calendarIntervals"): (200, {"status": "success", "result": []})}

    def test_single_day_intervals_in_the_vendor_shape(self):
        t, op = target(self.put_ok())
        t.write_calendar("40160", {"2026-10-02": {"price": 199.999}, "2026-10-01": {"price": 180, "min_stay": 3},
                                   "2026-10-03": {"min_stay": 2}}, "USD")
        put = [r for r in op.requests if r["method"] == "PUT"]
        self.assertEqual(len(put), 1)
        self.assertEqual(put[0]["body"], [{"startDate": "2026-10-01", "endDate": "2026-10-01", "price": 180.0, "minimumStay": 3},
                                          {"startDate": "2026-10-02", "endDate": "2026-10-02", "price": 200.0},
                                          {"startDate": "2026-10-03", "endDate": "2026-10-03", "minimumStay": 2}])
        self.assertEqual(put[0]["headers"]["content-type"], "application/json")
        self.assertEqual(put[0]["timeout"], 60)

    def test_more_than_200_dates_go_in_documented_chunks(self):
        t, op = target(self.put_ok())
        start = date(2026, 1, 1).toordinal()
        t.write_calendar("40160", {date.fromordinal(start + i).isoformat(): {"price": 100} for i in range(250)}, "USD")
        self.assertEqual([len(r["body"]) for r in op.requests if r["method"] == "PUT"], [200, 50])

    def test_a_failed_write_is_never_retried_and_names_no_body(self):
        for status in (500, 403, 429):
            with self.subTest(status=status):
                t, op = target({("PUT", "/v1/listings/40160/calendarIntervals"): (status, {})})
                with self.assertRaises(CannotWrite) as ctx:
                    t.write_calendar("40160", {"2026-10-01": {"price": 100}}, "USD")
                self.assertEqual(str(ctx.exception), f"Hostaway PUT /v1/listings/40160/calendarIntervals: HTTP {status}")
                self.assertNotIn("SECRET", str(ctx.exception))
                self.assertEqual(sum(r["method"] == "PUT" for r in op.requests), 1)

    def test_http_200_with_status_fail_is_a_failure(self):
        t, _ = target({("PUT", "/v1/listings/40160/calendarIntervals"): (200, {"status": "fail", "result": "SECRET"})})
        with self.assertRaises(CannotWrite) as ctx:
            t.write_calendar("40160", {"2026-10-01": {"price": 100}}, "USD")
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_partial_chunk_failure_says_what_was_already_sent(self):
        n = []
        def put(rec):
            n.append(1)
            return (200, {"status": "success"}) if len(n) == 1 else (500, {})
        t, _ = target({("PUT", "/v1/listings/40160/calendarIntervals"): put})
        start = date(2026, 1, 1).toordinal()
        with self.assertRaisesRegex(CannotWrite, "200 of 201 dates were already sent"):
            t.write_calendar("40160", {date.fromordinal(start + i).isoformat(): {"price": 100} for i in range(201)}, "USD")

    def test_refusals_before_anything_is_sent(self):
        bad = ({}, {"2026-10-01": {}}, {"2026-10-01": {"price": -5}}, {"2026-10-01": {"price": True}},
               {"2026-10-01": {"price": float("nan")}}, {"2026-10-01": {"min_stay": 0}}, {"2026-10-01": {"min_stay": 1.5}},
               {"2026-10-01": {"available": False}}, {"2026-13-01": {"price": 1}}, {"20261001": {"price": 1}})
        for changes in bad:
            with self.subTest(changes=changes):
                t, op = target(self.put_ok())
                with self.assertRaises(CannotWrite):
                    t.write_calendar("40160", changes, "USD")
                self.assertEqual(op.requests, [])

    def test_currency_mismatch_archived_and_bad_ids_are_refused(self):
        t, op = target(self.put_ok())
        with self.assertRaisesRegex(CannotWrite, "priced in USD"):
            t.write_calendar("40160", {"2026-10-01": {"price": 100}}, "CAD")
        t, op = target({**self.put_ok(), ("GET", "/v1/listings/40160"): (200, ok(dict(LISTING, specialStatus="archived")))})
        with self.assertRaisesRegex(CannotWrite, "archived"):
            t.write_calendar("40160", {"2026-10-01": {"price": 100}}, "USD")
        for lid in ("../users", "40160/calendar", "abc"):
            t, op = target(self.put_ok())
            with self.assertRaises(CannotWrite):
                t.write_calendar(lid, {"2026-10-01": {"price": 100}}, "USD")
            self.assertFalse(any(r["method"] == "PUT" for r in op.requests))

    def test_transport_refuses_everything_outside_allowed(self):
        t, op = target({})
        t._token = "tok"
        for method, path in (("DELETE", "/v1/listings/40160"), ("PUT", "/v1/listings/40160"),
                             ("POST", "/v1/reservations"), ("PUT", "/v1/listings/40160/calendar"),
                             ("GET", "/v1/listings/1/../../users"), ("GET", "/v1/users")):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                t._call(method, path)
        self.assertEqual(op.requests, [])

    def test_call_budget_and_no_redirects(self):
        t, _ = target({("GET", "/v1/listings/40160/calendar"): (200, ok([cday("2026-10-01")]))})
        t.transport.max_calls = 2
        with self.assertRaisesRegex(CannotWrite, "budget"):
            t.read_calendar("40160", date(2026, 10, 1), date(2026, 10, 1))
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/"))

    def test_bad_credentials_on_mint_say_so(self):
        t, _ = target({("POST", "/v1/accessTokens"): (401, {})})
        with self.assertRaisesRegex(CannotWrite, "HTTP 401.*create a new pair"):
            t.read_calendar("40160", date(2026, 10, 1), date(2026, 10, 1))

    def test_floor_and_pricing_tool_are_not_documented(self):
        t, op = target({})
        self.assertIsNone(t.floor("40160"))
        self.assertIsNone(t.pricing_managed("40160"))
        self.assertEqual(op.requests, [])
        self.assertEqual((t.name, t.host), ("hostaway", "api.hostaway.com"))


class Registry(unittest.TestCase):
    """Both new PMSs are detected by the env names the connections kit writes, and loaded."""

    def test_env_names_are_read_detected_and_adapted(self):
        from _mvp_config import load_env
        from _pms_lodgify import LodgifySource
        from _pms_registry import SUPPORTED, adapter, choose, connected
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("HOSTAWAY_ACCOUNT_ID=12345\nHOSTAWAY_API_KEY=k\nLODGIFY_API_KEY=l\n")
            self.assertEqual(load_env(env), {"HOSTAWAY_ACCOUNT_ID": "12345", "HOSTAWAY_API_KEY": "k", "LODGIFY_API_KEY": "l"})
        self.assertIn("hostaway", SUPPORTED)
        self.assertIn("lodgify", SUPPORTED)
        self.assertEqual(connected(Conn({"HOSTAWAY_ACCOUNT_ID": "1", "HOSTAWAY_API_KEY": "k"})), ["hostaway"])
        self.assertEqual(connected(Conn({"HOSTAWAY_ACCOUNT_ID": "1"})), [], "half a pair is not a connection")
        self.assertEqual(choose(Conn({"LODGIFY_API_KEY": "l"})), "lodgify")
        self.assertIsInstance(adapter("hostaway", FakeClient({}), Conn()), HostawaySource)
        self.assertIsInstance(adapter("lodgify", FakeClient({}), Conn({"LODGIFY_API_KEY": "l"})), LodgifySource)


if __name__ == "__main__":
    unittest.main()
