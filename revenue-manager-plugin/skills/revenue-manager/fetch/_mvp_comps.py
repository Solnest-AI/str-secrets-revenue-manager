"""Optional, cached AirROI context with native currency and comparable-set checks."""
from __future__ import annotations

import math
import re
from urllib.parse import urlencode

from _mvp_store import CannotAnalyze
from factcheck import AIRROI_COLUMNS, _facts_from_rows, _r


_COMPACT_COLUMNS = tuple(column for column in AIRROI_COLUMNS if column != "currency")
_BASE = "https://api.airroi.com/listings/comparables"
_PERIOD = "trailing_12_months"


def _number(value, *, maximum=None, integer=False, minimum=0):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(number) or number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    if integer and not number.is_integer():
        return None
    return int(number) if integer else number


def _currency(value):
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value) else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _rounded(value, kind, maximum=None):
    number = _number(value, maximum=maximum)
    return _r(number, kind) if number is not None else None


def _listing_id(value):
    value = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    return value if re.fullmatch(r"\d+", value) else None


def _row(raw, currency):
    listing = _object(raw.get("listing_info"))
    details = _object(raw.get("property_details"))
    performance = _object(raw.get("performance_metrics"))
    ratings = _object(raw.get("ratings"))
    settings = _object(raw.get("booking_settings"))
    listing_id = _listing_id(listing.get("listing_id"))
    if not listing_id:
        raise CannotAnalyze("AirROI comparable has no verifiable Airbnb listing ID")
    name = listing.get("listing_name")
    name = name if isinstance(name, str) else ""
    name = re.sub(r"\s+", " ", name.replace(chr(8212), "-"))[:40].strip()
    return {
        "listing_id": listing_id, "name": name,
        "bedrooms": _number(details.get("bedrooms"), integer=True),
        "baths": _number(details.get("baths")),
        "guests": _number(details.get("guests"), integer=True, minimum=1),
        "ttm_revenue": _rounded(performance.get("ttm_revenue"), "revenue"),
        "ttm_adr": _rounded(performance.get("ttm_avg_rate"), "adr"),
        "ttm_occ": _rounded(performance.get("ttm_occupancy"), "occ", maximum=1),
        "ttm_revpar": _rounded(performance.get("ttm_revpar"), "revpar"),
        "rating": _rounded(ratings.get("rating_overall"), "rating", maximum=5),
        "reviews": _number(ratings.get("num_reviews"), integer=True),
        "currency": currency,
        "min_nights": _rounded(settings.get("min_nights"), "min_nights"),
        "los": _number(performance.get("ttm_avg_length_of_stay")),
    }


def _summary(rows):
    facts = _facts_from_rows(rows, None, None, False)
    # These fields are shared by the whole result, not separate market statistics.
    for field in ("subject_in_set", "subject_rank_revenue", "currency"):
        facts.pop(field)
    return {"period": _PERIOD, **facts,
            "occupancy_unit": "fraction_of_nights",
            "rows_with_ttm_adr": sum(row["ttm_adr"] is not None for row in rows)}


def _normalize(raw, *, bedrooms, baths, guests, currency, subject):
    listings = raw.get("listings") if isinstance(raw, dict) else None
    if not isinstance(listings, list) or not listings:
        raise CannotAnalyze("AirROI returned no readable comparables")
    rows = []
    seen = {}
    duplicates = 0
    # Validate all returned currencies before exclusions, including the subject.
    for item in listings:
        if not isinstance(item, dict):
            raise CannotAnalyze("AirROI comparable rows are unreadable")
        reported = _currency(_object(item.get("pricing_info")).get("currency"))
        if reported is None:
            raise CannotAnalyze("AirROI returned a comparable without a currency")
        if reported != currency:
            raise CannotAnalyze("AirROI returned mixed or unexpected currencies")
        row = _row(item, currency)
        listing_id = row["listing_id"]
        if listing_id in seen:
            if row != seen[listing_id]:
                raise CannotAnalyze("AirROI returned conflicting duplicate listing rows")
            duplicates += 1
            continue
        seen[listing_id] = row
        rows.append(row)
    ranked = sorted(rows, key=lambda row: (-(row["ttm_revenue"] or 0), row["listing_id"]))
    subject_rank = next((index for index, row in enumerate(ranked, 1)
                         if row["listing_id"] == subject), None)
    exclusions = {"subject": 0, "bedrooms_or_baths": 0,
                  "unknown_bedrooms_or_baths": 0, "duplicate_rows": duplicates}
    comps = []
    for row in ranked:
        if row["listing_id"] == subject:
            exclusions["subject"] += 1
        elif row["bedrooms"] is None or row["baths"] is None:
            exclusions["unknown_bedrooms_or_baths"] += 1
        elif row["bedrooms"] != bedrooms or row["baths"] != baths:
            exclusions["bedrooms_or_baths"] += 1
        else:
            comps.append(row)
    if not comps:
        raise CannotAnalyze("AirROI has no other comparables with matching bedrooms and baths")
    capacity = [row for row in comps if row["guests"] is not None and row["guests"] >= guests]
    return {
        "status": "ok", "reason": "native-currency historical comparables validated",
        "source": "airroi", "period": _PERIOD, "currency": currency,
        "use": "Historical market context, not forward asking prices or a revenue forecast",
        "comps": [{key: row[key] for key in _COMPACT_COLUMNS} for row in comps],
        "summary": _summary(comps),
        "capacity_subset": {
            "minimum_guests": guests, "count": len(capacity),
            "unknown_capacity_count": sum(row["guests"] is None for row in comps),
            "smaller_capacity_count": sum(row["guests"] is not None and row["guests"] < guests
                                          for row in comps),
            "listing_ids": [row["listing_id"] for row in capacity],
            "summary": _summary(capacity),
        },
        "exclusions": exclusions, "returned_count": len(listings),
        "currency_rows_checked": len(listings),
        "subject_in_set": subject_rank is not None, "subject_rank_revenue": subject_rank,
        "subject_rank_scope": "returned_set_before_exclusion",
    }


def _parameters(metadata, property_data, subject_airbnb_id):
    metadata, property_data = _object(metadata), _object(property_data)
    capacity = _object(property_data.get("capacity"))
    lat = _number(metadata.get("latitude"), minimum=-90, maximum=90)
    lng = _number(metadata.get("longitude"), minimum=-180, maximum=180)
    bedrooms = _number(capacity.get("bedrooms"), integer=True)
    pl_bedrooms = _number(metadata.get("no_of_bedrooms"), integer=True)
    if bedrooms is None:
        bedrooms = pl_bedrooms
    elif pl_bedrooms is not None and bedrooms != pl_bedrooms:
        raise CannotAnalyze("PMS and pricing-tool bedroom counts disagree for named comparables")
    baths = _number(capacity.get("bathrooms"))
    guests = _number(capacity.get("max"), integer=True, minimum=1)
    currency = _currency(metadata.get("currency"))
    pms_currency = _currency(property_data.get("currency"))
    if not currency or not pms_currency or currency != pms_currency:
        raise CannotAnalyze("Named comparables require matching PMS and pricing-tool currencies")
    subject = _listing_id(subject_airbnb_id)
    if any(value is None for value in (lat, lng, bedrooms, baths, guests)):
        raise CannotAnalyze("Named comparables need location, bedrooms, baths and guest capacity")
    if subject is None:
        raise CannotAnalyze("Named comparables require the subject Airbnb ID for exclusion")
    return lat, lng, bedrooms, baths, guests, currency, subject


def fetch_comps(client, connections, listing_metadata: dict, pms_property: dict,
                subject_airbnb_id: str, refresh=False) -> dict:
    """Fetch one optional named comp set, normalized before private SQLite storage.

    Every actual HTTP request passes through ReadClient. A seven-day cache includes
    account and exact property inputs. All currency values are checked before any
    filtering. Rows have 13 fields; their shared currency is factored out without
    losing LOS or any field from the existing AirROI fact-check contract.

    The broader set matches bedrooms and baths. Its separately reported capacity
    subset contains only known capacities at least as large as the subject. An
    unavailable optional source never blocks the primary pricing gate.
    """
    try:
        key = connections.key("airroi")
        if not key:
            raise CannotAnalyze("Missing AIRROI_API_KEY for optional named comparables")
        lat, lng, bedrooms, baths, guests, currency, subject = _parameters(
            listing_metadata, pms_property, subject_airbnb_id,
        )

        def load():
            params = {"latitude": lat, "longitude": lng, "bedrooms": bedrooms,
                      "baths": baths, "guests": guests, "currency": "native"}
            raw, _ = client.request(
                "airroi", "comparables", _BASE + "?" + urlencode(params),
                headers={"X-API-KEY": key, "Accept": "application/json"},
            )
            return _normalize(raw, bedrooms=bedrooms, baths=baths, guests=guests,
                              currency=currency, subject=subject)

        return client.fetch(
            "airroi.comps", [connections.account("airroi"), lat, lng, bedrooms, baths,
                            guests, currency, subject], load,
            ttl_seconds=0 if refresh else 604800,
        )
    except CannotAnalyze as exc:
        return {"status": "unavailable", "source": "airroi", "period": _PERIOD,
                "reason": str(exc), "optional": True, "comps": []}
