"""Offline contracts for the IntelliHost visibility/ranking adapter. Fixtures follow the shapes
measured live 2026-09-24 on Premium properties (values made up)."""

from __future__ import annotations

import unittest
from datetime import date

import flywheel
from _mvp_store import CannotAnalyze, ReadClient
from _rank_intellihost import PREMIUM_GAP, funnel_from_dashboard, premium_refusal, rank_rows

START = date(2026, 9, 24)
DASH = {"property_id": 11, "period": {"from": "2026-08-26", "to": "2026-09-24"},
        "funnel": {"first_page_search_impressions": 800, "comp_first_page_search_impressions": 1000,
                   "click_rate": 0.9, "comp_click_rate": 1.0, "click_to_book_rate": 1.5, "comp_click_to_book_rate": 4.0,
                   "nights_booked": 12, "comp_nights_booked": 20}}


class Funnel(unittest.TestCase):
    def test_maps_three_stages_and_the_gate_diagnoses_them(self):
        f = funnel_from_dashboard(DASH, START)
        self.assertEqual(f["status"], "ok")
        self.assertEqual((f["last_sync_date"], f["current_month"]), ("2026-09-24", "2026-09"))
        row = f["visibility_row"]
        self.assertEqual(row["stages"], ["first_page_impressions", "click_through_rate", "booking_rate"])
        vis = flywheel.spoke_visibility(row)
        self.assertTrue(vis["ok"], vis)
        self.assertEqual(vis["diagnosis"]["verdict"], "break")
        self.assertEqual(vis["diagnosis"]["stage"], "booking_rate", "impressions and clicks are within 25%; booking is not")

    def test_missing_benchmark_is_not_a_pass(self):
        d = {**DASH, "funnel": {**DASH["funnel"], "comp_click_rate": None}}
        vis = flywheel.spoke_visibility(funnel_from_dashboard(d, START)["visibility_row"])
        self.assertFalse(vis["ok"])

    def test_unreadable_payload_is_skipped_with_a_reason(self):
        f = funnel_from_dashboard({"status": "no_data", "message": "x"}, START)
        self.assertEqual(f["status"], "skipped")
        self.assertTrue(f["reason"])


class RankBreezeUnchanged(unittest.TestCase):
    def test_six_stage_walk_still_stops_on_a_missing_stage_when_none_are_declared(self):
        comp = {"first_page_impressions": {"listing": 10, "similar_listings": 10},
                "click_through_rate": {"listing": 1, "similar_listings": 1}}
        self.assertEqual(flywheel.funnel_diagnosis(comp)["verdict"], "unknown")


class Ranking(unittest.TestCase):
    SERIES = {"series": [{"scrape_date": "2026-09-21", "guest_count": g, "rank": 12 + g, "page": 1} for g in (1, 2, 3, 4)]
              + [{"scrape_date": "2026-09-10", "guest_count": 1, "rank": 60, "page": 3}]}

    def test_latest_scrape_within_7_days_is_used_and_dated_honestly(self):
        rows = rank_rows(self.SERIES, START, guest_capacity=4)
        self.assertEqual({r["date"] for r in rows}, {"2026-09-21"})
        self.assertEqual({r["guest_count"] for r in rows}, {1, 2, 3, 4})
        self.assertTrue(all(r["max_age_days"] == 7 for r in rows))
        self.assertTrue(flywheel.spoke_ranking(rows)["ok"])

    def test_stale_scrape_gives_no_rows(self):
        old = {"series": [{"scrape_date": "2026-09-10", "guest_count": 1, "rank": 5, "page": 1}]}
        self.assertEqual(rank_rows(old, START, guest_capacity=1), [])

    def test_guest_counts_beyond_capacity_are_dropped(self):
        rows = rank_rows(self.SERIES, START, guest_capacity=2)
        self.assertEqual({r["guest_count"] for r in rows}, {1, 2})


class Premium(unittest.TestCase):
    def test_premium_refusal_is_recognised(self):
        self.assertTrue(premium_refusal("An IntelliHost Premium subscription is required to read that property through the API."))
        self.assertFalse(premium_refusal("some other error"))
        self.assertIn("Premium", PREMIUM_GAP)


class Transport(unittest.TestCase):
    def test_only_read_tools_pass_the_transport(self):
        url = "https://clients.intellihost.co/api/mcp"
        for name in ("get-funnel-dashboard", "get-rank-series-tool", "list-properties-tool", "whoami-tool"):
            ReadClient._read_only("intellihost", "rpc", "POST", {"method": "tools/call", "params": {"name": name}}, url)
        for name in ("set-property-price-override-tool", "set-price-thresholds", "set-auto-sync-tool",
                     "upsert-pricing-rule", "copy-pricing-rules", "delete-pricing-rules", "resolve-action-item-tool", "refresh-audit"):
            with self.subTest(name=name), self.assertRaises(CannotAnalyze):
                ReadClient._read_only("intellihost", "rpc", "POST", {"method": "tools/call", "params": {"name": name}}, url)


if __name__ == "__main__":
    unittest.main()
