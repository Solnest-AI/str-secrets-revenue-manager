"""The FLYWHEEL block: six rows, one verdict each, the first break named in plain English."""

from __future__ import annotations

import unittest

import flywheel_brief as fb


def stage(name, mine, theirs):
    return {"stage": name, "listing": mine, "similar": theirs, "delta": mine - theirs}


def pack(*, stages=None, verdict="healthy", broke=None, occ=47.0, market=50.5, rating=4.9, count=15,
         page=1, cur=18, prior=15, pick7=3, vis_ok=True):
    diag = {"verdict": verdict, "stage": broke, "stages": stages or []}
    return {
        "flywheel": {"spokes": {
            "visibility": {"ok": vis_ok, "diagnosis": diag if vis_ok else None,
                           "detail": "RankBreeze integration_status is 'deactivated'" if not vis_ok else ""},
            "reviews": {"ok": True, "rating": rating, "count": count},
            "ranking": {"ok": True, "worst_page": page, "best_position": 4},
        }},
        "windows": [{"days": 30, "occupancy_pct": occ, "market_occupancy_pct": market}],
        "pms": {"same_lead": {"windows": [{"days": 30, "current": {"reconstructed_accepted_nights": cur},
                                          "prior_same_calendar": {"reconstructed_accepted_nights": prior}}]},
                "pickup": {"last_7d": {"confirmed_positive_value_bookings": pick7}}},
    }


HEALTHY = [stage("first_page_impressions", 1240, 980), stage("click_through_rate", 3.1, 2.8),
           stage("view", 210, 190), stage("wishlist", 14, 11), stage("booking_rate", 30, 28),
           stage("conversion_rate", 2, 2)]


class Rows(unittest.TestCase):
    def test_six_rows_in_flywheel_order_with_plain_numbers(self):
        table = fb.rows(pack(stages=HEALTHY))
        self.assertEqual([r[0] for r in table], ["Visibility", "Views", "Bookings", "Reviews", "Ranking", "Pacing"])
        by = {r[0]: r for r in table}
        self.assertIn("Seen in search 1,240 times vs 980 for similar listings. 3.1% of them click it vs 2.8%.", by["Visibility"][2])
        self.assertEqual(by["Visibility"][1], "✅ AHEAD")
        self.assertIn("210 page views vs 190", by["Views"][2])
        self.assertIn("Next 30 nights: 47% booked vs the market's 50.5%.", by["Bookings"][2])
        self.assertEqual(by["Bookings"][1], "✅ OK")
        self.assertEqual(by["Reviews"], ("Reviews", "✅ OK", "4.9 stars across 15 reviews."))
        self.assertEqual(by["Ranking"][1], "✅ OK")
        self.assertIn("Shows on page 1 of search, best position 4.", by["Ranking"][2])
        self.assertEqual(by["Pacing"][1], "✅ AHEAD")
        self.assertIn("18 nights booked for the next 30 days vs 15 at this point last year. 3 new bookings in the last 7 days.", by["Pacing"][2])
        self.assertIn("Nothing breaks: every stage", fb.break_line(pack(stages=HEALTHY), table))

    def test_a_funnel_break_names_the_stage_in_plain_english(self):
        stages = HEALTHY[:4] + [stage("booking_rate", 4.66, 33.81)]
        p = pack(stages=stages, verdict="break", broke="booking_rate", occ=30, market=50)
        table = fb.rows(p)
        by = {r[0]: r for r in table}
        self.assertEqual(by["Bookings"][1], "⚠️ BEHIND")
        self.assertIn("4.7% of lookers book vs 33.8%", by["Bookings"][2])
        self.assertEqual(fb.break_line(p, table),
                         "Where it breaks: Bookings. " + fb.BREAK_MEANING["booking_rate"])

    def test_stages_past_the_break_say_not_measured_never_a_bare_number(self):
        p = pack(stages=[stage("first_page_impressions", 100, 900)], verdict="break", broke="first_page_impressions")
        by = {r[0]: r for r in fb.rows(p)}
        self.assertEqual(by["Visibility"][1], "⚠️ BEHIND")
        self.assertEqual(by["Views"][1], "❌ NO DATA")
        self.assertIn("not measured: the funnel breaks earlier, at first page impressions", by["Views"][2])
        self.assertEqual(fb.break_line(p, fb.rows(p)), "Where it breaks: Visibility. Guests are not seeing it in search.")

    def test_no_funnel_at_all_says_why(self):
        by = {r[0]: r for r in fb.rows(pack(vis_ok=False))}
        self.assertEqual(by["Visibility"][1], "❌ NO DATA")
        self.assertIn("deactivated", by["Visibility"][2])
        self.assertIn("Next 30 nights", by["Bookings"][2], "the PMS calendar still speaks for bookings")

    def test_reviews_ranking_and_pacing_verdicts(self):
        by = {r[0]: r for r in fb.rows(pack(stages=HEALTHY, rating=4.5, page=8, cur=10, prior=15))}
        self.assertEqual(by["Reviews"][1], "⚠️ BEHIND")
        self.assertIn("Below 4.6 hurts ranking", by["Reviews"][2])
        self.assertEqual(by["Ranking"][1], "⚠️ BEHIND")
        self.assertIn("page 8", by["Ranking"][2])
        self.assertEqual(by["Pacing"][1], "⚠️ BEHIND")
        by = {r[0]: r for r in fb.rows(pack(stages=HEALTHY, prior=0))}
        self.assertEqual(by["Pacing"][1], "❌ NO DATA")
        self.assertIn("nothing was on the books at this point last year", by["Pacing"][2])

    def test_render_is_one_block_with_the_break_last(self):
        lines = fb.render(pack(stages=HEALTHY, rating=4.5))
        self.assertTrue(lines[0].startswith("FLYWHEEL: where this listing stands"))
        self.assertEqual(len(lines), 8)
        self.assertTrue(lines[-1].strip().startswith("Where it breaks: Reviews."))


if __name__ == "__main__":
    unittest.main()


class Wording(unittest.TestCase):
    def test_booking_rate_never_sits_next_to_a_verdict_it_contradicts(self):
        low_rate = HEALTHY[:4] + [stage("booking_rate", 4.66, 33.81)]
        by = {r[0]: r for r in fb.rows(pack(stages=low_rate, occ=47, market=50.5))}
        self.assertEqual(by["Bookings"][1], "✅ OK")
        self.assertNotIn("lookers", by["Bookings"][2])
        high_rate = HEALTHY[:4] + [stage("booking_rate", 51.8, 23.4)]
        by = {r[0]: r for r in fb.rows(pack(stages=high_rate, occ=0, market=13.9))}
        self.assertEqual(by["Bookings"][1], "⚠️ BEHIND")
        self.assertNotIn("lookers", by["Bookings"][2])

    def test_one_night_is_singular_and_a_visibility_break_always_explains_itself(self):
        by = {r[0]: r for r in fb.rows(pack(stages=HEALTHY, cur=1, prior=0))}
        self.assertIn("1 night booked", by["Pacing"][2])
        p = pack(stages=[stage("first_page_impressions", 331, 2136)], verdict="healthy")
        self.assertEqual(fb.break_line(p, fb.rows(p)), "Where it breaks: Visibility. Guests are not seeing it in search.")
