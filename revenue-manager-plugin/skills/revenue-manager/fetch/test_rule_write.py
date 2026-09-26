"""Offline contracts for the `rule_set` operation on the PriceLabs writer (rules first, 2026-09-25).

A listing-level PriceLabs rule is changed through the same plan / apply / rollback flow as
min/base/max and DSOs, with every guarantee in docs/WRITE-TARGETS.md kept: a fresh read with
toggled_on=false at plan AND at apply, a drift refusal when the rule block moved, the
customization_write snapshot on disk before the send, a guarded payload (merge_dow, validate,
destructive_warnings), a field-by-field re-read where an empty or unreadable re-read is NOT
success, a journal always, a one-step undo that re-POSTs the snapshot, no retries and no
response bodies in errors. Group and account rules are shown by the runner but never written.

FakeRules plays PriceLabs' customization endpoints the way the live API behaves under HTTP 200:
day-of-week days omitted from a write reset to 0, last-minute toggled off resets to linear/0,
a `fix` far-out reads back as `linear` step 1, and the default read hides OFF rules.
"""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mvp_write import (  # noqa: E402
    CannotWrite, Live, WriteClient, apply_batch, apply_envelope, describe, plan_change,
    plan_id, rollback_change,
)
from test_mvp_write import LATER, LID, NOW, PMS, TODAY, FakePriceLabs, Response  # noqa: E402

DOW = {"dow_factor_on": True, "dow_factor_value_mon": -8.0, "dow_factor_value_tue": -8.0,
       "dow_factor_value_wed": -8.0, "dow_factor_value_thu": 0.0, "dow_factor_value_fri": 5.0,
       "dow_factor_value_sat": 5.0, "dow_factor_value_sun": 0.0}
LAST_MIN = {"last_min_factor_on": True, "last_min_factor_type": "linear_gradual",
            "last_min_factor_value": -10.0, "last_min_factor_dfd": 7}
FAR_OUT = {"far_out_premium_on": True, "far_out_premium_type": "linear",
           "far_out_premium_value": 10.0, "far_out_premium_start": 45, "far_out_premium_step": 90}
SEASONALITY = {"seasonality_customization_on": False, "seasonality_type": "recommended"}
DEMAND = {"tone_demand_factor_on": False, "tone_demand_factor": None}
CSP = {"custom_seasonal_profile_on": False,
       "custom_seasonal_profile": {"seasons": None, "price_type": None}}


class FakeRules(FakePriceLabs):
    """FakePriceLabs plus the listing customization endpoints."""

    def __init__(self, *, rules_mode=None, **kw):
        super().__init__(**kw)
        self.rules = {"seasonality": copy.deepcopy(SEASONALITY),
                      "last_minute_prices": copy.deepcopy(LAST_MIN),
                      "far_out_premium": copy.deepcopy(FAR_OUT),
                      "day_of_week_adjustment": copy.deepcopy(DOW),
                      "demand_factor": copy.deepcopy(DEMAND),
                      "custom_seasonal_profile": copy.deepcopy(CSP)}
        self.rules_mode = rules_mode or set()
        self.rule_posts = 0

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        method = req.get_method()
        path = url.path
        if path in ("/v1/customizations/group", "/v1/customizations/account"):
            raise AssertionError(f"the writer must never touch {method} {path}")
        if path != "/v1/customizations/listing":
            return super().open(req, timeout)
        body = json.loads(req.data) if req.data else None
        self.requests.append((method, path, body))
        assert req.headers.get("X-api-key") == "synthetic-key"
        if method == "GET":
            q = parse_qs(url.query)
            assert q.get("listing_id") == [LID] and q.get("pms_name") == [PMS], q
            if "empty_reread" in self.rules_mode and self.rule_posts:
                return Response({"customizations": {}})
            if "unreadable" in self.rules_mode:
                return Response({"error": "temporarily unavailable"})
            rules = copy.deepcopy(self.rules)
            if q.get("toggled_on") != ["false"]:  # the live default hides every OFF rule
                rules = {k: v for k, v in rules.items()
                         if any(k2.endswith("_on") and v2 is True for k2, v2 in v.items())}
            return Response({"customizations": rules})
        assert method == "POST", method
        assert body["listing_id"] == LID and body["pms_name"] == PMS, body
        self.rule_posts += 1
        if "http_error" in self.rules_mode:
            raise HTTPError(req.full_url, 400, "bad", {},
                            io.BytesIO(b'{"error":"echo synthetic-key back"}'))
        if "error_envelope" in self.rules_mode:
            return Response({"error_code": "ERR-VALIDATION-FAILED",
                             "error": "echo synthetic-key back", "details": []})
        if "silent_noop" in self.rules_mode:
            return Response({"status": "ok", "applied": sorted(body["customizations"])})
        for rule, cfg in body["customizations"].items():
            stored = self.rules.setdefault(rule, {})
            if rule == "day_of_week_adjustment":
                for k in [k for k in stored if k.startswith("dow_factor_value_")]:
                    stored[k] = 0.0  # days omitted from a write reset to 0 (measured live)
            if rule == "last_minute_prices" and cfg.get("last_min_factor_on") is False:
                stored.update({"last_min_factor_on": False, "last_min_factor_type": "linear",
                               "last_min_factor_value": 0.0, "last_min_factor_dfd": None})
                continue
            for k, v in cfg.items():
                stored[k] = float(v) if isinstance(v, int) and not isinstance(v, bool) and "_on" not in k \
                    and k not in ("last_min_factor_dfd", "far_out_premium_start", "far_out_premium_step") else v
            if rule == "far_out_premium" and cfg.get("far_out_premium_type") == "fix":
                stored.update({"far_out_premium_type": "linear", "far_out_premium_step": 1})
            if "sign_flip" in self.rules_mode and "last_min_factor_value" in cfg:
                stored["last_min_factor_value"] = abs(float(cfg["last_min_factor_value"]))
        if "side_effect" in self.rules_mode:
            self.rules["far_out_premium"]["far_out_premium_value"] = 99.0
        return Response({"status": "ok", "applied": sorted(body["customizations"])})

    def rule_writes(self):
        return [r for r in self.requests if r[0] == "POST" and r[1] == "/v1/customizations/listing"]


def live_for(fake):
    return Live(WriteClient("synthetic-key", opener=fake), LID, PMS)


def change(**parts):
    base = {"listing_id": LID, "pms": PMS, "reason": "rules first test"}
    base.update(parts)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, fake, spec, now=NOW, rollback=False):
        return plan_change(spec, live_for(fake), today=TODAY, now=now, rollback=rollback)

    def apply(self, fake, env, now=LATER):
        return apply_envelope(env, live_for(fake), state_dir=self.state, today=TODAY, now=now)

    def journal_on_disk(self):
        files = sorted(self.state.glob("journal/*.json"))
        self.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text())


class PlanRules(Base):
    def test_plan_reads_the_rules_fresh_with_toggled_on_false_and_sends_nothing(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}}))
        gets = [r for r in fake.requests if r[0] == "GET" and r[1] == "/v1/customizations/listing"]
        self.assertEqual(len(gets), 1)
        self.assertEqual(fake.writes(), [])
        (op,) = [o for o in env["operations"] if o["kind"] == "rule_set"]
        self.assertEqual(op["field"], "last_minute_prices")
        self.assertEqual(op["before"], LAST_MIN)
        self.assertEqual(op["after"], {"last_min_factor_on": True, "last_min_factor_type": "linear_gradual",
                                       "last_min_factor_value": -18, "last_min_factor_dfd": 7})

    def test_the_card_names_the_rule_and_blast_radius(self):
        env = self.plan(FakeRules(), change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}}))
        card = describe(env)
        self.assertIn("last_minute_prices", card)
        self.assertIn("-10%", card)
        self.assertIn("-18%", card)
        self.assertIn("BLAST RADIUS", card)
        self.assertNotIn("APPROVE", card)
        self.assertIn("first live write for PriceLabs rules: read the after-values carefully", card)

    def test_day_of_week_write_carries_all_seven_days(self):
        env = self.plan(FakeRules(), change(rules_set={"day_of_week_adjustment": {
            "dow_factor_value_mon": -5, "dow_factor_value_tue": -5, "dow_factor_value_wed": -5}}))
        after = env["operations"][0]["after"]
        self.assertEqual(after["dow_factor_value_fri"], 5)   # untouched day kept, not zeroed
        self.assertEqual(after["dow_factor_value_mon"], -5)
        self.assertEqual(sum(k.startswith("dow_factor_value_") for k in after), 7)
        self.assertIs(after["dow_factor_on"], True)

    def test_plan_id_is_stable_and_covers_the_rule_block(self):
        fake = FakeRules()
        spec = change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}})
        a, b = self.plan(fake, spec, now=NOW), self.plan(fake, spec, now=LATER)
        self.assertEqual(plan_id(a), plan_id(b))
        fake.rules["far_out_premium"]["far_out_premium_value"] = 12.0  # an untouched rule moved
        self.assertNotEqual(plan_id(a), plan_id(self.plan(fake, spec)))

    def test_out_of_range_value_is_refused_before_anything_is_sent(self):
        fake = FakeRules()
        with self.assertRaisesRegex(CannotWrite, "outside 0..75"):
            self.plan(fake, change(rules_set={"last_minute_prices": {"last_min_factor_value": -80}}))
        self.assertEqual(fake.writes(), [])

    def test_group_and_account_writes_are_refused_by_name(self):
        for level, word in (("group", "group"), ("account", "account")):
            with self.subTest(level=level), self.assertRaisesRegex(
                    CannotWrite, f"this changes every listing in the {word}; change it in PriceLabs"):
                self.plan(FakeRules(), change(level=level, rules_set={
                    "last_minute_prices": {"last_min_factor_value": -18}}))
        with self.assertRaisesRegex(CannotWrite, "every listing in the group"):
            self.plan(FakeRules(), change(group_id=42, rules_set={
                "last_minute_prices": {"last_min_factor_value": -18}}))

    def test_group_refusal_happens_before_any_read(self):
        fake = FakeRules()
        with self.assertRaises(CannotWrite):
            self.plan(fake, change(level="group", rules_set={"last_minute_prices": {}}))
        self.assertEqual(fake.requests, [])

    def test_a_rule_the_writer_cannot_prove_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "change it in PriceLabs"):
            self.plan(FakeRules(), change(rules_set={"seasonality": {"seasonality_type": "aggressive"}}))

    def test_a_rule_the_listing_does_not_set_is_refused(self):
        fake = FakeRules()
        del fake.rules["last_minute_prices"]
        with self.assertRaisesRegex(CannotWrite, "does not set last_minute_prices itself"):
            self.plan(fake, change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}}))

    def test_unknown_field_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "last_min_factor_percent"):
            self.plan(FakeRules(), change(rules_set={"last_minute_prices": {"last_min_factor_percent": -18}}))

    def test_no_op_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "already"):
            self.plan(FakeRules(), change(rules_set={"last_minute_prices": {"last_min_factor_value": -10}}))

    def test_toggling_last_minute_off_warns_it_resets_and_is_not_none(self):
        env = self.plan(FakeRules(), change(rules_set={"last_minute_prices": {"last_min_factor_on": False}}))
        text = " ".join(env["warnings"])
        self.assertIn("RESETS", text)
        self.assertIn("type none", text)

    def test_a_discount_that_turns_into_a_premium_is_flagged(self):
        env = self.plan(FakeRules(), change(rules_set={"last_minute_prices": {"last_min_factor_value": 5}}))
        self.assertTrue(any("SIGN FLIP" in w for w in env["warnings"]), env["warnings"])

    def test_a_big_rule_move_is_flagged_over_the_cap(self):
        env = self.plan(FakeRules(), change(rules_set={"day_of_week_adjustment": {"dow_factor_value_fri": 30}}))
        self.assertTrue(any("OVER 15%" in w for w in env["warnings"]), env["warnings"])

    def test_numbers_on_a_rule_that_stays_off_are_refused(self):
        fake = FakeRules()
        fake.rules["last_minute_prices"] = {"last_min_factor_on": False, "last_min_factor_type": "recommended",
                                            "last_min_factor_value": None, "last_min_factor_dfd": None}
        with self.assertRaisesRegex(CannotWrite, "is OFF on this listing"):
            self.plan(fake, change(rules_set={"last_minute_prices": {"last_min_factor_value": -15}}))
        env = self.plan(fake, change(rules_set={"last_minute_prices": {
            "last_min_factor_on": True, "last_min_factor_type": "linear", "last_min_factor_value": -15,
            "last_min_factor_dfd": 7}}))
        self.assertTrue(any("take over" in w for w in env["warnings"]), env["warnings"])

    def test_unreadable_rule_read_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "absence cannot be assumed"):
            self.plan(FakeRules(rules_mode={"unreadable"}),
                      change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}}))


class ApplyRules(Base):
    SPEC = {"last_minute_prices": {"last_min_factor_value": -18}}

    def test_happy_path_posts_the_guarded_payload_and_verifies(self):
        fake = FakeRules()
        journal = self.apply(fake, self.plan(fake, change(rules_set=self.SPEC)))
        self.assertEqual(journal["status"], "verified")
        (post,) = fake.rule_writes()
        self.assertEqual(post[2], {"listing_id": LID, "pms_name": PMS, "customizations": {
            "last_minute_prices": {"last_min_factor_on": True, "last_min_factor_type": "linear_gradual",
                                   "last_min_factor_value": -18, "last_min_factor_dfd": 7}}})
        self.assertEqual(fake.rules["last_minute_prices"]["last_min_factor_value"], -18.0)
        rows = {v.get("field"): v for v in journal["verification"]}
        self.assertTrue(rows["last_minute_prices"]["ok"])
        self.assertTrue(rows["day_of_week_adjustment"]["ok"])  # untouched rules re-read too

    def test_the_confirmation_lists_the_rule_as_changed(self):
        from apply_change import verification_lines
        fake = FakeRules()
        lines = verification_lines(self.apply(fake, self.plan(fake, change(rules_set=self.SPEC))))
        self.assertIn("  ok  last_minute_prices", lines)
        self.assertFalse(any("day_of_week_adjustment" in l for l in lines), lines)

    def test_day_of_week_write_keeps_the_days_it_did_not_change(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set={"day_of_week_adjustment": {"dow_factor_value_mon": -5}}))
        self.assertEqual(self.apply(fake, env)["status"], "verified")
        self.assertEqual(fake.rules["day_of_week_adjustment"]["dow_factor_value_fri"], 5.0)
        self.assertEqual(fake.rules["day_of_week_adjustment"]["dow_factor_value_tue"], -8.0)

    def test_rules_go_first_and_a_failed_rule_write_sends_nothing_else(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set=self.SPEC, listing_prices={"min": 160},
                                     overrides_set=[{"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        self.assertEqual(env["operations"][0]["kind"], "rule_set")
        self.assertEqual(self.apply(fake, env)["status"], "verified")
        order = [r[1] for r in fake.writes()]
        self.assertEqual(order[0], "/v1/customizations/listing", order)

        fake2 = FakeRules(rules_mode={"http_error"})
        env2 = self.plan(fake2, change(rules_set=self.SPEC, listing_prices={"min": 160}))
        with self.assertRaises(CannotWrite):
            self.apply(fake2, env2)
        self.assertEqual([r[1] for r in fake2.writes()], ["/v1/customizations/listing"])

    def test_drift_in_the_rule_block_refuses_and_sends_nothing(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set=self.SPEC))
        fake.rules["day_of_week_adjustment"]["dow_factor_value_sun"] = 3.0  # an untouched rule moved
        with self.assertRaisesRegex(CannotWrite, "rules changed since the plan"):
            self.apply(fake, env)
        self.assertEqual(fake.writes(), [])

    def test_apply_reads_the_rules_fresh_again(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set=self.SPEC))
        before = len([r for r in fake.requests if r[0] == "GET" and r[1] == "/v1/customizations/listing"])
        self.apply(fake, env)
        after = len([r for r in fake.requests if r[0] == "GET" and r[1] == "/v1/customizations/listing"])
        self.assertEqual(after - before, 2)  # the fresh pre-send read and the re-read

    def test_customization_snapshot_is_on_disk_before_the_send(self):
        fake = FakeRules(rules_mode={"http_error"})
        env = self.plan(fake, change(rules_set=self.SPEC))
        with self.assertRaises(CannotWrite):
            self.apply(fake, env)
        journal = self.journal_on_disk()
        snap = json.loads(Path(journal["rule_snapshot_path"]).read_text())
        self.assertEqual(snap, {"listing_id": LID, "pms_name": PMS,
                                "customizations": {"last_minute_prices": LAST_MIN}})

    def test_silent_no_op_under_200_is_caught(self):
        fake = FakeRules(rules_mode={"silent_noop"})
        with self.assertRaisesRegex(CannotWrite, "SENT but did not take"):
            self.apply(fake, self.plan(fake, change(rules_set=self.SPEC)))
        self.assertEqual(self.journal_on_disk()["status"], "sent-unverified")

    def test_a_sign_stored_the_wrong_way_is_caught(self):
        fake = FakeRules(rules_mode={"sign_flip"})
        with self.assertRaisesRegex(CannotWrite, "SIGN INVERTED"):
            self.apply(fake, self.plan(fake, change(rules_set=self.SPEC)))

    def test_an_empty_reread_is_not_success(self):
        fake = FakeRules(rules_mode={"empty_reread"})
        with self.assertRaisesRegex(CannotWrite, "SENT but"):
            self.apply(fake, self.plan(fake, change(rules_set=self.SPEC)))
        self.assertEqual(self.journal_on_disk()["status"], "sent-unverified")

    def test_a_side_effect_on_another_rule_is_caught(self):
        fake = FakeRules(rules_mode={"side_effect"})
        with self.assertRaisesRegex(CannotWrite, "far_out_premium"):
            self.apply(fake, self.plan(fake, change(rules_set=self.SPEC)))

    def test_error_envelope_under_200_never_echoes_the_body(self):
        fake = FakeRules(rules_mode={"error_envelope"})
        with self.assertRaises(CannotWrite) as ctx:
            self.apply(fake, self.plan(fake, change(rules_set=self.SPEC)))
        self.assertIn("ERR-VALIDATION-FAILED", str(ctx.exception))
        self.assertNotIn("synthetic-key", str(ctx.exception))
        self.assertNotIn("synthetic-key", json.dumps(self.journal_on_disk()))

    def test_http_error_is_never_retried_and_never_echoed(self):
        fake = FakeRules(rules_mode={"http_error"})
        with self.assertRaises(CannotWrite) as ctx:
            self.apply(fake, self.plan(fake, change(rules_set=self.SPEC)))
        self.assertEqual(len(fake.rule_writes()), 1)
        self.assertNotIn("synthetic-key", str(ctx.exception))

    def test_far_out_fix_reads_back_as_linear_and_still_verifies(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set={"far_out_premium": {
            "far_out_premium_type": "fix", "far_out_premium_value": 12}}))
        self.assertEqual(self.apply(fake, env)["status"], "verified")

    def test_toggle_off_last_minute_verifies_on_the_toggle(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set={"last_minute_prices": {"last_min_factor_on": False}}))
        self.assertEqual(self.apply(fake, env)["status"], "verified")
        self.assertFalse(fake.rules["last_minute_prices"]["last_min_factor_on"])

    def test_batch_with_a_rule_plan_applies(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set=self.SPEC))
        (journal,) = apply_batch([env], lambda lid, pms: live_for(fake), state_dir=self.state,
                                 today=TODAY, now=LATER)
        self.assertEqual(journal["status"], "verified")


class RollbackRules(Base):
    def roundtrip(self, spec):
        fake = FakeRules()
        original = copy.deepcopy(fake.rules)
        journal = self.apply(fake, self.plan(fake, change(rules_set=spec)))
        back = self.plan(fake, rollback_change(journal), now=LATER, rollback=True)
        self.assertEqual(self.apply(fake, back)["status"], "verified")
        norm = lambda d: json.loads(json.dumps(d), parse_float=float, parse_int=float)
        for rule in spec:
            got = {k: v for k, v in fake.rules[rule].items() if v is not None}
            want = {k: v for k, v in original[rule].items() if v is not None}
            self.assertEqual(norm(got), norm(want), rule)
        return fake

    def test_last_minute_rolls_back(self):
        self.roundtrip({"last_minute_prices": {"last_min_factor_value": -18}})

    def test_day_of_week_rolls_back_with_all_seven_days(self):
        self.roundtrip({"day_of_week_adjustment": {"dow_factor_value_mon": -2, "dow_factor_value_sat": 9}})

    def test_rollback_from_the_customization_snapshot_alone(self):
        fake = FakeRules()
        env = self.plan(fake, change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}}))
        journal = self.apply(fake, env)
        snap = json.loads(Path(journal["rule_snapshot_path"]).read_text())
        back = self.plan(fake, rollback_change(snap), now=LATER, rollback=True)
        self.assertEqual(back["operations"][0]["kind"], "rule_set")
        self.assertEqual(self.apply(fake, back)["status"], "verified")
        self.assertEqual(fake.rules["last_minute_prices"]["last_min_factor_value"], -10.0)

    def test_rules_restore_is_undo_only(self):
        with self.assertRaisesRegex(CannotWrite, "undo"):
            self.plan(FakeRules(), change(rules_restore={"last_minute_prices": LAST_MIN}))

    def test_nothing_to_undo_when_already_back(self):
        fake = FakeRules()
        journal = self.apply(fake, self.plan(fake, change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}})))
        fake.rules["last_minute_prices"]["last_min_factor_value"] = -10.0
        with self.assertRaisesRegex(CannotWrite, "Nothing to undo"):
            self.plan(fake, rollback_change(journal), now=LATER, rollback=True)


class TransportAndAudit(unittest.TestCase):
    def test_listing_rules_are_reachable_group_and_account_are_not(self):
        c = WriteClient("synthetic-key", opener=FakeRules())
        c.request("GET", "/v1/customizations/listing",
                  {"listing_id": LID, "pms_name": PMS, "toggled_on": "false"})
        for method, path in (("POST", "/v1/customizations/group"), ("POST", "/v1/customizations/account"),
                             ("GET", "/v1/customizations/group"), ("POST", "/v1/customization_profiles")):
            with self.subTest(path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                c.request(method, path, body={"x": 1})

    def test_audit_row_names_the_rule(self):
        from apply_change import audit_statement
        journal = {"plan_id": "0123456789ab", "journal_path": "/x/j.json", "status": "verified",
                   "envelope": {"target": {"listing_id": LID, "pms": PMS}, "reason": "r",
                                "operations": [{"kind": "rule_set", "field": "last_minute_prices",
                                                "rule": "last_minute_prices", "before": LAST_MIN,
                                                "after": dict(LAST_MIN, last_min_factor_value=-18)}]}}
        sql = audit_statement(journal)
        self.assertIn("'rule_set'", sql)
        self.assertIn("'last_minute_prices'", sql)


class CLIFlow(unittest.TestCase):
    """The commands the operator runs, end to end against FakeRules: plan prints a clear
    before -> after card, apply verifies, rollback --journal re-POSTs the snapshot exactly and
    the re-read proves it."""

    def cli(self, *argv):
        import apply_change
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = apply_change.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_plan_apply_rollback(self):
        import re
        from unittest.mock import patch
        fake = FakeRules()
        original = copy.deepcopy(fake.rules)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"RC_CACHE_DIR": tmp, "PRICELABS_API_KEY": "synthetic-key"}), \
                patch("apply_change.live_for", lambda c, lid, pms: live_for(fake)):
            f = Path(tmp) / "c.json"
            f.write_text(json.dumps(change(rules_set={"last_minute_prices": {"last_min_factor_value": -18}})))
            rc, card, err = self.cli("plan", "--change", str(f))
            self.assertEqual(rc, 0, err)
            self.assertIn("rule last_minute_prices (listing level, applied first): on, linear_gradual -10% "
                          "over 0-7 days out  ->  on, linear_gradual -18% over 0-7 days out", card)
            self.assertEqual(fake.writes(), [])
            pid = re.search(r"--plan ([0-9a-f]{12})", card).group(1)
            rc, out, err = self.cli("apply", "--plan", pid, "--no-audit")
            self.assertEqual(rc, 0, err)
            self.assertIn("APPLIED AND VERIFIED", out)
            self.assertIn("  ok  last_minute_prices", out)
            self.assertEqual(fake.rules["last_minute_prices"]["last_min_factor_value"], -18.0)
            journal = re.search(r"rollback --journal (\S+)", out).group(1)
            rc, back, err = self.cli("rollback", "--journal", journal)
            self.assertEqual(rc, 0, err)
            self.assertIn("RESTORES last_minute_prices exactly as it was before", back)
            self.assertIn("-18% over 0-7 days out  ->  on, linear_gradual -10%", back)
            pid2 = re.search(r"--plan ([0-9a-f]{12})", back).group(1)
            rc, out, err = self.cli("apply", "--plan", pid2, "--no-audit")
            self.assertEqual(rc, 0, err)
            self.assertIn("APPLIED AND VERIFIED", out)
            self.assertEqual(fake.rules, original)
            reposted = fake.rule_writes()[-1][2]["customizations"]["last_minute_prices"]
            # the FIRST rule snapshot (the one the change took); names carry a timestamp and sort
            snap = json.loads(sorted(Path(tmp, "writes", "snapshots").glob("snapshot_*.json"))[0].read_text())
            self.assertEqual(reposted, {k: (int(v) if isinstance(v, float) and v.is_integer() else v)
                                        for k, v in snap["customizations"]["last_minute_prices"].items()})


class CLI(unittest.TestCase):
    def test_group_level_change_file_exits_2_with_the_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "c.json"
            f.write_text(json.dumps(change(level="group", rules_set={
                "last_minute_prices": {"last_min_factor_value": -18}})))
            env = dict(os.environ, RC_CACHE_DIR=tmp, PRICELABS_API_KEY="synthetic-key")
            r = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parent / "apply_change.py"),
                                "plan", "--change", str(f)], capture_output=True, text=True, env=env,
                               cwd=tmp, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("this changes every listing in the group; change it in PriceLabs", r.stderr)


if __name__ == "__main__":
    unittest.main()
