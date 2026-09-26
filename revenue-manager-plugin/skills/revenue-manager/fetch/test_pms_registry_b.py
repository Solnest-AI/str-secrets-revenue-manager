"""Uplisting, Smoobu and Hostfully are discovered by the connections kit's env var names and
dispatched to their adapters; the keys load from a .env like every other provider's."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _mvp_config import KEYS, PROVIDERS, load_env
from _mvp_store import CannotAnalyze
from _pms_fakes import connections
from _pms_registry import SUPPORTED, adapter, choose, connected

NAMES = ("UPLISTING_API_KEY", "SMOOBU_API_KEY", "SMOOBU_API_SECRET", "HOSTFULLY_API_KEY", "HOSTFULLY_AGENCY_UID")


class Registry(unittest.TestCase):
    def test_supported_and_discovered_by_kit_env_names(self):
        for pms in ("uplisting", "smoobu", "hostfully"):
            self.assertIn(pms, SUPPORTED)
        self.assertEqual(connected(connections(UPLISTING_API_KEY="k")), ["uplisting"])
        self.assertEqual(connected(connections(SMOOBU_API_KEY="k", SMOOBU_API_SECRET="s")), ["smoobu"])
        self.assertEqual(connected(connections(HOSTFULLY_API_KEY="k", HOSTFULLY_AGENCY_UID="a")), ["hostfully"])

    def test_half_a_credential_is_not_a_connection(self):
        self.assertEqual(connected(connections(SMOOBU_API_KEY="k")), [], "the key alone cannot sign")
        self.assertEqual(connected(connections(HOSTFULLY_API_KEY="k")), [], "the property list needs the agency uid")

    def test_choose_and_adapter(self):
        self.assertEqual(choose(connections(UPLISTING_API_KEY="k")), "uplisting")
        with self.assertRaises(CannotAnalyze):
            choose(connections(UPLISTING_API_KEY="k", HOSTFULLY_API_KEY="k", HOSTFULLY_AGENCY_UID="a"))
        conn = connections(UPLISTING_API_KEY="k", SMOOBU_API_KEY="k", SMOOBU_API_SECRET="s",
                           HOSTFULLY_API_KEY="k", HOSTFULLY_AGENCY_UID="a")
        for pms, cls in (("uplisting", "UplistingSource"), ("smoobu", "SmoobuSource"), ("hostfully", "HostfullySource")):
            with self.subTest(pms=pms):
                self.assertEqual(type(adapter(pms, object(), conn)).__name__, cls)


class Config(unittest.TestCase):
    def test_kit_names_are_read_from_env_files(self):
        self.assertTrue(set(NAMES) <= KEYS)
        self.assertTrue({"uplisting", "smoobu", "hostfully"} <= set(PROVIDERS))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("".join(f"{n}=v-{n}\n" for n in NAMES), encoding="utf-8")
            self.assertEqual(load_env(p), {n: f"v-{n}" for n in NAMES})


if __name__ == "__main__":
    unittest.main()
