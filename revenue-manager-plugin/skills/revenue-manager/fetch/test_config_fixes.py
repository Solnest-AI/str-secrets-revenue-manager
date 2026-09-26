"""Offline contracts for the 2026-09-25 config/setup/data-source audit fixes.

No live API calls: every provider read is stubbed. Covers audit items 2, 16, 17, 18, 26, 27, 28.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _mvp_config
import analyze90
import setup_properties
from _mvp_config import KEYS, Connections, normalized_context
from _mvp_sources import Sources
from _mvp_store import CannotAnalyze

FETCH = Path(__file__).resolve().parent
REPO = FETCH.parents[3]


def clean_env():
    """os.environ without any provider key, so a developer's shell cannot leak into a test."""
    return {k: v for k, v in os.environ.items() if k not in KEYS and k != "SKILL_PATH_REVENUE_MANAGER"}


class TmpHome:
    """A fake HOME with its own ~/.claude.json, and cwd moved out of any checkout."""

    def __init__(self, servers=None, env=None):
        self.servers, self.env = servers or {}, env or {}

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": self.servers}))
        self._cwd = os.getcwd()
        os.chdir(self.home)
        self._env = mock.patch.dict(os.environ, {**clean_env(), "HOME": str(self.home), **self.env}, clear=True)
        self._env.start()
        return self

    def __exit__(self, *exc):
        self._env.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()


SUPA = {"command": "npx", "args": ["-y", "@supabase/mcp-server-supabase", "--project-ref=abcproj"],
        "env": {"SUPABASE_ACCESS_TOKEN": "sbp_test"}}
IH_HTTP = {"type": "http", "url": "https://clients.intellihost.co/api/mcp",
           "headers": {"Authorization": "Bearer ih-token-123"}}


# ------------------------------------------------------------------ item 17: where keys are found

class KeyDiscovery(unittest.TestCase):
    def test_intellihost_token_is_read_from_the_http_header(self):
        with TmpHome({"intellihost": IH_HTTP}):
            self.assertEqual(Connections().key("intellihost"), "ih-token-123")

    def test_skill_path_env_points_at_the_bundle_env(self):
        with tempfile.TemporaryDirectory() as bundle, TmpHome(env={"SKILL_PATH_REVENUE_MANAGER": ""}) as h:
            Path(bundle, ".env").write_text("PRICELABS_API_KEY=pl-from-bundle\n")
            with mock.patch.dict(os.environ, {"SKILL_PATH_REVENUE_MANAGER": bundle}):
                self.assertEqual(Connections().key("pricelabs"), "pl-from-bundle")
            del h

    def test_kit_env_names_the_bundle_when_the_plugin_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit, bundle = Path(tmp, "kit"), Path(tmp, "bundle")
            (kit / "mcp-servers" / "hospitable").mkdir(parents=True)
            bundle.mkdir()
            (kit / "fan-out-env.sh").write_text("#!/bin/sh\n")
            (kit / ".env").write_text(f"SKILL_PATH_REVENUE_MANAGER={bundle}\n")
            (bundle / ".env").write_text("AIRROI_API_KEY=airroi-from-bundle\n")
            servers = {"hospitable": {"command": "node", "args": [str(kit / "mcp-servers/hospitable/index.js")]}}
            with TmpHome(servers):
                self.assertEqual(Connections().key("airroi"), "airroi-from-bundle")

    def test_installed_plugin_cache_path_is_not_treated_as_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            here = Path(tmp, ".claude/plugins/cache/mkt/revenue-manager/5.0.0/skills/revenue-manager/fetch/_mvp_config.py")
            here.parent.mkdir(parents=True)
            roots = _mvp_config.bundle_roots({}, here=here)
            self.assertNotIn(here.parents[4], roots)
        self.assertIn(REPO, _mvp_config.bundle_roots({}, here=FETCH / "_mvp_config.py"))

    def test_env_example_declares_the_intellihost_token_and_template_drops_the_cookie(self):
        self.assertIn("INTELLIHOST_MCP_TOKEN=", (REPO / ".env.example").read_text())
        self.assertNotIn("RANKBREEZE_SESSION", (REPO / ".env.template").read_text())


# ------------------------------------------------------------------ item 27: Windows

class Windows(unittest.TestCase):
    def test_bom_env_keeps_its_first_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, ".env")
            p.write_bytes("﻿HOSPITABLE_API_KEY=abc\nPRICELABS_API_KEY=def\n".encode("utf-8"))
            self.assertEqual(_mvp_config.load_env(p), {"HOSPITABLE_API_KEY": "abc", "PRICELABS_API_KEY": "def"})

    def test_bom_claude_json_is_readable(self):
        with TmpHome() as h:
            (h.home / ".claude.json").write_bytes(
                ("﻿" + json.dumps({"mcpServers": {"intellihost": IH_HTTP}})).encode("utf-8"))
            self.assertEqual(Connections().key("intellihost"), "ih-token-123")

    def test_console_is_reconfigured_so_a_tick_cannot_crash_cp1252(self):
        raw = io.BytesIO()
        cp = io.TextIOWrapper(raw, encoding="cp1252")
        with mock.patch.object(sys, "stdout", cp), mock.patch.object(sys, "stderr", cp):
            _mvp_config.utf8_console()
            print("✅ done")
            sys.stdout.flush()
        self.assertIn("✅".encode("utf-8"), raw.getvalue())

    def test_entry_points_call_the_console_fix(self):
        for name in ("setup_properties.py", "analyze90.py", "apply_change.py"):
            src = (FETCH / name).read_text()
            body = src.split("def main(", 1)[1]
            self.assertIn("utf8_console()", body.split("\n\n\n")[0], name)

    def test_401_names_the_token(self):
        import urllib.error
        from _mvp_store import ReadClient

        class Opener:
            def open(self, req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b""))

        client = ReadClient(SimpleNamespace(), max_calls=5, opener=Opener())
        with self.assertRaisesRegex(CannotAnalyze, "token expired or wrong"):
            client.request("pricelabs", "listings", "https://api.pricelabs.co/v1/listings")


# ------------------------------------------------------------------ item 16: context fields

class BlankEnvLines(unittest.TestCase):
    def test_blank_placeholder_in_a_later_file_does_not_erase_a_real_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            real, blank = Path(tmp, "real.env"), Path(tmp, "blank.env")
            real.write_text("HOSPITABLE_API_KEY=abc\n")
            blank.write_text("HOSPITABLE_API_KEY=\nPRICELABS_API_KEY=''\nOWNERREZ_TOKEN=pt_x\n")
            self.assertEqual(_mvp_config.load_env(blank), {"OWNERREZ_TOKEN": "pt_x"})
            merged = {**_mvp_config.load_env(real), **_mvp_config.load_env(blank)}
            self.assertEqual(merged["HOSPITABLE_API_KEY"], "abc")


class Context(unittest.TestCase):
    def test_normalized_context_keeps_intellihost_and_pms_source(self):
        row = {"property_id": "g-1", "settings": {"pms_source": "guesty", "intellihost_property_id": "11",
                                                   "pms_name": "guesty", "pricelabs_listing_id": "g-1"}}
        s = normalized_context({"config": [row]}, "g-1")["settings"]
        self.assertEqual((s["pms_source"], s["intellihost_property_id"]), ("guesty", "11"))


# ------------------------------------------------------------------ items 2 + 16: end-to-end onboarding

class FakePMS:
    """A Guesty/OwnerRez adapter double returning the runner's normalized shapes."""

    def __init__(self, pid):
        self.pid = pid

    def prop(self):
        return {"id": self.pid, "name": "Lake House", "listed": True, "timezone": "America/Vancouver",
                "currency": "CAD", "capacity": {"max": 4, "bedrooms": 2},
                "listings": [{"platform": "airbnb", "platform_id": "1734"}]}

    def inventory(self):
        return {"data": [self.prop()], "total": 1, "complete": True}

    def property(self, selector):
        return self.prop()


class Onboarding(unittest.TestCase):
    """setup_properties (fakes) -> the upserted row -> read_context -> analyze90.run_live's branch."""

    def run_setup(self, pms, env, *, pricelabs=True, extra_servers=None):
        fake = FakePMS(f"{pms}-prop-1")
        sql = []
        servers = {"supabase-revenue-manager": SUPA, "intellihost": IH_HTTP, **(extra_servers or {})}
        out, err = io.StringIO(), io.StringIO()
        with TmpHome(servers, env) as h, \
                mock.patch("_pms_registry.adapter", return_value=fake), \
                mock.patch.object(Sources, "pricelabs_inventory", return_value={fake.pid: pms}), \
                mock.patch.object(Sources, "listing", return_value={"id": fake.pid, "pms": pms}), \
                mock.patch("_rank_intellihost.IntelliHostSource.airbnb_map", return_value={"1734": "11"}), \
                mock.patch.object(setup_properties, "post_sql", side_effect=lambda p, t, s: sql.append(s)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = setup_properties.main(["--pms", pms, "--markup", "airbnb=16", "--db", str(h.home / "wb.sqlite3")])
        return code, sql, out.getvalue(), err.getvalue(), fake

    @staticmethod
    def row_from(sql):
        m = re.search(r"\('([^']+)', '[^']*', '(\{.*?\})'::jsonb\)", sql[-1])
        return {"property_id": m[1], "settings": json.loads(m[2].replace("''", "'"))}

    def analyze_branch(self, row, pms):
        fake = FakePMS(row["property_id"])
        seen = {}

        class Client:
            def fetch(self, source, ident, loader, ttl_seconds=0):
                return loader()

            def request(self, provider, op, url, **kw):
                assert provider == "supabase", provider
                return [{"context": {"config": [row], "changes": [], "decisions": []}}], {}

        def listing(self_, lid, pms_name):
            seen["listing"] = (lid, pms_name)
            return {"id": lid, "pms": pms_name}

        stub = lambda *a, **k: {"data": [], "total": 0, "complete": True}  # noqa: E731
        args = SimpleNamespace(property="Lake House", start=None, settings=None, days=30, refresh_context=False, pms=pms)
        conns = SimpleNamespace(supabase=lambda: ("abcproj", "sbp_test"), values={}, paths={},
                                key=lambda p: "k", account=lambda p: "acct", rankbreeze_url=lambda: None)
        with mock.patch("_pms_registry.adapter", return_value=fake), \
                mock.patch.multiple(Sources, calendar=stub, reservations=stub, reviews=stub, listing=listing,
                                    prices=stub, overrides=stub, rules=stub, pile=stub, neighborhood=stub), \
                mock.patch("_rank_intellihost.IntelliHostSource.__init__", lambda s, c, n: None), \
                mock.patch("_rank_intellihost.IntelliHostSource.funnel",
                           lambda s, ih, st: seen.setdefault("ih_funnel", ih) and {"status": "ok"}), \
                mock.patch("_rank_intellihost.IntelliHostSource.rankings",
                           lambda s, ih, st, cap=1: seen.setdefault("ih_rank", ih) and []), \
                mock.patch("_mvp_comps.fetch_comps", return_value={"status": "unavailable"}):
            inputs, start, errors = analyze90.run_live(args, Client(), conns, datetime(2026, 9, 25, 18, tzinfo=timezone.utc))
        return inputs, errors, seen

    def test_guesty_without_hospitable_key_sets_up_and_analyses_through_intellihost(self):
        code, sql, out, err, fake = self.run_setup("guesty", {"GUESTY_CLIENT_ID": "cid", "GUESTY_CLIENT_SECRET": "s",
                                                              "PRICELABS_API_KEY": "pl"})
        self.assertEqual(code, 0, err)
        self.assertNotIn("Hospitable", out + err)
        row = self.row_from(sql)
        s = row["settings"]
        self.assertEqual((s["pms_source"], s["pms_name"], s["intellihost_property_id"]), ("guesty", "guesty", "11"))
        inputs, errors, seen = self.analyze_branch(row, "guesty")
        self.assertEqual(seen["listing"], (fake.pid, "guesty"))
        self.assertEqual((seen["ih_funnel"], seen["ih_rank"]), ("11", "11"), "IntelliHost branch not taken")
        self.assertNotIn("No verified RankBreeze or IntelliHost listing mapping", errors)
        self.assertIn("context", inputs)

    def test_stale_rankbreeze_mapping_falls_back_to_intellihost_and_says_why(self):
        # Live 2026-09-25: setup merges settings, so a RankBreeze id survives RankBreeze being
        # disconnected; the runner then never reached IntelliHost and both spokes read "no
        # RankBreeze summary row". rankbreeze_url() is None in analyze_branch's connections.
        code, sql, out, err, fake = self.run_setup("guesty", {"GUESTY_CLIENT_ID": "cid", "GUESTY_CLIENT_SECRET": "s",
                                                              "PRICELABS_API_KEY": "pl"})
        self.assertEqual(code, 0, err)
        row = self.row_from(sql)
        row["settings"]["rankbreeze_listing_id"] = "148285"
        inputs, errors, seen = self.analyze_branch(row, "guesty")
        self.assertEqual((seen.get("ih_funnel"), seen.get("ih_rank")), ("11", "11"), "IntelliHost branch not taken")
        self.assertTrue(any("RankBreeze is not connected" in e for e in errors), errors)

    def test_stale_rankbreeze_mapping_without_intellihost_names_the_reason(self):
        code, sql, out, err, fake = self.run_setup("guesty", {"GUESTY_CLIENT_ID": "cid", "GUESTY_CLIENT_SECRET": "s",
                                                              "PRICELABS_API_KEY": "pl"})
        row = self.row_from(sql)
        row["settings"]["rankbreeze_listing_id"] = "148285"
        row["settings"].pop("intellihost_property_id", None)
        inputs, errors, seen = self.analyze_branch(row, "guesty")
        self.assertNotIn("ih_funnel", seen)
        self.assertIn("RankBreeze is not connected", inputs["funnel"]["reason"])
        self.assertIn("RankBreeze is not connected", inputs["rank_gap"])

    def test_ownerrez_without_hospitable_key_sets_up_and_analyses(self):
        code, sql, out, err, fake = self.run_setup("ownerrez", {"OWNERREZ_EMAIL": "a@b.c", "OWNERREZ_TOKEN": "t",
                                                                "PRICELABS_API_KEY": "pl"})
        self.assertEqual(code, 0, err)
        row = self.row_from(sql)
        self.assertEqual(row["settings"]["pms_source"], "ownerrez")
        inputs, errors, seen = self.analyze_branch(row, "ownerrez")
        self.assertEqual(seen["listing"], (fake.pid, "ownerrez"))

    def test_no_pricelabs_is_a_named_gap_not_a_hard_stop(self):
        code, sql, out, err, fake = self.run_setup("guesty", {"GUESTY_CLIENT_ID": "cid", "GUESTY_CLIENT_SECRET": "s"})
        self.assertEqual(code, 0, err)
        row = self.row_from(sql)
        self.assertNotIn("pricelabs_listing_id", row["settings"])
        self.assertIn("PriceLabs", row["settings"]["pricing_gap"])
        self.assertIn("PriceLabs", out)
        with self.assertRaisesRegex(CannotAnalyze, "PriceLabs"):
            self.analyze_branch(row, "guesty")

    def test_nothing_mapped_error_names_the_chosen_pms(self):
        fake = FakePMS("g-1")
        err = io.StringIO()
        with TmpHome({"supabase-revenue-manager": SUPA}, {"GUESTY_CLIENT_ID": "c", "PRICELABS_API_KEY": "pl"}) as h, \
                mock.patch("_pms_registry.adapter", return_value=fake), \
                mock.patch.object(Sources, "pricelabs_inventory", return_value={}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = setup_properties.main(["--pms", "guesty", "--markup", "airbnb=0", "--db", str(h.home / "w.db")])
        self.assertEqual(code, 2)
        self.assertIn("Guesty", err.getvalue())
        self.assertNotIn("Hospitable", err.getvalue())

    def test_hospitable_still_requires_its_own_key(self):
        err = io.StringIO()
        with TmpHome({"supabase-revenue-manager": SUPA}, {"PRICELABS_API_KEY": "pl"}) as h, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = setup_properties.main(["--pms", "hospitable", "--markup", "airbnb=0", "--db", str(h.home / "w.db")])
        self.assertEqual(code, 2)
        self.assertIn("HOSPITABLE_API_KEY", err.getvalue())


# ------------------------------------------------------------------ items 18 + 26: IntelliHost

class IntelliHostThreads(unittest.TestCase):
    def test_concurrent_calls_get_their_own_ids_and_one_initialize(self):
        from _rank_intellihost import IntelliHostSource
        inits = []

        class Client:
            def request(self, provider, op, url, headers=None, body=None, text=False):
                if body["method"] == "initialize":
                    inits.append(1)
                time.sleep(0.01)
                payload = {"jsonrpc": "2.0", "id": body["id"],
                           "result": {"content": [{"type": "text", "text": json.dumps({"ok": body["id"]})}]}}
                return "event: message\ndata: " + json.dumps(payload) + "\n", {"mcp-session-id": "s1"}

        src = IntelliHostSource(Client(), SimpleNamespace(key=lambda p: "tok"))
        results, errors = [], []

        def call():
            try:
                results.append(src._tool("get-funnel-dashboard", {}))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len({r["ok"] for r in results}), 8)
        self.assertEqual(len(inits), 1)


class IntelliHostData(unittest.TestCase):
    START = date(2026, 9, 24)

    def test_stale_funnel_is_skipped_with_its_age(self):
        from _rank_intellihost import funnel_from_dashboard
        dash = {"period": {"from": "2026-05-01", "to": "2026-05-31"},
                "funnel": {"first_page_search_impressions": 1, "comp_first_page_search_impressions": 1}}
        f = funnel_from_dashboard(dash, self.START)
        self.assertEqual(f["status"], "skipped")
        self.assertIn("2026-05-31", f["reason"])
        self.assertIsNone(f["visibility_row"])

    def test_string_guest_counts_are_read(self):
        from _rank_intellihost import rank_rows
        rows = rank_rows({"series": [{"scrape_date": "2026-09-22", "guest_count": "2", "rank": 5, "page": 1}]},
                         self.START, guest_capacity=4)
        self.assertEqual([r["guest_count"] for r in rows], [2])

    def test_truncated_series_is_said_out_loud(self):
        from _rank_intellihost import IntelliHostSource, rank_rows
        payload = {"truncated": True, "series": [{"scrape_date": "2026-09-22", "guest_count": 1, "rank": 5, "page": 1}]}
        self.assertTrue(all(r["series_truncated"] for r in rank_rows(payload, self.START, 2)))
        src = IntelliHostSource.__new__(IntelliHostSource)
        src._token = "t"
        src.client = SimpleNamespace(fetch=lambda s, i, loader, ttl_seconds=0: loader())
        src._tool = lambda n, a: {"truncated": True, "series": []}
        with self.assertRaisesRegex(CannotAnalyze, "truncated"):
            src.rankings("11", self.START, 2)


# ------------------------------------------------------------------ item 26: RankBreeze + PriceLabs pile

class RankBreezeComplete(unittest.TestCase):
    @staticmethod
    def row(pull, complete=True):
        stages = ("first_page_impressions", "click_through_rate", "view", "wishlist", "booking_rate", "conversion_rate")
        comp = {k: {"listing": 1.0, "similar_listings": 2.0} for k in stages}
        if not complete:
            comp["booking_rate"] = {"listing": None, "similar_listings": 2.0}
        return {"listing_id": "555", "integration_status": "active", "pull_date": pull,
                "similar_listings_comparison": comp}

    def test_newest_complete_pull_wins_over_a_newer_partial_one(self):
        from _mvp_rankbreeze import funnel_from_summary
        f = funnel_from_summary({"metrics": [self.row("2026-09-23"), self.row("2026-09-24", complete=False)]},
                                date(2026, 9, 25), "555")
        self.assertEqual((f["status"], f["last_sync_date"]), ("ok", "2026-09-23"))


class Pile(unittest.TestCase):
    def pile_with(self, actions):
        client = SimpleNamespace(fetch=lambda s, i, loader, ttl_seconds=0: loader())
        src = Sources.__new__(Sources)
        src.client, src.connections = client, SimpleNamespace(account=lambda p: "acct")
        src.pl_get = lambda path, *a, **k: actions if path == "/v1/actions" else {"nudges": []}
        return src.pile("L1", "smartbnb")

    def test_actions_error_envelope_is_not_zero_actions(self):
        for bad in ({"error": "Unauthorized"}, {"message": "rate limited"}, None, "oops"):
            with self.subTest(bad=bad), self.assertRaises(CannotAnalyze):
                self.pile_with(bad)

    def test_real_empty_actions_are_zero(self):
        self.assertEqual(self.pile_with([])["counts"]["actions"], 0)
        self.assertEqual(self.pile_with({"data": []})["counts"]["actions"], 0)


# ------------------------------------------------------------------ item 28: Guesty reviews

class GuestyReviews(unittest.TestCase):
    def test_reviews_ask_by_listingId_and_drop_unscoped_rows(self):
        # Live 2026-09-25: Guesty /reviews answers `filters` with HTTP 400 ("filters" is not
        # allowed) and honours listingId. The per-row scope check still drops foreign rows.
        from urllib.parse import parse_qs, urlsplit

        from _pms_guesty import GuestySource
        seen = {}

        class Client:
            def request(self, provider, op, url, headers=None):
                seen["q"] = parse_qs(urlsplit(url).query)
                return {"data": [{"_id": "r1", "listingId": "L1", "channelId": "airbnb2", "rawReview": {"overall_rating": 5}},
                                 {"_id": "r2", "channelId": "airbnb2", "rawReview": {"overall_rating": 1}},
                                 {"_id": "r3", "listingId": "L2", "channelId": "airbnb2", "rawReview": {"overall_rating": 1}}]}, {}

            def fetch(self, source, ident, loader, ttl_seconds=0):
                return loader()

        src = GuestySource(Client(), SimpleNamespace(account_or=lambda p: "acct"))
        src._token = "tok"
        out = src.reviews("L1")
        self.assertEqual([r["id"] for r in out["data"]], ["r1"])
        self.assertEqual(seen["q"]["listingId"], ["L1"])
        self.assertNotIn("filters", seen["q"])


if __name__ == "__main__":
    unittest.main()
