"""Offline contracts for the summit first-run setup (property_config builder)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from setup_properties import (
    SetupError,
    airbnb_id,
    build_settings,
    match_min_prices,
    match_rankbreeze,
    migration_files,
    parse_markups,
    parse_property_markups,
    match_property_markups,
    listed_channels,
    property_markups,
    channel_name,
    airbnb_ids,
    pick_airbnb,
    parse_airbnb_choices,
    parse_min_prices,
    upsert_statement,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class Markups(unittest.TestCase):
    def test_parses_channels(self):
        self.assertEqual(parse_markups(["airbnb=16", "vrbo=20", "Booking=22.5"]),
                         {"airbnb": 16.0, "vrbo": 20.0, "booking": 22.5})

    def test_no_channel_is_special(self):
        # Ryan 2026-09-26: every OTA matters equally; Airbnb is not required over the others
        self.assertEqual(parse_markups(["vrbo=20"]), {"vrbo": 20.0})
        self.assertEqual(parse_markups(["booking=18", "airbnb=16"]), {"booking": 18.0, "airbnb": 16.0})

    def test_at_least_one_markup_is_required(self):
        with self.assertRaisesRegex(SetupError, "every booking site"):
            parse_markups([])

    def test_channel_spellings_collapse_to_one_name(self):
        for raw, want in (("Booking.com", "booking"), ("booking_com", "booking"), ("HomeAway", "vrbo"),
                          ("VRBO", "vrbo"), ("airbnb2", "airbnb"), ("Air BnB", "airbnb"), ("expedia", "expedia"),
                          ("gvr", "direct"), ("Google Vacation Rentals", "direct")):  # GVR sells the direct price (Ryan 2026-09-26)
            with self.subTest(raw=raw):
                self.assertEqual(channel_name(raw), want)
        with self.assertRaisesRegex(SetupError, "twice"):
            parse_markups(["booking=10", "Booking.com=12"])

    def test_zero_is_a_real_answer(self):
        self.assertEqual(parse_markups(["airbnb=0"]), {"airbnb": 0.0})

    def test_bad_values_are_refused(self):
        for bad in (["airbnb"], ["airbnb=abc"], ["airbnb=-1"], ["airbnb=600"], ["airbnb=nan"],
                    ["airbnb=16", "airbnb=18"], ["my site=16"]):
            with self.subTest(bad=bad), self.assertRaises(SetupError):
                parse_markups(bad)


class PerPropertyMarkups(unittest.TestCase):
    PROPS = [{"id": "p1", "name": "Lake House", "listings": [{"platform": "airbnb", "platform_id": "1"},
                                                              {"platform": "homeaway", "platform_id": "v"}]},
             {"id": "p2", "name": "Beach Hut", "listings": [{"platform": "booking.com", "platform_id": "b"},
                                                             {"platform": "direct", "platform_id": "d"}]},
             {"id": "p3", "name": "Cabin", "listings": []}]

    def test_override_parses_and_matches_by_name_or_id(self):
        parsed = parse_property_markups(["Lake House:vrbo=24", "p2:booking=19", "lake house:airbnb=15"])
        self.assertEqual(parsed, {"Lake House": {"vrbo": 24.0, "airbnb": 15.0}, "p2": {"booking": 19.0}})
        self.assertEqual(match_property_markups(parsed, self.PROPS),
                         {"p1": {"vrbo": 24.0, "airbnb": 15.0}, "p2": {"booking": 19.0}})

    def test_override_that_lands_nowhere_is_refused(self):
        with self.assertRaisesRegex(SetupError, "matches 0"):
            match_property_markups(parse_property_markups(["Nowhere:vrbo=20"]), self.PROPS)

    def test_bad_overrides_are_refused(self):
        for bad in (["Lake House vrbo=20"], [":vrbo=20"], ["Lake House:vrbo=abc"], ["Lake House:vrbo=-2"],
                    ["Lake House:vrbo=20", "Lake House:vrbo=21"]):
            with self.subTest(bad=bad), self.assertRaises(SetupError):
                parse_property_markups(bad)

    def test_listed_channels_are_the_otas_only(self):
        self.assertEqual(listed_channels(self.PROPS[0]), {"airbnb", "vrbo"})
        self.assertEqual(listed_channels(self.PROPS[1]), {"booking"})      # direct is not an OTA
        self.assertEqual(listed_channels(self.PROPS[2]), set())            # PMS reports no channels

    def test_every_listed_ota_needs_a_markup(self):
        values, missing = property_markups(self.PROPS[0], {"airbnb": 16.0}, {})
        self.assertEqual((values, missing), ({"airbnb": 16.0}, ["vrbo"]))
        values, missing = property_markups(self.PROPS[0], {"airbnb": 16.0}, {"p1": {"vrbo": 24.0}})
        self.assertEqual((values, missing), ({"airbnb": 16.0, "vrbo": 24.0}, []))
        values, missing = property_markups(self.PROPS[1], {"airbnb": 16.0, "vrbo": 20.0}, {})
        self.assertEqual(missing, ["booking"])
        self.assertEqual(property_markups(self.PROPS[2], {"vrbo": 20.0}, {}), ({"vrbo": 20.0}, []))

    def test_a_property_off_an_ota_does_not_carry_its_markup_when_the_pms_reports_every_channel(self):
        given = {"airbnb": 16.0, "vrbo": 20.0, "booking": 22.0, "direct": 10.0}
        # Hospitable reports every channel: Beach Hut is on Booking.com only, so no Airbnb/VRBO markup
        self.assertEqual(property_markups(self.PROPS[1], given, {}, "hospitable"),
                         ({"booking": 22.0, "direct": 10.0}, []))
        # a PMS that reports only some channels keeps every markup the operator gave
        self.assertEqual(property_markups(self.PROPS[1], given, {}, "hostaway"), (given, []))
        # no channels reported at all: keep everything
        self.assertEqual(property_markups(self.PROPS[2], given, {}, "hospitable"), (given, []))
        # an explicit per-property markup is kept even for an OTA the PMS does not list
        self.assertEqual(property_markups(self.PROPS[1], given, {"p2": {"airbnb": 17.0}}, "hospitable")[0],
                         {"booking": 22.0, "direct": 10.0, "airbnb": 17.0})


class TwoAirbnbListings(unittest.TestCase):
    # Farm House, live 2026-09-26: two Airbnb listings on one Hospitable property (the house and a
    # cottage rented on its own). Parent/child listings look the same.
    PROP = {"id": "fh", "name": "The Farm House",
            "listings": [{"platform": "airbnb", "platform_id": "111"}, {"platform": "homeaway", "platform_id": "v"},
                         {"platform": "airbnb", "platform_id": "222"}, {"platform": "gvr", "platform_id": "g"}]}

    def test_both_ids_are_seen(self):
        self.assertEqual(airbnb_ids(self.PROP), ["111", "222"])

    def test_the_one_rankbreeze_tracks_is_used(self):
        ab, note = pick_airbnb(self.PROP, [{"id": 9, "room_id": "222"}])
        self.assertEqual(ab, "222")
        self.assertIn("the one RankBreeze tracks", note)

    def test_none_or_both_tracked_is_not_guessed_and_says_how_to_choose(self):
        for rb in ([], [{"id": 9, "room_id": "111"}, {"id": 8, "room_id": "222"}]):
            with self.subTest(rb=rb):
                ab, note = pick_airbnb(self.PROP, rb)
                self.assertIsNone(ab)
                self.assertIn("2 Airbnb listings", note)
                self.assertIn('--airbnb-for "The Farm House=<room id>"', note)

    def test_the_operator_can_choose_and_a_wrong_id_is_refused(self):
        self.assertEqual(pick_airbnb(self.PROP, [], "111")[0], "111")
        with self.assertRaisesRegex(SetupError, "not one of its Airbnb listings"):
            pick_airbnb(self.PROP, [], "333")
        self.assertEqual(parse_airbnb_choices(["The Farm House=111"]), {"The Farm House": "111"})
        with self.assertRaises(SetupError):
            parse_airbnb_choices(["The Farm House"])

    def test_one_listing_is_used_as_is(self):
        self.assertEqual(pick_airbnb({"listings": [{"platform": "airbnb", "platform_id": "5"}]}, []), ("5", None))

    def test_gvr_is_direct_so_it_needs_no_markup_of_its_own(self):
        self.assertEqual(listed_channels(self.PROP), {"airbnb", "vrbo"})


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


class PricingOwnerAndFloor(unittest.TestCase):
    PROPS = [{"id": "prop-0001", "name": "Lake House"}, {"id": "prop-0002", "name": "Loft"}]

    def test_pricing_tool_is_pricelabs_when_mapped_beyond_when_stated_else_null(self):
        self.assertEqual(build_settings("p", None, None, {"airbnb": 0.0}, NOW)["pricing_tool"], "pricelabs")
        self.assertEqual(build_settings("p", None, None, {"airbnb": 0.0}, NOW, pricelabs=False,
                                        pricing_tool="beyond")["pricing_tool"], "beyond")
        s = build_settings("p", None, None, {"airbnb": 0.0}, NOW, pricelabs=False)
        self.assertIn("pricing_tool", s)  # always written, so a re-run clears a stale value
        self.assertIsNone(s["pricing_tool"])

    def test_min_price_is_stored_only_when_given(self):
        self.assertEqual(build_settings("p", None, None, {"airbnb": 0.0}, NOW, min_price=140)["min_price"], 140.0)
        self.assertNotIn("min_price", build_settings("p", None, None, {"airbnb": 0.0}, NOW))

    def test_min_prices_parse_per_property(self):
        self.assertEqual(parse_min_prices(["Lake House=140", "prop-0002=99.5"]),
                         {"Lake House": 140.0, "prop-0002": 99.5})
        for bad in (["140"], ["=140"], ["Loft=abc"], ["Loft=0"], ["Loft=-5"], ["Loft=nan"], ["Loft=1", "loft=2"]):
            with self.subTest(bad=bad), self.assertRaises(SetupError):
                parse_min_prices(bad)

    def test_min_prices_must_each_land_on_exactly_one_property(self):
        self.assertEqual(match_min_prices({"lake house": 140.0, "prop-0002": 99.0}, self.PROPS),
                         {"prop-0001": 140.0, "prop-0002": 99.0})
        with self.assertRaisesRegex(SetupError, "matches 0"):
            match_min_prices({"Cabin": 140.0}, self.PROPS)


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
