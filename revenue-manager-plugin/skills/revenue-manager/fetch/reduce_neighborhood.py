#!/usr/bin/env python3
"""Fetch PriceLabs neighborhood data (the comp engine), cache it, print only what a decision reads.

WHY THIS EXISTS
---------------
One `pricelabs_get_neighborhood_data` response is ~118,000 tokens. It carries every bedroom
category the market has (3, 4, 5 for a 4BR listing), 540 days of occupancy of which the
first 180 are the past, and ten series where the decision reads seven. The listing's own
category over the forward year is a few thousand tokens. `fetch/factcheck.py neighborhood`
proves 25 decision facts survive the cut, per-date values included.

WHAT SURVIVES, AND WHY
----------------------
  daily   the 365-day forward ask curve (p25/p50/p75/p90), the median BOOKED price
          (cleared, not ask: the ask-vs-cleared spread is a core signal), N bookings,
          market occupancy, occupancy STLY (pacing at equal lead time), occupancy LY
          (how the date finished last year), new bookings and cancellations (pickup),
          available listings (supply)
  monthly the same percentiles rolled up by month (seasonality at a glance)
  kpi     booking window, LOS, 7-day pickup and STLY, by month plus trailing 365/730

Dropped: the other bedroom categories, the 180 days of daily history (the monthly KPI
block carries history), Occupancy_L2Y / ST2Y, last-year supply.

MARKET IDENTITY
--------------
The response can contain a listing-specific custom comp set. Nearby listings therefore
cannot safely share a payload based on location alone. Cache identity includes the full
listing id and PMS, and the embedded listing identity is checked before reuse.

USAGE
-----
    python3 reduce_neighborhood.py --listing <pricelabs listing id> --bedrooms 4 \
        [--pms smartbnb] [--lat 50.88 --lng -119.90] [--days 365] [--currency CAD] [--no-cache]

EXIT CODES
----------
0  printed
2  could not produce a trustworthy table (no key, API error, bedroom category absent,
   currency mismatch). Never treat 2 as "no market data".
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _calendar import local_today  # noqa: E402
from _cache import cache_dir, cache_name, listing_matches, read_json, write_json  # noqa: E402
from factcheck import (  # noqa: E402
    NB_DAILY_COLUMNS, NB_KPI_COLUMNS, NB_KPI_SERIES, NB_MONTHLY_COLUMNS, NB_PCT_SERIES,
    _nb_kind, _r, neighborhood_daily_from_raw,
    label_index, neighborhood_missing_series, neighborhood_base_percentiles,
)

BASE = "https://api.pricelabs.co"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"  # WAF 403s bare clients
ENV_CANDIDATES = [
    "./mcp-servers/pricelabs/.env", "../mcp-servers/pricelabs/.env",
    "../../../../mcp-servers/pricelabs/.env", "~/.claude/mcp-servers/pricelabs/.env",
]
CACHE_DIR = cache_dir("neighborhood")


class CannotProduce(Exception):
    pass


def resolve_key() -> str:
    for n in ("PRICELABS_API_KEY", "PRICELABS_KEY"):
        if os.environ.get(n):
            return os.environ[n]
    for path in ENV_CANDIDATES:
        p = os.path.expanduser(path)
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8-sig"):
                m = re.match(r"\s*(PRICELABS_API_KEY|PRICELABS_KEY)\s*=\s*(.+?)\s*$", line)
                if m:
                    return m.group(2).strip('"').strip("'")
    raise CannotProduce("No PRICELABS_API_KEY in the environment or in " + ", ".join(ENV_CANDIDATES))


def cache_path(listing: str, pms: str, lat: float | None, lng: float | None) -> tuple[str, str]:
    key = f"listing:{listing}:{pms}"
    name = cache_name("nb", listing, pms)
    return os.path.join(CACHE_DIR, name), key


def fetch(listing: str, pms: str, key: str) -> dict:
    url = f"{BASE}/v1/neighborhood_data?{urllib.parse.urlencode({'listing_id': listing, 'pms': pms})}"
    req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise CannotProduce(f"PriceLabs HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from e
    except Exception as e:  # noqa: BLE001
        raise CannotProduce(f"PriceLabs request failed: {e}") from e
    if "data" not in data:
        raise CannotProduce(f"unexpected response shape: {list(data)[:5]}")
    return {"pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "listing": listing, "pms": pms, "data": data["data"]}


def load_or_fetch(listing, pms, lat, lng, ttl_days, use_cache):
    path, ckey = cache_path(listing, pms, lat, lng)
    if use_cache and os.path.isfile(path):
        try:
            blob = read_json(path)
            if (listing_matches(blob, listing, pms)
                    and time.time() - datetime.fromisoformat(blob["pulled_at"]).timestamp() <= ttl_days * 86400):
                return blob, "hit", ckey
        except Exception:  # noqa: BLE001
            pass
    blob = fetch(listing, pms, resolve_key())
    write_json(path, blob)
    return blob, "miss", ckey


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listing", required=True, help="PriceLabs listing id")
    ap.add_argument("--bedrooms", required=True, help="the listing's bedroom count; selects the category")
    ap.add_argument("--category", help="use this category name instead of the bedroom count "
                                       "(custom PriceLabs comp sets, e.g. 'My Comp Set')")
    ap.add_argument("--pms", default="smartbnb")
    ap.add_argument("--lat", type=float); ap.add_argument("--lng", type=float)
    ap.add_argument("--days", type=int, default=365, help="forward window (default 365)")
    ap.add_argument("--currency", help="expected ISO code; the payload must report it")
    ap.add_argument("--ttl-days", type=float, default=1, help="cache lifetime (default 1: percentiles move daily)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--today", default=None, help=argparse.SUPPRESS)  # window start; tests pin it
    ap.add_argument("--tz", help="property timezone (IANA name or +HH:MM); 'today' is the "
                                 "property's date, not this computer's. Default: local clock")
    a = ap.parse_args()
    today = a.today
    try:
        today = today or local_today(a.tz).isoformat()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    blob, how, ckey = load_or_fetch(a.listing, a.pms, a.lat, a.lng, a.ttl_days, not a.no_cache)
    d = blob["data"]
    cats = sorted(d.get("Future Percentile Prices", {}).get("Category", {}).keys(), key=lambda x: (len(x), x))
    cat = a.category or str(a.bedrooms)
    cat_note = ""
    if cat not in cats:
        # A listing priced against a custom PriceLabs comp set has ONE named category and
        # no bedroom categories at all. Use it and say so; never substitute a neighbouring
        # bedroom category when several exist.
        # A NAMED single category is a custom comp set. A single NUMERIC category is
        # just a bedroom category that happens to be the only one present, and silently
        # answering a 4-bedroom question with 3-bedroom data -- then labelling it a
        # custom comp set -- is a substitution wearing a different name.
        if a.category is None and len(cats) == 1 and not str(cats[0]).strip().isdigit():
            cat, cat_note = cats[0], f"custom_comp_set(bedroom_category_{a.bedrooms}_absent)"
        else:
            raise CannotProduce(f"category {cat!r} is not in this market's data (available: {cats}). "
                                "Refusing to substitute a neighbouring category; pass --category to pick one.")
    cur = d.get("currency")
    if a.currency and str(cur).upper() != a.currency.upper():
        raise CannotProduce(f"currency mismatch: expected {a.currency}, neighborhood reports {cur}")

    try:
        daily = neighborhood_daily_from_raw({"data": d}, cat, a.days, start=today)
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise CannotProduce(f"payload shape not understood for category {cat!r}: {e}") from e
    missing = neighborhood_missing_series({"data": d}, cat)
    if not daily:
        raise CannotProduce("no forward dates in the percentile series")
    pct_cat = d["Future Percentile Prices"]["Category"][cat]
    basep = neighborhood_base_percentiles(d, cat)
    mp = d.get("Future Percentile Prices Monthly", {}).get("Category", {}).get(cat)  # absent on custom comp sets
    mlabels = d["Future Percentile Prices"]["Labels"]
    kp = d["Market KPI"]["Category"][cat]
    klabels = d["Market KPI"]["Labels"]

    n30 = daily[:30]
    def mean(col, kind):
        v = [float(r[col]) for r in n30 if r.get(col) is not None]
        return _r(sum(v) / len(v), kind) if v else ""
    def total(col):
        # sum([]) is 0, and 0 is a legitimate pickup number, so a MISSING New Bookings
        # series printed `new_bk=0` and a pricing agent read it as "zero market pickup"
        # and cut price. Its sibling mean() already returns "" in this case.
        vals = [float(r[col]) for r in n30 if r.get(col) is not None]
        return int(sum(vals)) if vals else ""

    buf = io.StringIO()
    print(f"# source=pricelabs_neighborhood pulled={blob['pulled_at']} cache={how} cache_key={ckey} "
          f"listing={a.listing[:8]} category={cat.replace(' ', '_')} listings_used={pct_cat.get('Listings Used')} "
          f"active={pct_cat.get('Active Used')} currency={cur} days={len(daily)} window_start={today} "
          f"daily_first={daily[0]['date']} daily_last={daily[-1]['date']} "
          f"categories_available={','.join(c.replace(' ', '_') for c in cats)}"
          + (f" category_note={cat_note}" if cat_note else "")
          + (f" missing_series={','.join(m.replace(' ', '_') for m in missing)}" if missing else ""),
          file=buf)
    print(f"# base_p25={_r(basep['base_p25'], 'nb_price')} base_p50={_r(basep['base_p50'], 'nb_price')} "
          f"base_p75={_r(basep['base_p75'], 'nb_price')} base_p90={_r(basep['base_p90'], 'nb_price')}",
          file=buf)
    print(f"# next30 p50={mean('p50', 'nb_price')} p90={mean('p90', 'nb_price')} "
          f"booked_med={mean('booked_med', 'nb_price')} occ={mean('occ', 'nb_pct')} "
          f"occ_stly={mean('occ_stly', 'nb_pct')} occ_ly={mean('occ_ly', 'nb_pct')} "
          f"new_bk={total('new_bk')} avail={mean('avail', 'count')}",
          file=buf)

    # Build the whole body first. Streamed straight to stdout, an IndexError in the
    # monthly block left the header, the summary, every daily row and a bare
    # "## monthly" on stdout AND exited 2 -- a half-table that reads as a real answer.
    w = csv.writer(buf, lineterminator="\n")
    print("## daily", file=buf); w.writerow(NB_DAILY_COLUMNS)
    for r in daily:
        w.writerow([r[c] if r.get(c) is not None else "" for c in NB_DAILY_COLUMNS])
    print("## monthly", file=buf); w.writerow(NB_MONTHLY_COLUMNS)
    if mp:
        for i, m in enumerate(mp["X_values"]):
            w.writerow([m] + [(_r(mp["Y_values"][label_index(mlabels, lab)][i], _nb_kind(col))
                               if label_index(mlabels, lab) is not None else "") for lab, col in NB_PCT_SERIES.items()])
    else:
        print("# monthly percentiles are not provided for this category; use the daily block", file=buf)
    print("## kpi", file=buf); w.writerow(NB_KPI_COLUMNS)
    for i, m in enumerate(kp["X_values"]):
        w.writerow([m] + [(_r(kp["Y_values"][label_index(klabels, lab)][i], "count")
                           if label_index(klabels, lab) is not None else "") for lab in NB_KPI_SERIES])
    sys.stdout.write(buf.getvalue())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CannotProduce as e:
        print(f"NEIGHBORHOOD UNAVAILABLE: {e}", file=sys.stderr)
        print("Do not price against comps this run; report the market as unverified.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # noqa: BLE001 - schema drift must read as "cannot produce", not a crash
        print(f"NEIGHBORHOOD UNAVAILABLE: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        print("Do not price against comps this run; report the market as unverified.", file=sys.stderr)
        sys.exit(2)
