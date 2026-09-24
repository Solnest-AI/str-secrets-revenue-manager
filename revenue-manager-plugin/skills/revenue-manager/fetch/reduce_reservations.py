#!/usr/bin/env python3
"""Fetch PriceLabs reservation history, cache it, print a rollup a decision can read.

WHY THIS EXISTS
---------------
Two years of reservations is ~13,000 tokens of JSON per listing, every booking as a full
record, and the record carries the guest's name. A pricing decision reads none of that
per booking. It reads pacing (nights and ADR by month), lead time and length-of-stay
distributions, channel mix, cancellations, and the handful of bookings that landed in the
last two weeks (the "booked within hours of going live" red flag needs those individually).
That is ~700 tokens. `fetch/factcheck.py reservations` proves 12 facts survive the cut.

`guestName` is dropped at the parsing boundary and never written anywhere, including the
cache.

USAGE
-----
    python3 reduce_reservations.py --listing <pricelabs id> [--pms smartbnb]
        [--back 730] [--forward 365] [--currency CAD] [--no-cache] [--ttl-days 1]

EXIT CODES
----------
0  printed
2  could not produce a trustworthy rollup (no key, API error, currency mismatch)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cache import cache_dir, cache_name, listing_matches, read_json, write_json  # noqa: E402
from factcheck import (  # noqa: E402
    LEAD_BUCKETS, LOS_BUCKETS, RES_MONTHLY_COLUMNS, RES_RECENT_COLUMNS,
    reservation_rows, reservation_tables,
)

BASE = "https://api.pricelabs.co"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
ENV_CANDIDATES = ["./mcp-servers/pricelabs/.env", "../mcp-servers/pricelabs/.env",
                  "../../../../mcp-servers/pricelabs/.env", "~/.claude/mcp-servers/pricelabs/.env"]
CACHE_DIR = cache_dir("reservations")
PAGE_SIZE = 100
MAX_PAGES = 20
PII_FIELDS = ("guestName", "guest_name", "email", "phone")


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


# Substring markers, matched case-insensitively against every key at every depth.
# The exact-match list above only ever saw four top-level keys, so a nested
# {"guest": {"name": ..., "email": ...}} object, or a sibling like `guest_email` or
# `phone_number`, went straight into the on-disk cache -- which is never pruned. The
# docstring promised guest data is "never written anywhere, including the cache", so
# the code is what had to change.
PII_MARKERS = ("guest", "name", "email", "phone", "address", "contact")
# Keys that contain a marker substring but carry no personal data. Without this,
# `listing_name` and `booking_channel`-style fields would be stripped too.
PII_ALLOW = {"listing_name", "listing_id", "guest_count", "no_of_guests"}


def _is_pii_key(key) -> bool:
    k = str(key).lower()
    if k in PII_ALLOW or k in {c.lower() for c in PII_ALLOW}:
        return False
    return key in PII_FIELDS or any(m in k for m in PII_MARKERS)


def strip_pii(row):
    """Drop guest-identifying fields at EVERY depth, not just the top level."""
    if isinstance(row, dict):
        return {k: strip_pii(v) for k, v in row.items() if not _is_pii_key(k)}
    if isinstance(row, list):
        return [strip_pii(v) for v in row]
    return row


def fetch(listing: str, pms: str, d_from: str, d_to: str, key: str) -> list[dict]:
    """Pages with `offset`/`limit`. Returns rows with PII already removed.

    TRAP (found 2026-09-12 by a row-count check, not by the fact harness): the endpoint
    paginates on `offset`, and `next_page` is a bare boolean. A `page=N` parameter is
    silently ignored, so the old loop fetched page 1 twenty times and every monthly total
    for a busy listing came out up to 20x too high. Both the raw cache and the reduced table
    carried the same duplicates, so raw-vs-reduced agreement proved nothing. Rows are
    de-duplicated on reservation_id and the loop stops the moment a page adds nothing new.
    """
    rows, seen_ids, offset, pages = [], set(), 0, 0
    foreign = [0]   # rows returned for a DIFFERENT listing than the one requested
    while pages < MAX_PAGES:
        q = {"listing_id": listing, "pms": pms, "start_date": d_from, "end_date": d_to,
             "limit": PAGE_SIZE, "offset": offset}
        url = f"{BASE}/v1/reservation_data?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise CannotProduce(f"PriceLabs HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from e
        except Exception as e:  # noqa: BLE001
            raise CannotProduce(f"PriceLabs request failed: {e}") from e
        if not isinstance(data, dict) or "data" not in data:
            raise CannotProduce(
                "the response carries no `data` collection; keys are "
                f"{sorted(data)[:6] if isinstance(data, dict) else type(data).__name__}. "
                "An error envelope is not an empty history.")
        page_rows = data.get("data") or []
        # Dedupe is keyed on reservation_id. If the field is missing or renamed, every
        # row's key is None, the first row poisons seen_ids, and page 2 reads as 100%
        # duplicate -- which trips the `not new` break below and silently truncates the
        # history to one page at exit 0. That is the mirror of the 20x duplication bug
        # this loop was written to fix, and the truncated rows are what gets cached, so
        # raw-vs-reduced fact checking agrees with itself and never sees it.
        undated = sum(1 for r in page_rows if not r.get("reservation_id"))
        if undated:
            raise CannotProduce(
                f"{undated} of {len(page_rows)} reservation rows carry no "
                "`reservation_id`, so they cannot be de-duplicated across pages. "
                "Refusing rather than silently truncating the history.")
        # Add to seen_ids WHILE consuming, not after: a page that repeats the same
        # reservation twice used to pass both copies, because neither id was in
        # seen_ids yet when the comprehension ran.
        new = []
        for r in page_rows:
            rid = r.get("reservation_id")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            new.append(r)
        # PriceLabs' reservation_data has been observed returning account-wide rows
        # regardless of the listing_id asked for. Measured 2026-09-19 across 8 cached
        # pulls it honoured the filter every time, so this is insurance, not a fix for
        # something reproduced -- but an unfiltered foreign row lands straight in this
        # listing's revenue and ADR with nothing to show it happened.
        kept = [r for r in new if not r.get("listing_id") or str(r["listing_id"]) == listing]
        foreign[0] += len(new) - len(kept)
        rows.extend(strip_pii(r) for r in kept)
        pages += 1
        offset += len(page_rows)
        if not page_rows:
            break
        if not new:
            # The API says there is another page, yet this one added nothing new: it is
            # ignoring `offset` and replaying the same rows. Stopping is right (the
            # alternative is the 20x duplication this loop exists to prevent) but what
            # we hold is a prefix of unknown completeness, and returning it as the whole
            # history is the silent-wrong-answer failure. Say we cannot tell.
            if data.get("next_page"):
                raise CannotProduce(
                    f"pagination stalled at offset {offset}: the API reports another "
                    "page but returned no new reservations, so it is replaying rows "
                    "and the history cannot be read completely.")
            break
        if not data.get("next_page"):
            break
    else:
        raise CannotProduce(f"more than {MAX_PAGES * PAGE_SIZE} reservations in the window; refusing to truncate silently")
    return rows, foreign[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listing", required=True)
    ap.add_argument("--pms", default="smartbnb")
    ap.add_argument("--back", type=int, default=730, help="days of history (default 730)")
    ap.add_argument("--forward", type=int, default=365, help="days ahead (default 365)")
    ap.add_argument("--currency", help="expected ISO code; every booking must match")
    ap.add_argument("--today", default=date.today().isoformat(), help=argparse.SUPPRESS)
    ap.add_argument("--ttl-days", type=float, default=1)
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    today = date.fromisoformat(a.today)
    d_from, d_to = (today - timedelta(days=a.back)).isoformat(), (today + timedelta(days=a.forward)).isoformat()
    path = os.path.join(CACHE_DIR, cache_name("res", a.listing, a.pms, d_from, d_to))
    blob, how = None, "miss"
    if not a.no_cache and os.path.isfile(path):
        try:
            b = read_json(path)
            if (listing_matches(b, a.listing, a.pms, [d_from, d_to])
                    and time.time() - datetime.fromisoformat(b["pulled_at"]).timestamp() <= a.ttl_days * 86400):
                blob, how = b, "hit"
        except Exception:  # noqa: BLE001
            blob = None
    if blob is None:
        rows, foreign_rows = fetch(a.listing, a.pms, d_from, d_to, resolve_key())
        blob = {"pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "listing": a.listing, "pms": a.pms, "window": [d_from, d_to],
                "foreign_rows_dropped": foreign_rows, "data": rows}
        write_json(path, blob)

    rows = reservation_rows(blob["data"], a.today)
    unknown_currency = sum(1 for r in rows if not r["cancelled"] and not r.get("blocked")
                           and not re.fullmatch(r"[A-Za-z]{3}", str(r.get("currency") or "")))
    if unknown_currency:
        raise CannotProduce(f"{unknown_currency} live booking(s) report no usable currency. "
                            "Every amount must have a verified currency before revenue is summed.")
    t = reservation_tables(rows)
    # A MIXED set means CAD and USD were added together into one revenue number. That
    # is wrong whether or not --currency was passed, so it is checked unconditionally.
    # reduce_comps refuses in the identical situation.
    if str(t["currency"] or "").upper().startswith("MIXED:"):
        raise CannotProduce(
            f"reservations report more than one currency ({t['currency']}); revenue and "
            "ADR would be different units added together. Refusing to print.")
    if a.currency and not t["currency"] and t["bookings"]:
        raise CannotProduce(
            f"--currency {a.currency} was asked for but no booking reports a currency, "
            "so nothing was actually verified.")
    if a.currency and t["currency"] and t["currency"].upper() != a.currency.upper():
        raise CannotProduce(f"currency mismatch: expected {a.currency}, reservations report {t['currency']}")

    print(f"# source=pricelabs_reservations pulled={blob['pulled_at']} cache={how} listing={a.listing[:8]} "
          f"window={d_from}..{d_to} today={a.today} bookings={t['bookings']} cancelled={t['cancelled']} "
          f"blocked={t['blocked']} "
          f"nights={t['nights']} revenue={t['revenue']} adr={t['adr'] if t['adr'] is not None else 'none'} "
          f"currency={t['currency'] or 'none'} "
          f"foreign_rows_dropped={blob.get('foreign_rows_dropped', 0)} "
          f"channels={','.join(f'{c}:{n}' for c, n in t['channels'].items())}")
    print("# los " + " ".join(f"{k}={t['los'][k] if t['los'][k] is not None else 'none'}" for k, _, _ in LOS_BUCKETS)
          + "   (share of live bookings by length of stay, %)")
    print("# lead " + " ".join(f"{k}={t['lead'][k] if t['lead'][k] is not None else 'none'}" for k, _, _ in LEAD_BUCKETS)
          + "   (share of live bookings by days between booking and check-in, %)")
    w = csv.writer(sys.stdout, lineterminator="\n")
    print("## monthly (by check-in month; cancelled bookings counted but excluded from nights/revenue/adr)")
    w.writerow(RES_MONTHLY_COLUMNS)
    for m in t["monthly"]:
        w.writerow([m[c] if m.get(c) is not None else "" for c in RES_MONTHLY_COLUMNS])
    print("## recent (booked in the last 14 days; individual rows for the booked-fast red flag)")
    w.writerow(RES_RECENT_COLUMNS)
    for r in t["recent"]:
        w.writerow([r["booked"], r["check_in"], r["lead_days"], r["nights"], r["adr"], r["channel"], "live"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CannotProduce as e:
        print(f"RESERVATIONS UNAVAILABLE: {e}", file=sys.stderr)
        print("Do not reason about pacing or lead time this run; say history is unverified.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # noqa: BLE001 - schema drift must read as "cannot produce"
        print(f"RESERVATIONS UNAVAILABLE: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        print("Do not reason about pacing or lead time this run; say history is unverified.", file=sys.stderr)
        sys.exit(2)
