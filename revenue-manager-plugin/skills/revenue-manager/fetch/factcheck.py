#!/usr/bin/env python3
"""Fact-class harness: proves a reducer preserved every fact a pricing decision reads.

WHY
---
In August a "cheaper" tier dropped four fields that looked like noise and inverted a
verdict on one listing. Token cuts are only safe when the facts the decision depends on
survive the cut, so every reducer in this directory must pass this harness before it
ships, and the smoke test runs it on every commit.

HOW
---
For each source there are two extractors that compute the SAME named facts:

  *_facts_full(raw)      reads the untouched API payload
  *_facts_reduced(text)  parses the reducer's printed output

They deliberately share no parsing code. If the reducer mangles a number, the two sides
disagree and the check fails. `compare()` lists every mismatch by name.

Precision is fixed here and the reducers import it, so "equal" means equal at the
precision the decision actually uses (an ADR of 538.94 vs 538.9 is not a lost fact).

USAGE
-----
    python3 factcheck.py airroi --full raw.json --reduced reduced.txt [--subject-id ID]
    python3 factcheck.py neighborhood --full raw.json --reduced reduced.txt --category 4 [--days N]
    python3 factcheck.py calendar --full <gate bundle>.json --reduced <one listing's block>.txt
    exit 0 = every fact matches, exit 1 = mismatch (listed), exit 2 = could not check
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import sys

from _calendar import pms_status, pricelabs_status

# One place for precision. Reducers round to these; extractors compare at these.
PRECISION = {
    "adr": 1, "occ": 3, "revenue": 0, "revpar": 1, "rating": 2, "min_nights": 0,
    # neighborhood: whole dollars and whole percent. A decision comparing an ask of
    # $700 to a p75 of $1,115 does not read the cents, and 85.29% vs 85% is not a
    # different market.
    "nb_price": 0, "nb_pct": 0, "count": 0,
    # calendar reconciliation: markup ratio to 3 dp (1.000 = no markup), prices whole
    "ratio": 3, "price": 0,
    "pct": 1, "days": 1,   # reservations: distribution shares and mean lead/LOS
}


def _r(value, kind: str):
    if value is None or value == "":
        return None
    v = round(float(value), PRECISION[kind])
    return int(v) if PRECISION[kind] == 0 else v


def _median(values, kind):
    # Round each input to decision precision FIRST. A decision only ever sees rounded
    # values, so the fact is "the median of what it sees". Aggregating raw values and
    # rounding the result can differ by one unit in the last place (780.27 vs 780.25),
    # which is not a lost fact, it is double rounding. The harness caught this on its
    # first run; do not "fix" it by adding decimals to the CSV.
    vals = [_r(v, kind) for v in values if v not in (None, "")]
    return _r(statistics.median(vals), kind) if vals else None


def _quartile(values, q, kind):
    vals = sorted(_r(v, kind) for v in values if v not in (None, ""))
    if not vals:
        return None
    # nearest-rank quartile: deterministic, no interpolation to argue about
    idx = max(0, min(len(vals) - 1, round(q * (len(vals) - 1))))
    return _r(vals[idx], kind)


# ---------------------------------------------------------------- AirROI comparables

AIRROI_FACTS = [
    "comp_count", "currency", "adr_median", "adr_p25", "adr_p75", "occ_median",
    "revenue_median", "revpar_median", "rating_median", "min_nights_median",
    "top5_by_revenue", "subject_in_set", "subject_rank_revenue",
]

# The CSV columns the reducer must emit, in order. The harness parses by header name,
# so extra columns are tolerated; missing ones are a failure.
AIRROI_COLUMNS = [
    "listing_id", "name", "bedrooms", "baths", "guests", "ttm_revenue", "ttm_adr",
    "ttm_occ", "ttm_revpar", "rating", "reviews", "currency", "min_nights", "los",
]


def _comp_rows_from_raw(raw: dict) -> list[dict]:
    """Flatten the nested AirROI comp into the same shape the CSV carries."""
    out = []
    for c in raw.get("listings", []):
        li, pd, pm = c.get("listing_info", {}), c.get("property_details", {}), c.get("performance_metrics", {})
        rt, pi, bs = c.get("ratings", {}), c.get("pricing_info", {}), c.get("booking_settings", {})
        out.append({
            "listing_id": str(li.get("listing_id", "")),
            "name": li.get("listing_name", ""),
            "bedrooms": pd.get("bedrooms"), "baths": pd.get("baths"), "guests": pd.get("guests"),
            "ttm_revenue": pm.get("ttm_revenue"), "ttm_adr": pm.get("ttm_avg_rate"),
            "ttm_occ": pm.get("ttm_occupancy"), "ttm_revpar": pm.get("ttm_revpar"),
            "rating": rt.get("rating_overall"), "reviews": rt.get("num_reviews"),
            "currency": pi.get("currency"), "min_nights": bs.get("min_nights"),
            "los": pm.get("ttm_avg_length_of_stay"),
        })
    return out


def _facts_from_rows(rows: list[dict], subject_id: str | None, subject_rank: int | None,
                     subject_in_set: bool) -> dict:
    comps = [r for r in rows if not (subject_id and str(r["listing_id"]) == str(subject_id))]
    # Rank on the value at its DECLARED precision. The reduced CSV prints revenue in
    # whole dollars (PRECISION["revenue"] = 0), so ranking the full side on raw floats
    # made 100.1 and 100.4 order differently there than the two identical 100s do here
    # -- a false mismatch, and a subject rank that disagreed with itself.
    by_rev = sorted(comps, key=lambda r: (-(_r(r["ttm_revenue"], "revenue") or 0),
                                          str(r["listing_id"])))
    currencies = sorted({str(r["currency"]) for r in comps if r.get("currency")})
    return {
        "comp_count": len(comps),
        # No comps means no currency to report. Falling through to the MIXED branch
        # produced the literal string "MIXED:" for an empty set, while the reservations
        # extractor returns None for the same concept.
        "currency": (currencies[0] if len(currencies) == 1
                     else None if not currencies
                     else f"MIXED:{','.join(currencies)}"),
        "adr_median": _median((r["ttm_adr"] for r in comps), "adr"),
        "adr_p25": _quartile([r["ttm_adr"] for r in comps], 0.25, "adr"),
        "adr_p75": _quartile([r["ttm_adr"] for r in comps], 0.75, "adr"),
        "occ_median": _median((r["ttm_occ"] for r in comps), "occ"),
        "revenue_median": _median((r["ttm_revenue"] for r in comps), "revenue"),
        "revpar_median": _median((r["ttm_revpar"] for r in comps), "revpar"),
        "rating_median": _median((r["rating"] for r in comps), "rating"),
        "min_nights_median": _median((r["min_nights"] for r in comps), "min_nights"),
        "top5_by_revenue": [str(r["listing_id"]) for r in by_rev[:5]],
        "subject_in_set": subject_in_set,
        "subject_rank_revenue": subject_rank,
    }


def airroi_facts_full(raw: dict, subject_id: str | None = None) -> dict:
    rows = _comp_rows_from_raw(raw)
    rank, in_set = None, False
    if subject_id:
        ranked = sorted(rows, key=lambda r: (-(_r(r["ttm_revenue"], "revenue") or 0),
                                             str(r["listing_id"])))
        for i, r in enumerate(ranked, 1):
            if str(r["listing_id"]) == str(subject_id):
                rank, in_set = i, True
    return _facts_from_rows(rows, subject_id, rank, in_set)


def airroi_facts_reduced(text: str) -> dict:
    """Parse the reducer's output: a '# key=value ...' header line, then a CSV block."""
    header, csv_lines = {}, []
    for line in text.splitlines():
        if line.startswith("# "):
            for kv in line[2:].split():
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    header[k] = v
        elif line.strip() and not line.startswith("#"):
            csv_lines.append(line)
    if not csv_lines:
        raise ValueError("no CSV block found in reduced output")
    reader = csv.DictReader(io.StringIO("\n".join(csv_lines)))
    rows = list(reader)
    # Read the columns off the HEADER, not off the first data row. A zero-row section
    # is a valid answer everywhere else in this codebase, and checking rows[0] turned
    # a correct empty table into "missing all 14 columns" -> exit 2, blaming a header
    # that was present and correct.
    missing = [c for c in AIRROI_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"reduced CSV is missing columns: {missing}")
    in_set = header.get("subject_in_set", "false").lower() == "true"
    rank = int(header["subject_rank_revenue"]) if header.get("subject_rank_revenue", "none") != "none" else None
    # the reducer already excluded the subject from the CSV; pass no id so nothing is re-excluded
    facts = _facts_from_rows(rows, None, rank, in_set)
    return facts


def compare(full: dict, reduced: dict, names: list[str]) -> list[str]:
    bad = []
    for n in names:
        a, b = full.get(n), reduced.get(n)
        if a != b:
            bad.append(f"{n}: full={a!r} reduced={b!r}")
    return bad


# ---------------------------------------------------------------- PriceLabs neighborhood

NEIGHBORHOOD_FACTS = [
    "category", "listings_used", "currency", "daily_rows", "daily_first", "daily_last",
    "base_p25", "base_p50", "base_p75", "base_p90",
    "p50_next30_mean", "p90_next30_mean", "booked_med_next30_mean",
    "occ_next30_mean", "occ_stly_next30_mean", "occ_ly_next30_mean",
    "new_bk_next30_sum", "avail_next30_mean",
    "p50_by_date_digest", "occ_by_date_digest",
    "kpi_months", "kpi_booking_window_l365", "kpi_los_l365", "kpi_pickup7_latest",
    "monthly_p50_digest",
]

# Series kept from 'Future Occ/New/Canc' (label -> csv column). Dropped on purpose:
# Total_Available_Listings_LY, Occupancy_L2Y, Occupancy_ST2Y (two years back / LY supply).
NB_OCC_SERIES = {
    "Occupancy": "occ", "Occupancy_STLY": "occ_stly", "Occupancy_LY": "occ_ly",
    "New Bookings": "new_bk", "New_Bookings_STLY": "new_bk_stly",
    "Canceled Bookings": "cancel", "Total_Available_Listings": "avail",
}
NB_PCT_SERIES = {
    "25th Percentile": "p25", "50th Percentile": "p50", "75th Percentile": "p75",
    "Median Booked Price": "booked_med", "90th Percentile": "p90", "N_Bookings": "n_bk",
}
NB_KPI_SERIES = {
    "Total Available Days": "avail_days", "Booking Window": "booking_window", "LOS": "los",
    "Revenue": "revenue", "Total Booked Days": "booked_days", "Future Available Days": "fut_avail",
    "Future Booked Days": "fut_booked", "Future Booked Days STLY": "fut_booked_stly",
    "7 Day Pickup": "pickup7", "7 Day Pickup STLY": "pickup7_stly",
}
NB_DAILY_COLUMNS = ["date"] + list(NB_PCT_SERIES.values()) + list(NB_OCC_SERIES.values())
NB_KPI_COLUMNS = ["month"] + list(NB_KPI_SERIES.values())
NB_MONTHLY_COLUMNS = ["month"] + list(NB_PCT_SERIES.values())


def _digest(pairs) -> str:
    """Stable fingerprint of a (date, value) sequence so per-date exactness is checked
    without listing hundreds of values in the fact table."""
    import hashlib
    return hashlib.sha1("|".join(f"{d}={v}" for d, v in pairs).encode()).hexdigest()[:16]


def _nb_kind(col: str) -> str:
    if col in ("p25", "p50", "p75", "p90", "booked_med"):
        return "nb_price"
    if col.startswith("occ"):
        return "nb_pct"
    return "count"


NB_REQUIRED_SERIES = ("50th Percentile", "Occupancy")   # without these the table is not a market


def label_index(labels: list, lab: str) -> int | None:
    """Resolve a series label. Exact first, then case-insensitive (PriceLabs spells
    N_Bookings/N_bookings differently between bedroom and custom comp-set payloads).
    None when absent: the column is left blank, never filled from another series."""
    if lab in labels:
        return labels.index(lab)
    low = [str(x).lower() for x in labels]
    return low.index(lab.lower()) if lab.lower() in low else None


def neighborhood_missing_series(raw: dict, category: str) -> list[str]:
    """Series the reducer wants that this payload does not carry (reported, not substituted)."""
    d = raw.get("data", raw)
    out = []
    for block, series in (("Future Percentile Prices", NB_PCT_SERIES), ("Future Occ/New/Canc", NB_OCC_SERIES),
                          ("Market KPI", NB_KPI_SERIES)):
        labels = d.get(block, {}).get("Labels", [])
        out += [lab for lab in series if label_index(labels, lab) is None]
    return out


NB_BASE_PREFIXES = {"25th Percentile": "base_p25", "50th Percentile": "base_p50",
                    "75th Percentile": "base_p75", "90th Percentile": "base_p90"}


def neighborhood_base_percentiles(d: dict, category: str) -> dict:
    """Base-price percentiles by LABEL. Bedroom payloads carry four labelled values; custom
    comp-set payloads carry nine (median booked nightly/weekly/monthly, LOS, lead time, then
    the percentiles), so position is not a key. Legacy payloads without labels: first four."""
    st = d["Summary Table Base Price"]
    y = st["Category"][category]["Y_values"]
    labels = st.get("Labels")
    if labels:
        low = [str(lab).lower() for lab in labels]
        out = {}
        for prefix, key in NB_BASE_PREFIXES.items():
            j = next((i for i, lab in enumerate(low) if lab.startswith(prefix.lower())), None)
            out[key] = y[j] if (j is not None and j < len(y)) else None
        return out
    # zip() truncates to the shorter side, so the old `+ [None] * 4` padding never
    # reached the result. strict=False says the truncation is deliberate.
    return dict(zip(NB_BASE_PREFIXES.values(), list(y[:4]), strict=False))


def neighborhood_daily_from_raw(raw: dict, category: str, days: int | None = None,
                                start: str | None = None) -> list[dict]:
    """Forward daily rows for one category. `start` (YYYY-MM-DD) drops dates before it: custom
    comp-set payloads begin six months in the past, bedroom payloads begin tomorrow."""
    d = raw.get("data", raw)
    pct = d["Future Percentile Prices"]["Category"][category]
    occ = d["Future Occ/New/Canc"]["Category"][category]
    pct_labels = d["Future Percentile Prices"]["Labels"]
    occ_labels = d["Future Occ/New/Canc"]["Labels"]
    for lab in NB_REQUIRED_SERIES:
        if label_index(pct_labels, lab) is None and label_index(occ_labels, lab) is None:
            raise ValueError(f"required series {lab!r} missing from the neighborhood payload")
    # occ series come wrapped one level deeper than percentile series
    occ_by_label = {}
    for lab in NB_OCC_SERIES:
        j = label_index(occ_labels, lab)
        if j is not None:
            y = occ["Y_values"][j]
            occ_by_label[lab] = y[0] if (y and isinstance(y[0], list)) else y
    occ_dates = {dt: i for i, dt in enumerate(occ["X_values"])}
    rows = []
    for i, dt in enumerate(pct["X_values"]):
        if start and str(dt) < start:
            continue
        if days is not None and len(rows) >= days:
            break
        row = {"date": dt}
        for lab, col in NB_PCT_SERIES.items():
            j = label_index(pct_labels, lab)
            # Length-guarded exactly like the occupancy side below. A percentile series
            # shorter than X_values raised IndexError straight out of the extractor.
            ser = pct["Y_values"][j] if j is not None else None
            row[col] = (_r(ser[i], _nb_kind(col))
                        if ser is not None and i < len(ser) else None)
        k = occ_dates.get(dt)
        for lab, col in NB_OCC_SERIES.items():
            ser = occ_by_label.get(lab)
            row[col] = _r(ser[k], _nb_kind(col)) if (k is not None and ser is not None and k < len(ser)) else None
        rows.append(row)
    return rows


def _nb_facts_from_tables(meta: dict, daily: list[dict], monthly: list[dict], kpi: list[dict]) -> dict:
    n30 = daily[:30]
    def mean(col, kind):
        vals = [float(r[col]) for r in n30 if r.get(col) not in (None, "")]
        return _r(sum(vals) / len(vals), kind) if vals else None
    def total(col):
        return int(sum(float(r[col]) for r in n30 if r.get(col) not in (None, "")))
    l365 = next((r for r in kpi if r["month"] == "Last 365 Days"), None)
    latest = next((r for r in reversed(kpi) if not str(r["month"]).startswith("Last")), None)
    return {
        "category": str(meta.get("category")),
        "listings_used": _r(meta.get("listings_used"), "count"),
        "currency": meta.get("currency"),
        "daily_rows": len(daily),
        "daily_first": daily[0]["date"] if daily else None,
        "daily_last": daily[-1]["date"] if daily else None,
        "base_p25": _r(meta.get("base_p25"), "nb_price"), "base_p50": _r(meta.get("base_p50"), "nb_price"),
        "base_p75": _r(meta.get("base_p75"), "nb_price"), "base_p90": _r(meta.get("base_p90"), "nb_price"),
        "p50_next30_mean": mean("p50", "nb_price"), "p90_next30_mean": mean("p90", "nb_price"),
        "booked_med_next30_mean": mean("booked_med", "nb_price"),
        "occ_next30_mean": mean("occ", "nb_pct"), "occ_stly_next30_mean": mean("occ_stly", "nb_pct"),
        "occ_ly_next30_mean": mean("occ_ly", "nb_pct"),
        "new_bk_next30_sum": total("new_bk"), "avail_next30_mean": mean("avail", "count"),
        "p50_by_date_digest": _digest((r["date"], r["p50"]) for r in daily),
        "occ_by_date_digest": _digest((r["date"], r["occ"]) for r in daily),
        "kpi_months": len(kpi),
        "kpi_booking_window_l365": _r(l365["booking_window"], "count") if l365 else None,
        "kpi_los_l365": _r(l365["los"], "count") if l365 else None,
        "kpi_pickup7_latest": _r(latest["pickup7"], "count") if latest else None,
        "monthly_p50_digest": _digest((r["month"], r["p50"]) for r in monthly),
    }


def neighborhood_facts_full(raw: dict, category: str, days: int | None = None, start: str | None = None) -> dict:
    d = raw.get("data", raw)
    meta = {"category": category.replace(" ", "_"), "currency": d.get("currency"),
            "listings_used": d["Future Percentile Prices"]["Category"][category].get("Listings Used"),
            **neighborhood_base_percentiles(d, category)}
    daily = neighborhood_daily_from_raw(raw, category, days, start)
    mp = d.get("Future Percentile Prices Monthly", {}).get("Category", {}).get(category)  # absent on custom comp sets
    mlabels = d["Future Percentile Prices"]["Labels"]
    monthly = [{"month": m, **{col: (_r(mp["Y_values"][label_index(mlabels, lab)][i], _nb_kind(col))
                                     if label_index(mlabels, lab) is not None else None)
                               for lab, col in NB_PCT_SERIES.items()}}
               for i, m in enumerate(mp["X_values"])] if mp else []
    kp = d["Market KPI"]["Category"][category]
    klabels = d["Market KPI"]["Labels"]
    kpi = [{"month": m, **{col: (_r(kp["Y_values"][label_index(klabels, lab)][i], "count")
                                 if label_index(klabels, lab) is not None else None)
                           for lab, col in NB_KPI_SERIES.items()}}
           for i, m in enumerate(kp["X_values"])]
    return _nb_facts_from_tables(meta, daily, monthly, kpi)


def neighborhood_facts_reduced(text: str) -> dict:
    """Parse: '# ' header lines, then '## daily', '## monthly', '## kpi' CSV blocks."""
    meta, blocks, cur = {}, {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip(); blocks[cur] = []
        elif line.startswith("# "):
            for kv in line[2:].split():
                if "=" in kv:
                    k, v = kv.split("=", 1); meta[k] = v
        elif line.strip() and cur:
            blocks[cur].append(line)
    def table(name, required):
        rows = list(csv.DictReader(io.StringIO("\n".join(blocks.get(name, [])))))
        if rows and any(c not in rows[0] for c in required):
            raise ValueError(f"{name} block missing columns: {[c for c in required if c not in rows[0]]}")
        return rows
    daily = table("daily", NB_DAILY_COLUMNS)
    if not daily:
        raise ValueError("no daily block in reduced output")
    def num(rows, cols_kinds):
        for r in rows:
            for c, k in cols_kinds:
                r[c] = _r(r[c], k) if r.get(c, "") != "" else None
    num(daily, [(c, _nb_kind(c)) for c in NB_DAILY_COLUMNS if c != "date"])
    monthly = table("monthly", NB_MONTHLY_COLUMNS)
    num(monthly, [(c, _nb_kind(c)) for c in NB_MONTHLY_COLUMNS if c != "month"])
    kpi = table("kpi", NB_KPI_COLUMNS)
    num(kpi, [(c, "count") for c in NB_KPI_COLUMNS if c != "month"])
    m = {"category": meta.get("category"), "currency": meta.get("currency"),
         "listings_used": meta.get("listings_used"),
         "base_p25": meta.get("base_p25"), "base_p50": meta.get("base_p50"),
         "base_p75": meta.get("base_p75"), "base_p90": meta.get("base_p90")}
    return _nb_facts_from_tables(m, daily, monthly, kpi)


# ---------------------------------------------------------------- PMS calendar vs PriceLabs

CALENDAR_FACTS = [
    "pms_days", "pms_min_mode", "pms_reserved", "pl_booked", "invisible_count", "invisible_digest",
    "owner_stay_count", "paired_dates", "markup_median", "markup_stdev",
    "min_stay_mismatch", "drift_count", "drift_digest",
    "blocked_runs", "blocked_nights", "blocked_digest", "gap_runs", "gap_digest",
]
CAL_BLOCKED_COLUMNS = ["start", "end", "nights", "source", "note"]
CAL_GAP_COLUMNS = ["start", "end", "nights"]
CAL_INVISIBLE_COLUMNS = ["date", "pms_price", "note"]
CAL_DRIFT_COLUMNS = ["date", "pms_status", "pms_price", "pl_price", "ratio", "pms_min", "pl_min", "why"]
DRIFT_TOLERANCE = 0.05  # ratio further than this from the median is a drift row
SENTINELS = {"-1", "-2", "-1.0", "-2.0"}  # PriceLabs "no value" markers


def _cal_is_booked(status) -> bool:
    return str(status or "").strip().lower().startswith("booked")


def calendar_rows(pms_days: list[dict], pl_rows: dict) -> dict:
    """Everything the calendar block prints, computed from the raw pair. Shared by the
    gate (to print) and the harness's FULL side (to check). The REDUCED side re-derives
    the same facts by parsing the printed block; it never calls this."""
    reserved = [d for d in pms_days if pms_status(d) == "RESERVED"]
    invisible = [d for d in reserved if d["date"] in pl_rows
                 and pricelabs_status(pl_rows[d["date"]]) == "AVAILABLE"]
    owner = [d for d in reserved if "owner" in str(d.get("note") or "").lower()]
    # markup is measured on nights that are for sale in both systems; a booked night's
    # calendar price is whatever it sold at, not a live ask
    pairs = []
    for d in pms_days:
        row = pl_rows.get(d["date"])
        if pms_status(d) != "AVAILABLE" or pricelabs_status(row) != "AVAILABLE":
            continue
        pms_p = (d.get("price") or {}).get("amount")
        pl_p = row.get("price")
        try:
            pms_p, pl_p = float(pms_p) / 100.0, float(pl_p)
        except (TypeError, ValueError):
            continue
        if pl_p <= 0 or pms_p <= 0:
            continue
        pairs.append((d, row, pms_p, pl_p, pms_p / pl_p))
    ratios = [p[4] for p in pairs]
    med = _r(statistics.median(ratios), "ratio") if ratios else None
    sd = _r(statistics.pstdev(ratios), "ratio") if len(ratios) > 1 else (0.0 if ratios else None)
    drift, mism = [], 0
    for d, row, pms_p, pl_p, ratio in pairs:
        why = []
        if med is not None and abs(ratio - med) > DRIFT_TOLERANCE:
            why.append("price")
        pms_min, pl_min = d.get("min_stay"), row.get("min_stay")
        # -1 / -2 are PriceLabs sentinels for "no value", never a real min-stay; comparing
        # against them flagged every date of a listing PriceLabs had no min-stay for.
        if (pms_min not in (None, "") and pl_min not in (None, "")
                and str(pl_min) not in SENTINELS and int(pms_min) != int(pl_min)):
            why.append("min_stay"); mism += 1
        if why:
            drift.append({"date": d["date"], "pms_status": "AVAILABLE", "pms_price": _r(pms_p, "price"),
                          "pl_price": _r(pl_p, "price"), "ratio": _r(ratio, "ratio"),
                          "pms_min": pms_min, "pl_min": pl_min, "why": "+".join(why)})
    from collections import Counter
    mins = Counter(int(d["min_stay"]) for d in pms_days if d.get("min_stay") not in (None, ""))
    # Blocked runs: host/user blocks are not for sale and not revenue. The raw calendar shows
    # them; the printed block did not, and a live eval saw the full side call a block an
    # "owner stay" the reduced side could not see. Gaps: single or double available nights
    # boxed in by non-available nights (the framework's orphan-day logic needs them named).
    by_date = sorted(pms_days, key=lambda d: str(d["date"]))
    blocked_runs, gaps = [], []
    from datetime import date as _d, timedelta as _td
    for d in by_date:
        st = d.get("status") or {}
        if pms_status(d) != "BLOCKED":
            continue
        src = str(st.get("source_type") or st.get("source") or "")
        note = (d.get("note") or "").replace("\n", " ")[:40]
        if blocked_runs and blocked_runs[-1]["source"] == src and blocked_runs[-1]["note"] == note \
                and _d.fromisoformat(str(blocked_runs[-1]["end"])[:10]) + _td(days=1) == _d.fromisoformat(str(d["date"])[:10]):
            blocked_runs[-1]["end"], blocked_runs[-1]["nights"] = d["date"], blocked_runs[-1]["nights"] + 1
        else:
            blocked_runs.append({"start": d["date"], "end": d["date"], "nights": 1, "source": src, "note": note})
    avail = [pms_status(d) == "AVAILABLE" for d in by_date]
    i = 0
    while i < len(by_date):
        if avail[i]:
            j = i
            while j + 1 < len(by_date) and avail[j + 1]:
                j += 1
            run_len = j - i + 1
            boxed = i > 0 and j < len(by_date) - 1 and not avail[i - 1] and not avail[j + 1]
            if boxed and run_len <= 2:
                gaps.append({"start": by_date[i]["date"], "end": by_date[j]["date"], "nights": run_len})
            i = j + 1
        else:
            i += 1
    return {
        "pms_min_mode": mins.most_common(1)[0][0] if mins else None,
        "pms_days": len(pms_days), "pms_reserved": len(reserved), "invisible": invisible,
        "pl_booked": sum(1 for r in pl_rows.values() if _cal_is_booked(r.get("booking_status"))),
        "owner_stay_count": len(owner), "paired_dates": len(pairs),
        "markup_median": med, "markup_stdev": sd, "min_stay_mismatch": mism, "drift": drift,
        "blocked_runs": blocked_runs, "gaps": gaps,
    }


def _cal_facts_from(c: dict) -> dict:
    return {
        "pms_days": c["pms_days"], "pms_min_mode": c["pms_min_mode"],
        "pms_reserved": c["pms_reserved"], "pl_booked": c["pl_booked"],
        "invisible_count": len(c["invisible"]),
        "invisible_digest": _digest((d["date"], _r((((d.get("price") or {}).get("amount")) or 0) / 100.0, "price")) for d in c["invisible"]),
        "owner_stay_count": c["owner_stay_count"], "paired_dates": c["paired_dates"],
        "markup_median": c["markup_median"], "markup_stdev": c["markup_stdev"],
        "min_stay_mismatch": c["min_stay_mismatch"], "drift_count": len(c["drift"]),
        "blocked_runs": len(c.get("blocked_runs", [])),
        "blocked_nights": sum(int(b["nights"]) for b in c.get("blocked_runs", [])),
        "blocked_digest": _digest((f"{b['start']}..{b['end']}", f"{b['source']}|{b['note']}") for b in c.get("blocked_runs", [])),
        "gap_runs": len(c.get("gaps", [])),
        "gap_digest": _digest((f"{g['start']}..{g['end']}", g["nights"]) for g in c.get("gaps", [])),
        "drift_digest": _digest((d["date"], f"{d['ratio']}/{d['why']}") for d in c["drift"]),
    }


def calendar_facts_full(raw: dict) -> dict:
    """raw = the gate's cached bundle: {"pms_days": [...], "pl_rows": {date: row}}"""
    return _cal_facts_from(calendar_rows(raw["pms_days"], raw["pl_rows"]))


def calendar_facts_reduced(text: str) -> dict:
    meta, blocks, cur = {}, {}, None
    for line in text.splitlines():
        if line.startswith("### "):
            cur = line[4:].strip(); blocks[cur] = []
        elif line.startswith("## calendar"):
            for kv in line.split()[2:]:
                if "=" in kv:
                    k, v = kv.split("=", 1); meta[k] = v
        elif line.startswith("#"):
            continue
        elif not line.strip():
            cur = None  # a blank line ends the block; whatever follows is not CSV
        elif cur:
            blocks[cur].append(line)
    inv = list(csv.DictReader(io.StringIO("\n".join(blocks.get("invisible", [])))))
    dr = list(csv.DictReader(io.StringIO("\n".join(blocks.get("drift", [])))))
    bl = list(csv.DictReader(io.StringIO("\n".join(blocks.get("blocked", [])))))
    gp = list(csv.DictReader(io.StringIO("\n".join(blocks.get("gaps", [])))))
    def num(v, kind):
        return _r(v, kind) if v not in (None, "", "none") else None
    return {
        "pms_days": int(meta["pms_days"]),
        "pms_min_mode": int(meta["pms_min_mode"]) if meta.get("pms_min_mode", "none") != "none" else None,
        "pms_reserved": int(meta["pms_reserved"]),
        "pl_booked": int(meta["pl_booked"]), "invisible_count": len(inv),
        "invisible_digest": _digest((r["date"], _r(r["pms_price"], "price")) for r in inv),
        "owner_stay_count": int(meta["owner_stays"]), "paired_dates": int(meta["paired"]),
        "markup_median": num(meta.get("markup_median"), "ratio"),
        "markup_stdev": num(meta.get("markup_stdev"), "ratio"),
        "min_stay_mismatch": int(meta["min_stay_mismatch"]), "drift_count": len(dr),
        "drift_digest": _digest((r["date"], f"{_r(r['ratio'], 'ratio')}/{r['why']}") for r in dr),
        "blocked_runs": len(bl), "blocked_nights": sum(int(r["nights"]) for r in bl),
        "blocked_digest": _digest((f"{r['start']}..{r['end']}", f"{r['source']}|{r['note']}") for r in bl),
        "gap_runs": len(gp), "gap_digest": _digest((f"{r['start']}..{r['end']}", int(r["nights"])) for r in gp),
    }


# ---------------------------------------------------------------- PriceLabs reservations

RESERVATION_FACTS = [
    "bookings", "cancelled", "blocked", "nights", "revenue", "adr", "currency", "months",
    "los_digest", "lead_digest", "channel_digest", "monthly_digest", "recent_count",
]
RES_MONTHLY_COLUMNS = ["month", "bookings", "nights", "revenue", "adr", "avg_lead", "avg_los", "cancelled",
                       "airbnb", "vrbo", "bcom", "manual", "other"]
RES_RECENT_COLUMNS = ["booked", "check_in", "lead_days", "nights", "adr", "channel", "status"]
LOS_BUCKETS = [("n1", 1, 1), ("n2", 2, 2), ("n3", 3, 3), ("n4_6", 4, 6), ("n7p", 7, 10**6)]
LEAD_BUCKETS = [("d0_7", 0, 7), ("d8_14", 8, 14), ("d15_30", 15, 30), ("d31_60", 31, 60), ("d61p", 61, 10**6)]
CHANNELS = ["airbnb", "vrbo", "bcom", "manual"]
RECENT_DAYS = 14


def reservation_rows(raw_rows: list[dict], today: str) -> list[dict]:
    """Normalise one PriceLabs reservation_data row per booking. Drops PII (guestName).
    Shared by the reducer (to print) and the harness FULL side (to check)."""
    from datetime import date as _date
    out = []
    for r in raw_rows:
        try:
            ci = _date.fromisoformat(str(r.get("check_in"))[:10])
            bd = _date.fromisoformat(str(r["booked_date"])[:10]) if r.get("booked_date") else None
        except ValueError as exc:
            raise ValueError("reservation row has an unreadable check_in or booked_date; "
                             "refusing to omit it from revenue and booking totals") from exc
        nights = int(r.get("no_of_days") or 0)
        rev = float(r.get("rental_revenue") or 0)
        status = str(r.get("booking_status") or "").lower()
        ch = str(r.get("booking_channel") or "other").lower()
        out.append({
            "check_in": ci.isoformat(), "month": ci.isoformat()[:7],
            "booked": bd.isoformat() if bd else "", "lead_days": (ci - bd).days if bd else None,
            "nights": nights, "revenue": rev, "adr": _r(rev / nights, "adr") if nights else None,
            "channel": ch if ch in CHANNELS else "other",
            "status": status,
            "cancelled": status == "cancelled" or bool(r.get("cancelled_on")),
            # A BLOCKED record is an owner stay or a manual hold, not a sold night.
            # Counting it as live inflated bookings, nights and revenue -- measured:
            # one blocked 3-night record turned 1 booking / 2 nights / 400 into
            # 2 / 5 / 1000. The local API reference explicitly permits these records.
            "blocked": status in ("blocked", "block", "owner_stay", "owner stay"),
            "currency": r.get("currency"),
            # 0 <= delta: a booked_date after "today" is a data anomaly, never "recent"
            "recent": bool(bd) and 0 <= (_date.fromisoformat(today) - bd).days <= RECENT_DAYS,
        })
    return out


def _share(items, buckets, key):
    n = len(items)
    out = {}
    for name, lo, hi in buckets:
        c = sum(1 for i in items if i.get(key) is not None and lo <= i[key] <= hi)
        out[name] = _r(100.0 * c / n, "pct") if n else None
    return out


def reservation_tables(rows: list[dict]) -> dict:
    live = [r for r in rows if not r["cancelled"] and not r.get("blocked")]
    nights = sum(r["nights"] for r in live); rev = sum(r["revenue"] for r in live)
    monthly = {}
    for r in rows:
        m = monthly.setdefault(r["month"], {"month": r["month"], "bookings": 0, "nights": 0, "revenue": 0.0,
                                             "leads": [], "loss": [], "cancelled": 0, **{c: 0 for c in CHANNELS}, "other": 0})
        if r["cancelled"]:
            m["cancelled"] += 1
            continue
        if r.get("blocked"):
            continue
        m["bookings"] += 1; m["nights"] += r["nights"]; m["revenue"] += r["revenue"]
        if r["lead_days"] is not None: m["leads"].append(r["lead_days"])
        m["loss"].append(r["nights"]); m[r["channel"]] += 1
    monthly_rows = []
    for m in sorted(monthly.values(), key=lambda x: x["month"]):
        monthly_rows.append({
            "month": m["month"], "bookings": m["bookings"], "nights": m["nights"],
            "revenue": _r(m["revenue"], "revenue"),
            "adr": _r(m["revenue"] / m["nights"], "adr") if m["nights"] else None,
            "avg_lead": _r(sum(m["leads"]) / len(m["leads"]), "days") if m["leads"] else None,
            "avg_los": _r(sum(m["loss"]) / len(m["loss"]), "days") if m["loss"] else None,
            "cancelled": m["cancelled"], **{c: m[c] for c in CHANNELS}, "other": m["other"],
        })
    currencies = sorted({r["currency"] for r in rows if r.get("currency")})
    return {
        "bookings": len(live),
        "cancelled": sum(1 for r in rows if r["cancelled"]),
        "blocked": sum(1 for r in rows if r.get("blocked") and not r["cancelled"]),
        "nights": nights,
        "revenue": _r(rev, "revenue"), "adr": _r(rev / nights, "adr") if nights else None,
        "currency": currencies[0] if len(currencies) == 1 else ("MIXED:" + ",".join(currencies) if currencies else None),
        "los": _share(live, LOS_BUCKETS, "nights"), "lead": _share(live, LEAD_BUCKETS, "lead_days"),
        "channels": {c: sum(1 for r in live if r["channel"] == c) for c in CHANNELS + ["other"]},
        "monthly": monthly_rows,
        "recent": sorted([r for r in live if r["recent"]], key=lambda r: (r["booked"], r.get("check_in"))),
    }


def _res_facts_from(t: dict) -> dict:
    return {
        "bookings": t["bookings"], "cancelled": t["cancelled"],
        "blocked": t.get("blocked", 0), "nights": t["nights"],
        "revenue": t["revenue"], "adr": t["adr"], "currency": t["currency"], "months": len(t["monthly"]),
        "los_digest": _digest(sorted(t["los"].items())), "lead_digest": _digest(sorted(t["lead"].items())),
        "channel_digest": _digest(sorted(t["channels"].items())),
        "monthly_digest": _digest((m["month"], f"{m['nights']}/{m['adr']}/{m['bookings']}") for m in t["monthly"]),
        "recent_count": len(t["recent"]),
    }


def reservation_facts_full(raw: dict, today: str) -> dict:
    rows = raw.get("data") if isinstance(raw, dict) else raw
    return _res_facts_from(reservation_tables(reservation_rows(rows, today)))


def reservation_facts_reduced(text: str) -> dict:
    meta, blocks, cur = {}, {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip().split()[0]; blocks[cur] = []
        elif line.startswith("# "):
            for kv in line[2:].split():
                if "=" in kv:
                    k, v = kv.split("=", 1); meta[k] = v
        elif not line.strip():
            cur = None
        elif cur:
            blocks[cur].append(line)
    monthly = list(csv.DictReader(io.StringIO("\n".join(blocks.get("monthly", [])))))
    recent = list(csv.DictReader(io.StringIO("\n".join(blocks.get("recent", [])))))
    los = {k: _r(meta[k], "pct") for k, _, _ in LOS_BUCKETS if meta.get(k, "none") != "none"}
    for k, _, _ in LOS_BUCKETS: los.setdefault(k, None)
    lead = {k: _r(meta[k], "pct") for k, _, _ in LEAD_BUCKETS if meta.get(k, "none") != "none"}
    for k, _, _ in LEAD_BUCKETS: lead.setdefault(k, None)
    channels = {}
    for kv in meta.get("channels", "").split(","):
        if ":" in kv:
            c, n = kv.split(":"); channels[c] = int(n)
    for c in CHANNELS + ["other"]: channels.setdefault(c, 0)
    return {
        "bookings": int(meta["bookings"]), "cancelled": int(meta["cancelled"]),
        "blocked": int(meta.get("blocked", 0)), "nights": int(meta["nights"]),
        "revenue": _r(meta["revenue"], "revenue"),
        "adr": _r(meta["adr"], "adr") if meta.get("adr", "none") != "none" else None,
        "currency": meta.get("currency") if meta.get("currency") != "none" else None, "months": len(monthly),
        "los_digest": _digest(sorted(los.items())), "lead_digest": _digest(sorted(lead.items())),
        "channel_digest": _digest(sorted(channels.items())),
        "monthly_digest": _digest((m["month"], f"{int(m['nights'])}/{_r(m['adr'], 'adr') if m['adr'] else None}/{int(m['bookings'])}") for m in monthly),
        "recent_count": len(recent),
    }


# ----------------------------------------------------------------------------- overrides
# PriceLabs returns every override as one row per date (a busy listing carries a few hundred).
# The decision-relevant shape is the RUN: consecutive dates with the same price, price type,
# min-stay and reason. The reducer prints runs; the harness re-derives every run from the raw
# rows and from the printed table and compares them one by one through a digest.
OVERRIDE_FACTS = ["dates_total", "runs", "first_date", "last_date", "percent_dates", "fixed_dates",
                  "min_stay_dates", "run_digest"]
OVERRIDE_COLUMNS = ["start", "end", "nights", "price", "price_type", "min_stay", "reason"]


def _ov_price(v, ptype: str):
    if v in (None, ""):
        return ""
    return _r(v, "pct" if ptype == "percent" else "price")


def override_runs(rows: list[dict], today: str | None = None) -> list[dict]:
    """Collapse per-date rows into runs. A run breaks on any change of value or on a date gap.
    Rows before `today` are history, not active overrides, and are dropped."""
    from datetime import date as _d, timedelta as _td
    keyed = sorted((r for r in rows if r.get("date") and (today is None or str(r["date"]) >= today)),
                   key=lambda r: str(r["date"]))
    runs: list[dict] = []
    for r in keyed:
        ptype = r.get("price_type") or ""
        sig = (_ov_price(r.get("price"), ptype), ptype,
               _r(r.get("min_stay"), "min_nights") if r.get("min_stay") not in (None, "") else "",
               (r.get("reason") or "").strip())
        d = _d.fromisoformat(str(r["date"])[:10])
        if runs and runs[-1]["sig"] == sig and _d.fromisoformat(str(runs[-1]["end"])[:10]) + _td(days=1) == d:
            runs[-1]["end"], runs[-1]["nights"] = r["date"], runs[-1]["nights"] + 1
        else:
            runs.append({"sig": sig, "start": r["date"], "end": r["date"], "nights": 1})
    for run in runs:
        run["price"], run["price_type"], run["min_stay"], run["reason"] = run.pop("sig")
    return runs


def override_facts_from_runs(runs: list[dict]) -> dict:
    return {
        "dates_total": sum(int(r["nights"]) for r in runs),
        "runs": len(runs),
        "first_date": runs[0]["start"] if runs else None,
        "last_date": runs[-1]["end"] if runs else None,
        "percent_dates": sum(int(r["nights"]) for r in runs if r["price_type"] == "percent"),
        "fixed_dates": sum(int(r["nights"]) for r in runs if r["price_type"] == "fixed"),
        "min_stay_dates": sum(int(r["nights"]) for r in runs if r["min_stay"] != ""),
        "run_digest": _digest((f"{r['start']}..{r['end']}", f"{r['price']}|{r['price_type']}|{r['min_stay']}|{r['reason']}")
                              for r in runs),
    }


def override_facts_full(raw: dict, today: str) -> dict:
    rows = raw.get("overrides", raw.get("data", raw)) if isinstance(raw, dict) else raw
    if isinstance(rows, dict):
        rows = rows.get("overrides", [])
    return override_facts_from_runs(override_runs(rows, today))


def override_facts_reduced(text: str) -> dict:
    blocks, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip(); blocks[cur] = []
        elif line.startswith("# "):
            continue
        elif line.strip() and cur:
            blocks[cur].append(line)
    rows = list(csv.DictReader(io.StringIO("\n".join(blocks.get("runs", [])))))
    runs = []
    for r in rows:
        ptype = r["price_type"]
        runs.append({"start": r["start"], "end": r["end"], "nights": int(r["nights"]),
                     "price": _ov_price(r["price"], ptype) if r["price"] != "" else "",
                     "price_type": ptype,
                     "min_stay": _r(r["min_stay"], "min_nights") if r["min_stay"] != "" else "",
                     "reason": r["reason"].strip()})
    return override_facts_from_runs(runs)


# --------------------------------------------------------------- customizations
CUSTOMIZATION_FACTS = ["rules_total", "rules_on", "rules_off", "rules_digest",
                       "dow_abs_total", "dow_digest", "stored_seasons",
                       "rule_sig_digest"]

# _CZ_TOGGLE_KEY and _CZ_DOW_KEYS deliberately duplicate reduce_customizations.TOGGLE_KEY
# and attribution.DOW_KEYS instead of importing them. This is NOT a DRY violation: a
# fact-class extractor that imports the reducer's own constants would recompute "on" or
# "day-of-week magnitude" using the very mapping that might have just changed underneath
# it, so a bug that renames or reorders a key would sail through undetected. Keeping a
# second, independently-typed copy here is what makes this a real check instead of the
# reducer checking itself. If reduce_customizations.py ever changes these, this file must
# be updated by hand -- that friction is the point.
_CZ_TOGGLE_KEY = {
    "seasonality": "seasonality_customization_on",
    "last_minute_prices": "last_min_factor_on",
    "far_out_premium": "far_out_premium_on",
    "day_of_week_adjustment": "dow_factor_on",
    "demand_factor": "tone_demand_factor_on",
    "custom_seasonal_profile": "custom_seasonal_profile_on",
}
_CZ_DOW_KEYS = ["dow_factor_value_mon", "dow_factor_value_tue", "dow_factor_value_wed",
                "dow_factor_value_thu", "dow_factor_value_fri", "dow_factor_value_sat",
                "dow_factor_value_sun"]


def customization_facts_full(raw: dict) -> dict:
    """Derive the facts straight from the API payload, not from the reducer.

    rules_digest and dow_digest exist because rules_on/off and dow_abs_total are a count
    and a sum, and both are invariant under permutation: swap which two rules are off, or
    which day carries which value, and the count/sum does not move. A digest over the
    actual (rule, state) / (day, value) pairs is the only thing that can see a
    mislabeling -- and in this domain, a discount rendered as a premium on the wrong day
    is the worst failure there is.
    """
    rules = raw.get("customizations") or {}
    rule_pairs = sorted((name, "on" if _cz_on((cfg or {}).get(_CZ_TOGGLE_KEY.get(name, "")))
                         else "off")
                        for name, cfg in rules.items())
    on = sum(1 for _, state in rule_pairs if state == "on")
    dow = rules.get("day_of_week_adjustment") or {}
    # Round through PRECISION's "pct" bucket here, per value, not just on the final sum:
    # the reducer prints each day's value with :g (6 significant digits), so comparing a
    # raw full-precision float against that truncation is a false mismatch waiting to
    # happen (see PRECISION's own comment: "Reducers round to these; extractors compare at
    # these"). Rounding every value the same way before it feeds either the sum or the
    # digest is what makes both dow_abs_total and dow_digest agree on an unchanged value.
    # No day-of-week rule in the payload means NO pairs, not seven zeros. The reduced
    # side has no row to parse and builds an empty list; filling seven (day, 0.0) pairs
    # here made the two digests differ on a listing that simply has no dow rule, which
    # reads as a reducer bug and is not one.
    dow_pairs = ([(k[-3:], _r(dow.get(k) or 0, "pct")) for k in _CZ_DOW_KEYS]
                 if dow else [])
    dow_abs = sum(abs(v) for _, v in dow_pairs)
    profile = (rules.get("custom_seasonal_profile") or {}).get("custom_seasonal_profile") or {}
    seasons = len(profile.get("seasons") or []) + len(profile.get("non_repeating_seasons") or [])
    return {"rules_total": len(rules), "rules_on": on, "rules_off": len(rules) - on,
            "rules_digest": _digest(rule_pairs),
            "dow_abs_total": dow_abs, "dow_digest": _digest(dow_pairs),
            "stored_seasons": seasons,
            "rule_sig_digest": _digest(_cz_sig_full(rules))}


def _cz_on(raw) -> bool:
    """A toggle may come back as a bool or as the STRING "false", and `bool("false")` is
    True. The reducer already reads it this way; the full side has to match or every
    string-toggled rule is a false mismatch."""
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    return bool(raw)


_CZ_TYPE_KEY = {
    "seasonality": "seasonality_type",
    "last_minute_prices": "last_min_factor_type",
    "far_out_premium": "far_out_premium_type",
    "demand_factor": "tone_demand_factor",
}
_CZ_VALUE_KEY = {
    "last_minute_prices": "last_min_factor_value",
    "far_out_premium": "far_out_premium_value",
}
_CZ_WINDOW_KEY = {
    "last_minute_prices": "last_min_factor_dfd",
    "far_out_premium": "far_out_premium_start",
}


def _cz_sig_full(rules: dict) -> list:
    """(rule, type, SIGNED value, window threshold) straight off the raw config.

    Without this the fact set carried only counts, toggles and the day-of-week digest,
    so flipping last_min_factor_value from -20 to +20 -- a 20% discount becoming a 20%
    premium on a live guest-facing calendar -- left every fact matching. A window moving
    from 7 days to 90 was invisible for the same reason.

    Derived here from the raw payload and, on the reduced side, re-derived by PARSING
    THE PRINTED TABLE. Two independent derivations; if they ever agree by sharing code
    the check proves nothing.
    """
    out = []
    for rule in sorted(rules):
        cfg = rules.get(rule) or {}
        if not isinstance(cfg, dict):
            out.append((rule, "?|None|None"))
            continue
        kind = cfg.get(_CZ_TYPE_KEY.get(rule, ""), "") or "-"
        vkey = _CZ_VALUE_KEY.get(rule)
        value = _cz_num(cfg.get(vkey)) if vkey else None
        wkey = _CZ_WINDOW_KEY.get(rule)
        window = _cz_num(cfg.get(wkey)) if wkey else None
        if rule == "custom_seasonal_profile":
            prof = cfg.get("custom_seasonal_profile") or {}
            window = float(len(prof.get("seasons") or [])
                           + len(prof.get("non_repeating_seasons") or []))
        out.append((rule, f"{kind}|{value}|{window}"))
    return out


def _cz_num(value):
    """A config number. -1 and -2 are REAL percentages here, never sentinels."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cz_sig_reduced(parsed: list[dict]) -> list:
    """The same signature, re-derived by parsing the reducer's printed columns."""
    out = []
    for row in sorted(parsed, key=lambda r: r["rule"]):
        rule = row["rule"]
        kind = row.get("type") or "-"
        value = _cz_num(row.get("value")) if rule in _CZ_VALUE_KEY else None
        window = None
        w = (row.get("window") or "").strip()
        if rule in _CZ_WINDOW_KEY:
            m = re.match(r"^[<>]=(-?\d+(?:\.\d+)?)d$", w)
            window = float(m.group(1)) if m else None
        elif rule == "custom_seasonal_profile":
            m = re.match(r"^(\d+) seasons?$", w)
            window = float(m.group(1)) if m else 0.0
        out.append((rule, f"{kind}|{value}|{window}"))
    return out


def customization_facts_reduced(text: str) -> dict:
    """Re-derive the same facts by parsing the reducer's printed table."""
    rows = []
    in_rules = False
    for line in text.splitlines():
        if line.startswith("## rules"):
            in_rules = True
            continue
        if line.startswith("## "):
            in_rules = False
            continue
        if in_rules and line.strip():
            rows.append(line)
    if not rows:
        return {n: None for n in CUSTOMIZATION_FACTS}
    parsed = list(csv.DictReader(io.StringIO("\n".join(rows))))
    rule_pairs = sorted((r["rule"], "on" if r["toggle"] == "on" else "off") for r in parsed)
    on = sum(1 for _, state in rule_pairs if state == "on")
    dow_pairs = []
    seasons = 0
    for r in parsed:
        if r["rule"] == "day_of_week_adjustment":
            for pair in r["value"].split():
                day, val = pair.split("=")
                dow_pairs.append((day, _r(val, "pct")))
        if r["rule"] == "custom_seasonal_profile":
            match = re.match(r"(\d+) seasons", r["window"])
            seasons = int(match.group(1)) if match else 0
    dow_abs = sum(abs(v) for _, v in dow_pairs)
    return {"rules_total": len(parsed), "rules_on": on, "rules_off": len(parsed) - on,
            "rules_digest": _digest(rule_pairs),
            "dow_abs_total": dow_abs, "dow_digest": _digest(dow_pairs),
            "stored_seasons": seasons,
            "rule_sig_digest": _digest(_cz_sig_reduced(parsed))}


SOURCES = {
    "airroi": (AIRROI_FACTS, airroi_facts_full, airroi_facts_reduced),
    "neighborhood": (NEIGHBORHOOD_FACTS, neighborhood_facts_full, neighborhood_facts_reduced),
    "calendar": (CALENDAR_FACTS, calendar_facts_full, calendar_facts_reduced),
    "reservations": (RESERVATION_FACTS, reservation_facts_full, reservation_facts_reduced),
    "overrides": (OVERRIDE_FACTS, override_facts_full, override_facts_reduced),
    "customizations": (CUSTOMIZATION_FACTS, customization_facts_full, customization_facts_reduced),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=sorted(SOURCES))
    ap.add_argument("--full", required=True, help="raw API payload (JSON)")
    ap.add_argument("--reduced", required=True, help="reducer output (text)")
    ap.add_argument("--subject-id", default=None)
    ap.add_argument("--category", default=None, help="neighborhood: bedroom category, e.g. 4")
    ap.add_argument("--start", default=None, help="neighborhood: drop dates before this (the reducer's window_start)")
    ap.add_argument("--days", type=int, default=None, help="neighborhood: forward window the reducer used")
    ap.add_argument("--today", default=None, help="reservations: the 'today' the reducer used (YYYY-MM-DD)")
    args = ap.parse_args()

    names, f_full, f_red = SOURCES[args.source]
    try:
        with open(args.full, encoding="utf-8") as fh:
            raw_text = fh.read()
        # Parse the whole document. Cutting from the first "{" could not read a
        # top-level JSON ARRAY at all (it left a dangling "]"), which made the list
        # branches in reservation_facts_full and override_facts_full unreachable from
        # the CLI -- and an empty array failed outright. Fall back to raw_decode only
        # when the file genuinely has a prefix before the JSON.
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            starts = [i for i in (raw_text.find("{"), raw_text.find("[")) if i >= 0]
            if not starts:
                raise
            raw, _end = json.JSONDecoder().raw_decode(raw_text[min(starts):])
        if args.source == "airroi":
            full = f_full(raw, args.subject_id)
        elif args.source == "neighborhood":
            if not args.category:
                raise ValueError("--category is required for neighborhood")
            full = f_full(raw, args.category, args.days, args.start)
        elif args.source in ("reservations", "overrides"):
            if not args.today:
                raise ValueError("--today is required for reservations and overrides")
            full = f_full(raw, args.today)
        else:
            full = f_full(raw)
        reduced = f_red(open(args.reduced, encoding="utf-8").read())
    except Exception as e:  # noqa: BLE001
        print(f"COULD NOT CHECK: {e}", file=sys.stderr)
        return 2

    bad = compare(full, reduced, names)
    for n in names:
        mark = "ok  " if n not in [b.split(":")[0] for b in bad] else "FAIL"
        print(f"  {mark}  {n:22s} {full.get(n)!r}")
    if bad:
        print(f"\n{len(bad)} fact class(es) changed by the reducer:")
        for b in bad:
            print("  -", b)
        return 1
    print(f"\nall {len(names)} fact classes preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
