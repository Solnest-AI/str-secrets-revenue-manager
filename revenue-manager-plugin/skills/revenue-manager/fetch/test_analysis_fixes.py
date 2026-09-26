"""Regression tests for the 2026-09-25 analysis audit (fix/analysis).

Each class pins one confirmed bug: occupancy denominators, mismatch scoping, the missing
min-price recommendation, rule grading, reducer dates, thin comps, the saved movement cap
and days-out after the evening rollover.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attribution  # noqa: E402
import reduce_prices  # noqa: E402
from _calendar import local_today, pricelabs_status  # noqa: E402
from _mvp_analysis import build, min_price_recommendation, render  # noqa: E402
from _mvp_pms import analyze  # noqa: E402
from test_mvp_analysis import (  # noqa: E402
    AS_OF, METRICS, START, compute, direct_build, synthetic_bundle,
)
import test_mvp_pms as pms_t  # noqa: E402

HERE = Path(__file__).resolve().parent


# 1 ------------------------------------------------------------------ occupancy denominator
class OccupancyExcludesBlockedNights(unittest.TestCase):
    def test_owner_blocks_leave_the_denominator(self):
        # 21 owner-blocked + 2 booked of 30 used to print 6.67%. The true figure is 2 of 9
        # bookable nights, 22.22%.
        s = pms_t.START
        cal = pms_t.calendar_rows(
            days=30,
            reserved={s, s + timedelta(days=1)},
            blocked={s + timedelta(days=n) for n in range(2, 23)},
        )
        result = pms_t.run([pms_t.reservation(nights=2, total=20000)], cal, days=30)
        window = next(w for w in result["windows"] if w["days"] == 30)
        self.assertEqual(window["blocked_nights"], 21)
        self.assertEqual(window["bookable_nights"], 9)
        self.assertEqual(window["confirmed_occupancy_pct"], 22.22)

    def test_brief_prints_blocked_beside_occupancy(self):
        brief = render(compute(synthetic_bundle()), "run", METRICS)
        self.assertIn("blocked", brief.split("days |", 1)[1].splitlines()[0])
        self.assertIn("1 owner-blocked", brief)

    def test_reducers_treat_unbookable_like_the_runner(self):
        row = {"date": "2031-06-10", "booking_status": "", "unbookable": "1"}
        self.assertEqual(pricelabs_status(row), "BLOCKED")
        self.assertTrue(reduce_prices.is_blocked(row))
        rows = []
        for i in range(30):
            d = (START + timedelta(days=i)).isoformat()
            status = "Booked" if i < 3 else ""
            rows.append({"date": d, "booking_status": status,
                         "unbookable": 1 if 3 <= i < 13 else 0, "booking_status_STLY": ""})
        self.assertIn("next30 occ=15.0%", reduce_prices.pacing_line(rows, START.isoformat()))
        _, _, totals = reduce_prices.tier_b(rows, 12.0)
        self.assertEqual(totals["blocked"], 10)
        self.assertEqual(totals["bookable"], 20)


# 2 ---------------------------------------------------------------- mismatch scoping
class MismatchesAreScopedToTheirDates(unittest.TestCase):
    def test_one_mismatch_withholds_only_that_date(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["prices"]["data"][10]["price"] = 121
        result = compute(bundle)
        self.assertNotEqual(result["status"], "blocked", result["blockers"])
        self.assertEqual(len(result["reconciliation"]["mismatches"]), 1)
        row = result["daily"][10]
        self.assertEqual(row["action"], "pricing_opinion_withheld")
        self.assertIn("PriceLabs", row["withheld_reason"])
        self.assertNotIn(row["date"], {c["date"] for c in result["candidates"]})
        self.assertTrue(result["candidates"], "every other date keeps its recommendation")
        brief = render(result, "run", METRICS)
        head = "\n".join(brief.splitlines()[:3])
        self.assertIn("MISMATCH", head)
        self.assertIn(row["date"], head)

    def test_mismatches_over_twenty_percent_of_open_nights_block_the_run(self):
        bundle = synthetic_bundle()
        for i in range(10, 40):
            bundle["inputs"]["prices"]["data"][i]["price"] = 121
        result = compute(bundle)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("20%" in b for b in result["blockers"]), result["blockers"])
        self.assertEqual(result["candidates"], [])


# 3 ---------------------------------------------------------------- min price
def _rows(n, status, at_floor, occ=50.0, p25=None, start=0):
    return [{"date": (START + timedelta(days=start + i)).isoformat(), "days_out": start + i,
             "status": status, "at_floor": at_floor, "market_occ": occ, "p25": p25}
            for i in range(n)]


class MinPriceRecommendation(unittest.TestCase):
    bounds = {"min": 100.0, "base": 150.0, "max": 300.0}

    def test_keep_prints_a_number_and_a_reason(self):
        rec = min_price_recommendation(self.bounds, _rows(30, "open", False), 1.2, 0.15)
        self.assertEqual(rec["action"], "keep")
        self.assertEqual(rec["recommended"], 100)
        self.assertTrue(rec["reason"])
        self.assertNotIn("breakeven", rec["reason"].lower())

    def test_after_rollover_next_30_nights_is_the_tables_30_nights(self):
        # Live 2026-09-25: the window started tomorrow (days_out 1..90) and the min line read
        # 9 of 29 nights (31%) under a table saying 9 of 30 (30.0%).
        rows = (_rows(9, "confirmed_paid", False, start=1) + _rows(21, "open", False, start=10)
                + _rows(60, "open", False, start=31))
        rec = min_price_recommendation(self.bounds, rows, 1.2, 0.15)
        self.assertIn("next 30 nights are 30% booked", rec["reason"])
        self.assertEqual(rec["open_near"], 21)

    def test_floor_pinned_and_lagging_pace_lowers_capped_at_15pct(self):
        rows = _rows(28, "open", True, p25=60.0) + _rows(2, "confirmed_paid", True, start=28)
        rec = min_price_recommendation(self.bounds, rows, 1.2, 0.15)
        self.assertEqual(rec["action"], "lower")
        self.assertEqual(rec["recommended"], 85)
        self.assertTrue(rec["large_move"])
        self.assertIn("15%", rec["reason"])

    def test_never_lowered_below_comp_lower_quartile_net_of_markup(self):
        rows = _rows(28, "open", True, p25=114.0) + _rows(2, "confirmed_paid", True, start=28)
        rec = min_price_recommendation(self.bounds, rows, 1.2, 0.15)
        self.assertEqual(rec["action"], "lower")
        self.assertGreaterEqual(rec["recommended"], 114.0 / 1.2)
        self.assertFalse(rec["large_move"])

    def test_floor_already_at_comp_quartile_keeps(self):
        rows = _rows(28, "open", True, p25=120.0) + _rows(2, "confirmed_paid", True, start=28)
        rec = min_price_recommendation(self.bounds, rows, 1.2, 0.15)
        self.assertEqual(rec["action"], "keep")
        self.assertEqual(rec["recommended"], 100)

    def test_selling_at_floor_ahead_of_market_raises(self):
        rows = (_rows(20, "confirmed_paid", True, occ=40.0, p25=150.0)
                + _rows(10, "open", False, occ=40.0, p25=150.0, start=20))
        rec = min_price_recommendation(self.bounds, rows, 1.2, 0.15)
        self.assertEqual(rec["action"], "raise")
        self.assertEqual(rec["recommended"], 115)
        self.assertTrue(rec["large_move"])

    def test_brief_carries_the_min_price_line(self):
        result = compute(synthetic_bundle())
        self.assertEqual(result["min_price"]["recommended"], 80)
        self.assertIn("Recommended min price:", render(result, "run", METRICS))

    def test_a_reason_that_ends_in_a_period_is_not_doubled_on_the_card(self):
        result = compute(synthetic_bundle())
        result["min_price"]["reason"] = "Look at visibility and the listing before the floor."
        card = render(result, "run", METRICS)
        self.assertIn("before the floor.", card)
        self.assertNotIn("floor..", card)
        self.assertNotIn(". .", card)

    def test_runner_proposes_raises_on_underpriced_nights(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["market"]["data"][40].update(p25=150, p50=160, p75=170, p90=180)
        result = compute(bundle)
        raise_rows = [c for c in result["candidates"] if c["direction"] == "raise"]
        self.assertEqual([c["date"] for c in raise_rows], [result["daily"][40]["date"]])
        lower, upper = raise_rows[0]["review_net_range"]
        self.assertGreater(lower, 120)
        self.assertLessEqual(upper, 120 * 1.15)
        self.assertGreaterEqual(lower, result["bounds"]["min"])


# 4 ---------------------------------------------------------------- rule grading
def _daily(n=84, booked=lambda i, d: i % 2 == 0, mkt=50.0, start=date(2031, 6, 2)):
    out = []
    for i in range(n):
        d = start + timedelta(days=i)
        out.append({"date": d.isoformat(), "days_out": i, "dow": d.weekday(),
                    "booked": booked(i, d), "blocked": False,
                    "market_occ": mkt(i, d) if callable(mkt) else mkt})
    return out


class RuleGrading(unittest.TestCase):
    def test_within_five_points_is_neutral(self):
        rules = {"last_minute_prices": {"last_min_factor_on": True, "last_min_factor_type": "linear",
                                        "last_min_factor_value": -10, "last_min_factor_dfd": 13}}
        # inside and outside both book 50% against a flat 50% market: a 0.0 pt delta. The old
        # grader called anything not 5 pts worse "working".
        rows = _daily()
        (e,) = attribution.rule_effectiveness(rules, rows)
        self.assertEqual(e["verdict"], "neutral", e)

    def test_seasonality_graded_month_by_month_against_market(self):
        rules = {"seasonality": {"seasonality_customization_on": True}}

        def booked(i, d):
            return {6: i % 5 != 0, 7: i % 2 == 0, 8: i % 5 == 0}.get(d.month, True)

        rows = _daily(n=95, booked=booked, start=date(2031, 6, 1))
        (e,) = attribution.rule_effectiveness(rules, rows)
        months = {m["month"]: m["verdict"] for m in e["months"]}
        self.assertEqual(months["2031-06"], "working")
        self.assertEqual(months["2031-07"], "neutral")
        self.assertEqual(months["2031-08"], "underperforming")
        self.assertEqual(months["2031-09"], "not-enough-data")

    def test_day_of_week_discount_and_premium_days_graded_separately(self):
        rules = {"day_of_week_adjustment": {"dow_factor_on": True, "dow_factor_value_mon": -10,
                                            "dow_factor_value_sat": 20}}

        def booked(i, d):
            return True if d.weekday() == 0 else False if d.weekday() == 5 else i % 2 == 0

        (e,) = attribution.rule_effectiveness(rules, _daily(booked=booked))
        self.assertEqual(e["discount_days"]["verdict"], "working", e)
        self.assertEqual(e["premium_days"]["verdict"], "underperforming", e)


# 5 ---------------------------------------------------------------- reducer dates
class ReducerDates(unittest.TestCase):
    def test_local_today_follows_the_property_timezone(self):
        now = datetime(2031, 6, 10, 5, tzinfo=timezone.utc)
        self.assertEqual(local_today("America/Vancouver", now), date(2031, 6, 9))
        self.assertEqual(local_today("+05:30", now), date(2031, 6, 10))
        self.assertEqual(local_today("-08:00", now), date(2031, 6, 9))
        with self.assertRaises(ValueError):
            local_today("Not/AZone", now)

    def test_every_reducer_accepts_tz(self):
        for name in ("reduce_prices", "reduce_neighborhood", "reduce_overrides",
                     "reduce_reservations", "reduce_customizations"):
            with self.subTest(name=name):
                out = subprocess.run([sys.executable, str(HERE / f"{name}.py"), "--help"],
                                     capture_output=True, text=True, timeout=30)
                self.assertIn("--tz", out.stdout)

    def test_pacing_includes_tonight(self):
        rows = [{"date": (START + timedelta(days=i)).isoformat(),
                 "booking_status": "Booked" if i == 0 else "", "booking_status_STLY": ""}
                for i in range(30)]
        self.assertIn("next30 occ=3.3%", reduce_prices.pacing_line(rows, START.isoformat()))


class ReducePricesFindsTheKeyWhenInstalled(unittest.TestCase):
    """parents[4] of an INSTALLED plugin is ~/.claude/plugins/cache/..., not the bundle.
    The key must be found via SKILL_PATH_REVENUE_MANAGER or the connections kit instead."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_installed_copy_ignores_cache_parent_and_uses_skill_path(self):
        from unittest.mock import patch
        installed = self.root / "cache/mkt/revenue-manager/1.0/skills/revenue-manager/fetch/x.py"
        installed.parent.mkdir(parents=True)
        (self.root / "cache/mkt/mcp-servers/pricelabs").mkdir(parents=True)
        (self.root / "cache/mkt/mcp-servers/pricelabs/.env").write_text("PRICELABS_API_KEY=wrong\n")
        bundle = self.root / "bundle"
        (bundle / "mcp-servers/pricelabs").mkdir(parents=True)
        # Notepad writes a BOM; it must not glue itself to the key name.
        (bundle / "mcp-servers/pricelabs/.env").write_text("\ufeffPRICELABS_API_KEY=right\n",
                                                          encoding="utf-8")
        with patch.dict("os.environ", {"SKILL_PATH_REVENUE_MANAGER": str(bundle)}, clear=False):
            import os
            os.environ.pop("PRICELABS_API_KEY", None)
            found = reduce_prices.env_candidates(None, here=installed, servers={})
            self.assertNotIn(self.root / "cache/mkt/mcp-servers/pricelabs/.env", found)
            self.assertEqual(reduce_prices.key_from(found), "right")

    def test_kit_env_names_the_bundle(self):
        kit = self.root / "kit"
        (kit / "servers/pricelabs").mkdir(parents=True)
        (kit / "fan-out-env.sh").write_text("")
        bundle = self.root / "bundle2"
        bundle.mkdir()
        (kit / ".env").write_text(f"SKILL_PATH_REVENUE_MANAGER={bundle}\n")
        (bundle / ".env").write_text("export PRICELABS_API_KEY='kitkey'\n")
        servers = {"pricelabs": {"command": str(kit / "servers/pricelabs/run.sh")}}
        found = reduce_prices.env_candidates(None, here=self.root / "a/b/c/d/e/f.py",
                                             servers=servers)
        self.assertEqual(reduce_prices.key_from(found), "kitkey")


# 6 ---------------------------------------------------------------- thin comps
class ThinComps(unittest.TestCase):
    def test_under_twenty_comps_says_rough_guide(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["market"]["listings_used"] = 12
        brief = render(compute(bundle), "run", METRICS)
        self.assertIn("based on only 12 comps, treat as a rough guide", brief)

    def test_twenty_or_more_says_nothing(self):
        brief = render(compute(synthetic_bundle()), "run", METRICS)
        self.assertNotIn("rough guide", brief)

    def test_unknown_comp_count_prints_unknown(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["market"]["listings_used"] = None
        brief = render(compute(bundle), "run", METRICS)
        self.assertIn("market comps: unknown", brief)
        self.assertNotIn("comps: None", brief)
        self.assertNotIn("None", brief)


# 7 ---------------------------------------------------------------- saved max delta
class SavedMaxDelta(unittest.TestCase):
    def test_accepts_percent_or_fraction(self):
        for saved in (10, 0.10, "10"):
            bundle = synthetic_bundle()
            bundle["inputs"]["context"]["settings"]["max_delta_pct"] = saved
            result = compute(bundle)
            with self.subTest(saved=saved):
                self.assertEqual(result["movement_scrutiny_pct"], 10)
                for row in result["candidates"]:
                    self.assertGreaterEqual(row["review_net_range"][0], row["net"] * 0.90)

    def test_unset_defaults_to_fifteen(self):
        bundle = synthetic_bundle()
        del bundle["inputs"]["context"]["settings"]["max_delta_pct"]
        self.assertEqual(compute(bundle)["movement_scrutiny_pct"], 15)


# 8 ---------------------------------------------------------------- days out after rollover
class DaysOutFromLocalToday(unittest.TestCase):
    def test_build_counts_days_out_from_property_local_today(self):
        result = direct_build_today(synthetic_bundle(), START - timedelta(days=1))
        self.assertEqual(result["daily"][0]["days_out"], 1)
        self.assertEqual(result["daily"][13]["days_out"], 14)

    def test_compute_derives_local_today_after_rollover(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["property"]["timezone"] = "-13:00"  # 12:00Z is 23:00 the day before
        result = compute(bundle)
        self.assertEqual(result["daily"][0]["days_out"], 1)


def direct_build_today(bundle, today):
    s = bundle["inputs"]
    pms = analyze(s["property"], s["calendar"], s["reservations"]["data"], s["reviews"]["data"],
                  START, 90, AS_OF)
    return build(pms, s["listing"], s["prices"], s["market"], s["overrides"], s["rules"],
                 s["funnel"], s["rankings"], s["context"], AS_OF, today=today)



class ArrivalRulesNotExposed(unittest.TestCase):
    """A PMS that sends the arrival/departure flags on NO night is a named gap, not an unknown
    calendar; one that sends them on some nights and drops them on others is still refused."""

    def _facts(self, flag_for):
        from datetime import date, datetime, timezone
        from _mvp_pms import analyze
        from _pms_lodgify import day_row, property_row, reservation_row
        from test_pms_lodgify import BOOK, PROP, ROOM, item
        days = {f"2026-10-{d:02d}": ("RESERVED" if d in (5, 6, 7) else "AVAILABLE") for d in range(4, 14)}
        cal = []
        for i, (k, v) in enumerate(days.items()):
            row = day_row(item(k), v, "USD")
            row["closed_for_checkin"] = row["closed_for_checkout"] = flag_for(i)
            cal.append(row)
        return analyze(property_row(PROP, ROOM), cal, [reservation_row(BOOK)], [], date(2026, 10, 4), 10,
                       datetime(2026, 10, 1, tzinfo=timezone.utc))

    def test_no_night_exposes_the_flags_is_a_named_gap(self):
        facts = self._facts(lambda i: None)
        codes = {w["code"] for w in facts["warnings"]}
        self.assertIn("pms_does_not_expose_arrival_rules", codes)
        self.assertTrue(facts["coverage"]["analysable"])

    def test_flags_on_some_nights_only_are_still_refused_per_night(self):
        facts = self._facts(lambda i: False if i % 2 == 0 else None)
        codes = {w["code"] for w in facts["warnings"]}
        self.assertNotIn("pms_does_not_expose_arrival_rules", codes)
        self.assertTrue(facts["coverage"]["pms_arrival_rules_exposed"])
        self.assertFalse(facts["coverage"]["analysable"])

    def test_smoobu_no_longer_assumes_false(self):
        import _pms_smoobu
        self.assertIsNone(_pms_smoobu.NO_ARRIVAL_RULES_EXPOSED)


class HospitableMinorUnits(unittest.TestCase):
    """Hospitable money is in the currency's own minor unit; the engine's *_cents are hundredths.
    Non-two-decimal currencies are refused on that path instead of showing 100x or 10x off."""

    def _raw_day(self, currency, amount):
        return {"date": "2026-10-04", "price": {"amount": amount, "currency": currency},
                "status": {"available": True, "reason": "AVAILABLE"}, "min_stay": 2}

    def test_two_decimal_currencies_read_unchanged(self):
        from _mvp_pms import normalize_calendar
        for cur in ("USD", "CAD", "EUR", "GBP", "AUD"):
            with self.subTest(cur=cur):
                rows = normalize_calendar([self._raw_day(cur, 15025)])
                self.assertEqual(rows[0]["price_cents"], 15025)

    def test_jpy_and_kwd_are_refused_with_a_plain_reason(self):
        from _mvp_pms import normalize_calendar
        for cur in ("JPY", "KWD"):
            with self.subTest(cur=cur), self.assertRaisesRegex(ValueError, f"{cur} has . decimal places"):
                normalize_calendar([self._raw_day(cur, 15000)])

    def test_adapter_rows_already_in_hundredths_are_untouched(self):
        from _mvp_pms import normalize_calendar
        row = {"date": "2026-10-04", "price_cents": 1500000, "currency": "JPY", "min_stay": 2,
               "available": True, "status_reason": "AVAILABLE"}
        self.assertEqual(normalize_calendar([row])[0]["price_cents"], 1500000)


# live 2026-09-25 --------------------------------------------------- funnel break on the card
class FunnelBreakIsPrinted(unittest.TestCase):
    def test_break_is_not_printed_as_ok_and_headline_is_shown(self):
        bundle = synthetic_bundle()
        comp = bundle["inputs"]["funnel"]["visibility_row"]["similar_listings_comparison"]
        comp["booking_rate"] = {"listing": 0.99, "similar_listings": 34.19}
        brief = render(compute(bundle), "run", METRICS)
        self.assertIn("visibility: BREAK, booking_rate is 0.99 against a comp-set 34.19", brief)
        self.assertIn("diagnosis: funnel breaks at booking_rate", brief)
        self.assertNotIn("visibility: ok", brief)

    def test_healthy_funnel_still_prints_ok_with_its_headline(self):
        brief = render(compute(synthetic_bundle()), "run", METRICS)
        self.assertIn("visibility: ok, every funnel stage is at or above its comp set", brief)
        self.assertIn("diagnosis: funnel healthy against its comp set", brief)


# live 2026-09-25 ------------------------------------- one occupancy on the card, no vanishing nights
class OneOccupancyOnTheCard(unittest.TestCase):
    bounds = {"min": 100.0, "base": 150.0, "max": 300.0}

    def test_zero_value_nights_are_not_demand_in_the_min_price_pace(self):
        # The Apres Arcade, live: 26 open nights pinned at the min + 4 $0 nights, market 13%.
        # The card said "13% booked vs the market's 13%" under a table saying 0%.
        rows = (_rows(26, "open", True, occ=13.0, p25=60.0)
                + _rows(4, "zero_value_accepted", True, occ=13.0, p25=60.0, start=26))
        rec = min_price_recommendation(self.bounds, rows, 1.2, 0.15)
        self.assertIn("0% booked vs the market's 13%", rec["reason"])
        self.assertEqual(rec["action"], "lower")

    def test_table_shows_zero_value_column_and_every_night_is_counted(self):
        brief = render(compute(synthetic_bundle()), "run", METRICS)
        header, first = brief.split("days |", 1)[1].splitlines()[:2]
        self.assertIn("$0 stay", header)
        cells = [c.strip() for c in first.split("|")]
        days, counted = int(cells[0]), sum(int(c) for c in cells[1:6])
        self.assertEqual(days, counted, first)

    def test_windows_with_no_open_nights_never_print_none(self):
        # Azure Palms, live: 60 fully booked nights printed "None | None/None" and
        # "open Airbnb None vs matched p90 None".
        pack = compute(synthetic_bundle())
        for key in ("open_airbnb_mean", "matched_open_p50", "matched_open_p90", "occupancy_pct"):
            pack["windows"][0][key] = None
        pack["months"][0].update(open=0, open_airbnb_mean=None, matched_open_p90=None,
                                 occupancy_pct=None)
        brief = render(pack, "run", METRICS)
        self.assertNotIn("None", brief)
        self.assertIn("no open nights to price", brief)


# live 2026-09-25 ------------------------------------------ a PriceLabs push not yet made is not a broken sync
class PendingPriceLabsPush(unittest.TestCase):
    def test_pms_matching_the_last_pushed_price_does_not_block_the_card(self):
        # Azure Palms, live: PMS == user_price to the cent on 21 of 27 open nights, `price`
        # freshly recalculated 1-3% away. The whole card blocked as "sync broken".
        bundle = synthetic_bundle()
        for i in range(10, 40):
            bundle["inputs"]["prices"]["data"][i].update(price=121, user_price=120)
        result = compute(bundle)
        self.assertNotEqual(result["status"], "blocked", result.get("blockers"))
        self.assertEqual(result["reconciliation"]["mismatches"], [])
        self.assertEqual(len(result["reconciliation"]["pending_push"]), 30)
        withheld = [r for r in result["daily"] if r.get("withheld_reason")]
        self.assertEqual(len(withheld), 30)
        self.assertTrue(all("not in the PMS" in r["withheld_reason"] for r in withheld))
        brief = render(result, "run", METRICS)
        self.assertIn("PRICELABS UPDATE NOT IN THE PMS on 30 date(s)", brief.splitlines()[1])
        self.assertNotIn("sync is working", brief)  # not measured, never claimed

    def test_pms_matching_neither_price_still_blocks(self):
        bundle = synthetic_bundle()
        for i in range(10, 40):
            bundle["inputs"]["prices"]["data"][i].update(price=121, user_price=125)
        self.assertEqual(compute(bundle)["status"], "blocked")




# live 2026-09-25 ------------------------------ a booking PriceLabs has not seen yet is not a broken sync
class BookingAfterPriceLabsRefresh(unittest.TestCase):
    def bundle(self, booked_minutes_ago):
        from datetime import timedelta as td
        bundle = synthetic_bundle()
        for i in (0, 1):  # the paid stay: PriceLabs does not show it yet
            bundle["inputs"]["prices"]["data"][i]["booking_status"] = ""
        stamp = (AS_OF - td(minutes=booked_minutes_ago)).isoformat()
        for r in bundle["inputs"]["reservations"]["data"]:
            if r["id"] == "fixture-booked":
                r["booking_date"] = stamp
                r["reservation_status"]["history"][0]["changed_at"] = stamp
        return bundle

    def test_booked_after_the_refresh_is_named_not_a_mismatch(self):
        # Boho Bliss, live: booked 03:06Z, PriceLabs refreshed 07:48Z the day before; the
        # whole card blocked as "the sync itself is broken".
        result = compute(self.bundle(booked_minutes_ago=10))  # PriceLabs refreshed 60 min ago
        rec = result["reconciliation"]
        self.assertEqual(rec["mismatches"], [])
        self.assertEqual(rec["booked_after_pricelabs_refresh"],
                         [START.isoformat(), (START + timedelta(days=1)).isoformat()])
        self.assertIn("NEW BOOKING NOT IN PRICELABS YET: 2 night(s)", render(result, "run", METRICS))

    def test_booked_before_the_refresh_is_still_a_mismatch(self):
        rec = compute(self.bundle(booked_minutes_ago=24 * 60))["reconciliation"]
        self.assertEqual([m["date"] for m in rec["mismatches"] if m["paid_booking_missing"]],
                         [START.isoformat(), (START + timedelta(days=1)).isoformat()])
        self.assertEqual(rec["booked_after_pricelabs_refresh"], [])

# Ryan 2026-09-25: PriceLabs recalculates daily; a card must not block at 24.5 hours.
class PriceLabsFreshnessWindow(unittest.TestCase):
    def card(self, hours):
        bundle = synthetic_bundle()
        bundle["inputs"]["prices"]["last_refreshed_at"] = (AS_OF - timedelta(hours=hours)).isoformat()
        result = compute(bundle)
        return result, render(result, "run", METRICS)

    def test_up_to_24_hours_is_clean(self):
        result, card = self.card(23.9)
        self.assertNotEqual(result["status"], "blocked")
        self.assertNotIn("STALE PRICELABS DATA", card)

    def test_24_to_48_hours_prices_with_a_warning_at_the_top(self):
        result, card = self.card(30)
        self.assertNotEqual(result["status"], "blocked")
        self.assertTrue(result["candidates"] or result["status"] == "analysable")
        head = "\n".join(card.splitlines()[:4])
        self.assertIn("STALE PRICELABS DATA: PriceLabs last recalculated 30 hours ago", head)
        self.assertIn("Sync Now", head)

    def test_past_48_hours_blocks(self):
        result, _ = self.card(49)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("more than 48 hours" in b for b in result["blockers"]), result["blockers"])

    def test_future_dated_still_blocks(self):
        result, _ = self.card(-2)
        self.assertEqual(result["status"], "blocked")

# Live 2026-09-25 (The Apres Arcade): raise DSOs suggested on nights under comp p25 while the
# listing booked 0% vs the market's 13%. Cheap and still not booking is not a price problem; the
# same booking guard the rule layer uses now holds for DSO suggestions, per 30-night block.
class DsoBookingGuard(unittest.TestCase):
    def rows(self, booked, market, n=30):
        out = []
        for i in range(n):
            out.append({"date": f"d{i}", "status": "confirmed_paid" if i < booked else "open",
                        "market_occ": market})
        return out

    def test_raise_withheld_when_the_block_books_under_the_market(self):
        from _mvp_analysis import dso_booking_guard
        rows = self.rows(booked=0, market=13.0)
        why = dso_booking_guard(rows, 5, "raise")
        self.assertIsNotNone(why)
        self.assertIn("0% booked vs the market's 13%", why)
        self.assertIn("not the price", why)

    def test_raise_allowed_when_the_block_books_with_the_market(self):
        from _mvp_analysis import dso_booking_guard
        self.assertIsNone(dso_booking_guard(self.rows(booked=4, market=13.0), 5, "raise"))

    def test_cut_withheld_when_the_block_is_selling_ahead(self):
        from _mvp_analysis import dso_booking_guard
        why = dso_booking_guard(self.rows(booked=20, market=40.0), 25, "cut")
        self.assertIsNotNone(why)
        self.assertIn("selling", why)

    def test_no_market_yardstick_means_no_guard(self):
        from _mvp_analysis import dso_booking_guard
        self.assertIsNone(dso_booking_guard(self.rows(booked=0, market=None), 5, "raise"))

    def test_blocked_nights_are_not_counted_as_unbooked(self):
        from _mvp_analysis import dso_booking_guard
        rows = self.rows(booked=3, market=13.0)
        for r in rows[3:25]:
            r["status"] = "blocked"
        self.assertIsNone(dso_booking_guard(rows, 26, "raise"))  # 3 of 8 bookable = 37.5%


if __name__ == "__main__":
    unittest.main()
