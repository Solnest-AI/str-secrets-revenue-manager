#!/usr/bin/env python3
"""Offline smoke test for reduce_prices.py -- no API key, no network.

Guards the four traps found against live PriceLabs data:
  1. 'Booked (Check-In)' is a booked night (matching == 'Booked' misses ~40%)
  2. Blocked nights are out of the occupancy denominator
  3. Empty booking_status_STLY means "no data", not "was available":
     a month with zero coverage must render blank, never 0%
  4. -1 / -2 sentinels never surface as real numbers
"""
import csv, io, json, subprocess, sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import reduce_prices as rp  # noqa: E402

rows = json.load(open(HERE / "test_fixture.json"))[0]["data"]
fails = []

def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}: {label}{'' if cond else '  -> ' + detail}")
    if not cond:
        fails.append(label)

print("reduce_prices smoke test\n")

# --- Tier B -------------------------------------------------------------
roll, exc, totals = rp.tier_b(rows, gap_pct=12.0)
months = {r["month"]: r for r in csv.DictReader(io.StringIO(roll))}

apr = months["2027-04"]
check("check-in night counts as booked", apr["booked"] == "2", f"got {apr['booked']}, want 2")
check("blocked night excluded from denominator", apr["bookable"] == "3", f"got {apr['bookable']}, want 3")
check("occupancy = booked/bookable", apr["occ_pct"] == "66.7", f"got {apr['occ_pct']}, want 66.7")
check("STLY reported when history exists", apr["stly_occ_pct"] not in ("", None),
      "blank despite 2/4 coverage")

sep = months["2026-09"]
check("STLY blank when zero coverage", sep["stly_occ_pct"] == "",
      f"got {sep['stly_occ_pct']!r}, want '' (0% would read as a YoY collapse)")
check("STLY coverage is shown", sep["stly_cov"] == "0/2", f"got {sep['stly_cov']}")

may = months["2027-05"]
check("STLY blocked night excluded from STLY denominator", may["stly_occ_pct"] == "100.0",
      f"got {may['stly_occ_pct']!r}, want 100.0 (1 booked / (2 - 1 blocked))")

ex = list(csv.DictReader(io.StringIO(exc)))
check("gap >= threshold surfaces as an exception", len(ex) == 1, f"got {len(ex)} rows")
if ex:
    check("exception carries the right date", ex[0]["date"] == "2027-04-04", ex[0]["date"])

# --- Tier A -------------------------------------------------------------
a = rp.tier_a(rows)
arows = list(csv.DictReader(io.StringIO(a)))
check("Tier A emits one row per date", len(arows) == len(rows), f"{len(arows)} vs {len(rows)}")
check("-1 sentinel blanked in ADR", arows[0]["ADR"] == "", f"got {arows[0]['ADR']!r}")
check("-2 sentinel blanked in ADR_STLY", arows[0]["ADR_STLY"] == "", f"got {arows[0]['ADR_STLY']!r}")
check("Tier A is smaller than raw JSON", len(a) < len(json.dumps(rows, indent=2)))

# --- num() --------------------------------------------------------------
check("num(-1) is None", rp.num(-1) is None)
check("num(-2) is None", rp.num(-2) is None)
check("num('407') parses", rp.num("407") == 407.0)

# --- reason flattening --------------------------------------------------
txt = rp.reason_slice(rows, {"2027-04-01"})
check("reason renders a factor line", "Seasonality -13% -> 608" in txt, txt[:120])
check("reason omits bulky listing_info", "avg_los" not in txt)
txt2 = rp.reason_slice(rows, {"2027-04-01", "2026-01-01"})
check("reason names a requested date that was not fetched", "2026-01-01" in txt2 and "not in the response" in txt2,
      txt2[-160:])

# --- CLI ----------------------------------------------------------------
p = subprocess.run([sys.executable, str(HERE / "reduce_prices.py")],
                   capture_output=True, text=True)
check("CLI exits non-zero with no args", p.returncode != 0)

# (summary moved to the end of the file)

# --- AirROI reducer + fact-class harness ----------------------------------
print("\nreduce_comps + factcheck smoke test\n")
import os, tempfile  # noqa: E402
import factcheck as fc  # noqa: E402
import reduce_comps as rc  # noqa: E402

fx = json.load(open(HERE / "test_fixture_airroi.json"))
fx_mixed = json.load(open(HERE / "test_fixture_airroi_mixed.json"))

def run_reducer(fixture, *extra):
    """Run reduce_comps against a fixture by pointing its cache at a temp dir."""
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, AIRROI_API_KEY="offline-test-key-never-used")
        params = {"bedrooms": 4, "baths": 2.0, "guests": 8, "radius": 0, "latitude": 50.88, "longitude": -119.9}
        # the subprocess resolves RC_CACHE_DIR/airroi via _cache; seed exactly there
        rc.CACHE_DIR = os.path.join(td, "airroi"); os.makedirs(rc.CACHE_DIR, exist_ok=True)
        blob = {"pulled_at": "2026-01-01T00:00:00+00:00", "request": params, "listings": fixture["listings"]}
        json.dump(blob, open(rc.cache_path(params), "w"))
        p = subprocess.run([sys.executable, str(HERE / "reduce_comps.py"), "--bedrooms", "4", "--baths", "2",
                            "--guests", "8", "--lat", "50.88", "--lng", "-119.9", "--ttl-days", "36500", *extra],
                           capture_output=True, text=True, env=dict(env, RC_CACHE_DIR=td))
        return p

# reduce_comps reads CACHE_DIR at import; make the subprocess honour the temp dir
import _cache  # noqa: E402
with patch.dict(os.environ, RC_CACHE_DIR="/tmp/rc-probe-xyz"):
    check("caches resolve under RC_CACHE_DIR when set (never inside the plugin tree)",
          _cache.cache_dir("airroi") == "/tmp/rc-probe-xyz/airroi")
with patch.dict(os.environ):
    os.environ.pop("RC_CACHE_DIR", None)
    expected = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                            "revenue-manager")
    check("default cache root is under the user cache dir, not the plugin",
          _cache.cache_dir() == expected and str(HERE) not in expected)

p = run_reducer(fx, "--currency", "CAD")
check("reducer exits 0 on a clean CAD set", p.returncode == 0, p.stderr[:200])
out = p.stdout
check("output has header + medians + CSV", out.count("\n") >= 8 and out.startswith("# source=airroi"), out[:120])
check("description/photos are NOT in the default output", "x" * 50 not in out and "photo" not in out)

full = fc.airroi_facts_full(fx)
try:
    red = fc.airroi_facts_reduced(out)
    bad = fc.compare(full, red, fc.AIRROI_FACTS)
except Exception as e:  # noqa: BLE001
    red, bad = {}, [f"reduced output unparseable: {e}"]
check("all 13 fact classes preserved (no subject)", not bad, "; ".join(bad))
check("median ADR from reduced CSV equals median at decision precision", red.get("adr_median") == full["adr_median"], f"{red.get('adr_median')} vs {full['adr_median']}")

p2 = run_reducer(fx, "--currency", "CAD", "--subject-id", "9999", "--subject-name", "subject")
check("subject exclusion exits 0", p2.returncode == 0, p2.stderr[:200])
full2 = fc.airroi_facts_full(fx, subject_id="9999")
try:
    red2 = fc.airroi_facts_reduced(p2.stdout); bad2 = fc.compare(full2, red2, fc.AIRROI_FACTS)
except Exception as e:  # noqa: BLE001
    red2, bad2 = {"subject_in_set": None, "subject_rank_revenue": None, "comp_count": None}, [f"unparseable: {e}"]
check("all 13 fact classes preserved (subject excluded)", not bad2, "; ".join(bad2))
check("subject reported in set at rank 6 of 6", red2["subject_in_set"] and red2["subject_rank_revenue"] == 6,
      f"{red2['subject_in_set']} {red2['subject_rank_revenue']}")
check("subject removed from CSV rows", "9999" not in p2.stdout.split("\n", 2)[2])
check("comp_count drops by exactly one", red2["comp_count"] == 5, str(red2["comp_count"]))
# This check used to pass on its SECOND clause: --subject-id already removed the only
# matching comp before reduce_comps computed name_hits, so the warning could never fire
# and the guard went untested. The guard exists for the case where the id does NOT
# match, so exercise it with --subject-name alone.
p2n = run_reducer(fx, "--currency", "CAD", "--subject-name", "subject")
check("subject-name guard fires when the id does NOT remove the matching comp",
      "subject_name" in p2n.stdout.lower() or "subject_name" in p2n.stderr.lower(),
      f"stdout head: {p2n.stdout[:200]!r}")

p3 = run_reducer(fx, "--currency", "USD")
check("currency guard: CAD set + expected USD -> exit 2", p3.returncode == 2, f"rc={p3.returncode}")
check("currency guard prints nothing to stdout on refusal", p3.stdout.strip() == "", p3.stdout[:80])

p4 = run_reducer(fx_mixed, "--currency", "CAD")
check("mixed-currency set -> exit 2 even when expected matches most", p4.returncode == 2, f"rc={p4.returncode}")

p5 = run_reducer(fx, "--currency", "CAD", "--full")
check("--full includes description column", "description" in p5.stdout.split("\n", 2)[2].split("\n")[0])

p6 = subprocess.run([sys.executable, str(HERE / "reduce_comps.py"), "--bedrooms", "4", "--baths", "2", "--guests", "8"],
                    capture_output=True, text=True)
check("no location -> argparse error, exit 2", p6.returncode == 2)

# (summary moved to the end of the file)

# --- neighborhood reducer + fact-class harness -----------------------------
print("\nreduce_neighborhood + factcheck smoke test\n")
import reduce_neighborhood  # noqa: E402,F401  (import-clean check)

nfx = json.load(open(HERE / "test_fixture_neighborhood.json"))

def run_nb(*extra, seed=True):
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, RC_CACHE_DIR=td, PRICELABS_API_KEY="offline-test-key-never-used")
        if seed:
            os.makedirs(os.path.join(td, "neighborhood"), exist_ok=True)
            for name in (_cache.cache_name("nb", "fixture-listing", "smartbnb"),):
                json.dump({"pulled_at": "2026-01-01T00:00:00+00:00", "listing": "fixture-listing", "pms": "smartbnb",
                           "data": nfx["data"]}, open(os.path.join(td, "neighborhood", name), "w"))
        else:
            # Exercise the unavailable-fetch branch without sending an invalid key to
            # the real service. This suite is offline, including negative cases.
            shim = Path(td) / "sitecustomize.py"
            shim.write_text("import urllib.request\n"
                            "def offline(*args, **kwargs):\n"
                            "    raise RuntimeError('offline test: no network')\n"
                            "urllib.request.urlopen = offline\n")
            env["PYTHONPATH"] = td
        return subprocess.run([sys.executable, str(HERE / "reduce_neighborhood.py"), "--listing", "fixture-listing",
                               "--ttl-days", "36500", "--today", "2026-06-01", *extra], capture_output=True, text=True, env=env)

p = run_nb("--bedrooms", "4", "--lat", "50.88", "--lng", "-119.9", "--currency", "CAD")
check("neighborhood reducer exits 0", p.returncode == 0, p.stderr[:200])
check("three blocks present", all(f"## {b}" in p.stdout for b in ("daily", "monthly", "kpi")), p.stdout[:200])
check("history dates are NOT in the daily block", "2026-12-2" not in p.stdout.split("## daily")[1].split("## monthly")[0])
check("only the requested category's base prices", "base_p50=790" in p.stdout and "base_p50=400" not in p.stdout)
nfull = fc.neighborhood_facts_full(nfx, "4")
nred = fc.neighborhood_facts_reduced(p.stdout)
nbad = fc.compare(nfull, nred, fc.NEIGHBORHOOD_FACTS)
check("all 25 neighborhood fact classes preserved", not nbad, "; ".join(nbad))
check("per-date p50 digest matches (every daily value survived)", nred["p50_by_date_digest"] == nfull["p50_by_date_digest"])

p2 = run_nb("--bedrooms", "4", "--lat", "50.88", "--lng", "-119.9", "--days", "5")
nfull5 = fc.neighborhood_facts_full(nfx, "4", 5)
nred5 = fc.neighborhood_facts_reduced(p2.stdout)
check("--days 5 -> exactly 5 daily rows", nred5["daily_rows"] == 5, str(nred5["daily_rows"]))
check("--days 5 -> all 25 facts preserved for the shorter window", not fc.compare(nfull5, nred5, fc.NEIGHBORHOOD_FACTS))

p3 = run_nb("--bedrooms", "7", "--lat", "50.88", "--lng", "-119.9")
check("absent bedroom category -> exit 2, never substituted", p3.returncode == 2 and "3', '4'" in p3.stderr, p3.stderr[:120])
p4 = run_nb("--bedrooms", "4", "--lat", "50.88", "--lng", "-119.9", "--currency", "USD")
check("currency mismatch -> exit 2", p4.returncode == 2 and p4.stdout.strip() == "")
p5 = run_nb("--bedrooms", "3", "--lat", "50.8849", "--lng", "-119.9021")
check("the same listing reuses its own cache when coordinates are restated", "cache=hit" in p5.stdout and "category=3" in p5.stdout, p5.stdout[:160] + p5.stderr[:120])
p6 = run_nb("--bedrooms", "4", seed=False)
check("no cache + no real key -> exit 2, not a crash", p6.returncode == 2, p6.stderr[:120])

# (summary moved to the end of the file)

# --- calendar reconciliation block + fact-class harness ---------------------
print("\nreconcile_pms calendar block + factcheck smoke test\n")
import reconcile_pms as rp2  # noqa: E402

def day(d, status="AVAILABLE", price_cents=40000, min_stay=2, note=None):
    return {"date": d, "min_stay": min_stay, "note": note,
            "status": {"reason": status, "available": status == "AVAILABLE"},
            "price": {"amount": price_cents, "currency": "CAD"}}
def pl(price=400, status="", min_stay=2, unbookable=0):
    return {"price": price, "booking_status": status, "min_stay": min_stay, "unbookable": unbookable}

cal_days = [
    day("2027-01-01"),                                        # clean pair, ratio 1.0
    day("2027-01-02"),
    day("2027-01-03", "RESERVED", 60000),                     # PL says booked too: agreed
    day("2027-01-04", "RESERVED", 70000, note="Off the platform"),   # PL says available: INVISIBLE
    day("2027-01-05", "RESERVED", 50000, note="Owner's Stay"),       # invisible + owner stay
    day("2027-01-06", price_cents=44000),                     # ratio 1.10 -> price drift
    day("2027-01-07", min_stay=3),                            # min-stay 3 vs PL 2 -> mismatch
    day("2027-01-08", min_stay=3),                            # min-stay 3 vs PL sentinel -1 -> NOT a mismatch
    day("2027-01-09", price_cents=0),                         # zero price: excluded from pairs
    day("2027-01-10"),
    day("2027-01-11", "BLOCKED"),                             # host block run (2 nights) ...
    day("2027-01-12", "BLOCKED"),
    day("2027-01-13"),                                        # ... one open night boxed in by the block and a booking = orphan gap
    day("2027-01-14", "RESERVED", 60000),
]
cal_pl = {
    "2027-01-01": pl(), "2027-01-02": pl(), "2027-01-03": pl(600, "Booked"),
    "2027-01-04": pl(700), "2027-01-05": pl(500), "2027-01-06": pl(400),
    "2027-01-07": pl(), "2027-01-08": pl(min_stay=-1), "2027-01-09": pl(), "2027-01-10": pl(),
    "2027-01-11": pl(status="Blocked"), "2027-01-12": pl(status="Blocked"), "2027-01-13": pl(), "2027-01-14": pl(600, "Booked"),
}
c = fc.calendar_rows(cal_days, cal_pl)
check("reserved nights counted", c["pms_reserved"] == 4, str(c["pms_reserved"]))
check("invisible = PMS reserved but PL available (2)", len(c["invisible"]) == 2 and {d["date"] for d in c["invisible"]} == {"2027-01-04", "2027-01-05"})
check("owner stay detected from the PMS note", c["owner_stay_count"] == 1)
check("agreed booked night is NOT invisible", "2027-01-03" not in {d["date"] for d in c["invisible"]})
check("sync ratio uses sellable, non-zero paired nights; blocked dates are excluded", c["paired_dates"] == 7, str(c["paired_dates"]))
check("markup median is 1.0 for a no-markup listing", c["markup_median"] == 1.0, str(c["markup_median"]))
check("ratio 1.10 date is a price drift row", any(d["date"] == "2027-01-06" and "price" in d["why"] for d in c["drift"]))
check("min-stay 3 vs 2 is a mismatch", c["min_stay_mismatch"] == 1 and any(d["date"] == "2027-01-07" for d in c["drift"]), str(c["min_stay_mismatch"]))
check("PL sentinel -1 min-stay is NOT a mismatch", not any(d["date"] == "2027-01-08" for d in c["drift"]))
check("pms_min_mode is the most common PMS min-stay", c["pms_min_mode"] == 2)
check("host block run collapsed to one row of 2 nights", len(c["blocked_runs"]) == 1 and c["blocked_runs"][0]["nights"] == 2 and c["blocked_runs"][0]["start"] == "2027-01-11", str(c["blocked_runs"]))
check("one open night boxed in by a block and a booking is an orphan gap", len(c["gaps"]) == 1 and c["gaps"][0]["start"] == "2027-01-13" and c["gaps"][0]["nights"] == 1, str(c["gaps"]))
check("the two open nights at the start are NOT a gap (nothing before them)", not any(g["start"] == "2027-01-01" for g in c["gaps"]))

import io as _io
buf = _io.StringIO(); rp2.print_calendar_block("fixture-listing-id", "Fixture House", c, buf)
block = buf.getvalue()
check("block has header + invisible + drift + blocked + gaps sections", all(x in block for x in ("## calendar", "### invisible", "### drift", "### blocked", "### gaps")))
cfull = fc.calendar_facts_full({"pms_days": cal_days, "pl_rows": cal_pl})
cred = fc.calendar_facts_reduced(block)
cbad = fc.compare(cfull, cred, fc.CALENDAR_FACTS)
check("all 18 calendar fact classes survive the printed block", not cbad, "; ".join(cbad))
check("trailing non-CSV text after a blank line does not pollute the drift block",
      not fc.compare(cfull, fc.calendar_facts_reduced(block + "\nExclusion set written to /x\n*** 2 nights ***\n"), fc.CALENDAR_FACTS))
ltr = fc.calendar_rows([day(f"2027-02-{i:02d}", min_stay=90) for i in range(1, 8)], {f"2027-02-{i:02d}": pl(min_stay=-1) for i in range(1, 8)})
buf2 = _io.StringIO(); rp2.print_calendar_block("x", "LTR", ltr, buf2)
check("90-night min-stay prints the long-term-rental NOTE, zero drift rows", "long-term rental" in buf2.getvalue() and len(ltr["drift"]) == 0)

# (summary moved to the end of the file)

# --- reservations reducer + fact-class harness ------------------------------
print("\nreduce_reservations + factcheck smoke test\n")
import reduce_reservations as rr  # noqa: E402

def resv(rid, ci, booked, nights, rev, status="booked", channel="airbnb", cancelled_on=None):
    return {"listing_id": "fixture-listing", "reservation_id": rid, "check_in": ci,
            "check_out": ci, "booking_status": status, "booked_date": booked + "T12:00:00.000Z",
            "rental_revenue": str(rev), "no_of_days": nights, "currency": "CAD",
            "cancelled_on": cancelled_on, "booking_channel": channel, "guestName": "Jane Q. Private",
            "guest_count": 2}
TODAY = "2027-01-15"
res_fx = {"data": [
    resv("r1", "2027-02-01", "2026-11-01", 3, 900.0),                      # lead 92, LOS 3
    resv("r2", "2027-02-10", "2027-01-10", 1, 200.0, channel="vrbo"),        # lead 31, LOS 1, recent (5d)
    resv("r3", "2027-02-20", "2027-01-14", 7, 2100.0, channel="manual"),     # lead 37, LOS 7, recent (1d)
    resv("r4", "2027-03-05", "2026-12-01", 2, 500.0, status="cancelled"),    # cancelled: counted, excluded
    resv("r5", "2027-03-10", "2027-03-08", 4, 1000.0, channel="bcom"),       # lead 2, LOS 4
    resv("r6", "2027-03-12", "2026-06-01", 2, 400.0, cancelled_on="2026-07-01"),  # cancelled via date
]}
# Deliberately NOT written next to the source. This file is git-tracked, nothing reads
# it, and it was regenerated from the inline res_fx on every run -- a decorative input
# that looks authoritative, and a test with a working-tree side effect. Everything below
# uses res_fx directly.

rows = fc.reservation_rows(res_fx["data"], TODAY)
t = fc.reservation_tables(rows)
check("live bookings = 4, cancelled = 2 (status OR cancelled_on)", t["bookings"] == 4 and t["cancelled"] == 2, f"{t['bookings']}/{t['cancelled']}")
check("nights and revenue exclude cancelled", t["nights"] == 15 and t["revenue"] == 4200, f"{t['nights']}/{t['revenue']}")
check("overall ADR = revenue / nights", t["adr"] == 280.0, str(t["adr"]))
check("LOS distribution sums to 100", abs(sum(v for v in t["los"].values()) - 100.0) < 0.2, str(t["los"]))
check("lead-time buckets: one 0-7, one 31-60 x2, one 61+", t["lead"]["d0_7"] == 25.0 and t["lead"]["d31_60"] == 50.0 and t["lead"]["d61p"] == 25.0, str(t["lead"]))
check("channel mix counts live bookings only", t["channels"] == {"airbnb": 1, "vrbo": 1, "bcom": 1, "manual": 1, "other": 0}, str(t["channels"]))
check("recent = booked within 14 days of today (2)", len(t["recent"]) == 2, str(len(t["recent"])))
check("monthly rows by check-in month (2027-02, 2027-03)", [m["month"] for m in t["monthly"]] == ["2027-02", "2027-03"])
check("guestName is dropped by the row normaliser", not any("guestName" in r or "Jane" in json.dumps(r) for r in rows))
check("strip_pii removes every PII field", "guestName" not in rr.strip_pii(res_fx["data"][0]))

with tempfile.TemporaryDirectory() as td:
    env = dict(os.environ, RC_CACHE_DIR=td, PRICELABS_API_KEY="offline-test-key-never-used")
    os.makedirs(os.path.join(td, "reservations"), exist_ok=True)
    from datetime import date as _d, timedelta as _td
    d_from = (_d.fromisoformat(TODAY) - _td(days=730)).isoformat(); d_to = (_d.fromisoformat(TODAY) + _td(days=365)).isoformat()
    json.dump({"pulled_at": "2027-01-15T00:00:00+00:00", "listing": "fixture-listing", "pms": "smartbnb",
               "window": [d_from, d_to], "data": [rr.strip_pii(r) for r in res_fx["data"]]},
              open(os.path.join(td, "reservations", _cache.cache_name("res", "fixture-listing", "smartbnb", d_from, d_to)), "w"))
    pr = subprocess.run([sys.executable, str(HERE / "reduce_reservations.py"), "--listing", "fixture-listing",
                         "--today", TODAY, "--currency", "CAD", "--ttl-days", "36500"], capture_output=True, text=True, env=env)
check("reservations reducer exits 0 from cache", pr.returncode == 0, pr.stderr[:200])
check("output never contains a guest name", "Jane" not in pr.stdout and "guest" not in pr.stdout.lower().replace("guest_count", ""))
rfull = fc.reservation_facts_full(res_fx, TODAY)
try:
    rred = fc.reservation_facts_reduced(pr.stdout); rbad = fc.compare(rfull, rred, fc.RESERVATION_FACTS)
except Exception as e:  # noqa: BLE001
    rbad = [f"unparseable: {e}"]
check("all 12 reservation fact classes survive the printed rollup", not rbad, "; ".join(rbad))
with tempfile.TemporaryDirectory() as td:
    env = dict(os.environ, RC_CACHE_DIR=td, PRICELABS_API_KEY="offline-test-key-never-used")
    os.makedirs(os.path.join(td, "reservations"), exist_ok=True)
    json.dump({"pulled_at": "2027-01-15T00:00:00+00:00", "listing": "fixture-listing", "pms": "smartbnb",
               "window": [d_from, d_to], "data": [rr.strip_pii(r) for r in res_fx["data"]]},
              open(os.path.join(td, "reservations", _cache.cache_name("res", "fixture-listing", "smartbnb", d_from, d_to)), "w"))
    pr2 = subprocess.run([sys.executable, str(HERE / "reduce_reservations.py"), "--listing", "fixture-listing",
                          "--today", TODAY, "--currency", "USD", "--ttl-days", "36500"], capture_output=True, text=True, env=env)
check("currency mismatch -> exit 2, nothing printed", pr2.returncode == 2 and pr2.stdout.strip() == "")

# --- pagination: the endpoint pages on `offset`; a repeated page must not be summed twice ---
import io as _io
import urllib.request as _ur


class _FakeResp(_io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _serve(pages_by_offset):
    calls = []
    def fake_urlopen(req, timeout=0):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(req.full_url).query)
        off = int(q.get("offset", ["0"])[0]); calls.append(off)
        body = pages_by_offset(off)
        return _FakeResp(json.dumps(body).encode())
    return fake_urlopen, calls


_orig = _ur.urlopen
try:
    # 1) API that ignores offset (the bug's shape): same 3 rows forever, next_page always true
    same = {"pms_name": "smartbnb", "next_page": True,
            "data": [{"reservation_id": f"R{i}", "guestName": "x", "check_in": "2027-01-01"} for i in range(3)]}
    _ur.urlopen, calls = _serve(lambda off: same)
    # A pager that replays the same rows while still claiming next_page=true leaves us
    # holding a prefix of unknown completeness. Stopping is right; calling it the whole
    # history is not. The old assertion here locked in returning it as complete.
    try:
        rr.fetch("fixture-listing", "smartbnb", "2026-01-01", "2027-12-31", "k")
        _stalled = None
    except rr.CannotProduce as _e:
        _stalled = str(_e)
    check("a stalled pager REFUSES instead of returning an unknown-completeness prefix",
          _stalled is not None and "stalled" in _stalled, f"got {_stalled!r}")
    check("it stops after 2 calls rather than looping (the 20x duplication guard holds)",
          len(calls) == 2, f"calls={calls}")
    # the same replayed page, but honestly flagged as the last one, still de-duplicates
    same_last = dict(same, next_page=False)
    _ur.urlopen, calls = _serve(lambda off: same_last)
    got, _foreign = rr.fetch("fixture-listing", "smartbnb", "2026-01-01", "2027-12-31", "k")
    check("a repeated page that does NOT claim more is not double counted (3 rows)",
          len(got) == 3, f"rows={len(got)} calls={calls}")
    check("guest names stripped by the pager", all("guestName" not in r for r in got))
    # 2) honest offset pagination: 2 full pages + a short last page, next_page false at the end
    def paged(off):
        n = {0: 100, 100: 100, 200: 7}.get(off, 0)
        return {"pms_name": "smartbnb", "next_page": off + n < 207,
                "data": [{"reservation_id": f"R{off + i}", "check_in": "2027-01-01"} for i in range(n)]}
    _ur.urlopen, calls = _serve(paged)
    got, _foreign = rr.fetch("fixture-listing", "smartbnb", "2026-01-01", "2027-12-31", "k")
    check("offset pagination collects every distinct row once (207)", len(got) == 207 and calls == [0, 100, 200], f"rows={len(got)} calls={calls}")
    check("a clean pull reports zero foreign rows", _foreign == 0, f"got {_foreign}")
    # a page repeating one id INSIDE itself must not be counted twice
    def dupe_in_page(off):
        if off:
            return {"pms_name": "smartbnb", "next_page": False, "data": []}
        return {"pms_name": "smartbnb", "next_page": False,
                "data": [{"reservation_id": "R1", "check_in": "2027-01-01"},
                         {"reservation_id": "R1", "check_in": "2027-01-01"},
                         {"reservation_id": "R2", "check_in": "2027-01-01"}]}
    _ur.urlopen, calls = _serve(dupe_in_page)
    got, _ = rr.fetch("fixture-listing", "smartbnb", "2026-01-01", "2027-12-31", "k")
    check("a duplicate WITHIN one page is de-duplicated (2 rows, not 3)",
          len(got) == 2, f"rows={len(got)} -- seen_ids was updated after the whole page")
    # a row belonging to a different listing must never reach this listing's totals
    def foreign_page(off):
        if off:
            return {"pms_name": "smartbnb", "next_page": False, "data": []}
        return {"pms_name": "smartbnb", "next_page": False,
                "data": [{"reservation_id": "R1", "listing_id": "fixture-listing",
                          "check_in": "2027-01-01"},
                         {"reservation_id": "R2", "listing_id": "someone-else",
                          "check_in": "2027-01-01"}]}
    _ur.urlopen, calls = _serve(foreign_page)
    got, _foreign = rr.fetch("fixture-listing", "smartbnb", "2026-01-01", "2027-12-31", "k")
    check("a row for another listing is dropped and counted, not silently summed",
          len(got) == 1 and _foreign == 1, f"rows={len(got)} foreign={_foreign}")
finally:
    _ur.urlopen = _orig

# --- reduce_overrides: per-date override rows collapse into runs, every run checked ------------
print("\nreduce_overrides + factcheck smoke test\n")
import reduce_overrides  # noqa: E402,F401  (import-clean check)

ov_rows = []
def _ov(d, price=None, ptype=None, min_stay=None, reason=""):
    ov_rows.append({"date": d, "price": price, "price_type": ptype, "min_stay": min_stay, "reason": reason,
                    "currency": "CAD", "created_at": "2026-01-01T00:00:00.000Z", "updated_at": "2026-01-01T00:00:00.000Z"})
for i in range(1, 6):    _ov(f"2026-10-0{i}", "-25", "percent", 1, "Shoulder fall, lowest demand")   # run 1 (reason has a comma)
for i in range(6, 9):    _ov(f"2026-10-0{i}", "-25", "percent", 1, "Shoulder fall, lowest demand")   # same run continues
for i in range(10, 13):  _ov(f"2026-10-{i}", "-25", "percent", 1, "Shoulder fall, lowest demand")    # gap on the 9th -> new run
for i in range(20, 23):  _ov(f"2026-12-{i}", 2500, "fixed", 3, "Christmas peak")                     # fixed price run
for i in range(1, 4):    _ov(f"2027-02-0{i}", None, None, 4, "")                                     # min-stay only run
_ov("2026-01-05", "10", "percent", 1, "history")                                                     # in the past: dropped by --today
ov_fx = {"overrides": ov_rows}
with tempfile.TemporaryDirectory() as td:
    env = dict(os.environ, RC_CACHE_DIR=td, PRICELABS_API_KEY="offline-test-key-never-used")
    os.makedirs(os.path.join(td, "overrides"), exist_ok=True)
    json.dump({"pulled_at": "2026-09-01T00:00:00+00:00", "listing": "fixture-listing", "pms": "smartbnb", "data": ov_fx},
              open(os.path.join(td, "overrides", _cache.cache_name("ov", "fixture-listing", "smartbnb")), "w"))
    po = subprocess.run([sys.executable, str(HERE / "reduce_overrides.py"), "--listing", "fixture-listing",
                         "--today", "2026-09-01", "--ttl-days", "36500"], capture_output=True, text=True, env=env)
check("overrides reducer exits 0", po.returncode == 0, po.stderr[:200])
check("four runs (Oct 1-8 contiguous, gap on the 9th splits, fixed, min-stay only), past row dropped", "runs=4" in po.stdout and "dates=17" in po.stdout and "dropped_past=1" in po.stdout, po.stdout[:300])
check("run row carries the comma reason intact", '"Shoulder fall, lowest demand"' in po.stdout)
try:
    ofull = fc.override_facts_full(ov_fx, "2026-09-01")
    ored = fc.override_facts_reduced(po.stdout)
    obad = fc.compare(ofull, ored, fc.OVERRIDE_FACTS)
except Exception as e:  # noqa: BLE001
    obad = [f"unparseable: {e}"]
check("all override fact classes preserved (every run's dates, value, type, min-stay, reason)", not obad, "; ".join(obad))
with tempfile.TemporaryDirectory() as td:
    env = dict(os.environ, RC_CACHE_DIR=td, PRICELABS_API_KEY="offline-test-key-never-used")
    os.makedirs(os.path.join(td, "overrides"), exist_ok=True)
    json.dump({"pulled_at": "2026-09-01T00:00:00+00:00", "listing": "fixture-listing", "pms": "smartbnb", "data": {"overrides": []}},
              open(os.path.join(td, "overrides", _cache.cache_name("ov", "fixture-listing", "smartbnb")), "w"))
    po2 = subprocess.run([sys.executable, str(HERE / "reduce_overrides.py"), "--listing", "fixture-listing",
                          "--today", "2026-09-01", "--ttl-days", "36500"], capture_output=True, text=True, env=env)
check("no overrides is a valid answer: exit 0, dates=0", po2.returncode == 0 and "dates=0" in po2.stdout, po2.stdout[:200] + po2.stderr[:200])

# --- pacing line (Tier A header): rolling occupancy vs STLY with blocked nights out of the denominator
rows = json.load(open(HERE / "test_fixture.json"))[0]["data"]   # `rows` was rebound to reservations above
pl = rp.pacing_line(rows, "2027-03-31", windows=(8,))
check("pacing line names the window and both occupancies", pl.startswith("[pacing] next8 occ=") and "stly=" in pl, pl)
_fwd = [r for r in rows if str(r["date"]) > "2027-03-31"]          # the two 2026-09 rows are history
_booked = sum(1 for r in _fwd if rp.is_booked(str(r.get("booking_status", ""))))
_blocked = sum(1 for r in _fwd if str(r.get("booking_status", "")).strip().lower() == "blocked")
_expect = 100.0 * _booked / (len(_fwd) - _blocked)
check("pacing occupancy excludes blocked nights from the denominator and past rows", f"occ={_expect:.1f}%" in pl, pl)
_six = rp.pacing_line(rows, "2027-03-31", windows=(6,))
check("past rows are not in the window (next8 and next6 see the same 6 forward rows)", f"occ={_expect:.1f}%" in _six and _six.split("occ=")[1] == pl.split("occ=")[1], f"{pl} vs {_six}")
_no_hist = [dict(r, booking_status_STLY="") for r in rows]
check("no STLY history -> stly=n/a, never 0%", "stly=n/a" in rp.pacing_line(_no_hist, "2027-03-31", windows=(8,)))
_behind = [dict(r, booking_status="", booking_status_STLY="Booked") for r in rows]
check("0% booked against 100% STLY reads 'behind'", "(behind)" in rp.pacing_line(_behind, "2027-03-31", windows=(8,)))

# --- tier_b must not read a -1/-2 STLY sentinel as real history -------------
# pacing_line() filters SENTINELS on this field; tier_b did not, so the sentinel
# landed in the denominator as "available last year" and printed a fake 0% STLY,
# i.e. a -100% year-over-year that never happened.
_sent_rows = [dict(r, booking_status_STLY="-1") for r in rows]
_roll_s, _, _ = rp.tier_b(_sent_rows, gap_pct=12.0)
_months_s = {r["month"]: r for r in csv.DictReader(io.StringIO(_roll_s))}
check("tier_b: a '-1' STLY sentinel is NOT counted as last-year coverage",
      all(m["stly_occ_pct"] == "" for m in _months_s.values()),
      f"got {[(k, m['stly_occ_pct'], m.get('stly_cov')) for k, m in _months_s.items()]}")
for _form in ("-2", "-1.0", "-2.0"):
    _r2 = [dict(r, booking_status_STLY=_form) for r in rows]
    _roll2, _, _ = rp.tier_b(_r2, gap_pct=12.0)
    check(f"tier_b: the {_form!r} sentinel form is filtered too",
          all(m["stly_occ_pct"] == ""
              for m in csv.DictReader(io.StringIO(_roll2))),
          "reduce_prices.SENTINELS was missing the string float forms")
_real = [dict(r, booking_status_STLY="Booked") for r in rows]
_roll_r, _, _ = rp.tier_b(_real, gap_pct=12.0)
check("tier_b: REAL STLY history still produces a percentage",
      any(m["stly_occ_pct"] not in ("", None)
          for m in csv.DictReader(io.StringIO(_roll_r))),
      "the sentinel filter must not suppress genuine history")

# --- a payload-shape change must never read as "nothing there" --------------
import factcheck as _fc  # noqa: E402

_ov_live = [{"date": f"2027-04-0{i}", "price": 200, "price_type": "fixed"}
            for i in range(1, 6)]
check("override_runs keeps well-formed future rows",
      len(_fc.override_runs(_ov_live, "2027-03-31")) >= 1)
_ov_undated = [dict(r, date=None) for r in _ov_live]
check("override_runs itself drops undated rows (which is why main() must reconcile)",
      _fc.override_runs(_ov_undated, "2027-03-31") == [],
      "the guard cannot live here: this function is also the fact-harness side")
_src = (HERE / "reduce_overrides.py").read_text()
check("reduce_overrides refuses a payload whose rows carry no usable date",
      "carry no usable `date` field" in _src and "do not reconcile" in _src,
      "a renamed date field must exit 2, never print an empty table at exit 0")
check("reduce_overrides prints rows_in so the reconciliation is visible",
      "rows_in={len(rows)}" in _src)

_rsrc = (HERE / "reduce_reservations.py").read_text()
check("reduce_reservations refuses rows with no reservation_id",
      "cannot be de-duplicated across pages" in _rsrc,
      "a missing id poisons seen_ids with None and truncates history to page 1")

_csrc = (HERE / "reconcile_pms.py").read_text()
check("reconcile_pms treats an EMPTY PriceLabs row map as unsynced, not as 0 defects",
      "if lid not in pl or not pl[lid]:" in _csrc,
      "its own comment claimed this coverage before the code had it")

# --- a BLOCKED record is not a sold night -----------------------------------
_blk = fc.reservation_rows([
    {"reservation_id": "B1", "check_in": "2027-01-10", "check_out": "2027-01-13",
     "booked_date": "2026-12-01", "no_of_days": 3, "rental_revenue": 600,
     "currency": "CAD", "booking_status": "blocked", "booking_channel": "Airbnb"},
    {"reservation_id": "B2", "check_in": "2027-01-20", "check_out": "2027-01-22",
     "booked_date": "2026-12-01", "no_of_days": 2, "rental_revenue": 400,
     "currency": "CAD", "booking_status": "booked", "booking_channel": "Airbnb"},
], "2027-01-01")
_bt = fc.reservation_tables(_blk)
check("a blocked record is not counted as a live booking",
      _bt["bookings"] == 1, f"got {_bt['bookings']} -- an owner stay is not a sale")
check("a blocked record's nights do not inflate occupancy",
      _bt["nights"] == 2, f"got {_bt['nights']}")
check("a blocked record's revenue does not inflate the total",
      _bt["revenue"] == 400, f"got {_bt['revenue']}")
check("blocked records are counted and reported, not silently dropped",
      _bt["blocked"] == 1, f"got {_bt.get('blocked')}")
check("the normaliser keeps booking_status so the filter has something to read",
      _blk[0].get("status") == "blocked", f"got {_blk[0].get('status')!r}")

# --- the fact harness must see a sign flip, a window change and a type change ---
_cz_base = {"customizations": {"last_minute_prices": {
    "last_min_factor_on": True, "last_min_factor_type": "linear",
    "last_min_factor_value": -20.0, "last_min_factor_dfd": 7}}}
_sig = fc.customization_facts_full(_cz_base)["rule_sig_digest"]
for _lab, _mut in (
        ("a 20% DISCOUNT rewritten as a 20% PREMIUM", {"last_min_factor_value": 20.0}),
        ("a 7-day window widened to 90 days", {"last_min_factor_dfd": 90}),
        ("the type changed from linear to fixed", {"last_min_factor_type": "fixed"})):
    _m = {"customizations": {"last_minute_prices":
                             dict(_cz_base["customizations"]["last_minute_prices"], **_mut)}}
    check(f"factcheck catches {_lab}",
          fc.customization_facts_full(_m)["rule_sig_digest"] != _sig,
          "the fact set carried only counts and toggles, so this was invisible")
_cz_txt = ("## rules\nrule,toggle,type,value,window\n"
           "last_minute_prices,on,linear,-20,<=7d\n")
check("the signature is derived independently on each side and still agrees",
      fc.customization_facts_reduced(_cz_txt)["rule_sig_digest"] == _sig,
      "one side reads the config, the other parses the printed table")
check("and the reduced side catches the sign flip too",
      fc.customization_facts_reduced(_cz_txt.replace("-20", "20"))["rule_sig_digest"] != _sig)

# --- PII must be stripped at EVERY depth, not just the top level ------------
_pii_row = {"guest": {"name": "Jane Doe", "email": "j@example.com"},
            "guestName": "Jane", "guest_email": "j@example.com",
            "phone_number": "555-0100", "contact_info": {"mobile": "555"},
            "listing_name": "A Property", "guest_count": 3,
            "rental_revenue": 100, "reservation_id": "R1", "check_in": "2027-01-01"}
_clean = rr.strip_pii(_pii_row)
_blob = json.dumps(_clean)
for _leak in ("Jane", "j@example.com", "555"):
    check(f"strip_pii removes {_leak!r} from a NESTED object, not just the top level",
          _leak not in _blob,
          f"the cache is never pruned, and the docstring promises this: {_blob}")
check("strip_pii keeps the fields the rollup actually needs",
      _clean.get("rental_revenue") == 100 and _clean.get("check_in") == "2027-01-01"
      and _clean.get("reservation_id") == "R1",
      f"got {_clean}")
check("strip_pii keeps listing_name and guest_count (marker words, not personal data)",
      _clean.get("listing_name") == "A Property" and _clean.get("guest_count") == 3,
      f"got {_clean}")
check("strip_pii is a no-op on a row that carries no PII",
      rr.strip_pii({"a": 1, "b": {"c": 2}}) == {"a": 1, "b": {"c": 2}})

# --- mixed currencies must never be summed into one revenue number ----------
_mixed = rr.reservation_tables(rr.reservation_rows([
    {"reservation_id": "R1", "check_in": "2027-01-10", "check_out": "2027-01-12",
     "booked_date": "2026-12-01", "no_of_days": 2, "rental_revenue": 600,
     "currency": "CAD", "booking_status": "booked", "booking_channel": "Airbnb"},
    {"reservation_id": "R2", "check_in": "2027-02-10", "check_out": "2027-02-12",
     "booked_date": "2026-12-01", "no_of_days": 2, "rental_revenue": 1200,
     "currency": "USD", "booking_status": "booked", "booking_channel": "Airbnb"},
], "2027-01-01"))
check("mixed currencies are detected and labelled, not silently added",
      str(_mixed["currency"]).upper().startswith("MIXED:"),
      f"got {_mixed['currency']!r} revenue={_mixed['revenue']}")
_src = (HERE / "reduce_reservations.py").read_text()
check("a MIXED currency set is refused whether or not --currency was passed",
      'startswith("MIXED:")' in _src,
      "CAD + USD in one revenue total is two units added together")
check("--currency with nothing to check against is refused, not treated as verified",
      "no booking reports a currency" in _src)

# --- summary ----------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + ", ".join(fails))
    sys.exit(1)
print("all checks passed.")
