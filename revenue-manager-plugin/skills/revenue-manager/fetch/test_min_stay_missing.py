"""A night with a price and no min-stay rule withholds its own pricing, not the whole card.

Measured live 2026-09-26 on OwnerRez (read-only, 23 properties): one property's 13 December
nights came back with rate.rent and no `rules` object, so no min stay. Each became an "unknown"
night and the whole 90-night card blocked. Their status and price are known; only the min stay
is not. They are classified, their pricing opinion is withheld and named at the top of the card.
A night with no price, or a min stay below 1, is still refused as before.

Reported 2026-10-22 by Amanda Daigle (v5.0.10): those OwnerRez nights were 1-night minimums.
OwnerRez only sends rules.min_nights when it is 2 or more, and documents `rules` as "omitted
when no restrictions apply". 27-30 open nights per run lost their verdict while PriceLabs read
them as 1. The OwnerRez adapter now reads a priced night with no min_nights as 1. Every other
PMS still withholds a night that has a price and no min stay."""
from datetime import timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mvp_analysis import render  # noqa: E402
from _mvp_pms import analyze, normalize_reservation  # noqa: E402
from _pms_hostaway import day_row as hostaway_day_row  # noqa: E402
from _pms_ownerrez import day_row, reservation_row  # noqa: E402
from test_mvp_analysis import AS_OF, METRICS, START, direct_build, synthetic_bundle  # noqa: E402
from test_pms_ownerrez import BOOK  # noqa: E402

NO_RULES = {"date": "2026-12-01T00:00:00", "status": "available",
            "rate": {"amount": 63.0, "is_spot_rate": True, "rent": 63.0, "season_id": 1}}  # live shape
DECEMBER = range(60, 73)  # 13 open nights in the synthetic bundle, like the live December run


def bundle_with_missing_stay(offsets, value=None):
    b = synthetic_bundle()
    for i in offsets:
        b["inputs"]["calendar"][i]["min_stay"] = value
    return b


def bundle_with_pms_rows(offsets, row_for):
    """The synthetic bundle with these nights replaced by a real adapter's own mapping of them."""
    b = synthetic_bundle()
    for i in offsets:
        b["inputs"]["calendar"][i] = row_for((START + timedelta(days=i)).isoformat())
    return b


def facts_and_pack(bundle):
    """The PMS facts per night (pack["pms"] carries only the summary) and the built card."""
    src = bundle["inputs"]
    facts = analyze(src["property"], src["calendar"], src["reservations"]["data"], src["reviews"]["data"],
                    START, 90, AS_OF)
    return {r["date"]: r for r in facts["daily"]}, direct_build(bundle, facts)


def ownerrez_open_night(day, **rules):
    raw = {"date": f"{day}T00:00:00", "status": "available",
           "rate": {"amount": 120.0, "is_spot_rate": True, "rent": 120.0}}
    if rules:
        raw["rules"] = rules
    return day_row(raw, "CAD")


class OwnerRezShape(unittest.TestCase):
    def test_a_priced_night_with_no_rules_is_a_one_night_minimum(self):
        row = day_row(NO_RULES, "USD")
        self.assertEqual((row["price_cents"], row["min_stay"], row["status_reason"]), (6300, 1, "AVAILABLE"))

    def test_rules_without_min_nights_is_a_one_night_minimum(self):
        row = day_row(dict(NO_RULES, rules={"is_arrival_disallowed": True}), "USD")
        self.assertEqual((row["min_stay"], row["closed_for_checkin"]), (1, True))

    def test_a_sent_min_nights_is_kept(self):
        for sent in (1, 2, 4):
            with self.subTest(sent=sent):
                self.assertEqual(day_row(dict(NO_RULES, rules={"min_nights": sent}), "USD")["min_stay"], sent)

    def test_an_unpriced_night_keeps_no_min_stay(self):
        row = day_row({"date": "2026-12-01T00:00:00", "status": "available"}, "USD")
        self.assertEqual((row["price_cents"], row["min_stay"]), (None, None))

    def test_an_unreadable_min_nights_is_not_read_as_one(self):
        for sent in ("3", 2.5, True, None):
            with self.subTest(sent=sent):
                self.assertIsNone(day_row(dict(NO_RULES, rules={"min_nights": sent}), "USD")["min_stay"])

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


class OwnerRezOneNightMinimum(unittest.TestCase):
    """Amanda Daigle, 2026-10-22: OwnerRez leaves min_nights out on a 1-night minimum."""

    def test_the_nights_get_a_verdict(self):
        facts, pack = facts_and_pack(bundle_with_pms_rows(DECEMBER, ownerrez_open_night))
        self.assertEqual(pack["status"], "analysable")
        self.assertFalse(any(w["code"] == "calendar_min_stay_missing" for w in pack["pms"]["warnings"]))
        self.assertEqual(pack["reconciliation"]["min_stay_missing"], [])
        rows = {r["date"]: r for r in pack["daily"]}
        for i in DECEMBER:
            d = (START + timedelta(days=i)).isoformat()
            with self.subTest(date=d):
                self.assertEqual(facts[d]["min_stay"], 1)
                self.assertFalse(facts[d]["min_stay_missing"])
                self.assertEqual(rows[d]["status"], "open")
                self.assertNotEqual(rows[d]["action"], "pricing_opinion_withheld")
                self.assertNotIn("withheld_reason", rows[d])

    def test_a_sent_min_nights_is_used_as_sent(self):
        facts, pack = facts_and_pack(bundle_with_pms_rows(DECEMBER, lambda d: ownerrez_open_night(d, min_nights=3)))
        self.assertEqual({facts[(START + timedelta(days=i)).isoformat()]["min_stay"] for i in DECEMBER}, {3})
        self.assertEqual(pack["reconciliation"]["min_stay_missing"], [])


class OtherPmsStillWithholds(unittest.TestCase):
    def test_a_hostaway_night_with_no_minimum_stay_is_still_withheld(self):
        def hostaway_open_night(day):
            return hostaway_day_row({"date": day, "status": "available", "isAvailable": 1, "price": 120,
                                     "closedOnArrival": 0, "closedOnDeparture": 0}, "CAD")
        facts, pack = facts_and_pack(bundle_with_pms_rows(DECEMBER, hostaway_open_night))
        self.assertEqual(pack["status"], "analysable")
        self.assertIn({"code": "calendar_min_stay_missing", "count": 13}, pack["pms"]["warnings"])
        missing = [(START + timedelta(days=i)).isoformat() for i in DECEMBER]
        self.assertEqual(pack["reconciliation"]["min_stay_missing"], missing)
        rows = {r["date"]: r for r in pack["daily"]}
        for d in missing:
            self.assertIsNone(facts[d]["min_stay"])
            self.assertTrue(facts[d]["min_stay_missing"])
            self.assertEqual(rows[d]["action"], "pricing_opinion_withheld")


if __name__ == "__main__":
    unittest.main()
