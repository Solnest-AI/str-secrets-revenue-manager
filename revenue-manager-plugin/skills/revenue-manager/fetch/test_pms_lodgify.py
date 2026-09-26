"""Offline contracts for the Lodgify read adapter and calendar write target. No live calls:
fixtures follow the OpenAPI shapes on docs.lodgify.com (read 2026-09-25), values made up.
Nothing here proves live behaviour; the target has never written to a real account."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

from _mvp_pms import analyze, normalize_calendar, normalize_property, normalize_reservation
from _mvp_store import CannotAnalyze
from _mvp_write import CannotWrite
from _pms_lodgify import (
    LodgifyCalendarTarget, LodgifyError, LodgifySource, day_row, night_statuses, property_row, reservation_row,
    single_room,
)
from test_pms_hostaway import FakeOpener

PROP = {"id": 5501, "name": "Lake Cabin", "internal_name": "LC-1", "city": "Lake Town", "country": "United States",
        "currency_code": "USD", "rooms": [{"id": 77, "name": "Whole house"}], "is_active": False,
        "agreement_text": "SECRET CONTRACT", "min_price": 12.0}
ROOM = {"id": 77, "name": "Whole house", "max_people": 8, "bedrooms": 3, "bathrooms": 2, "units": 1,
        "description": "SECRET TEXT", "min_price": 12.0}
BOOK = {"id": 9001, "user_id": 1, "property_id": 5501, "arrival": "2026-10-05", "departure": "2026-10-08",
        "status": "Booked", "source": "AirbnbIntegration", "created_at": "2026-09-01T12:00:00Z", "canceled_at": None,
        "is_deleted": False, "currency_code": "USD", "total_amount": 900.0,
        "subtotals": {"stay": 600.0, "fees": 150.0, "taxes": 90.0, "promotions": 0},
        "guest": {"name": "SHOULD NEVER APPEAR", "email": "g@x", "phone": "1"},
        "rooms": [{"room_type_id": 77, "guest_breakdown": {"adults": 2}, "key_code": "SECRET-KEY"}]}


def item(d, price=180.0, stay=2, **extra):
    return {"date": f"{d}T00:00:00", "is_default": False,
            "prices": [{"min_stay": stay, "max_stay": 30, "price_per_day": price, "price_per_additional_guest": 15.0,
                        "additional_guests_starts_from": 5, **extra}]}


def period(first, last, available=1, closed=None, **extra):
    return {"property_id": 5501, "room_type_id": 77, "period_start": f"{first}T00:00:00", "period_end": f"{last}T00:00:00",
            "available": available, "total_units": 1, "is_available": bool(available) and closed is None,
            "booking_ids": [] if available else [9001], "closed_period_id": closed, **extra}


def rates(items, currency="USD"):
    return {"calendar_items": items, "rate_settings": {"currency_code": currency, "bookability": 0}}


class Property(unittest.TestCase):
    def test_maps_to_the_runner_shape_without_guessing(self):
        p = normalize_property(property_row(PROP, ROOM))
        self.assertEqual((p["id"], p["name"], p["currency"]), ("5501", "LC-1", "USD"))
        self.assertEqual(p["capacity"]["max"], 8.0)
        self.assertIsNone(p["timezone"], "no timezone in the documented schema")
        self.assertIsNone(p["listed"], "is_active means 'linked to a website', not listed")
        self.assertEqual(p["listings"], [])
        self.assertNotIn("SECRET", json.dumps(property_row(PROP, ROOM)))

    def test_only_one_single_unit_room_type_is_readable(self):
        self.assertEqual(single_room([ROOM])["id"], 77)
        self.assertIsNone(single_room([ROOM, dict(ROOM, id=78)]))
        self.assertIsNone(single_room([dict(ROOM, units=4)]))
        self.assertIsNone(single_room([]))


class Calendar(unittest.TestCase):
    def test_period_end_is_inclusive_and_overlap_is_unknown(self):
        st = night_statuses([period("2026-10-01", "2026-10-02"), period("2026-10-03", "2026-10-04", available=0),
                             period("2026-10-05", "2026-10-05", available=0, closed=31),
                             period("2026-10-05", "2026-10-06")], date(2026, 10, 1), date(2026, 10, 6))
        self.assertEqual([st[f"2026-10-0{d}"] for d in range(1, 7)],
                         ["AVAILABLE", "AVAILABLE", "RESERVED", "RESERVED", "UNKNOWN", "AVAILABLE"])

    def test_multi_unit_period_is_unknown(self):
        st = night_statuses([period("2026-10-01", "2026-10-01", total_units=3)], date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(st["2026-10-01"], "UNKNOWN")

    def test_rows_in_cents_with_restrictions_unknown(self):
        rows = normalize_calendar([day_row(item("2026-10-01", 151.255), "AVAILABLE", "USD"),
                                   day_row(item("2026-10-02"), "RESERVED", "USD")])
        self.assertEqual([r["price_cents"] for r in rows], [15126, 18000])
        self.assertEqual([r["date"] for r in rows], ["2026-10-01", "2026-10-02"])
        self.assertEqual([r["available"] for r in rows], [True, False])
        self.assertEqual({r["closed_for_checkin"] for r in rows}, {None}, "Lodgify documents no CTA/CTD")

    def test_length_of_stay_tiers_have_no_single_price(self):
        tiered = {"date": "2026-10-01", "prices": [{"min_stay": 1, "price_per_day": 200}, {"min_stay": 7, "price_per_day": 150}]}
        row = day_row(tiered, "AVAILABLE", "USD")
        self.assertEqual((row["price_cents"], row["min_stay"]), (None, None))


class Reservations(unittest.TestCase):
    def test_stay_subtotal_is_the_room_revenue_and_no_guest_data(self):
        r = normalize_reservation(reservation_row(BOOK))
        self.assertEqual(r["financials"]["host_accommodation_cents"], 60000)
        self.assertEqual((r["status"], r["platform"], r["property_ids"], r["nights"]), ("accepted", "airbnb", ["5501"], 3))
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(reservation_row(BOOK)))
        self.assertNotIn("SECRET", json.dumps(reservation_row(BOOK)))

    def test_undocumented_money_shapes_stay_unknown(self):
        for sub in ({"stay": {"amount": 600}}, {"stay": 600, "promotions": -50}, {"stay": 600, "promotions": {"x": 1}},
                    {}, None):
            with self.subTest(sub=sub):
                self.assertIsNone(reservation_row(dict(BOOK, subtotals=sub))["financials"]["host_accommodation_cents"])

    def test_status_words_and_cancellation(self):
        for theirs, ours in (("Booked", "accepted"), ("Tentative", "request"), ("Open", "inquiry"),
                             ("Declined", "not accepted"), ("Mystery", "unknown")):
            with self.subTest(theirs=theirs):
                self.assertEqual(normalize_reservation(reservation_row(dict(BOOK, status=theirs)))["status"], ours)
        self.assertEqual(reservation_row(dict(BOOK, canceled_at="2026-09-10T00:00:00Z"))["status"], "cancelled")


# ------------------------------------------------------------------------------ source with a fake ReadClient

class FakeClient:
    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def request(self, provider, op, url, headers=None):
        assert provider == "lodgify" and headers["X-ApiKey"] == "k"
        self.urls.append(url)
        handler = self.routes[urlsplit(url).path]
        return (handler(parse_qs(urlsplit(url).query)) if callable(handler) else handler), {}

    def fetch(self, source, ident, loader, ttl_seconds=0):
        return loader()


class Conn:
    values = {"LODGIFY_API_KEY": "k"}
    paths = {}

    def account(self, p):
        return "acct"


class SourceReads(unittest.TestCase):
    def test_properties_paginate_and_read_capacity_from_the_room(self):
        pages = {"1": {"count": 3, "items": [dict(PROP, id=1), dict(PROP, id=2)]}, "2": {"count": 3, "items": [dict(PROP, id=3)]}}
        routes = {"/v2/properties": lambda q: pages[q["page"][0]], "/v2/properties/1/rooms": [ROOM],
                  "/v2/properties/2/rooms": [ROOM, dict(ROOM, id=78)], "/v2/properties/3/rooms": [ROOM]}
        src = LodgifySource(FakeClient(routes), Conn())
        src_pages = src._pages("/v2/properties", {}, 2)
        self.assertEqual(([r["id"] for r in src_pages[0]], src_pages[1]), ([1, 2, 3], True))

    def test_property_reads_rooms_for_the_match_only(self):
        client = FakeClient({"/v2/properties": {"count": 2, "items": [PROP, dict(PROP, id=5502, internal_name="Other")]},
                             "/v2/properties/5501/rooms": [ROOM]})
        p = LodgifySource(client, Conn()).property("lc-1")
        self.assertEqual((p["id"], p["capacity"]["max"]), ("5501", 8.0))
        self.assertEqual(sum("/rooms" in u for u in client.urls), 1)

    def test_bookings_are_read_account_wide_and_filtered_here(self):
        rows = [BOOK, dict(BOOK, id=2, property_id=999), dict(BOOK, id=3, is_deleted=True),
                dict(BOOK, id=4, canceled_at="2026-09-10T00:00:00Z")]
        client = FakeClient({"/v2/reservations/bookings": {"count": 4, "items": rows}})
        out = LodgifySource(client, Conn()).reservations("5501", date(2026, 10, 1), 30)
        self.assertEqual([(r["id"], r["status"]) for r in out["data"]], [("9001", "accepted"), ("4", "cancelled")])
        self.assertEqual((out["total"], out["complete"]), (2, True))
        self.assertIn("stayFilter=All", client.urls[0])

    def test_short_count_is_incomplete(self):
        client = FakeClient({"/v2/reservations/bookings": {"count": 9, "items": [BOOK]}})
        self.assertFalse(LodgifySource(client, Conn()).reservations("5501", date(2026, 10, 1), 30)["complete"])

    def test_calendar_merges_rates_and_availability(self):
        client = FakeClient({"/v2/properties/5501/rooms": [ROOM],
                             "/v2/rates/calendar": rates([item("2026-10-01"), item("2026-10-02"), item("2026-10-03")]),
                             "/v1/availability/5501/77": [period("2026-10-01", "2026-10-01"),
                                                          period("2026-10-02", "2026-10-03", available=0)]})
        rows = LodgifySource(client, Conn()).calendar("5501", date(2026, 10, 1), 3)
        self.assertEqual([r["status_reason"] for r in rows], ["AVAILABLE", "RESERVED", "RESERVED"])
        self.assertIn("houseId=5501", client.urls[1])
        self.assertIn("roomTypeId=77", client.urls[1])

    def test_multi_room_property_is_refused(self):
        client = FakeClient({"/v2/properties/5501/rooms": [ROOM, dict(ROOM, id=78)]})
        with self.assertRaisesRegex(LodgifyError, "single-unit room type"):
            LodgifySource(client, Conn()).calendar("5501", date(2026, 10, 1), 3)

    def test_reviews_are_a_named_gap_not_zero(self):
        with self.assertRaisesRegex(CannotAnalyze, "no reviews endpoint"):
            LodgifySource(FakeClient({}), Conn()).reviews("5501")

    def test_missing_key_is_named(self):
        class NoKey(Conn):
            values = {}
        with self.assertRaisesRegex(LodgifyError, "LODGIFY_API_KEY"):
            LodgifySource(FakeClient({}), NoKey())


class EndToEnd(unittest.TestCase):
    def test_runs_through_the_real_analysis_cancelled_excluded_restrictions_named_as_a_gap(self):
        start = date(2026, 10, 4)
        st = {f"2026-10-{d:02d}": ("RESERVED" if d in (5, 6, 7) else "AVAILABLE") for d in range(4, 14)}
        cal = [day_row(item(k), v, "USD") for k, v in st.items()]
        cancelled = dict(BOOK, id=9002, arrival="2026-10-10", departure="2026-10-12", canceled_at="2026-09-10T00:00:00Z")
        facts = analyze(property_row(PROP, ROOM), cal, [reservation_row(BOOK), reservation_row(cancelled)], [], start, 10,
                        datetime(2026, 10, 1, tzinfo=timezone.utc))
        codes = {w["code"] for w in facts["warnings"]}
        # Lodgify exposes no closed-to-arrival or closed-to-departure per night. That is a named gap
        # (pms_does_not_expose_arrival_rules), not an unanalysable calendar.
        self.assertIn("pms_does_not_expose_arrival_rules", codes)
        self.assertNotIn("calendar_price_currency_or_restrictions_unknown", codes)
        self.assertFalse(facts["coverage"]["pms_arrival_rules_exposed"])
        self.assertTrue(facts["coverage"]["analysable"])
        self.assertTrue(facts["coverage"]["reservation_source_trusted"])
        self.assertEqual(facts["coverage"]["scoped_unique_records"], 2)


# ------------------------------------------------------------------------------ write target with fake HTTP

def target(routes, key="k"):
    base = {("GET", "/v2/properties/5501/rooms"): (200, [ROOM])}
    opener = FakeOpener({**base, **routes})
    class C:
        values = {"LODGIFY_API_KEY": key} if key else {}
    return LodgifyCalendarTarget(C(), opener=opener), opener


class TargetRead(unittest.TestCase):
    def test_fresh_read_in_major_units_with_availability(self):
        t, op = target({("GET", "/v2/rates/calendar"): (200, rates([item("2026-10-01", 225.5), item("2026-10-02"),
                                                                      {"date": "2026-10-03T00:00:00", "prices": []}])),
                        ("GET", "/v1/availability/5501/77"): (200, [period("2026-10-01", "2026-10-01"),
                                                                    period("2026-10-02", "2026-10-03", available=0, closed=4)])})
        cal = t.read_calendar("5501", date(2026, 10, 1), date(2026, 10, 3))
        self.assertEqual(cal, {"currency": "USD", "days": {
            "2026-10-01": {"price": 225.5, "min_stay": 2, "available": True},
            "2026-10-02": {"price": 180.0, "min_stay": 2, "available": False},
            "2026-10-03": {"price": None, "min_stay": None, "available": False}}})
        rq = [r for r in op.requests if r["path"] == "/v2/rates/calendar"][0]
        self.assertEqual((rq["query"]["startDate"], rq["query"]["endDate"]), (["2026-10-01"], ["2026-10-03"]))
        self.assertEqual(rq["headers"]["x-apikey"], "k")

    def test_missing_dates_tiers_and_no_currency_are_refused(self):
        cases = (rates([item("2026-10-01")]),
                 rates([item("2026-10-01"), {"date": "2026-10-02", "prices": [{"price_per_day": 1}, {"price_per_day": 2}]}]),
                 rates([item("2026-10-01"), item("2026-10-02")], currency=None),
                 rates([item("2026-10-01"), item("2026-10-02", price="x")]))
        for body in cases:
            with self.subTest(body=str(body)[:60]):
                t, _ = target({("GET", "/v2/rates/calendar"): (200, body), ("GET", "/v1/availability/5501/77"): (200, [])})
                with self.assertRaises(CannotWrite):
                    t.read_calendar("5501", date(2026, 10, 1), date(2026, 10, 2))


class TargetWrite(unittest.TestCase):
    def routes(self, post=(200, True), cal=None):
        return {("GET", "/v2/rates/calendar"): (200, cal or rates([item("2026-10-01"), item("2026-10-02"), item("2026-10-03")])),
                ("POST", "/v1/rates/savewithoutavailability"): post}

    def test_vendor_shape_exclusive_end_and_untouched_fields_carried(self):
        t, op = target(self.routes())
        t.write_calendar("5501", {"2026-10-03": {"min_stay": 4}, "2026-10-01": {"price": 199.999}}, "USD")
        post = [r for r in op.requests if r["method"] == "POST"]
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]["body"], {"property_id": 5501, "room_type_id": 77, "rates": [
            {"is_default": False, "start_date": "2026-10-01", "end_date": "2026-10-02", "price_per_day": 200.0, "min_stay": 2,
             "max_stay": 30, "additional_guests_starts_from": 5, "price_per_additional_guest": 15.0},
            {"is_default": False, "start_date": "2026-10-03", "end_date": "2026-10-04", "price_per_day": 180.0, "min_stay": 4,
             "max_stay": 30, "additional_guests_starts_from": 5, "price_per_additional_guest": 15.0}]})
        self.assertEqual(post[0]["headers"]["content-type"], "application/json")

    def test_documented_price_minimum_and_tiers_are_refused_before_sending(self):
        t, op = target(self.routes())
        with self.assertRaisesRegex(CannotWrite, "at least 1"):
            t.write_calendar("5501", {"2026-10-01": {"price": 0.5}}, "USD")
        tiered = rates([{"date": "2026-10-01", "prices": [{"price_per_day": 1}, {"price_per_day": 2}]}])
        t, op2 = target(self.routes(cal=tiered))
        with self.assertRaisesRegex(CannotWrite, "rate tiers"):
            t.write_calendar("5501", {"2026-10-01": {"price": 150}}, "USD")
        nightless = rates([{"date": "2026-10-01", "prices": []}])
        t, op3 = target(self.routes(cal=nightless))
        with self.assertRaisesRegex(CannotWrite, "at least 1"):
            t.write_calendar("5501", {"2026-10-01": {"min_stay": 3}}, "USD")
        self.assertFalse(any(r["method"] == "POST" for r in op.requests + op2.requests + op3.requests))

    def test_answer_other_than_true_is_a_failure_and_never_retried(self):
        for post in ((200, False), (200, {"message": "SECRET"}), (400, {}), (500, {}), (429, {})):
            with self.subTest(post=post):
                t, op = target(self.routes(post=post))
                with self.assertRaises(CannotWrite) as ctx:
                    t.write_calendar("5501", {"2026-10-01": {"price": 150}}, "USD")
                self.assertNotIn("SECRET", str(ctx.exception))
                self.assertEqual(sum(r["method"] == "POST" for r in op.requests), 1)
        t, _ = target(self.routes(post=(500, {})))
        with self.assertRaises(CannotWrite) as ctx:
            t.write_calendar("5501", {"2026-10-01": {"price": 150}}, "USD")
        self.assertEqual(str(ctx.exception), "Lodgify POST /v1/rates/savewithoutavailability: HTTP 500")

    def test_currency_room_and_input_refusals(self):
        t, op = target(self.routes())
        with self.assertRaisesRegex(CannotWrite, "priced in USD"):
            t.write_calendar("5501", {"2026-10-01": {"price": 150}}, "EUR")
        t, op = target({**self.routes(), ("GET", "/v2/properties/5501/rooms"): (200, [ROOM, dict(ROOM, id=78)])})
        with self.assertRaisesRegex(CannotWrite, "single-unit room type"):
            t.write_calendar("5501", {"2026-10-01": {"price": 150}}, "USD")
        for changes in ({}, {"2026-10-01": {"price": -1}}, {"2026-10-01": {"min_stay": 0}}, {"2026-10-01": {"note": "x"}}):
            t, op = target(self.routes())
            with self.subTest(changes=changes), self.assertRaises(CannotWrite):
                t.write_calendar("5501", changes, "USD")
            self.assertEqual(op.requests, [])
        with self.assertRaisesRegex(CannotWrite, "LODGIFY_API_KEY"):
            target({}, key=None)

    def test_transport_refuses_everything_outside_allowed(self):
        t, op = target({})
        for method, path in (("POST", "/v1/rates/save"), ("POST", "/v2/rates/calendar"), ("DELETE", "/v1/reservation/booking/1"),
                             ("PUT", "/v1/reservation/booking/1"), ("POST", "/v1/availability/5501/77/set"),
                             ("GET", "/v2/properties/5501/../../reservations/bookings")):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                t._call(method, path)
        self.assertEqual(op.requests, [])

    def test_floor_and_pricing_tool_are_not_documented(self):
        t, op = target({})
        self.assertIsNone(t.floor("5501"))
        self.assertIsNone(t.pricing_managed("5501"))
        self.assertEqual(op.requests, [])
        self.assertEqual((t.name, t.host), ("lodgify", "api.lodgify.com"))


if __name__ == "__main__":
    unittest.main()
