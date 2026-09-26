"""Offline contracts for the Beyond writer (docs/WRITE-TARGETS.md, all 8 guarantees).

Every test runs against FakeBeyond, a stateful stand-in built from Beyond's documented
behaviour (references/beyond.md): JSON:API documents, trailing-slash paths (301 without),
additive manual-overrides PATCH, a fixed override accepted below the floor, max-price null
as "no ceiling", whole-unit prices. It can also be told to misbehave the ways a live API does
under HTTP 200: a silent no-op, a replace-instead-of-merge, a side effect on a rule nobody
asked to change, an unreadable reply. Nothing here touches a network. DOCS-ONLY: the fake is
our reading of the docs, not a recording of a live account.
"""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import _beyond as B
from _beyond_write import (
    CannotWrite, Live, WriteClient, apply_batch, apply_envelope, content_hash, describe, live_for,
    load_plan, plan_change, plan_id, rollback_change, save_envelope, send_order,
)

LID = "48213"
TOKEN = "bpat_synthetic"      # short on purpose: the prepublish scan flags real-length tokens
TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)
PREFIX = f"/api/v1/listings/{LID}/"
WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SECRET = "SECRET-ECHO client credentials"


class Response:
    def __init__(self, body, status=200):
        self.body = b"" if body is None else json.dumps(body).encode()
        self.status = status
        self.headers = {"Content-Type": B.MEDIA}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


def http_error(url, code):
    return HTTPError(url, code, "x", {}, io.BytesIO(json.dumps(
        {"errors": [{"status": str(code), "detail": SECRET, "code": "error"}]}).encode()))


class FakeBeyond:
    """One listing, its customizations, overrides and a modeled calendar."""

    def __init__(self, *, silent_noop=False, side_effect=None, fail=None, reply=None,
                 replace_overrides=False, enforce_bounds=True, in_active_market=True, enabled=True,
                 currency="USD", max_price=400, reads_fail_after_patch=False, empty_reread=False):
        self.cust = {
            "base-price": {"base-price": 200},
            "min-max-prices": {
                "min-price": 150, "max-price": max_price, "monthly-min-price": None,
                "day-of-week-min-prices": [{"weekday": w, "min-price": None} for w in WEEK],
                "seasonal-day-of-week-min-prices": [],
                "seasonal-prices": [{"start-date": "2026-12-20", "end-date": "2027-01-05",
                                     "rollover": True, "min-price": 250, "max-price": None}],
                "seasonal-monthly-prices": []},
            "min-stays": {
                "min-stay": 2, "gap-fill-min-stay": {"enabled": True, "gaps": 3, "buffer": 1},
                "seasonal-min-stays": [], "last-minute-min-stays": [],
                "day-of-week-min-stays": [{"weekday": w, "min-stay": None} for w in WEEK],
                "seasonal-day-of-week-min-stays": [], "seasonal-gap-fill-min-stays": [],
                "seasonal-time-based-min-stays": []},
            "extra-guest-fees": {"extra-guest-fee": 25, "extra-guest-threshold": 4},
            "time-based-adjustments": {
                "dynamic-time-based-adjustments": {"enabled": True, "tier": "revenue"},
                "time-based-adjustments": [], "seasonal-time-based-adjustments": []},
        }
        self.overrides = {"2026-10-10": {"price": 250, "percentage-adjustment": None},
                          "2026-11-20": {"price": None, "percentage-adjustment": -10}}
        self.modeled = {(TODAY + timedelta(days=i)).isoformat(): 160 if i % 10 < 3 else 220
                        for i in range(800)}
        self.currency, self.enabled, self.in_active_market = currency, enabled, in_active_market
        self.silent_noop, self.side_effect, self.fail, self.reply = silent_noop, side_effect, fail, reply
        self.replace_overrides, self.enforce_bounds = replace_overrides, enforce_bounds
        self.reads_fail_after_patch, self.empty_reread = reads_fail_after_patch, empty_reread
        self.requests, self.timeouts, self.patched = [], [], False

    # -------------------------------------------------------------- views
    def patches(self):
        return [r for r in self.requests if r[0] != "GET"]

    def floor(self, d):
        mm = self.cust["min-max-prices"]
        eff = mm["min-price"]
        for s in mm["seasonal-prices"]:
            if s["start-date"] <= d <= s["end-date"] and s.get("min-price"):
                eff = max(eff, s["min-price"])
        return eff

    def entry(self, d):
        mm = self.cust["min-max-prices"]
        modeled = max(self.modeled[d], self.floor(d))
        if mm["max-price"] is not None:
            modeled = min(modeled, mm["max-price"])
        ov, kind = self.overrides.get(d), None
        price = modeled
        if ov and ov["price"] is not None:
            price, kind = ov["price"], "fixed"
        elif ov and ov["percentage-adjustment"] is not None:
            price, kind = round(modeled * (1 + ov["percentage-adjustment"] / 100)), "percentage"
        return {"type": "calendar-entries", "id": d, "attributes": {
            "date": d, "availability": "available", "price": int(price), "price-posted": int(price),
            "effective-min-price": int(self.floor(d)),
            "effective-max-price": mm["max-price"] and int(mm["max-price"]),
            "price-override-type": kind, "factors": [{"key": "seasonality", "order": 1,
                                                      "ratio": 0.1, "amount": 10}]}}

    def listing_doc(self):
        mm = self.cust["min-max-prices"]
        return {"data": {"type": "listings", "id": int(LID), "attributes": {
            "title": "Test Beyond Listing", "currency": self.currency,
            "timezone": "America/Los_Angeles", "enabled": self.enabled,
            "in-active-market": self.in_active_market,
            "base-price": self.cust["base-price"]["base-price"], "min-price": mm["min-price"],
            "max-price": mm["max-price"], "min-stay": self.cust["min-stays"]["min-stay"],
            "bedrooms": 2, "channel-listings": [{"channel": "airbnb", "channel-id": "777"}],
            "sync-status": {"state": "completed", "last-successful-sync-at": "2026-10-01T10:00:00Z"}}}}

    # -------------------------------------------------------------- the API
    def open(self, req, timeout):
        self.timeouts.append(timeout)
        url = urlsplit(req.full_url)
        method, path, q = req.get_method(), url.path, parse_qs(url.query)
        body = json.loads(req.data) if req.data else None
        self.requests.append((method, path, q, body))
        assert url.netloc == B.HOST, url.netloc
        assert req.get_header("Authorization") == f"Bearer {TOKEN}", "bearer token missing"
        assert req.get_header("Accept") == B.MEDIA, "JSON:API Accept header missing"
        if not path.endswith("/"):
            raise http_error(req.full_url, 301)  # documented: no trailing slash answers 301
        if method == "GET" and self.patched and self.reads_fail_after_patch:
            raise http_error(req.full_url, 503)
        if method == "GET" and path == PREFIX:
            return Response(self.listing_doc())
        if method == "GET" and path == PREFIX + "customizations/":
            return Response({"data": {"type": "listing-customizations", "id": LID,
                                      "attributes": copy.deepcopy(self.cust)}})
        if method == "GET" and path == PREFIX + "customizations/manual-overrides/":
            start = q.get("filter[start-date]", [TODAY.isoformat()])[0]
            end = q.get("filter[end-date]", [(TODAY + timedelta(days=365)).isoformat()])[0]
            rows = [] if (self.patched and self.empty_reread) else [
                {"start-date": d, "end-date": d, "price": v["price"],
                 "percentage-adjustment": v["percentage-adjustment"]}
                for d, v in sorted(self.overrides.items()) if start <= d <= end]
            return Response({"data": {"type": "manual-override-customizations", "id": LID,
                                      "attributes": {"overrides": rows}}})
        if method == "GET" and path == PREFIX + "calendar/":
            # documented: any other filter name is ignored and the default window returned
            start = q.get("filter[start-date]", [TODAY.isoformat()])[0]
            end = q.get("filter[end-date]", [(TODAY + timedelta(days=365)).isoformat()])[0]
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            rows = [self.entry((date.fromisoformat(start) + timedelta(days=i)).isoformat())
                    for i in range(days)]
            return Response({"data": rows, "meta": {"pagination": {"page": 1, "pages": 1,
                                                                   "count": len(rows)}}})
        if method == "PATCH":
            return self.patch(req, path, body)
        raise AssertionError(f"FakeBeyond has no route for {method} {path}")

    def patch(self, req, path, body):
        assert req.get_header("Content-type") == B.MEDIA, "JSON:API Content-Type missing"
        suffix = path[len(PREFIX):]
        if self.fail and self.fail[0] in (suffix, "*"):
            self.patched = True  # a failed call may still have landed; here it did not
            raise http_error(req.full_url, self.fail[1])
        types = {"customizations/base-price/": "base-price-customizations",
                 "customizations/min-max-prices/": "min-max-price-customizations",
                 "customizations/min-stays/": "min-stay-customizations",
                 "customizations/manual-overrides/": "manual-override-customizations"}
        doc = body.get("data") or {}
        if doc.get("type") != types.get(suffix) or doc.get("id") != LID:
            raise http_error(req.full_url, 409)  # documented: wrong data.type is a 409
        attrs = doc["attributes"]
        before = copy.deepcopy(self.cust)
        if suffix == "customizations/base-price/":
            assert set(attrs) == {"base-price"} and type(attrs["base-price"]) is int, attrs
            if attrs["base-price"] < 10:
                raise http_error(req.full_url, 400)
            if not self.silent_noop:
                self.cust["base-price"]["base-price"] = attrs["base-price"]
        elif suffix == "customizations/min-max-prices/":
            assert set(attrs) <= {"min-price", "max-price"} and attrs, attrs
            if not self.silent_noop:
                self.cust["min-max-prices"].update(attrs)
        elif suffix == "customizations/min-stays/":
            assert set(attrs) == {"min-stay"}, attrs
            if not self.silent_noop:
                self.cust["min-stays"]["min-stay"] = attrs["min-stay"]
        else:
            if self.replace_overrides and not self.silent_noop:
                self.overrides = {}
            for row in attrs["overrides"]:
                assert row["start-date"] == row["end-date"] and "days-of-week" not in row, row
                assert not ("price" in row and "percentage-adjustment" in row), row
                if row["start-date"] < TODAY.isoformat():
                    raise http_error(req.full_url, 422)  # documented: past dates rejected
                if self.silent_noop:
                    continue
                if row.get("price") is not None:
                    self.overrides[row["start-date"]] = {"price": row["price"],
                                                         "percentage-adjustment": None}
                elif row.get("percentage-adjustment") is not None:
                    self.overrides[row["start-date"]] = {
                        "price": None, "percentage-adjustment": row["percentage-adjustment"]}
                else:
                    self.overrides.pop(row["start-date"], None)
        if self.enforce_bounds:
            mm, base = self.cust["min-max-prices"], self.cust["base-price"]["base-price"]
            if not (mm["min-price"] <= base and (mm["max-price"] is None or base <= mm["max-price"])):
                self.cust = before
                raise http_error(req.full_url, 422)
        self.patched = True
        if self.side_effect:
            self.side_effect(self)
        if self.reply is not None:
            return Response(self.reply)
        return Response({"data": {"type": types[suffix], "id": LID, "attributes": attrs}})


def live(fake):
    return Live(WriteClient(TOKEN, LID, opener=fake), LID)


def change(**parts):
    base = {"listing_id": LID, "reason": "test reason"}
    base.update(parts)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, fake, spec, now=NOW, **kw):
        return plan_change(spec, live(fake), today=TODAY, now=now, **kw)

    def apply(self, fake, env, now=LATER, today=TODAY):
        return apply_envelope(env, live(fake), state_dir=self.state, today=today, now=now)

    def undo(self, fake, journal, today=TODAY, now=LATER):
        return plan_change(rollback_change(journal), live(fake), today=today, now=now, rollback=True)

    def journal_on_disk(self):
        files = sorted(self.state.glob("journal/*.json"))
        self.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text())

    def refused(self, fn, *words):
        with self.assertRaises(CannotWrite) as ctx:
            fn()
        for w in words:
            self.assertIn(w, str(ctx.exception))
        return str(ctx.exception)


# ------------------------------------------------------------------ 1. fresh read, before/after

class FreshPlan(Base):
    def test_plan_reads_live_and_sends_nothing(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.assertEqual(fake.patches(), [])
        self.assertEqual(env["operations"], [{"kind": "listing_price", "field": "min",
                                              "before": 150.0, "after": 170.0}])
        self.assertEqual(env["target"], {"listing_id": LID, "pms": "beyond", "currency": "USD"})

    def test_every_plan_reads_live_never_cache(self):
        fake = FakeBeyond()
        self.plan(fake, change(listing_prices={"min": 170}))
        n = len(fake.requests)
        fake.cust["min-max-prices"]["min-price"] = 140
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.assertGreater(len(fake.requests), n)
        self.assertEqual(env["operations"][0]["before"], 140.0)

    def test_card_shows_before_and_after_for_every_date_and_the_first_live_notice(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"},
            {"date": "2026-10-10", "price": 5, "price_type": "percent"}]))
        card = describe(env)
        self.assertIn("2026-10-05: no override  ->  fixed 230.00", card)
        self.assertIn("2026-10-10: fixed 250.00  ->  +5% on Beyond's price", card)
        self.assertIn("First live write for Beyond", card)
        self.assertIn(f"Plan {plan_id(env)}", card)
        self.assertNotIn("CODE", card.upper().replace("DECODE", ""))

    def test_max_null_reads_as_no_ceiling(self):
        fake = FakeBeyond(max_price=None)
        env = self.plan(fake, change(listing_prices={"base": 450}))
        self.assertEqual(env["operations"][0]["after"], 450.0)
        card = describe(self.plan(fake, change(listing_prices={"max": 500})))
        self.assertIn("max: no ceiling -> 500.00", card)
        self.assertIn("ADDS A CEILING", card)


# ------------------------------------------------------------------ 2. floor and the 15% flag

class FloorAndFlag(Base):
    def test_fixed_override_below_min_is_refused(self):
        self.refused(lambda: self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2026-10-05", "price": 140, "price_type": "fixed"}])), "below your min of 150.00",
            "Beyond accepts an override under the floor")

    def test_fixed_override_below_the_min_this_change_sets_is_refused(self):
        self.refused(lambda: self.plan(FakeBeyond(), change(
            listing_prices={"min": 180},
            overrides_set=[{"date": "2026-10-05", "price": 175, "price_type": "fixed"}])),
            "below your min of 180.00")

    def test_fixed_override_below_a_seasonal_floor_is_refused(self):
        self.refused(lambda: self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2026-12-24", "price": 200, "price_type": "fixed"}])), "that night's own floor",
            "250.00")

    def test_percent_that_lands_below_min_is_refused(self):
        # 2026-10-02 is modeled at 160: -10% is 144, under the 150 floor
        self.refused(lambda: self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2026-10-02", "price": -10, "price_type": "percent"}])), "144.00", "below your min")

    def test_percent_on_a_fixed_night_cannot_be_checked_and_says_so(self):
        env = self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2026-10-10", "price": 5, "price_type": "percent"}]))
        self.assertTrue(any("could not be checked against your min" in w for w in env["warnings"]))

    def test_fixed_override_above_max_is_a_loud_warning(self):
        env = self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2026-10-05", "price": 450, "price_type": "fixed"}]))
        self.assertTrue(any(w.startswith("ABOVE YOUR MAX") for w in env["warnings"]))

    def test_over_15_percent_is_flagged_and_exactly_15_is_not(self):
        flagged = self.plan(FakeBeyond(), change(listing_prices={"base": 240}))  # +20%
        self.assertTrue(any(w.startswith("OVER 15%") for w in flagged["warnings"]))
        exact = self.plan(FakeBeyond(), change(listing_prices={"base": 230}))  # +15%
        self.assertFalse(any(w.startswith("OVER") for w in exact["warnings"]))

    def test_override_far_from_tonights_price_is_flagged(self):
        env = self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2026-10-05", "price": 300, "price_type": "fixed"}]))  # 220 -> 300
        self.assertTrue(any(w.startswith("OVER 15%") and "2026-10-05" in w for w in env["warnings"]))

    def test_max_delta_accepts_15_or_0_15_and_a_property_setting(self):
        a = self.plan(FakeBeyond(), change(listing_prices={"base": 220}), max_delta_pct=5)
        b = self.plan(FakeBeyond(), change(listing_prices={"base": 220}), max_delta=0.05)
        self.assertTrue(any(w.startswith("OVER 5%") for w in a["warnings"]))
        self.assertEqual(a["warnings"], b["warnings"])

    def test_min_raise_counts_nights_lifted_and_names_overrides_it_will_not_lift(self):
        fake = FakeBeyond()
        fake.overrides["2026-10-12"] = {"price": 165, "percentage-adjustment": None}
        env = self.plan(fake, change(listing_prices={"min": 170}))
        text = "\n".join(env["warnings"])
        self.assertIn("nights are priced below the new min 170.00 today and will be lifted to it", text)
        self.assertIn("NOT lifted to it: 2026-10-12", text)

    def test_zero_priced_night_is_a_clear_refusal(self):
        fake = FakeBeyond(enforce_bounds=False)
        fake.overrides["2026-10-06"] = {"price": 0, "percentage-adjustment": None}
        fake.modeled["2026-10-06"] = 0
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-06", "price": 200, "price_type": "fixed"}])), "shows 0")


# ------------------------------------------------------------------ 3. hashing, expiry, past dates

class PlanIdAndExpiry(Base):
    def test_rebuilt_plan_keeps_its_id_and_one_changed_field_gets_a_new_one(self):
        fake = FakeBeyond()
        a = self.plan(fake, change(listing_prices={"min": 170}), now=NOW)
        b = self.plan(fake, change(listing_prices={"min": 170}), now=LATER)
        c = self.plan(fake, change(listing_prices={"min": 171}))
        self.assertEqual(plan_id(a), plan_id(b))
        self.assertNotEqual(plan_id(a), plan_id(c))

    def test_id_covers_the_before_image(self):
        fake = FakeBeyond()
        a = self.plan(fake, change(listing_prices={"min": 170}))
        fake.cust["min-max-prices"]["min-price"] = 140
        self.assertNotEqual(plan_id(a), plan_id(self.plan(fake, change(listing_prices={"min": 170}))))

    def test_plan_edited_on_disk_is_refused(self):
        env = self.plan(FakeBeyond(), change(listing_prices={"min": 170}))
        path = Path(save_envelope(env, self.state))
        data = json.loads(path.read_text())
        data["operations"][0]["after"] = 100
        path.write_text(json.dumps(data))
        self.refused(lambda: load_plan(self.state, plan_id(env)), "edited after it was shown")

    def test_plan_older_than_24h_is_refused_and_sends_nothing(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.refused(lambda: self.apply(fake, env, now=NOW + timedelta(hours=25)), "more than 24")
        self.assertEqual(fake.patches(), [])

    def test_past_dates_are_refused_at_plan_and_at_apply(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-09-30", "price": 200, "price_type": "fixed"}])), "in the past")
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-02", "price": 200, "price_type": "fixed"}]))
        self.refused(lambda: self.apply(fake, env, today=date(2026, 10, 3),
                                        now=datetime(2026, 10, 2, 11, tzinfo=timezone.utc)),
                     "in the past now")
        self.assertEqual(fake.patches(), [])

    def test_a_date_past_the_override_read_window_is_refused(self):
        self.refused(lambda: self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2027-10-05", "price": 200, "price_type": "fixed"}])), "a year ahead")


# ------------------------------------------------------------------ 4. drift

class Drift(Base):
    def test_listing_price_drift_refuses_and_sends_nothing(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        fake.cust["min-max-prices"]["min-price"] = 160
        self.refused(lambda: self.apply(fake, env), "min changed since the plan")
        self.assertEqual(fake.patches(), [])
        self.assertEqual(list(self.state.glob("snapshots/*")), [])

    def test_override_drift_refuses_and_sends_nothing(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        fake.overrides["2026-10-05"] = {"price": 240, "percentage-adjustment": None}
        self.refused(lambda: self.apply(fake, env), "override on 2026-10-05 changed")
        self.assertEqual(fake.patches(), [])

    def test_a_seasonal_floor_edited_since_the_plan_is_drift(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-12-24", "price": 260, "price_type": "fixed"}]))
        fake.cust["min-max-prices"]["seasonal-prices"][0]["min-price"] = 280
        self.refused(lambda: self.apply(fake, env), "price rule in Beyond", "changed since the plan")
        self.assertEqual(fake.patches(), [])

    def test_currency_change_is_drift(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        fake.currency = "CAD"
        self.refused(lambda: self.apply(fake, env), "currency changed")

    def test_min_stay_drift(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(listing_min_stay=3))
        fake.cust["min-stays"]["min-stay"] = 1
        self.refused(lambda: self.apply(fake, env), "min stay changed")


# ------------------------------------------------------------------ 5. snapshot first, journal always

class SnapshotAndJournal(Base):
    def test_snapshot_is_on_disk_before_the_first_send(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(listing_prices={"min": 170}))
        seen = {}
        orig = fake.patch

        def spy(req, path, body):
            seen["snapshots"] = list(self.state.glob("snapshots/*.json"))
            return orig(req, path, body)
        fake.patch = spy
        self.apply(fake, env)
        self.assertEqual(len(seen["snapshots"]), 1)
        snap = json.loads(seen["snapshots"][0].read_text())
        self.assertEqual(snap["listing_prices"], {"min": 150.0})

    def test_a_failed_send_writes_the_journal_and_is_never_retried(self):
        fake = FakeBeyond(fail=("*", 500))
        env = self.plan(fake, change(listing_prices={"min": 170}))
        msg = self.refused(lambda: self.apply(fake, env), "SENT but did not take", "HTTP 500")
        self.assertNotIn(SECRET, msg)
        self.assertEqual(len(fake.patches()), 1)
        j = self.journal_on_disk()
        self.assertEqual(j["status"], "sent-unverified")
        self.assertTrue(Path(j["snapshot_path"]).is_file())

    def test_reread_failure_after_send_still_writes_the_journal(self):
        fake = FakeBeyond(reads_fail_after_patch=True)
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.refused(lambda: self.apply(fake, env), "could not be verified")
        self.assertEqual(self.journal_on_disk()["status"], "sent-unverified")

    def test_journal_and_snapshot_never_hold_the_token(self):
        fake = FakeBeyond()
        self.apply(fake, self.plan(fake, change(listing_prices={"min": 170})))
        for p in self.state.rglob("*.json"):
            self.assertNotIn(TOKEN, p.read_text())


# ------------------------------------------------------------------ 6. transport

class Transport(unittest.TestCase):
    def test_refuses_every_call_it_was_not_built_for(self):
        fake = FakeBeyond()
        client = WriteClient(TOKEN, LID, opener=fake)
        for method, path in (("PATCH", PREFIX + "activation/"), ("POST", PREFIX + "refresh/"),
                             ("PATCH", PREFIX + "customizations/time-based-adjustments/"),
                             ("PATCH", PREFIX + "customizations/extra-guest-fees/"),
                             ("GET", "/api/v1/users/"), ("PATCH", "/api/v1/listings/99999/customizations/base-price/"),
                             ("PATCH", PREFIX + "customizations/base-price"),
                             ("PATCH", PREFIX + "customizations/../activation/"),
                             ("DELETE", PREFIX + "customizations/manual-overrides/")):
            with self.assertRaises(CannotWrite, msg=f"{method} {path}"):
                client.request(method, path, body={})
        self.assertEqual(fake.requests, [])

    def test_one_attempt_no_body_in_the_error_60s_timeout(self):
        fake = FakeBeyond(fail=("customizations/base-price/", 429))
        client = WriteClient(TOKEN, LID, opener=fake)
        with self.assertRaises(CannotWrite) as ctx:
            client.request("PATCH", PREFIX + "customizations/base-price/", body={"data": {}})
        self.assertEqual(str(ctx.exception), f"Beyond PATCH {PREFIX}customizations/base-price/: HTTP 429")
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(fake.timeouts, [60])

    def test_call_budget(self):
        fake = FakeBeyond()
        client = WriteClient(TOKEN, LID, opener=fake, max_calls=2)
        client.request("GET", PREFIX)
        client.request("GET", PREFIX)
        with self.assertRaises(CannotWrite):
            client.request("GET", PREFIX)

    def test_redirects_are_not_followed(self):
        from _mvp_write import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 301, "", {}, "https://evil/"))
        self.assertTrue(any(isinstance(h, _NoRedirect)
                            for h in WriteClient(TOKEN, LID).opener.handlers))

    def test_bad_listing_ids_and_missing_token(self):
        for bad in ("abc", "12/..", "", None, True):
            with self.assertRaises(CannotWrite):
                WriteClient(TOKEN, bad)
        with self.assertRaises(CannotWrite):
            WriteClient("", LID)
        with self.assertRaises(CannotWrite):
            Live(WriteClient(TOKEN, "1", opener=FakeBeyond()), LID)


# ------------------------------------------------------------------ 7. apply + re-read verification

class Apply(Base):
    def test_happy_path_sends_only_the_changed_field_and_verifies_everything(self):
        fake = FakeBeyond()
        j = self.apply(fake, self.plan(fake, change(listing_prices={"min": 170})))
        self.assertEqual(j["status"], "verified")
        (m, path, _, body), = fake.patches()
        self.assertEqual((m, path), ("PATCH", PREFIX + "customizations/min-max-prices/"))
        self.assertEqual(body, {"data": {"type": "min-max-price-customizations", "id": LID,
                                         "attributes": {"min-price": 170.0}}})
        fields = {v.get("field"): v["ok"] for v in j["verification"]}
        self.assertEqual(fields, {"min": True, "base": True, "max": True, "min_stay": True,
                                  "other Beyond rules": True})

    def test_overrides_set_replace_and_delete_in_one_patch(self):
        fake = FakeBeyond()
        env = self.plan(fake, change(
            overrides_set=[{"date": "2026-10-05", "price": 230, "price_type": "fixed"},
                           {"date": "2026-10-10", "price": 5, "price_type": "percent"}],
            overrides_delete=["2026-11-20"]))
        j = self.apply(fake, env)
        self.assertEqual(j["status"], "verified")
        (_, path, _, body), = fake.patches()
        self.assertEqual(body["data"]["type"], "manual-override-customizations")
        self.assertEqual(sorted(body["data"]["attributes"]["overrides"], key=lambda r: r["start-date"]), [
            {"start-date": "2026-10-05", "end-date": "2026-10-05", "price": 230},
            {"start-date": "2026-10-10", "end-date": "2026-10-10", "percentage-adjustment": 5},
            {"start-date": "2026-11-20", "end-date": "2026-11-20"}])
        self.assertEqual(fake.overrides, {"2026-10-05": {"price": 230, "percentage-adjustment": None},
                                          "2026-10-10": {"price": None, "percentage-adjustment": 5}})

    def test_silent_no_op_under_200_is_caught(self):
        fake = FakeBeyond(silent_noop=True)
        env = self.plan(fake, change(listing_prices={"base": 210}))
        self.refused(lambda: self.apply(fake, env), "did not take as approved", "base is 200.00 live")
        self.assertEqual(self.journal_on_disk()["status"], "sent-unverified")

    def test_side_effect_on_a_rule_nobody_asked_to_change_is_caught(self):
        def touch(f):
            f.cust["min-max-prices"]["seasonal-prices"] = []
        fake = FakeBeyond(side_effect=touch)
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.refused(lambda: self.apply(fake, env), "min-max-prices.seasonal-prices")

    def test_side_effect_on_an_unwritten_listing_field_is_caught(self):
        def touch(f):
            f.cust["base-price"]["base-price"] = 199
        fake = FakeBeyond(side_effect=touch)
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.refused(lambda: self.apply(fake, env), "base is 199.00 live, expected 200.00")

    def test_override_patch_that_replaces_instead_of_merging_is_caught(self):
        fake = FakeBeyond(replace_overrides=True)
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        self.refused(lambda: self.apply(fake, env), "override 2026-10-10 differs", "override 2026-11-20")

    def test_other_override_dates_must_be_untouched(self):
        def touch(f):
            f.overrides["2026-11-20"]["percentage-adjustment"] = -20
        fake = FakeBeyond(side_effect=touch)
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        self.refused(lambda: self.apply(fake, env), "override 2026-11-20 differs on percentage_adjustment")

    def test_an_empty_reread_is_not_success(self):
        fake = FakeBeyond(empty_reread=True)
        env = self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))
        self.refused(lambda: self.apply(fake, env), "did not take as approved")

    def test_unreadable_reply_but_the_write_landed_is_verified_by_the_reread(self):
        fake = FakeBeyond(reply={"ok": True})
        j = self.apply(fake, self.plan(fake, change(listing_prices={"min": 170})))
        self.assertEqual(j["status"], "verified")
        self.assertIn("unreadable", j["response_note"])

    def test_unreadable_reply_stops_the_next_send_and_the_reread_decides(self):
        fake = FakeBeyond(reply={"ok": True})
        env = self.plan(fake, change(listing_prices={"min": 170}, listing_min_stay=3))
        self.refused(lambda: self.apply(fake, env), "min stay is 2 live, expected 3")
        self.assertEqual(len(fake.patches()), 1)

    def test_min_stay_writes_only_the_annual_value(self):
        fake = FakeBeyond()
        j = self.apply(fake, self.plan(fake, change(listing_min_stay=3)))
        self.assertEqual(j["status"], "verified")
        self.assertEqual(fake.patches()[0][3]["data"]["attributes"], {"min-stay": 3})

    def test_the_write_order_keeps_min_base_max_true_after_each_call(self):
        # raise base above today's max: the max must go up first
        fake = FakeBeyond()
        j = self.apply(fake, self.plan(fake, change(listing_prices={"base": 450, "max": 600})))
        self.assertEqual(j["status"], "verified")
        self.assertEqual([p[1].rsplit("/", 2)[-2] for p in fake.patches()], ["min-max-prices", "base-price"])
        # raise min above today's base: the base must go up first
        fake = FakeBeyond()
        j = self.apply(fake, self.plan(fake, change(listing_prices={"min": 250, "base": 300})))
        self.assertEqual(j["status"], "verified")
        self.assertEqual([p[1].rsplit("/", 2)[-2] for p in fake.patches()], ["base-price", "min-max-prices"])

    def test_no_safe_order_is_refused_at_plan(self):
        self.refused(lambda: self.plan(FakeBeyond(), change(
            listing_prices={"min": 450, "base": 500, "max": 600})), "Split it into two changes")
        self.assertEqual(send_order({"min": 1, "base": 2, "max": None}, {"min": 1, "base": 2, "max": None}), [])


class Batch(Base):
    def test_one_yes_applies_every_plan_in_order(self):
        fake = FakeBeyond()
        envs = [self.plan(fake, change(listing_min_stay=3)),
                self.plan(fake, change(overrides_set=[
                    {"date": "2026-10-05", "price": 230, "price_type": "fixed"}]))]
        done = []
        out = apply_batch(envs, lambda lid, pms: live(fake), state_dir=self.state, today=TODAY,
                          now=LATER, on_verified=done.append)
        self.assertEqual([j["status"] for j in out], ["verified", "verified"])
        self.assertEqual(done, out)
        self.assertEqual(len(list(self.state.glob("journal/*.json"))), 2)

    def test_a_later_plan_whose_floor_checks_the_first_invalidated_is_refused(self):
        fake = FakeBeyond()
        envs = [self.plan(fake, change(listing_prices={"min": 170})),
                self.plan(fake, change(overrides_set=[
                    {"date": "2026-10-05", "price": 160, "price_type": "fixed"}]))]
        done = []
        # the second plan checked 160 against min 150; once the first raises min to 170 that
        # check is stale, so it is refused, never applied under the new floor
        self.refused(lambda: apply_batch(envs, lambda lid, pms: live(fake), state_dir=self.state,
                                         today=TODAY, now=LATER, on_verified=done.append),
                     "price rule in Beyond")
        self.assertEqual(len(done), 1)
        self.assertNotIn("2026-10-05", fake.overrides)

    def test_first_failure_stops_and_names_what_was_not_attempted(self):
        fake = FakeBeyond(silent_noop=True)
        a = self.plan(fake, change(listing_prices={"min": 170}))
        b = self.plan(fake, change(listing_min_stay=3))
        msg = self.refused(lambda: apply_batch([a, b], lambda lid, pms: live(fake),
                                               state_dir=self.state, today=TODAY, now=LATER),
                           "NOT ATTEMPTED", plan_id(b))
        self.assertIn(plan_id(a), msg)

    def test_two_plans_fine_alone_but_not_together_send_nothing(self):
        fake = FakeBeyond()
        a = self.plan(fake, change(listing_prices={"max": 300}))
        b = self.plan(fake, change(listing_prices={"base": 350}))
        self.refused(lambda: apply_batch([a, b], lambda lid, pms: live(fake), state_dir=self.state,
                                         today=TODAY, now=LATER), "min <= base <= max")
        self.assertEqual(fake.patches(), [])


# ------------------------------------------------------------------ 8. one-step undo

class Undo(Base):
    def roundtrip(self, spec, fake=None):
        fake = fake or FakeBeyond()
        original = (copy.deepcopy(fake.cust), copy.deepcopy(fake.overrides))
        j = self.apply(fake, self.plan(fake, spec))
        undo_env = self.undo(fake, j)
        self.assertEqual(self.apply(fake, undo_env, now=LATER + timedelta(minutes=5))["status"], "verified")
        self.assertEqual((fake.cust, fake.overrides), original)
        return undo_env

    def test_listing_prices_and_min_stay_roll_back(self):
        self.roundtrip(change(listing_prices={"min": 170, "base": 210}, listing_min_stay=3))

    def test_new_override_rolls_back_to_a_clear(self):
        env = self.roundtrip(change(overrides_set=[{"date": "2026-10-05", "price": 230,
                                                    "price_type": "fixed"}]))
        self.assertIn("REMOVES the override on 2026-10-05", "\n".join(env["warnings"]))

    def test_changed_and_deleted_overrides_roll_back_whole(self):
        env = self.roundtrip(change(overrides_set=[{"date": "2026-10-10", "price": 5, "price_type": "percent"}],
                                    overrides_delete=["2026-11-20"]))
        # 2026-11-20 was -10% on a 160 night (144, under the 150 min) before the change. The
        # undo puts it back exactly, and says so loudly rather than refusing the undo.
        self.assertIn("UNDO RESTORES A NIGHT BELOW YOUR MIN: 2026-11-20", "\n".join(env["warnings"]))

    def test_a_new_change_to_the_same_below_floor_value_is_still_refused(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-11-21", "price": -10, "price_type": "percent"}])), "below your min")

    def test_a_ceiling_added_where_there_was_none_rolls_back_to_none(self):
        fake = FakeBeyond(max_price=None)
        env = self.roundtrip(change(listing_prices={"max": 500}), fake)
        self.assertEqual(env["operations"][0]["after"], None)

    def test_rollback_from_the_snapshot_file_alone(self):
        fake = FakeBeyond()
        self.apply(fake, self.plan(fake, change(listing_prices={"min": 170})))
        snap = json.loads(next(self.state.glob("snapshots/*.json")).read_text())
        env = self.undo(fake, snap)
        self.assertEqual(env["operations"], [{"kind": "listing_price", "field": "min",
                                              "before": 170.0, "after": 150.0}])

    def test_undo_skips_items_already_back_and_drops_past_dates(self):
        fake = FakeBeyond()
        j = self.apply(fake, self.plan(fake, change(
            listing_prices={"min": 170},
            overrides_set=[{"date": "2026-10-02", "price": 230, "price_type": "fixed"},
                           {"date": "2026-10-05", "price": 230, "price_type": "fixed"}])))
        fake.cust["min-max-prices"]["min-price"] = 150  # someone already put min back
        env = self.undo(fake, j, today=date(2026, 10, 3),
                        now=datetime(2026, 10, 3, 9, tzinfo=timezone.utc))
        self.assertEqual([op.get("date") or op["field"] for op in env["operations"]], ["2026-10-05"])
        text = "\n".join(env["warnings"])
        self.assertIn("left out of the undo: 2026-10-02", text)
        self.assertIn("1 item already back to before", text)

    def test_nothing_to_undo_says_so(self):
        fake = FakeBeyond()
        j = self.apply(fake, self.plan(fake, change(listing_prices={"min": 170})))
        fake.cust["min-max-prices"]["min-price"] = 150
        self.refused(lambda: self.undo(fake, j), "Nothing to undo")

    def test_a_change_file_cannot_carry_a_restore_or_clear_a_ceiling(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, change(overrides_restore=[{"date": "2026-10-05", "price": 200}])),
                     "only built by the undo command")
        self.refused(lambda: self.plan(fake, change(listing_prices={"max": None})), "cannot be cleared")

    def test_a_pricelabs_journal_is_not_a_beyond_undo(self):
        self.refused(lambda: rollback_change({"envelope": {"target": {"listing_id": "x", "pms": "smartbnb"},
                                                           "operations": []}}), "not a Beyond change")


# ------------------------------------------------------------------ units and documented refusals

class UnitsAndRefusals(Base):
    def test_prices_are_major_units_and_whole_where_beyond_says_integer(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, change(listing_prices={"base": 189.5})), "not a whole number")
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 229.99, "price_type": "fixed"}])), "not a whole number")
        env = self.plan(fake, change(listing_prices={"min": 152.5}))
        self.assertEqual(env["operations"][0]["after"], 152.5)  # min-price is a documented double
        j = self.apply(fake, self.plan(fake, change(listing_prices={"base": 210})))
        self.assertIs(type(fake.patches()[0][3]["data"]["attributes"]["base-price"]), int)
        self.assertEqual(fake.cust["base-price"]["base-price"], 210)  # never 21000, never 2.10
        self.assertEqual(j["status"], "verified")

    def test_documented_minimums(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, change(listing_prices={"base": 9})), "at least 10")
        self.refused(lambda: self.plan(fake, change(listing_prices={"min": 4})), "at least 5")
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 1001, "price_type": "percent"}])), "between -100 and 1000")
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 2.5, "price_type": "percent"}])), "whole percent")

    def test_per_date_min_stay_is_refused_with_the_reason(self):
        self.refused(lambda: self.plan(FakeBeyond(), change(overrides_set=[
            {"date": "2026-10-05", "min_stay": 3}])), "does not document", "Refused rather than guessed")

    def test_stacked_percent_and_unknown_fields_are_refused(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 5, "price_type": "percent_stacked"}])), "no stacked percentage")
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 5, "price_type": "fixed", "days_of_week": ["friday"]}])),
            "not a field")
        self.refused(lambda: self.plan(fake, change(extra_guest_fee=30)), "Unsupported change keys")

    def test_listing_identity_and_pms(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, change(pms="hospitable", listing_prices={"min": 170})),
                     "pms 'hospitable'")
        self.refused(lambda: self.plan(fake, {**change(listing_prices={"min": 170}), "listing_id": "999"}),
                     "different listing")
        env = self.plan(fake, {**change(listing_prices={"min": 170}), "listing_id": int(LID), "pms": "beyond"})
        self.assertEqual(env["target"]["listing_id"], LID)

    def test_reason_nothing_to_do_and_no_ops(self):
        fake = FakeBeyond()
        self.refused(lambda: self.plan(fake, {"listing_id": LID, "listing_prices": {"min": 170}}), "reason")
        self.refused(lambda: self.plan(fake, change()), "nothing to write")
        self.refused(lambda: self.plan(fake, change(listing_prices={"min": 150})), "already 150.00")
        self.refused(lambda: self.plan(fake, change(overrides_delete=["2026-10-06"])), "no override to delete")
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-10", "price": 250, "price_type": "fixed"}])), "already has exactly")

    def test_not_in_an_active_market_refuses_what_needs_the_calendar(self):
        fake = FakeBeyond(in_active_market=False)
        self.refused(lambda: self.plan(fake, change(overrides_set=[
            {"date": "2026-10-05", "price": 230, "price_type": "fixed"}])), "in-active-market")
        self.plan(fake, change(listing_prices={"base": 210}))  # needs no calendar: allowed

    def test_sync_off_and_auto_base_are_said_on_the_card(self):
        env = self.plan(FakeBeyond(enabled=False), change(listing_prices={"base": 210}))
        text = "\n".join(env["warnings"])
        self.assertIn("Price syncing is OFF", text)
        self.assertIn("accepts its own base-price recommendations automatically", text)

    def test_listing_without_a_floor_is_refused(self):
        fake = FakeBeyond()
        fake.cust["min-max-prices"]["min-price"] = None
        self.refused(lambda: self.plan(fake, change(listing_prices={"base": 210})), "no minimum price")

    def test_live_for_needs_the_token_and_only_beyond(self):
        from _mvp_store import CannotAnalyze

        class NoKey:
            def key(self, provider):
                raise CannotAnalyze("Missing BEYOND_TOKEN; put it in the connector .env or use --env-file")
        self.refused(lambda: live_for(NoKey(), LID), "BEYOND_TOKEN")
        self.refused(lambda: live_for(NoKey(), LID, "smartbnb"), "Beyond id alone")

    def test_config_reads_beyond_token(self):
        from _mvp_config import Connections
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "x.env"
            env.write_text(f"BEYOND_TOKEN={TOKEN}\n")
            c = Connections(env_files=[str(env)], config_path=Path(tmp) / "none.json")
            self.assertEqual(c.key("beyond"), TOKEN)
            self.assertEqual(live_for(c, LID).lid, LID)

    def test_hash_is_the_shared_content_hash(self):
        env = self.plan(FakeBeyond(), change(listing_prices={"min": 170}))
        self.assertEqual(plan_id(env), content_hash(env)[:12])


if __name__ == "__main__":
    unittest.main()
