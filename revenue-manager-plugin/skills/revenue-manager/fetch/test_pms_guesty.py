"""Offline contracts for the Guesty adapter. Fixtures follow shapes measured live 2026-09-24
(values made up): whole-unit prices, `available`/`booked` calendar status, the Airbnb id under
integrations[].airbnb2.id, money.fareAccommodationAdjusted, raw Airbnb reviews."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from _mvp_pms import analyze, normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _pms_guesty import (
    GuestyError, GuestySource, day_row, property_row, reservation_query, reservation_row, review_row, token_from_cache,
)

LISTING = {"_id": "gst-listing-0001", "nickname": "Test Place", "title": "Long title", "active": True,
           "timezone": "America/Denver", "accommodates": 6, "bedrooms": 2, "beds": 3, "bathrooms": 2.0,
           "address": {"city": "Boulder", "country": "United States"},
           "prices": {"currency": "USD", "basePrice": 200},
           "integrations": [{"platform": "airbnb2", "airbnb2": {"id": "1234567890123456789"}},
                            {"platform": "homeaway2", "homeaway2": {"id": "v-9"}}]}


class Property(unittest.TestCase):
    def test_maps_to_the_runner_shape(self):
        p = normalize_property(property_row(LISTING))
        self.assertEqual(p["id"], "gst-listing-0001")
        self.assertEqual(p["name"], "Test Place")
        self.assertEqual(p["currency"], "USD")
        self.assertEqual(p["timezone"], "America/Denver")
        self.assertEqual(p["capacity"], {"max": 6, "bedrooms": 2, "beds": 3, "bathrooms": 2.0})
        self.assertIn({"platform": "airbnb", "platform_id": "1234567890123456789"}, p["listings"])
        self.assertTrue(p["listed"])

    def test_inactive_listing_is_not_listed(self):
        self.assertFalse(normalize_property(property_row(dict(LISTING, active=False)))["listed"])

    def test_missing_currency_is_refused(self):
        with self.assertRaises(GuestyError):
            property_row(dict(LISTING, prices={}))


class Calendar(unittest.TestCase):
    def test_whole_units_become_cents_and_status_maps(self):
        rows = normalize_calendar([day_row({"date": "2026-10-01", "price": 151, "currency": "USD", "minNights": 2, "status": "available"}),
                                   day_row({"date": "2026-10-02", "price": 158, "currency": "USD", "minNights": 2, "status": "booked", "reservationId": "r1"}),
                                   day_row({"date": "2026-10-03", "price": 158, "currency": "USD", "minNights": 2, "status": "unavailable", "cta": True})])
        self.assertEqual([r["price_cents"] for r in rows], [15100, 15800, 15800])
        self.assertEqual([r["status_reason"] for r in rows], ["AVAILABLE", "RESERVED", "BLOCKED"])
        self.assertEqual([r["available"] for r in rows], [True, False, False])
        self.assertEqual(rows[2]["closed_for_checkin"], True)

    def test_unknown_status_stays_unknown(self):
        self.assertEqual(normalize_calendar([day_row({"date": "2026-10-01", "price": 1, "currency": "USD", "status": "weird"})])[0]["status_reason"], "UNKNOWN")

    def test_fractional_price_rounds_to_the_cent(self):
        self.assertEqual(day_row({"date": "2026-10-01", "price": 151.255, "currency": "USD", "status": "available"})["price_cents"], 15126)

    def test_bad_price_is_left_unknown_not_zero(self):
        self.assertIsNone(day_row({"date": "2026-10-01", "price": None, "currency": "USD", "status": "available"})["price_cents"])


class Reservations(unittest.TestCase):
    RAW = {"_id": "res-1", "status": "confirmed", "listingId": "gst-listing-0001",
           "checkInDateLocalized": "2026-10-05", "checkOutDateLocalized": "2026-10-08", "nightsCount": 3,
           "createdAt": "2026-09-01T12:00:00.000Z", "confirmedAt": "2026-09-01T12:05:00.000Z",
           "integration": {"platform": "airbnb2"},
           "money": {"currency": "USD", "fareAccommodation": 480.0, "fareAccommodationAdjusted": 432.5},
           "guest": {"fullName": "SHOULD NEVER APPEAR"}}

    def test_maps_money_dates_status_and_scope(self):
        r = normalize_reservation(reservation_row(self.RAW))
        self.assertEqual(r["status"], "accepted")
        self.assertEqual((r["check_in"], r["check_out"], r["nights"]), ("2026-10-05", "2026-10-08", 3))
        self.assertEqual(r["property_ids"], ["gst-listing-0001"])
        self.assertEqual(r["financials"]["host_accommodation_cents"], 43250)
        self.assertEqual(r["financials"]["currency"], "USD")
        self.assertEqual(r["platform"], "airbnb")
        self.assertTrue(r["booking_date"].startswith("2026-09-01"))

    def test_no_guest_data_survives(self):
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(reservation_row(self.RAW)))

    def test_status_words_map_to_the_runner_vocabulary(self):
        for guesty, ours in (("confirmed", "accepted"), ("canceled", "cancelled"), ("declined", "not accepted"),
                             ("inquiry", "inquiry"), ("reserved", "request"), ("expired", "not accepted"),
                             ("something-new", "unknown")):
            with self.subTest(guesty=guesty):
                self.assertEqual(normalize_reservation(reservation_row(dict(self.RAW, status=guesty)))["status"], ours)

    def test_missing_adjusted_fare_is_unknown_not_zero(self):
        raw = dict(self.RAW, money={"currency": "USD"})
        self.assertIsNone(normalize_reservation(reservation_row(raw))["financials"]["host_accommodation_cents"])


class ReservationQuery(unittest.TestCase):
    def test_uses_the_filters_json_never_the_ignored_listingId_param(self):
        q = reservation_query("gst-listing-0001", "_id status")
        self.assertNotIn("listingId", q)
        self.assertEqual(json.loads(q["filters"]), [{"field": "listingId", "operator": "$eq", "value": "gst-listing-0001"}])

    def test_a_foreign_reservation_still_refuses(self):
        class FakeClient:
            def request(self, provider, op, url, headers=None):
                return {"results": [dict(Reservations.RAW, listingId="someone-else")], "count": 1}, {}
            def fetch(self, source, ident, loader, ttl_seconds=0):
                return loader()
        class FakeConn:
            paths, values = {}, {}
            def account_or(self, p): return "acct"
        src = GuestySource(FakeClient(), FakeConn()); src._token = "t"
        with self.assertRaisesRegex(GuestyError, "scope"):
            src.reservations("gst-listing-0001", date(2026, 10, 1), 90)


class Reviews(unittest.TestCase):
    def test_airbnb_review_keeps_rating_and_categories_only(self):
        raw = {"_id": "rv1", "channelId": "airbnb2", "createdAt": "2026-09-05T00:00:00Z",
               "rawReview": {"overall_rating": 5, "submitted_at": "2026-09-04T10:00:00Z", "public_review": "SECRET TEXT",
                             "category_ratings_cleanliness": 5, "category_ratings_checkin": 4}}
        r = normalize_review(review_row(raw))
        self.assertEqual(r["rating"], 5)
        self.assertEqual(r["platform"], "airbnb")
        self.assertIn({"type": "cleanliness", "rating": 5}, r["detailed_ratings"])
        self.assertNotIn("SECRET TEXT", json.dumps(review_row(raw)))

    def test_non_airbnb_scale_is_not_mixed_in(self):
        # Booking.com scores out of 10; mixing them with Airbnb's 5 corrupts the average
        raw = {"_id": "rv2", "channelId": "bookingCom", "rawReview": {"overall_rating": 9}}
        self.assertIsNone(normalize_review(review_row(raw))["rating"])


class ReviewsQuery(unittest.TestCase):
    def test_reviews_ask_by_listingId_and_still_drop_foreign_rows(self):
        # Live 2026-09-25: /reviews answers `filters` with HTTP 400; `listingId` works.
        seen = {}
        class FakeClient:
            def request(self, provider, op, url, headers=None):
                seen["url"] = url
                return {"data": [{"_id": "r1", "listingId": "gst-listing-0001", "channelId": "airbnb2",
                                  "rawReview": {"overall_rating": 5}},
                                 {"_id": "r2", "listingId": "someone-else", "channelId": "airbnb2",
                                  "rawReview": {"overall_rating": 1}}]}, {}
            def fetch(self, source, ident, loader, ttl_seconds=0):
                return loader()
        class FakeConn:
            paths, values = {}, {}
            def account_or(self, p): return "acct"
        src = GuestySource(FakeClient(), FakeConn()); src._token = "t"
        out = src.reviews("gst-listing-0001")
        self.assertIn("listingId=gst-listing-0001", seen["url"])
        self.assertNotIn("filters", seen["url"])
        self.assertEqual([r["rating"] for r in out["data"]], [5])


class TokenCache(unittest.TestCase):
    def test_kit_raw_cache_is_used_while_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "guesty.token"; p.write_text("tok-abc")
            self.assertEqual(token_from_cache(p), "tok-abc")
            old = time.time() - 24 * 3600; os.utime(p, (old, old))
            self.assertIsNone(token_from_cache(p), "a raw cache older than 23h must not be trusted")

    def test_json_cache_uses_its_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text(json.dumps({"access_token": "tok-json", "expires_at": time.time() + 3600}))
            self.assertEqual(token_from_cache(p), "tok-json")
            p.write_text(json.dumps({"access_token": "tok-json", "expires_at": time.time() - 10}))
            self.assertIsNone(token_from_cache(p))


class EndToEnd(unittest.TestCase):
    def test_guesty_rows_run_through_the_real_pms_analysis(self):
        start = date(2026, 10, 1)
        days = [day_row({"date": f"2026-10-{d:02d}", "price": 150, "currency": "USD", "minNights": 1,
                         "status": "booked" if d in (5, 6, 7) else "available"}) for d in range(1, 11)]
        res = [reservation_row(Reservations.RAW)]
        facts = analyze(property_row(LISTING), days, res, [], start, 10,
                        datetime(2026, 9, 30, 12, tzinfo=timezone.utc))
        self.assertIsInstance(facts, dict)


if __name__ == "__main__":
    unittest.main()


class GuestyDetection(unittest.TestCase):
    """Live 2026-09-25: any guesty.token FILE made Guesty 'connected', expired or not."""

    def conns(self, d):
        class C:
            values, paths = {}, {"guesty": [d]}
        return C()

    def test_expired_cache_is_not_a_connection_fresh_one_is(self):
        import os
        import tempfile
        import time
        from pathlib import Path
        from _pms_registry import _has_guesty
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, ".cache", "guesty.token"); p.parent.mkdir(); p.write_text("tok")
            old = time.time() - 30 * 3600
            os.utime(p, (old, old))
            self.assertFalse(_has_guesty(self.conns(d)))
            os.utime(p, None)
            self.assertTrue(_has_guesty(self.conns(d)))
