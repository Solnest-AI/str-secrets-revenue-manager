"""apply_pace: a funnel dip against similar listings only stands as a break when bookings trail
the market too (Ryan, 2026-09-26; RankBreeze's similar-listings rates are Airbnb per-day averages)."""
import copy
import unittest

import flywheel

# Live RankBreeze numbers, 2026-09-25 pulls.
URBAN_NEST = {  # 1,499 views vs 390, booking rate 0.97% vs 34.06%
    "first_page_impressions": {"listing": 3259, "similar_listings": 2330},
    "click_through_rate": {"listing": 56.57, "similar_listings": 14.13},
    "view": {"listing": 1499, "similar_listings": 390},
    "wishlist": {"listing": 5, "similar_listings": 2},
    "booking_rate": {"listing": 0.97, "similar_listings": 34.06},
    "conversion_rate": {"listing": 0.42, "similar_listings": 6.27},
}


def wheel(comparison):
    vis = flywheel.spoke_visibility({"integration_status": "active",
                                     "similar_listings_comparison": comparison})
    ok = {"ok": True, "detail": "ok"}
    return flywheel.gate("L", vis, dict(ok, spoke="bookings"), dict(ok, spoke="reviews"),
                         dict(ok, spoke="ranking"))


class ApplyPace(unittest.TestCase):
    def test_the_raw_comparison_still_calls_it_a_break(self):
        self.assertEqual(wheel(URBAN_NEST)["spokes"]["visibility"]["diagnosis"]["verdict"], "break")

    def test_selling_ahead_of_the_market_is_not_a_funnel_problem(self):
        # Sunburst Chalet: booking rate 10.51% vs 29.36%, but 44% booked vs the market's 13%
        out = flywheel.apply_pace(wheel(URBAN_NEST), 44.0, 13.0, 30)
        diag = out["spokes"]["visibility"]["diagnosis"]
        self.assertEqual(diag["verdict"], "selling")
        self.assertIn("traffic arithmetic, not a problem", out["spokes"]["visibility"]["detail"])
        self.assertIn("not a funnel problem", out["headline"])
        self.assertEqual(diag["pace"], {"listing_pct": 44.0, "market_pct": 13.0, "window_days": 30})

    def test_trailing_the_market_keeps_the_break_and_says_so(self):
        # Urban Nest: 10% booked vs the market's 40%
        out = flywheel.apply_pace(wheel(URBAN_NEST), 10.0, 40.0, 30)
        self.assertEqual(out["spokes"]["visibility"]["diagnosis"]["verdict"], "break")
        self.assertIn("Bookings trail the market too (10% booked vs the market's 40%", out["headline"])

    def test_the_five_point_band(self):
        self.assertEqual(flywheel.apply_pace(wheel(URBAN_NEST), 44.0, 49.0)["spokes"]["visibility"]
                         ["diagnosis"]["verdict"], "selling")
        self.assertEqual(flywheel.apply_pace(wheel(URBAN_NEST), 43.9, 49.0)["spokes"]["visibility"]
                         ["diagnosis"]["verdict"], "break")

    def test_unknown_occupancy_changes_nothing(self):
        for listing, market in ((None, 40.0), (10.0, None), (None, None)):
            before = wheel(URBAN_NEST)
            snapshot = copy.deepcopy(before)
            with self.subTest(listing=listing, market=market):
                self.assertEqual(flywheel.apply_pace(before, listing, market), snapshot)

    def test_a_healthy_funnel_is_left_alone(self):
        healthy = {k: {"listing": v["similar_listings"], "similar_listings": v["similar_listings"]}
                   for k, v in URBAN_NEST.items()}
        before = wheel(healthy)
        snapshot = copy.deepcopy(before)
        self.assertEqual(flywheel.apply_pace(before, 90.0, 10.0), snapshot)


if __name__ == "__main__":
    unittest.main()
