"""Synthetic, credential-free contracts for the dashboard comparison parser."""
import json
import unittest
from datetime import date

from _mvp_rankbreeze import parse_booking_funnel
from flywheel import spoke_ranking, spoke_visibility


AS_OF = date(2025, 12, 20)
METRIC_FIXTURES = [
    ("1st Page Impressions", "1,200 impressions 1,100 impressions",
     "first_page_impressions", 1200, 1100),
    ("Click-through rate", "15.50% 14.00%", "click_through_rate", 15.5, 14),
    ("Listing views", "186 views 154 views", "view", 186, 154),
    ("Wishlists", "6 4", "wishlist", 6, 4),
    ("Booking rate", "4.00% 8.00%", "booking_rate", 4, 8),
    ("Conversion rates", "0.62% 1.12%", "conversion_rate", 0.62, 1.12),
]


def table(label, values, *, sync="December 20, 2025", markup=False):
    """Monthly rows deliberately differ from the forward blended headline."""
    stamp = f"Last sync: {sync}" if sync else ""
    if markup:
        return (
            f"<section><h2>{label}</h2><p>{stamp}</p><p>Month Day</p>"
            f"<table><tr><th>Period</th><th>{label}</th><th>Similar Listings data</th></tr>"
            f"<tr><td>November, 2025</td><td>{values}</td></tr>"
            f"<tr><td>December, 2025</td><td>CURRENT</td><td>{values}</td></tr>"
            f"<tr><td>January, 2026</td><td>{values}</td></tr></table></section>"
        )
    return (
        f"{label} description {stamp} Month Day Period {label} Similar Listings data "
        f"November, 2025 {values} December, 2025 CURRENT {values} January, 2026 {values} "
    )


def dashboard(*, sync="December 20, 2025", markup=False):
    header = (
        "Average city rankings 8 of 200 (Page 1) "
        "First page impressions 9,999 Explore Impressions 9,999 99,999 "
        "Booking rate 1.00% Explore booking rate 1.00% 99.00% "
    )
    return header + " ".join(table(label, values, sync=sync, markup=markup)
                             for label, values, *_ in METRIC_FIXTURES)


class BookingFunnelTests(unittest.TestCase):
    def test_current_month_is_usable_without_blended_headline_values(self):
        result = parse_booking_funnel(dashboard(), AS_OF)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["last_sync_date"], "2025-12-20")
        self.assertEqual(result["age_days"], 0)
        self.assertEqual(list(result["months"]), ["2025-11", "2025-12", "2026-01"])
        current = result["visibility_row"]["similar_listings_comparison"]
        for _, _, key, listing, peers in METRIC_FIXTURES:
            self.assertEqual(current[key], {"listing": listing, "similar_listings": peers})
        gate = spoke_visibility(result["visibility_row"])
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["diagnosis"]["stage"], "booking_rate")
        self.assertEqual(result["ranking"]["city_rank"], {"position": 8, "of": 200, "page": 1})
        self.assertEqual(result["ranking"]["status"], "requires_separate_source")
        self.assertIsNone(result["ranking"]["rows"])

    def test_full_html_and_visible_text_produce_same_metrics(self):
        plain = parse_booking_funnel(dashboard(), AS_OF)
        html = parse_booking_funnel(dashboard(markup=True), AS_OF)
        self.assertEqual(html, plain)

    def test_year_rollover_uses_explicit_year_and_ignores_current_badge(self):
        text = dashboard(sync="January 2, 2026")
        text = text.replace("January, 2026 4.00% 8.00%", "January, 2026 6.00% 7.00%")
        result = parse_booking_funnel(text, date(2026, 1, 3))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["current_month"], "2026-01")
        comparison = result["visibility_row"]["similar_listings_comparison"]
        self.assertEqual(comparison["booking_rate"], {"listing": 6, "similar_listings": 7})

    def test_stale_and_future_dates_fail_closed(self):
        for stamp, expected in (("December 16, 2025", "stale"), ("December 21, 2025", "future")):
            with self.subTest(stamp=stamp):
                result = parse_booking_funnel(dashboard(sync=stamp), AS_OF)
                self.assertEqual(result["status"], "skipped")
                self.assertIsNone(result["visibility_row"])
                self.assertIn(expected, result["reason"])
        at_limit = parse_booking_funnel(dashboard(sync="December 17, 2025"), AS_OF)
        self.assertEqual(at_limit["status"], "ok")

    def test_missing_metric_date_does_not_inherit_other_metric_timestamp(self):
        text = dashboard().replace("Last sync: December 20, 2025", "", 1)
        result = parse_booking_funnel(text, AS_OF)
        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(result["metric_sync_dates"]["first_page_impressions"])
        self.assertIn("first_page_impressions has no readable Last sync date", result["reason"])

    def test_missing_peer_is_unknown_not_zero(self):
        for values in ("4.00%", "4.00% N/A", "4.00% --", "4.00% No data"):
            with self.subTest(values=values):
                text = dashboard().replace("December, 2025 CURRENT 4.00% 8.00%",
                                           "December, 2025 CURRENT " + values)
                result = parse_booking_funnel(text, AS_OF)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["months"]["2025-12"]["booking_rate"],
                                 {"listing": 4, "similar_listings": None})

    def test_zero_peers_are_valid(self):
        result = parse_booking_funnel(dashboard().replace("6 4", "0 0"), AS_OF)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["months"]["2025-12"]["wishlist"],
                         {"listing": 0, "similar_listings": 0})

    def test_absent_current_month_or_headline_only_cannot_pass(self):
        for text in (dashboard().replace("December, 2025", "October, 2025"),
                     "Booking rate 1.0% Explore booking rate 1.0% 99.0% "
                     "Last sync: December 20, 2025"):
            with self.subTest(text=text[:40]):
                result = parse_booking_funnel(text, AS_OF)
                self.assertEqual(result["status"], "skipped")
                self.assertIsNone(result["visibility_row"])

    def test_script_style_and_personal_data_do_not_leave_parser(self):
        payload = (
            '<body data-listing-id="12345" data-user-name="Example Person">'
            '<p>Example Person example@example.invalid</p>'
            '<style>.note::after { content: "sensitive-css-token"; }</style>'
            '<script>const secret = "sensitive-session-token"; '
            + dashboard(sync="December 20, 2025") + '</script>'
            + dashboard(sync="December 1, 2025", markup=True) + '</body>'
        )
        result = parse_booking_funnel(payload, AS_OF, expected_listing_id="12345")
        self.assertEqual(result["status"], "skipped")
        self.assertIn("stale", result["reason"])
        self.assertEqual(result["subject_verification"], "matched")
        encoded = json.dumps(result)
        for private in ("Example Person", "example@", "sensitive-", "data-user-name", "<script>"):
            self.assertNotIn(private, encoded)

    def test_wrong_or_ambiguous_subject_cannot_pass_either_spoke(self):
        for attrs in ('<body data-listing-id="54321">',
                      '<body data-listing-id="54321"><div data-listing-id="12345">'):
            result = parse_booking_funnel(attrs + dashboard(markup=True), AS_OF,
                                          expected_listing_id="12345")
            self.assertEqual(result["status"], "skipped")
            self.assertIsNone(result["visibility_row"])
            self.assertIsNone(result["ranking"]["rows"])

    def test_absent_subject_is_explicit_for_caller_to_bind_to_request(self):
        result = parse_booking_funnel(dashboard(), AS_OF, expected_listing_id="12345")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["subject_verification"], "unavailable")

    def test_conflicting_duplicate_metric_is_rejected(self):
        text = dashboard() + table("Booking rate", "20.00% 8.00%")
        result = parse_booking_funnel(text, AS_OF)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("conflicting booking_rate rows", result["reason"])

    def test_explicit_fresh_ranking_date_is_usable(self):
        text = "Ranking date: December 20, 2025 " + dashboard()
        result = parse_booking_funnel(text, AS_OF)
        self.assertEqual(result["ranking"]["status"], "ok")
        self.assertTrue(spoke_ranking(result["ranking"]["rows"])["ok"])
        self.assertEqual(result["ranking"]["date"], "2025-12-20")

    def test_metric_sync_cannot_repair_a_stale_or_future_ranking_date(self):
        for stamp in ("December 1, 2025", "December 21, 2025"):
            result = parse_booking_funnel("Rankings last sync: " + stamp + " " + dashboard(), AS_OF)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["ranking"]["status"], "requires_separate_source")
            self.assertIsNone(result["ranking"]["rows"])

    def test_ranking_sync_cannot_repair_an_undated_metric(self):
        text = dashboard().replace("Last sync: December 20, 2025", "", 1)
        result = parse_booking_funnel("Rankings last sync: December 20, 2025 " + text, AS_OF)
        self.assertEqual(result["ranking"]["status"], "ok")
        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(result["metric_sync_dates"]["first_page_impressions"])

    def test_invalid_percent_and_calendar_date_are_not_accepted(self):
        for text in (dashboard().replace("4.00% 8.00%", "104.00% 8.00%"),
                     dashboard(sync="December 99, 2025")):
            result = parse_booking_funnel(text, AS_OF)
            self.assertEqual(result["status"], "skipped")
            self.assertIsNone(result["visibility_row"])

    def test_date_without_year_is_not_inferred(self):
        text = dashboard().replace("December, 2025 CURRENT", "December CURRENT")
        result = parse_booking_funnel(text, AS_OF)
        self.assertEqual(result["status"], "skipped")
        self.assertNotIn("2025-12", result["months"])


if __name__ == "__main__":
    unittest.main()
