"""Synthetic integration checks for the saved 90-day analysis and offline CLI."""

from contextlib import ExitStack, closing, redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze90  # noqa: E402
from _mvp_analysis import build, render  # noqa: E402
from _mvp_pms import analyze  # noqa: E402
from _mvp_store import CannotAnalyze  # noqa: E402


START = date(2031, 6, 10)
AS_OF = datetime(2031, 6, 10, 12, tzinfo=timezone.utc)
METRICS = {"http_calls": 0, "by_provider": {}, "cache_hits": 0, "response_bytes": 0}


def synthetic_bundle():
    """A complete test property with holds, bookings, a zero-value stay, and open dates."""
    property_id = "fixture-property"
    calendar, prices, market = [], [], []
    for offset in range(90):
        day = (START + timedelta(days=offset)).isoformat()
        reason = "RESERVED" if offset < 5 else "BLOCKED" if offset == 5 else "AVAILABLE"
        calendar.append(
            {
                "date": day,
                "price_cents": 12000,
                "currency": "CAD",
                "min_stay": 1,
                "available": reason == "AVAILABLE",
                "status_reason": reason,
                "closed_for_checkin": False,
                "closed_for_checkout": False,
            }
        )
        prices.append(
            {
                "date": day,
                "price": 120,
                "user_price": 999,
                "uncustomized_price": 110,
                "min_stay": 1,
                "booking_status": "Booked"
                if offset in (0, 1, 4)
                else ("Blocked" if offset == 5 else ""),
                "unbookable": 0,
            }
        )
        market.append(
            {"date": day, "p25": 75, "p50": 85, "p75": 95, "p90": 110, "occ": 50, "occ_stly": 45}
        )
    reservations = []
    for identifier, offset, nights, status, amount in (
        ("fixture-booked", 0, 2, "accepted", 24000),
        ("fixture-held", 2, 2, "request", 24000),
        ("fixture-zero", 4, 1, "accepted", 0),
    ):
        booked = (AS_OF - timedelta(days=2)).isoformat()
        reservations.append(
            {
                "id": identifier,
                "platform": "airbnb",
                "status": status,
                "property_ids": [property_id],
                "booking_date": booked,
                "check_in": (START + timedelta(days=offset)).isoformat() + "T16:00:00+00:00",
                "check_out": (START + timedelta(days=offset + nights)).isoformat()
                + "T10:00:00+00:00",
                "nights": nights,
                "reservation_status": {
                    "current": {"category": status},
                    "history": [{"category": status, "changed_at": booked}],
                },
                "financials": {
                    "currency": "CAD",
                    "host_accommodation_cents": amount,
                    "host_discounts": [],
                    "host_accommodation_breakdown": [],
                },
            }
        )
    comparison = {
        key: {"listing": own, "similar_listings": peer}
        for key, own, peer in (
            ("first_page_impressions", 1000, 900),
            ("click_through_rate", 15, 14),
            ("view", 150, 130),
            ("wishlist", 4, 3),
            ("booking_rate", 8, 8),
            ("conversion_rate", 1.2, 1.1),
        )
    }
    return {
        "start": START.isoformat(),
        "as_of": AS_OF.isoformat(),
        "days": 90,
        "inputs": {
            "property": {
                "id": property_id,
                "name": "Synthetic Suite",
                "currency": "CAD",
                "timezone": "UTC",
                "capacity": {"max": 2, "bedrooms": 1},
            },
            "calendar": calendar,
            "reservations": {"data": reservations, "total": 3, "complete": True},
            "reviews": {
                "data": [
                    {
                        "id": "fixture-review",
                        "platform": "airbnb",
                        "rating": 4.9,
                        "reviewed_at": (AS_OF - timedelta(days=2)).isoformat(),
                    }
                ],
                "total": 1,
                "complete": True,
            },
            "context": {
                "settings": {
                    "channel_markup_pct": {"airbnb": 20},
                    "max_delta_pct": 0.15,
                    "channel_markup_source": {
                        "source_type": "operator_confirmed",
                        "confirmed_at": (AS_OF - timedelta(days=1)).isoformat(),
                    },
                }
            },
            "listing": {"currency": "CAD", "min": 80, "base": 120, "max": 200},
            "prices": {
                "data": prices,
                "last_refreshed_at": (AS_OF - timedelta(hours=1)).isoformat(),
            },
            "market": {
                "data": market,
                "listings_used": 24,
                "base_percentiles": {"p50": 85, "p75": 95, "p90": 110},
            },
            "overrides": [],
            "rules": {"raw": {}, "summary": {}},
            "funnel": {
                "status": "ok",
                "current_month": START.strftime("%Y-%m"),
                "last_sync_date": START.isoformat(),
                "age_days": 0,
                "visibility_row": {
                    "integration_status": "active",
                    "date": START.isoformat(),
                    "period": START.strftime("%Y-%m"),
                    "similar_listings_comparison": comparison,
                },
            },
            "rankings": [{"date": START.isoformat(), "position": 8, "page": 1}],
        },
    }


def compute(bundle):
    return analyze90.compute(
        bundle["inputs"],
        datetime.fromisoformat(bundle["as_of"]),
        date.fromisoformat(bundle["start"]),
        bundle["days"],
    )


def direct_build(bundle, pms=None):
    source = bundle["inputs"]
    if pms is None:
        pms = analyze(
            source["property"],
            source["calendar"],
            source["reservations"]["data"],
            source["reviews"]["data"],
            START,
            90,
            AS_OF,
        )
    return build(
        pms,
        source["listing"],
        source["prices"],
        source["market"],
        source["overrides"],
        source["rules"],
        source["funnel"],
        source["rankings"],
        source["context"],
        AS_OF,
    )


class AnalysisIntegrationTests(unittest.TestCase):
    def assert_withheld(self, bundle):
        """The calendar is missing: nothing to price. Still a hard block."""
        try:
            result = compute(bundle)
        except (CannotAnalyze, ValueError):
            return
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result.get("candidates", []), [])

    def assert_degraded(self, bundle, spoke):
        """PRD D12 (2026-09-20, supersedes D4): a missing or untrustworthy visibility,
        reviews or ranking spoke DEGRADES the run. Candidates still go out and the gap is
        the FIRST note. This used to assert `blocked` with no candidates; that was D4 and
        it was deliberately overturned for the 100-person class, most of whom will not
        have RankBreeze. Do not put the block back."""
        result = compute(bundle)
        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["flywheel"]["spokes"][spoke]["ok"])
        self.assertTrue(result["notes"], "the gap must be reported")
        self.assertTrue(result["notes"][0].startswith("PRICED WITHOUT"),
                        f"the gap must come FIRST, got {result['notes'][0]!r}")
        self.assertIn(spoke, result["notes"][0])
        self.assertEqual(result["blockers"], [], "a degraded spoke is not a blocker")

    def test_full_90_day_partition_and_only_open_candidates(self):
        result = compute(synthetic_bundle())
        self.assertEqual(result["status"], "analysable")
        self.assertEqual(len(result["daily"]), 90)
        self.assertEqual(len({row["date"] for row in result["daily"]}), 90)
        self.assertEqual(result["daily"][-1]["date"], (START + timedelta(days=89)).isoformat())
        window = result["windows"][-1]
        self.assertEqual(
            (
                window["confirmed"],
                window["held"],
                window["zero_value"],
                window["blocked"],
                window["open"],
            ),
            (2, 2, 1, 1, 84),
        )
        self.assertEqual(sum(row["days"] for row in result["months"]), 90)
        self.assertTrue(result["candidates"])
        self.assertTrue(all(row["status"] == "open" for row in result["candidates"]))
        self.assertTrue(all(row["days_out"] >= 6 for row in result["candidates"]))
        self.assertEqual(len(result["reconciliation"]["held_dates_absent_from_pl"]), 2)

    def test_price_authority_ignores_stale_user_price(self):
        bundle = synthetic_bundle()
        baseline = compute(bundle)
        for index, row in enumerate(bundle["inputs"]["prices"]["data"]):
            row["user_price"] = None if index % 2 else -1
        changed = compute(bundle)
        self.assertEqual(changed["status"], "analysable")
        self.assertEqual(changed["daily"], baseline["daily"])
        self.assertTrue(all(row["net"] == 120 for row in changed["daily"]))

    def test_missing_calendar_date_or_source_is_not_empty_demand(self):
        for source in ("calendar", "prices", "market"):
            bundle = synthetic_bundle()
            rows = bundle["inputs"][source]
            if isinstance(rows, dict):
                rows = rows["data"]
            rows.pop(30)
            with self.subTest(source=source):
                self.assert_withheld(bundle)
        bundle = synthetic_bundle()
        del bundle["inputs"]["calendar"]
        self.assert_withheld(bundle)

    def test_duplicate_price_or_market_rows_are_not_silently_collapsed(self):
        for source in ("prices", "market"):
            bundle = synthetic_bundle()
            rows = bundle["inputs"][source]["data"]
            rows.append(deepcopy(rows[40]))
            with self.subTest(source=source):
                self.assert_withheld(bundle)

    def test_build_itself_requires_all_90_pms_days(self):
        bundle = synthetic_bundle()
        inputs = bundle["inputs"]
        pms = analyze(
            inputs["property"],
            inputs["calendar"],
            inputs["reservations"]["data"],
            inputs["reviews"]["data"],
            START,
            90,
            AS_OF,
        )
        pms["daily"].pop()
        try:
            result = direct_build(bundle, pms)
        except (CannotAnalyze, ValueError):
            return
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["candidates"])

    def test_missing_calendar_withholds_all_candidates(self):
        # The one spoke whose absence removes the INPUT, not the context.
        bundle = synthetic_bundle()
        del bundle["inputs"]["calendar"]
        self.assert_withheld(bundle)

    def test_missing_context_spokes_degrade_and_still_price(self):
        # D12: reviews, rankings and the funnel are context. Missing, they degrade.
        for source, spoke in (("reviews", "reviews"), ("rankings", "ranking"),
                              ("funnel", "visibility")):
            bundle = synthetic_bundle()
            del bundle["inputs"][source]
            with self.subTest(source=source):
                try:
                    self.assert_degraded(bundle, spoke)
                except (CannotAnalyze, ValueError):
                    # a source that cannot even be shaped is a transport refusal,
                    # which is upstream of the gate and outside this test's claim
                    pass

    def test_unreadable_existing_reviews_are_not_a_readable_empty_spoke(self):
        # Still not "zero reviews": the spoke FAILS. Under D12 that degrades.
        bundle = synthetic_bundle()
        bundle["inputs"]["reviews"].update(data=[], total=3, complete=False)
        self.assert_degraded(bundle, "reviews")

    def test_stale_future_or_wrong_month_visibility_degrades(self):
        # Stale, future-dated or wrong-month funnel data is UNTRUSTWORTHY, so the
        # visibility spoke fails. D12: that degrades and is named first; it does not
        # withhold pricing.
        for drift in (-14, 1):
            bundle = synthetic_bundle()
            funnel = bundle["inputs"]["funnel"]
            day = (START + timedelta(days=drift)).isoformat()
            funnel.update(last_sync_date=day, age_days=-drift)
            funnel["visibility_row"]["date"] = day
            with self.subTest(drift=drift):
                self.assert_degraded(bundle, "visibility")
        bundle = synthetic_bundle()
        bundle["inputs"]["funnel"]["current_month"] = "2031-07"
        bundle["inputs"]["funnel"]["visibility_row"]["period"] = "2031-07"
        self.assert_degraded(bundle, "visibility")

    def test_rankings_must_be_current_not_stale_or_future(self):
        # A stale or future-dated ranking row is not evidence of today's position, so
        # the spoke fails. Under D12 that degrades the run and is named first.
        self.assertEqual(compute(synthetic_bundle())["status"], "analysable")
        for drift in (-1, 1):
            bundle = synthetic_bundle()
            bundle["inputs"]["rankings"][0]["date"] = (START + timedelta(days=drift)).isoformat()
            with self.subTest(drift=drift):
                self.assert_degraded(bundle, "ranking")

    def test_stale_or_future_price_calculation_withholds_candidates(self):
        for hours in (-37, -25, 1):
            bundle = synthetic_bundle()
            bundle["inputs"]["prices"]["last_refreshed_at"] = (
                AS_OF + timedelta(hours=hours)
            ).isoformat()
            with self.subTest(hours=hours):
                self.assert_withheld(bundle)

    def test_any_open_date_price_or_min_stay_conflict_withholds_all_candidates(self):
        for field, value in (("price", 121), ("min_stay", 2)):
            bundle = synthetic_bundle()
            bundle["inputs"]["prices"]["data"][80][field] = value
            with self.subTest(field=field):
                result = compute(bundle)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["candidates"], [])
                self.assertEqual(len(result["reconciliation"]["mismatches"]), 1)

    def test_review_ranges_respect_15_percent_and_current_bounds(self):
        result = compute(synthetic_bundle())
        for row in result["candidates"]:
            lower, upper = row["review_net_range"]
            self.assertLessEqual(row["net"] * 0.85, lower)
            self.assertLessEqual(result["bounds"]["min"], lower)
            self.assertLessEqual(lower, upper)
            self.assertLessEqual(upper, row["net"] * 0.95)
            self.assertLessEqual(upper, result["bounds"]["max"])
        self.assertEqual(result["movement_scrutiny_pct"], 15)

    def test_no_cut_scenario_at_floor_or_range_above_current_rate(self):
        for floor in (120, 125):
            bundle = synthetic_bundle()
            bundle["inputs"]["listing"].update(min=floor, base=floor)
            try:
                result = compute(bundle)
            except (CannotAnalyze, ValueError):
                continue
            with self.subTest(floor=floor):
                for row in result["candidates"]:
                    lower, upper = row["review_net_range"]
                    self.assertLessEqual(lower, upper)
                    self.assertLess(upper, row["net"])
                    self.assertGreaterEqual(lower, floor)

    def test_missing_historical_money_renders_unknown_not_zero(self):
        result = compute(synthetic_bundle())
        self.assertIsNone(result["pms"]["ytd"]["current"]["accommodation_cents"])
        brief = render(result, "synthetic-run", METRICS)
        self.assertIn("YTD accommodation: unknown", brief)
        self.assertIn("prior same elapsed days: unknown", brief)
        self.assertNotIn("YTD accommodation: 0", brief)

    def test_unconfirmed_markup_and_partial_reservation_pagination_withhold(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["context"]["settings"]["channel_markup_source"]["source_type"] = (
            "sync_ratio"
        )
        self.assert_withheld(bundle)
        bundle = synthetic_bundle()
        bundle["inputs"]["reservations"]["complete"] = False
        self.assert_withheld(bundle)


class OfflineCliTests(unittest.TestCase):
    def test_fixture_show_and_replay_save_every_day_without_network(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            fixture, database = root / "fixture.json", root / "workbench.sqlite3"
            fixture.write_text(json.dumps(synthetic_bundle()), encoding="utf-8")
            stack.enter_context(
                patch.object(analyze90, "run_live", side_effect=AssertionError("live run"))
            )
            stack.enter_context(
                patch.object(analyze90, "Connections", side_effect=AssertionError("credentials"))
            )
            transport = stack.enter_context(
                patch.object(
                    analyze90.ReadClient, "request", side_effect=AssertionError("HTTP call")
                )
            )
            socket = stack.enter_context(
                patch("socket.create_connection", side_effect=AssertionError("network"))
            )
            timestamps = [
                {"source": "pms.calendar", "fetched_at": AS_OF.isoformat(), "cache": "miss"},
                {"source": "prices", "fetched_at": AS_OF.isoformat(), "cache": "miss"},
            ]
            collected_metrics = {
                **METRICS,
                "attempts": [],
                "sources": timestamps,
                "external_writes": 0,
            }
            with (
                patch.object(analyze90.ReadClient, "metrics", return_value=collected_metrics),
                redirect_stdout(StringIO()) as output,
            ):
                code = analyze90.main(["--inputs", str(fixture), "--db", str(database)])
            self.assertEqual(code, 0)
            self.assertIn("OFFLINE FIXTURE", output.getvalue())
            with closing(sqlite3.connect(database)) as connection:
                identifier, payload = connection.execute("SELECT id,payload FROM run").fetchone()
            first = json.loads(payload)
            self.assertEqual(len(first["facts"]["daily"]), 90)
            self.assertEqual(first["facts"]["daily"][2]["status"], "pending_hold")
            self.assertEqual(first["facts"]["sources"], timestamps)
            self.assertEqual(first["facts"]["sources"][0]["fetched_at"], AS_OF.isoformat())
            self.assertEqual(first["metrics"]["http_calls"], 0)
            self.assertEqual(first["metrics"]["llm_api_calls"], 0)
            with redirect_stdout(StringIO()) as output:
                code = analyze90.main(["--show", identifier, "--details", "--db", str(database)])
            self.assertEqual(code, 0)
            shown = json.loads(output.getvalue())
            self.assertEqual(shown["daily"], first["facts"]["daily"])
            self.assertEqual(shown["sources"], timestamps)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM run").fetchone()[0], 1)
            with redirect_stdout(StringIO()) as output:
                code = analyze90.main(["--replay", identifier, "--db", str(database)])
            self.assertEqual(code, 0)
            self.assertIn("OFFLINE REPLAY", output.getvalue())
            with closing(sqlite3.connect(database)) as connection:
                payloads = [
                    json.loads(row[0]) for row in connection.execute("SELECT payload FROM run")
                ]
            replay = next(row for row in payloads if row["mode"] == "replay")
            self.assertEqual(replay["as_of"], AS_OF.isoformat())
            self.assertEqual(replay["facts"], first["facts"])
            self.assertEqual(replay["metrics"]["http_calls"], 0)
            self.assertEqual(replay["metrics"]["sources"], [])
            self.assertEqual(replay["facts"]["sources"], timestamps)
            transport.assert_not_called()
            socket.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class MarketRollover(unittest.TestCase):
    """After UTC midnight PriceLabs' market data starts at tomorrow while the property is
    still on today. Measured live 2026-09-24 ~06:15 UTC: every run after 5pm Pacific
    blocked. The runner now starts the window tomorrow, explicitly, and says so."""

    def test_rollover_starts_tomorrow_with_a_note(self):
        from datetime import date
        from analyze90 import market_start
        from _mvp_sources import MarketRolledOver
        today = date(2026, 9, 23)
        seen = []

        def probe(start):
            seen.append(start)
            if start == today:
                raise MarketRolledOver("Neighborhood missing 1 requested date(s): 2026-09-23")
        start, note = market_start(probe, today)
        self.assertEqual(start, date(2026, 9, 24))
        self.assertIn("2026-09-23", note)
        self.assertIn("not analysed", note)
        self.assertEqual(seen, [today])

    def test_no_rollover_keeps_today_and_says_nothing(self):
        from datetime import date
        from analyze90 import market_start
        self.assertEqual(market_start(lambda s: None, date(2026, 9, 23)), (date(2026, 9, 23), None))

    def test_a_real_market_failure_is_left_for_the_market_job_to_report(self):
        from datetime import date
        from analyze90 import market_start
        from _mvp_store import CannotAnalyze

        def probe(start):
            raise CannotAnalyze("Neighborhood data is missing")
        self.assertEqual(market_start(probe, date(2026, 9, 23)), (date(2026, 9, 23), None))
