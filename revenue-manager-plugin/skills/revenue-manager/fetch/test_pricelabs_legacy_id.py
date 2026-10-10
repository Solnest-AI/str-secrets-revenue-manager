"""S2-022: setup skipped a property PriceLabs still knows by an older PMS id (a Summit student
report, 2026-09-29, with a patch from the student; this is that patch's approach plus two guards).

A Hospitable child listing from its Smartbnb days keeps a numeric id in PriceLabs while
Hospitable now reports a UUID, so setup said "NOT IN PRICELABS under the same Hospitable id".
When no PriceLabs listing has the PMS id, setup falls back to the booking-site listing ids in
PriceLabs' `channel_listing_details` (field names verified live 2026-10-10): a unique match or
nothing. Every site both sides report must agree; direct, Google and manual ids never count; a
listing another PMS property owns by id is never taken. Review guards: one PriceLabs listing
is never given to two properties, and a listing under another PriceLabs integration (a stale
copy from a previous PMS) is not taken while this PMS's own name is known.
"""

import unittest
from datetime import datetime, timezone

from setup_properties import build_settings, match_all_by_channel, match_pricelabs_by_channel

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
CHILD = {"id": "child-uuid", "name": "Child",
         "listings": [{"platform": "homeaway", "platform_id": "321.1.2"},
                      {"platform": "airbnb", "platform_id": "8319"},
                      {"platform": "gvr", "platform_id": "g-1"},
                      {"platform": "direct", "platform_id": "d-1"}]}


def pl(lid, *pairs, pms="smartbnb"):
    return {"id": lid, "pms": pms,
            "channel_listing_details": [{"channel_name": c, "channel_listing_id": i} for c, i in pairs]}


class ByBookingSiteIds(unittest.TestCase):
    def test_an_older_pms_id_matches_by_its_booking_site_ids(self):
        rows = [pl("100100", ("vrbo", "321.1.2"), ("airbnb", "8319"), ("direct", "d-1")),
                pl("parent-uuid", ("airbnb", "1678"))]
        lid, pms, how = match_pricelabs_by_channel(CHILD, rows, {"child-uuid", "parent-uuid"})
        self.assertEqual((lid, pms), ("100100", "smartbnb"))
        self.assertIn("airbnb and vrbo", how)

    def test_a_listing_another_property_owns_by_id_is_never_taken(self):
        rows = [pl("other-uuid", ("airbnb", "8319"))]
        self.assertIsNone(match_pricelabs_by_channel(CHILD, rows, {"child-uuid", "other-uuid"})[0])

    def test_a_conflicting_site_id_is_not_a_match(self):
        rows = [pl("100100", ("airbnb", "8319"), ("vrbo", "321.9.9"))]
        self.assertIsNone(match_pricelabs_by_channel(CHILD, rows, {"child-uuid"})[0])

    def test_two_candidates_are_not_guessed(self):
        rows = [pl("1", ("airbnb", "8319")), pl("2", ("vrbo", "321.1.2"))]
        lid, _, why = match_pricelabs_by_channel(CHILD, rows, {"child-uuid"})
        self.assertIsNone(lid)
        self.assertIn("not guessed", why)

    def test_direct_google_and_manual_ids_alone_are_not_a_match(self):
        rows = [pl("100100", ("direct", "d-1"), ("gvr", "g-1"), ("manual", "d-1"))]
        self.assertIsNone(match_pricelabs_by_channel(CHILD, rows, {"child-uuid"})[0])

    def test_the_pricelabs_id_is_stored_when_it_is_not_the_pms_id(self):
        s = build_settings("child-uuid", "8319", None, {"airbnb": 16.0}, NOW, pricelabs_id="100100")
        self.assertEqual(s["pricelabs_listing_id"], "100100")
        self.assertEqual(build_settings("p", None, None, {"airbnb": 16.0}, NOW)["pricelabs_listing_id"], "p")


class ReviewGuards(unittest.TestCase):
    def test_one_listing_is_never_given_to_two_properties(self):
        twin = {"id": "twin-uuid", "name": "Twin", "listings": [{"platform": "airbnb", "platform_id": "8319"}]}
        rows = [pl("100100", ("airbnb", "8319"), ("vrbo", "321.1.2"))]
        out = match_all_by_channel([CHILD, twin], rows, {"child-uuid", "twin-uuid"})
        for pid in ("child-uuid", "twin-uuid"):
            lid, _, why = out[pid]
            self.assertIsNone(lid, pid)
            self.assertIn("2 properties", why)

    def test_a_listing_under_another_pricelabs_integration_is_not_taken(self):
        rows = [pl("old-guesty-1", ("airbnb", "8319"), pms="guesty")]
        lid, _, why = match_pricelabs_by_channel(CHILD, rows, {"child-uuid"}, allowed_pms={"smartbnb"})
        self.assertIsNone(lid)
        self.assertIn("guesty", why)
        # with no id-matched listing to learn this PMS's PriceLabs name from, it is not filtered
        self.assertEqual(match_pricelabs_by_channel(CHILD, rows, {"child-uuid"})[0], "old-guesty-1")


if __name__ == "__main__":
    unittest.main()


class SetupEndToEnd(unittest.TestCase):
    """setup_properties.main() with a PMS property PriceLabs has only under an older id."""

    def test_the_row_stores_the_pricelabs_id_and_setup_reads_that_listing(self):
        import contextlib
        import io
        from unittest import mock

        import setup_properties
        from _mvp_sources import Sources
        from test_config_fixes import SUPA, FakePMS, Onboarding, TmpHome

        fake = FakePMS("guesty-prop-1")
        sql, seen = [], []
        rows = [pl("100100", ("airbnb", "1734"), pms="guesty")]
        with TmpHome({"supabase-revenue-manager": SUPA}, {**Onboarding.GUESTY, "PRICELABS_API_KEY": "pl"}) as h, \
                mock.patch("_pms_registry.adapter", return_value=fake), \
                mock.patch.object(Sources, "pricelabs_inventory", return_value={"some-other-id": "guesty"}), \
                mock.patch.object(Sources, "pricelabs_listings", return_value=rows), \
                mock.patch.object(Sources, "listing", side_effect=lambda lid, pms: seen.append((lid, pms)) or
                                  {"id": lid, "pms": pms}), \
                mock.patch.object(setup_properties, "post_sql", side_effect=lambda p, t, s: sql.append(s)), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = setup_properties.main(["--pms", "guesty", "--markup", "airbnb=16", "--db", str(h.home / "w.db")])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(seen, [("100100", "guesty")])
        row = Onboarding.row_from(sql)
        self.assertEqual((row["property_id"], row["settings"]["pricelabs_listing_id"]), ("guesty-prop-1", "100100"))
        self.assertIn("by airbnb listing id, PriceLabs id 100100", out.getvalue())
