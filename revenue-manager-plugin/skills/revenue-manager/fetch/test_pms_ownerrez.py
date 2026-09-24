"""Offline contracts for the OwnerRez adapter. Fixtures follow shapes measured live 2026-09-24
(values made up): int ids, arrival/departure dates, type booking|block, status `active`,
charges[type=rent] as room revenue, listing_numbers.Airbnb on the property detail."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone

from _mvp_pms import analyze, normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _pms_ownerrez import OwnerRezError, calendar_rows, property_row, reservation_row, review_row

PROP = {"id": 4401, "name": "Lake Cabin", "external_name": "Lake Cabin on the water", "active": True, "is_snoozed": False,
        "time_zone": "America/New_York", "currency_code": "USD", "max_guests": 8, "bedrooms": 3, "bathrooms": 2,
        "address": {"city": "Lake Town", "country": "US"}, "listing_numbers": {"Airbnb": "987654321098765432", "Vrbo": "v-55"}}
BOOK = {"id": 9001, "property_id": 4401, "type": "booking", "is_block": False, "status": "active",
        "arrival": "2026-10-05", "departure": "2026-10-08", "booked_utc": "2026-09-01T12:00:00Z",
        "currency_code": "USD", "listing_site": "Airbnb", "total_amount": 900.0,
        "charges": [{"type": "rent", "amount": 600.0}, {"type": "surcharge", "amount": 150.0}, {"type": "tax", "amount": 90.0}],
        "guest": {"name": "SHOULD NEVER APPEAR"}}
BLOCK = {"id": 9002, "property_id": 4401, "type": "block", "is_block": True, "status": "active",
         "arrival": "2026-10-10", "departure": "2026-10-12", "booked_utc": "2026-09-02T12:00:00Z"}


class Property(unittest.TestCase):
    def test_maps_to_the_runner_shape(self):
        p = normalize_property(property_row(PROP))
        self.assertEqual((p["id"], p["name"], p["currency"], p["timezone"]), ("4401", "Lake Cabin", "USD", "America/New_York"))
        self.assertEqual(p["capacity"]["max"], 8)
        self.assertIn({"platform": "airbnb", "platform_id": "987654321098765432"}, p["listings"])
        self.assertTrue(p["listed"])

    def test_snoozed_is_not_listed(self):
        self.assertFalse(normalize_property(property_row(dict(PROP, is_snoozed=True)))["listed"])


class Calendar(unittest.TestCase):
    def test_built_from_bookings_and_blocks_with_no_prices(self):
        rows = normalize_calendar(calendar_rows([BOOK, BLOCK], "USD", date(2026, 10, 4), 9))
        self.assertEqual([r["date"] for r in rows][:2], ["2026-10-04", "2026-10-05"])
        by = {r["date"]: r["status_reason"] for r in rows}
        self.assertEqual(by["2026-10-04"], "AVAILABLE")
        self.assertEqual([by[d] for d in ("2026-10-05", "2026-10-06", "2026-10-07")], ["RESERVED"] * 3)
        self.assertEqual(by["2026-10-08"], "AVAILABLE", "departure day is free")
        self.assertEqual([by[d] for d in ("2026-10-10", "2026-10-11")], ["BLOCKED"] * 2)
        self.assertTrue(all(r["price_cents"] is None and r["min_stay"] is None for r in rows))
        self.assertTrue(all(r["currency"] == "USD" for r in rows))

    def test_cancelled_booking_frees_the_night(self):
        rows = normalize_calendar(calendar_rows([dict(BOOK, status="canceled")], "USD", date(2026, 10, 5), 3))
        self.assertTrue(all(r["status_reason"] == "AVAILABLE" for r in rows))


class Reservations(unittest.TestCase):
    def test_rent_charge_is_the_room_revenue(self):
        r = normalize_reservation(reservation_row(BOOK))
        self.assertEqual(r["financials"]["host_accommodation_cents"], 60000)
        self.assertEqual((r["check_in"], r["check_out"], r["nights"]), ("2026-10-05", "2026-10-08", 3))
        self.assertEqual((r["status"], r["platform"], r["property_ids"]), ("accepted", "airbnb", ["4401"]))
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(reservation_row(BOOK)))

    def test_no_rent_line_is_unknown_not_zero(self):
        self.assertIsNone(normalize_reservation(reservation_row(dict(BOOK, charges=[{"type": "tax", "amount": 9}])))["financials"]["host_accommodation_cents"])
        self.assertIsNone(normalize_reservation(reservation_row(dict(BOOK, charges=None)))["financials"]["host_accommodation_cents"])

    def test_a_block_is_not_a_reservation(self):
        with self.assertRaises(OwnerRezError):
            reservation_row(BLOCK)

    def test_status_words(self):
        for ours, theirs in (("accepted", "active"), ("cancelled", "canceled"), ("cancelled", "cancelled"), ("unknown", "strange")):
            with self.subTest(theirs=theirs):
                self.assertEqual(normalize_reservation(reservation_row(dict(BOOK, status=theirs)))["status"], ours)


class Reviews(unittest.TestCase):
    def test_keeps_stars_and_date_only(self):
        r = normalize_review(review_row({"id": 1, "property_id": 4401, "stars": 4.8, "date": "2026-09-01", "listing_site": "Airbnb", "body": "SECRET"}))
        self.assertEqual((r["rating"], r["platform"]), (4.8, "airbnb"))
        self.assertNotIn("SECRET", json.dumps(review_row({"id": 1, "stars": 5, "body": "SECRET"})))

    def test_off_scale_ratings_are_dropped_not_mixed(self):
        self.assertIsNone(review_row({"id": 2, "stars": 9, "listing_site": "Booking.com"})["rating"])
        self.assertIsNone(review_row({"id": 3, "stars": 7, "listing_site": "Airbnb"})["rating"])


class Paging(unittest.TestCase):
    def source(self, body):
        from _pms_ownerrez import OwnerRezSource
        class C:
            def request(self, *a, **k): return body, {}
        class Conn:
            values = {"OWNERREZ_EMAIL": "e", "OWNERREZ_TOKEN": "t"}
        return OwnerRezSource(C(), Conn())

    def test_limit_offset_only_is_an_empty_collection(self):
        self.assertEqual(self.source({"limit": 100, "offset": 0})._paged("/bookings", {})[0], [])

    def test_any_other_body_without_items_is_refused(self):
        for body in ({"messages": ["unauthorized"]}, {}, {"limit": 100, "offset": 0, "error": "x"}):
            with self.subTest(body=body), self.assertRaises(OwnerRezError):
                self.source(body)._paged("/bookings", {})


class EndToEnd(unittest.TestCase):
    def test_runs_through_the_real_pms_analysis_in_no_rates_mode(self):
        start = date(2026, 10, 4)
        facts = analyze(property_row(PROP), calendar_rows([BOOK, BLOCK], "USD", start, 10),
                        [reservation_row(BOOK)], [], start, 10, datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertFalse(facts["coverage"]["pms_rates_exposed"])
        self.assertTrue(facts["coverage"]["analysable"], facts["warnings"])


if __name__ == "__main__":
    unittest.main()
