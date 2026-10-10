"""S2-023 and S2-024, Hostfully, from a Summit student report (2026-09-29).

S2-023: every Hostfully review was dropped ("Every fetched review has an unreadable date or
rating"). Hostfully's v3.3 schema documents the review `date` as `format: date`, a plain day,
and the runner only reads full timestamps. Separately, Hostfully stores only the reviews it
holds (the student had 1 there and 159 on Airbnb): the card now says so instead of reading
the count as the listing's.

S2-024: pacing read NO DATA on every Hostfully card. A lead carries no status history, so
at last year's comparison date every booking read "unknown", and one unknown blanks the
comparison. The lead's own timestamps prove part of the history (schema LeadApiV3_1Dto:
`bookedUtcDateTime`, `metadata.createdUtcDateTime`, `metadata.updatedUtcDateTime`, `type`):
booked at bookedUtcDateTime; a request or inquiry from creation; a cancelled or declined lead
reached that state by its last update at the latest, and in between it stays unknown.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from _mvp_pms import analyze, normalize_reservation
from _pms_fakes import FakeOpener, connections, read_client
from _pms_hostfully import HostfullySource, reservation_row, review_row
from test_pms_hostfully import AGENCY, KEY, P, PID, PROP, entry, order

START = date(2031, 6, 10)
AS_OF = datetime(2031, 6, 9, 18, tzinfo=timezone.utc)
STLY = AS_OF - timedelta(days=365)


def hf_lead(uid, check_in, check_out, status="BOOKED", kind="BOOKING", booked=None, created=None,
            updated=None):
    raw = {"uid": uid, "propertyUid": PID, "agencyUid": AGENCY, "status": status, "type": kind,
           "channel": "AIRBNB", "checkInLocalDateTime": f"{check_in}T16:00:00",
           "checkOutLocalDateTime": f"{check_out}T10:00:00", "metadata": {}}
    if booked:
        raw["bookedUtcDateTime"] = booked
    if created:
        raw["metadata"]["createdUtcDateTime"] = created
    if updated:
        raw["metadata"]["updatedUtcDateTime"] = updated
    return raw


def facts_for(leads, reviews=()):
    cal = [entry((START + timedelta(days=i)).isoformat()) for i in range(30)]
    opener = FakeOpener({
        ("GET", f"{P}/properties"): {"properties": [PROP], "_metadata": {"totalCount": 1}, "_paging": {}},
        ("GET", f"{P}/leads"): {"leads": list(leads), "_metadata": {"totalCount": len(leads)}},
        ("GET", f"{P}/orders"): {"orders": [order(x["uid"], 600.0) for x in leads], "_metadata": {}},
        ("GET", f"{P}/property-calendar/{PID}"): {"calendar": {"propertyUid": PID, "entries": cal}},
        ("GET", f"{P}/reviews"): {"reviews": list(reviews), "_metadata": {"totalCount": len(reviews)}},
    })
    src = HostfullySource(read_client(opener), connections(HOSTFULLY_API_KEY=KEY, HOSTFULLY_AGENCY_UID=AGENCY))
    reviews_in = src.reviews(PID)
    facts = analyze(src.property(PID), src.calendar(PID, START, 30), src.reservations(PID, START, 30)["data"],
                    reviews_in["data"], START, 30, AS_OF)
    return facts, reviews_in


def stly(facts):
    return next(w for w in facts["same_lead"]["windows"] if w["days"] == 30)["prior_same_calendar"]


def warned(facts, code):
    return next((w["count"] for w in facts["warnings"] if w["code"] == code), 0)


class PlainDayReviewDates(unittest.TestCase):
    def test_a_review_dated_as_a_plain_day_is_read(self):
        review = {"uid": "r1", "propertyUid": PID, "rating": 5, "source": "AIRBNB", "date": "2031-05-20"}
        facts, _ = facts_for([], [review])
        self.assertEqual(facts["reviews"]["included_records"], 1)
        self.assertEqual(warned(facts, "review_date_or_rating_invalid"), 0)

    def test_the_day_becomes_midnight_utc(self):
        self.assertEqual(review_row({"uid": "r", "rating": 5, "source": "VRBO", "date": "2023-06-18"})["reviewed_at"],
                         "2023-06-18T00:00:00Z")

    def test_a_future_or_garbage_date_is_still_refused(self):
        bad = [{"uid": "r1", "propertyUid": PID, "rating": 5, "source": "VRBO", "date": "2031-13-40"},
               {"uid": "r2", "propertyUid": PID, "rating": 5, "source": "VRBO", "date": "2032-01-01"}]
        facts, _ = facts_for([], bad)
        self.assertEqual(facts["reviews"]["included_records"], 0)
        self.assertEqual(warned(facts, "review_date_or_rating_invalid"), 2)

    def test_no_airbnb_review_on_an_airbnb_listed_property_is_named(self):
        _, reviews_in = facts_for([], [{"uid": "r1", "propertyUid": PID, "rating": 5, "source": "HOSTFULLY",
                                        "date": "2031-05-20"}])
        self.assertIn("no Airbnb reviews", reviews_in["gap"])
        _, with_airbnb = facts_for([], [{"uid": "r1", "propertyUid": PID, "rating": 5, "source": "AIRBNB",
                                         "date": "2031-05-20"}])
        self.assertIsNone(with_airbnb.get("gap"))


class StatusHistoryFromTheLeadsOwnTimestamps(unittest.TestCase):
    LAST_YEAR_IN, LAST_YEAR_OUT = "2030-06-12", "2030-06-15"   # inside last year's 30-night window

    def test_a_booking_made_before_last_years_comparison_date_counts(self):
        booked = hf_lead("L1", self.LAST_YEAR_IN, self.LAST_YEAR_OUT, booked="2030-05-01T10:00:00")
        pace = stly(facts_for([booked])[0])
        self.assertEqual(pace["unknown_status_records"], 0)
        self.assertEqual(pace["reconstructed_accepted_nights"], 3)
        self.assertIsNotNone(pace["reconstructed_accepted_occupancy_pct"])

    def test_a_booking_made_after_the_comparison_date_was_not_on_the_books_yet(self):
        late = hf_lead("L1", self.LAST_YEAR_IN, self.LAST_YEAR_OUT, booked=(STLY + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"))
        pace = stly(facts_for([late])[0])
        self.assertEqual((pace["unknown_status_records"], pace["reconstructed_accepted_nights"]), (0, 0))

    def test_a_cancellation_finished_before_the_comparison_date_is_not_counted(self):
        gone = hf_lead("L1", self.LAST_YEAR_IN, self.LAST_YEAR_OUT, status="CANCELLED",
                       booked="2030-04-01T10:00:00", updated="2030-04-20T10:00:00")
        pace = stly(facts_for([gone])[0])
        self.assertEqual((pace["unknown_status_records"], pace["reconstructed_accepted_nights"]), (0, 0))

    def test_a_cancellation_whose_time_is_not_known_stays_unknown(self):
        # last update after the comparison date: cancelled before or after it is not knowable
        maybe = hf_lead("L1", self.LAST_YEAR_IN, self.LAST_YEAR_OUT, status="CANCELLED",
                        booked="2030-04-01T10:00:00", updated="2030-06-20T10:00:00")
        pace = stly(facts_for([maybe])[0])
        self.assertEqual(pace["unknown_status_records"], 1)
        self.assertIsNone(pace["reconstructed_accepted_occupancy_pct"])

    def test_a_declined_request_was_a_request_until_it_was_declined(self):
        declined = hf_lead("L1", self.LAST_YEAR_IN, self.LAST_YEAR_OUT, status="DECLINED", kind="BOOKING_REQUEST",
                           created="2030-05-01T10:00:00", updated="2030-06-20T10:00:00")
        pace = stly(facts_for([declined])[0])
        self.assertEqual((pace["unknown_status_records"], pace["reconstructed_pending_nights"]), (0, 3))

    def test_an_inquiry_never_holds_nights(self):
        ask = hf_lead("L1", self.LAST_YEAR_IN, self.LAST_YEAR_OUT, status="NEW", kind="INQUIRY",
                      created="2030-05-01T10:00:00")
        pace = stly(facts_for([ask])[0])
        self.assertEqual((pace["unknown_status_records"], pace["reconstructed_accepted_nights"],
                          pace["reconstructed_pending_nights"]), (0, 0, 0))

    def test_a_booking_with_no_booked_time_is_not_guessed(self):
        undated = hf_lead("L1", self.LAST_YEAR_IN, self.LAST_YEAR_OUT, created="2030-05-01T10:00:00")
        self.assertEqual(stly(facts_for([undated])[0])["unknown_status_records"], 1)

    def test_history_survives_normalization(self):
        raw = reservation_row(hf_lead("L1", "2031-06-12", "2031-06-15", booked="2031-05-01T10:00:00"), None)
        history = normalize_reservation(raw)["status_history"]
        self.assertEqual(history, [{"category": "accepted", "changed_at": "2031-05-01T10:00:00Z"}])


if __name__ == "__main__":
    unittest.main()


class TheCardSaysWhoseReviewsItRead(unittest.TestCase):
    def test_a_review_source_gap_is_a_note_on_the_card(self):
        from _mvp_analysis import render
        from test_mvp_analysis import METRICS, compute, synthetic_bundle
        b = synthetic_bundle()
        b["inputs"]["reviews"]["gap"] = "REVIEWS FROM THE PMS ONLY: test gap"
        result = compute(b)
        self.assertIn("REVIEWS FROM THE PMS ONLY: test gap", result["notes"])
        self.assertIn("REVIEWS FROM THE PMS ONLY: test gap", render(result, "run", METRICS))
