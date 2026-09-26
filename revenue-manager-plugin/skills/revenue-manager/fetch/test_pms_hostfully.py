"""Offline contracts for the Hostfully (v3.3) adapter and write target. Fixtures follow the
OpenAPI schemas on dev.hostfully.com (read 2026-09-25); values are made up. DOCS-ONLY: no live
Hostfully account has been read or written."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone

from _mvp_pms import analyze
from _mvp_write import CannotWrite
from _pms_fakes import SECRET_BODY, FakeOpener, connections, read_client
from _pms_hostfully import (
    HostfullyCalendarTarget, HostfullyError, HostfullySource, day_row, lead_status, property_row, reservation_row,
    review_row,
)

KEY, AGENCY, PID = "hf-key", "agency-0001", "prop-0001"
P = "/api/v3.3"
PROP = {"uid": PID, "agencyUid": AGENCY, "name": "Lake House", "timeZone": "America/Chicago", "isActive": True,
        "bedrooms": 3, "beds": 4, "bathrooms": "2.5", "address": {"city": "Austin", "countryCode": "US"},
        "availability": {"maxGuests": 8, "minimumStay": 2}, "pricing": {"currency": "USD", "dailyRate": 250},
        "airbnbData": {"airbnbId": "12345678"}, "wifiPassword": "SHOULD NEVER APPEAR"}


def lead(uid, check_in, check_out, status="BOOKED", kind="BOOKING", pid=PID):
    return {"uid": uid, "propertyUid": pid, "agencyUid": AGENCY, "status": status, "type": kind, "channel": "AIRBNB",
            "checkInLocalDateTime": f"{check_in}T16:00:00", "checkOutLocalDateTime": f"{check_out}T10:00:00",
            "bookedUtcDateTime": "2030-01-10T12:00:00", "notes": "HIDDEN",
            "guestInformation": {"firstName": "SHOULD NEVER APPEAR", "email": "g@x.y"}}


def order(lead_uid, rent, currency="USD"):
    return {"uid": f"o-{lead_uid}", "leadUid": lead_uid, "currency": currency,
            "rent": {"rentNetPrice": rent, "rentBreakdowns": [], "netPrice": rent + 10, "grossPrice": rent + 30},
            "fees": {"cleaningFee": {"netPrice": 100}}}


def entry(d, unavailable=False, why=None, price=250.0, mlos=2):
    return {"date": d, "pricing": {"currency": "USD", "value": price},
            "availability": {"unavailable": unavailable, "unavailabilityReason": why, "availableForCheckIn": True,
                             "availableForCheckOut": True, "minimumStayLength": mlos}}


class Mappers(unittest.TestCase):
    def test_property(self):
        p = property_row(PROP)
        self.assertEqual((p["id"], p["currency"], p["timezone"], p["listed"]), (PID, "USD", "America/Chicago", True))
        self.assertEqual(p["capacity"]["max"], 8)
        self.assertEqual(p["listings"], [{"platform": "airbnb", "platform_id": "12345678"}])
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(p))
        with self.assertRaises(HostfullyError):
            property_row(dict(PROP, pricing={"currency": "NONE"}))

    def test_calendar_reasons(self):
        self.assertEqual(day_row(entry("2030-02-01"), "USD")["status_reason"], "AVAILABLE")
        self.assertEqual(day_row(entry("2030-02-01", True, "BOOKING"), "USD")["status_reason"], "RESERVED")
        for why in ("BLOCK", "BLOCK_BY_OWNER", "INQUIRY", "PROPERTY_AVAILABILITY_SETTINGS", "OTHER"):
            self.assertEqual(day_row(entry("2030-02-01", True, why), "USD")["status_reason"], "BLOCKED")
        self.assertEqual(day_row(entry("2030-02-01", True, None), "USD")["status_reason"], "UNKNOWN")
        row = day_row(entry("2030-02-01", price=250.5), "USD")
        self.assertEqual((row["price_cents"], row["min_stay"], row["closed_for_checkin"]), (25050, 2, False))

    def test_lead_status_words(self):
        for status, kind, ours in (("BOOKED", "BOOKING", "accepted"), ("CANCELLED", "BOOKING", "cancelled"),
                                   ("NEW", "INQUIRY", "inquiry"), ("PENDING", "BOOKING_REQUEST", "request"),
                                   ("ON_HOLD", "INQUIRY", "request"), ("DECLINED", "BOOKING_REQUEST", "not accepted"),
                                   ("BOOKED", "BLOCK", None), ("BLOCKED", "BLOCK", None), ("SAMPLE", "BOOKING", None),
                                   ("SOMETHING_NEW", "BOOKING", "unknown")):
            with self.subTest(status=status, kind=kind):
                self.assertEqual(lead_status({"status": status, "type": kind}), ours)

    def test_money_comes_from_the_order_and_guest_data_is_dropped(self):
        r = reservation_row(lead("L1", "2030-02-03", "2030-02-05"), order("L1", 480.255))
        self.assertEqual((r["financials"]["host_accommodation_cents"], r["financials"]["currency"]), (48026, "USD"))
        self.assertEqual((r["check_in"], r["check_out"], r["nights"], r["platform"]), ("2030-02-03", "2030-02-05", 2, "airbnb"))
        self.assertEqual(r["booking_date"], "2030-01-10T12:00:00Z")
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(r))
        self.assertIsNone(reservation_row(lead("L1", "2030-02-03", "2030-02-05"), None)["financials"]["host_accommodation_cents"])

    def test_reviews_keep_only_a_five_star_scale(self):
        r = review_row({"uid": "r1", "rating": 5, "source": "VRBO", "date": "2030-01-02", "content": "SECRET TEXT",
                        "ratingCategories": [{"category": "CLEANLINESS", "rate": 4}]})
        self.assertEqual((r["rating"], r["platform"], r["detailed_ratings"]), (5, "vrbo", [{"type": "cleanliness", "rating": 4}]))
        self.assertNotIn("SECRET TEXT", json.dumps(r))
        self.assertIsNone(review_row({"uid": "r2", "rating": 9, "source": "BOOKING_DOT_COM"})["rating"])
        self.assertIsNone(review_row({"uid": "r3", "rating": 8, "source": "HOSTFULLY"})["rating"])


class Source(unittest.TestCase):
    START = date(2030, 2, 1)

    def setUp(self):
        self.leads_pages = [
            {"leads": [lead("L1", "2030-02-03", "2030-02-05"), lead("L2", "2030-02-06", "2030-02-08", status="CANCELLED")],
             "_metadata": {"count": 2, "totalCount": 3}, "_paging": {"_nextCursor": "c2"}},
            {"leads": [lead("B1", "2030-02-10", "2030-02-11", kind="BLOCK", status="BLOCKED")],
             "_metadata": {"count": 1, "totalCount": 3}, "_paging": {"_nextCursor": None}},
        ]
        cal = [entry(f"2030-02-{d:02d}", *((True, "BOOKING") if d in (3, 4) else (True, "BLOCK") if d == 10 else (False, None)))
               for d in range(1, 12)]
        self.opener = FakeOpener({
            ("GET", f"{P}/properties"): {"properties": [PROP], "_metadata": {"totalCount": 1}, "_paging": {}},
            ("GET", f"{P}/leads"): lambda r: self.leads_pages[1 if dict(r["query"]).get("_cursor") == "c2" else 0],
            ("GET", f"{P}/orders"): {"orders": [order("L1", 480.0), order("L2", 999.0)], "_metadata": {"totalCount": 2}},
            ("GET", f"{P}/property-calendar/{PID}"): {"calendar": {"propertyUid": PID, "entries": cal}},
            ("GET", f"{P}/reviews"): {"reviews": [{"uid": "r1", "propertyUid": PID, "rating": 5, "source": "VRBO",
                                                   "date": "2030-01-02"}], "_metadata": {"totalCount": 1}},
        })
        self.src = HostfullySource(read_client(self.opener), connections(HOSTFULLY_API_KEY=KEY, HOSTFULLY_AGENCY_UID=AGENCY))

    def test_both_env_vars_required(self):
        with self.assertRaises(HostfullyError):
            HostfullySource(read_client(self.opener), connections(HOSTFULLY_API_KEY=KEY))

    def test_v33_base_header_and_agency_uid_only_on_the_property_list(self):
        self.src.property(PID)
        self.src.reservations(PID, self.START, 10)
        self.src.calendar(PID, self.START, 10)
        self.src.reviews(PID)
        for r in self.opener.requests:
            self.assertEqual((r["method"], r["host"]), ("GET", "api.hostfully.com"))
            self.assertTrue(r["path"].startswith("/api/v3.3/"))
            self.assertEqual(r["headers"]["x-hostfully-apikey"], KEY)
            has_agency = "agencyUid" in dict(r["query"])
            self.assertEqual(has_agency, r["path"] == f"{P}/properties", r["path"])
            self.assertNotIn("_limit", dict(r["query"]), "no page size is documented, none is sent")

    def test_cursor_pagination_blocks_skipped_cancelled_kept(self):
        out = self.src.reservations(PID, self.START, 10)
        self.assertEqual([dict(c["query"]).get("_cursor") for c in self.opener.calls("GET", f"{P}/leads")], [None, "c2"])
        self.assertEqual({r["id"]: r["status"] for r in out["data"]}, {"L1": "accepted", "L2": "cancelled"})
        self.assertTrue(out["complete"], "3 leads seen, 3 reported; the block is left out after the count")

    def test_short_count_is_incomplete(self):
        for page in self.leads_pages:
            page["_metadata"]["totalCount"] = 5
        self.assertFalse(self.src.reservations(PID, self.START, 10)["complete"])

    def test_leads_for_another_property_refuse(self):
        self.leads_pages[1]["leads"][0]["propertyUid"] = "someone-else"
        with self.assertRaisesRegex(HostfullyError, "another property"):
            self.src.reservations(PID, self.START, 10)

    def test_repeating_cursor_refuses(self):
        self.leads_pages[1]["_paging"]["_nextCursor"] = "c2"
        with self.assertRaisesRegex(HostfullyError, "same cursor"):
            self.src.reservations(PID, self.START, 10)

    def test_cancelled_lead_is_excluded_from_booked_nights_in_the_real_analysis(self):
        prop = self.src.property(PID)
        facts = analyze(prop, self.src.calendar(PID, self.START, 10), self.src.reservations(PID, self.START, 10)["data"],
                        self.src.reviews(PID)["data"], self.START, 10, datetime(2030, 1, 31, tzinfo=timezone.utc))
        self.assertEqual(facts["windows"][0]["confirmed_paid_nights"], 2)
        self.assertEqual(facts["windows"][0]["on_books_accommodation_cents"], 48000)
        daily = {d["date"]: d["classification"] for d in facts["daily"]}
        self.assertEqual((daily["2030-02-06"], daily["2030-02-10"]), ("open", "blocked"))


class Target(unittest.TestCase):
    def target(self, periods=(), post=None):
        self.posts_answer = post
        def post_route(rec):
            if self.posts_answer is not None:
                return self.posts_answer(rec) if callable(self.posts_answer) else self.posts_answer
            return {"pricingPeriod": rec["body"]["pricingPeriod"]}
        opener = FakeOpener({
            ("GET", f"{P}/properties/{PID}"): {"property": PROP},
            ("GET", f"{P}/property-calendar/{PID}"): {"calendar": {"propertyUid": PID,
                                                                  "entries": [entry(f"2030-02-{d:02d}") for d in range(1, 5)]}},
            ("GET", f"{P}/pricing-periods"): {"pricingPeriods": list(periods)},
            ("POST", f"{P}/pricing-periods"): post_route,
        })
        return HostfullyCalendarTarget(connections(HOSTFULLY_API_KEY=KEY), opener=opener), opener

    def test_interface(self):
        t, _ = self.target()
        self.assertEqual((t.name, t.host), ("hostfully", "api.hostfully.com"))
        self.assertIsNone(t.floor(PID))
        self.assertIsNone(t.pricing_managed(PID))

    def test_read_calendar(self):
        t, opener = self.target()
        cal = t.read_calendar(PID, date(2030, 2, 1), date(2030, 2, 3))
        self.assertEqual(cal["currency"], "USD")
        self.assertEqual(cal["days"]["2030-02-02"], {"price": 250.0, "min_stay": 2, "available": True})
        self.assertEqual(len(cal["days"]), 3)
        self.assertEqual(dict(opener.calls("GET", f"{P}/property-calendar/{PID}")[0]["query"])["to"], "2030-02-04")

    def test_one_set_per_date_carrying_existing_fields_forward(self):
        existing = {"propertyUid": PID, "date": "2030-02-02", "price": 300.0, "minimumStay": 3,
                    "availableForCheckIn": False, "availableForCheckOut": True, "name": "Festival"}
        t, opener = self.target(periods=[existing])
        t.write_calendar(PID, {"2030-02-02": {"price": 275.555}, "2030-02-03": {"price": 260, "min_stay": 2}}, "USD")
        bodies = [r["body"] for r in opener.calls("POST", f"{P}/pricing-periods")]
        self.assertEqual(bodies, [
            {"operation": "SET", "pricingPeriod": {"propertyUid": PID, "date": "2030-02-02", "price": 275.56, "minimumStay": 3,
                                                   "availableForCheckIn": False, "availableForCheckOut": True, "name": "Festival"}},
            {"operation": "SET", "pricingPeriod": {"propertyUid": PID, "date": "2030-02-03", "price": 260.0, "minimumStay": 2}}])

    def test_min_stay_alone_on_a_date_without_a_period_is_refused_before_any_send(self):
        t, opener = self.target()
        with self.assertRaisesRegex(CannotWrite, "2030-02-02 has no pricing period"):
            t.write_calendar(PID, {"2030-02-01": {"price": 200}, "2030-02-02": {"min_stay": 4}}, "USD")
        self.assertEqual(opener.calls("POST"), [])

    def test_min_stay_alone_keeps_the_existing_period_price(self):
        t, opener = self.target(periods=[{"propertyUid": PID, "date": "2030-02-02", "price": 300.0, "minimumStay": 3}])
        t.write_calendar(PID, {"2030-02-02": {"min_stay": 5}}, "USD")
        self.assertEqual(opener.calls("POST")[0]["body"]["pricingPeriod"], {"propertyUid": PID, "date": "2030-02-02",
                                                                           "price": 300.0, "minimumStay": 5})

    def test_mid_batch_failure_says_how_far_it_got_and_never_retries(self):
        answers = [{"pricingPeriod": {"propertyUid": PID, "date": "2030-02-01"}}, (409, None)]
        t, opener = self.target(post=lambda rec: answers.pop(0))
        with self.assertRaises(CannotWrite) as ctx:
            t.write_calendar(PID, {"2030-02-01": {"price": 200}, "2030-02-02": {"price": 210}, "2030-02-03": {"price": 220}}, "USD")
        self.assertIn(f"Hostfully POST {P}/pricing-periods: HTTP 409", str(ctx.exception))
        self.assertIn("1 of 3 dates were sent", str(ctx.exception))
        self.assertNotIn(SECRET_BODY, str(ctx.exception))
        self.assertEqual(len(opener.calls("POST")), 2, "stops at the first failure, resends nothing")

    def test_a_reply_that_does_not_echo_the_date_is_not_success(self):
        t, _ = self.target(post={"pricingPeriod": {"propertyUid": PID, "date": "2031-01-01"}})
        with self.assertRaisesRegex(CannotWrite, "did not echo 2030-02-01"):
            t.write_calendar(PID, {"2030-02-01": {"price": 200}}, "USD")

    def test_currency_and_ids_checked_before_sending(self):
        t, opener = self.target()
        with self.assertRaisesRegex(CannotWrite, "currency"):
            t.write_calendar(PID, {"2030-02-01": {"price": 200}}, "EUR")
        with self.assertRaisesRegex(CannotWrite, "not a Hostfully property uid"):
            t.write_calendar("../../agencies", {"2030-02-01": {"price": 200}}, "USD")
        self.assertEqual(opener.calls("POST"), [])

    def test_transport_refuses_every_other_call_including_remove_paths(self):
        t, opener = self.target()
        for method, path in (("GET", f"{P}/leads"), ("DELETE", f"{P}/pricing-periods"), ("POST", f"{P}/pricing-periods-bulk"),
                             ("PUT", f"{P}/property-pricing-rules/{PID}"), ("PATCH", f"{P}/properties/{PID}"),
                             ("GET", f"/api/v3.2/properties/{PID}")):
            with self.subTest(path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                t.http.request(method, path)
        self.assertEqual(opener.requests, [])


if __name__ == "__main__":
    unittest.main()
