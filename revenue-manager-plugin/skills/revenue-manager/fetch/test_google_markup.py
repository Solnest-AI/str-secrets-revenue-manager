"""S2-040: a separate Google Vacation Rentals markup (a Summit student report, 2026-09-29).

Setup folded every spelling of Google Vacation Rentals into "direct", because GVR sells the
direct-booking site's price (Ryan 2026-09-26). That stays the default: GVR needs no markup of
its own. But a host whose Google markup differs (10% there, 5% direct) could not say so:
`--markup google=10 --markup direct=5` was refused as "direct given twice", and the workaround
`google_vr` was stored as an unknown channel. Google is now its own name, so both are kept.
"""

import unittest

from setup_properties import NOT_OTA, channel_name, listed_channels, parse_markups, property_markups

GVR_PROP = {"id": "p1", "name": "Beach House",
            "listings": [{"platform": "airbnb", "platform_id": "1"}, {"platform": "gvr", "platform_id": "g"}]}


class GoogleIsItsOwnChannel(unittest.TestCase):
    def test_every_spelling_is_google(self):
        for raw in ("gvr", "GVR", "google", "Google Vacation Rentals", "google_vr", "Google VR"):
            with self.subTest(raw=raw):
                self.assertEqual(channel_name(raw), "google")

    def test_a_google_markup_can_differ_from_direct(self):
        self.assertEqual(parse_markups(["airbnb=16", "direct=5", "google=10"]),
                         {"airbnb": 16.0, "direct": 5.0, "google": 10.0})
        self.assertEqual(parse_markups(["direct=5", "google_vr=10"])["google"], 10.0)

    def test_google_still_needs_no_markup_of_its_own(self):
        # Ryan 2026-09-26: GVR sells the direct price; with no google markup the direct one applies
        self.assertIn("google", NOT_OTA)
        self.assertEqual(listed_channels(GVR_PROP), {"airbnb"})

    def test_a_given_google_markup_is_kept_on_a_property_listed_there(self):
        values, missing = property_markups(GVR_PROP, {"airbnb": 16.0, "direct": 5.0, "google": 10.0}, {},
                                           pms="hospitable")
        self.assertEqual(values, {"airbnb": 16.0, "direct": 5.0, "google": 10.0})
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
