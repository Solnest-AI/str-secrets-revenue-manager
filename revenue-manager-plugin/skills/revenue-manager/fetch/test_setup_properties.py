"""Offline contracts for the summit first-run setup (property_config builder)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from setup_properties import (
    SetupError,
    airbnb_id,
    build_settings,
    match_rankbreeze,
    migration_files,
    parse_markups,
    upsert_statement,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class Markups(unittest.TestCase):
    def test_parses_channels(self):
        self.assertEqual(parse_markups(["airbnb=16", "vrbo=20", "Booking=22.5"]),
                         {"airbnb": 16.0, "vrbo": 20.0, "booking": 22.5})

    def test_airbnb_is_required(self):
        with self.assertRaisesRegex(SetupError, "airbnb"):
            parse_markups(["vrbo=20"])

    def test_zero_is_a_real_answer(self):
        self.assertEqual(parse_markups(["airbnb=0"]), {"airbnb": 0.0})

    def test_bad_values_are_refused(self):
        for bad in (["airbnb"], ["airbnb=abc"], ["airbnb=-1"], ["airbnb=600"], ["airbnb=nan"],
                    ["airbnb=16", "airbnb=18"], ["air bnb=16"]):
            with self.subTest(bad=bad), self.assertRaises(SetupError):
                parse_markups(bad)


class Mapping(unittest.TestCase):
    PROP = {"id": "prop-0001", "name": "Test Place",
            "listings": [{"platform": "vrbo", "platform_id": "v-1"},
                         {"platform": "airbnb", "platform_id": "1734384025235879146"}]}

    def test_airbnb_id_from_pms_listings(self):
        self.assertEqual(airbnb_id(self.PROP), "1734384025235879146")
        self.assertIsNone(airbnb_id({"listings": [{"platform": "vrbo", "platform_id": "v"}]}))

    def test_two_airbnb_listings_is_ambiguous_not_a_guess(self):
        prop = {"listings": [{"platform": "airbnb", "platform_id": "1"}, {"platform": "airbnb", "platform_id": "2"}]}
        self.assertIsNone(airbnb_id(prop))

    def test_rankbreeze_matches_on_room_id_and_returns_its_own_id(self):
        # measured live 2026-09-24: the key is `id`, the Airbnb id is `room_id` (a string)
        rb = [{"id": 157301, "room_id": "1754671150601227373"}, {"id": 154924, "room_id": "1734384025235879146"}]
        self.assertEqual(match_rankbreeze("1734384025235879146", rb), "154924")
        self.assertIsNone(match_rankbreeze("999", rb))
        self.assertIsNone(match_rankbreeze(None, rb))

    def test_duplicate_room_id_in_rankbreeze_is_not_guessed(self):
        rb = [{"id": 1, "room_id": "5"}, {"id": 2, "room_id": "5"}]
        self.assertIsNone(match_rankbreeze("5", rb))


class Settings(unittest.TestCase):
    def test_settings_carry_a_confirmed_markup_the_runner_accepts(self):
        from _mvp_analysis import markups
        s = build_settings("prop-0001", "1734", "154924", {"airbnb": 16.0}, NOW)
        self.assertEqual(s["pricelabs_listing_id"], "prop-0001")
        self.assertEqual(s["pms_name"], "smartbnb")
        self.assertEqual(s["max_delta_pct"], 0.15)
        later = datetime(2026, 9, 25, tzinfo=timezone.utc)
        self.assertEqual(markups({"settings": s}, later), {"airbnb": 16.0})

    def test_intellihost_id_is_stored_when_mapped(self):
        s = build_settings("prop-0001", "1734", None, {"airbnb": 16.0}, NOW, intellihost="11")
        self.assertEqual(s["intellihost_property_id"], "11")
        self.assertNotIn("intellihost_property_id", build_settings("prop-0001", "1734", None, {"airbnb": 16.0}, NOW))

    def test_optional_ids_are_left_out_not_blank(self):
        s = build_settings("prop-0001", None, None, {"airbnb": 16.0}, NOW)
        self.assertNotIn("rankbreeze_listing_id", s)
        self.assertNotIn("airbnb_listing_id", s)


class Sql(unittest.TestCase):
    def test_upsert_merges_settings_and_escapes_quotes(self):
        sql = upsert_statement([{"property_id": "prop-0001", "display_name": "Bob's Place",
                                 "settings": {"pms_name": "smartbnb"}}])
        self.assertIn("ON CONFLICT (property_id) DO UPDATE", sql)
        self.assertIn("property_config.settings || EXCLUDED.settings", sql)
        self.assertIn("'Bob''s Place'", sql)
        self.assertNotIn("Bob's", sql)

    def test_upsert_refuses_non_identifier_property_ids(self):
        with self.assertRaises(SetupError):
            upsert_statement([{"property_id": "x'; DROP TABLE y; --", "display_name": "n", "settings": {}}])

    def test_migrations_are_applied_in_order_and_all_present(self):
        names = [p.name for p in migration_files()]
        self.assertEqual(names, sorted(names))
        self.assertEqual([n[:3] for n in names], ["001", "002", "003", "004"])
        for p in migration_files():
            self.assertTrue(Path(p).read_text().strip())


if __name__ == "__main__":
    unittest.main()
