#!/usr/bin/env python3
"""Reconcile PMS availability against PriceLabs before any pricing recommendation.

WHY THIS EXISTS
---------------
PriceLabs does not see every booking. Off-platform reservations and bookings made
under a channel account that is not wired into the PriceLabs sync are returned as
plainly AVAILABLE (`booking_status: ""`, `unbookable: 0`), not as blocks.

Measured live on 2026-09-12 against a real operator portfolio, forward 180 days:
dozens of booked nights, worth tens of thousands in calendar price, were invisible to PriceLabs
across more than half of the synced listings. One listing (a ski chalet) showed Dec 0% / Jan 0% occupancy in PriceLabs
while the PMS had it at 71% / 84% booked over Christmas and New Year.

That is not a cosmetic reporting gap. The revenue framework's own red-flag table
fires "5+ consecutive unbooked days -> drop 10 to 15%" and "comp set booked and you
are not -> match comp pricing" on those dates. Left unguarded, the agent recommends
discounting the highest-ADR inventory of the year because it cannot see it is sold.

THE RULE THIS ENFORCES
----------------------
The PMS calendar is ground truth for AVAILABILITY.
PriceLabs is ground truth for PRICE and MARKET.
A date where the PMS says RESERVED and PriceLabs says available is a SYNC DEFECT.
It is never an underperforming date, and it must never enter the discount candidate set.

THE CALENDAR BLOCK (why the raw PMS calendar never enters context)
------------------------------------------------------------------
A 365-day Hospitable calendar is ~49,000 tokens of JSON. Step 5 needs three things from
it: which nights are sold (the reconciliation above), the per-listing markup between the
PMS calendar price and the PriceLabs price (measured, never assumed), and any dates where
the two systems disagree on price or min-stay. All three are computed here and printed as
a `## calendar` block per listing: a header line with the counts and the markup median
and spread, then `### invisible` (sold nights PriceLabs cannot see) and `### drift`
(available nights whose price ratio is off the median by more than 5%, or whose min-stay
differs). A healthy sync prints a few lines. A broken one prints every disagreeing date,
which is exactly when you want to see them.

The raw bundle for each listing is cached under ~/.cache/revenue-manager/reconcile/ so
`fetch/factcheck.py calendar` can prove the block carries every fact, and a re-run inside
--ttl-days makes no PMS call.

USAGE
-----
    python3 reconcile_pms.py --days 180
    python3 reconcile_pms.py --days 365 --json exclusions.json
    python3 reconcile_pms.py --listing <listing-uuid> --from 2026-12-15 --to 2027-02-01

Keys are read from the environment first, then from the MCP servers' own .env files.
Nothing is printed that could leak a key.

EXIT CODES
----------
0  the check ran (findings, if any, are in the report)
2  the check could NOT run, in whole or in part (missing keys, unreachable API,
   a listing whose PMS calendar could not be read) -- never treat as "clean".
   The report and --json are still written for the listings that did reconcile.
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
import urllib.request
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cache import cache_dir, cache_name, listing_matches, read_json, write_json  # noqa: E402
from _calendar import pms_status, pricelabs_status, validate_calendar  # noqa: E402
from factcheck import CAL_BLOCKED_COLUMNS, CAL_DRIFT_COLUMNS, CAL_GAP_COLUMNS, CAL_INVISIBLE_COLUMNS, calendar_rows, _r  # noqa: E402
from pathlib import Path

PL_BASE = "https://api.pricelabs.co"
HO_BASE = "https://public.api.hospitable.com/v2"

# PriceLabs' WAF 403s any request without a browser-shaped User-Agent. The MCP works
# only because axios sets one for free. Direct calls must set it themselves.
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Candidate .env locations, checked in order. Add your own if your layout differs.
# The script lives at <repo>/revenue-manager-plugin/skills/revenue-manager/fetch/,
# four levels below the mcp-servers/ folder, and SKILL.md runs it from fetch/, so
# cwd-relative paths alone never find the connector .env files. Resolve against
# the script's own location first (same rule as reduce_prices.py), then cwd.
def _repo_root() -> str | None:
    parents = Path(__file__).resolve().parents
    return str(parents[4]) if len(parents) > 4 else None


def _env_candidates(connector: str) -> list[str]:
    out = []
    root = _repo_root()
    if root:
        out.append(os.path.join(root, "mcp-servers", connector, ".env"))
    out += [
        f"./mcp-servers/{connector}/.env",
        f"../mcp-servers/{connector}/.env",
        f"~/.claude/mcp-servers/{connector}/.env",
    ]
    return out


PL_ENV_CANDIDATES = _env_candidates("pricelabs")
HO_ENV_CANDIDATES = _env_candidates("hospitable")

PL_KEYS = ("PRICELABS_API_KEY", "PRICELABS_KEY")
HO_KEYS = ("HOSPITABLE_API_KEY", "HOSPITABLE_TOKEN", "HOSPITABLE_PAT")


class CheckCannotRun(Exception):
    """Raised when the reconciliation could not be performed at all.

    Distinct from "performed and found nothing". A check that goes green because it
    was blind is worse than no check, so this always exits non-zero.
    """


def _read_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    expanded = os.path.expanduser(path)
    if not os.path.isfile(expanded):
        return out
    with open(expanded) as fh:
        for line in fh:
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def resolve_key(names: tuple[str, ...], candidates: list[str], label: str) -> str:
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    for path in candidates:
        env = _read_env_file(path)
        for n in names:
            if env.get(n):
                return env[n]
    raise CheckCannotRun(
        f"No {label} key found. Set one of {', '.join(names)} in the environment, "
        f"or place it in one of: {', '.join(candidates)}"
    )


def _request(req: urllib.request.Request, timeout: int = 120):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        raise CheckCannotRun(f"{req.full_url.split('?')[0]} returned {e.code}: {body}") from e
    except Exception as e:  # noqa: BLE001 - surface the cause, never swallow it
        raise CheckCannotRun(f"{req.full_url.split('?')[0]} failed: {e}") from e


def is_booked(status: str) -> bool:
    """PriceLabs emits 'Booked' AND 'Booked (Check-In)' as separate values.

    Matching `== "Booked"` undercounts occupancy by roughly 40% on a busy month.
    """
    return str(status).strip().lower().startswith("booked")


def pms_reserved(day: dict) -> bool:
    return pms_status(day) == "RESERVED"


def check_calendar(rows, label, status_reader, d_from: str, d_to: str):
    try:
        validate_calendar(rows, label, status_reader, d_from, d_to)
    except (TypeError, ValueError) as exc:
        raise CheckCannotRun(str(exc)) from exc


def check_price_calendar(rows, d_from: str, d_to: str):
    if not isinstance(rows, dict):
        raise CheckCannotRun("PriceLabs calendar is not a date map")
    dated = []
    for when, row in rows.items():
        if not isinstance(row, dict) or row.get("date", when) != when:
            raise CheckCannotRun("PriceLabs calendar has an invalid row or conflicting date")
        dated.append(dict(row, date=when))
    check_calendar(dated, "PriceLabs", pricelabs_status, d_from, d_to)


def fetch_pricelabs(key: str, listings: list[dict], d_from: str, d_to: str) -> dict:
    """One POST covers every listing. Returns {listing_id: {date: row}}.

    NOTE: PriceLabs silently ignores date ranges in the past. It returns 200 with a
    shorter window rather than an error, so always sanity-check what came back.
    """
    payload = {
        "listings": [
            {"id": item["id"], "pms": item.get("pms", "smartbnb"),
             "dateFrom": d_from, "dateTo": d_to}
            for item in listings
        ]
    }
    req = urllib.request.Request(
        f"{PL_BASE}/v1/listing_prices",
        data=json.dumps(payload).encode(),
        headers={"X-API-Key": key, "User-Agent": UA, "Content-Type": "application/json"},
    )
    raw = _request(req, timeout=180)
    out: dict[str, dict] = {}
    expected = {str(item["id"]): item.get("pms", "smartbnb") for item in listings}
    for item in (raw if isinstance(raw, list) else [raw]):
        if not isinstance(item, dict):
            raise CheckCannotRun("PriceLabs returned a non-object listing result")
        lid = item.get("id") or item.get("listing_id")
        if not lid:
            continue
        lid = str(lid)
        if lid not in expected:
            continue
        if lid in out or item.get("pms", expected[lid]) != expected[lid]:
            out[lid] = {"__error__": "duplicate listing response or PMS identity mismatch"}
            continue
        if item.get("error"):
            out[lid] = {"__error__": str(item["error"])}
            continue
        rows = item.get("data")
        try:
            check_calendar(rows, "PriceLabs", pricelabs_status, d_from, d_to)
        except CheckCannotRun as exc:
            out[lid] = {"__error__": str(exc)}
            continue
        out[lid] = {r["date"]: r for r in rows}
    return out


def fetch_pms_calendar(token: str, property_id: str, d_from: str, d_to: str) -> list[dict]:
    """Hospitable calendar for one property.

    TRAP: this endpoint takes `start_date`/`end_date` in snake_case while the MCP
    wrapper takes `propertyId` in camelCase, and the wrapper DROPS unknown keys
    silently rather than erroring. Guessing `startDate` returns Hospitable's default
    ~15-day window with no warning, so we assert the echoed range matches the request.
    """
    url = f"{HO_BASE}/properties/{property_id}/calendar?start_date={d_from}&end_date={d_to}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    data = _request(req, timeout=90).get("data", {})
    got_from, got_to = data.get("start_date"), data.get("end_date")
    if got_from != d_from or got_to != d_to:
        raise CheckCannotRun(
            f"PMS returned {got_from}..{got_to} for a {d_from}..{d_to} request. "
            "The date filter was ignored; the comparison would be against wrong dates."
        )
    days = data.get("days", [])
    check_calendar(days, "PMS", pms_status, d_from, d_to)
    return days


def reconcile(pms_days: list[dict], pl_rows: dict) -> dict:
    """Classify every date. Returns counts plus the defect list."""
    reserved = [d for d in pms_days if pms_reserved(d)]
    invisible, blocked_only, agreed = [], [], []
    for day in reserved:
        row = pl_rows.get(day["date"])
        if row is None:
            continue  # outside the PriceLabs window; not a defect
        if is_booked(row.get("booking_status", "")):
            agreed.append(day)
        elif pricelabs_status(row) == "BLOCKED":
            # PriceLabs knows it is not sellable, just not that it is revenue.
            blocked_only.append(day)
        elif pricelabs_status(row) == "AVAILABLE":
            # The dangerous case: PriceLabs believes this night is for sale.
            invisible.append(day)
    return {
        "pms_reserved": len(reserved),
        "pl_booked": sum(1 for r in pl_rows.values() if is_booked(r.get("booking_status", ""))),
        "agreed": len(agreed),
        "blocked_only": blocked_only,
        "invisible": invisible,
    }


def night_value(day: dict) -> float:
    """Hospitable prices are in CENTS on read. Divide by 100 or you are out by 100x."""
    return ((day.get("price") or {}).get("amount") or 0) / 100.0


def print_calendar_block(lid: str, name: str, c: dict, out) -> None:
    """One listing's `## calendar` block. Pure formatting; the harness parses this back."""
    w = csv.writer(out, lineterminator="\n")
    print(f"\n## calendar listing={lid[:8]} name={name.replace(' ', '_')} pms_days={c['pms_days']} "
          f"pms_min_mode={c['pms_min_mode'] if c['pms_min_mode'] is not None else 'none'} "
          f"pms_reserved={c['pms_reserved']} pl_booked={c['pl_booked']} invisible={len(c['invisible'])} "
          f"owner_stays={c['owner_stay_count']} paired={c['paired_dates']} "
          f"markup_median={c['markup_median'] if c['markup_median'] is not None else 'none'} "
          f"markup_stdev={c['markup_stdev'] if c['markup_stdev'] is not None else 'none'} "
          f"min_stay_mismatch={c['min_stay_mismatch']} drift={len(c['drift'])} "
          f"blocked_runs={len(c.get('blocked_runs', []))} blocked_nights={sum(b['nights'] for b in c.get('blocked_runs', []))} "
          f"gaps={len(c.get('gaps', []))} compared_at={c.get('compared_at', 'live')}", file=out)
    if c["markup_stdev"] is not None and c["markup_stdev"] > 0.05:
        print("# WARNING markup spread > 5%: sync is broken or markup logic is misconfigured (SKILL Step 5)", file=out)
    if c["pms_min_mode"] is not None and c["pms_min_mode"] >= 28:
        print(f"# NOTE PMS min-stay is {c['pms_min_mode']} nights on most dates: this listing is configured "
              "as a long-term rental, not an STR. Nightly pricing logic does not apply.", file=out)
    print("### invisible", file=out); w.writerow(CAL_INVISIBLE_COLUMNS)
    for d in c["invisible"]:
        w.writerow([d["date"], _r((((d.get("price") or {}).get("amount")) or 0) / 100.0, "price"),
                    (d.get("note") or "").replace("\n", " ")[:40]])
    print("### drift", file=out); w.writerow(CAL_DRIFT_COLUMNS)
    for d in c["drift"]:
        w.writerow([d[k] if d.get(k) is not None else "" for k in CAL_DRIFT_COLUMNS])
    # host/user blocks (not for sale, not revenue) and orphan gaps (1-2 open nights boxed in)
    print("### blocked", file=out); w.writerow(CAL_BLOCKED_COLUMNS)
    for b in c.get("blocked_runs", []):
        w.writerow([b["start"], b["end"], b["nights"], b["source"], b["note"]])
    print("### gaps", file=out); w.writerow(CAL_GAP_COLUMNS)
    for g in c.get("gaps", []):
        w.writerow([g["start"], g["end"], g["nights"]])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=180, help="forward window in days (default 180)")
    ap.add_argument("--from", dest="d_from", help="explicit start date YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", help="explicit end date YYYY-MM-DD")
    ap.add_argument("--listing", action="append", help="limit to these listing ids (repeatable)")
    ap.add_argument("--json", help="write the exclusion set to this path")
    ap.add_argument("--pms", default="smartbnb", help="PriceLabs pms_name (default smartbnb)")
    ap.add_argument("--ttl-days", type=float, default=1, help="serve the PMS calendar from cache inside this window")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-calendar", action="store_true", help="portfolio table only, skip the per-listing blocks")
    args = ap.parse_args()

    if args.days < 1:
        ap.error("--days must be at least 1")
    try:
        first = date.fromisoformat(args.d_from) if args.d_from else date.today()
        last = date.fromisoformat(args.d_to) if args.d_to else first + timedelta(days=args.days - 1)
    except ValueError:
        ap.error("--from and --to must be YYYY-MM-DD dates")
    if last < first:
        ap.error("--to must not precede --from")
    d_from, d_to = first.isoformat(), last.isoformat()
    window_days = (last - first).days + 1
    if d_from < date.today().isoformat():
        print("WARNING: start date is in the past. PriceLabs silently ignores past "
              "ranges and will return a shorter window than you asked for.", file=sys.stderr)

    pl_key = resolve_key(PL_KEYS, PL_ENV_CANDIDATES, "PriceLabs")
    ho_key = resolve_key(HO_KEYS, HO_ENV_CANDIDATES, "PMS")

    listings = _request(urllib.request.Request(
        f"{PL_BASE}/v1/listings", headers={"X-API-Key": pl_key, "User-Agent": UA}
    ))
    rows = listings.get("listings", listings) if isinstance(listings, dict) else listings
    wanted = [dict(item, id=str(item["id"]), pms=item.get("pms") or args.pms) for item in rows
              if isinstance(item, dict) and item.get("id")
              and (not args.listing or str(item["id"]) in args.listing)]
    if not wanted:
        raise CheckCannotRun("No listings matched. Check --listing ids against /v1/listings.")
    if args.listing and set(args.listing) - {item["id"] for item in wanted}:
        raise CheckCannotRun("Some requested listings were not found in /v1/listings; the run is incomplete.")
    if len({item["id"] for item in wanted}) != len(wanted):
        raise CheckCannotRun("Duplicate listing ids across PMSs cannot be reconciled in one pull.")

    pl = fetch_pricelabs(pl_key, wanted, d_from, d_to)

    print(f"PMS-vs-PriceLabs reconciliation   {d_from} -> {d_to}  ({window_days}d window)\n")
    print(f"{'listing':26s} {'PMS bkd':>8s} {'PL bkd':>7s} {'INVISIBLE':>10s} {'cal. value':>11s}  note")
    print("-" * 78)

    exclusions: dict[str, list[str]] = defaultdict(list)
    cal_blocks: list = []
    total_missed = total_value = 0
    unsynced: list[str] = []
    # Listings whose PMS calendar could not be read. They are NOT verified, so
    # they go in the report and the JSON, and the run exits 2. A listing that
    # silently falls out of the exclusion set reads as "clean" downstream.
    pms_failed: list[str] = []

    for listing in wanted:
        lid, name = listing["id"], (listing.get("name") or listing["id"])[:26]
        if lid not in pl or not pl[lid]:
            # PriceLabs dropped it from the response with no error entry. The
            # fetch_pricelabs docstring warns it does this silently; an empty
            # row map would otherwise reconcile to "0 defects".
            unsynced.append(f"{name}: no rows returned by PriceLabs for {d_from}..{d_to}")
            print(f"{name:26s} {'-':>8s} {'-':>7s} {'-':>10s} {'-':>11s}  NO PRICELABS DATA")
            continue
        pl_rows = pl[lid]
        if "__error__" in pl_rows:
            unsynced.append(f"{name}: {pl_rows['__error__']}")
            print(f"{name:26s} {'-':>8s} {'-':>7s} {'-':>10s} {'-':>11s}  NOT SYNCED")
            continue
        try:
            check_price_calendar(pl_rows, d_from, d_to)
        except CheckCannotRun as exc:
            unsynced.append(f"{name}: {exc}")
            print(f"{name:26s} {'-':>8s} {'-':>7s} {'-':>10s} {'-':>11s}  PRICELABS COVERAGE FAILED")
            continue
        pms = listing["pms"]
        bundle_path = os.path.join(cache_dir("reconcile"), cache_name("calendar", lid, pms, d_from, d_to))
        days = None
        compared_at = "live"
        if not args.no_cache and os.path.isfile(bundle_path):
            try:
                b = read_json(bundle_path)
                if (listing_matches(b, lid, pms, [d_from, d_to])
                        and time.time() - datetime.fromisoformat(b["pulled_at"]).timestamp() <= args.ttl_days * 86400):
                    # ATOMIC PAIR. The PMS calendar and the PriceLabs rows must come from the
                    # same instant. Serving a cached calendar against freshly fetched PriceLabs
                    # prices turned every PriceLabs refresh in between into "drift" (measured:
                    # 0 drift dates at pull time, 5 three hours later on the same listing, with
                    # ratios up to 1.31 that were nothing but the stale copy). Use the cached
                    # pair, and say so. --no-cache refetches both.
                    check_calendar(b["pms_days"], "PMS", pms_status, d_from, d_to)
                    check_price_calendar(b["pl_rows"], d_from, d_to)
                    days, pl_rows, compared_at = b["pms_days"], b["pl_rows"], b["pulled_at"]
            except Exception:  # noqa: BLE001
                days = None
        try:
            if days is None:
                days = fetch_pms_calendar(ho_key, lid, d_from, d_to)
                check_calendar(days, "PMS", pms_status, d_from, d_to)
                write_json(bundle_path, {"pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                        "listing": lid, "pms": pms, "window": [d_from, d_to],
                                        "pms_days": days, "pl_rows": pl_rows})
        except CheckCannotRun as e:
            pms_failed.append(f"{name}: {e}")
            print(f"{name:26s} {'-':>8s} {'-':>7s} {'-':>10s} {'-':>11s}  PMS READ FAILED")
            continue

        r = reconcile(days, pl_rows)
        c = calendar_rows(days, pl_rows)
        c["compared_at"] = compared_at
        cal_blocks.append((lid, name, c))
        value = sum(night_value(d) for d in r["invisible"])
        total_missed += len(r["invisible"])
        total_value += value
        exclusions[lid] = [d["date"] for d in r["invisible"]]

        note = ""
        if r["invisible"]:
            notes = sorted({d.get("note") for d in r["invisible"] if d.get("note")})
            note = notes[0][:28] if notes else "no PMS note"
        print(f"{name:26s} {r['pms_reserved']:8d} {r['pl_booked']:7d} "
              f"{len(r['invisible']):10d} {value:11,.0f}  {note}")
        time.sleep(0.4)  # stay under the 60 req/min ceiling

    print("-" * 78)
    print(f"{'TOTAL':26s} {'':8s} {'':7s} {total_missed:10d} {total_value:11,.0f}")
    print("Calendar value uses PMS calendar prices, not realized booking revenue.")

    if unsynced:
        print("\nNOT SYNCED TO PRICELABS (reported, never silently skipped):")
        for u in unsynced:
            print(f"  - {u}")

    if pms_failed:
        print("\nPMS CALENDAR COULD NOT BE READ (these listings are UNVERIFIED):")
        for u in pms_failed:
            print(f"  - {u}")

    if total_missed:
        print(f"\n*** {total_missed} booked nights with calendar value {total_value:,.0f} are invisible "
              f"to PriceLabs. ***")
        print("These dates are SOLD. They must be excluded from the discount candidate")
        print("set and reported as a sync defect, not treated as underperformance.")

    if not args.no_calendar:
        for lid, name, c in cal_blocks:
            print_calendar_block(lid, name, c, sys.stdout)

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump({
                "generated": date.today().isoformat(),
                "window": {"from": d_from, "to": d_to},
                "rule": "PMS RESERVED + PriceLabs available = sync defect, exclude from pricing",
                "exclude_dates_by_listing": {k: v for k, v in exclusions.items() if v},
                "unsynced_listings": unsynced,
                "pms_read_failed": pms_failed,
            }, fh, indent=2)
        print(f"\nExclusion set written to {args.json}")

    if pms_failed or unsynced:
        raise CheckCannotRun(
            f"{len(pms_failed) + len(unsynced)} listing(s) could not be reconciled "
            f"({len(pms_failed)} PMS read failures, {len(unsynced)} PriceLabs failures). "
            "The report above covers the rest; re-run with --listing for the failures."
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CheckCannotRun as exc:
        print(f"RECONCILIATION COULD NOT RUN: {exc}", file=sys.stderr)
        print("Do NOT proceed with pricing recommendations on an unverified calendar.",
              file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # Schema drift must never report a clean calendar.
        print(f"RECONCILIATION COULD NOT RUN: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Do NOT proceed with pricing recommendations on an unverified calendar.", file=sys.stderr)
        sys.exit(2)
