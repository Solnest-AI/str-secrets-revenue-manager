"""_seasons: which value prices each night under a PriceLabs custom seasonal profile (S2-007)."""

from __future__ import annotations

import copy
import unittest
from datetime import date

import _seasons as S
from test_seasonal_write import APRIL_ONLY, FULL_YEAR, profile, season

LISTING = {"min": 435.0, "base": 510.0, "max": 1400.0}


class Resolve(unittest.TestCase):
    def test_fixed_season_replaces_the_listing_values(self):
        n = S.from_rule(FULL_YEAR).night(date(2026, 10, 20), LISTING)
        self.assertEqual((n["min"], n["base"], n["max"]), (300, 495, 700))
        self.assertTrue(all(n["fixed"].values()))
        self.assertEqual(n["set_by"]["min"], "season Sep 1 to Dec 31")

    def test_empty_field_and_uncovered_date_use_the_listing_value(self):
        p = S.from_rule(APRIL_ONLY)
        april = p.night(date(2027, 4, 12), LISTING)
        self.assertEqual((april["min"], april["max"]), (500, 1400.0))
        self.assertIsNone(april["set_by"]["max"])
        may = p.night(date(2027, 5, 1), LISTING)
        self.assertEqual((may["min"], may["base"], may["max"]), (435.0, 510.0, 1400.0))
        self.assertIsNone(may["set_by"]["min"])

    def test_percentage_scales_each_listing_value_by_its_own_field(self):
        p = S.from_rule(profile([season("All", 1, 1, 12, 31, 15, -5, 10)], price_type="percentage"))
        n = p.night(date(2026, 7, 4), {"min": 99.0, "base": 132.0, "max": 199.0})
        self.assertEqual((n["min"], n["base"], n["max"]), (113.85, 125.4, 218.9))
        self.assertFalse(any(n["fixed"].values()))

    def test_one_off_beats_repeating_and_only_in_its_year(self):
        csp = profile([season("Fall", 9, 1, 12, 31, 300, 495, 700)],
                      one_off=[{**season("Event", 10, 18, 10, 25, 600, 800, 1500),
                                "start_year": "2026", "end_year": "2026"}])
        p = S.from_rule(csp)
        self.assertEqual(p.night(date(2026, 10, 20), LISTING)["min"], 600)
        self.assertEqual(p.night(date(2027, 10, 20), LISTING)["min"], 300)

    def test_feb_29_season_end_covers_feb_28_in_a_common_year(self):
        p = S.from_rule(profile([season("Feb", 2, 1, 2, 29, 200, None, None)]))
        self.assertEqual(p.night(date(2027, 2, 28), LISTING)["min"], 200)
        self.assertEqual(p.night(date(2027, 3, 1), LISTING)["min"], 435.0)
        self.assertEqual(p.night(date(2028, 2, 29), LISTING)["min"], 200)

    def test_a_season_starting_feb_29_starts_mar_1_in_a_common_year(self):
        # DOCS-ONLY: PriceLabs validates seasons on a leap-year calendar; what it does with a
        # Feb 29 start in a common year is not documented. Here Feb 28 stays on the listing
        # value and Mar 1 is the first covered night.
        p = S.from_rule(profile([season("Leap", 2, 29, 3, 10, 200, None, None)]))
        self.assertEqual(p.night(date(2027, 2, 28), LISTING)["min"], 435.0)
        self.assertEqual(p.night(date(2027, 3, 1), LISTING)["min"], 200)
        self.assertEqual(p.night(date(2028, 2, 29), LISTING)["min"], 200)

    def test_reach_counts_nights_a_fixed_season_holds(self):
        self.assertEqual(S.from_rule(FULL_YEAR).reach("min", date(2026, 10, 1), 365)["moves"], 0)
        r = S.from_rule(APRIL_ONLY).reach("min", date(2026, 10, 1), 365)
        self.assertEqual((r["moves"], r["season_set"]), (335, 30))
        self.assertEqual(r["seasons"], [{"season": "season Apr 1 to Apr 30", "nights": 30, "value": 500}])


class OffOrAbsent(unittest.TestCase):
    def test_switched_off_including_the_string_false_is_none(self):
        for toggle in (False, "false", "0", None):
            cfg = copy.deepcopy(FULL_YEAR)
            cfg["custom_seasonal_profile_on"] = toggle
            with self.subTest(toggle=toggle):
                self.assertIsNone(S.from_rule(cfg))

    def test_on_with_no_seasons_is_none(self):
        self.assertIsNone(S.from_rule({"custom_seasonal_profile_on": True,
                                       "custom_seasonal_profile": {"seasons": None, "price_type": None}}))

    def test_stack_listing_first_then_group_then_account(self):
        off = copy.deepcopy(FULL_YEAR)
        off["custom_seasonal_profile_on"] = False
        prof, level = S.from_stack({"custom_seasonal_profile": off},
                                   {"custom_seasonal_profile": APRIL_ONLY}, {"custom_seasonal_profile": FULL_YEAR})
        self.assertEqual(level, "group")
        self.assertEqual(prof.night(date(2027, 4, 2), LISTING)["min"], 500)
        self.assertEqual(S.from_stack({}, None, None), (None, None))


class Unreadable(unittest.TestCase):
    def bad(self, mutate):
        cfg = copy.deepcopy(FULL_YEAR)
        mutate(cfg["custom_seasonal_profile"])
        with self.assertRaises(S.SeasonUnreadable) as caught:
            S.from_rule(cfg)
        return str(caught.exception)

    def test_a_season_that_wraps_the_year_end(self):
        def wrap(p):
            p["seasons"] = [season("Holidays", 12, 15, 1, 5, 400, None, None)]
        self.assertIn("ends before it starts", self.bad(wrap))

    def test_two_repeating_seasons_that_overlap(self):
        def overlap(p):
            p["seasons"][1]["start_month"] = "3"  # Spring now starts inside Winter
        self.assertIn("overlap", self.bad(overlap))

    def test_a_date_that_does_not_exist(self):
        def bad_day(p):
            p["seasons"][0]["end_day"] = "32"
        self.assertIn("does not exist", self.bad(bad_day))

    def test_no_price_type(self):
        self.assertIn("price_type", self.bad(lambda p: p.update(price_type=None)))

    def test_a_boolean_price(self):
        def boolean(p):
            p["seasons"][0]["lowest_price"] = True
        self.bad(boolean)

    def test_inherited_season_base_prices(self):
        self.assertIn("inherit_baseprice", self.bad(lambda p: p.update(inherit_baseprice="1")))

    def test_overlap_across_repeating_and_one_off_is_allowed(self):
        cfg = copy.deepcopy(FULL_YEAR)
        cfg["custom_seasonal_profile"]["non_repeating_seasons"] = [
            {**season("Event", 10, 18, 10, 25, 600, None, None), "start_year": "2026", "end_year": "2026"}]
        cfg["custom_seasonal_profile"]["price_type_non_repeating"] = "fixed"
        self.assertIsNotNone(S.from_rule(cfg))


class Fingerprint(unittest.TestCase):
    def test_moves_when_a_season_value_or_the_level_moves(self):
        a = S.fingerprint(APRIL_ONLY, "listing")
        changed = copy.deepcopy(APRIL_ONLY)
        changed["custom_seasonal_profile"]["seasons"][0]["lowest_price"] = 550
        self.assertNotEqual(a, S.fingerprint(changed, "listing"))
        self.assertNotEqual(a, S.fingerprint(APRIL_ONLY, "group"))
        self.assertEqual(a, S.fingerprint(copy.deepcopy(APRIL_ONLY), "listing"))


if __name__ == "__main__":
    unittest.main()
