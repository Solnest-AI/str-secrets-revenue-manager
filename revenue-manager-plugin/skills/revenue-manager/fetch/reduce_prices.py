#!/usr/bin/env python3
"""
reduce_prices.py — pull PriceLabs listing prices to disk and emit a compact table.

WHY THIS EXISTS
    The pricing MCP returns `JSON.stringify(data, null, 2)`. That return value
    lands in the agent's context verbatim, so the cost is paid the moment the
    tool is called -- you cannot reduce it after the fact. Measured on one real
    listing over 366 forward dates:

        365d with reason ....... 1,662,343 B   ~461,762 tok
        365d without reason .......193,974 B    ~53,882 tok   (9x smaller)
        Tier A per-date CSV ........16,030 B     ~4,453 tok  (104x smaller)
        Tier B rollup + exceptions .....733 B       ~204 tok (2264x smaller)

    `reason` alone is 87.7% of the per-date payload. This script fetches over
    plain HTTP, writes the raw JSON to a cache directory, and prints only the
    reduced table -- so the agent reads kilobytes instead of megabytes.

USAGE
    reduce_prices.py --listings <id>:<pms>[,<id>:<pms>...] [--days 365] [--tier b]
    reduce_prices.py --all --tier b                  # every listing on the account
    reduce_prices.py --listings <id>:<pms> --reason-dates 2026-09-04,2026-09-05

    --tier b        month rollup + exception rows  (default; portfolio scan)
    --tier a        full per-date CSV, useful fields only  (working a listing)
    --tier both     rollup, then the per-date CSV
    --reason-dates  targeted `reason` pull for named dates only; the expensive
                    field, fetched for the handful of dates you will act on

    Raw responses are cached under --cache-dir (default ~/.cache/revenue-manager/pricelabs) so a later
    reason pull or a re-run costs no extra API calls. Nothing raw is printed.

CREDENTIALS
    PRICELABS_API_KEY from the environment, or from the --env-file .env
    (defaults to the pricelabs connector's own .env next to this bundle).
    The key is never printed.

NOTE
    Direct calls to api.pricelabs.co return 403 without a browser-like
    User-Agent, even with a valid key. The header below is required.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import statistics as st
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from _cache import cache_name, listing_matches, read_json, write_json
from _calendar import pricelabs_status, validate_calendar

BASE = "https://api.pricelabs.co"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# PriceLabs uses these as "no data" markers, not real values.
# -1 = field unavailable (e.g. user_price on a booked date)
# -2 = no same-time-last-year data (listing is under ~13 months old)
SENTINELS = {-1, -2, "-1", "-2", "-1.0", "-2.0"}
# -1 and -2 collapse to the same set members as -1.0 and -2.0 (in Python -1 == -1.0), so
# writing both floats and ints was 6 literals for 4 real members and read as wider
# coverage than it had. The STRING forms "-1.0"/"-2.0" are the ones that were genuinely
# missing: PriceLabs returns them in text fields, and factcheck.SENTINELS already
# included them, so the two modules disagreed about the same marker.

# The only per-date fields any step of the skill actually consumes.
# Everything else in the payload is carried and never read.
TIER_A_FIELDS = [
    "date", "price", "user_price", "uncustomized_price", "min_stay",
    "booking_status", "ADR", "booking_status_STLY", "ADR_STLY",
    "booked_date", "booked_date_STLY", "demand_desc", "occupancy", "unbookable",
]
# booked_date / booked_date_STLY carry pace at equal lead time; demand_desc is
# PriceLabs' per-date demand signal; uncustomized_price reveals that a
# customization moved the price. Dropping them cost 4 of 12 fact classes in the
# tier evaluation, so they stay even though they widen the CSV ~40%.

BYTES_PER_TOKEN = 3.6  # rough; use count_tokens for anything load-bearing


# ---------------------------------------------------------------- helpers

def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def load_key(env_file: Path | None) -> str:
    key = os.environ.get("PRICELABS_API_KEY")
    if key:
        return key.strip()
    candidates = [env_file] if env_file else []
    parents = Path(__file__).resolve().parents
    if len(parents) > 4:  # <repo>/revenue-manager-plugin/skills/revenue-manager/fetch/
        candidates.append(parents[4] / "mcp-servers" / "pricelabs" / ".env")
    candidates.append(Path.cwd() / ".env")
    for path in candidates:
        if path and path.is_file():
            for line in path.read_text().splitlines():
                if line.startswith("PRICELABS_API_KEY="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val
    die("PRICELABS_API_KEY not set and no .env found (try --env-file)")


def call(path: str, key: str, body: dict | None = None, params: str = "") -> dict:
    url = f"{BASE}{path}{params}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if body is not None else "GET",
        headers={
            "X-API-Key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,  # required: 403 without it
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code == 429:
            die(f"rate limited (60/min, 1000/hr). {detail}")
        die(f"HTTP {exc.code} from {path}: {detail}")
    except urllib.error.URLError as exc:
        die(f"network error reaching {path}: {exc.reason}")


def fetch_metrics(listing_id: str, pms: str, key: str,
                  refresh: bool = False) -> dict | None:
    """PriceLabs precomputes the numbers Tier B cannot derive from prices alone.

    `min_prices` is the percent of nights pinned to the floor per window -- the
    single fact whose absence flipped a live verdict from "raise the min" to
    "cut and promote". Also carries mpi (market penetration), revpar vs
    stly_revpar, and booking_pickup vs stly. ~8.7 KB raw, reduced to one line.
    """
    # Disk-cached for a day: metrics move daily at most, and a portfolio re-run inside
    # the window is otherwise one API call per listing for numbers that have not changed.
    import time as _time
    from datetime import datetime as _dt
    from _cache import cache_dir as _cd
    cpath = os.path.join(_cd("metrics"), cache_name("metrics", listing_id, pms))
    data = None
    # `--refresh` means "ignore cache". It used to refresh prices while still serving
    # day-old metrics, so a floor change made minutes earlier did not move
    # floor-pinned% -- the one number whose absence flipped a live verdict.
    if not refresh and os.path.isfile(cpath):
        try:
            b = read_json(cpath)
            if (listing_matches(b, listing_id, pms)
                    and _time.time() - _dt.fromisoformat(b["pulled_at"]).timestamp() <= 86400):
                data = b["data"]
        except Exception:  # noqa: BLE001
            data = None
    if data is None:
        try:
            params = f"?listing_id={listing_id}&pms_name={pms}"   # note: pms_name, not pms (pms 400s)
            data = call("/v1/listing_metrics", key, params=params)
        except SystemExit:
            return None
        try:
            from datetime import timezone as _tz
            write_json(cpath, {"pulled_at": _dt.now(_tz.utc).isoformat(timespec="seconds"),
                               "listing": listing_id, "pms": pms, "data": data})
        except OSError:
            pass  # a cache write failure must never fail the run
    node = data
    for step in ("data", "listing_level"):
        if isinstance(node, dict) and step in node:
            node = node[step]
    return node if isinstance(node, dict) else None


def metrics_line(m: dict, window: str = "30") -> str:
    def pick(field, w=window):
        v = (m.get(field) or {}).get(w) if isinstance(m.get(field), dict) else m.get(field)
        return None if v in (None, -1, -2, "-1", "-2") else v
    bits = []
    for label, field in (("floor_pinned%", "min_prices"), ("mpi", "mpi"),
                         ("revpar", "revpar"), ("stly_revpar", "stly_revpar"),
                         ("adr", "adr")):
        v = pick(field)
        if v is not None:
            bits.append(f"{label}={v}")
    pu, spu = pick("booking_pickup", "-30"), pick("stly_booking_pickup", "-30")
    if pu is not None:
        bits.append(f"pickup30={pu}" + (f" (stly {spu})" if spu is not None else " (no stly)"))
    fp90 = pick("min_prices", "90")
    if fp90 is not None:
        bits.append(f"floor_pinned90%={fp90}")
    return " | ".join(bits) if bits else "(no metrics)"


def num(value) -> float | None:
    """Parse a numeric field, mapping PriceLabs sentinels to None."""
    if value in SENTINELS or value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if parsed < 0 else parsed


def find_date_rows(payload) -> list[dict]:
    """The per-date array moves around between response shapes; locate it."""
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict) and "date" in value[0]:
                return value
        for value in payload.values():
            found = find_date_rows(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = find_date_rows(value)
            if found:
                return found
    return []


def split_payload(payload) -> tuple[dict[str, list[dict]], list[tuple[str, str]]]:
    """Return ({listing_id: rows}, [(listing_id, error)]).

    A listing whose sync is toggled off comes back as {id, pms, error,
    error_status} with no date rows. Silently skipping it makes an unmanaged
    property invisible to the operator -- the raw payload says so, so the
    reduction must too.
    """
    out: dict[str, list[dict]] = {}
    errors: list[tuple[str, str]] = []
    if isinstance(payload, dict) and (payload.get("id") or payload.get("listing_id")):
        payload = [payload]
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                lid = str(entry.get("id") or entry.get("listing_id") or f"listing_{len(out)}")
                if entry.get("error"):
                    errors.append((lid, str(entry.get("error"))))
                    continue
                rows = find_date_rows(entry)
                if rows:
                    out[lid] = rows
                else:
                    errors.append((lid, "no readable per-date rows returned"))
    if not out and not errors:
        rows = find_date_rows(payload)
        if rows:
            out["listing"] = rows
    return out, errors


def payload_matches(payload, targets) -> bool:
    """Require the complete requested identity set before trusting prices or a cache."""
    expected = dict(targets)
    entries = payload if isinstance(payload, list) else [payload]
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        lid = str(entry.get("id") or entry.get("listing_id") or "")
        pms = entry.get("pms", entry.get("pms_name"))
        if lid not in expected or lid in seen or (pms is not None and pms != expected[lid]):
            return False
        seen.add(lid)
    return bool(expected) and seen == set(expected)


# ---------------------------------------------------------------- reducers

def pacing_line(rows: list[dict], today: str | None = None, windows=(30, 60, 90), min_stly_cov: float = 0.20) -> str:
    """Rolling forward pacing: occupancy now vs same time last year, next 30/60/90 nights.

    Why this exists: the per-date table carries `booking_status_STLY` for every night, but a
    reader has to aggregate 366 rows to see it, and in a live decision eval the reduced side
    called a listing "even" that was 20% booked against 44% STLY. Same corrections as tier_b:
    blocked nights leave the denominator, and a window with under 20% STLY coverage says
    "n/a" rather than 0%. Verdict: behind / ahead when the gap is more than 5 points.
    """
    fwd = sorted((r for r in rows if r.get("date") and (today is None or str(r["date"]) > today)),
                 key=lambda r: str(r["date"]))
    bits = []
    for w in windows:
        win = fwd[:w]
        if not win:
            continue
        booked = sum(1 for r in win if is_booked(str(r.get("booking_status", ""))))
        blocked = sum(1 for r in win if str(r.get("booking_status", "")).strip().lower() == "blocked")
        bookable = len(win) - blocked
        occ = 100.0 * booked / bookable if bookable else None
        stly_vals = [str(r.get("booking_status_STLY", "")).strip() for r in win]
        cov = sum(1 for v in stly_vals if v and v not in SENTINELS)
        if cov / len(win) >= min_stly_cov:
            s_booked = sum(1 for v in stly_vals if is_booked(v))
            s_blocked = sum(1 for v in stly_vals if v.lower() == "blocked")
            s_bookable = len(win) - s_blocked
            stly = 100.0 * s_booked / s_bookable if s_bookable else None
        else:
            stly = None
        if occ is None:
            bits.append(f"next{w} occ=n/a")
            continue
        if stly is None:
            bits.append(f"next{w} occ={occ:.1f}% stly=n/a")
        else:
            verdict = "behind" if occ < stly - 5 else "ahead" if occ > stly + 5 else "even"
            bits.append(f"next{w} occ={occ:.1f}% stly={stly:.1f}% ({verdict})")
    return "[pacing] " + " | ".join(bits) if bits else "[pacing] (no forward rows)"


def tier_a(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TIER_A_FIELDS)
    for row in rows:
        writer.writerow([
            "" if row.get(f) in SENTINELS else row.get(f, "")
            for f in TIER_A_FIELDS
        ])
    return buf.getvalue()


def is_booked(status: str) -> bool:
    """PriceLabs emits 'Booked' AND 'Booked (Check-In)' as separate values.
    A check-in night is a revenue night; matching only == 'Booked' undercounts
    occupancy (measured: 21 vs 35 of 366 on a live listing, a 40% miss)."""
    return status.strip().lower().startswith("booked")


def tier_b(rows: list[dict], gap_pct: float, min_stly_cov: float = 0.20) -> tuple[str, str, dict]:
    """Month rollup + exception rows.

    Two corrections that the raw field values require:

    1. Blocked nights are excluded from the occupancy denominator. An
       owner-blocked night is not a night you failed to sell, and counting it
       as occupied inverts the underpriced signal.
    2. `booking_status` and `booking_status_STLY` are EMPTY for available
       nights -- they are only populated when a night is booked or blocked.
       So an empty STLY means either "was available last year" or "the listing
       did not exist yet", and those are indistinguishable per-row. We resolve
       it per-month: zero populated STLY values in a month means no history,
       and the column is left blank rather than reported as 0% (which would
       read as a catastrophic year-over-year collapse on a new listing).
    """
    months: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "booked": 0, "blocked": 0, "ask": [],
                 "stly_booked": 0, "stly_blocked": 0, "stly_cov": 0}
    )
    for row in rows:
        month = str(row.get("date", ""))[:7]
        bucket = months[month]
        bucket["n"] += 1
        status = str(row.get("booking_status", ""))
        if is_booked(status):
            bucket["booked"] += 1
        elif status.strip().lower() == "blocked":
            bucket["blocked"] += 1
        stly = str(row.get("booking_status_STLY", "")).strip()
        # A "-1"/"-2" sentinel is PriceLabs saying "no value", NOT "the listing existed
        # and sold nothing". Counted as coverage it lands in the denominator as an
        # available night and prints a fake 0% STLY, i.e. a -100% year-over-year that
        # never happened. pacing_line() filters the same field; tier_b did not.
        if stly and stly not in SENTINELS:
            bucket["stly_cov"] += 1
            if is_booked(stly):
                bucket["stly_booked"] += 1
            elif stly.lower() == "blocked":
                bucket["stly_blocked"] += 1
        ask = num(row.get("user_price")) or num(row.get("price"))
        if ask:
            bucket["ask"].append(ask)

    roll = io.StringIO()
    writer = csv.writer(roll)
    writer.writerow(["month", "nights", "bookable", "booked", "blocked",
                     "occ_pct", "stly_occ_pct", "stly_cov", "ask_med", "ask_min", "ask_max"])
    totals = {"nights": 0, "bookable": 0, "booked": 0, "blocked": 0, "stly_months": 0}
    for month in sorted(months):
        b = months[month]
        bookable = b["n"] - b["blocked"]
        # Same correction for last year: an owner block then was not a night
        # you failed to sell either, or the YoY column compares two denominators.
        stly_bookable = b["n"] - b["stly_blocked"]
        totals["nights"] += b["n"]
        totals["bookable"] += bookable
        totals["booked"] += b["booked"]
        totals["blocked"] += b["blocked"]
        # STLY only means something once the listing demonstrably had a calendar
        # that month. Below the coverage floor, say nothing rather than "0%".
        has_history = b["n"] and (b["stly_cov"] / b["n"]) >= min_stly_cov
        if has_history:
            totals["stly_months"] += 1
        writer.writerow([
            month, b["n"], bookable, b["booked"], b["blocked"],
            round(100 * b["booked"] / bookable, 1) if bookable else "",
            round(100 * b["stly_booked"] / stly_bookable, 1) if has_history and stly_bookable else "",
            f"{b['stly_cov']}/{b['n']}",
            round(st.median(b["ask"])) if b["ask"] else "",
            round(min(b["ask"])) if b["ask"] else "",
            round(max(b["ask"])) if b["ask"] else "",
        ])

    exc = io.StringIO()
    ewriter = csv.writer(exc)
    ewriter.writerow(["date", "ask", "recommended", "gap_pct", "min_stay", "status"])
    n_exc = 0
    for row in rows:
        ask, rec = num(row.get("user_price")), num(row.get("price"))
        if ask and rec:
            gap = 100 * (rec - ask) / ask
            if abs(gap) >= gap_pct:
                ewriter.writerow([row.get("date"), round(ask), round(rec),
                                  round(gap, 1), row.get("min_stay"),
                                  row.get("booking_status")])
                n_exc += 1
    totals["exceptions"] = n_exc
    return roll.getvalue(), exc.getvalue(), totals


# `reason` has no summary field. It is four groups of numbered dicts, each entry
# shaped {key, value, price, title}, plus a bulky `listing_info` block. These are
# the groups that explain a price; `listing_info` is booking stats we already
# have from the per-date fields and is ~2x the size of everything else combined.
REASON_GROUPS = ["market_factors", "pricing_customizations",
                 "other_customizations", "final_adjustments", "final_price_override"]


def flatten_factors(group) -> list[str]:
    """{'0': {title, value, price}, '1': {...}} -> ['Seasonality -13% -> 608', ...]"""
    if not isinstance(group, dict):
        return []
    parts = []
    for _, entry in sorted(group.items(), key=lambda kv: str(kv[0])):
        if not isinstance(entry, dict):
            continue
        title = entry.get("title") or entry.get("key") or "?"
        value, price = entry.get("value"), entry.get("price")
        bit = f"{title} {value}" if value not in (None, "") else str(title)
        # SENTINELS, not a bare -1: a `-2` price rendered as a real number
        # ("Seasonality -13% -> -2") in the factor breakdown an operator reads.
        if price not in (None, "") and price not in SENTINELS:
            bit += f" -> {price}"
        parts.append(bit)
    return parts


def reason_slice(rows: list[dict], wanted: set[str]) -> str:
    """Emit a flattened price explanation for named dates only.

    `reason` is 87.7% of the whole payload (~2,332 B/date), so it is pulled
    per-decision and rendered as one line per factor rather than raw JSON.
    Dates that were asked for but are not in the payload (already past, or
    outside what PriceLabs returned) are named explicitly, so a missing block
    reads as "not fetched", never as "no factors".
    """
    out = []
    seen: set[str] = set()
    no_reason: list[str] = []
    for row in rows:
        date = str(row.get("date", ""))
        if date not in wanted:
            continue
        seen.add(date)
        reason = row.get("reason")
        if not isinstance(reason, dict):
            # The date IS in the response, it just carries no reason object. Falling
            # straight through left it in `seen`, so it was neither rendered nor listed
            # under "not in the response" -- it disappeared from the output entirely and
            # read as "no factors", which is the opposite of what the docstring promises.
            no_reason.append(date)
            continue
        ask, rec = num(row.get("user_price")), num(row.get("price"))
        head = f"--- {date} ---"
        if rec:
            head += f"  recommended {rec:g}"
        if ask:
            head += f", ask {ask:g}"
        lines = [head]
        for group in REASON_GROUPS:
            factors = flatten_factors(reason.get(group))
            if factors:
                lines.append(f"  {group}: " + " | ".join(factors))
        bounds = flatten_factors(reason.get("thresholds"))
        if bounds:
            lines.append("  thresholds: " + " | ".join(bounds))
        out.append("\n".join(lines))
    missing = sorted(wanted - seen)
    if missing:
        out.append("!! not in the response (past, or outside the fetched window): "
                   + ", ".join(missing))
    if no_reason:
        out.append("!! present in the response but carrying no reason breakdown: "
                   + ", ".join(sorted(no_reason))
                   + "  (this is NOT 'no factors' -- the field was absent)")
    return "\n".join(out) if out else "(no reason data for the requested dates)"


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch PriceLabs prices to disk, print a compact table.")
    ap.add_argument("--listings", help="id:pms[,id:pms...]")
    ap.add_argument("--all", action="store_true", help="every listing on the account")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--tier", choices=["a", "b", "both"], default="a")
    ap.add_argument("--reason-dates", help="comma-separated YYYY-MM-DD; pulls reason for these only")
    ap.add_argument("--gap-pct", type=float, default=12.0, help="exception threshold (default 12)")
    from _cache import cache_dir as _cd
    ap.add_argument("--cache-dir", default=_cd("pricelabs"),
                    help="raw payload cache (default ~/.cache/revenue-manager/pricelabs)")
    ap.add_argument("--env-file", type=Path)
    ap.add_argument("--refresh", action="store_true", help="ignore cache")
    ap.add_argument("--no-metrics", action="store_true",
                    help="skip the listing_metrics pull (floor-pinned pct, MPI, RevPAR vs STLY)")
    args = ap.parse_args()

    if not args.listings and not args.all:
        ap.error("pass --listings or --all")
    if args.days < 1:
        ap.error("--days must be at least 1")

    key = load_key(args.env_file)
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    if args.all:
        catalog = call("/v1/listings", key)
        entries = catalog.get("listings", catalog) if isinstance(catalog, dict) else catalog
        targets = [(str(e["id"]), str(e.get("pms", "smartbnb")))
                   for e in entries if isinstance(e, dict) and e.get("id")]
    else:
        targets = []
        for chunk in args.listings.split(","):
            if ":" not in chunk:
                die(f"bad --listings entry {chunk!r}; expected id:pms")
            lid, pms = chunk.split(":", 1)
            targets.append((lid.strip(), pms.strip()))

    if not targets or any(not lid or not pms for lid, pms in targets):
        die("no complete listing id/PMS targets were supplied")
    if len({lid for lid, _ in targets}) != len(targets):
        die("each listing id must appear once per pull; request duplicate ids on different PMSs separately")

    want_reason = bool(args.reason_dates)
    wanted_dates = {d.strip() for d in args.reason_dates.split(",")} if want_reason else set()
    today = dt.date.today()
    if want_reason:
        # Narrow the window to the span actually asked about. Pulling `reason`
        # over a full year to read two dates costs ~1 MB of API payload for
        # ~5 KB of answer -- cheap in context but wasteful on the wire.
        ordered = sorted(wanted_dates)
        date_from = max(ordered[0], today.isoformat())
        date_to = ordered[-1]
        if date_to < date_from:
            die(f"--reason-dates are all in the past (earliest bookable date is {today})")
    else:
        date_from = today.isoformat()
        date_to = (today + dt.timedelta(days=args.days - 1)).isoformat()

    # The cache key must pin everything that changes the payload: the exact
    # listing set and the exact window. Plain pulls derive date_to from
    # --days; reason pulls derive both ends from --reason-dates, so a wider
    # second request must not be served from a narrower first one. Today's
    # date is in the key so a reason pull for far-future dates does not serve
    # week-old prices forever.
    cache_file = cache / cache_name("prices", sorted(targets), date_from, date_to,
                                   want_reason, today.isoformat())
    payload = None
    if cache_file.is_file() and not args.refresh:
        try:
            candidate = read_json(cache_file)
            if payload_matches(candidate, targets):
                payload = candidate
                source = "cache"
        except (OSError, ValueError, TypeError):
            pass
    if payload is None:
        body = {"listings": [
            {"id": lid, "pms": pms, "dateFrom": date_from, "dateTo": date_to,
             "reason": want_reason}
            for lid, pms in targets
        ]}
        payload = call("/v1/listing_prices", key, body)
        if not payload_matches(payload, targets):
            die("PriceLabs response does not identify every requested listing/PMS exactly once")
        write_json(cache_file, payload)
        source = "api"

    raw_bytes = cache_file.stat().st_size
    # what the MCP would have put in context: pretty-printed, not compact
    mcp_bytes = len(json.dumps(payload, indent=2))

    by_listing, listing_errors = split_payload(payload)
    if not want_reason:
        for lid, rows in list(by_listing.items()):
            try:
                validate_calendar(rows, "PriceLabs", pricelabs_status, date_from, date_to)
            except (TypeError, ValueError) as exc:
                listing_errors.append((lid, str(exc)))
                del by_listing[lid]
    if not by_listing and not listing_errors:
        die("no per-date rows found in the response")

    pms_of = {lid: pms for lid, pms in targets}
    metrics: dict[str, dict] = {}
    if not args.no_metrics and not want_reason:
        for lid in by_listing:
            m = fetch_metrics(lid, pms_of.get(lid, "smartbnb"), key,
                              refresh=args.refresh)
            if m:
                metrics[lid] = m

    out_parts: list[str] = []
    for lid, rows in by_listing.items():
        header = f"### listing {lid}  ({len(rows)} dates, {date_from} to {date_to})"
        if want_reason:
            out_parts.append(header + "\n" + reason_slice(rows, wanted_dates))
            continue
        # Computed once for every tier. Pacing exists because a live eval read a
        # listing as "even" when it was 20% booked against 44% STLY, and it used to be
        # printed ONLY for --tier a -- absent from `b` and from `both`, which is the
        # tier an operator picks for the fullest look.
        mline = f"[metrics] {metrics_line(metrics[lid])}\n" if lid in metrics else ""
        pline = pacing_line(rows, today.isoformat()) + "\n"
        if args.tier in ("b", "both"):
            roll, exc, totals = tier_b(rows, args.gap_pct)
            summary = (f"{totals['nights']} nights, {totals['bookable']} bookable, "
                       f"{totals['blocked']} blocked, {totals['exceptions']} exception dates "
                       f"(gap >= {args.gap_pct:g}%)")
            out_parts.append(f"{header}\n{summary}\n{mline}{pline}\n[months]\n{roll}\n"
                             f"[exceptions]\n{exc}")
        if args.tier == "a":
            out_parts.append(f"{header}\n{mline}{pline}[per-date]\n{tier_a(rows)}")
        elif args.tier == "both":
            out_parts.append(f"[per-date]\n{tier_a(rows)}")

    if listing_errors:
        lines = "\n".join(f"  !! {lid}: {err}" for lid, err in listing_errors)
        out_parts.insert(0, f"### {len(listing_errors)} LISTING(S) RETURNED NO DATA\n{lines}\n"
                            "These are NOT in the tables below. Treat them as unmanaged until fixed.")
    body_text = "\n".join(out_parts)
    out_tok = len(body_text) / BYTES_PER_TOKEN
    mcp_tok = mcp_bytes / BYTES_PER_TOKEN

    print(body_text)
    print(f"\n[reduce_prices] source={source} listings={len(by_listing)} "
          f"raw={raw_bytes:,}B cached at {cache_file}")
    print(f"[reduce_prices] context: ~{out_tok:,.0f} tok emitted vs ~{mcp_tok:,.0f} tok "
          f"if the raw payload had been returned ({mcp_tok / max(out_tok, 1):,.0f}x smaller)")
    if listing_errors:
        raise SystemExit(2)


if __name__ == "__main__":
    # Every sibling reducer exits 2 on "cannot produce a trustworthy answer". This
    # module was called bare, so schema drift produced a raw traceback and exit 1,
    # and the exit-2 contract the whole skill reads did not hold here.
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - drift must read as "cannot produce"
        print(f"PRICES UNAVAILABLE: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        print("Do not price from this run; the per-date table could not be produced.",
              file=sys.stderr)
        sys.exit(2)
