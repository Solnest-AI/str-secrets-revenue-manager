"""Offline contracts for the IntelliHost visibility/ranking adapter. Fixtures follow the shapes
measured live 2026-09-24 on Premium properties (values made up)."""

from __future__ import annotations

import unittest
from datetime import date

import flywheel
from _mvp_store import CannotAnalyze, ReadClient
from _rank_intellihost import LIST_LIMIT, PREMIUM_GAP, IntelliHostSource, funnel_from_dashboard, premium_refusal, rank_rows

START = date(2026, 9, 24)
DASH = {"property_id": 11, "period": {"from": "2026-08-26", "to": "2026-09-24"},
        "funnel": {"first_page_search_impressions": 800, "comp_first_page_search_impressions": 1000,
                   "click_rate": 0.9, "comp_click_rate": 1.0, "click_to_book_rate": 1.5, "comp_click_to_book_rate": 4.0,
                   "nights_booked": 12, "comp_nights_booked": 20},
        "step_benchmarks": {"focus_step": "book", "dead_band": 0.15, "fp": {"index": 0.8, "deficit": 0.2},
                            "ctr": {"expected_rate": 1.0, "index": 0.9, "deficit": 0.1},
                            "book": {"expected_rate": 4.0, "index": 0.375, "deficit": 0.625}}}


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
        # the click-rate benchmark is IntelliHost's visibility-adjusted expected rate, not comp_click_rate
        d = {**DASH, "step_benchmarks": {**DASH["step_benchmarks"], "ctr": {"index": 0.9}}}
        vis = flywheel.spoke_visibility(funnel_from_dashboard(d, START)["visibility_row"])
        self.assertFalse(vis["ok"])

    def test_unreadable_payload_is_skipped_with_a_reason(self):
        f = funnel_from_dashboard({"status": "no_data", "message": "x"}, START)
        self.assertEqual(f["status"], "skipped")
        self.assertTrue(f["reason"])


    def test_raw_comp_gap_at_par_is_not_a_break(self):
        # Live 2026-09-25 (Outliers 17219): click rate 35.14 vs raw comp 47.84 is a 27% "shortfall",
        # yet IntelliHost puts it at 2.08x the rate expected at its visibility. Raw comp would call it
        # a click_through_rate BREAK; the visibility-adjusted benchmark says healthy.
        live = {"period": {"from": "2026-08-27", "to": "2026-09-25"},
                "funnel": {"first_page_search_impressions": 3645, "comp_first_page_search_impressions": 393,
                           "click_rate": 35.14, "comp_click_rate": 47.84,
                           "click_to_book_rate": 2.81, "comp_click_to_book_rate": 4.79},
                "step_benchmarks": {"focus_step": None, "ctr": {"expected_rate": 16.92, "index": 2.077},
                                    "book": {"expected_rate": 1.4, "index": 2.007}}}
        vis = flywheel.spoke_visibility(funnel_from_dashboard(live, date(2026, 9, 25))["visibility_row"])
        self.assertTrue(vis["ok"], vis)
        self.assertEqual(vis["diagnosis"]["verdict"], "healthy")
        raw = {k: {"listing": live["funnel"][a], "similar_listings": live["funnel"][b]} for k, a, b in (
            ("first_page_impressions", "first_page_search_impressions", "comp_first_page_search_impressions"),
            ("click_through_rate", "click_rate", "comp_click_rate"),
            ("booking_rate", "click_to_book_rate", "comp_click_to_book_rate"))}
        self.assertEqual(flywheel.funnel_diagnosis(raw, ["first_page_impressions", "click_through_rate", "booking_rate"])["stage"],
                         "click_through_rate", "the raw comp read this listing as broken; that is the bug")

    def test_missing_step_benchmark_is_a_named_gap_not_a_raw_comp_fallback(self):
        d = {k: v for k, v in DASH.items() if k != "step_benchmarks"}
        vis = flywheel.spoke_visibility(funnel_from_dashboard(d, START)["visibility_row"])
        self.assertFalse(vis["ok"])
        self.assertIn("click_through_rate", vis["detail"])

    def test_funnel_window_is_pinned_to_the_property_local_date(self):
        # Live 2026-09-25 23:36 PDT: end_date alone was ignored and period.to came back as the UTC
        # date (09-26), one day after the property-local start, so build() rejected the funnel.
        sent = []

        class Client:
            def fetch(self, source, ident, loader, ttl_seconds=0):
                return loader()

        class Conn:
            def key(self, provider):
                return "t"
        ih = IntelliHostSource(Client(), Conn())

        def tool(name, args):
            sent.append((name, args))
            return {**DASH, "period": {"from": args.get("start_date"), "to": args.get("end_date")}}
        ih._tool = tool
        f = ih.funnel("11", START)
        (name, args), = sent
        self.assertEqual(args["end_date"], START.isoformat())
        self.assertEqual(args["start_date"], "2026-08-26", "30 days inclusive")
        self.assertEqual((f["status"], f["last_sync_date"]), ("ok", START.isoformat()))

    def test_property_map_asks_for_the_maximum_page(self):
        asked = []

        class Client:
            def fetch(self, source, ident, loader, ttl_seconds=0):
                return loader()

        class Conn:
            def key(self, provider):
                return "t"
        ih = IntelliHostSource(Client(), Conn())
        ih._tool = lambda name, args: asked.append(args) or {"properties": [{"id": 7, "listing_id": "123"}]}
        self.assertEqual(ih.airbnb_map(), {"123": "7"})
        self.assertEqual({a["limit"] for a in asked}, {LIST_LIMIT})
        self.assertEqual(LIST_LIMIT, 200)


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
