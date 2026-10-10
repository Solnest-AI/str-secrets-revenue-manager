"""S2-007, analysis side: the card reads each night's min/max from the seasonal profile.

A Summit student (2026-09-29), on a listing with full-year fixed seasons: the card said "keep min at
435" and "0 of 19 nights at min" while 37 of 112 nights sat at the $275-300 seasonal floor;
DSO suggestions were justified against the $435 listing min when the real floor was $300; nights
pinned at the seasonal max were invisible. Here the same shape at the synthetic bundle's scale:
listing min/base/max 150/180/400, full-year fixed seasons whose floors are 100 (summer) and 90
(fall) and whose ceiling is 130.
"""

from __future__ import annotations

import copy
import unittest
from datetime import timedelta

from _mvp_analysis import render
from test_mvp_analysis import METRICS, START, compute, synthetic_bundle
from test_seasonal_write import profile, season

SEASONS = profile([
    season("Winter", 1, 1, 3, 31, 95, 120, 130),
    season("Spring", 4, 1, 5, 31, 95, 120, 130),
    season("Summer", 6, 1, 8, 31, 100, 120, 130),
    season("Fall", 9, 1, 12, 31, 90, 120, 130),
])
AT_FLOOR = range(6, 21)          # 15 open June nights at the summer floor (100)
AT_CEILING = range(40, 46)       # 6 open July nights pinned at the seasonal max (130)
FALL_FLOOR = range(83, 90)       # 7 open September nights at the fall floor (90)


def seasonal_bundle(csp=SEASONS, listing=(150, 180, 400)):
    b = synthetic_bundle()
    src = b["inputs"]
    src["listing"].update(dict(zip(("min", "base", "max"), listing)))
    src["rules"]["raw"]["custom_seasonal_profile"] = copy.deepcopy(csp)

    def set_price(offset, price):
        src["prices"]["data"][offset]["price"] = price
        src["calendar"][offset]["price_cents"] = int(price * 100)

    for i in AT_FLOOR:
        set_price(i, 100)
    for i in AT_CEILING:
        set_price(i, 130)
    for i in FALL_FLOOR:
        set_price(i, 90)
    set_price(10, 110)
    return b


def day(offset):
    return (START + timedelta(days=offset)).isoformat()


class NightsAtTheSeasonalFloorAndCeiling(unittest.TestCase):
    def setUp(self):
        self.result = compute(seasonal_bundle())

    def test_open_nights_at_the_seasonal_min_count_as_at_min(self):
        rows = {r["date"]: r for r in self.result["daily"]}
        self.assertTrue(all(rows[day(i)]["at_floor"] for i in AT_FLOOR if i != 10))
        self.assertTrue(all(rows[day(i)]["at_floor"] for i in FALL_FLOOR))
        w30 = next(w for w in self.result["windows"] if w["days"] == 30)
        self.assertEqual(w30["floor_open"], 14)  # 15 summer-floor nights minus the one at 110
        self.assertEqual(self.result["min_price"]["floor_open_near"], 14)

    def test_open_nights_pinned_at_the_seasonal_max_are_visible(self):
        w90 = next(w for w in self.result["windows"] if w["days"] == 90)
        self.assertEqual(w90["ceiling_open"], 6)

    def test_each_night_carries_the_bound_that_prices_it(self):
        rows = {r["date"]: r for r in self.result["daily"]}
        self.assertEqual(rows[day(7)]["night_min"], 100)
        self.assertEqual(rows[day(85)]["night_min"], 90)
        self.assertEqual(rows[day(42)]["night_max"], 130)


class MinRecommendationNamesTheMinThatApplies(unittest.TestCase):
    def test_the_min_line_is_about_the_seasonal_min_not_the_unused_listing_min(self):
        result = compute(seasonal_bundle())
        mp = result["min_price"]
        self.assertEqual(mp["current"], 100)
        self.assertEqual(mp["applies_to"], "season")
        self.assertIn("Jun 1 to Aug 31", mp["season"])
        brief = render(result, "run", METRICS)
        line = next(x for x in brief.splitlines() if x.startswith("Recommended min price"))
        self.assertNotIn("currently 150", line)
        self.assertIn("150", line)                # the listing-level min is named as unused
        self.assertIn("seasonal", line.lower())
        self.assertNotIn("—", brief)

    def test_bounds_line_says_the_seasons_set_their_own(self):
        brief = render(compute(seasonal_bundle()), "run", METRICS)
        line = next(x for x in brief.splitlines() if "Bounds min/base/max" in x)
        self.assertIn("seasonal profile", line.lower())
        self.assertIn("90 of these 90 nights", line)


class ReviewRangesUseTheNightsSeasonalBounds(unittest.TestCase):
    def test_a_cut_range_opens_down_to_the_seasonal_floor(self):
        result = compute(seasonal_bundle())
        cand = {c["date"]: c for c in result["candidates"]}
        # 110 net, 132 on Airbnb vs p75 95: a cut. Before the fix the floor was the unused
        # listing min (150), so no cut fit and the night read "no reduction range within bounds".
        self.assertIn(day(10), cand)
        self.assertEqual(cand[day(10)]["review_net_range"], [100, 104])

    def test_an_existing_dso_above_the_seasonal_floor_is_not_flagged_below_the_min(self):
        b = seasonal_bundle()
        b["inputs"]["overrides"] = [{"date": day(30), "price": "120", "price_type": "fixed"}]
        flags = {d["date"]: d["flags"] for d in compute(b)["rules_first"]["existing_dsos"]}
        self.assertFalse([f for f in flags[day(30)] if "below the min" in f], flags[day(30)])


    def test_a_dso_between_the_listing_min_and_a_higher_seasonal_min_is_flagged(self):
        # review of PR #1: listing min 90, a fixed season lifts the night's min to 100, DSO at 95
        b = seasonal_bundle(csp=profile([season("All", 1, 1, 12, 31, 100, 120, 130)]),
                            listing=(90, 120, 400))
        b["inputs"]["overrides"] = [{"date": day(30), "price": "95", "price_type": "fixed"}]
        b["inputs"]["prices"]["data"][30]["price"] = 95
        b["inputs"]["calendar"][30]["price_cents"] = 9500
        flags = {d["date"]: d["flags"] for d in compute(b)["rules_first"]["existing_dsos"]}
        self.assertIn("below the min: fixed 95 under that night's seasonal min 100", flags[day(30)])


class SeasonsThatDoNotApplyChangeNothing(unittest.TestCase):
    def test_a_profile_switched_off_keeps_the_listing_bounds(self):
        off = copy.deepcopy(SEASONS)
        off["custom_seasonal_profile_on"] = False
        result = compute(seasonal_bundle(csp=off, listing=(100, 120, 400)))
        mp = result["min_price"]
        self.assertEqual(mp["current"], 100)
        self.assertNotEqual(mp.get("applies_to"), "season")
        self.assertEqual(next(w for w in result["windows"] if w["days"] == 90)["ceiling_open"], 0)

    def test_an_unreadable_profile_is_named_and_the_listing_bounds_are_used(self):
        bad = copy.deepcopy(SEASONS)
        bad["custom_seasonal_profile"]["seasons"][0]["start_month"] = "13"
        result = compute(seasonal_bundle(csp=bad))
        self.assertTrue(any("SEASONAL PROFILE UNREADABLE" in n for n in result["notes"]),
                        result["notes"])
        self.assertEqual(result["min_price"]["current"], 150)

    def test_a_percentage_season_scales_the_listing_min(self):
        pct = profile([season("All", 1, 1, 12, 31, -10, None, None)], price_type="percentage")
        result = compute(seasonal_bundle(csp=pct, listing=(100, 120, 400)))
        rows = {r["date"]: r for r in result["daily"]}
        self.assertEqual(rows[day(7)]["night_min"], 90)    # 100 x (1 - 10%)
        self.assertEqual(rows[day(85)]["at_floor"], True)  # priced 90


if __name__ == "__main__":
    unittest.main()
