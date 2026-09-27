"""A night with a price and no min-stay rule withholds its own pricing, not the whole card.

Measured live 2026-09-26 on OwnerRez (read-only, 23 properties): one property's 13 December
nights came back with rate.rent and no `rules` object, so no min stay. Each became an "unknown"
night and the whole 90-night card blocked. Their status and price are known; only the min stay
is not. They are classified, their pricing opinion is withheld and named at the top of the card.
A night with no price, or a min stay below 1, is still refused as before."""
from datetime import timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mvp_analysis import render  # noqa: E402
from _mvp_pms import normalize_reservation  # noqa: E402
from _pms_ownerrez import day_row, reservation_row  # noqa: E402
from test_mvp_analysis import METRICS, START, direct_build, synthetic_bundle  # noqa: E402
from test_pms_ownerrez import BOOK  # noqa: E402

NO_RULES = {"date": "2026-12-01T00:00:00", "status": "available",
            "rate": {"amount": 63.0, "is_spot_rate": True, "rent": 63.0, "season_id": 1}}  # live shape


def bundle_with_missing_stay(offsets, value=None):
    b = synthetic_bundle()
    for i in offsets:
        b["inputs"]["calendar"][i]["min_stay"] = value
    return b


class OwnerRezShape(unittest.TestCase):
    def test_a_night_with_no_rules_reads_as_no_min_stay(self):
        row = day_row(NO_RULES, "USD")
        self.assertEqual((row["price_cents"], row["min_stay"], row["status_reason"]), (6300, None, "AVAILABLE"))

    def test_the_airbnb_code_is_the_booking_code(self):
        r = normalize_reservation(reservation_row(dict(BOOK, platform_reservation_number="HMABC12345")))
        self.assertEqual(r["code"], "HMABC12345")
        self.assertIsNone(normalize_reservation(reservation_row(BOOK))["code"])  # VRBO: none sent


class MissingMinStayIsScoped(unittest.TestCase):
    def test_the_nights_are_classified_and_the_card_is_not_blocked(self):
        pack = direct_build(bundle_with_missing_stay(range(60, 73)))
        self.assertEqual(pack["status"], "analysable")
        pms = pack["pms"]
        self.assertTrue(pms["coverage"]["analysable"])
        self.assertIn({"code": "calendar_min_stay_missing", "count": 13}, pms["warnings"])
        missing = pack["reconciliation"]["min_stay_missing"]
        self.assertEqual(missing, [(START + timedelta(days=i)).isoformat() for i in range(60, 73)])
        rows = {r["date"]: r for r in pack["daily"]}
        for d in missing:
            self.assertEqual(rows[d]["status"], "open")
            self.assertEqual(rows[d]["action"], "pricing_opinion_withheld")
            self.assertIn("no min-stay rule", rows[d]["withheld_reason"])
        self.assertFalse(any(c["date"] in missing for c in pack["candidates"]))

    def test_the_card_names_the_nights_at_the_top(self):
        text = render(direct_build(bundle_with_missing_stay(range(60, 73))), "run", METRICS)
        self.assertIn("NO MIN-STAY RULE IN THE PMS on 13 night(s), pricing withheld on those nights only: "
                      f"{(START + timedelta(days=60)).isoformat()}", text)
        self.assertLess(text.index("NO MIN-STAY RULE"), text.index("Run run."))

    def test_a_booked_night_with_no_min_stay_is_not_listed(self):
        pack = direct_build(bundle_with_missing_stay([0]))  # offset 0 is a paid booking
        self.assertEqual(pack["reconciliation"]["min_stay_missing"], [])
        self.assertEqual(pack["status"], "analysable")

    def test_a_min_stay_below_one_is_still_refused(self):
        pack = direct_build(bundle_with_missing_stay([60], value=0))
        self.assertFalse(pack["pms"]["coverage"]["analysable"])
        self.assertEqual(pack["status"], "blocked")

    def test_a_missing_price_is_still_refused(self):
        b = synthetic_bundle()
        b["inputs"]["calendar"][60]["price_cents"] = None
        self.assertEqual(direct_build(b)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
