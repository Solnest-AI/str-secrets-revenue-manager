"""Offline contracts for the Uplisting adapter and write target. Fixtures follow the shapes in
Uplisting's Postman collection (1320372/SWTBfdW6, read 2026-09-25); values are made up. DOCS-ONLY:
no live Uplisting account has been read or written."""

from __future__ import annotations

import base64
import json
import unittest
from datetime import date, datetime, timezone

from _mvp_pms import analyze
from _mvp_write import CannotWrite
from _pms_fakes import SECRET_BODY, FakeOpener, connections, read_client
from _pms_uplisting import (
    UplistingCalendarTarget, UplistingError, UplistingSource, auth_header, day_row, property_row, reservation_row,
)

KEY = "test-uplisting-key-0001"
PROP = {"id": "11033", "type": "properties",
        "attributes": {"name": "Chic apt with Garden", "nickname": "BDC Double", "currency": "GBP",
                       "time_zone": "Europe/London", "maximum_capacity": 5, "bedrooms": 2, "beds": 2, "bathrooms": 1.0},
        "relationships": {"address": {"data": {"id": "7666", "type": "addresses"}}}}
ADDRESS = {"id": "7666", "type": "addresses", "attributes": {"city": "London", "country": "United Kingdom"}}


def booking(i, check_in, check_out, status="confirmed", total=645.0, pid=11033):
    return {"id": i, "currency": "GBP", "property_name": "BDC Double", "property_id": pid, "check_in": check_in,
            "check_out": check_out, "number_of_nights": (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days,
            "guest_name": "SHOULD NEVER APPEAR", "guest_email": "x@y.z", "lock_code": "1234", "status": status,
            "channel": "airbnb_official", "accomodation_total": total, "cleaning_fee": 100.0,
            "total_payout": 700.49, "booked_at": "2030-01-01T16:48:07Z"}


def day(d, available=True, rate=131.0, mlos=2):
    return {"available": available, "available_count": int(available), "date": d, "day_rate": rate,
            "minimum_length_of_stay": mlos, "maximum_available_nights": 30,
            "closed_for_arrival": False, "closed_for_departure": False}


class Auth(unittest.TestCase):
    def test_base64_of_the_key_alone_no_colon_no_newline(self):
        self.assertEqual(auth_header(KEY + "\n"), "Basic " + base64.b64encode(KEY.encode()).decode())
        self.assertNotIn(base64.b64encode(f"{KEY}:".encode()).decode(), auth_header(KEY))


class Mappers(unittest.TestCase):
    def test_property_from_json_api_with_included_address(self):
        p = property_row(PROP, [ADDRESS])
        self.assertEqual((p["id"], p["name"], p["public_name"], p["currency"], p["timezone"]),
                         ("11033", "BDC Double", "Chic apt with Garden", "GBP", "Europe/London"))
        self.assertEqual(p["capacity"]["max"], 5)
        self.assertEqual(p["address"], {"city": "London", "country": "United Kingdom"})
        self.assertEqual(p["listings"], [], "no Airbnb id is documented on an Uplisting property")

    def test_missing_currency_is_refused(self):
        with self.assertRaises(UplistingError):
            property_row({"id": "1", "attributes": {"name": "x"}})

    def test_reservation_money_status_and_no_guest_data(self):
        r = reservation_row(booking(1, "2030-02-03", "2030-02-05", total=645.255))
        self.assertEqual(r["financials"], {"currency": "GBP", "host_accommodation_cents": 64526, "host_discounts": []})
        self.assertEqual((r["status"], r["platform"], r["property_ids"], r["nights"]), ("accepted", "airbnb", ["11033"], 2))
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(r))
        self.assertNotIn("1234", json.dumps(r))
        for theirs, ours in (("checked_in", "accepted"), ("needs_check_out", "accepted"), ("cancelled", "cancelled"),
                             ("something_new", "unknown")):
            with self.subTest(theirs=theirs):
                self.assertEqual(reservation_row(booking(1, "2030-02-03", "2030-02-05", status=theirs))["status"], ours)

    def test_closed_night_is_reserved_only_inside_a_live_booking(self):
        booked = {"2030-02-03"}
        self.assertEqual(day_row(day("2030-02-03", available=False), "GBP", booked)["status_reason"], "RESERVED")
        self.assertEqual(day_row(day("2030-02-04", available=False), "GBP", booked)["status_reason"], "BLOCKED")
        self.assertEqual(day_row(day("2030-02-05"), "GBP", booked)["status_reason"], "AVAILABLE")
        self.assertEqual(day_row(dict(day("2030-02-05"), available=None), "GBP")["status_reason"], "UNKNOWN")
        self.assertEqual(day_row(day("2030-02-05", rate=131.5), "GBP")["price_cents"], 13150)
        self.assertIsNone(day_row(day("2030-02-05", rate=None), "GBP")["price_cents"], "missing is unknown, not zero")


def routes(pages, calendar_days, prop=PROP):
    def bookings(rec):
        return pages[int(dict(rec["query"])["page"])]
    return {("GET", "/properties"): {"data": [prop], "included": [ADDRESS]},
            ("GET", "/bookings/11033"): bookings,
            ("GET", "/calendar/11033"): {"calendar": {"days": calendar_days}}}


class Source(unittest.TestCase):
    START = date(2030, 2, 1)

    def setUp(self):
        self.pages = [
            {"bookings": [booking(1, "2030-02-03", "2030-02-05"), booking(2, "2030-02-06", "2030-02-08", status="cancelled", total=999.0)],
             "meta": {"total": 3, "total_pages": 2}},
            {"bookings": [booking(3, "2029-02-03", "2029-02-05")], "meta": {"total": 3, "total_pages": 2}},
        ]
        self.cal = [day(f"2030-02-{d:02d}", available=d not in (3, 4, 10)) for d in range(1, 12)]
        self.opener = FakeOpener(routes(self.pages, self.cal))
        self.src = UplistingSource(read_client(self.opener), connections(UPLISTING_API_KEY=KEY))

    def test_every_call_is_a_get_with_the_documented_headers(self):
        self.src.reservations("11033", self.START, 10)
        for r in self.opener.requests:
            self.assertEqual((r["method"], r["host"]), ("GET", "connect.uplisting.io"))
            self.assertEqual(r["headers"]["authorization"], "Basic " + base64.b64encode(KEY.encode()).decode())
            self.assertEqual(r["headers"]["content-type"], "application/json")

    def test_zero_based_pagination_to_the_end_and_cancelled_kept_as_cancelled(self):
        out = self.src.reservations("11033", self.START, 10)
        self.assertEqual([dict(r["query"])["page"] for r in self.opener.requests], ["0", "1"])
        self.assertEqual((out["total"], out["complete"]), (3, True))
        self.assertEqual({r["id"]: r["status"] for r in out["data"]}, {"1": "accepted", "2": "cancelled", "3": "accepted"})

    def test_a_short_count_is_reported_incomplete_not_complete(self):
        self.pages[0]["meta"]["total"] = self.pages[1]["meta"]["total"] = 4
        out = self.src.reservations("11033", self.START, 10)
        self.assertFalse(out["complete"])

    def test_bookings_for_another_property_refuse(self):
        self.pages[1]["bookings"][0]["property_id"] = 999
        with self.assertRaisesRegex(UplistingError, "another property"):
            self.src.reservations("11033", self.START, 10)

    def test_calendar_joins_bookings_and_trims_the_extra_night(self):
        rows = self.src.calendar("11033", self.START, 10)
        self.assertEqual([r["date"] for r in rows][-1], "2030-02-10", "the 11th was asked for and trimmed")
        status = {r["date"]: r["status_reason"] for r in rows}
        self.assertEqual((status["2030-02-03"], status["2030-02-04"], status["2030-02-10"], status["2030-02-06"]),
                         ("RESERVED", "RESERVED", "BLOCKED", "AVAILABLE"))
        self.assertEqual({r["currency"] for r in rows}, {"GBP"})
        cal_call = self.opener.calls("GET", "/calendar/11033")[0]
        self.assertEqual(dict(cal_call["query"]), {"from": "2030-02-01", "to": "2030-02-11"})

    def test_paginated_properties_are_refused_not_truncated(self):
        self.opener.routes[("GET", "/properties")] = {"data": [PROP], "links": {"next": "https://x"}}
        with self.assertRaisesRegex(UplistingError, "paginated"):
            self.src.inventory()

    def test_reviews_are_unreadable_not_zero(self):
        with self.assertRaisesRegex(UplistingError, "no reviews endpoint"):
            self.src.reviews("11033")

    def test_cancelled_booking_is_excluded_from_booked_nights_in_the_real_analysis(self):
        prop = self.src.property("11033")
        cal = self.src.calendar("11033", self.START, 10)
        res = self.src.reservations("11033", self.START, 10)["data"]
        facts = analyze(prop, cal, res, [], self.START, 10, datetime(2030, 1, 31, tzinfo=timezone.utc))
        week = facts["windows"][0]
        self.assertEqual(week["confirmed_paid_nights"], 2, "only booking 1's two nights; the cancelled stay is open")
        self.assertEqual(week["on_books_accommodation_cents"], 64500)
        daily = {d["date"]: d["classification"] for d in facts["daily"]}
        self.assertEqual((daily["2030-02-06"], daily["2030-02-07"]), ("open", "open"))


class Target(unittest.TestCase):
    def target(self, routes_):
        opener = FakeOpener(routes_)
        return UplistingCalendarTarget(connections(UPLISTING_API_KEY=KEY), opener=opener), opener

    def base(self, extra=None):
        r = {("GET", "/properties/11033"): {"data": PROP},
             ("GET", "/calendar/11033"): {"calendar": {"days": [day(f"2030-02-{d:02d}") for d in range(1, 5)]}},
             ("POST", "/calendar/11033"): (202, {"request_id": "6a20706e-cd4e"})}
        r.update(extra or {})
        return r

    def test_interface(self):
        t, _ = self.target(self.base())
        self.assertEqual((t.name, t.host), ("uplisting", "connect.uplisting.io"))
        self.assertIsNone(t.floor("11033"))
        self.assertIsNone(t.pricing_managed("11033"))
        self.assertTrue(t.APPLIES_ASYNC)

    def test_read_calendar_is_fresh_inclusive_and_in_major_units(self):
        t, opener = self.target(self.base())
        cal = t.read_calendar("11033", date(2030, 2, 1), date(2030, 2, 3))
        self.assertEqual(cal["currency"], "GBP")
        self.assertEqual(cal["days"]["2030-02-01"], {"price": 131.0, "min_stay": 2, "available": True})
        self.assertEqual(sorted(cal["days"]), ["2030-02-01", "2030-02-02", "2030-02-03"])
        t.read_calendar("11033", date(2030, 2, 1), date(2030, 2, 3))
        self.assertEqual(len(opener.calls("GET", "/calendar/11033")), 2, "never cached")

    def test_partial_or_duplicate_calendar_is_refused(self):
        t, _ = self.target(self.base({("GET", "/calendar/11033"): {"calendar": {"days": [day("2030-02-01")]}}}))
        with self.assertRaisesRegex(CannotWrite, "missing 2 of 3"):
            t.read_calendar("11033", date(2030, 2, 1), date(2030, 2, 3))
        t, _ = self.target(self.base({("GET", "/calendar/11033"): {"calendar": {"days": [day("2030-02-01"), day("2030-02-01")]}}}))
        with self.assertRaisesRegex(CannotWrite, "twice"):
            t.read_calendar("11033", date(2030, 2, 1), date(2030, 2, 1))

    def test_write_sends_only_rate_and_min_stay_in_major_units(self):
        t, opener = self.target(self.base())
        t.write_calendar("11033", {"2030-02-02": {"price": 150.005, "min_stay": 3}, "2030-02-01": {"min_stay": 2}}, "GBP")
        post = opener.calls("POST", "/calendar/11033")
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]["body"], {"calendar": {"days": [
            {"date": "2030-02-01", "minimum_length_of_stay": 2},
            {"date": "2030-02-02", "day_rate": 150.01, "minimum_length_of_stay": 3}]}})
        self.assertNotIn("available", json.dumps(post[0]["body"]), "this writer never opens or closes a night")
        self.assertEqual(t.last_request_id, "6a20706e-cd4e")

    def test_refusals_happen_before_anything_is_sent(self):
        cases = ((("11033", {"2030-02-02": {"price": 100}}, "USD"), "currency"),
                 (("11033", {"2020-01-01": {"price": 100}}, "GBP"), "past"),
                 (("11033", {"2030-02-02": {"available": True}}, "GBP"), "only set"),
                 (("../x", {"2030-02-02": {"price": 100}}, "GBP"), "not an Uplisting"))
        for args, why in cases:
            t, opener = self.target(self.base())
            with self.subTest(why=why), self.assertRaisesRegex(CannotWrite, why):
                t.write_calendar(*args)
            self.assertEqual(opener.calls("POST"), [])

    def test_http_error_names_the_code_never_the_body_and_is_not_retried(self):
        t, opener = self.target(self.base({("POST", "/calendar/11033"): (400, None)}))
        with self.assertRaises(CannotWrite) as ctx:
            t.write_calendar("11033", {"2030-02-02": {"price": 100}}, "GBP")
        self.assertEqual(str(ctx.exception), "Uplisting POST /calendar/11033: HTTP 400")
        self.assertNotIn(SECRET_BODY, str(ctx.exception))
        self.assertEqual(len(opener.calls("POST")), 1)

    def test_anything_but_202_with_a_request_id_is_not_success(self):
        for answer in ((200, {"request_id": "r"}), (202, {}), (202, None)):
            t, _ = self.target(self.base({("POST", "/calendar/11033"): answer}))
            with self.subTest(answer=answer), self.assertRaisesRegex(CannotWrite, "202"):
                t.write_calendar("11033", {"2030-02-02": {"price": 100}}, "GBP")

    def test_transport_refuses_every_other_call(self):
        t, opener = self.target(self.base())
        for method, path in (("GET", "/bookings/11033"), ("DELETE", "/calendar/11033"), ("POST", "/v2/bookings"),
                             ("GET", "/properties"), ("PUT", "/calendar/11033")):
            with self.subTest(path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                t.http.request(method, path)
        self.assertEqual(opener.requests, [])

    def test_no_key_no_target(self):
        with self.assertRaises(CannotWrite):
            UplistingCalendarTarget(connections())


if __name__ == "__main__":
    unittest.main()
