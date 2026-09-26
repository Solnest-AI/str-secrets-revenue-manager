"""The 90-day runner on Beyond (analyze90 --pricing beyond): a DEGRADED card, every gap named.

End to end: the real analyze90.main, its real ReadClient and SQLite workbench, BeyondSource,
the runner adapters, the analysis and the card. Only the network is replaced (the doc-built
FakeBeyond from test_beyond_write plus a fake AirROI, behind a router that fails any other
host), and the PMS is a stub at the Sources level whose calendar agrees with the fake's own.
DOCS-ONLY: the fake is our reading of Beyond's docs, not a recording of a live account.
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import _beyond as B
import _beyond_runner as BR
import analyze90
from _mvp_store import Store
from test_beyond_write import LID, TODAY, TOKEN, FakeBeyond, Response, http_error

AS_OF = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
PID = "pms-prop-0001"
AIRROI_KEY = "airroi-synthetic"
PROP = {"id": PID, "name": "Beyond Test Cabin", "currency": "USD", "timezone": "UTC", "listed": True,
        "capacity": {"max": 4, "bedrooms": 2, "bathrooms": 1.0},
        "listings": [{"platform": "airbnb", "platform_id": "777"}]}


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return AS_OF if tz else AS_OF.replace(tzinfo=None)


class FakeAirROI:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req.full_url)
        assert req.get_header("X-api-key") == AIRROI_KEY
        rows = [{"listing_info": {"listing_id": str(1000 + i), "listing_name": f"Comp {i}"},
                 "property_details": {"bedrooms": 2, "baths": 1.0, "guests": 4},
                 "performance_metrics": {"ttm_revenue": 40000 + i * 1000, "ttm_avg_rate": 180 + i * 20,
                                         "ttm_occupancy": 0.6, "ttm_revpar": 120,
                                         "ttm_avg_length_of_stay": 3},
                 "ratings": {"rating_overall": 4.8, "num_reviews": 50},
                 "booking_settings": {"min_nights": 2}, "pricing_info": {"currency": "USD"}}
                for i in range(5)]
        return Response({"listings": rows})


class Router:
    """The only network there is: Beyond and AirROI. Any other host fails the test."""

    def __init__(self, beyond, airroi):
        self.beyond, self.airroi = beyond, airroi

    def open(self, req, timeout):
        host = urlsplit(req.full_url).netloc
        if host == B.HOST:
            return self.beyond.open(req, timeout)
        if host == "api.airroi.com":
            return self.airroi.open(req, timeout)
        raise AssertionError(f"unexpected network call to {host}")


def pms_stub(fake, *, min_stay=2, price_off=()):
    """A PMS that agrees with Beyond: same quoted price per night, booked where Beyond is."""
    class Stub:
        def __init__(self, client, connections, pms="hospitable"):
            self.pms = pms

        def __getattr__(self, name):  # every PriceLabs read: not connected in these tests
            def refuse(*a, **k):
                raise analyze90.CannotAnalyze(f"PriceLabs is not connected ({name})")
            return refuse

        def property(self, selector):
            assert selector == PROP["name"]
            return copy.deepcopy(PROP)

        def calendar(self, pid, start, days):
            rows = []
            for i in range(days):
                d = (start + timedelta(days=i)).isoformat()
                price = fake.entry(d)["attributes"]["price-posted"]
                if d in price_off:
                    price += 7
                booked = d in fake.booked
                rows.append({"date": d, "price_cents": price * 100, "currency": "USD", "min_stay": min_stay,
                             "available": not booked, "status_reason": "RESERVED" if booked else "AVAILABLE",
                             "closed_for_checkin": False, "closed_for_checkout": False})
            return rows

        def reservations(self, pid, start, days):
            booked = (AS_OF - timedelta(days=3)).isoformat()
            data = [{"id": "res-1", "platform": "airbnb", "status": "accepted", "property_ids": [PID],
                     "booking_date": booked, "check_in": "2026-10-01T16:00:00+00:00",
                     "check_out": "2026-10-03T10:00:00+00:00", "nights": 2,
                     "reservation_status": {"current": {"category": "accepted"},
                                            "history": [{"category": "accepted", "changed_at": booked}]},
                     "financials": {"currency": "USD", "host_accommodation_cents": 44000,
                                    "host_discounts": [], "host_accommodation_breakdown": []}}]
            return {"data": data, "total": 1, "complete": True, "pages": 1}

        def reviews(self, pid):
            return {"data": [{"id": "rev-1", "platform": "airbnb", "rating": 4.9,
                              "reviewed_at": (AS_OF - timedelta(days=5)).isoformat()}],
                    "total": 1, "complete": True, "pages": 1}
    return Stub


def beyond_fake(**kw):
    fake = FakeBeyond(max_price=None, **kw)
    fake.booked = {"2026-10-01", "2026-10-02"}
    # 12 of the next 30 nights modeled under the 150 floor: Beyond pins them at it
    for i in range(3, 30, 2)[:12]:
        fake.modeled[(TODAY + timedelta(days=i)).isoformat()] = 120
    return fake


class Card(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_card(self, fake, *, airroi=True, settings=None, args=(), pms=None):
        env = self.dir / "keys.env"
        env.write_text(f"BEYOND_TOKEN={TOKEN}\n" + (f"AIRROI_API_KEY={AIRROI_KEY}\n" if airroi else ""))
        cfg = self.dir / "claude.json"
        cfg.write_text("{}")
        s = {"pms_source": "hospitable", "pricing_tool": "beyond", "beyond_listing_id": LID,
             "airbnb_listing_id": "777", "max_delta_pct": 0.15, "pricing_gap": None,
             "channel_markup_pct": {"airbnb": 15},
             "channel_markup_source": {"source_type": "operator_confirmed",
                                       "confirmed_at": "2026-09-30T12:00:00Z"}}
        s.update(settings or {})
        sfile = self.dir / "settings.json"
        sfile.write_text(json.dumps({"property_id": PID, "settings": s}))
        self.airroi = FakeAirROI()
        router = Router(fake, self.airroi)
        out = StringIO()
        with patch("analyze90.Sources", pms or pms_stub(fake)), \
                patch("analyze90.datetime", FixedDateTime), \
                patch("_beyond.utc_now", lambda: AS_OF.isoformat(timespec="seconds")), \
                patch("_mvp_store.urllib.request.build_opener", lambda *h: router), \
                redirect_stdout(out):
            code = analyze90.main(["--property", PROP["name"], "--pms", "hospitable", "--settings", str(sfile),
                                   "--db", str(self.dir / "wb.sqlite3"), "--env-file", str(env),
                                   "--connection-config", str(cfg), *args])
        brief = out.getvalue()
        run_id = re.search(r"(?:Run |analysis )([0-9a-f-]{12})", brief).group(1)
        store = Store(self.dir / "wb.sqlite3")
        try:
            run = store.get_run(run_id)
        finally:
            store.close()
        return code, brief, run

    # ------------------------------------------------------------------ the full card

    def test_full_beyond_card_prices_and_names_every_gap(self):
        fake = beyond_fake()
        code, brief, run = self.run_card(fake)
        facts = run["facts"]
        self.assertEqual((code, facts["status"]), (0, "degraded"), brief)
        self.assertEqual(facts["blockers"], [])
        # every gap, named at the top of the card
        top = brief.split("days | confirmed")[0]
        for words in ("GAPS (priced without these",
                      "Market percentiles: Beyond gives averages, not p25-p90",
                      "Beyond market insights (cluster benchmark, averages)",
                      "Comp source: AirROI trailing-12-month ADR",
                      BR.GAP_RULES, BR.GAP_MIN_STAY,
                      "Freshness: Beyond gives no price-calculation time; prices are as read at",
                      BR.GAP_CEILING, BR.GAP_PILE):
            self.assertIn(words, top)
        self.assertIn("Bounds min/base/max: 150/200/none", brief)
        self.assertIn("Pricing tool Beyond: calendar read at", brief)
        self.assertNotIn("PriceLabs calculated", brief)
        # the flywheel's own degraded spokes still show (no RankBreeze here)
        self.assertIn("visibility: unreadable", brief)
        # the min is an output, from the SAME rule as PriceLabs: 11 of 28 open nights sit on the
        # floor (12 modeled under it; 2026-10-10 carries a fixed override) and pace lags, but
        # the min is already under AirROI's ADR p25 (net), so the rule keeps it and says why
        rec = facts["min_price"]
        self.assertEqual((rec["floor_open_near"], rec["open_near"], rec["pace"], rec["action"]),
                         (11, 28, "lags", "keep"))
        self.assertIn("Recommended min price: keep at 150 net (currently 150). 11 of 28 open nights", brief)
        self.assertIn("the lower quartile of AirROI trailing-12-month ADR", brief)
        self.assertIn("Heads up: comps behind Beyond's benchmark: unknown", brief)
        # dated review scenarios, cuts AND raises, each measured against a NAMED reference
        by = {c["direction"]: c["reference_source"] for c in facts["candidates"]}
        self.assertEqual(by, {"cut": "beyond_benchmark_avg", "raise": "airroi_adr_p25"})
        self.assertIn("vs Beyond benchmark avg 200; review cut net 187-209", brief)
        # the raise scenarios exist, but this listing books 7% vs the market's 60%: the booking
        # guard withholds the raises (cheap and still not booking is not a price problem) and the
        # card says so instead of dropping them silently
        self.assertTrue(all(c.get("withheld_by_guard") for c in facts["candidates"] if c["direction"] == "raise"))
        self.assertNotIn("review raise net", brief)
        self.assertIn("booking guard: nights 1-30 of the window are 7% booked vs the market's 60%", brief)
        self.assertTrue(all(c["days_out"] < 14 for c in facts["candidates"] if c["direction"] == "cut"))
        # read-only, and only Beyond + AirROI were called
        self.assertEqual([r for r in fake.requests if r[0] != "GET"], [])
        self.assertEqual(set(run["metrics"]["by_provider"]), {"beyond", "airroi"})
        self.assertIn("external writes 0", brief)
        self.assertEqual(facts["rule_effectiveness"], [])
        self.assertIsNone(facts["pile"])

    def test_a_saved_beyond_run_replays_offline(self):
        code, _, run = self.run_card(beyond_fake())
        out = StringIO()
        with redirect_stdout(out):
            again = analyze90.main(["--replay", run["id"], "--db", str(self.dir / "wb.sqlite3")])
        self.assertEqual(again, code)
        self.assertIn("OFFLINE REPLAY", out.getvalue())
        self.assertIn(BR.GAP_RULES, out.getvalue())

    # ------------------------------------------------------------------ market variants

    def test_market_in_another_currency_is_not_converted_and_airroi_stands_in(self):
        fake = beyond_fake()
        fake.market_currency = "EUR"
        code, brief, run = self.run_card(fake)
        self.assertEqual(code, 0, brief)
        self.assertIn("market comparison unavailable in Beyond's API: its market insights are priced in "
                      "your billing currency EUR, your listing in USD", brief)
        self.assertIn("is the only market reference on this card", brief)
        by = {c["direction"]: c["reference_source"] for c in run["facts"]["candidates"]}
        self.assertEqual(by, {"cut": "airroi_adr_p75", "raise": "airroi_adr_p25"})

    def test_unreadable_market_is_a_named_gap_not_a_crash(self):
        fake = beyond_fake()
        fake.market_status = 403  # token without insights:read
        code, brief, _ = self.run_card(fake)
        self.assertEqual(code, 0, brief)
        self.assertIn("market insights could not be read: beyond market-insights: HTTP 403", brief)

    def test_no_benchmark_on_the_cluster_retries_the_whole_market_once(self):
        fake = beyond_fake()
        fake.benchmark = False
        code, brief, _ = self.run_card(fake)
        scopes = [r[2].get("filter[compare-to]") for r in fake.requests if r[1].endswith("/market-insights/")]
        self.assertEqual(scopes, [["cluster"], ["market"]])
        self.assertIn("Beyond has no benchmark coverage", brief)

    def test_no_market_and_no_comps_says_so_and_emits_no_scenarios(self):
        fake = beyond_fake()
        fake.market_currency = "EUR"
        code, brief, run = self.run_card(fake, airroi=False)
        self.assertEqual(code, 0, brief)
        self.assertIn("No comp source: AirROI is not connected", brief)
        self.assertIn(BR.GAP_NO_RAISE, brief)
        self.assertEqual(run["facts"]["candidates"], [])
        self.assertIn("Recommended min price: keep at 150 net (currently 150)", brief)
        self.assertIn("pace vs the market is unreadable", brief)

    def test_the_min_lowers_by_the_same_rule_when_no_comp_quartile_holds_it(self):
        code, brief, run = self.run_card(beyond_fake(), airroi=False)
        self.assertEqual(code, 0, brief)
        self.assertIn("Recommended min price: lower to 128 net (currently 150). 11 of 28 open nights in "
                      "the next 30 days sit at your min and your next 30 nights are 7% booked vs the "
                      "market's 60%; comp lower quartile unavailable, so the step is the cap", brief)

    def test_a_ceiling_when_beyond_has_one_is_shown_not_gapped(self):
        fake = FakeBeyond()  # max 400
        fake.booked = {"2026-10-01", "2026-10-02"}
        code, brief, _ = self.run_card(fake)
        self.assertIn("Bounds min/base/max: 150/200/400", brief)
        self.assertNotIn(BR.GAP_CEILING, brief)

    # ------------------------------------------------------------------ sync check and refusals

    def test_min_stay_half_is_skipped_and_a_price_mismatch_is_scoped_to_its_date(self):
        fake = beyond_fake()
        code, brief, run = self.run_card(fake, pms=pms_stub(fake, min_stay=5))
        self.assertEqual(run["facts"]["reconciliation"]["mismatches"], [])  # min stay not compared
        code, brief, run = self.run_card(fake, pms=pms_stub(fake, price_off={"2026-10-05"}))
        self.assertEqual((code, run["facts"]["status"]), (0, "degraded"))
        self.assertIn("SYNC MISMATCH on 1 date(s), pricing withheld on those dates only: 2026-10-05. "
                      "PMS and Beyond disagree", brief)
        self.assertIn("2026-10-05: PMS and Beyond disagree on price; fix the sync", brief)
        self.assertNotIn("2026-10-05", {c["date"] for c in run["facts"]["candidates"]})
        self.assertTrue(run["facts"]["candidates"])  # the rest of the run is still priced

    def test_mismatches_on_more_than_a_fifth_of_open_nights_block_the_run(self):
        fake = beyond_fake()
        off = {(TODAY + timedelta(days=i)).isoformat() for i in range(3, 25)}  # 22 of 88 open
        code, brief, run = self.run_card(fake, pms=pms_stub(fake, price_off=off))
        self.assertEqual((code, run["facts"]["status"]), (2, "blocked"))
        self.assertIn("PMS/Beyond price or booking mismatches on 22 dates, more than 20% of the 88 open "
                      "nights", brief)

    def test_an_unreadable_beyond_calendar_blocks_by_name(self):
        class NotClustered(FakeBeyond):
            def open(self, req, timeout):
                if urlsplit(req.full_url).path.endswith("/calendar/"):
                    raise http_error(req.full_url, 400)
                return super().open(req, timeout)
        fake = NotClustered(max_price=None)
        code, brief, _ = self.run_card(fake)
        self.assertEqual(code, 2)
        self.assertIn("Unreadable required sources: prices", brief)
        self.assertIn("prices: beyond calendar: HTTP 400", brief)

    def test_missing_beyond_mapping_is_named(self):
        code, brief, _ = self.run_card(beyond_fake(), settings={"beyond_listing_id": None})
        self.assertEqual(code, 2)
        self.assertIn("run setup_properties.py --pricing beyond", brief)

    def test_explicit_pricelabs_never_touches_beyond(self):
        fake = beyond_fake()
        code, brief, _ = self.run_card(fake, args=("--pricing", "pricelabs"))
        self.assertEqual(code, 2)
        self.assertIn("Unreadable required sources", brief)
        self.assertEqual(fake.requests, [])


# ------------------------------------------------------------------ the one min rule, Beyond-shaped

class MinPriceOnBeyondRows(unittest.TestCase):
    """_mvp_analysis.min_price_recommendation is the only min rule; Beyond rows carry no p25."""

    def rows(self, pinned=10, open_n=20, booked=0, occ=60.0, sold_at_floor=0):
        out = []
        for i in range(30):
            status = "open" if i < open_n else "confirmed_paid" if i < open_n + booked else "blocked"
            at_floor = (i < pinned) if status == "open" else (i - open_n < sold_at_floor)
            out.append({"days_out": i, "status": status, "at_floor": at_floor, "market_occ": occ, "p25": None})
        return out

    def test_a_borrowed_comp_quartile_is_named_and_never_undercut(self):
        from _mvp_analysis import min_price_recommendation
        mp = min_price_recommendation({"min": 150.0, "base": 200.0, "max": None}, self.rows(), 1.15, 0.15,
                                      comp_p25=161.0, comp_source="AirROI trailing-12-month ADR, 5 comps")
        self.assertEqual((mp["action"], mp["recommended"], mp["comp_p25_net"]), ("lower", 140, 140.0))
        self.assertIn("the lower quartile of AirROI trailing-12-month ADR, 5 comps is 140 net", mp["reason"])

    def test_no_ceiling_is_never_read_and_the_raise_stops_at_base(self):
        from _mvp_analysis import min_price_recommendation
        rows = self.rows(pinned=0, open_n=5, booked=20, occ=20.0, sold_at_floor=5)
        mp = min_price_recommendation({"min": 190.0, "base": 200.0, "max": None}, rows, 1.15, 0.15)
        self.assertEqual((mp["action"], mp["recommended"]), ("raise", 200))


class Market(unittest.TestCase):
    def test_usable_market_gaps(self):
        self.assertEqual(BR.usable_market(None, "USD")["status"], "unavailable")
        doc = {"status": "read", "currency": "USD", "benchmark_available": False, "data": []}
        self.assertIn("no benchmark coverage", BR.usable_market(doc, "USD")["reason"])
        doc = {"status": "read", "currency": "USD", "benchmark_available": True, "compare_to": "market",
               "data": [{"date": "2026-10-01", "posted_avg": None, "occ": 50.0}]}
        got = BR.usable_market(doc, "USD")
        self.assertEqual((got["status"], got["missing_dates"]), ("ok", 1))
        self.assertIn("market benchmark", got["source"])

    def test_undocumented_availability_is_refused_not_guessed(self):
        cal = {"read_at": "x", "data": [{"date": "2026-10-01", "price": 100.0, "price_posted": None,
                                         "availability": "maybe"}]}
        with self.assertRaises(ValueError):
            BR.runner_prices(cal, TODAY, 1)


# ------------------------------------------------------------------ setup mapping

class SetupMapping(unittest.TestCase):
    def rows(self, *items):
        return [{"id": i, "name": n, "channels": [{"channel": c, "channel_id": v} for c, v in ch]}
                for i, n, ch in items]

    def test_pms_id_then_airbnb_id_then_exact_name(self):
        from setup_properties import match_beyond
        prop = {"id": "pms-1", "name": "Lake  House", "listings": [{"platform": "airbnb", "platform_id": "777"}]}
        rows = self.rows(("1", "Other", [("hospitable", "pms-1")]), ("2", "Other", [("airbnb", "777")]),
                         ("3", "lake house", []))
        self.assertEqual(match_beyond(prop, rows, "hospitable"), ("1", "Hospitable id"))
        self.assertEqual(match_beyond(prop, rows[1:], "hospitable"), ("2", "Airbnb id"))
        self.assertEqual(match_beyond(prop, rows[2:], "hospitable"), ("3", "exact name"))
        self.assertEqual(match_beyond(prop, [], "hospitable")[0], None)

    def test_two_candidates_are_not_guessed_and_do_not_fall_through(self):
        from setup_properties import match_beyond
        prop = {"id": "pms-1", "name": "Lake House", "listings": [{"platform": "airbnb", "platform_id": "777"}]}
        rows = self.rows(("1", "A", [("airbnb", "777")]), ("2", "B", [("airbnb", "777")]), ("3", "Lake House", []))
        got = match_beyond(prop, rows, "hospitable")
        self.assertEqual(got[0], None)
        self.assertIn("2 Beyond listings share its Airbnb id; not guessed", got[1])

    def test_settings_store_the_pricing_tool(self):
        from setup_properties import build_settings
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        s = build_settings("pms-1", "777", None, {"airbnb": 15.0}, now, pricelabs=False, beyond="48213")
        self.assertEqual((s["pricing_tool"], s["beyond_listing_id"], s["pricing_gap"]), ("beyond", "48213", None))
        self.assertNotIn("pricelabs_listing_id", s)
        self.assertEqual(build_settings("pms-1", None, None, {"airbnb": 1.0}, now)["pricing_tool"], "pricelabs")
        gap = build_settings("pms-1", None, None, {"airbnb": 1.0}, now, pricelabs=False)
        self.assertEqual(gap["pricing_tool"], None)
        self.assertIn("PriceLabs or Beyond", gap["pricing_gap"])
        # Beyond sets the prices but is not readable yet: still Beyond-owned, with a named gap
        stated = build_settings("pms-1", None, None, {"airbnb": 1.0}, now, pricelabs=False, pricing_tool="beyond")
        self.assertEqual(stated["pricing_tool"], "beyond")
        self.assertIn("BEYOND_TOKEN is not connected", stated["pricing_gap"])
        unmapped = build_settings("pms-1", None, None, {"airbnb": 1.0}, now, pricelabs=False,
                                  pricing_tool="beyond", gap="could not be matched")
        self.assertEqual((unmapped["pricing_tool"], unmapped["pricing_gap"]), ("beyond", "could not be matched"))
        self.assertEqual(build_settings("p", None, None, {"airbnb": 1.0}, now, min_price=140,
                                        pricelabs=False, beyond="9")["min_price"], 140.0)

    def test_the_runner_context_keeps_the_mapping(self):
        from _mvp_config import normalized_context
        ctx = normalized_context({"config": [{"property_id": "p", "settings": {
            "pricing_tool": "beyond", "beyond_listing_id": "48213", "secret": "x"}}]}, "p")
        self.assertEqual(ctx["settings"], {"pricing_tool": "beyond", "beyond_listing_id": "48213"})


class CliThroughTheRealWriter(unittest.TestCase):
    """apply_change.py's own Beyond routing, driving the REAL _beyond_write (integrate's routing
    tests use a stand-in module): plan, apply on a yes, undo, apply the undo."""

    def test_plan_apply_rollback_apply(self):
        import os

        import apply_change
        from _mvp_config import Connections
        fake = FakeBeyond()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = tmp / "keys.env"
            env.write_text(f"BEYOND_TOKEN={TOKEN}\n")
            change = tmp / "change.json"
            change.write_text(json.dumps({"listing_id": LID, "pms": "beyond", "reason": "test base",
                                          "listing_prices": {"base": 210}}))

            def cli(*argv):
                out, err = StringIO(), StringIO()
                with patch.dict(os.environ, {"RC_CACHE_DIR": str(tmp / "cache")}), \
                        patch.object(apply_change, "Connections",
                                     lambda env_files=(): Connections(env_files, config_path=tmp / "none.json")), \
                        patch("_beyond_write.urllib.request.build_opener", lambda *h: fake), \
                        redirect_stdout(out), patch("sys.stderr", err):
                    code = apply_change.main(["--env-file", str(env), *argv])
                return code, out.getvalue() + err.getvalue()

            code, out = cli("plan", "--change", str(change))
            self.assertEqual(code, 0, out)
            self.assertIn("First live write for Beyond", out)
            pid = re.search(r"--target beyond --plan ([0-9a-f]{12})", out).group(1)
            self.assertEqual([r for r in fake.requests if r[0] != "GET"], [])
            code, out = cli("apply", "--plan", pid, "--no-audit")
            self.assertEqual(code, 0, out)
            self.assertIn("APPLIED AND VERIFIED", out)
            self.assertEqual(fake.cust["base-price"]["base-price"], 210)
            journal = re.search(r"rollback --target beyond --journal (\S+\.json)", out).group(1)
            code, out = cli("rollback", "--journal", journal)
            self.assertEqual(code, 0, out)
            undo = re.search(r"--plan ([0-9a-f]{12})", out).group(1)
            code, out = cli("apply", "--plan", undo, "--no-audit")
            self.assertEqual(code, 0, out)
            self.assertEqual(fake.cust["base-price"]["base-price"], 200)


class Audit(unittest.TestCase):
    def test_audit_statement_takes_the_dateless_min_stay_operation(self):
        from apply_change import audit_statement
        env = {"target": {"listing_id": LID, "pms": "beyond", "currency": "USD"}, "listing_name": "Cabin",
               "reason": "r", "operations": [
                   {"kind": "listing_min_stay", "field": "min_stay", "before": 2, "after": 3},
                   {"kind": "listing_price", "field": "min", "before": 150.0, "after": 160.0},
                   {"kind": "override", "date": "2026-10-05", "before": None, "after": {"date": "2026-10-05",
                                                                                      "price": 230.0}}]}
        sql = audit_statement({"envelope": env, "plan_id": "abc", "journal_path": "/x/j.json",
                               "status": "verified"})
        self.assertIn("'beyond_listing_min_stay', 'min_stay', '2', '3'", sql)
        self.assertIn("'beyond_override_set', '2026-10-05'", sql)

    def test_a_null_max_is_recorded_as_null_not_deleted(self):
        from apply_change import audit_statement
        env = {"target": {"listing_id": LID, "pms": "beyond", "currency": "USD"}, "listing_name": "Cabin",
               "reason": "undo", "operations": [
                   {"kind": "listing_price", "field": "max", "before": 400.0, "after": None}]}
        sql = audit_statement({"envelope": env, "plan_id": "abc", "journal_path": "/x/j.json",
                               "status": "verified"})
        self.assertIn("'beyond_listing_price', 'max', '400.0', 'null'", sql)
        self.assertNotIn("deleted", sql)


if __name__ == "__main__":
    unittest.main()
