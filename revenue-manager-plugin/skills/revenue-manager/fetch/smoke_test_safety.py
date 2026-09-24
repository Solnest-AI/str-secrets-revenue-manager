#!/usr/bin/env python3
"""Offline regressions for cache identity, currency and calendar safety."""
import contextlib
from datetime import date, datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_imports = tempfile.TemporaryDirectory(prefix="revenue-manager-imports-")
with patch.dict(os.environ, RC_CACHE_DIR=_imports.name):
    import attribution as at
    import customization_write as cw
    import factcheck as fc
    import flywheel as fw
    import reconcile_pms as rec
    import reduce_customizations as cz
    import reduce_neighborhood as nb
    import reduce_overrides as ov
    import reduce_prices as prices
    import reduce_reservations as res

# Synthetic full UUIDs sharing the old eight-character cache key.
A = str(uuid.UUID(int=(int("12345678", 16) << 96) + 1))
B = str(uuid.UUID(int=(int("12345678", 16) << 96) + 2))
START, END = "2099-01-01", "2099-01-03"


def pms_day(when, status="AVAILABLE", amount=10000):
    return {"date": when, "status": {"reason": status, "available": status == "AVAILABLE"},
            "price": {"amount": amount, "currency": "CAD"}, "min_stay": 1}


def pl_day(when, status="", price=100):
    return {"date": when, "booking_status": status, "price": price,
            "min_stay": 1, "unbookable": int(status == "Blocked")}


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="revenue-manager-safety-")
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, RC_CACHE_DIR=self.tmp.name)
        env.start()
        self.addCleanup(env.stop)
        for mod, sub in ((ov, "overrides"), (nb, "neighborhood"),
                         (cz, "customizations"), (res, "reservations")):
            folder = Path(self.tmp.name) / sub
            folder.mkdir()
            p = patch.object(mod, "CACHE_DIR", str(folder))
            p.start()
            self.addCleanup(p.stop)

    def load(self, mod, listing, pms="smartbnb"):
        if mod is nb:
            return mod.load_or_fetch(listing, pms, 50.88, -119.9, 1, True)
        if mod is cz:
            return mod.load_or_fetch(listing, pms, 1, True, True)
        return mod.load_or_fetch(listing, pms, 1, True)

    def test_listing_caches_separate_full_ids_and_reject_wrong_metadata(self):
        for mod in (ov, nb, cz):
            with self.subTest(module=mod.__name__):
                def fetched(listing, pms, key):
                    if mod is ov:
                        return {"overrides": []}
                    return {"pulled_at": datetime.now(timezone.utc).isoformat(),
                            "listing": listing, "pms": pms, "data": {}}
                network = (patch.object(mod, "call", return_value={"customizations": {}})
                           if mod is cz else patch.object(mod, "fetch", side_effect=fetched))
                with patch.object(mod, "resolve_key", return_value="offline"), network:
                    self.assertEqual(self.load(mod, A)[0]["listing"], A)
                    self.assertEqual(self.load(mod, B)[0]["listing"], B)
                    self.assertEqual(self.load(mod, A, "other-pms")[0]["pms"], "other-pms")
                    for path in Path(mod.CACHE_DIR).glob("*.json"):
                        blob = json.loads(path.read_text())
                        if blob.get("listing") == A and blob.get("pms") == "smartbnb":
                            blob["listing"] = B
                            path.write_text(json.dumps(blob))
                    fresh, source, *_ = self.load(mod, A)
                    self.assertEqual(fresh["listing"], A)
                    self.assertEqual(source, "miss")

    def test_metrics_cache_separates_full_ids(self):
        with patch.object(prices, "call", side_effect=[{"adr": 100}, {"adr": 200}]):
            self.assertEqual(prices.fetch_metrics(A, "smartbnb", "offline")["adr"], 100)
            self.assertEqual(prices.fetch_metrics(B, "smartbnb", "offline")["adr"], 200)

    def run_prices(self, listing=A, pms="smartbnb", days=3):
        payloads = []
        def serve(path, key, body=None, **kwargs):
            payloads.append(body)
            target = body["listings"][0]
            start, end = date.fromisoformat(target["dateFrom"]), date.fromisoformat(target["dateTo"])
            return [{"id": listing, "pms": pms, "data": [pl_day((start + timedelta(days=i)).isoformat())
                    for i in range((end - start).days + 1)]}]
        argv = ["prices", "--listings", f"{listing}:{pms}", "--days", str(days),
                "--no-metrics", "--cache-dir", self.tmp.name]
        with patch.object(prices, "load_key", return_value="offline"), \
                patch.object(prices, "call", side_effect=serve), patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()):
            prices.main()
        return payloads

    def test_prices_cache_separates_listing_and_pms(self):
        self.assertEqual(len(self.run_prices(A)), 1)
        self.assertEqual(len(self.run_prices(A)), 0)
        self.assertEqual(len(self.run_prices(B)), 1)
        self.assertEqual(len(self.run_prices(A, "other-pms")), 1)

    def test_prices_cache_rejects_incorrect_embedded_listing(self):
        self.run_prices(A)
        path = next(Path(self.tmp.name).glob("*.json"))
        payload = json.loads(path.read_text())
        payload[0]["id"] = B
        path.write_text(json.dumps(payload))
        self.assertEqual(len(self.run_prices(A)), 1)

    def test_prices_horizon_is_exact_inclusive_days(self):
        body = self.run_prices(days=3)[0]["listings"][0]
        self.assertEqual((date.fromisoformat(body["dateTo"]) - date.fromisoformat(body["dateFrom"])).days, 2)

    def test_partial_price_portfolio_names_unreadable_listing_and_fails(self):
        today = date.today().isoformat()
        payload = [{"id": A, "pms": "smartbnb", "data": [pl_day(today)]},
                   {"id": B, "pms": "smartbnb", "data": []}]
        output = io.StringIO()
        with patch.object(prices, "load_key", return_value="offline"), \
                patch.object(prices, "call", return_value=payload), \
                patch.object(sys, "argv", ["prices", "--listings", f"{A}:smartbnb,{B}:smartbnb",
                                            "--days", "1", "--no-metrics", "--cache-dir", self.tmp.name]), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            prices.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn(B, output.getvalue())

    def test_truncated_price_horizon_fails(self):
        payload = [{"id": A, "data": [pl_day(date.today().isoformat())]}]
        with patch.object(prices, "load_key", return_value="offline"), \
                patch.object(prices, "call", return_value=payload), \
                patch.object(sys, "argv", ["prices", "--listings", f"{A}:smartbnb", "--days", "3",
                                            "--no-metrics", "--cache-dir", self.tmp.name]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            prices.main()
        self.assertEqual(stopped.exception.code, 2)

    def test_default_windows_agree_at_local_utc_date_boundary(self):
        class LocalDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 19)
        calls = []
        def calendar(token, listing, start, end):
            return [pms_day((date.fromisoformat(start) + timedelta(days=i)).isoformat())
                    for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)]
        def pull_prices(key, listings, start, end):
            calls.append((start, end))
            return {A: {r["date"]: pl_day(r["date"]) for r in calendar(None, A, start, end)}}
        with patch.object(rec, "date", LocalDate), patch.object(prices.dt, "date", LocalDate):
            request = self.run_prices(days=3)[0]["listings"][0]
            with patch.object(rec, "resolve_key", return_value="offline"), \
                    patch.object(rec, "_request", return_value={"listings": [{"id": A, "pms": "smartbnb"}]}), \
                    patch.object(rec, "fetch_pricelabs", side_effect=pull_prices), \
                    patch.object(rec, "fetch_pms_calendar", side_effect=calendar), \
                    patch.object(rec.time, "sleep"), \
                    patch.object(sys, "argv", ["reconcile", "--listing", A, "--days", "3"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rec.main(), 0)
        self.assertEqual(calls, [("2026-09-19", "2026-09-21")])
        self.assertEqual(calls[0], (request["dateFrom"], request["dateTo"]))

    def run_reservations(self, rows, listing=A, currency="CAD"):
        argv = ["reservations", "--listing", listing, "--today", "2026-09-19"]
        if currency:
            argv += ["--currency", currency]
        output = io.StringIO()
        with patch.object(res, "resolve_key", return_value="offline"), \
                patch.object(res, "fetch", return_value=(rows, 0)) as fetch, \
                patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            res.main()
        return output.getvalue(), fetch.call_count

    @staticmethod
    def reservation(currency="CAD"):
        return {"check_in": "2026-10-01", "booked_date": "2026-09-18", "no_of_days": 2,
                "rental_revenue": 200, "booking_status": "booked", "currency": currency}

    def test_one_unlabelled_booking_cannot_be_added_to_cad(self):
        with self.assertRaisesRegex(res.CannotProduce, "currency"):
            self.run_reservations([self.reservation(), self.reservation(None)])

    def test_unlabelled_booking_is_refused_without_expected_currency(self):
        with self.assertRaisesRegex(res.CannotProduce, "currency"):
            self.run_reservations([self.reservation(None)], currency=None)

    def test_undated_reservation_does_not_disappear_from_totals(self):
        for field in ("check_in", "booked_date"):
            with self.subTest(field=field), self.assertRaises((ValueError, res.CannotProduce)):
                fc.reservation_rows([dict(self.reservation(), **{field: "unreadable"})], "2026-09-19")

    def test_reservation_cache_separates_full_ids_and_checks_metadata(self):
        rows = [self.reservation()]
        self.assertEqual(self.run_reservations(rows, A)[1], 1)
        self.assertEqual(self.run_reservations(rows, B)[1], 1)
        for path in Path(res.CACHE_DIR).glob("*.json"):
            blob = json.loads(path.read_text())
            if blob.get("listing") == A:
                blob["listing"] = B
                path.write_text(json.dumps(blob))
        self.assertEqual(self.run_reservations(rows, A)[1], 1)

    def test_markup_uses_only_affirmatively_sellable_dates(self):
        days = [pms_day(f"2099-01-0{i}", "AVAILABLE" if i < 3 else "BLOCKED",
                        10000 if i < 3 else 20000) for i in range(1, 6)]
        pl = {d["date"]: pl_day(d["date"], "" if i < 2 else "Blocked") for i, d in enumerate(days)}
        result = fc.calendar_rows(days, pl)
        self.assertEqual(result["paired_dates"], 2)
        self.assertEqual(result["markup_median"], 1)
        self.assertEqual(result["drift"], [])
        self.assertEqual(result["blocked_runs"][0]["nights"], 3)

    def test_unknown_or_unsellable_price_status_is_not_a_markup_pair(self):
        for status, unbookable in (("Unknown", 0), ("Blocked", 0), ("", 1)):
            with self.subTest(status=status, unbookable=unbookable):
                row = dict(pl_day(START, status), unbookable=unbookable)
                self.assertEqual(fc.calendar_rows([pms_day(START)], {START: row})["paired_dates"], 0)

    def test_calendar_and_reconciliation_agree_on_sellability(self):
        day = pms_day(START, "RESERVED")
        for status, unbookable, count in (("", "0", 1), ("Blocked", 0, 0)):
            with self.subTest(status=status):
                pl = {START: dict(pl_day(START, status), unbookable=unbookable)}
                self.assertEqual(len(rec.reconcile([day], pl)["invisible"]), count)
                self.assertEqual(len(fc.calendar_rows([day], pl)["invisible"]), count)

    def run_reconcile(self, days=None, pl=None, listing=A, extra=None):
        if days is None:
            days = [pms_day(f"2099-01-0{i}") for i in range(1, 4)]
        if pl is None:
            pl = {d["date"]: pl_day(d["date"]) for d in days}
        argv = ["reconcile", "--listing", listing, "--from", START, "--to", END]
        argv += extra or []
        with patch.object(rec, "resolve_key", return_value="offline"), \
                patch.object(rec, "_request", return_value={"listings": [{"id": listing, "pms": "smartbnb"}]}), \
                patch.object(rec, "fetch_pricelabs", return_value={listing: pl}), \
                patch.object(rec, "fetch_pms_calendar", return_value=days) as fetch, \
                patch.object(rec.time, "sleep"), patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()):
            code = rec.main()
        return code, fetch.call_count

    def test_empty_truncated_and_unknown_pms_calendars_fail(self):
        pl = {f"2099-01-0{i}": pl_day(f"2099-01-0{i}") for i in range(1, 4)}
        for days in ([], [pms_day(START)], [pms_day(f"2099-01-0{i}", "UNRECOGNIZED") for i in range(1, 4)]):
            with self.subTest(days=len(days)), self.assertRaises(rec.CheckCannotRun):
                self.run_reconcile(days=days, pl=pl, extra=["--no-cache"])

    def test_unsynced_and_truncated_pricelabs_calendars_fail(self):
        for rows in ({}, {"__error__": "not synced"}, {START: pl_day(START)}):
            with self.subTest(rows=rows), self.assertRaises(rec.CheckCannotRun):
                self.run_reconcile(pl=rows, extra=["--no-cache"])

    def test_reconciliation_cache_separates_ids_and_checks_metadata(self):
        self.assertEqual(self.run_reconcile(listing=A), (0, 1))
        self.assertEqual(self.run_reconcile(listing=A), (0, 0))
        self.assertEqual(self.run_reconcile(listing=B), (0, 1))
        for path in (Path(self.tmp.name) / "reconcile").glob("*.json"):
            blob = json.loads(path.read_text())
            if blob.get("listing") == A:
                blob["listing"] = B
                path.write_text(json.dumps(blob))
        self.assertEqual(self.run_reconcile(listing=A), (0, 1))

    def test_fixed_absolute_price_has_no_percentage_direction(self):
        cfg = {"last_min_factor_on": True, "last_min_factor_type": "fixed",
               "last_min_factor_value": 180, "last_min_factor_dfd": 7}
        self.assertEqual(at.rule_direction("last_minute_prices", cfg), "unknown")
        self.assertTrue(cw.echo_diff({"rule": "last_minute_prices", "direction": "up", "magnitude": 180}, cfg))

    def test_enabled_seasonal_profile_requires_nested_object(self):
        configs = [{"custom_seasonal_profile_on": True}]
        configs.extend({"custom_seasonal_profile_on": True, "custom_seasonal_profile": value}
                       for value in (None, [], "profile", 1))
        for cfg in configs:
            with self.subTest(config=cfg):
                errors = cw.validate({"custom_seasonal_profile": cfg})
                self.assertTrue(any("custom_seasonal_profile" in error and "object" in error
                                    for error in errors), errors)

    def test_seasonal_profile_checks_required_fields_and_declared_types(self):
        season = {"season_name": "Winter", "start_month": "1", "start_day": "1",
                  "end_month": "2", "end_day": "28"}
        profiles = [
            {"seasons": {}},
            {"price_type": "percentage", "seasons": [None]},
            {"seasons": [season]},
            {"price_type": "invalid", "seasons": [season]},
            {"price_type": [], "seasons": [season]},
            {"price_type": "fixed", "seasons": [{"season_name": "Winter"}]},
            {"price_type": "fixed", "seasons": [dict(season, start_month=[])]},
            {"price_type": "fixed", "seasons": [dict(season, base_price=True)]},
            {"price_type": "fixed", "seasons": [dict(season, base_price="NaN")]},
        ]
        for profile in profiles:
            with self.subTest(profile=profile):
                self.assertTrue(cw.validate({"custom_seasonal_profile": {
                    "custom_seasonal_profile_on": True, "custom_seasonal_profile": profile}}))

    def test_enabled_seasonal_profile_requires_at_least_one_season(self):
        for profile in ({}, {"seasons": []}, {"non_repeating_seasons": []},
                        {"seasons": [], "non_repeating_seasons": []}):
            with self.subTest(profile=profile):
                errors = cw.validate({"custom_seasonal_profile": {
                    "custom_seasonal_profile_on": True, "custom_seasonal_profile": profile}})
                self.assertTrue(any("at least one season" in error for error in errors), errors)

    def test_seasonal_profile_accepts_valid_types_and_disabled_toggle_only(self):
        self.assertEqual(cw.validate({"custom_seasonal_profile": {
            "custom_seasonal_profile_on": False}}), [])
        season = {"season_name": "Winter", "start_month": "1", "start_day": "1",
                  "end_month": "2", "end_day": "28", "lowest_price": None,
                  "base_price": "120", "highest_price": 200}
        for kind in ("percentage", "fixed"):
            self.assertEqual(cw.validate({"custom_seasonal_profile": {
                "custom_seasonal_profile_on": True, "custom_seasonal_profile": {
                    "price_type": kind, "seasons": [season]}}}), [])

    def test_flywheel_cannot_pass_unreadable_rows(self):
        self.assertFalse(fw.spoke_bookings([{"unexpected": "shape"}])["ok"])
        self.assertFalse(fw.spoke_bookings([pms_day(START, "UNKNOWN")])["ok"])
        self.assertFalse(fw.spoke_ranking([{"unexpected": "shape"}])["ok"])
        self.assertFalse(fw.spoke_ranking([{"page": "nan"}])["ok"])
        self.assertTrue(fw.spoke_ranking([{"page": "1.0", "position": "8"}])["ok"])
        # PRD D12 (2026-09-20): every spoke unreadable is BLOCKED, because bookings is
        # among them and without the PMS calendar there is nothing to price. "skipped"
        # was the D4 verdict and no longer exists. A gate with only context spokes
        # missing degrades instead; that is covered in smoke_test_flywheel.py.
        blocked = fw.gate("test", None, None, None, None)
        self.assertEqual(blocked["verdict"], "blocked")
        self.assertNotIn("headline", blocked)

    def test_snapshot_collision_cannot_overwrite_after_stale_existence_check(self):
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 1, tzinfo=tz)
        original_exists = os.path.exists
        checked, released = threading.Event(), threading.Event()
        outcome = []
        def delayed_exists(path):
            exists = original_exists(path)
            if threading.current_thread().name == "delayed-snapshot" and str(path).endswith(".json"):
                checked.set()
                if not released.wait(5):
                    raise RuntimeError("snapshot test synchronization timed out")
            return exists
        def delayed_write():
            try:
                cw.write_snapshot({"listing_id": A, "version": "delayed"}, self.tmp.name)
                outcome.append("overwrote")
            except FileExistsError:
                outcome.append("refused")
        with patch.object(cw, "datetime", FrozenDatetime), patch.object(cw.os.path, "exists", side_effect=delayed_exists):
            worker = threading.Thread(target=delayed_write, name="delayed-snapshot")
            worker.start()
            # The old implementation pauses after a stale nonexistence read. The new
            # atomic publication path may finish immediately without that unsafe check.
            checked.wait(0.1)
            try:
                path = cw.write_snapshot({"listing_id": A, "version": "first"}, self.tmp.name)
                expected = "first"
            except FileExistsError:
                path = next(Path(self.tmp.name).glob("snapshot_*.json"))
                expected = "delayed"
            released.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(json.loads(Path(path).read_text())["version"], expected)
        if expected == "first":
            self.assertEqual(outcome, ["refused"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
