#!/usr/bin/env python3
"""V2: every reducer is fed a drifted payload and must REFUSE. No network.

This is the entire bug class the build keeps producing, tested mechanically instead of
found by reading. Measured examples, all of which returned exit 0 and looked like
answers:

  a renamed date field        -> "this listing has no overrides"
  an empty PriceLabs response -> "0 defects, calendar verified", 8 sold nights inside it
  a -1 no-data marker         -> "last year sold nothing", a fake -100% year-over-year
  a missing New Bookings row  -> "zero market pickup", which argues for a price cut

Each reducer is run as a subprocess with `urllib.request.urlopen` replaced, so the real
fetch and parse path executes against a payload we control. Nothing touches the network
and no API key is used.

The contract, from the skill's own doctrine: exit 2 means "cannot produce a trustworthy
answer this run". It never means "there is none". A reducer that prints an empty table
at exit 0 on a drifted payload has stated a fact it cannot support.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}: {label}{'' if cond else '  -> ' + detail}")
    if not cond:
        fails.append(label)


SHIM = '''
import json, os, io, urllib.request, urllib.error

_body = open(os.environ["DRIFT_BODY"], "rb").read()
_status = int(os.environ.get("DRIFT_STATUS", "200"))


class _Resp(io.BytesIO):
    status = _status
    code = _status
    headers = {}
    def info(self):
        return {}
    def geturl(self):
        return "https://drift.test/"
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _fake(req, *a, **k):
    if _status >= 400:
        url = getattr(req, "full_url", "https://drift.test/")
        raise urllib.error.HTTPError(url, _status, "drift", {}, io.BytesIO(_body))
    return _Resp(_body)


urllib.request.urlopen = _fake
'''

# Every reducer, with a command that reaches its fetch path, and the payload shapes a
# vendor change actually produces. `ok_body` is the shape each one is happy with, so a
# refusal cannot be dismissed as "it refuses everything".
CASES = [
    ("reduce_overrides.py",
     ["--listing", "drift-listing-0001", "--pms", "smartbnb", "--today", "2026-01-01"],
     {"overrides": [{"date": "2027-01-0%d" % i, "price": 200, "price_type": "fixed"}
                    for i in range(1, 6)]},
     {"renamed date field": {"overrides": [{"override_date": "2027-01-01", "price": 200}]},
      "null dates": {"overrides": [{"date": None, "price": 200}] * 5},
      "error envelope under HTTP 200": {"error": "unavailable"}}),

    ("reduce_reservations.py",
     ["--listing", "drift-listing-0001", "--pms", "smartbnb", "--today", "2026-01-01",
      "--back", "30", "--forward", "30"],
     {"data": [{"reservation_id": "R1", "check_in": "2026-01-10",
                "check_out": "2026-01-12", "booked_date": "2025-12-01",
                "no_of_days": 2, "rental_revenue": 400, "currency": "CAD",
                "booking_status": "booked", "booking_channel": "Airbnb"}],
      "next_page": False},
     {"missing reservation_id": {"data": [{"check_in": "2026-01-10",
                                           "check_out": "2026-01-12",
                                           "no_of_days": 2, "rental_revenue": 400,
                                           "currency": "CAD"}], "next_page": False},
      "mixed currencies": {"data": [
          {"reservation_id": "R1", "check_in": "2026-01-10", "check_out": "2026-01-12",
           "booked_date": "2025-12-01", "no_of_days": 2, "rental_revenue": 400,
           "currency": "CAD", "booking_status": "booked", "booking_channel": "Airbnb"},
          {"reservation_id": "R2", "check_in": "2026-01-20", "check_out": "2026-01-22",
           "booked_date": "2025-12-01", "no_of_days": 2, "rental_revenue": 800,
           "currency": "USD", "booking_status": "booked", "booking_channel": "Airbnb"}],
          "next_page": False},
      "stalled pager": {"data": [{"reservation_id": "R1", "check_in": "2026-01-10",
                                  "check_out": "2026-01-12", "booked_date": "2025-12-01",
                                  "no_of_days": 2, "rental_revenue": 400,
                                  "currency": "CAD", "booking_status": "booked",
                                  "booking_channel": "Airbnb"}], "next_page": True}}),

    ("reduce_customizations.py",
     ["--listing", "drift-listing-0001", "--pms", "smartbnb", "--skip-logs"],
     # Every endpoint gets the same body from the shim, and the reducer nests each
     # response under its own key, so the body must satisfy the customizations read.
     {"customizations": {"seasonality": {"seasonality_customization_on": False,
                                         "seasonality_type": "recommended"}},
      "nudges": [], "data": []},
     {"customizations is a list, not an object":
         {"customizations": [], "nudges": [], "data": []},
      "customizations block missing entirely":
         {"nudges": [], "data": []}}),
]


def run(script: str, args: list, body, status: int = 200) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as td:
        shim_dir = Path(td) / "shim"
        shim_dir.mkdir()
        (shim_dir / "sitecustomize.py").write_text(SHIM, encoding="utf-8")
        payload = Path(td) / "body.json"
        payload.write_text(json.dumps(body), encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(shim_dir),
            "DRIFT_BODY": str(payload),
            "DRIFT_STATUS": str(status),
            "RC_CACHE_DIR": str(Path(td) / "cache"),
            "PRICELABS_API_KEY": "offline-drift-key-never-sent",
            "AIRROI_API_KEY": "offline-drift-key-never-sent",
        })
        return subprocess.run([sys.executable, str(HERE / script), *args],
                              capture_output=True, text=True, env=env, timeout=120)


print("reducer drift smoke test (V2)\n")

for script, args, ok_body, drifts in CASES:
    good = run(script, args, ok_body)
    check(f"{script}: the UNDRIFTED payload is accepted (exit 0)",
          good.returncode == 0,
          f"rc={good.returncode} {good.stderr.strip()[:130]}")

    for label, body in drifts.items():
        res = run(script, args, body)
        check(f"{script}: refuses a {label} (exit 2, not an empty table at 0)",
              res.returncode == 2,
              f"rc={res.returncode} stdout={res.stdout.strip()[:90]!r}")

    # an HTTP error must also be a refusal, never a silent empty result
    res = run(script, args, {"error": "boom"}, status=500)
    check(f"{script}: refuses an HTTP 500", res.returncode == 2,
          f"rc={res.returncode}")

    # and a truncated body that is not JSON at all
    with tempfile.TemporaryDirectory() as td:
        pass
    res = run(script, args, "{not json at all")
    check(f"{script}: refuses a non-JSON body", res.returncode == 2,
          f"rc={res.returncode}")

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails[:6]))
    sys.exit(1)
print("all checks passed.")
