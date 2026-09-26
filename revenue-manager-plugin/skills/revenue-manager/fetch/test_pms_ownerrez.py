"""Offline contracts for the OwnerRez adapter. Fixtures follow shapes measured live 2026-09-24
(values made up): GET /v2/calendar/{id} nights with rate.rent and rules, int ids,
arrival/departure dates, type booking|block, charges[type=rent] as room revenue,
listing_numbers.Airbnb on the property detail."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone

from _mvp_pms import analyze, normalize_calendar, normalize_property, normalize_reservation, normalize_review
from _pms_ownerrez import OwnerRezError, day_row, property_row, reservation_row, review_row

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


def night(d, status, rent=180.0, **rules):
    return {"date": f"{d}T00:00:00", "status": status, "rate": {"amount": rent, "rent": rent, "is_spot_rate": True},
            "rules": {"min_nights": 2, **rules}}


class Calendar(unittest.TestCase):
    def test_reads_rate_min_stay_status_and_rules(self):
        rows = normalize_calendar([day_row(night("2026-10-04", "available"), "USD"),
                                   day_row(night("2026-10-05", "booked"), "USD"),
                                   day_row(night("2026-10-06", "blocked"), "USD"),
                                   day_row(night("2026-10-07", "available", is_arrival_disallowed=True), "USD")])
        self.assertEqual([r["status_reason"] for r in rows], ["AVAILABLE", "RESERVED", "BLOCKED", "AVAILABLE"])
        self.assertEqual([r["price_cents"] for r in rows], [18000] * 4)
        self.assertEqual([r["min_stay"] for r in rows], [2] * 4)
        self.assertEqual((rows[3]["closed_for_checkin"], rows[3]["closed_for_checkout"]), (True, False))
        self.assertEqual(rows[0]["date"], "2026-10-04", "the date-time is trimmed to the local date")

    def test_gap_is_bookable_but_stay_disallowed_is_not(self):
        self.assertEqual(day_row(night("2026-10-04", "gap"), "USD")["status_reason"], "AVAILABLE")
        self.assertEqual(day_row(night("2026-10-04", "available", is_stay_disallowed=True), "USD")["status_reason"], "BLOCKED")
        self.assertEqual(day_row(night("2026-10-04", "unavailable"), "USD")["status_reason"], "BLOCKED")

    def test_missing_rate_is_unknown_not_zero(self):
        self.assertIsNone(day_row({"date": "2026-10-04", "status": "available"}, "USD")["price_cents"])


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


class AccountTimeZone(unittest.TestCase):
    """Live 2026-09-25: 8 of 23 active properties on a real account had no time_zone, and
    every one of their cards blocked. The account (/v2/users/me) has one."""

    def source(self, prop, me):
        from _pms_ownerrez import OwnerRezSource
        seen = []
        class C:
            def request(self, provider, op, url, **k):
                seen.append(url)
                return (me if url.endswith("/users/me") else prop), {}
        class Conn:
            values = {"OWNERREZ_EMAIL": "e", "OWNERREZ_TOKEN": "t"}
        return OwnerRezSource(C(), Conn()), seen

    def test_missing_property_zone_falls_back_to_the_account_and_is_marked(self):
        prop = {k: v for k, v in PROP.items() if k != "time_zone"}
        src, seen = self.source(prop, {"time_zone": "America/New_York"})
        p = src._detail(4401)
        self.assertEqual((p["timezone"], p["timezone_source"]), ("America/New_York", "account"))
        src._detail(4401)
        self.assertEqual(sum(u.endswith("/users/me") for u in seen), 1, "account zone read once")

    def test_property_zone_wins_and_no_extra_call(self):
        src, seen = self.source(PROP, {"time_zone": "America/Chicago"})
        p = src._detail(4401)
        self.assertEqual(p["timezone"], "America/New_York")
        self.assertNotIn("timezone_source", p)
        self.assertFalse(any(u.endswith("/users/me") for u in seen))

    def test_no_zone_anywhere_stays_missing(self):
        prop = {k: v for k, v in PROP.items() if k != "time_zone"}
        src, _ = self.source(prop, {"time_zone": None})
        self.assertIsNone(src._detail(4401)["timezone"])


class EndToEnd(unittest.TestCase):
    def test_runs_through_the_real_pms_analysis_with_rates(self):
        start = date(2026, 10, 4)
        cal = [day_row(night(f"2026-10-{d:02d}", "booked" if d in (5, 6, 7) else "available"), "USD") for d in range(4, 14)]
        facts = analyze(property_row(PROP), cal, [reservation_row(BOOK)], [], start, 10, datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertTrue(facts["coverage"]["pms_rates_exposed"])
        self.assertTrue(facts["coverage"]["analysable"], facts["warnings"])


if __name__ == "__main__":
    unittest.main()
