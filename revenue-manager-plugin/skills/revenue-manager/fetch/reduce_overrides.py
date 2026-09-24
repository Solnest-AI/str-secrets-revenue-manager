#!/usr/bin/env python3
"""Reduce PriceLabs overrides (DSOs / custom rates / min-stay rules) to date RUNS.

    python3 reduce_overrides.py --listing <pricelabs id> [--pms smartbnb] [--today YYYY-MM-DD]

`GET /v1/listings/{id}/overrides` returns one row per date. A busy listing carries a few
hundred (measured: 281, 318, 383 on three live listings, ~12k tokens each through the raw
MCP tool), and the decision-relevant shape is never the row: it is the RUN of consecutive
dates with the same price, price type, min-stay and reason ("2026-10-01..2026-11-19, -25%
percent, min 1, Shoulder fall"). Prints:

    # source=pricelabs_overrides pulled=... cache=hit|miss listing=... dates=N runs=M first=.. last=.. today=..
    ## runs
    start,end,nights,price,price_type,min_stay,reason

Dates before today are history, not active overrides, and are dropped. No overrides at all is a
valid answer (exit 0, dates=0), distinct from "could not read" (exit 2, nothing printed).
`fetch/factcheck.py overrides` re-derives every run from the raw payload and from this table and
compares them through a digest, so a run that changed shape fails loudly.

Exit 0: printed. Exit 2: cannot produce a trustworthy table (no key, API error).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cache import cache_dir, cache_name, listing_matches, read_json, write_json  # noqa: E402
from factcheck import OVERRIDE_COLUMNS, override_facts_from_runs, override_runs  # noqa: E402

BASE = "https://api.pricelabs.co"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"  # WAF 403s bare clients
ENV_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "mcp-servers", "pricelabs", ".env"),
    "./mcp-servers/pricelabs/.env", "../mcp-servers/pricelabs/.env", "~/.claude/mcp-servers/pricelabs/.env",
]
CACHE_DIR = cache_dir("overrides")


class CannotProduce(Exception):
    pass


def resolve_key() -> str:
    for n in ("PRICELABS_API_KEY", "PRICELABS_KEY"):
        if os.environ.get(n):
            return os.environ[n]
    for path in ENV_CANDIDATES:
        p = os.path.expanduser(path)
        if os.path.isfile(p):
            for line in open(p):
                m = re.match(r"\s*(PRICELABS_API_KEY|PRICELABS_KEY)\s*=\s*(.+?)\s*$", line)
                if m:
                    return m.group(2).strip('"').strip("'")
    raise CannotProduce("No PRICELABS_API_KEY in the environment or in " + ", ".join(ENV_CANDIDATES))


def fetch(listing: str, pms: str, key: str) -> dict:
    url = f"{BASE}/v1/listings/{urllib.parse.quote(listing)}/overrides?{urllib.parse.urlencode({'pms': pms})}"
    req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise CannotProduce(f"PriceLabs HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from e
    except Exception as e:  # noqa: BLE001
        raise CannotProduce(f"PriceLabs request failed: {e}") from e


def load_or_fetch(listing: str, pms: str, ttl_days: float, use_cache: bool) -> tuple[dict, str]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, cache_name("ov", listing, pms))
    if use_cache and os.path.isfile(path):
        # A corrupt or legacy cache file must FALL THROUGH to a refetch, never brick the
        # listing. Unguarded, a blob missing `pulled_at` raised KeyError out of here and
        # exited 2 on every subsequent run until someone deleted the file by hand.
        # reduce_comps / reduce_neighborhood / reduce_reservations already do this.
        try:
            blob = read_json(path)
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(blob["pulled_at"])).total_seconds()
            fresh = age <= ttl_days * 86400
        except Exception:  # noqa: BLE001  - any unreadable cache is simply a miss
            blob, fresh = None, False
        if listing_matches(blob, listing, pms) and fresh:
            return blob, "hit"
    data = fetch(listing, pms, resolve_key())
    blob = {"pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "listing": listing, "pms": pms, "data": data}
    write_json(path, blob)
    return blob, "miss"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listing", required=True)
    ap.add_argument("--pms", default="smartbnb")
    ap.add_argument("--today", default=date.today().isoformat(), help=argparse.SUPPRESS)
    ap.add_argument("--ttl-days", type=float, default=1)
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    blob, how = load_or_fetch(a.listing, a.pms, a.ttl_days, not a.no_cache)
    data = blob["data"]
    # The collection must be PRESENT, not merely defaulted. A 200 carrying
    # {"error": "unavailable"} has neither key, and defaulting to [] turned a vendor
    # error into "this listing has no overrides" at exit 0 -- after which the caller
    # prices straight over a live override.
    if isinstance(data, dict):
        if "overrides" in data:
            rows = data["overrides"]
        elif "data" in data:
            rows = data["data"]
        else:
            raise CannotProduce(
                "the response carries neither `overrides` nor `data`; keys are "
                f"{sorted(data)[:6]}. This is an error envelope or a shape change, "
                "NOT an empty calendar.")
    else:
        rows = data
    if not isinstance(rows, list):
        raise CannotProduce(f"unexpected payload shape: {type(rows).__name__}")
    # A row PriceLabs returns but we cannot date is a payload-shape change, not an
    # absence. Dropping it silently turns "the date field was renamed" into "this
    # listing has no overrides" at exit 0, and the caller then prices over a live
    # override. The fact harness cannot catch it because both sides call override_runs.
    undated = sum(1 for r in rows if not r.get("date"))
    if undated:
        raise CannotProduce(
            f"{undated} of {len(rows)} override rows carry no usable `date` field. "
            "This is a payload-shape change, not an empty calendar: do NOT read it as "
            "'no overrides exist'.")
    dropped_past = sum(1 for r in rows if str(r.get("date", "")) < a.today)
    runs = override_runs(rows, a.today)
    f = override_facts_from_runs(runs)
    if f["dates_total"] + dropped_past != len(rows):
        raise CannotProduce(
            f"override rows do not reconcile: {len(rows)} in, {f['dates_total']} kept, "
            f"{dropped_past} past. Every row must be accounted for before this table "
            "can be trusted.")
    print(f"# source=pricelabs_overrides pulled={blob['pulled_at']} cache={how} listing={a.listing[:8]} "
          f"rows_in={len(rows)} "
          f"dates={f['dates_total']} runs={f['runs']} first={f['first_date'] or 'none'} last={f['last_date'] or 'none'} "
          f"percent_dates={f['percent_dates']} fixed_dates={f['fixed_dates']} min_stay_dates={f['min_stay_dates']} "
          f"today={a.today} dropped_past={dropped_past}")
    w = csv.writer(sys.stdout, lineterminator="\n")
    print("## runs"); w.writerow(OVERRIDE_COLUMNS)
    for r in runs:
        w.writerow([r["start"], r["end"], r["nights"], r["price"], r["price_type"], r["min_stay"], r["reason"]])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CannotProduce as e:
        print(f"OVERRIDES UNAVAILABLE: {e}", file=sys.stderr)
        print("Do not assume there are no overrides this run; say the source could not be read.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(f"OVERRIDES UNAVAILABLE: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
