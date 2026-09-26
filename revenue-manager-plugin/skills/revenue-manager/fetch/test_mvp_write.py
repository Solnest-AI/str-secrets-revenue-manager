"""Offline contracts for the price writer (PRD D1 as amended 2026-09-23, D8, S2-S4).

Every test runs against FakePriceLabs, a stateful stand-in that can be told to misbehave
the ways a live API does under HTTP 200: a silent no-op, replace-instead-of-merge, a side
effect on a field nobody asked to change, an `errors` array. Nothing here touches a network.
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
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from _mvp_write import (
    CannotWrite,
    Live,
    WriteClient,
    apply_batch,
    apply_envelope,
    content_hash,
    load_plan,
    plan_change,
    plan_id,
    rollback_change,
    save_envelope,
)

LID = "listing-write-0001"  # not UUID-shaped: the leak scan flags the 8-4-4-4-12 shape
PMS = "hospitable"
TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)


class Response:
    def __init__(self, body, status=200):
        self.body = b"" if body is None else json.dumps(body).encode()
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class FakePriceLabs:
    """Holds one listing and its overrides; answers the six calls the writer may make."""

    def __init__(self, *, currency="CAD", override_post="merge", silent_noop=False,
                 side_effect=None, listing_errors=None, fail_post=None, error_envelope=False):
        self.listing = {"id": LID, "pms": PMS, "name": "Test Listing", "currency": currency,
                        "min": 150.0, "base": 200.0, "max": 400.0, "push_enabled": True}
        self.overrides = {
            "2026-10-10": {"date": "2026-10-10", "price": "250", "price_type": "fixed",
                           "currency": "CAD", "min_stay": 3,
                           "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"},
            "2026-11-20": {"date": "2026-11-20", "price": "-10", "price_type": "percent",
                           "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"},
            # measured live 2026-09-23: a real override can carry no price at all
            "2026-12-01": {"date": "2026-12-01", "min_stay": 1, "reason": "Weekday single-night test",
                           "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"},
        }
        # the recommended nightly price PriceLabs currently shows, next 90 days
        self.prices = {}
        d = TODAY.toordinal()
        for i in range(120):
            day = date.fromordinal(d + i).isoformat()
            self.prices[day] = 160.0 if i % 10 < 3 else 220.0
        self.override_post = override_post
        self.silent_noop = silent_noop
        self.side_effect = side_effect
        self.listing_errors = listing_errors
        self.fail_post = fail_post
        self.error_envelope = error_envelope
        self.requests = []

    def writes(self):
        return [r for r in self.requests if r[0] in ("POST", "DELETE") and r[1] != "/v1/listing_prices"]

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        method = req.get_method()
        body = json.loads(req.data) if req.data else None
        self.requests.append((method, url.path, body))
        assert req.headers.get("X-api-key") == "synthetic-key", "API key header missing"
        path = url.path
        if method == "GET" and path == f"/v1/listings/{LID}":
            if self.error_envelope:
                return Response({"error": "temporarily unavailable"})
            return Response({"listings": [copy.deepcopy(self.listing)]})
        if method == "GET" and path == f"/v1/listings/{LID}/overrides":
            assert parse_qs(url.query).get("pms") == [PMS]
            return Response({"overrides": copy.deepcopy(list(self.overrides.values()))})
        if method == "POST" and path == "/v1/listing_prices":
            item = body["listings"][0]
            rows = [{"date": k, "price": v, "booking_status": "", "unbookable": 0}
                    for k, v in sorted(self.prices.items())
                    if item["dateFrom"] <= k <= item["dateTo"]]
            return Response([{"id": LID, "pms": PMS, "currency": self.listing["currency"], "data": rows}])
        if method in ("POST", "DELETE") and self.fail_post:
            raise HTTPError(req.full_url, self.fail_post, "boom", {}, io.BytesIO(b"{}"))
        if method == "POST" and path == "/v1/listings":
            item = body["listings"][0]
            if not self.silent_noop:
                for k in ("min", "base", "max"):
                    if k in item:
                        self.listing[k] = float(item[k])
            if self.side_effect:
                self.listing.update(self.side_effect)
            out = {k: self.listing[k] for k in ("id", "min", "base", "max")}
            if self.listing_errors:
                out["errors"] = self.listing_errors
            return Response({"listings": [out]})
        if method == "POST" and path == f"/v1/listings/{LID}/overrides":
            assert body["pms"] == PMS and body["update_children"] is False, body
            for o in body["overrides"]:
                o = {k: (str(v) if k == "price" else v) for k, v in o.items()}
                if self.silent_noop:
                    continue
                if self.override_post == "merge" and o["date"] in self.overrides:
                    self.overrides[o["date"]].update(o)
                else:
                    self.overrides[o["date"]] = dict(o)
            return Response({"overrides": body["overrides"]})
        if method == "DELETE" and path == f"/v1/listings/{LID}/overrides":
            assert body["pms"] == PMS and body["update_children"] is False, body
            for o in body["overrides"]:
                if not self.silent_noop:
                    self.overrides.pop(o["date"], None)
            return Response(None, status=204)
        raise AssertionError(f"FakePriceLabs has no route for {method} {path}")


def live_for(fake):
    return Live(WriteClient("synthetic-key", opener=fake), LID, PMS)


def change(**parts):
    base = {"listing_id": LID, "pms": PMS, "reason": "test reason"}
    base.update(parts)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, fake, spec, now=NOW):
        return plan_change(spec, live_for(fake), today=TODAY, now=now)

    def apply(self, fake, env, now=LATER, today=TODAY):
        return apply_envelope(env, live_for(fake), state_dir=self.state, today=today, now=now)

    def plan_undo(self, fake, journal, today=TODAY, now=LATER):
        return plan_change(rollback_change(journal), live_for(fake), today=today, now=now,
                           rollback=True)

    def journal_on_disk(self):
        files = sorted(self.state.glob("journal/*.json"))
        self.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text())


class PlanId(Base):
    def test_rebuilt_plan_keeps_its_id(self):
        fake = FakePriceLabs()
        a = self.plan(fake, change(listing_prices={"min": 170}), now=NOW)
        b = self.plan(fake, change(listing_prices={"min": 170}), now=LATER)
        self.assertNotEqual(a["created_at"], b["created_at"])
        self.assertEqual(plan_id(a), plan_id(b))

    def test_one_changed_field_gets_a_new_id(self):
        fake = FakePriceLabs()
        a = self.plan(fake, change(listing_prices={"min": 170}))
        b = self.plan(fake, change(listing_prices={"min": 171}))
        self.assertNotEqual(plan_id(a), plan_id(b))

    def test_id_covers_the_before_image_too(self):
        fake = FakePriceLabs()
        a = self.plan(fake, change(listing_prices={"min": 170}))
        fake.listing["min"] = 140.0
        b = self.plan(fake, change(listing_prices={"min": 170}))
        self.assertNotEqual(plan_id(a), plan_id(b))

    def test_reason_is_not_part_of_what_gets_written(self):
        fake = FakePriceLabs()
        a = self.plan(fake, change(listing_prices={"min": 170}))
        spec = change(listing_prices={"min": 170})
        spec["reason"] = "different words"
        self.assertEqual(plan_id(a), plan_id(self.plan(fake, spec)))

    def test_id_format(self):
        env = self.plan(FakePriceLabs(), change(listing_prices={"min": 170}))
        self.assertRegex(plan_id(env), r"^[0-9a-f]{12}$")
        self.assertEqual(plan_id(env), content_hash(env)[:12])

    def test_card_has_no_code_ritual(self):
        from _mvp_write import describe
        card = describe(self.plan(FakePriceLabs(), change(listing_prices={"min": 170})))
        self.assertNotIn("APPROVE", card)
        self.assertNotIn("CODE", card)

    def test_saved_plan_loads_by_id(self):
        env = self.plan(FakePriceLabs(), change(listing_prices={"min": 170}))
        save_envelope(env, self.state)
        self.assertEqual(load_plan(self.state, plan_id(env))["operations"], env["operations"])

    def test_plan_edited_on_disk_after_it_was_shown_is_refused(self):
        env = self.plan(FakePriceLabs(), change(listing_prices={"min": 170}))
        path = save_envelope(env, self.state)
        data = json.loads(Path(path).read_text())
        data["operations"][0]["after"] = 999.0
        Path(path).write_text(json.dumps(data))
        with self.assertRaisesRegex(CannotWrite, "edited after it was shown"):
            load_plan(self.state, plan_id(env))

    def test_unknown_or_malformed_plan_id_is_refused(self):
        for bad in ("0123456789ab", "APPROVE 0123456789ab", "zz", ""):
            with self.subTest(bad=bad), self.assertRaises(CannotWrite):
                load_plan(self.state, bad)


class PlanListingPrices(Base):
    def test_plan_reads_fresh_and_sends_nothing(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.assertEqual(fake.writes(), [])
        self.assertEqual(env["operations"], [
            {"kind": "listing_price", "field": "min", "before": 150.0, "after": 170.0}])
        self.assertEqual(env["target"], {"listing_id": LID, "pms": PMS, "currency": "CAD"})

    def test_every_plan_reads_live_never_cache(self):
        fake = FakePriceLabs()
        self.plan(fake, change(listing_prices={"min": 170}))
        self.plan(fake, change(listing_prices={"min": 170}))
        gets = [r for r in fake.requests if r[0] == "GET" and r[1] == f"/v1/listings/{LID}"]
        self.assertEqual(len(gets), 2)

    def test_over_15_percent_is_flagged_loudly(self):
        env = self.plan(FakePriceLabs(), change(listing_prices={"base": 240}))
        self.assertTrue(any("OVER 15%" in w for w in env["warnings"]), env["warnings"])

    def test_exactly_15_percent_is_not_flagged(self):
        env = self.plan(FakePriceLabs(), change(listing_prices={"base": 230}))
        self.assertFalse(any("OVER 15%" in w for w in env["warnings"]), env["warnings"])

    def test_min_raise_counts_nights_priced_below_it(self):
        env = self.plan(FakePriceLabs(), change(listing_prices={"min": 170}))
        joined = " ".join(env["warnings"])
        self.assertIn("27 of the next 90 nights", joined)

    def test_min_above_base_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "min <= base <= max"):
            self.plan(FakePriceLabs(), change(listing_prices={"min": 210}))

    def test_bad_numbers_are_refused(self):
        for bad in (0, -5, "abc", float("nan"), float("inf"), True, None):
            with self.subTest(bad=bad), self.assertRaises(CannotWrite):
                self.plan(FakePriceLabs(), change(listing_prices={"min": bad}))

    def test_fields_the_writer_does_not_own_are_refused(self):
        for field in ("push_enabled", "tags", "group_id", "id", "pms"):
            with self.subTest(field=field), self.assertRaisesRegex(CannotWrite, "not a field"):
                self.plan(FakePriceLabs(), change(listing_prices={field: 1}))

    def test_no_op_change_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "already"):
            self.plan(FakePriceLabs(), change(listing_prices={"min": 150}))

    def test_reason_is_required(self):
        spec = change(listing_prices={"min": 170})
        del spec["reason"]
        with self.assertRaisesRegex(CannotWrite, "reason"):
            self.plan(FakePriceLabs(), spec)

    def test_unknown_top_level_key_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "customizations"):
            self.plan(FakePriceLabs(), change(customizations={"x": 1}))

    def test_empty_change_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "nothing to write"):
            self.plan(FakePriceLabs(), change())

    def test_error_envelope_under_http_200_is_refused(self):
        with self.assertRaises(CannotWrite):
            self.plan(FakePriceLabs(error_envelope=True), change(listing_prices={"min": 170}))


class PlanOverrides(Base):
    def test_new_fixed_override_takes_listing_currency(self):
        env = self.plan(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-10-05", "price": 240, "price_type": "fixed"}]))
        op = env["operations"][0]
        self.assertEqual(op["before"], None)
        self.assertEqual(op["after"], {"date": "2026-10-05", "price": "240", "price_type": "fixed",
                                       "currency": "CAD"})

    def test_existing_override_fields_are_carried_forward(self):
        env = self.plan(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-10-10", "price": 270, "price_type": "fixed"}]))
        op = env["operations"][0]
        self.assertEqual(op["before"]["min_stay"], 3)
        self.assertEqual(op["after"]["min_stay"], 3, "an unmentioned field must not be wiped")
        self.assertEqual(op["after"]["price"], "270")
        self.assertNotIn("created_at", op["after"])
        self.assertTrue(any("REPLACES" in w for w in env["warnings"]), env["warnings"])

    def test_min_stay_only_override_is_described_in_words(self):
        env = self.plan(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-12-01", "price": 230, "price_type": "fixed"}]))
        joined = " ".join(env["warnings"])
        self.assertIn("no price set", joined)
        self.assertNotIn("None", joined)
        self.assertEqual(env["operations"][0]["after"]["min_stay"], 1)
        self.assertEqual(env["operations"][0]["after"]["reason"], "Weekday single-night test")

    def test_min_stay_only_override_roundtrips(self):
        Rollback.roundtrip(self, FakePriceLabs(), change(overrides_set=[
            {"date": "2026-12-01", "price": 230, "price_type": "fixed"}]))

    def test_fixed_override_far_from_current_price_is_flagged(self):
        env = self.plan(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-10-05", "price": 300, "price_type": "fixed"}]))
        self.assertTrue(any("OVER 15%" in w for w in env["warnings"]), env["warnings"])

    def test_percent_override_beyond_15_is_flagged(self):
        env = self.plan(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-10-05", "price": -20, "price_type": "percent"}]))
        self.assertTrue(any("OVER 15%" in w for w in env["warnings"]), env["warnings"])
        self.assertNotIn("currency", env["operations"][0]["after"])

    def test_percent_out_of_documented_range_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "-75"):
            self.plan(FakePriceLabs(), change(overrides_set=[
                {"date": "2026-10-05", "price": -80, "price_type": "percent"}]))

    def test_min_stay_must_be_a_positive_integer(self):
        for bad in (0, 2.5, -1, "2"):
            with self.subTest(bad=bad), self.assertRaises(CannotWrite):
                self.plan(FakePriceLabs(), change(overrides_set=[
                    {"date": "2026-10-05", "price": 200, "price_type": "fixed", "min_stay": bad}]))

    def test_past_and_malformed_dates_are_refused(self):
        for bad in ("2026-09-30", "2026-13-01", "next friday", ""):
            with self.subTest(bad=bad), self.assertRaises(CannotWrite):
                self.plan(FakePriceLabs(), change(overrides_set=[
                    {"date": bad, "price": 200, "price_type": "fixed"}]))

    def test_same_date_twice_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "twice"):
            self.plan(FakePriceLabs(), change(
                overrides_set=[{"date": "2026-10-10", "price": 200, "price_type": "fixed"}],
                overrides_delete=["2026-10-10"]))

    def test_delete_says_what_the_date_falls_back_to(self):
        env = self.plan(FakePriceLabs(), change(overrides_delete=["2026-10-10"]))
        op = env["operations"][0]
        self.assertEqual(op["after"], None)
        self.assertEqual(op["before"]["price"], "250")
        self.assertTrue(any("REMOVES" in w and "PriceLabs" in w for w in env["warnings"]))

    def test_delete_of_a_date_with_no_override_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "no override"):
            self.plan(FakePriceLabs(), change(overrides_delete=["2026-10-06"]))

    def test_unsupported_override_field_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "not a field"):
            self.plan(FakePriceLabs(), change(overrides_set=[
                {"date": "2026-10-05", "price": 200, "price_type": "fixed", "update_children": True}]))

    def test_fixed_override_without_known_currency_is_refused(self):
        fake = FakePriceLabs(currency=None)
        with self.assertRaisesRegex(CannotWrite, "currency"):
            self.plan(fake, change(overrides_set=[
                {"date": "2026-10-05", "price": 200, "price_type": "fixed"}]))


class Apply(Base):
    def test_happy_path_sends_only_the_changed_field_and_verifies(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        journal = self.apply(fake, env)
        self.assertEqual(journal["status"], "verified")
        self.assertEqual(fake.writes(), [
            ("POST", "/v1/listings", {"listings": [{"id": LID, "pms": PMS, "min": 170.0}]})])
        self.assertEqual(fake.listing["min"], 170.0)
        self.assertTrue(Path(journal["snapshot_path"]).is_file())
        self.assertTrue(Path(journal["journal_path"]).is_file())

    def test_drift_between_plan_and_apply_refuses_and_sends_nothing(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        fake.listing["min"] = 155.0
        with self.assertRaisesRegex(CannotWrite, "changed since"):
            self.apply(fake, env)
        self.assertEqual(fake.writes(), [])

    def test_override_drift_between_plan_and_apply_refuses_and_sends_nothing(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-10", "price": 270, "price_type": "fixed"}]))
        fake.overrides["2026-10-10"]["price"] = "255"  # someone edited it in the PriceLabs UI
        with self.assertRaisesRegex(CannotWrite, "changed since"):
            self.apply(fake, env)
        self.assertEqual(fake.writes(), [])

    def test_override_appearing_on_an_empty_date_since_the_plan_refuses(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        fake.overrides["2026-10-05"] = {"date": "2026-10-05", "price": "199", "price_type": "fixed",
                                        "currency": "CAD"}
        with self.assertRaisesRegex(CannotWrite, "changed since"):
            self.apply(fake, env)
        self.assertEqual(fake.writes(), [])

    def test_snapshot_exists_before_the_send(self):
        fake = FakePriceLabs(fail_post=500)
        env = self.plan(fake, change(listing_prices={"min": 170}))
        with self.assertRaisesRegex(CannotWrite, "HTTP 500") as ctx:
            self.apply(fake, env)
        snaps = list(self.state.glob("snapshots/*.json"))
        self.assertEqual(len(snaps), 1)
        self.assertIn("rollback", str(ctx.exception))

    def test_silent_no_op_under_200_is_caught(self):
        fake = FakePriceLabs(silent_noop=True)
        env = self.plan(fake, change(listing_prices={"min": 170}))
        with self.assertRaisesRegex(CannotWrite, "did not take"):
            self.apply(fake, env)
        journals = list(self.state.glob("journal/*.json"))
        self.assertEqual(len(journals), 1)
        self.assertEqual(json.loads(journals[0].read_text())["status"], "sent-unverified")

    def test_side_effect_on_an_untouched_field_is_caught(self):
        fake = FakePriceLabs(side_effect={"base": 199.0})
        env = self.plan(fake, change(listing_prices={"min": 170}))
        with self.assertRaisesRegex(CannotWrite, "base"):
            self.apply(fake, env)

    def test_errors_array_under_200_is_caught(self):
        fake = FakePriceLabs(listing_errors=["sync update failed"])
        env = self.plan(fake, change(listing_prices={"min": 170}))
        with self.assertRaisesRegex(CannotWrite, "sync update failed"):
            self.apply(fake, env)

    def test_override_replace_semantics_is_caught(self):
        fake = FakePriceLabs(override_post="replace")
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-10", "price": 270, "price_type": "fixed"}]))
        # carried-forward fields are SENT, so a replacing server still ends up whole
        journal = self.apply(fake, env)
        self.assertEqual(journal["status"], "verified")
        self.assertEqual(fake.overrides["2026-10-10"]["min_stay"], 3)

    def test_override_that_loses_a_field_is_caught(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-10", "price": 270, "price_type": "fixed"}]))
        real_open = fake.open

        def lossy(req, timeout):
            resp = real_open(req, timeout)
            if req.get_method() == "POST" and req.full_url.endswith("/overrides"):
                fake.overrides["2026-10-10"].pop("min_stay", None)
            return resp
        fake.open = lossy
        with self.assertRaisesRegex(CannotWrite, "min_stay"):
            self.apply(fake, env)

    def test_other_override_dates_must_be_untouched(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        real_open = fake.open

        def clobber(req, timeout):
            resp = real_open(req, timeout)
            if req.get_method() == "POST" and req.full_url.endswith("/overrides"):
                fake.overrides.pop("2026-11-20", None)
            return resp
        fake.open = clobber
        with self.assertRaisesRegex(CannotWrite, "2026-11-20"):
            self.apply(fake, env)

    def test_delete_happy_path(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_delete=["2026-10-10"]))
        journal = self.apply(fake, env)
        self.assertEqual(journal["status"], "verified")
        self.assertNotIn("2026-10-10", fake.overrides)
        self.assertEqual(fake.writes(), [("DELETE", f"/v1/listings/{LID}/overrides",
                                          {"pms": PMS, "update_children": False,
                                           "overrides": [{"date": "2026-10-10"}]})])

    def test_journal_never_holds_the_key(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        journal = self.apply(fake, env)
        for p in self.state.rglob("*.json"):
            self.assertNotIn("synthetic-key", p.read_text(), p)
        self.assertNotIn("synthetic-key", json.dumps(journal))


class Batch(Base):
    def test_one_yes_applies_every_plan_in_order(self):
        fake = FakePriceLabs()
        a = self.plan(fake, change(listing_prices={"min": 170}))
        b = self.plan(fake, change(overrides_delete=["2026-10-10"]))
        seen = []
        journals = apply_batch([a, b], lambda lid, pms: live_for(fake), state_dir=self.state,
                               today=TODAY, now=LATER, on_verified=lambda j: seen.append(j["plan_id"]))
        self.assertEqual([j["status"] for j in journals], ["verified", "verified"])
        self.assertEqual(seen, [plan_id(a), plan_id(b)])
        self.assertEqual(fake.listing["min"], 170.0)
        self.assertNotIn("2026-10-10", fake.overrides)

    def test_first_failure_stops_the_batch_and_names_what_was_not_attempted(self):
        fake = FakePriceLabs()
        a = self.plan(fake, change(listing_prices={"min": 170}))
        b = self.plan(fake, change(overrides_delete=["2026-10-10"]))
        fake.listing["min"] = 155.0  # a drifted since the plan
        with self.assertRaisesRegex(CannotWrite, "NOT ATTEMPTED: " + plan_id(b)):
            apply_batch([a, b], lambda lid, pms: live_for(fake), state_dir=self.state, today=TODAY,
                        now=LATER)
        self.assertEqual(fake.writes(), [])
        self.assertIn("2026-10-10", fake.overrides)


class Rollback(Base):
    def roundtrip(self, fake, spec):
        original = {"listing": copy.deepcopy(fake.listing), "overrides": copy.deepcopy(fake.overrides)}
        env = self.plan(fake, spec)
        journal = self.apply(fake, env)
        back = self.plan_undo(fake, journal)
        self.assertNotEqual(plan_id(back), plan_id(env), "a rollback is its own plan")
        self.assertEqual(self.apply(fake, back)["status"], "verified")
        self.assertEqual(fake.listing, original["listing"])
        strip = lambda d: {k: {f: v for f, v in o.items() if f not in ("created_at", "updated_at")}
                           for k, o in d.items()}
        self.assertEqual(strip(fake.overrides), strip(original["overrides"]))

    def test_listing_price_rollback_restores(self):
        self.roundtrip(FakePriceLabs(), change(listing_prices={"min": 170, "max": 420}))

    def test_new_override_rolls_back_to_a_delete(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        back = rollback_change(self.apply(fake, env))
        self.assertEqual(back["overrides_delete"], ["2026-10-05"])
        self.roundtrip(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))

    def test_deleted_override_rolls_back_whole(self):
        self.roundtrip(FakePriceLabs(), change(overrides_delete=["2026-10-10"]))

    def test_changed_override_rolls_back_whole(self):
        self.roundtrip(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-10-10", "price": 270, "price_type": "fixed", "min_stay": 2}]))

    def test_added_field_rolls_back_by_replacing_the_date(self):
        # 2026-11-20 has no min_stay; adding one and rolling back must remove it again,
        # which a merging POST alone cannot do
        self.roundtrip(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-11-20", "price": -10, "price_type": "percent", "min_stay": 2}]))

    def test_fixed_to_percent_drops_currency_by_replacing(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-10", "price": -5, "price_type": "percent"}]))
        self.assertTrue(env["operations"][0].get("replace"))
        self.assertEqual(self.apply(fake, env)["status"], "verified")
        self.assertNotIn("currency", fake.overrides["2026-10-10"])
        self.roundtrip(FakePriceLabs(), change(overrides_set=[
            {"date": "2026-10-10", "price": -5, "price_type": "percent"}]))

    def test_rollback_from_the_snapshot_file_alone(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        journal = self.apply(fake, env)
        snap = json.loads(Path(journal["snapshot_path"]).read_text())
        self.assertEqual(snap["listing_prices"], {"min": 150.0})


class Transport(unittest.TestCase):
    def client(self, fake=None):
        return WriteClient("synthetic-key", opener=fake or FakePriceLabs())

    def test_refuses_every_write_it_was_not_built_for(self):
        c = self.client()
        for method, path in (("POST", "/v1/customizations/listing"), ("POST", "/v1/nudges/accept"),
                             ("POST", "/v1/add_listing_data"), ("POST", "/v1/group_overrides"),
                             ("DELETE", "/v1/group_overrides"), ("POST", "/v1/mappings/map"),
                             ("POST", "/v1/refresh_listing"), ("PUT", "/v1/listings")):
            with self.subTest(path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                c.request(method, path, body={"x": 1})

    def test_refuses_other_listing_paths_and_traversal(self):
        c = self.client()
        for path in ("/v1/listings/abc/overrides/../../customizations/listing",
                     "/v1/listings/a b/overrides", "/v1/listings//overrides"):
            with self.subTest(path=path), self.assertRaises(CannotWrite):
                c.request("POST", path, body={"overrides": []})

    def test_a_failed_post_is_never_retried(self):
        fake = FakePriceLabs(fail_post=429)
        c = self.client(fake)
        with self.assertRaises(CannotWrite):
            c.request("POST", "/v1/listings", body={"listings": [{"id": LID, "pms": PMS, "min": 1}]})
        self.assertEqual(len(fake.writes()), 1)

    def test_error_text_never_carries_the_body(self):
        fake = FakePriceLabs(fail_post=400)
        c = self.client(fake)
        with self.assertRaises(CannotWrite) as ctx:
            c.request("POST", "/v1/listings", body={"listings": [{"id": LID, "pms": PMS, "min": 1}]})
        self.assertNotIn("synthetic-key", str(ctx.exception))


class CLI(unittest.TestCase):
    """The command surface the desktop app drives, run as a subprocess against no network."""

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ, RC_CACHE_DIR=self.tmp.name, PRICELABS_API_KEY="synthetic-key")
        env.update(env_extra or {})
        here = Path(__file__).resolve().parent
        return subprocess.run([sys.executable, "-B", str(here / "apply_change.py"), *args],
                              capture_output=True, text=True, env=env, cwd=self.tmp.name, timeout=60)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_with_an_unknown_plan_exits_2(self):
        r = self.run_cli("apply", "--plan", "0123456789ab")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no saved plan", r.stderr)

    def test_apply_takes_no_code_flag(self):
        r = self.run_cli("apply", "--plan", "0123456789ab", "--code", "APPROVE 0123456789ab")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unrecognized arguments: --code", r.stderr)

    def test_every_plan_id_is_checked_before_anything_is_sent(self):
        # the second id is unknown, so the batch is refused before the first is applied
        state = Path(self.tmp.name) / "writes" / "plans"
        state.mkdir(parents=True)
        r = self.run_cli("apply", "--plan", "0123456789ab", "--plan", "ba9876543210")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no saved plan", r.stderr)

    def test_bad_change_file_exits_2(self):
        bad = Path(self.tmp.name) / "c.json"
        bad.write_text("{not json")
        r = self.run_cli("plan", "--change", str(bad))
        self.assertEqual(r.returncode, 2, r.stderr)


# ------------------------------------------------------------------ audit 2026-09-25 items 4-10

def fail_on(fake, method, path_suffix, code=500, after=None):
    """Make one route raise an HTTP error (optionally only after another call was seen)."""
    real_open = fake.open

    def opener(req, timeout):
        seen = [r for r in fake.requests if after and r[0] == after[0] and r[1] == after[1]]
        if req.get_method() == method and urlsplit(req.full_url).path.endswith(path_suffix) \
                and (after is None or seen):
            fake.requests.append((method, urlsplit(req.full_url).path, None))
            raise HTTPError(req.full_url, code, "boom", {}, io.BytesIO(b"{}"))
        return real_open(req, timeout)
    fake.open = opener


class OverrideBounds(Base):
    """Bug 1: a night price is never written below the listing min."""

    def test_fixed_override_below_min_is_refused_in_plain_words(self):
        with self.assertRaisesRegex(CannotWrite, r"that night would be \$140\.00, below your min of \$150\.00"):
            self.plan(FakePriceLabs(), change(overrides_set=[
                {"date": "2026-10-05", "price": 140, "price_type": "fixed"}]))

    def test_fixed_override_below_the_min_this_change_sets_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, r"below your min of \$170\.00"):
            self.plan(FakePriceLabs(), change(listing_prices={"min": 170}, overrides_set=[
                {"date": "2026-10-05", "price": 165, "price_type": "fixed"}]))

    def test_fixed_override_above_max_is_a_loud_warning(self):
        fake = FakePriceLabs()
        fake.prices["2026-10-05"] = 400.0
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 420, "price_type": "fixed"}]))
        self.assertTrue(any("ABOVE YOUR MAX" in w and "400.00" in w for w in env["warnings"]),
                        env["warnings"])

    def test_percent_override_that_lands_below_min_is_refused(self):
        # 2026-10-01 is a $160 night; -10% = $144, below the $150 min
        with self.assertRaisesRegex(CannotWrite, r"that night would be \$144\.00, below your min"):
            self.plan(FakePriceLabs(), change(overrides_set=[
                {"date": "2026-10-01", "price": -10, "price_type": "percent"}]))

    def test_percent_override_with_no_known_night_price_warns(self):
        fake = FakePriceLabs()
        del fake.prices["2026-10-05"]
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": -10, "price_type": "percent"}]))
        self.assertTrue(any("could not check" in w and "2026-10-05" in w for w in env["warnings"]),
                        env["warnings"])


class ApplyBounds(Base):
    """Bug 2: min <= base <= max is re-checked at apply against live, across the batch."""

    def test_ui_edit_between_plan_and_yes_is_caught(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 190}))
        fake.listing["base"] = 180.0  # someone lowered base in the PriceLabs UI
        with self.assertRaisesRegex(CannotWrite, "min <= base <= max"):
            self.apply(fake, env)
        self.assertEqual(fake.writes(), [])

    def test_two_plans_that_are_fine_alone_but_not_together_send_nothing(self):
        fake = FakePriceLabs()
        a = self.plan(fake, change(listing_prices={"min": 190}))
        b = self.plan(fake, change(listing_prices={"base": 185}))
        with self.assertRaisesRegex(CannotWrite, "min <= base <= max"):
            apply_batch([a, b], lambda lid, pms: live_for(fake), state_dir=self.state,
                        today=TODAY, now=LATER)
        self.assertEqual(fake.writes(), [])


class UndoAfterPartialWrite(Base):
    """Bug 3: undo is built from live state; items already back are skipped, not fatal."""

    def test_undo_after_the_override_post_failed(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}, overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        fail_on(fake, "POST", "/overrides")
        with self.assertRaises(CannotWrite):
            self.apply(fake, env)
        journal = self.journal_on_disk()
        self.assertEqual(journal["status"], "sent-unverified")
        self.assertEqual(fake.listing["min"], 170.0)  # the min DID land
        back = self.plan_undo(fake, journal)
        self.assertEqual([op.get("field") for op in back["operations"]], ["min"])
        self.assertTrue(any("1 item already back to before" in w for w in back["warnings"]),
                        back["warnings"])
        fake.open = FakePriceLabs.open.__get__(fake)
        self.assertEqual(self.apply(fake, back)["status"], "verified")
        self.assertEqual(fake.listing["min"], 150.0)

    def test_undo_when_everything_is_already_back_says_so(self):
        fake = FakePriceLabs()
        journal = self.apply(fake, self.plan(fake, change(listing_prices={"min": 170})))
        fake.listing["min"] = 150.0
        with self.assertRaisesRegex(CannotWrite, "already back to before"):
            self.plan_undo(fake, journal)


class JournalAlwaysWritten(Base):
    """Bug 4: a failed re-read after a send still leaves a journal and an undo command."""

    def test_reread_failure_after_send_writes_the_journal(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        fail_on(fake, "GET", f"/v1/listings/{LID}", code=503, after=("POST", "/v1/listings"))
        with self.assertRaises(CannotWrite) as ctx:
            self.apply(fake, env)
        msg = str(ctx.exception)
        journal = self.journal_on_disk()
        self.assertEqual(journal["status"], "sent-unverified")
        self.assertIn("SENT", msg)
        self.assertIn(Path(journal["journal_path"]).name, msg)
        self.assertIn(f"apply_change.py rollback --journal {Path(journal['journal_path']).name}", msg)
        self.assertEqual(fake.listing["min"], 170.0)


class RestoreOnlyFromUndo(Base):
    """Bug 5: overrides_restore is internal to undo; a change file cannot carry it."""

    def test_change_file_with_overrides_restore_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "overrides_restore"):
            self.plan(FakePriceLabs(), change(overrides_restore=[
                {"date": "2026-10-05", "price": "1", "price_type": "fixed", "currency": "USD"}]))

    def test_undo_restore_still_checks_currency(self):
        fake = FakePriceLabs()
        journal = self.apply(fake, self.plan(fake, change(overrides_delete=["2026-10-10"])))
        journal["envelope"]["operations"][0]["before"]["currency"] = "USD"
        with self.assertRaisesRegex(CannotWrite, "currency"):
            self.plan_undo(fake, journal)

    def test_snapshot_file_is_an_undo_source(self):
        fake = FakePriceLabs()
        journal = self.apply(fake, self.plan(fake, change(overrides_delete=["2026-10-10"])))
        snap = json.loads(Path(journal["snapshot_path"]).read_text())
        back = plan_change(rollback_change(snap), live_for(fake), today=TODAY, now=LATER,
                           rollback=True)
        self.assertEqual(self.apply(fake, back)["status"], "verified")
        self.assertEqual(fake.overrides["2026-10-10"]["price"], "250")

    def test_cli_plan_refuses_a_change_file_with_overrides_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "c.json"
            f.write_text(json.dumps(change(overrides_restore=[{"date": "2026-10-05", "price": "1"}])))
            env = dict(os.environ, RC_CACHE_DIR=tmp, PRICELABS_API_KEY="synthetic-key")
            here = Path(__file__).resolve().parent
            r = subprocess.run([sys.executable, "-B", str(here / "apply_change.py"), "plan", "--change",
                                str(f)], capture_output=True, text=True, env=env, cwd=tmp, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("overrides_restore", r.stderr)


    def test_cli_undo_refuses_a_file_outside_the_writers_own_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "fake-journal.json"
            f.write_text(json.dumps(change(overrides_restore=[{"date": "2026-10-05", "price": "1"}])))
            env = dict(os.environ, RC_CACHE_DIR=tmp, PRICELABS_API_KEY="synthetic-key")
            here = Path(__file__).resolve().parent
            r = subprocess.run([sys.executable, "-B", str(here / "apply_change.py"), "rollback",
                                "--journal", str(f)], capture_output=True, text=True, env=env,
                               cwd=tmp, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a journal or snapshot this writer saved", r.stderr)

class OddPostResponse(Base):
    """Bug 6: a non-dict row in the POST response goes to the re-read, never a traceback."""

    def odd_response(self, fake):
        real_open = fake.open

        def opener(req, timeout):
            resp = real_open(req, timeout)
            if req.get_method() == "POST" and urlsplit(req.full_url).path == "/v1/listings":
                return Response({"listings": ["ok"]})
            return resp
        fake.open = opener

    def test_odd_row_but_the_write_landed_is_verified_by_the_reread(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.odd_response(fake)
        self.assertEqual(self.apply(fake, env)["status"], "verified")

    def test_odd_row_and_the_write_did_not_land_is_unverified(self):
        fake = FakePriceLabs(silent_noop=True)
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.odd_response(fake)
        with self.assertRaisesRegex(CannotWrite, "SENT"):
            self.apply(fake, env)
        self.assertEqual(self.journal_on_disk()["status"], "sent-unverified")

    def test_cli_turns_any_exception_into_cannot_write(self):
        import contextlib
        import apply_change
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RC_CACHE_DIR")
            os.environ["RC_CACHE_DIR"] = tmp
            orig = (apply_change.load_plan, apply_change.apply_batch)
            apply_change.load_plan = lambda state, pid: {"target": {}}

            def boom(*a, **k):
                raise RuntimeError("row 0 is a str")
            apply_change.apply_batch = boom
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    rc = apply_change.main(["apply", "--plan", "0123456789ab", "--no-audit"])
            finally:
                apply_change.load_plan, apply_change.apply_batch = orig
                if old is None:
                    os.environ.pop("RC_CACHE_DIR", None)
                else:
                    os.environ["RC_CACHE_DIR"] = old
        self.assertEqual(rc, 2)
        self.assertIn("CANNOT WRITE", err.getvalue())
        self.assertIn("journal", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class UndoPastDates(Base):
    """Bug 7: past dates drop out of an undo instead of blocking it."""

    def test_undo_drops_dates_that_are_now_past(self):
        fake = FakePriceLabs()
        journal = self.apply(fake, self.plan(fake, change(listing_prices={"min": 170}, overrides_set=[
            {"date": "2026-10-03", "price": 230, "price_type": "fixed"}])))
        later = date(2026, 10, 5)
        back = self.plan_undo(fake, journal, today=later)
        self.assertEqual([op.get("field") for op in back["operations"]], ["min"])
        self.assertTrue(any("2026-10-03" in w and "past" in w for w in back["warnings"]),
                        back["warnings"])

    def test_a_normal_change_with_a_past_date_is_still_refused(self):
        with self.assertRaisesRegex(CannotWrite, "past"):
            self.plan(FakePriceLabs(), change(overrides_set=[
                {"date": "2026-09-30", "price": 200, "price_type": "fixed"}]))


class PlanExpiry(Base):
    """Bug 8: a plan older than 24h, or with a date now past, is refused at apply."""

    def test_plan_older_than_24h_is_refused(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        with self.assertRaisesRegex(CannotWrite, "plan again for fresh numbers"):
            self.apply(fake, env, now=datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(fake.writes(), [])

    def test_plan_whose_date_is_now_past_is_refused(self):
        fake = FakePriceLabs()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-01", "price": 200, "price_type": "fixed"}]))
        with self.assertRaisesRegex(CannotWrite, "plan again for fresh numbers"):
            self.apply(fake, env, today=date(2026, 10, 2))
        self.assertEqual(fake.writes(), [])


class RoundingAndDuplicates(Base):
    def test_min_that_rounds_to_zero_is_refused(self):  # bug 9
        with self.assertRaisesRegex(CannotWrite, "above zero"):
            self.plan(FakePriceLabs(), change(listing_prices={"min": 0.004}))

    def test_fixed_to_fixed_over_15_is_flagged_once(self):  # bug 10
        # the min raise makes the plan read the calendar, which is where the second copy came from
        env = self.plan(FakePriceLabs(), change(listing_prices={"min": 155}, overrides_set=[
            {"date": "2026-10-10", "price": 300, "price_type": "fixed"}]))
        flags = [w for w in env["warnings"] if "OVER 15%" in w]
        self.assertEqual(len(flags), 1, env["warnings"])


class NightMove(Base):
    """Bug 11: the flag measures the night, and honours max_delta_pct."""

    def test_min_raise_that_lifts_a_night_more_than_15_is_flagged(self):
        fake = FakePriceLabs()
        fake.prices["2026-10-02"] = 100.0
        env = self.plan(fake, change(listing_prices={"min": 165}))  # +10% on the field
        flags = [w for w in env["warnings"] if "OVER 15%" in w]
        self.assertTrue(any("2026-10-02" in w and "+65.0%" in w for w in flags), env["warnings"])

    def test_small_night_lift_is_not_flagged(self):
        env = self.plan(FakePriceLabs(), change(listing_prices={"min": 170}))  # 160 -> 170
        self.assertFalse(any("OVER" in w for w in env["warnings"]), env["warnings"])
        self.assertTrue(any("+6.2%" in w for w in env["warnings"]), env["warnings"])

    def test_max_delta_accepts_15_or_0_15(self):
        for md in (15, 0.15):
            with self.subTest(md=md):
                over = self.plan_md(change(listing_prices={"base": 240}), md)
                at = self.plan_md(change(listing_prices={"base": 230}), md)
                self.assertTrue(any("OVER 15%" in w for w in over["warnings"]))
                self.assertFalse(any("OVER" in w for w in at["warnings"]))

    def test_max_delta_from_property_config_is_used(self):
        env = self.plan_md(change(listing_prices={"base": 225}), 10)  # +12.5%
        self.assertTrue(any("OVER 10%" in w for w in env["warnings"]), env["warnings"])

    def plan_md(self, spec, md):
        return plan_change(spec, live_for(FakePriceLabs()), today=TODAY, now=NOW, max_delta=md)


class ZeroNight(Base):
    def test_zero_priced_night_is_a_clear_refusal(self):  # bug 12
        fake = FakePriceLabs()
        fake.prices["2026-10-05"] = 0.0
        with self.assertRaisesRegex(CannotWrite, r"\$0"):
            self.plan(fake, change(overrides_set=[
                {"date": "2026-10-05", "price": 240, "price_type": "fixed"}]))


if __name__ == "__main__":
    unittest.main()


# live 2026-09-25 ---------------------------------------------- the PriceLabs PMS name, said plainly
class PriceLabsPmsName(Base):
    def test_change_file_with_the_pms_own_name_is_refused_with_the_fix(self):
        # Real PriceLabs files a Hospitable listing under "smartbnb". A change file copied from
        # --help said "hospitable" and got "PriceLabs returned a different listing or PMS".
        fake = FakePriceLabs()
        fake.listing["pms"] = "smartbnb"
        with self.assertRaisesRegex(CannotWrite, "under pms 'smartbnb'.*set \"pms\": \"smartbnb\""):
            self.plan(fake, change(listing_prices={"min": 160}))
