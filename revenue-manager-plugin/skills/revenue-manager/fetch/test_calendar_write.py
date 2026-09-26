"""Offline contracts for the PMS calendar writer (docs/WRITE-TARGETS.md, all 8 guarantees) and
the Hospitable, Guesty and OwnerRez calendar targets.

Every vendor is a stateful fake behind the real target class and the real transport, so the
HTTP method, path, body shape and unit conversion are what is tested. The fakes misbehave the
ways a live API can under a success status: a silent no-op, a side effect on a field nobody
asked to change, an asynchronous write that shows up late, an empty or partial re-read, an
error body that echoes a secret. Nothing here touches a network.
"""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import _calendar_write as cw
import apply_change
from _mvp_config import normalized_context
from _mvp_write import CannotWrite
from _pms_guesty import GuestyCalendarTarget
from _pms_hospitable import HospitableCalendarTarget
from _pms_ownerrez import OwnerRezCalendarTarget

LID = "prop-cal-0001"  # not UUID-shaped: the leak scan flags the 8-4-4-4-12 shape
GID = "guesty-listing-0001"
OID = "4242"
TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)
SECRET = "SECRET-ECHO-f00d"
FLOOR = {"min_price": 150}


def d(i: int) -> str:
    return (TODAY + timedelta(days=i)).isoformat()


class Response:
    def __init__(self, body, status=200):
        self.body = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class Conn:
    """Stand-in for _mvp_config.Connections: keys only, no files read."""

    def __init__(self, **values):
        self.values = {"HOSPITABLE_API_KEY": "synthetic-hosp", "OWNERREZ_EMAIL": "ops@example.test",
                       "OWNERREZ_TOKEN": "pt_synthetic", **values}
        self.paths = {}

    def key(self, provider):
        return {"hospitable": self.values["HOSPITABLE_API_KEY"]}[provider]


class FakeVendor:
    """Shared misbehaviour switches for every fake PMS."""

    def __init__(self, *, silent_noop=False, side_effect=False, fail_status=None, lag_reads=0,
                 drop_on_reread=None, empty_reread=False, broken_reread=False):
        self.silent_noop, self.side_effect, self.fail_status = silent_noop, side_effect, fail_status
        self.lag_reads, self.drop_on_reread = lag_reads, drop_on_reread
        self.empty_reread, self.broken_reread = empty_reread, broken_reread
        self.requests, self.writes_done, self._stale = [], 0, None

    def writes(self):
        return [r for r in self.requests if r[0] != "GET"]

    def _fail(self, req):
        raise HTTPError(req.full_url, self.fail_status, "boom", {},
                        io.BytesIO(json.dumps({"echo": SECRET}).encode()))

    def _read_view(self):
        """The calendar a GET sees: stale for lag_reads GETs after a write."""
        if self.writes_done and self._stale is not None and self.lag_reads > 0:
            self.lag_reads -= 1
            return self._stale
        return self.days

    def _after_write(self, apply):
        self._stale = copy.deepcopy(self.days)
        self.writes_done += 1
        if not self.silent_noop:
            apply()


# ------------------------------------------------------------------------------ Hospitable fake

class FakeHospitable(FakeVendor):
    def __init__(self, currency="USD", restricted=False, **kw):
        super().__init__(**kw)
        self.currency, self.restricted = currency, restricted
        scale = 10 ** cw.currency_decimals(currency)
        self.days = {d(i): {"amount": int((220 if i % 2 else 200) * scale), "min_stay": 2, "available": i != 9}
                     for i in range(0, 40)}

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        assert url.netloc == "public.api.hospitable.com"
        assert req.headers.get("Authorization") == "Bearer synthetic-hosp"
        method, body = req.get_method(), json.loads(req.data) if req.data else None
        self.requests.append((method, url.path, body))
        if method == "GET" and url.path == f"/v2/properties/{LID}":
            return Response({"data": {"id": LID, "calendar_restricted": self.restricted, "currency": self.currency}})
        if method == "GET" and url.path == f"/v2/properties/{LID}/calendar":
            q = parse_qs(url.query)
            start, end = q["start_date"][0], q["end_date"][0]
            if self.writes_done and self.broken_reread:
                return Response(b"<html>gateway</html>")
            view = self._read_view()
            rows = [] if (self.writes_done and self.empty_reread) else [
                {"date": k, "day": "X", "min_stay": v["min_stay"], "note": None,
                 "status": {"reason": "AVAILABLE" if v["available"] else "RESERVED", "available": v["available"]},
                 "price": {"amount": v["amount"], "formatted": "x", "currency": self.currency}}
                for k, v in sorted(view.items()) if start <= k <= end
                and not (self.writes_done and k == self.drop_on_reread)]
            return Response({"data": {"start_date": start, "end_date": end, "days": rows}})
        if method == "PUT" and url.path == f"/v2/properties/{LID}/calendar":
            if self.fail_status:
                self._fail(req)

            def apply():
                for row in body["dates"]:
                    day = self.days[row["date"]]
                    if "price" in row:
                        day["amount"] = row["price"]["amount"]
                        if self.side_effect:
                            day["min_stay"] += 1
                    if "min_stay" in row:
                        day["min_stay"] = row["min_stay"]
            self._after_write(apply)
            return Response({"status": "accepted"}, status=202)
        raise AssertionError(f"unexpected {method} {url.path}")


def hosp(fake, cls=HospitableCalendarTarget, **kw):
    return cls(Conn(), opener=fake, **kw)


def plan(fake, items, settings=FLOOR, target=None, **kw):
    t = target or hosp(fake)
    change = {"listing_id": LID, "target": "hospitable", "reason": "test", "calendar_set": items}
    return cw.plan_calendar(change, t, settings, today=TODAY, now=NOW, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.slept = []

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, env, fake, settings=FLOOR, target=None, when=LATER):
        return cw.apply_envelope(env, target or hosp(fake), settings, state_dir=self.state,
                                 today=TODAY, now=when, sleep=self.slept.append)

    def journals(self):
        return [json.loads(p.read_text()) for p in sorted((self.state / "journal").glob("*.json"))]


# ------------------------------------------------------------------------------ guarantee 1

class FreshPlan(Base):
    def test_every_plan_reads_the_calendar_fresh(self):
        fake = FakeHospitable()
        plan(fake, [{"date": d(3), "price": 210}])
        plan(fake, [{"date": d(3), "price": 210}])
        gets = [r for r in fake.requests if r[0] == "GET" and r[1].endswith("/calendar")]
        self.assertEqual(len(gets), 2)

    def test_card_shows_before_and_after_for_every_date(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230, "min_stay": 3}, {"date": d(4), "price": 205}])
        card = cw.describe(env, "Hospitable")
        self.assertIn(f"{d(3)}: price 220.00 -> 230.00 (+4.5%), min stay 2 -> 3", card)
        self.assertIn(f"{d(4)}: price 200.00 -> 205.00 (+2.5%), min stay 2 (unchanged)", card)
        self.assertIn("Floor: USD 150.00 (property_config min_price)", card)
        self.assertIn(f"Plan {cw.plan_id(env)}", card)

    def test_card_says_this_is_the_first_live_write_for_the_vendor(self):
        env = plan(FakeHospitable(), [{"date": d(3), "price": 230}])
        self.assertIn("first live write for Hospitable: read the after-values carefully.", env["warnings"][0])

    def test_a_date_the_vendor_did_not_return_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, f"{d(45)} did not come back"):
            plan(FakeHospitable(), [{"date": d(3), "price": 230}, {"date": d(45), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "no currency"):  # nothing at all came back
            plan(FakeHospitable(), [{"date": d(45), "price": 230}])

    def test_bad_input_is_refused_before_any_read(self):
        fake = FakeHospitable()
        for items, why in (([{"date": d(-1), "price": 230}], "past"),
                           ([{"date": d(3), "price": 0}], "above zero"),
                           ([{"date": d(3), "min_stay": 0}], "min_stay"),
                           ([{"date": d(3), "min_stay": True}], "min_stay"),
                           ([{"date": d(3)}], "sets nothing"),
                           ([{"date": d(3), "price": 1, "note": "x"}], "not a field"),
                           ([{"date": d(3), "price": 230}, {"date": d(3), "price": 240}], "twice"),
                           ([{"date": d(400), "price": 230}], "days out")):
            with self.subTest(why=why), self.assertRaisesRegex(CannotWrite, why):
                plan(fake, items)
        self.assertEqual(fake.requests, [])

    def test_more_dates_than_one_call_carries_is_refused(self):
        items = [{"date": d(i), "price": 300} for i in range(cw.MAX_DATES + 1)]
        with self.assertRaisesRegex(CannotWrite, "limit is 60"):
            plan(FakeHospitable(), items)

    def test_a_change_file_cannot_carry_an_undo(self):
        with self.assertRaisesRegex(CannotWrite, "only built by the undo command"):
            cw.plan_calendar({"listing_id": LID, "target": "hospitable", "reason": "x",
                              "calendar_restore": [{"date": d(3), "price": 1}]},
                             hosp(FakeHospitable()), FLOOR, today=TODAY, now=NOW)

    def test_a_change_for_another_target_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "guesty"):
            cw.plan_calendar({"listing_id": LID, "target": "guesty", "reason": "x",
                              "calendar_set": [{"date": d(3), "price": 230}]},
                             hosp(FakeHospitable()), FLOOR, today=TODAY, now=NOW)


# ------------------------------------------------------------------------------ guarantee 2

class Floor(Base):
    def test_price_below_the_settings_min_is_refused(self):
        with self.assertRaisesRegex(CannotWrite, "below your min of 150.00"):
            plan(FakeHospitable(), [{"date": d(3), "price": 149}])

    def test_the_targets_own_floor_wins_over_settings(self):
        class Floored(HospitableCalendarTarget):
            def floor(self, listing_id):
                return 190.0
        fake = FakeHospitable()
        with self.assertRaisesRegex(CannotWrite, r"below your min of 190.00 \(Hospitable min price\)"):
            plan(fake, [{"date": d(3), "price": 180}], target=hosp(fake, Floored))

    def test_no_floor_anywhere_refuses_a_cut(self):
        with self.assertRaisesRegex(CannotWrite, "Set your min first"):
            plan(FakeHospitable(), [{"date": d(3), "price": 210}], settings={})

    def test_no_floor_still_allows_a_raise_and_min_stay(self):
        env = plan(FakeHospitable(), [{"date": d(3), "price": 230}, {"date": d(4), "min_stay": 3}], settings={})
        self.assertEqual(len(env["operations"]), 2)
        self.assertIn("Floor: none set", cw.describe(env))

    def test_no_floor_and_no_current_price_is_refused(self):
        fake = FakeHospitable()
        fake.days[d(3)]["amount"] = None
        with self.assertRaisesRegex(CannotWrite, "Set your min first"):
            plan(fake, [{"date": d(3), "price": 230}], settings={})

    def test_a_move_over_15_percent_is_flagged_not_blocked(self):
        env = plan(FakeHospitable(), [{"date": d(3), "price": 300}])
        self.assertTrue(any(w.startswith("OVER 15%") and "+36.4%" in w for w in env["warnings"]))
        self.assertEqual(env["operations"][0]["after"]["price"], 300.0)

    def test_max_delta_comes_from_property_config(self):
        env = plan(FakeHospitable(), [{"date": d(3), "price": 240}], settings={**FLOOR, "max_delta_pct": 5})
        self.assertTrue(any(w.startswith("OVER 5%") for w in env["warnings"]))

    def test_booked_night_is_named(self):
        env = plan(FakeHospitable(), [{"date": d(9), "price": 230}])
        self.assertTrue(any("booked or blocked" in w for w in env["warnings"]))


# ------------------------------------------------------------------------------ pricing tool

class PricingTool(Base):
    def test_pricelabs_owned_listing_refuses_a_pms_price_write(self):
        fake = FakeHospitable()
        with self.assertRaises(CannotWrite) as ctx:
            plan(fake, [{"date": d(3), "price": 230}], settings={**FLOOR, "pricing_tool": "pricelabs"})
        self.assertIn("PriceLabs sets this listing's prices, so a PMS change would be overwritten on the "
                      "next sync. Change it in PriceLabs instead.", str(ctx.exception))
        self.assertEqual(fake.requests, [])

    def test_beyond_owned_listing_names_beyond(self):
        with self.assertRaisesRegex(CannotWrite, "Change it in Beyond instead"):
            plan(FakeHospitable(), [{"date": d(3), "price": 230}], settings={**FLOOR, "pricing_tool": "beyond"})

    def test_the_target_api_saying_so_also_refuses(self):
        class Managed(HospitableCalendarTarget):
            def pricing_managed(self, listing_id):
                return "pricelabs"
        fake = FakeHospitable()
        with self.assertRaisesRegex(CannotWrite, "Change it in PriceLabs instead"):
            plan(fake, [{"date": d(3), "price": 230}], target=hosp(fake, Managed))

    def test_min_stay_only_is_allowed_with_a_warning(self):
        env = plan(FakeHospitable(), [{"date": d(3), "min_stay": 3}], settings={"pricing_tool": "pricelabs"})
        self.assertTrue(any("PriceLabs manages this listing's prices" in w for w in env["warnings"]))

    def test_legacy_rows_mapped_to_pricelabs_read_as_pricelabs_owned(self):
        row = {"property_id": LID, "settings": {"pricelabs_listing_id": LID, "min_price": 150}}
        s = normalized_context({"config": [row]}, LID)["settings"]
        self.assertEqual((s["pricing_tool"], s["min_price"]), ("pricelabs", 150))
        row = {"property_id": LID, "settings": {"pricing_tool": None, "pricing_gap": "x"}}
        self.assertIsNone(normalized_context({"config": [row]}, LID)["settings"]["pricing_tool"])

    def test_apply_refuses_if_a_pricing_tool_took_over_after_the_plan(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "Change it in PriceLabs instead"):
            self.apply(env, fake, settings={**FLOOR, "pricing_tool": "pricelabs"})
        self.assertEqual(fake.writes(), [])

    def test_calendar_restricted_property_is_refused_before_planning(self):
        fake = FakeHospitable(restricted=True)
        with self.assertRaisesRegex(CannotWrite, "calendar_restricted"):
            plan(fake, [{"date": d(3), "price": 230}])
        self.assertFalse(any(r[1].endswith("/calendar") for r in fake.requests))


# ------------------------------------------------------------------------------ guarantee 3

class PlanIdentity(Base):
    def test_plan_id_is_stable_and_moves_with_one_field(self):
        a = plan(FakeHospitable(), [{"date": d(3), "price": 230}])
        b = plan(FakeHospitable(), [{"date": d(3), "price": 230}])
        c = plan(FakeHospitable(), [{"date": d(3), "price": 231}])
        self.assertEqual(cw.plan_id(a), cw.plan_id(b))
        self.assertNotEqual(cw.plan_id(a), cw.plan_id(c))

    def test_an_edited_saved_plan_is_refused(self):
        env = plan(FakeHospitable(), [{"date": d(3), "price": 230}])
        path = Path(cw.save_envelope(env, self.state))
        self.assertEqual(cw.load_plan(self.state, cw.plan_id(env))["operations"], env["operations"])
        tampered = json.loads(path.read_text())
        tampered["operations"][0]["after"]["price"] = 23.0
        path.write_text(json.dumps(tampered))
        with self.assertRaisesRegex(CannotWrite, "edited after it was shown"):
            cw.load_plan(self.state, cw.plan_id(env))

    def test_flipping_the_undo_flag_changes_the_id(self):
        env = plan(FakeHospitable(), [{"date": d(3), "price": 230}])
        self.assertNotEqual(cw.plan_id(env), cw.plan_id({**env, "undo": True}))

    def test_a_plan_older_than_24_hours_is_refused(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "more than 24"):
            self.apply(env, fake, when=NOW + timedelta(hours=25))
        self.assertEqual(fake.writes(), [])

    def test_a_date_that_passed_is_refused(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(0), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "in the past now"):
            cw.apply_envelope(env, hosp(fake), FLOOR, state_dir=self.state, today=TODAY + timedelta(days=1),
                              now=NOW + timedelta(hours=13), sleep=self.slept.append)
        self.assertEqual(fake.writes(), [])


# ------------------------------------------------------------------------------ guarantee 4

class Drift(Base):
    def test_one_moved_date_refuses_the_whole_batch(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}, {"date": d(4), "price": 210}, {"date": d(5), "min_stay": 4}])
        fake.days[d(4)]["amount"] = 20500
        with self.assertRaisesRegex(CannotWrite, rf"{d(4)} changed since the plan .*Nothing was sent"):
            self.apply(env, fake)
        self.assertEqual(fake.writes(), [])
        self.assertFalse((self.state / "snapshots").exists())

    def test_a_night_that_got_booked_is_drift(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}])
        fake.days[d(3)]["available"] = False
        with self.assertRaisesRegex(CannotWrite, "available True -> False"):
            self.apply(env, fake)

    def test_a_raised_floor_since_the_plan_is_refused(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "below your min of 240.00"):
            self.apply(env, fake, settings={"min_price": 240})
        self.assertEqual(fake.writes(), [])


# ------------------------------------------------------------------------------ guarantees 5-7

class ApplyAndVerify(Base):
    def test_batch_is_one_put_and_is_verified_field_by_field(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230.25, "min_stay": 3}, {"date": d(4), "price": 205}])
        journal = self.apply(env, fake)
        self.assertEqual(journal["status"], "verified")
        writes = fake.writes()
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2], {"dates": [
            {"date": d(3), "price": {"amount": 23025}, "min_stay": 3},
            {"date": d(4), "price": {"amount": 20500}}]})
        self.assertEqual([(v["date"], v["ok"]) for v in journal["verification"]], [(d(3), True), (d(4), True)])
        self.assertTrue(Path(journal["snapshot_path"]).is_file())
        self.assertIn(f"apply_change.py rollback --target hospitable --journal ", journal["undo"])

    def test_snapshot_is_on_disk_before_the_send(self):
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}])
        seen = {}
        real = fake.open

        def spy(req, timeout):
            if req.get_method() == "PUT":
                seen["snapshots"] = list((self.state / "snapshots").glob("*.json"))
            return real(req, timeout)
        fake.open = spy
        self.apply(env, fake)
        self.assertEqual(len(seen["snapshots"]), 1)
        snap = json.loads(seen["snapshots"][0].read_text())
        self.assertEqual(snap["calendar_restore"], [{"date": d(3), "price": 220.0}])

    def test_http_error_is_sent_once_journalled_and_never_echoed(self):
        fake = FakeHospitable(fail_status=500)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaises(CannotWrite) as ctx:
            self.apply(env, fake)
        msg = str(ctx.exception)
        self.assertEqual(len(fake.writes()), 1)  # never retried
        self.assertIn(f"Hospitable PUT /v2/properties/{LID}/calendar: HTTP 500", msg)
        self.assertIn("apply_change.py rollback --target hospitable --journal", msg)
        self.assertNotIn(SECRET, msg)
        [j] = self.journals()
        self.assertEqual(j["status"], "sent-unverified")
        self.assertNotIn(SECRET, json.dumps(j))
        self.assertEqual(self.slept, [])  # a refused send gets one read, not the settle schedule

    def test_422_names_the_documented_causes(self):
        fake = FakeHospitable(fail_status=422)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "Hospitable Dynamic Pricing is on"):
            self.apply(env, fake)

    def test_silent_noop_is_not_success(self):
        fake = FakeHospitable(silent_noop=True)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, rf"accepted the change but hadn't applied it after 110s "
                                                 rf"\({d(3)} differs on price\).*apply_change.py verify "
                                                 rf"--target hospitable --journal .*Undo with: apply_change.py "
                                                 rf"rollback --target hospitable"):
            self.apply(env, fake)
        self.assertEqual(self.journals()[0]["status"], "sent-unverified")
        self.assertEqual(len(fake.writes()), 1)
        self.assertEqual(self.slept, [5, 15, 30, 60])  # re-READ on the settle schedule, never re-sent

    def test_a_synchronous_target_says_it_did_not_take(self):
        class Sync(HospitableCalendarTarget):
            APPLIES_ASYNC = False
            SETTLE_SECONDS = (0,)
        fake = FakeHospitable(silent_noop=True)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, rf"SENT but did not take as approved: {d(3)} differs on price"):
            self.apply(env, fake, target=hosp(fake, Sync))
        self.assertEqual(self.slept, [])

    def test_a_total_settle_budget_polls_every_10s(self):
        class Up:
            APPLIES_ASYNC, SETTLE_SECONDS = True, 60
        self.assertEqual(cw.settle_schedule(Up), (0.0,) + (10.0,) * 6)
        Up.SETTLE_SECONDS = 25
        self.assertEqual(cw.settle_schedule(Up), (0.0, 10.0, 10.0, 5.0))
        self.assertEqual(cw.settle_schedule(object()), (0.0,))

    def test_a_foreign_transport_log_is_read_without_its_helpers(self):
        class Log:
            calls = [{"method": "GET"}, {"method": "POST", "path": "/x"}]
        self.assertIs(cw._attempted(Log, 1), True)
        self.assertIs(cw._attempted(Log, 2), False)
        Log.calls = [{"path": "/no-method"}]
        self.assertIsNone(cw._attempted(Log, 0))

    def test_verify_journal_is_read_only(self):
        fake = FakeHospitable(silent_noop=True)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaises(CannotWrite):
            self.apply(env, fake)
        [j] = self.journals()
        before = sorted(p.name for p in self.state.rglob("*"))
        writes = len(fake.writes())
        self.assertFalse(cw.verify_journal(j, hosp(fake))["ok"])
        fake.days[d(3)]["amount"] = 23000  # the change lands late
        result = cw.verify_journal(j, hosp(fake))
        self.assertEqual((result["ok"], result["problems"]), (True, []))
        self.assertEqual(len(fake.writes()), writes)
        self.assertEqual(sorted(p.name for p in self.state.rglob("*")), before)

    def test_a_side_effect_on_a_field_not_written_is_caught(self):
        fake = FakeHospitable(side_effect=True)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "differs on min_stay"):
            self.apply(env, fake)

    def test_an_empty_reread_is_not_success(self):
        fake = FakeHospitable(empty_reread=True)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "SENT but"):
            self.apply(env, fake)
        self.assertEqual(self.journals()[0]["status"], "sent-unverified")

    def test_an_unreadable_reread_is_not_success(self):
        fake = FakeHospitable(broken_reread=True)
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "SENT but could not be verified"):
            self.apply(env, fake)
        self.assertEqual(self.journals()[0]["status"], "sent-unverified")

    def test_a_date_missing_from_the_reread_is_not_success(self):
        fake = FakeHospitable(drop_on_reread=d(4))
        env = plan(fake, [{"date": d(3), "price": 230}, {"date": d(4), "price": 210}])
        with self.assertRaisesRegex(CannotWrite, f"{d(4)} did not come back in the re-read"):
            self.apply(env, fake)

    def test_an_asynchronous_write_that_lands_late_is_verified_without_resending(self):
        fake = FakeHospitable(lag_reads=2)
        env = plan(fake, [{"date": d(3), "price": 230}])
        journal = self.apply(env, fake)
        self.assertEqual(journal["status"], "verified")
        self.assertEqual(journal["reads"], 3)
        self.assertEqual(self.slept, [5, 15])
        self.assertEqual(len(fake.writes()), 1)

    def test_a_target_that_refuses_before_sending_is_failed_before_send(self):
        class Refuses(HospitableCalendarTarget):
            def write_calendar(self, listing_id, changes, currency):
                raise CannotWrite("nope, before any request")
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, "failed-before-send; nothing left this machine"):
            self.apply(env, fake, target=hosp(fake, Refuses))
        [j] = self.journals()
        self.assertEqual((j["status"], j["sent"]), ("failed-before-send", []))
        self.assertTrue(Path(j["snapshot_path"]).is_file())

    def test_a_crash_mid_send_still_leaves_a_journal(self):
        class Crashes(HospitableCalendarTarget):
            def write_calendar(self, listing_id, changes, currency):
                super().write_calendar(listing_id, changes, currency)
                raise KeyboardInterrupt
        fake = FakeHospitable()
        env = plan(fake, [{"date": d(3), "price": 230}])
        with self.assertRaises(KeyboardInterrupt):
            self.apply(env, fake, target=hosp(fake, Crashes))
        self.assertEqual(self.journals()[0]["status"], "sent-unverified")

    def test_batch_stops_at_the_first_failure(self):
        fake = FakeHospitable(silent_noop=True)
        a = plan(fake, [{"date": d(3), "price": 230}])
        b = plan(fake, [{"date": d(4), "price": 230}])
        with self.assertRaisesRegex(CannotWrite, f"NOT ATTEMPTED: {cw.plan_id(b)}"):
            cw.apply_batch([a, b], lambda lid: hosp(fake), lambda lid: FLOOR, state_dir=self.state,
                           today=TODAY, now=LATER, sleep=self.slept.append)
        self.assertEqual(len(fake.writes()), 1)


# ------------------------------------------------------------------------------ guarantee 8

class Undo(Base):
    def applied(self, fake, items, settings=FLOOR):
        env = plan(fake, items, settings=settings)
        return self.apply(env, fake, settings=settings)

    def test_undo_from_the_journal_restores_every_field_it_set(self):
        fake = FakeHospitable()
        journal = self.applied(fake, [{"date": d(3), "price": 230, "min_stay": 3}, {"date": d(4), "price": 210}])
        spec = cw.rollback_change(journal)
        self.assertEqual(spec["calendar_restore"], [{"date": d(3), "price": 220.0, "min_stay": 2},
                                                    {"date": d(4), "price": 200.0}])
        env = cw.plan_calendar(spec, hosp(fake), FLOOR, today=TODAY, now=LATER, rollback=True)
        self.assertTrue(env["undo"])
        self.assertIn("PROPOSED UNDO", cw.describe(env))
        self.assertEqual(self.apply(env, fake, when=LATER)["status"], "verified")
        self.assertEqual((fake.days[d(3)]["amount"], fake.days[d(3)]["min_stay"], fake.days[d(4)]["amount"]),
                         (22000, 2, 20000))

    def test_undo_skips_dates_already_back(self):
        fake = FakeHospitable()
        journal = self.applied(fake, [{"date": d(3), "price": 230}, {"date": d(4), "price": 210}])
        fake.days[d(4)]["amount"] = 20000  # someone already put it back by hand
        env = cw.plan_calendar(cw.rollback_change(journal), hosp(fake), FLOOR, today=TODAY, now=LATER, rollback=True)
        self.assertEqual([op["date"] for op in env["operations"]], [d(3)])
        self.assertTrue(any("1 date already back to before" in w for w in env["warnings"]))

    def test_undo_drops_dates_now_in_the_past(self):
        fake = FakeHospitable()
        journal = self.applied(fake, [{"date": d(0), "price": 230}, {"date": d(3), "price": 230}])
        env = cw.plan_calendar(cw.rollback_change(journal), hosp(fake), FLOOR, today=TODAY + timedelta(days=1),
                               now=LATER, rollback=True)
        self.assertEqual([op["date"] for op in env["operations"]], [d(3)])
        self.assertTrue(any("in the past now" in w for w in env["warnings"]))

    def test_undo_of_a_raise_works_without_a_floor(self):
        fake = FakeHospitable()
        journal = self.applied(fake, [{"date": d(3), "price": 230}], settings={})
        env = cw.plan_calendar(cw.rollback_change(journal), hosp(fake), {}, today=TODAY, now=LATER, rollback=True)
        self.assertEqual(self.apply(env, fake, settings={}, when=LATER)["status"], "verified")

    def test_nothing_to_undo_says_so(self):
        fake = FakeHospitable()
        journal = self.applied(fake, [{"date": d(3), "price": 230}])
        fake.days[d(3)]["amount"] = 22000
        with self.assertRaisesRegex(CannotWrite, "Nothing to undo"):
            cw.plan_calendar(cw.rollback_change(journal), hosp(fake), FLOOR, today=TODAY, now=LATER, rollback=True)

    def test_the_snapshot_is_an_undo_source_too(self):
        fake = FakeHospitable()
        journal = self.applied(fake, [{"date": d(3), "price": 230}])
        snap = json.loads(Path(journal["snapshot_path"]).read_text())
        self.assertEqual(cw.rollback_change(snap), snap)

    def test_a_price_there_was_none_of_cannot_be_put_back_and_says_so(self):
        fake = FakeHospitable()
        fake.days[d(3)]["amount"] = None
        journal = self.applied(fake, [{"date": d(3), "price": 230, "min_stay": 3}])
        spec = cw.rollback_change(journal)
        self.assertEqual(spec["calendar_restore"], [{"date": d(3), "min_stay": 2}])
        env = cw.plan_calendar(spec, hosp(fake), FLOOR, today=TODAY, now=LATER, rollback=True)
        self.assertTrue(any(w.startswith("CANNOT PUT BACK") for w in env["warnings"]))

    def test_a_pricelabs_journal_is_not_a_calendar_undo(self):
        with self.assertRaisesRegex(CannotWrite, "another writer"):
            cw.rollback_change({"envelope": {"target": {"listing_id": LID, "pms": "x"}, "operations": []}})


# ------------------------------------------------------------------------------ units

class Units(unittest.TestCase):
    def test_minor_units_follow_iso_4217(self):
        self.assertEqual(cw.to_minor(150.25, "USD"), 15025)
        self.assertEqual(cw.to_minor(15000, "JPY"), 15000)
        self.assertEqual(cw.to_minor(150.25, "KWD"), 150250)
        self.assertEqual(cw.from_minor(15025, "cad"), 150.25)
        self.assertEqual(cw.from_minor(15000, "JPY"), 15000.0)
        self.assertEqual(cw.from_minor(150250, "BHD"), 150.25)

    def test_a_price_the_currency_cannot_carry_is_refused_not_rounded(self):
        for price, cur in ((15000.5, "JPY"), (150.004, "USD"), (float("nan"), "USD")):
            with self.subTest(price=price, cur=cur), self.assertRaises(CannotWrite):
                cw.to_minor(price, cur)
        with self.assertRaises(CannotWrite):
            cw.currency_decimals("dollars")

    def test_hospitable_jpy_reads_and_writes_whole_yen(self):
        fake = FakeHospitable(currency="JPY")
        t = hosp(fake)
        cal = t.read_calendar(LID, TODAY, TODAY + timedelta(days=1))
        self.assertEqual((cal["currency"], cal["days"][d(0)]["price"]), ("JPY", 200.0))
        t.write_calendar(LID, {d(3): {"price": 15000.0}}, "JPY")
        self.assertEqual(fake.writes()[-1][2], {"dates": [{"date": d(3), "price": {"amount": 15000}}]})
        with self.assertRaisesRegex(CannotWrite, "0 decimal places"):
            plan(fake, [{"date": d(3), "price": 15000.5}])

    def test_hospitable_calendar_with_two_currencies_is_refused(self):
        fake = FakeHospitable()
        t = hosp(fake)
        real = fake.open

        def mixed(req, timeout):
            resp = real(req, timeout)
            body = json.loads(resp.body)
            body["data"]["days"][1]["price"]["currency"] = "CAD"
            return Response(body)
        fake.open = mixed
        with self.assertRaisesRegex(CannotWrite, "exactly one currency"):
            t.read_calendar(LID, TODAY, TODAY + timedelta(days=2))


# ------------------------------------------------------------------------------ transport

class Transport(unittest.TestCase):
    def test_only_listed_calls_to_the_one_host(self):
        fake = FakeHospitable()
        t = hosp(fake)
        for method, path in (("DELETE", f"/v2/properties/{LID}/calendar"), ("GET", "/v2/reservations"),
                             ("PUT", f"/v2/properties/{LID}"), ("GET", f"/v2/properties/{LID}/../x")):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                t.http.request(method, path)
        self.assertEqual(fake.requests, [])

    def test_call_budget(self):
        fake = FakeHospitable()
        t = hosp(fake, max_calls=2)
        t.read_calendar(LID, TODAY, TODAY)
        t.read_calendar(LID, TODAY, TODAY)
        with self.assertRaisesRegex(CannotWrite, "budget"):
            t.read_calendar(LID, TODAY, TODAY)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(cw._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test/"))


# ------------------------------------------------------------------------------ Guesty

class FakeGuesty(FakeVendor):
    def __init__(self, currency="USD", **kw):
        super().__init__(**kw)
        self.currency = currency
        self.days = {d(i): {"price": 151 + i, "minNights": 2, "status": "booked" if i == 9 else "available"}
                     for i in range(0, 40)}

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        assert url.netloc == "open-api.guesty.com"
        assert req.headers.get("Authorization") == "Bearer cached-guesty-token"
        method, body = req.get_method(), json.loads(req.data) if req.data else None
        self.requests.append((method, url.path, body))
        if method == "GET" and url.path == f"/v1/availability-pricing/api/calendar/listings/{GID}":
            q = parse_qs(url.query)
            view = self._read_view()
            rows = [{"date": k, "listingId": GID, "currency": self.currency, **v}
                    for k, v in sorted(view.items()) if q["startDate"][0] <= k <= q["endDate"][0]]
            return Response({"status": 200, "data": {"days": rows}})
        if method == "PUT" and url.path == "/v1/availability-pricing/api/calendar/listings":
            if self.fail_status:
                self._fail(req)

            def apply():
                for p in body:
                    assert p["listingId"] == GID and p["startDate"] == p["endDate"]
                    if "price" in p:
                        self.days[p["startDate"]]["price"] = p["price"]
                    if "minNights" in p:
                        self.days[p["startDate"]]["minNights"] = p["minNights"]
            self._after_write(apply)
            return Response("ok")
        raise AssertionError(f"unexpected {method} {url.path}")


class GuestyTarget(Base):
    def setUp(self):
        super().setUp()
        tok = self.state / "guesty-token.json"
        tok.write_text(json.dumps({"access_token": "cached-guesty-token", "expires_at": time.time() + 86400}))
        self._env = os.environ.get("GUESTY_TOKEN_CACHE")
        os.environ["GUESTY_TOKEN_CACHE"] = str(tok)  # a cached token: nothing is ever minted here

    def tearDown(self):
        if self._env is None:
            os.environ.pop("GUESTY_TOKEN_CACHE", None)
        else:
            os.environ["GUESTY_TOKEN_CACHE"] = self._env
        super().tearDown()

    def change(self, items):
        return {"listing_id": GID, "target": "guesty", "reason": "test", "calendar_set": items}

    def test_reads_whole_units_and_writes_one_put_of_single_day_periods(self):
        fake = FakeGuesty()
        t = GuestyCalendarTarget(Conn(), opener=fake)
        env = cw.plan_calendar(self.change([{"date": d(3), "price": 160, "min_stay": 3}, {"date": d(5), "price": 170}]),
                               t, FLOOR, today=TODAY, now=NOW)
        self.assertEqual(env["operations"][0]["before"], {"price": 154.0, "min_stay": 2, "available": True})
        journal = cw.apply_envelope(env, GuestyCalendarTarget(Conn(), opener=fake), FLOOR, state_dir=self.state,
                                    today=TODAY, now=LATER, sleep=self.slept.append)
        self.assertEqual(journal["status"], "verified")
        [w] = fake.writes()
        self.assertEqual(w[2], [{"listingId": GID, "startDate": d(3), "endDate": d(3), "price": 160, "minNights": 3},
                                {"listingId": GID, "startDate": d(5), "endDate": d(5), "price": 170}])

    def test_a_fractional_price_is_refused_for_guesty(self):
        fake = FakeGuesty()
        with self.assertRaisesRegex(CannotWrite, "steps of 1 USD"):
            cw.plan_calendar(self.change([{"date": d(3), "price": 160.5}]), GuestyCalendarTarget(Conn(), opener=fake),
                             FLOOR, today=TODAY, now=NOW)

    def test_jpy_whole_yen(self):
        fake = FakeGuesty(currency="JPY")
        t = GuestyCalendarTarget(Conn(), opener=fake)
        self.assertEqual(t.read_calendar(GID, TODAY, TODAY)["currency"], "JPY")
        t.write_calendar(GID, {d(3): {"price": 15000.0}}, "JPY")
        self.assertEqual(fake.writes()[-1][2][0]["price"], 15000)

    def test_a_calendar_for_another_listing_is_refused(self):
        fake = FakeGuesty()
        real = fake.open

        def other(req, timeout):
            resp = real(req, timeout)
            body = json.loads(resp.body)
            body["data"]["days"][0]["listingId"] = "someone-else"
            return Response(body)
        fake.open = other
        with self.assertRaisesRegex(CannotWrite, "another listing"):
            GuestyCalendarTarget(Conn(), opener=fake).read_calendar(GID, TODAY, TODAY)

    def test_booked_status_reads_as_unavailable(self):
        cal = GuestyCalendarTarget(Conn(), opener=FakeGuesty()).read_calendar(GID, TODAY, TODAY + timedelta(days=9))
        self.assertIs(cal["days"][d(9)]["available"], False)


# ------------------------------------------------------------------------------ OwnerRez

class FakeOwnerRez(FakeVendor):
    def __init__(self, currency="USD", **kw):
        super().__init__(**kw)
        self.currency = currency
        self.days = {d(i): {"rent": 180.5 + i, "min_nights": 2, "status": "booked" if i == 9 else "available"}
                     for i in range(0, 40) if i != 20}  # night 20: no calendar data (documented)

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        assert url.netloc == "api.ownerrez.com"
        assert req.headers.get("Authorization", "").startswith("Basic ")
        assert req.headers.get("User-agent") == "RevenueManager/1.0"
        method, body = req.get_method(), json.loads(req.data) if req.data else None
        self.requests.append((method, url.path, body))
        if method == "GET" and url.path == f"/v2/calendar/{OID}":
            q = parse_qs(url.query)
            view = self._read_view()
            nights = [{"date": k, "status": v["status"], "rate": {"amount": v["rent"], "rent": v["rent"],
                                                                   "is_spot_rate": True},
                       "rules": {"min_nights": v["min_nights"]}}
                      for k, v in sorted(view.items()) if q["from"][0] <= k <= q["to"][0]]
            return Response({"property_id": int(OID), "currency_code": self.currency, "days": nights})
        if method == "PATCH" and url.path == "/v2/spotrates":
            if self.fail_status:
                self._fail(req)

            def apply():
                for r in body:
                    assert r["property_id"] == int(OID) and r["currency"] == self.currency
                    if "amount" in r:
                        self.days[r["date"]]["rent"] = r["amount"]
                    if "min_nights" in r:
                        self.days[r["date"]]["min_nights"] = r["min_nights"]
            self._after_write(apply)
            return Response(body)
        raise AssertionError(f"unexpected {method} {url.path}")


class OwnerRezTarget(Base):
    def change(self, items):
        return {"listing_id": OID, "target": "ownerrez", "reason": "test", "calendar_set": items}

    def test_reads_major_units_and_patches_decimal_spot_rates(self):
        fake = FakeOwnerRez()
        env = cw.plan_calendar(self.change([{"date": d(3), "price": 190.25}, {"date": d(4), "min_stay": 3}]),
                               OwnerRezCalendarTarget(Conn(), opener=fake), FLOOR, today=TODAY, now=NOW)
        self.assertEqual(env["operations"][0]["before"]["price"], 183.5)
        journal = cw.apply_envelope(env, OwnerRezCalendarTarget(Conn(), opener=fake), FLOOR, state_dir=self.state,
                                    today=TODAY, now=LATER, sleep=self.slept.append)
        self.assertEqual(journal["status"], "verified")
        [w] = fake.writes()
        self.assertEqual(w[2], [{"property_id": 4242, "date": d(3), "currency": "USD", "amount": 190.25},
                                {"property_id": 4242, "date": d(4), "currency": "USD", "min_nights": 3}])

    def test_calendar_lag_is_covered_by_settle_reads(self):
        fake = FakeOwnerRez(lag_reads=1)
        env = cw.plan_calendar(self.change([{"date": d(3), "price": 190}]),
                               OwnerRezCalendarTarget(Conn(), opener=fake), FLOOR, today=TODAY, now=NOW)
        journal = cw.apply_envelope(env, OwnerRezCalendarTarget(Conn(), opener=fake), FLOOR, state_dir=self.state,
                                    today=TODAY, now=LATER, sleep=self.slept.append)
        self.assertEqual((journal["status"], self.slept, len(fake.writes())), ("verified", [5], 1))

    def test_jpy_amount_is_whole_yen_in_major_units(self):
        fake = FakeOwnerRez(currency="JPY")
        OwnerRezCalendarTarget(Conn(), opener=fake).write_calendar(OID, {d(3): {"price": 15000.0}}, "JPY")
        self.assertEqual(fake.writes()[-1][2][0]["amount"], 15000.0)

    def test_an_omitted_night_is_refused_not_assumed(self):
        with self.assertRaisesRegex(CannotWrite, f"{d(20)} did not come back"):
            cw.plan_calendar(self.change([{"date": d(20), "price": 190}]),
                             OwnerRezCalendarTarget(Conn(), opener=FakeOwnerRez()), FLOOR, today=TODAY, now=NOW)

    def test_non_numeric_property_id_and_missing_credentials_are_refused(self):
        t = OwnerRezCalendarTarget(Conn(), opener=FakeOwnerRez())
        with self.assertRaisesRegex(CannotWrite, "whole numbers"):
            t.read_calendar("abc", TODAY, TODAY)
        with self.assertRaisesRegex(CannotWrite, "OWNERREZ_EMAIL"):
            OwnerRezCalendarTarget(Conn(OWNERREZ_TOKEN=""))

    def test_a_calendar_for_another_property_is_refused(self):
        fake = FakeOwnerRez()
        real = fake.open

        def other(req, timeout):
            body = json.loads(real(req, timeout).body)
            body["property_id"] = 1
            return Response(body)
        fake.open = other
        with self.assertRaisesRegex(CannotWrite, "another property"):
            OwnerRezCalendarTarget(Conn(), opener=fake).read_calendar(OID, TODAY, TODAY)


# ------------------------------------------------------------------------------ CLI

class Cli(Base):
    def setUp(self):
        super().setUp()
        self._cache = os.environ.get("RC_CACHE_DIR")
        os.environ["RC_CACHE_DIR"] = str(self.state / "cache")
        self.fake = FakeHospitable()
        fake = self.fake

        class Target(HospitableCalendarTarget):
            SETTLE_SECONDS = (0,)

            def __init__(self, connections):
                super().__init__(Conn(), opener=fake)
        self._resolve = apply_change.resolve
        apply_change.resolve = lambda name: Target if name == "hospitable" else self._resolve(name)
        self.settings = self.state / "row.json"
        self.settings.write_text(json.dumps({"property_id": LID, "settings": {"min_price": 150}}))

    def tearDown(self):
        apply_change.resolve = self._resolve
        if self._cache is None:
            os.environ.pop("RC_CACHE_DIR", None)
        else:
            os.environ["RC_CACHE_DIR"] = self._cache
        super().tearDown()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = apply_change.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_plan_apply_rollback_end_to_end(self):
        change = self.state / "c.json"
        today = date.today()
        when = (today + timedelta(days=3)).isoformat()
        self.fake.days = {(today + timedelta(days=i)).isoformat(): {"amount": 20000, "min_stay": 2, "available": True}
                          for i in range(10)}
        change.write_text(json.dumps({"listing_id": LID, "target": "hospitable", "reason": "cli",
                                      "calendar_set": [{"date": when, "price": 210}]}))
        code, out, err = self.run_cli("plan", "--target", "hospitable", "--change", str(change),
                                      "--settings", str(self.settings))
        self.assertEqual(code, 0, err)
        pid = out.split("--plan ")[-1].split(")")[0].strip()
        self.assertIn(f"apply_change.py apply --target hospitable --plan {pid}", out)
        code, out, err = self.run_cli("apply", "--target", "hospitable", "--plan", pid, "--no-audit",
                                      "--settings", str(self.settings))
        self.assertEqual(code, 0, err)
        self.assertIn("APPLIED AND VERIFIED", out)
        self.assertEqual(self.fake.days[when]["amount"], 21000)
        journal = out.split("--journal ")[-1].strip().splitlines()[0]
        code, out, err = self.run_cli("rollback", "--target", "hospitable", "--journal", journal,
                                      "--settings", str(self.settings))
        self.assertEqual(code, 0, err)
        self.assertIn("PROPOSED UNDO", out)
        self.assertIn("price 210.00 -> 200.00", out)

    def test_verify_subcommand_rechecks_a_journal_read_only(self):
        today = date.today()
        when = (today + timedelta(days=3)).isoformat()
        self.fake.days = {(today + timedelta(days=i)).isoformat(): {"amount": 20000, "min_stay": 2, "available": True}
                          for i in range(10)}
        self.fake.silent_noop = True
        change = self.state / "c.json"
        change.write_text(json.dumps({"listing_id": LID, "reason": "cli", "calendar_set": [{"date": when, "price": 210}]}))
        code, out, err = self.run_cli("plan", "--target", "hospitable", "--change", str(change),
                                      "--settings", str(self.settings))
        pid = out.split("--plan ")[-1].split(")")[0].strip()
        code, out, err = self.run_cli("apply", "--target", "hospitable", "--plan", pid, "--no-audit",
                                      "--settings", str(self.settings))
        self.assertEqual(code, 2)
        journal = err.split("--journal ")[1].split("`")[0].strip()
        code, out, err = self.run_cli("verify", "--target", "hospitable", "--journal", journal)
        self.assertEqual(code, 2)
        self.assertIn(f"BAD {when}", out)
        self.fake.days[when]["amount"] = 21000
        writes = len(self.fake.writes())
        code, out, err = self.run_cli("verify", "--target", "hospitable", "--journal", journal)
        self.assertEqual(code, 0, err)
        self.assertIn("VERIFIED NOW", out)
        self.assertEqual(len(self.fake.writes()), writes)

    def test_verify_is_only_for_calendar_targets(self):
        code, _, err = self.run_cli("verify", "--journal", "x.json")
        self.assertEqual(code, 2)
        self.assertIn("verify re-checks a PMS calendar journal", err)

    def test_pricelabs_journal_path_is_not_accepted_by_a_calendar_target(self):
        code, _, err = self.run_cli("rollback", "--target", "hospitable", "--journal", "/etc/hosts",
                                    "--settings", str(self.settings))
        self.assertEqual(code, 2)
        self.assertIn("not a journal or snapshot this writer saved", err)

    def test_every_pms_target_is_installed(self):
        import importlib
        for name, spec in apply_change.CALENDAR_TARGETS.items():
            with self.subTest(name=name):
                mod, _, cls = spec.partition(":")
                self.assertIs(self._resolve(name), getattr(importlib.import_module(mod), cls))

    def test_a_missing_writer_module_is_named_not_a_traceback(self):
        import importlib.util
        if importlib.util.find_spec("_beyond_write") is not None:
            self.skipTest("the Beyond writer is installed in this build")
        code, _, err = self.run_cli("plan", "--target", "beyond", "--change", "missing.json")
        self.assertEqual(code, 2)
        self.assertIn("the Beyond writer is not installed in this version", err)

    def test_no_settings_source_refuses_rather_than_guessing(self):
        change = self.state / "c.json"
        change.write_text(json.dumps({"listing_id": LID, "reason": "x", "calendar_set": [{"date": d(3), "price": 1}]}))

        class NoSupabase(apply_change.Connections):
            def __init__(self, env_files=()):
                self.values, self.servers, self.paths = {}, {}, {}
        real = apply_change.Connections
        apply_change.Connections = NoSupabase
        try:
            code, _, err = self.run_cli("plan", "--target", "hospitable", "--change", str(change))
        finally:
            apply_change.Connections = real
        self.assertEqual(code, 2)
        self.assertIn("Cannot read property_config", err)
        self.assertEqual(self.fake.requests, [])

    def test_calendar_audit_row_per_night(self):
        env = plan(FakeHospitable(), [{"date": d(3), "price": 230}])
        sql = apply_change.calendar_audit_statement({"envelope": env, "plan_id": cw.plan_id(env),
                                                     "journal_path": "/x/j.json", "status": "verified"})
        self.assertIn("'hospitable_calendar'", sql)
        self.assertIn(f"'{d(3)}'", sql)
        self.assertIn('\'{"price": 220.0}\'', sql)


# ------------------------------------------------------------------------------ Beyond routing

class BeyondRouting(Base):
    """_beyond_write is built on another branch. A stand-in with its module API proves the
    routing: --target beyond, and any saved plan / journal / change file whose pms is beyond."""

    def setUp(self):
        super().setUp()
        import sys
        import types
        import _mvp_write as mw
        self._cache = os.environ.get("RC_CACHE_DIR")
        os.environ["RC_CACHE_DIR"] = str(self.state / "cache")
        self.calls = calls = []
        m = types.ModuleType("_beyond_write")

        def plan_change(change, live, *, today=None, now=None, max_delta=None, rollback=False):
            calls.append(("plan", change.get("listing_id"), rollback))
            return {"version": 1, "target": {"listing_id": change["listing_id"], "pms": "beyond", "currency": "USD"},
                    "reason": change.get("reason", "r"), "listing_name": "B",
                    "operations": [{"kind": "listing_min_stay", "before": 2, "after": 3}], "warnings": [],
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

        def apply_batch(envs, live_for, *, state_dir, on_verified=None, **_):
            calls.append(("apply", [mw.plan_id(e) for e in envs]))
            for e in envs:
                on_verified({"envelope": e, "plan_id": mw.plan_id(e), "status": "verified",
                             "journal_path": str(Path(state_dir) / "journal" / "j.json"),
                             "verification": [{"kind": "listing_min_stay", "ok": True}]})
            return []
        m.plan_change, m.apply_batch = plan_change, apply_batch
        m.live_for = lambda connections, lid, pms=None: ("live", lid, pms)
        m.rollback_change = lambda j: {"listing_id": "b-1", "pms": "beyond", "reason": "undo"}
        m.describe = lambda env: "BEYOND CARD"
        m.load_plan, m.save_envelope, m.plan_id = mw.load_plan, mw.save_envelope, mw.plan_id
        self._had = sys.modules.get("_beyond_write")
        sys.modules["_beyond_write"] = m

    def tearDown(self):
        import sys
        if self._had is None:
            sys.modules.pop("_beyond_write", None)
        else:
            sys.modules["_beyond_write"] = self._had
        if self._cache is None:
            os.environ.pop("RC_CACHE_DIR", None)
        else:
            os.environ["RC_CACHE_DIR"] = self._cache
        super().tearDown()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = apply_change.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def change(self, pms):
        path = self.state / f"{pms}.json"
        path.write_text(json.dumps({"listing_id": "b-1", "pms": pms, "reason": "r", "listing_min_stay": 3}))
        return str(path)

    def test_target_beyond_plans_and_applies_through_the_beyond_writer(self):
        code, out, err = self.run_cli("plan", "--target", "beyond", "--change", self.change("beyond"))
        self.assertEqual(code, 0, err)
        self.assertIn("BEYOND CARD", out)
        pid = out.split("--plan ")[-1].split(")")[0].strip()
        self.assertIn(f"apply_change.py apply --target beyond --plan {pid}", out)
        code, out, err = self.run_cli("apply", "--plan", pid, "--no-audit")  # no flag: routed by pms
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls[-1], ("apply", [pid]))
        self.assertIn("apply_change.py rollback --target beyond --journal j.json", out)

    def test_a_change_file_naming_beyond_is_never_sent_to_pricelabs(self):
        code, out, err = self.run_cli("plan", "--change", self.change("beyond"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls, [("plan", "b-1", False)])

    def test_pricelabs_and_beyond_in_one_command_is_refused(self):
        code, _, err = self.run_cli("plan", "--change", self.change("beyond"), "--change", self.change("smartbnb"))
        self.assertEqual(code, 2)
        self.assertIn("cannot go in one command", err)
        self.assertEqual(self.calls, [])

    def test_an_incomplete_beyond_module_is_not_installed(self):
        import sys
        del sys.modules["_beyond_write"].apply_batch
        with self.assertRaisesRegex(CannotWrite, "the Beyond writer is not installed"):
            apply_change.resolve("beyond")

    def test_audit_tolerates_ops_without_a_date_and_labels_beyond(self):
        env = {
            "target": {"listing_id": "b-1", "pms": "beyond", "currency": "USD"}, "reason": "r",
            "operations": [{"kind": "listing_min_stay", "before": 2, "after": 3},
                           {"kind": "listing_price", "field": "base", "before": 100, "after": 110},
                           {"kind": "override", "date": d(3), "before": None, "after": {"price": 1}}]}
        sql = apply_change.audit_statement({"envelope": env, "plan_id": "p", "journal_path": "/j.json",
                                            "status": "verified"})
        for piece in ("'beyond_listing_min_stay', 'listing_min_stay'", "'beyond_listing_price', 'base'",
                      f"'beyond_override_set', '{d(3)}'"):
            self.assertIn(piece, sql)

    def test_pricelabs_audit_rows_are_unchanged(self):
        env = {"target": {"listing_id": "l-1", "pms": "smartbnb", "currency": "USD"}, "reason": "r",
               "operations": [{"kind": "listing_price", "field": "min", "before": 100, "after": 110},
                              {"kind": "override", "date": d(3), "before": {"price": "1"}, "after": None}]}
        sql = apply_change.audit_statement({"envelope": env, "plan_id": "p", "journal_path": "/j.json",
                                            "status": "verified"})
        self.assertIn("'listing_price', 'min'", sql)
        self.assertIn(f"'override_delete', '{d(3)}'", sql)
        self.assertNotIn("beyond_", sql)


if __name__ == "__main__":
    unittest.main()
