"""Rules first, then DSOs (Ryan 2026-09-25): the runner reads the whole rule stack, attributes
every review night to the layers on it, proposes ONE rule change when a rule explains a pattern,
folds those nights into it, and leaves only the residual nights as DSO suggestions.

Offline: fake reads and synthetic rows, no network.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules_first as rf  # noqa: E402
from _mvp_analysis import render  # noqa: E402
from _mvp_store import CannotAnalyze  # noqa: E402
from test_mvp_analysis import METRICS, START, compute, synthetic_bundle  # noqa: E402

TODAY = date(2031, 6, 10)
LM = {"last_min_factor_on": True, "last_min_factor_type": "linear", "last_min_factor_value": -10.0,
      "last_min_factor_dfd": 10}
DOW = {"dow_factor_on": True, "dow_factor_value_mon": -8.0, "dow_factor_value_tue": -8.0,
       "dow_factor_value_wed": -8.0, "dow_factor_value_thu": 0.0, "dow_factor_value_fri": 5.0,
       "dow_factor_value_sat": 5.0, "dow_factor_value_sun": 0.0}
OFF = {"seasonality": {"seasonality_customization_on": False, "seasonality_type": None},
       "far_out_premium": {"far_out_premium_on": False, "far_out_premium_type": "linear",
                           "far_out_premium_value": 0.0, "far_out_premium_start": 999,
                           "far_out_premium_step": 1},
       "demand_factor": {"tone_demand_factor_on": False, "tone_demand_factor": None},
       "custom_seasonal_profile": {"custom_seasonal_profile_on": False, "custom_seasonal_profile": None},
       "day_of_week_adjustment": dict(DOW, dow_factor_on=False),
       "last_minute_prices": dict(LM, last_min_factor_on=False)}


def row(offset, *, status="open", airbnb=144.0, p25=75.0, p50=85.0, p75=95.0, layer="customization_stack",
        override=None, at_floor=False):
    d = TODAY + timedelta(days=offset)
    return {"date": d.isoformat(), "days_out": offset, "status": status, "net": airbnb / 1.2,
            "airbnb": airbnb, "p25": p25, "p50": p50, "p75": p75, "p90": 110.0, "layer": layer,
            "override": override, "at_floor": at_floor, "action": "monitor"}


def review(rows):
    """The runner's review rule: cut above p75 inside 14 days, raise under p25."""
    out = []
    for r in rows:
        if r["status"] != "open" or r["layer"] == "fixed_override":
            direction = "cut" if (r["status"] == "open" and r["days_out"] < 14 and r["airbnb"] > r["p75"]) else None
        elif r["days_out"] < 14 and r["airbnb"] > r["p75"]:
            direction = "cut"
        elif r["airbnb"] < r["p25"]:
            direction = "raise"
        else:
            direction = None
        if direction and r["status"] == "open":
            r["action"] = "review_price"
            out.append(dict(r, direction=direction, review_net_range=[1, 2]))
    return out


def run(rows, rules, levels=None, effect=None, max_delta=0.15, overrides=None, bounds=None):
    return rf.recommend(rows, review(rows), rules, levels or {}, effect or [],
                        bounds or {"min": 80.0, "base": 120.0, "max": 200.0}, max_delta,
                        overrides or [], TODAY)


class Stack(unittest.TestCase):
    def test_listing_rule_that_is_on_wins(self):
        eff, lv = rf.resolve_stack({"last_minute_prices": LM}, {"last_minute_prices": dict(LM, last_min_factor_value=-30)})
        self.assertEqual(lv["last_minute_prices"], "listing")
        self.assertEqual(eff["last_minute_prices"]["last_min_factor_value"], -10.0)

    def test_an_off_listing_rule_inherits_the_group_then_the_account(self):
        off = {"last_minute_prices": dict(LM, last_min_factor_on=False)}
        eff, lv = rf.resolve_stack(off, {"last_minute_prices": dict(LM, last_min_factor_value=-30)},
                                   {"last_minute_prices": dict(LM, last_min_factor_value=-40)})
        self.assertEqual((lv["last_minute_prices"], eff["last_minute_prices"]["last_min_factor_value"]),
                         ("group", -30))
        eff, lv = rf.resolve_stack(off, {}, {"last_minute_prices": dict(LM, last_min_factor_value=-40)})
        self.assertEqual(lv["last_minute_prices"], "account")

    def test_set_nowhere_is_the_pricelabs_default(self):
        eff, lv = rf.resolve_stack({}, None, {})
        self.assertEqual(lv["far_out_premium"], "default")
        self.assertEqual(eff["far_out_premium"], {})

    def test_the_card_stack_says_off_is_not_none(self):
        rows = rf.stack_rows(*rf.resolve_stack(OFF))
        self.assertTrue(any("market-driven default" in r["setting"] for r in rows))


class FakeClient:
    """Answers Sources.pl_get by path; records every read."""

    def __init__(self, answers):
        self.answers = answers
        self.reads = []

    def fetch(self, source, ident, loader, **_):
        return loader()


class SourcesReadTheWholeStack(unittest.TestCase):
    def sources(self, answers, group_id=None):
        from _mvp_sources import Sources
        src = Sources.__new__(Sources)
        src.client = FakeClient(answers)
        src.connections = type("C", (), {"account": staticmethod(lambda n: "acct"),
                                         "key": staticmethod(lambda n: "k")})()
        src.reads = []

        def pl_get(path, params=None, body=None):
            src.reads.append((path, dict(params or {})))
            got = answers.get(path)
            if isinstance(got, Exception):
                raise got
            return copy.deepcopy(got)
        src.pl_get = pl_get
        src.listing = lambda lid, pms: {"group_id": group_id, "subgroup_id": None}
        return src

    def test_listing_group_and_account_are_all_read_with_toggled_on_false(self):
        src = self.sources({"/v1/customizations/listing": {"customizations": OFF},
                            "/v1/customizations/group": {"customizations": {"last_minute_prices": dict(LM, last_min_factor_value=-25)}},
                            "/v1/customizations/account": {"customizations": {}}}, group_id=7)
        out = src.rules("L1", "smartbnb")
        paths = [p for p, _ in src.reads]
        self.assertEqual(paths, ["/v1/customizations/listing", "/v1/customizations/group",
                                 "/v1/customizations/account"])
        self.assertTrue(all(q.get("toggled_on") == "false" for _, q in src.reads))
        self.assertEqual(out["levels"]["last_minute_prices"], "group")
        self.assertEqual(out["raw"]["last_minute_prices"]["last_min_factor_value"], -25)
        self.assertEqual(out["gaps"], [])

    def test_no_group_means_no_group_read(self):
        src = self.sources({"/v1/customizations/listing": {"customizations": OFF},
                            "/v1/customizations/account": {"customizations": {}}})
        src.rules("L1", "smartbnb")
        self.assertNotIn("/v1/customizations/group", [p for p, _ in src.reads])

    def test_unreadable_group_or_account_is_a_named_gap_not_a_skip(self):
        src = self.sources({"/v1/customizations/listing": {"customizations": OFF},
                            "/v1/customizations/group": CannotAnalyze("pricelabs group: HTTP 403"),
                            "/v1/customizations/account": {"error": "nope"}}, group_id=7)
        out = src.rules("L1", "smartbnb")
        self.assertTrue(any(g.startswith("GROUP RULES UNREADABLE") for g in out["gaps"]), out["gaps"])
        self.assertTrue(any(g.startswith("ACCOUNT RULES UNREADABLE") for g in out["gaps"]), out["gaps"])

    def test_unreadable_listing_rules_still_block(self):
        src = self.sources({"/v1/customizations/listing": {"message": "nope"},
                            "/v1/customizations/account": {"customizations": {}}})
        with self.assertRaises(CannotAnalyze):
            src.rules("L1", "smartbnb")

    def test_a_listing_with_no_own_rules_inherits_the_account(self):
        src = self.sources({"/v1/customizations/listing": {"customizations": {}},
                            "/v1/customizations/account": {"customizations": {"day_of_week_adjustment": DOW}}})
        out = src.rules("L1", "smartbnb")
        self.assertEqual(out["levels"]["day_of_week_adjustment"], "account")
        self.assertEqual(out["levels"]["last_minute_prices"], "default")


def lm_rows():
    """Nights 0-9 inside a 10-day last-minute window priced above p75; 10-13 in band."""
    rows = [row(i) for i in range(10)] + [row(i, p75=150.0) for i in range(10, 14)]
    return rows + [row(i, p75=150.0) for i in range(14, 30)]


class RulesFirst(unittest.TestCase):
    def rules(self, **over):
        rules = dict(OFF)
        rules.update(over)
        return rules

    def test_a_rule_that_explains_the_pattern_gets_one_change_and_folds_the_nights(self):
        out = run(lm_rows(), self.rules(last_minute_prices=LM))
        (ch,) = out["rule_changes"]
        self.assertEqual(ch["rule"], "last_minute_prices")
        self.assertEqual(ch["direction"], "cut")
        self.assertEqual(len(ch["folded"]), 10)
        self.assertEqual(out["dso_dates"], [])  # no per-date DSO for a folded night
        self.assertTrue(ch["writable"])
        self.assertEqual(set(ch["change"]), {"last_minute_prices"})

    def test_the_change_is_sized_from_the_gap_and_capped(self):
        out = run(lm_rows(), self.rules(last_minute_prices=LM))
        (ch,) = out["rule_changes"]
        # needed 95/144 - 1 = -34.0%, capped at 15%: ((1 - .10)(1 - .15) - 1) = -23.5 -> -24
        self.assertEqual(ch["change"]["last_minute_prices"], {"last_min_factor_value": -24})
        self.assertTrue(ch["capped"])
        self.assertIn("LARGE MOVE", ch["why"])
        self.assertEqual(ch["needed_pct"], -34.0)

    def test_a_smaller_cap_makes_a_smaller_change(self):
        out = run(lm_rows(), self.rules(last_minute_prices=LM), max_delta=0.05)
        self.assertEqual(out["rule_changes"][0]["change"]["last_minute_prices"]["last_min_factor_value"], -15)

    def test_too_few_nights_is_not_a_pattern(self):
        rows = [row(i) for i in range(2)] + [row(i, p75=150.0) for i in range(2, 30)]
        out = run(rows, self.rules(last_minute_prices=LM))
        self.assertEqual(out["rule_changes"], [])
        self.assertEqual(len(out["dso_dates"]), 2)
        self.assertIn("needs at least 3 nights", out["why_dso"][out["dso_dates"][0]])

    def test_no_contrast_inside_vs_outside_means_the_rule_is_not_singled_out(self):
        rows = [row(i) for i in range(14)] + [row(i, p75=150.0) for i in range(14, 30)]
        out = run(rows, self.rules(last_minute_prices=LM))  # nights 10-13 outside want the cut too
        self.assertEqual(out["rule_changes"], [])
        self.assertIn("20 points more inside than outside", " ".join(out["why_dso"].values()))

    def test_day_of_week_raise_changes_only_the_discount_days(self):
        rows = []
        for i in range(28):
            wd = (TODAY + timedelta(days=i)).weekday()
            rows.append(row(i, airbnb=70.0 if wd in (0, 1, 2) else 90.0, p75=150.0, at_floor=wd in (0, 1, 2)))
        out = run(rows, self.rules(day_of_week_adjustment=DOW))
        (ch,) = out["rule_changes"]
        self.assertEqual(ch["direction"], "raise")
        self.assertEqual(set(ch["change"]["day_of_week_adjustment"]),
                         {"dow_factor_value_mon", "dow_factor_value_tue", "dow_factor_value_wed"})
        self.assertIn("sit at your min", ch["why"])  # the floor can still hold them
        self.assertEqual(ch["full_after"]["dow_factor_value_fri"], 5.0)  # all seven days carried

    def test_a_discount_is_never_resized_into_a_premium(self):
        rows = [row(i, airbnb=40.0, p75=150.0) if (TODAY + timedelta(days=i)).weekday() in (0, 1, 2)
                else row(i, airbnb=90.0, p75=150.0) for i in range(28)]
        out = run(rows, self.rules(day_of_week_adjustment=DOW))
        after = out["rule_changes"][0]["change"]["day_of_week_adjustment"]
        self.assertTrue(all(v == 0 for v in after.values()), after)
        self.assertIn("never flips", out["rule_changes"][0]["why"])

    def test_a_market_driven_rule_is_a_layer_never_a_proposal(self):
        md = dict(LM, last_min_factor_type="recommended", last_min_factor_value=None, last_min_factor_dfd=None)
        out = run(lm_rows(), self.rules(last_minute_prices=md))
        self.assertEqual(out["rule_changes"], [])
        self.assertIn("last-minute (market-driven recommended)", out["layers"][out["dso_dates"][0]])

    def test_a_group_level_rule_is_shown_but_not_writable(self):
        out = run(lm_rows(), self.rules(last_minute_prices=LM), levels={"last_minute_prices": "group"})
        (ch,) = out["rule_changes"]
        self.assertFalse(ch["writable"])
        self.assertEqual(ch["refusal"], "this changes every listing in the group; change it in PriceLabs")

    def test_graded_underperforming_proposes_a_cut_only_when_price_is_the_lever(self):
        rows = [row(i, airbnb=120.0, p75=150.0) for i in range(30)]  # above p50, in the p75 band
        grade = {"rule": "last_minute_prices", "verdict": "underperforming", "why": "trails by 9 pts",
                 "gap_to_market_inside": -12.0}
        out = run(rows, self.rules(last_minute_prices=LM), effect=[grade])
        (ch,) = out["rule_changes"]
        self.assertEqual((ch["trigger"], ch["direction"]), ("graded underperforming", "cut"))
        out = run(rows, self.rules(last_minute_prices=LM), effect=[dict(grade, gap_to_market_inside=4.0)])
        self.assertEqual(out["rule_changes"], [])
        self.assertTrue(any("still books +4.0 pts" in n for n in out["notes"]), out["notes"])

    def test_graded_cut_against_a_raise_pattern_proposes_nothing(self):
        # the window's nights sit under p25 (a raise), the rest in band
        rows = [row(i, airbnb=60.0 if i <= 10 else 90.0, p75=150.0) for i in range(30)]
        grade = {"rule": "last_minute_prices", "verdict": "underperforming", "why": "trails",
                 "gap_to_market_inside": -12.0}
        out = run(rows, self.rules(last_minute_prices=LM), effect=[grade])
        self.assertEqual(out["rule_changes"], [])
        self.assertTrue(any("evidence disagrees" in n for n in out["notes"]))

    def test_one_lever_per_diagnosis(self):
        # the same near-term nights sit inside the last-minute window AND on discount days
        rows = [row(i) if i < 10 else row(i, p75=150.0) for i in range(30)]
        dow_all = dict(DOW, **{k: -8.0 for k in DOW if k.startswith("dow_factor_value_")})
        out = run(rows, self.rules(last_minute_prices=LM, day_of_week_adjustment=dow_all))
        self.assertEqual(len(out["rule_changes"]), 1)
        folded = out["rule_changes"][0]["folded"]
        self.assertEqual(len(folded), len(set(folded)))

    def test_a_fixed_dso_night_is_never_folded_the_dso_is_the_lever(self):
        rows = lm_rows()
        rows[3] = row(3, layer="fixed_override", override={"price": "140", "price_type": "fixed"})
        cands = review(rows)
        cands.append(dict(rows[3], direction="cut", review_net_range=[1, 2]))
        out = rf.recommend(rows, cands, self.rules(last_minute_prices=LM), {}, [],
                           {"min": 80.0}, 0.15, [rows[3]["override"] | {"date": rows[3]["date"]}], TODAY)
        self.assertNotIn(rows[3]["date"], out["folded"])
        self.assertIn("the DSO is the lever", out["why_dso"][rows[3]["date"]])
        flagged = {e["date"]: e["flags"] for e in out["existing_dsos"]}
        self.assertTrue(any("blocks the proposed last-minute change" in f for f in flagged[rows[3]["date"]]))

    def test_a_change_that_fails_pricelabs_ranges_is_dropped_with_a_note(self):
        deep = dict(LM, last_min_factor_value=-74.0)
        out = run(lm_rows(), self.rules(last_minute_prices=deep))
        self.assertEqual(out["rule_changes"], [])
        self.assertTrue(any("fails PriceLabs' ranges" in n for n in out["notes"]), out["notes"])


class ExistingDsos(unittest.TestCase):
    def test_flags_below_min_stale_past_and_fighting(self):
        rows = [row(i) for i in range(10)]
        overrides = [
            {"date": rows[1]["date"], "price": "70", "price_type": "fixed", "updated_at": "2031-06-01"},
            {"date": rows[2]["date"], "min_price": 60.0, "min_stay": 2},
            {"date": rows[3]["date"], "price": "10", "price_type": "percent", "updated_at": "2031-01-01"},
            {"date": "2031-06-01", "price": "5", "price_type": "percent"},
        ]
        out = rf.existing_dsos(overrides, rows, {"last_minute_prices": LM}, {}, {"min": 80.0}, TODAY)
        flags = {e["date"]: " | ".join(e["flags"]) for e in out}
        self.assertIn("below the min: fixed 70 under min 80", flags[rows[1]["date"]])
        self.assertIn("its own min 60 under the listing min 80", flags[rows[2]["date"]])
        self.assertIn("stale: last set 160 days ago", flags[rows[3]["date"]])
        self.assertIn("fights the rule stack: +10% against last-minute -10%", flags[rows[3]["date"]])
        self.assertIn("past", flags["2031-06-01"])


class CardOrder(unittest.TestCase):
    def bundle(self):
        b = synthetic_bundle()
        b["inputs"]["listing"].update(id="fixture-listing", pms="smartbnb")
        for m in b["inputs"]["market"]["data"]:
            if (date.fromisoformat(m["date"]) - START).days >= 11:
                m["p75"], m["p90"] = 150, 160
        b["inputs"]["rules"] = {"raw": dict(OFF, last_minute_prices=LM), "summary": [],
                                "levels": {k: "listing" for k in OFF}, "gaps": ["ACCOUNT RULES UNREADABLE: x"]}
        return b

    def test_rule_changes_print_before_dso_suggestions_each_with_its_reason(self):
        facts = compute(self.bundle())
        card = render(facts, "run", METRICS)
        a = card.index("1) Rule changes (1):")
        b = card.index("2) DSO suggestions")
        c = card.index("3) Existing DSOs")
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertIn("Why:", card[a:b])
        self.assertIn('"rules_set": {"last_minute_prices": {"last_min_factor_value": -24}}', card)
        self.assertIn('"listing_id": "fixture-listing"', card)
        self.assertIn("GAP: ACCOUNT RULES UNREADABLE", card)
        folded = facts["rules_first"]["rule_changes"][0]["folded"]
        self.assertTrue(folded)
        for d in folded:
            self.assertNotIn(f"  {d}: net", card[b:c])  # a folded night is not also a DSO

    def test_the_change_file_on_the_card_plans_cleanly(self):
        """The printed change file is exactly what the writer accepts (offline, fake PriceLabs)."""
        import tempfile
        from _mvp_write import Live, WriteClient, plan_change
        from test_rule_write import FakeRules
        facts = compute(self.bundle())
        line = next(l for l in render(facts, "run", METRICS).splitlines() if "Change file:" in l)
        spec = json.loads(line.split("Change file:", 1)[1])
        fake = FakeRules()
        fake.rules["last_minute_prices"] = dict(LM)
        spec.update(listing_id=fake.listing["id"], pms=fake.listing["pms"])
        env = plan_change(spec, Live(WriteClient("synthetic-key", opener=fake), spec["listing_id"], spec["pms"]),
                          today=date(2026, 10, 1))
        self.assertEqual(env["operations"][0]["after"]["last_min_factor_value"], -24)

    def test_a_blocked_run_proposes_no_rule_change(self):
        b = self.bundle()
        b["inputs"]["prices"]["last_refreshed_at"] = "2031-06-01T00:00:00+00:00"  # stale: blocks
        facts = compute(b)
        self.assertEqual(facts["status"], "blocked")
        self.assertEqual(facts["rules_first"]["rule_changes"], [])


if __name__ == "__main__":
    unittest.main()
