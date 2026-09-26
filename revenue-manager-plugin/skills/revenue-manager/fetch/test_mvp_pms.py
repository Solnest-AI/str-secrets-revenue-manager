"""Synthetic privacy, accounting, and inventory regression tests for the PMS facts."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mvp_pms as pms  # noqa: E402


START = date(2032, 6, 10)
AS_OF = datetime(2032, 6, 10, 12, tzinfo=timezone.utc)
PROPERTY = {
    "id": "synthetic-property",
    "name": "Synthetic Suite",
    "currency": "CAD",
    "timezone": "UTC",
    "capacity": {"max": 2, "bedrooms": 1},
}


def calendar_rows(start=START, days=90, reserved=(), blocked=()):
    result = []
    for offset in range(days):
        when = start + timedelta(days=offset)
        reason = "RESERVED" if when in reserved else "BLOCKED" if when in blocked else "AVAILABLE"
        result.append(
            {
                "date": when.isoformat(),
                "price": {"amount": 10000, "currency": "CAD"},
                "min_stay": 1,
                "status": {"available": reason == "AVAILABLE", "reason": reason},
                "closed_for_checkin": False,
                "closed_for_checkout": False,
            }
        )
    return [{"days": result}]


def reservation(
    identifier="r1",
    start=START,
    nights=1,
    total=10000,
    status="accepted",
    booked=None,
    currency="CAD",
    discounts=(),
    history=None,
):
    booked = booked or (AS_OF - timedelta(days=2)).isoformat()
    return {
        "id": identifier,
        "platform": "airbnb",
        "status": status,
        "properties": [{"id": PROPERTY["id"]}],
        "booking_date": booked,
        "check_in": start.isoformat() + "T16:00:00+00:00",
        "check_out": (start + timedelta(days=nights)).isoformat() + "T10:00:00+00:00",
        "nights": nights,
        "reservation_status": {
            "current": {"category": status},
            "history": history or [{"category": status, "changed_at": booked}],
        },
        "financials": {
            "currency": currency,
            "host": {
                "accommodation": {"amount": total},
                "discounts": [{"amount": value, "label": "Weekly discount"} for value in discounts],
                "revenue": {"amount": 1},
                "guest_fees": [{"amount": 9000, "label": "Cleaning"}],
            },
        },
    }


def run(reservations=None, calendar=None, reviews=None, start=START, days=90, as_of=AS_OF):
    return pms.analyze(
        PROPERTY,
        calendar if calendar is not None else calendar_rows(start, days),
        reservations if reservations is not None else [],
        reviews if reviews is not None else [],
        start,
        days,
        as_of,
    )


class NormalizePrivacyTests(unittest.TestCase):
    def test_allowlists_drop_nested_guest_and_free_text(self):
        marker = "DO_NOT_PERSIST_PERSONAL_TEXT"
        raw = reservation()
        raw.update(
            {
                "guest": {"name": marker, "email": marker},
                "notes": marker,
                "messages": [{"body": marker}],
            }
        )
        raw["properties"][0]["address"] = marker
        raw["reservation_status"]["history"][0]["message"] = marker
        raw["financials"]["host"]["discounts"] = [
            {"amount": -10, "label": marker, "category": marker}
        ]
        normalized = pms.normalize_reservation(raw)
        self.assertNotIn(marker, json.dumps(normalized))
        self.assertEqual(normalized["financials"]["host_discounts"][0]["amount_cents"], -10)
        self.assertEqual(normalized, pms.normalize_reservation(normalized))
        review = pms.normalize_review(
            {
                "id": "review",
                "guest": marker,
                "reviewed_at": AS_OF.isoformat(),
                "public": {"review": marker, "rating": 4.5},
                "private": {
                    "feedback": marker,
                    "detailed_ratings": [
                        {"type": "cleanliness", "rating": 5, "text": marker},
                        {"type": marker, "rating": 5},
                    ],
                },
            }
        )
        self.assertNotIn(marker, json.dumps(review))
        self.assertEqual(review["rating"], 4.5)
        prop = pms.normalize_property(
            {
                **PROPERTY,
                "address": {"street": marker, "city": "Town"},
                "owner": marker,
                "capacity": {"max": 2, "notes": marker},
            }
        )
        self.assertNotIn(marker, json.dumps(prop))

    def test_untrusted_status_text_and_invalid_dates_are_not_retained(self):
        raw = reservation()
        raw["status"] = "secret guest note"
        raw["reservation_status"] = {"current": {"category": "secret guest note"}}
        raw["booking_date"] = "2032-01-01 guest email@example.invalid"
        normalized = pms.normalize_reservation(raw)
        self.assertEqual(normalized["status"], "unknown")
        self.assertIsNone(normalized["booking_date"])
        self.assertNotIn("secret", json.dumps(normalized))

    def test_calendar_normalization_is_idempotent(self):
        normalized = pms.normalize_calendar({"data": calendar_rows(days=7)})
        self.assertEqual(len(normalized), 7)
        self.assertEqual(normalized, pms.normalize_calendar(normalized))


class InventoryTests(unittest.TestCase):
    def test_pending_zero_and_confirmed_are_separate(self):
        records = [
            reservation(),
            reservation("held", START + timedelta(days=1), status="request"),
            reservation("zero", START + timedelta(days=2), total=0),
        ]
        calendar = calendar_rows(
            reserved={START + timedelta(days=n) for n in range(3)},
            blocked={START + timedelta(days=3)},
        )
        result = run(records, calendar)
        self.assertEqual(
            [row["classification"] for row in result["daily"][:5]],
            ["confirmed_paid", "pending_hold", "zero_value_accepted", "blocked", "open"],
        )
        window = result["windows"][0]
        self.assertEqual(
            (
                window["confirmed_paid_nights"],
                window["pending_held_nights"],
                window["zero_value_accepted_nights"],
            ),
            (1, 1, 1),
        )
        # 1 confirmed of 6 BOOKABLE nights (the blocked night leaves the denominator).
        self.assertEqual(window["confirmed_occupancy_pct"], 16.67)
        self.assertEqual(window["on_books_accommodation_cents"], 10000)

    def test_checkout_date_is_available_and_horizon_is_exclusive(self):
        record = reservation(start=START - timedelta(days=1), nights=2, total=20001)
        result = run([record], calendar_rows(reserved={START}))
        self.assertEqual(result["daily"][0]["accommodation_cents"], 10000)
        self.assertEqual(result["daily"][1]["classification"], "open")
        self.assertEqual(len(result["daily"]), 90)
        self.assertEqual(result["daily"][-1]["date"], (START + timedelta(days=89)).isoformat())
        self.assertEqual(sum(row["calendar_days"] for row in result["forward_months"]), 90)

    def test_pending_without_booking_date_uses_history_for_same_lead(self):
        record = reservation(status="request")
        record["booking_date"] = None
        result = run([record], calendar_rows(reserved={START}))
        self.assertEqual(result["daily"][0]["classification"], "pending_hold")
        current = result["same_lead"]["windows"][0]["current"]
        self.assertEqual(current["reconstructed_pending_nights"], 1)
        self.assertEqual(current["unknown_status_records"], 0)

    def test_mixed_currency_and_missing_financials_are_unknown_not_paid(self):
        for record in (reservation(currency="USD"), reservation(total=None)):
            with self.subTest(record=record["financials"]):
                result = run([record], calendar_rows(reserved={START}))
                self.assertEqual(result["daily"][0]["classification"], "accepted_unknown_value")
                self.assertEqual(result["windows"][0]["confirmed_paid_nights"], 0)
                self.assertIsNone(result["windows"][0]["confirmed_occupancy_pct"])
                self.assertFalse(result["coverage"]["analysable"])

    def test_unknown_scope_is_not_admitted(self):
        for properties in (
            [],
            [{"id": "another-property"}],
            [{"id": PROPERTY["id"]}, {"id": "another-property"}],
        ):
            record = reservation()
            record["properties"] = properties
            result = run([record], calendar_rows(reserved={START}))
            self.assertEqual(result["coverage"]["scoped_unique_records"], 0)
            self.assertFalse(result["coverage"]["analysable"])
            self.assertEqual(result["daily"][0]["classification"], "unknown")

    def test_calendar_reservation_conflict_blocks_analysability(self):
        result = run([reservation()])
        self.assertEqual(result["daily"][0]["classification"], "conflict")
        self.assertFalse(result["coverage"]["analysable"])

    def test_overlapping_reservations_never_double_count(self):
        result = run([reservation("first"), reservation("second")], calendar_rows(reserved={START}))
        self.assertEqual(result["daily"][0]["classification"], "conflict")
        self.assertEqual(result["windows"][0]["confirmed_paid_nights"], 0)
        self.assertEqual(result["coverage"]["overlap_days_excluded"], 1)

    def test_duplicate_id_conflict_is_quarantined_and_identical_page_row_is_deduplicated(self):
        original = reservation()
        identical = run([original, deepcopy(original)], calendar_rows(reserved={START}))
        self.assertEqual(identical["windows"][0]["confirmed_paid_nights"], 1)
        changed = reservation(total=20000)
        conflict = run([original, changed], calendar_rows(reserved={START}))
        self.assertEqual(conflict["coverage"]["scoped_unique_records"], 0)
        self.assertFalse(conflict["coverage"]["analysable"])

    def test_missing_invalid_and_contradictory_calendars_fail(self):
        missing = calendar_rows()
        missing[0]["days"].pop(4)
        contradictory = calendar_rows()
        contradictory[0]["days"][0]["status"]["available"] = False
        duplicate = calendar_rows()
        duplicate[0]["days"].append(deepcopy(duplicate[0]["days"][0]))
        for calendar in ([], missing, contradictory, duplicate):
            with self.subTest(calendar_size=len(calendar)), self.assertRaises(ValueError):
                run(calendar=calendar)

    def test_absent_sources_refuse_but_explicit_empty_sources_are_valid(self):
        self.assertTrue(run()["coverage"]["analysable"])
        for reservations, reviews in ((None, []), ([], None)):
            with self.assertRaises(ValueError):
                pms.analyze(PROPERTY, calendar_rows(), reservations, reviews, START, 90, AS_OF)
        with self.assertRaises(ValueError):
            pms.analyze(PROPERTY, calendar_rows(), [], [], START, 90, AS_OF.replace(tzinfo=None))


class MonetaryAndHistoricalTests(unittest.TestCase):
    def test_weighted_allocation_retains_exact_cents_after_signed_discounts(self):
        record = reservation(nights=3, total=10001, discounts=(-1000,))
        record["financials"]["host"]["accommodation_breakdown"] = [
            {"label": (START + timedelta(days=i)).isoformat(), "amount": amount}
            for i, amount in enumerate((1000, 2000, 7001))
        ]
        result = run(
            [record], calendar_rows(reserved={START + timedelta(days=i) for i in range(3)})
        )
        amounts = [row["accommodation_cents"] for row in result["daily"][:3]]
        self.assertEqual(sum(amounts), 9001)
        self.assertEqual(amounts, [900, 1800, 6301])
        self.assertEqual(result["coverage"]["allocation_methods"], {"nightly_breakdown": 1})

    def test_invalid_discount_amount_cannot_silently_become_zero(self):
        record = reservation()
        record["financials"]["host"]["discounts"] = [{"amount": "unreadable"}]
        result = run([record], calendar_rows(reserved={START}))
        self.assertEqual(result["daily"][0]["classification"], "accepted_unknown_value")

    def test_partial_breakdown_uses_equal_exact_cents(self):
        record = reservation(nights=3, total=10001)
        record["financials"]["host"]["accommodation_breakdown"] = [
            {"label": START.isoformat(), "amount": 10001}
        ]
        result = run(
            [record], calendar_rows(reserved={START + timedelta(days=i) for i in range(3)})
        )
        self.assertEqual(
            [row["accommodation_cents"] for row in result["daily"][:3]], [3334, 3334, 3333]
        )

    def test_stay_nights_split_between_historical_and_future(self):
        record = reservation(
            start=START - timedelta(days=2), nights=4, total=40000, discounts=(-4000,)
        )
        result = run([record], calendar_rows(reserved={START, START + timedelta(days=1)}))
        month = next(row for row in result["historical_months"] if row["month"] == "2032-06")
        self.assertEqual(month["positive_value_stay_nights"], 2)
        self.assertEqual(result["windows"][0]["on_books_accommodation_cents"], 18000)
        self.assertEqual(result["completed_bookings"]["bookings"], 0)

    def test_ytd_compares_equal_elapsed_days_across_leap_year(self):
        result = run()
        current = result["ytd"]["current"]
        prior = result["ytd"]["prior_same_elapsed_days"]
        self.assertEqual(current["calendar_days"], prior["calendar_days"])
        self.assertIsNone(current["accommodation_cents"])
        self.assertFalse(current["history_coverage_comparable"])
        self.assertFalse(result["coverage"]["listing_operational_start_verified"])

    def test_overlap_revenue_and_completed_cohort_are_excluded(self):
        begin = START - timedelta(days=2)
        records = [reservation("a", begin), reservation("b", begin)]
        result = run(records)
        self.assertEqual(result["completed_bookings"]["bookings"], 0)
        month = next(row for row in result["historical_months"] if row["month"] == "2032-06")
        self.assertEqual(month["overlap_nights_excluded"], 1)
        self.assertEqual(month["positive_value_stay_nights"], 0)


class PacePickupReviewTests(unittest.TestCase):
    def test_same_lead_retains_later_cancelled_bookings(self):
        prior_start = START.replace(year=START.year - 1)
        created = datetime(START.year - 1, 6, 1, 9, tzinfo=timezone.utc)
        cancelled = created + timedelta(days=20)
        record = reservation(
            "old-cancelled",
            prior_start,
            nights=3,
            total=0,
            status="cancelled",
            booked=created.isoformat(),
            history=[
                {"category": "accepted", "changed_at": created.isoformat()},
                {"category": "cancelled", "changed_at": cancelled.isoformat()},
            ],
        )
        result = run([record])
        prior = result["same_lead"]["windows"][0]["prior_same_calendar"]
        self.assertEqual(prior["reconstructed_accepted_nights"], 3)
        self.assertEqual(prior["later_cancelled_but_accepted_asof_bookings"], 1)
        self.assertEqual(result["completed_bookings"]["bookings"], 0)

    def test_pace_does_not_use_later_bookings_or_current_status_without_history(self):
        prior_start = START.replace(year=START.year - 1)
        late = reservation(
            "late",
            prior_start,
            booked=datetime(START.year - 1, 6, 11, 12, tzinfo=timezone.utc).isoformat(),
        )
        unknown = reservation(
            "unknown-history",
            prior_start + timedelta(days=1),
            booked=datetime(START.year - 1, 6, 1, 12, tzinfo=timezone.utc).isoformat(),
        )
        unknown["reservation_status"]["history"] = []
        result = run([late, unknown])
        prior = result["same_lead"]["windows"][0]["prior_same_calendar"]
        self.assertEqual(prior["reconstructed_accepted_nights"], 0)
        self.assertEqual(prior["unknown_status_records"], 1)
        self.assertIsNone(prior["reconstructed_accepted_occupancy_pct"])

    def test_pickup_uses_timestamp_boundaries_and_excludes_pending(self):
        records = [
            reservation("boundary", START, booked=(AS_OF - timedelta(days=1)).isoformat()),
            reservation(
                "too-old",
                START + timedelta(days=1),
                booked=(AS_OF - timedelta(days=1, seconds=1)).isoformat(),
            ),
            reservation(
                "pending",
                START + timedelta(days=2),
                status="request",
                booked=(AS_OF - timedelta(hours=2)).isoformat(),
            ),
        ]
        result = run(records, calendar_rows(reserved={START + timedelta(days=i) for i in range(3)}))
        self.assertEqual(result["pickup"]["last_24h"]["confirmed_positive_value_bookings"], 1)
        self.assertEqual(result["pickup"]["last_24h"]["pending_created_records"], 1)
        self.assertEqual(result["pickup"]["last_7d"]["confirmed_positive_value_bookings"], 2)

    def test_pending_pickup_uses_first_status_when_booking_date_is_null(self):
        record = reservation(
            status="request",
            history=[
                {"category": "request", "changed_at": (AS_OF - timedelta(days=4)).isoformat()},
                {"category": "request", "changed_at": (AS_OF - timedelta(hours=3)).isoformat()},
            ],
        )
        record["booking_date"] = None
        result = run([record], calendar_rows(reserved={START}))["pickup"]
        self.assertEqual(result["last_24h"]["pending_created_records"], 0)
        self.assertEqual(result["last_7d"]["pending_created_records"], 1)
        self.assertEqual(result["last_7d"]["created_records"], 1)
        self.assertEqual(result["last_7d"]["confirmed_positive_value_bookings"], 0)
        self.assertEqual(result["last_7d"]["lifecycle_timestamp_creations"], 1)
        self.assertTrue(result["last_7d"]["creation_timestamps_complete"])
        self.assertEqual(result["records_with_lifecycle_creation_timestamp"], 1)
        self.assertEqual(result["records_with_unknown_creation_timestamp"], 0)

    def test_unknown_pending_creation_time_is_explicitly_incomplete(self):
        record = reservation(status="request")
        record["booking_date"] = None
        record["reservation_status"]["history"] = [
            {"category": "request", "changed_at": "unreadable"},
            {"category": "request", "changed_at": (AS_OF + timedelta(days=1)).isoformat()},
        ]
        result = run([record], calendar_rows(reserved={START}))["pickup"]
        self.assertFalse(result["last_7d"]["creation_timestamps_complete"])
        self.assertEqual(result["last_7d"]["records_with_unknown_creation_timestamp"], 1)
        self.assertEqual(result["last_7d"]["pending_records_with_unknown_creation_timestamp"], 1)
        self.assertEqual(result["records_with_unknown_creation_timestamp"], 1)

    def test_accepted_lead_and_pickup_do_not_substitute_earlier_inquiry_time(self):
        record = reservation(
            start=START - timedelta(days=1),
            booked=(AS_OF - timedelta(days=2)).isoformat(),
            history=[
                {"category": "inquiry", "changed_at": (AS_OF - timedelta(days=30)).isoformat()},
                {"category": "accepted", "changed_at": (AS_OF - timedelta(days=2)).isoformat()},
            ],
        )
        result = run([record])
        self.assertEqual(result["pickup"]["last_7d"]["confirmed_positive_value_bookings"], 1)
        self.assertEqual(result["completed_bookings"]["median_lead_days"], 1)
        record["booking_date"] = None
        unknown = run([record])
        self.assertEqual(unknown["pickup"]["last_7d"]["confirmed_positive_value_bookings"], 0)
        self.assertEqual(unknown["pickup"]["records_with_unknown_creation_timestamp"], 1)
        self.assertEqual(unknown["completed_bookings"]["known_lead_count"], 0)
        self.assertIsNone(unknown["completed_bookings"]["median_lead_days"])

    def test_reviews_use_hospitable_five_scale_and_half_up_rounding(self):
        reviews = [
            {
                "id": "a",
                "platform": "booking",
                "reviewed_at": AS_OF.isoformat(),
                "rating": 4.85,
                "rating_platform_original": "9.70",
                "detailed_ratings": [
                    {"type": "cleanliness", "rating": 8},
                    {"type": "communication", "rating": 0},
                ],
            },
            {"id": "b", "platform": "airbnb", "reviewed_at": AS_OF.isoformat(), "rating": 5},
            {"id": "future", "reviewed_at": (AS_OF + timedelta(days=1)).isoformat(), "rating": 1},
        ]
        result = run(reviews=reviews)["reviews"]
        aggregate = result["windows"][-1]
        self.assertEqual(result["included_records"], 2)
        self.assertEqual(aggregate["mean_rating_out_of_5"], 4.93)
        self.assertEqual(aggregate["category_ratings"]["cleanliness"]["mean"], 4)
        self.assertNotIn("communication", aggregate["category_ratings"])
        self.assertFalse(result["all_time_coverage_verified"])


if __name__ == "__main__":
    unittest.main()
