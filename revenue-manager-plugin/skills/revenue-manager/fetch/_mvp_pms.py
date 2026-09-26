"""Private-data allowlists and deterministic PMS facts for a read-only revenue run.

No network, filesystem, or model calls occur here. Money stays in integer cents;
"paid" describes positive reservation accommodation, never verified cash receipts.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import statistics
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from _calendar import validate_calendar


_ONE = timedelta(days=1)
_STATUS_ALIASES = {
    "accepted": "accepted",
    "confirmed": "accepted",
    "request": "request",
    "request to book": "request",
    "pending": "request",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "not accepted": "not accepted",
    "declined": "not accepted",
    "expired": "not accepted",
    "rejected": "not accepted",
    "checkpoint": "checkpoint",
    "checkpoint voided": "checkpoint",
    "inquiry": "inquiry",
    "inquiries": "inquiry",
    "unknown": "unknown",
}
_DETAIL_TYPES = {
    "value",
    "cleanliness",
    "communication",
    "location",
    "checkin",
    "accuracy",
    "facilities",
    "staff",
    "services",
    "comfort",
    "wifi",
    "overall",
}


def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _numeric(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return float(result) if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value, limit=200):
    return value[:limit] if isinstance(value, str) else None


def _boolean(value):
    return value if isinstance(value, bool) else None


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _datesafe(value):
    if not isinstance(value, str) or _date(value) is None:
        return None
    try:
        if len(value) > 10:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif value != _date(value).isoformat():
            return None
    except ValueError:
        return None
    return value


def _moment(value):
    if not isinstance(value, str) or len(value) <= 10:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _currency(value):
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value) else None


def _status(value):
    key = value.lower().replace("_", " ").strip() if isinstance(value, str) else ""
    return _STATUS_ALIASES.get(key, "unknown")


def _object(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _status_tree(value):
    """Retain timestamps and known status words, never arbitrary status text."""
    if isinstance(value, list):
        return [_status_tree(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("current", "history"):
        if key in value:
            result[key] = _status_tree(value[key])
    for key in ("status", "category"):
        if key in value:
            result[key] = _status(value[key])
    if "sub_category" in value:
        sub = str(value["sub_category"] or "").lower().replace("_", " ")
        result["sub_category"] = (
            sub
            if sub in {"request to book", "declined", "expired", "voided", "checkpoint"}
            else None
        )
    for key in ("created_at", "updated_at", "changed_at", "date", "timestamp", "at"):
        if key in value:
            result[key] = _datesafe(value[key])
    return result


def _fees(items):
    if items is not None and not isinstance(items, list):
        return [{"amount_cents": None, "kind": "other", "category": None}]
    result = []
    for item in _list(items):
        item = _object(item)
        label = str(item.get("label") or item.get("kind") or "").lower().replace("_", " ")
        kind = next(
            (
                key
                for key in (
                    "promotion",
                    "weekly",
                    "monthly",
                    "cleaning",
                    "pet",
                    "host service",
                    "guest service",
                )
                if key in label
            ),
            "other",
        )
        category = str(item.get("category") or "").lower()
        result.append(
            {
                "amount_cents": _integer(item.get("amount_cents", item.get("amount"))),
                "kind": kind.replace(" ", "_"),
                "category": category
                if category
                in {"discount", "fee", "tax", "adjustment", "promotion", "cleaning", "pet"}
                else None,
            }
        )
    return result


def normalize_property(raw):
    """Allowlist a property object or a single-object API envelope."""
    if not isinstance(raw, dict):
        raise ValueError("PMS property source must be an object")
    raw = _object(raw.get("data", raw))
    result = {
        key: _text(raw.get(key))
        for key in (
            "id",
            "name",
            "public_name",
            "timezone",
            "property_type",
            "room_type",
            "checkin",
            "checkout",
        )
    }
    result["currency"] = _currency(raw.get("currency"))
    for key in ("listed", "calendar_restricted"):
        result[key] = raw.get(key) if isinstance(raw.get(key), bool) else None
    capacity = _object(raw.get("capacity"))
    result["capacity"] = {
        key: _numeric(capacity.get(key)) for key in ("max", "bedrooms", "beds", "bathrooms")
    }
    address = _object(raw.get("address"))
    result["address"] = {key: _text(address.get(key)) for key in ("city", "country")}
    listings = raw.get("listings")
    if isinstance(listings, dict):
        listings = listings.get("data")
    result["listings"] = [
        {key: _text(item.get(key)) for key in ("platform", "platform_id")}
        for item in _list(listings)
        if isinstance(item, dict)
    ]
    return result


def normalize_calendar(raw):
    """Flatten calendar blocks; ambiguous or duplicate date rows fail closed."""
    if isinstance(raw, dict) and "data" in raw:
        raw = raw["data"]
    blocks = raw if isinstance(raw, list) else [raw]
    result, seen = [], set()
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("PMS calendar contains an unreadable block")
        rows = block.get("days", [block] if "date" in block else None)
        if not isinstance(rows, list):
            raise ValueError("PMS calendar has no day rows")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("PMS calendar contains an unreadable day")
            when = _date(row.get("date"))
            if when is None or row["date"] != when.isoformat() or row["date"] in seen:
                raise ValueError("PMS calendar contains an invalid or duplicate date")
            seen.add(row["date"])
            status, price = _object(row.get("status")), _object(row.get("price"))
            reason = row.get("status_reason", status.get("reason"))
            result.append(
                {
                    "date": when.isoformat(),
                    "price_cents": _integer(row.get("price_cents", price.get("amount"))),
                    "currency": _currency(row.get("currency", price.get("currency"))),
                    "min_stay": _integer(row.get("min_stay")),
                    "available": _boolean(row.get("available", status.get("available"))),
                    "status_reason": reason.upper()
                    if isinstance(reason, str)
                    and reason.upper() in {"AVAILABLE", "RESERVED", "BLOCKED"}
                    else "UNKNOWN",
                    "closed_for_checkin": _boolean(row.get("closed_for_checkin")),
                    "closed_for_checkout": _boolean(row.get("closed_for_checkout")),
                }
            )
    return sorted(result, key=lambda item: item["date"])


def normalize_reservation(raw):
    """Drop guest identities, contacts, notes, messages, and free-form labels."""
    if not isinstance(raw, dict):
        raise ValueError("PMS reservation source must be an object")
    result = {key: _text(raw.get(key)) for key in ("id", "platform")}
    result["status"] = _status(raw.get("status"))
    result["stay_type"] = (
        raw.get("stay_type")
        if raw.get("stay_type") in {"guest_stay", "owner_stay", "maintenance"}
        else None
    )
    result["owner_stay"] = (
        raw.get("owner_stay") if isinstance(raw.get("owner_stay"), bool) else None
    )
    for key in ("booking_date", "arrival_date", "departure_date", "check_in", "check_out"):
        result[key] = _datesafe(raw.get(key))
    result["nights"] = _integer(raw.get("nights"))
    properties = raw.get("properties")
    if isinstance(properties, dict):
        properties = properties.get("data", [properties])
    ids = [item.get("id") for item in _list(properties) if isinstance(item, dict)]
    ids += [*_list(raw.get("property_ids")), raw.get("property_id")]
    result["property_ids"] = sorted({value for value in ids if isinstance(value, str) and value})
    result["reservation_status"] = _status_tree(raw.get("reservation_status"))
    result["status_history"] = _status_tree(raw.get("status_history"))
    financials = _object(raw.get("financials"))
    host, guest = _object(financials.get("host")), _object(financials.get("guest"))
    normalized = "host_accommodation_cents" in financials
    money_fields = {
        "host_accommodation_cents": (host, "accommodation"),
        "host_revenue_cents": (host, "revenue"),
        "guest_accommodation_cents": (guest, "accommodation"),
        "guest_average_nightly_rate_cents": (guest, "average_nightly_rate"),
        "guest_total_cents": (guest, "total_price"),
    }
    clean = {"currency": _currency(financials.get("currency"))}
    for key, (parent, field) in money_fields.items():
        value = financials.get(key) if normalized else _object(parent.get(field)).get("amount")
        clean[key] = _integer(value)
    for key, parent, field in (
        ("host_discounts", host, "discounts"),
        ("host_guest_fees", host, "guest_fees"),
        ("host_fees", host, "host_fees"),
        ("host_adjustments", host, "adjustments"),
        ("guest_discounts", guest, "discounts"),
        ("guest_fees", guest, "fees"),
        ("guest_taxes", guest, "taxes"),
    ):
        clean[key] = _fees(financials.get(key) if normalized else parent.get(field))
    breakdown = (
        financials.get("host_accommodation_breakdown")
        if normalized
        else host.get("accommodation_breakdown")
    )
    clean["host_accommodation_breakdown"] = [
        {
            "date": _date(item.get("date", item.get("label"))).isoformat(),
            "amount_cents": _integer(item.get("amount_cents", item.get("amount"))),
        }
        for item in _list(breakdown)
        if isinstance(item, dict) and _date(item.get("date", item.get("label"))) is not None
    ]
    result["financials"] = clean
    return result


def normalize_review(raw):
    """Keep numeric ratings and dates, never review text or reviewer details."""
    if not isinstance(raw, dict):
        raise ValueError("PMS review source must be an object")
    public = _object(raw.get("public"))
    private = _object(raw.get("private"))
    detail = private.get(
        "detailed_ratings", public.get("detailed_ratings", raw.get("detailed_ratings"))
    )
    return {
        "id": _text(raw.get("id")),
        "platform": _text(raw.get("platform")),
        "reviewed_at": _datesafe(raw.get("reviewed_at")),
        "responded_at": _datesafe(raw.get("responded_at")),
        "rating": _numeric(public.get("rating", raw.get("rating"))),
        "rating_platform_original": _numeric(
            public.get("rating_platform_original", raw.get("rating_platform_original"))
        ),
        "detailed_ratings": [
            {"type": item["type"], "rating": _numeric(item.get("rating"))}
            for item in _list(detail)
            if isinstance(item, dict) and item.get("type") in _DETAIL_TYPES
        ],
    }


def _round(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _mean(values):
    return _round(sum(Decimal(str(value)) for value in values) / len(values)) if values else None


def _pct(numerator, denominator):
    return _round(100 * Decimal(numerator) / denominator) if denominator else None


def _allocation(total, weights):
    """Largest remainders retain exact cents, including signed discounts."""
    denominator = sum(weights)
    bases = [total * weight // denominator for weight in weights]
    order = sorted(
        range(len(weights)), key=lambda i: total * weights[i] % denominator, reverse=True
    )
    for index in order[: total - sum(bases)]:
        bases[index] += 1
    return bases


def _days(start, end):
    return [start + index * _ONE for index in range(max(0, (end - start).days))]


def _year_before(value):
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _tz(value):
    try:
        return ZoneInfo(value), None
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        if isinstance(value, str) and re.fullmatch(r"[+-]\d{2}:?\d{2}", value):
            offset = value.replace(":", "")
            minutes = int(offset[1:3]) * 60 + int(offset[3:5])
            if int(offset[3:5]) < 60 and minutes < 24 * 60:
                sign = -1 if offset[0] == "-" else 1
                return timezone(timedelta(minutes=sign * minutes)), "fixed_offset_timezone"
        return timezone.utc, "missing_property_timezone_using_utc"


def _current_category(record):
    current = _object(_object(record.get("reservation_status")).get("current"))
    return current.get("category") or current.get("status") or record["status"]


def _events(record):
    history = _object(record.get("reservation_status")).get("history")
    if not history:
        history = record.get("status_history")
    result = []
    for event in _list(history):
        event = _object(event)
        stamp = next(
            (
                _moment(event.get(key))
                for key in ("changed_at", "timestamp", "at", "date", "created_at")
                if _moment(event.get(key)) is not None
            ),
            None,
        )
        if stamp is not None:
            result.append((stamp, event.get("category") or event.get("status") or "unknown"))
    return sorted(set(result))


def _calendar_status(row):
    reason = row.get("status_reason")
    if reason not in {"AVAILABLE", "RESERVED", "BLOCKED"}:
        return "UNKNOWN"
    return reason if row.get("available") is (reason == "AVAILABLE") else "UNKNOWN"


def analyze(property_data, calendar_days, reservations, reviews, start, days, as_of):
    """Build source-grounded facts; absent sources raise instead of becoming zero.

    Caller verifies provider pagination and requested query coverage. An explicitly
    empty reservations list is valid; None or an unreadable payload is not.
    """
    if not isinstance(start, date) or isinstance(start, datetime):
        raise ValueError("Analysis start must be a date")
    if _integer(days) is None or not 1 <= days <= 366:
        raise ValueError("Analysis days must be an integer from 1 through 366")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("Analysis as_of must include a timezone")
    if not isinstance(reservations, list):
        raise ValueError("PMS reservation source is missing or unreadable")
    if not isinstance(reviews, list):
        raise ValueError("PMS review source is missing or unreadable")
    prop = normalize_property(property_data)
    if not prop["id"] or not prop["currency"]:
        raise ValueError("PMS property identity and currency are required")
    local_tz, tz_warning = _tz(prop["timezone"])
    cutoff = as_of.astimezone(local_tz).date()
    end = start + days * _ONE
    normalized_days = normalize_calendar(calendar_days)
    rows = [row for row in normalized_days if start.isoformat() <= row["date"] < end.isoformat()]
    validate_calendar(rows, "PMS", _calendar_status, start.isoformat(), (end - _ONE).isoformat())
    warnings = Counter()
    if tz_warning:
        warnings[tz_warning] += 1
    records, seen, rejected_ids = [], {}, set()
    allocation_modes = Counter()
    historical = defaultdict(list)
    inventory = defaultdict(list)
    for original in reservations:
        record = normalize_reservation(original)
        identifier = record["id"]
        if not identifier:
            warnings["reservation_missing_id"] += 1
            continue
        if identifier in seen:
            if record != seen[identifier]:
                rejected_ids.add(identifier)
                warnings["conflicting_duplicate_reservation"] += 1
            else:
                warnings["duplicate_reservation_ignored"] += 1
            continue
        seen[identifier] = dict(record)
        if record["property_ids"] != [prop["id"]]:
            warnings["reservation_property_scope_unverified"] += 1
            continue
        arrival = _date(record["check_in"] or record["arrival_date"])
        departure = _date(record["check_out"] or record["departure_date"])
        if arrival is None or departure is None or departure <= arrival:
            warnings["reservation_stay_dates_invalid"] += 1
            continue
        if (departure - arrival).days > 3660:
            warnings["reservation_stay_dates_excessive"] += 1
            continue
        record.update(
            {
                "_start": arrival,
                "_end": departure,
                "_nights": (departure - arrival).days,
                "_category": _current_category(record),
                "_events": _events(record),
                "_booked": _moment(record["booking_date"]),
            }
        )
        record["_created"] = record["_booked"]
        record["_creation_source"] = "booking_date" if record["_booked"] is not None else None
        if record["_created"] is None and record["_category"] != "accepted":
            lifecycle = [
                stamp
                for stamp, category in record["_events"]
                if category != "unknown" and stamp <= as_of
            ]
            if lifecycle:
                record["_created"] = min(lifecycle)
                record["_creation_source"] = "first_known_lifecycle_status"
        if record["nights"] is not None and record["nights"] != record["_nights"]:
            warnings["reservation_nights_disagree_with_dates"] += 1
        financials = record["financials"]
        amounts = [item["amount_cents"] for item in financials["host_discounts"]]
        total = financials["host_accommodation_cents"]
        if financials["currency"] != prop["currency"]:
            warnings["reservation_currency_unverified"] += 1
            total = None
        elif total is not None and all(value is not None for value in amounts):
            total += sum(amounts)
        else:
            total = None
        if total is not None and total < 0:
            warnings["reservation_negative_accommodation"] += 1
            total = None
        record["_total"] = total
        if record["_category"] == "accepted" and total is None:
            warnings["accepted_reservation_value_unknown"] += 1
        if record["_category"] == "unknown":
            warnings["reservation_status_unknown"] += 1
        records.append(record)
    records = [record for record in records if record["id"] not in rejected_ids]
    reservation_source_trusted = not any(
        warnings[key]
        for key in (
            "reservation_missing_id",
            "conflicting_duplicate_reservation",
            "reservation_property_scope_unverified",
            "reservation_stay_dates_invalid",
            "reservation_stay_dates_excessive",
            "reservation_status_unknown",
        )
    )
    for record in records:
        stay_dates = _days(record["_start"], record["_end"])
        if record["_category"] in {"accepted", "request", "unknown"}:
            for day in stay_dates:
                inventory[day.isoformat()].append(record)
        if record["_category"] != "accepted":
            continue
        total = record["_total"]
        if total is not None:
            breakdown = record["financials"]["host_accommodation_breakdown"]
            weights_by_date = {item["date"]: item["amount_cents"] for item in breakdown}
            complete = (
                len(weights_by_date) == len(breakdown)
                and set(weights_by_date) == {day.isoformat() for day in stay_dates}
                and all(value is not None and value >= 0 for value in weights_by_date.values())
                and sum(weights_by_date.values()) > 0
            )
            weights = (
                [weights_by_date[day.isoformat()] for day in stay_dates]
                if complete
                else [1 for _ in stay_dates]
            )
            allocated = _allocation(total, weights)
            allocation_modes["nightly_breakdown" if complete else "equal_stay_nights"] += 1
        else:
            allocated = [None for _ in stay_dates]
        for day, cents in zip(stay_dates, allocated):
            historical[day.isoformat()].append((record, cents))
    overlap_dates = {day for day, group in historical.items() if len(group) > 1}
    overlap_ids = {record["id"] for day in overlap_dates for record, _ in historical[day]}
    if overlap_dates:
        warnings["overlapping_accepted_stay_dates"] = len(overlap_dates)
    daily = []
    # A PMS that never exposes nightly rates (OwnerRez: no GET for rates, measured 2026-09-24)
    # is a different source, not a broken one. Only when NO night carries a price or min-stay
    # do rates and restrictions become optional. A PMS that sends some prices and drops others
    # is still refused night by night, as before.
    rates_exposed = any(row["price_cents"] is not None or row["min_stay"] is not None for row in rows)
    if not rates_exposed:
        warnings["pms_does_not_expose_nightly_rates"] += 1
    # Same rule for check-in / check-out day restrictions (Lodgify and Smoobu document none per
    # night): only when NO night carries either flag do they become optional, and the card says so.
    # A PMS that sends the flags on some nights and drops them on others is still refused per night.
    restrictions_exposed = any(isinstance(row["closed_for_checkin"], bool)
                               or isinstance(row["closed_for_checkout"], bool) for row in rows)
    if not restrictions_exposed:
        warnings["pms_does_not_expose_arrival_rules"] += 1
    for calendar in rows:
        day = calendar["date"]
        group = inventory.get(day, [])
        classification = "unknown"
        cents = None
        calendar_ok = calendar["currency"] == prop["currency"] and (
            not rates_exposed
            or (
                calendar["price_cents"] is not None
                and calendar["price_cents"] >= 0
                and calendar["min_stay"] is not None
                and calendar["min_stay"] >= 1
                and (
                    not restrictions_exposed
                    or (
                        isinstance(calendar["closed_for_checkin"], bool)
                        and isinstance(calendar["closed_for_checkout"], bool)
                    )
                )
            )
        )
        if not calendar_ok:
            warnings["calendar_price_currency_or_restrictions_unknown"] += 1
        elif len(group) > 1 or (group and calendar["status_reason"] != "RESERVED"):
            classification = "conflict"
        elif group:
            record = group[0]
            if record["_category"] == "request":
                classification = "pending_hold"
            elif record["_category"] == "accepted":
                total = record["_total"]
                classification = (
                    "accepted_unknown_value"
                    if total is None
                    else ("confirmed_paid" if total > 0 else "zero_value_accepted")
                )
                cents = historical[day][0][1]
        elif calendar["status_reason"] == "AVAILABLE":
            classification = "open"
        elif calendar["status_reason"] == "BLOCKED":
            classification = "blocked"
        daily.append(
            {
                **calendar,
                "classification": classification,
                "accommodation_cents": cents,
                "reservation_count": len(group),
            }
        )
    warnings["calendar_reservation_conflict"] += sum(
        row["classification"] == "conflict" for row in daily
    )
    warnings["calendar_reservation_unknown"] += sum(
        row["classification"] in {"unknown", "accepted_unknown_value"} for row in daily
    )

    def forward(group, period_start, period_end):
        counts = Counter(row["classification"] for row in group)
        opened = [row for row in group if row["classification"] == "open"]
        paid = [row for row in group if row["classification"] == "confirmed_paid"]
        calendar_count = (period_end - period_start).days
        clean = not sum(counts[key] for key in ("unknown", "accepted_unknown_value", "conflict"))
        clean = clean and len(group) == calendar_count and reservation_source_trusted
        known_revenue = sum(row["accommodation_cents"] for row in paid)
        # Occupancy is over BOOKABLE nights. An owner-blocked night is not a night the
        # market failed to buy: 21 blocked + 2 booked of 30 is 2 of 9 (22.2%), not 6.7%.
        # Same denominator as attribution._occ and reduce_prices.tier_b.
        bookable = calendar_count - counts["blocked"]
        return {
            "start_date": period_start.isoformat(),
            "end_date_exclusive": period_end.isoformat(),
            "calendar_days": calendar_count,
            "covered_days": len(group),
            "confirmed_paid_nights": counts["confirmed_paid"],
            "pending_held_nights": counts["pending_hold"],
            "zero_value_accepted_nights": counts["zero_value_accepted"],
            "accepted_unknown_value_nights": counts["accepted_unknown_value"],
            "open_nights": counts["open"],
            "blocked_nights": counts["blocked"],
            "bookable_nights": bookable,
            "unknown_nights": counts["unknown"],
            "conflict_nights": counts["conflict"],
            "confirmed_occupancy_pct": _pct(counts["confirmed_paid"], bookable)
            if clean
            else None,
            "on_books_accommodation_cents": known_revenue if clean else None,
            "known_on_books_accommodation_cents": known_revenue,
            "on_books_adr_cents": _mean([row["accommodation_cents"] for row in paid]),
            "available_mean_rate_cents": _mean([row["price_cents"] for row in opened if row["price_cents"] is not None]),
            "available_min_stay_counts": dict(Counter(str(row["min_stay"]) for row in opened if row["min_stay"] is not None)),
            "inventory_complete": clean,
        }

    windows = [
        {"days": size, **forward(daily[:size], start, start + size * _ONE)}
        for size in sorted({size for size in (7, 30, 60, 90, days) if size <= days})
    ]
    forward_months = []
    for month in sorted({row["date"][:7] for row in daily}):
        group = [row for row in daily if row["date"].startswith(month)]
        forward_months.append(
            {
                "month": month,
                **forward(group, _date(group[0]["date"]), _date(group[-1]["date"]) + _ONE),
            }
        )
    accepted = [record for record in records if record["_category"] == "accepted"]
    history_start = min((record["_start"] for record in accepted), default=None)

    def historical_metrics(period_start, period_end):
        covered = history_start is not None and period_start >= history_start
        paid_rows, zero, unknown, conflicts = [], 0, 0, 0
        for when in _days(period_start, period_end):
            group = historical.get(when.isoformat(), [])
            if len(group) > 1:
                conflicts += 1
            elif group:
                record, cents = group[0]
                if record["_total"] is None:
                    unknown += 1
                elif record["_total"] == 0:
                    zero += 1
                else:
                    paid_rows.append(cents)
        count = (period_end - period_start).days
        complete = covered and not (unknown or conflicts) and reservation_source_trusted
        revenue = sum(paid_rows)
        return {
            "start_date": period_start.isoformat(),
            "end_date_exclusive": period_end.isoformat(),
            "calendar_days": count,
            "positive_value_stay_nights": len(paid_rows),
            "zero_value_accepted_nights": zero,
            "unknown_value_nights": unknown,
            "overlap_nights_excluded": conflicts,
            "accommodation_cents": revenue if complete else None,
            "known_accommodation_cents": revenue,
            "adr_cents": _mean(paid_rows) if covered else None,
            "gross_calendar_occupancy_pct": _pct(len(paid_rows), count) if complete else None,
            "gross_calendar_revpar_cents": _round(Decimal(revenue) / count)
            if complete and count
            else None,
            "history_coverage_comparable": complete,
        }

    historical_months = []
    month_start = date(cutoff.year - 1, 1, 1)
    while month_start < cutoff:
        next_month = date(
            month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1
        )
        historical_months.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "period_type": "full_month" if next_month <= cutoff else "elapsed_month_to_date",
                **historical_metrics(month_start, min(cutoff, next_month)),
            }
        )
        month_start = next_month
    current_ytd_start, prior_ytd_start = date(cutoff.year, 1, 1), date(cutoff.year - 1, 1, 1)
    elapsed = (cutoff - current_ytd_start).days
    current_ytd = historical_metrics(current_ytd_start, cutoff)
    prior_ytd = historical_metrics(prior_ytd_start, prior_ytd_start + elapsed * _ONE)
    changes = {}
    for key in ("positive_value_stay_nights", "accommodation_cents", "adr_cents"):
        before, after = prior_ytd[key], current_ytd[key]
        changes[key + "_pct"] = (
            _round((after / before - 1) * 100)
            if (
                before
                and after is not None
                and current_ytd["history_coverage_comparable"]
                and prior_ytd["history_coverage_comparable"]
            )
            else None
        )

    def status_at(record, instant):
        moments = [stamp for stamp, _ in record["_events"]]
        if record["_booked"]:
            moments.append(record["_booked"])
        if moments and min(moments) > instant:
            return "not_created"
        relevant = [(stamp, category) for stamp, category in record["_events"] if stamp <= instant]
        if not relevant:
            return "unknown"
        latest = max(stamp for stamp, _ in relevant)
        categories = {category for stamp, category in relevant if stamp == latest}
        return categories.pop() if len(categories) == 1 else "unknown"

    def pace(period_start, period_end, instant):
        accepted_dates, held_dates, unknown_dates = set(), set(), set()
        later_cancelled, unknown_records = 0, 0
        for record in records:
            overlap = _days(max(period_start, record["_start"]), min(period_end, record["_end"]))
            if not overlap:
                continue
            category = status_at(record, instant)
            if category == "accepted":
                accepted_dates.update(overlap)
                later_cancelled += record["_category"] == "cancelled"
            elif category == "request":
                held_dates.update(overlap)
            elif category == "unknown":
                unknown_records += 1
                unknown_dates.update(overlap)
        return {
            "start_date": period_start.isoformat(),
            "end_date_exclusive": period_end.isoformat(),
            "as_of": instant.isoformat(),
            "reconstructed_accepted_nights": len(accepted_dates),
            "reconstructed_accepted_occupancy_pct": _pct(
                len(accepted_dates), (period_end - period_start).days
            )
            if not unknown_records
            else None,
            "reconstructed_pending_nights": len(held_dates - accepted_dates),
            "unknown_status_records": unknown_records,
            "unknown_status_nights": len(unknown_dates),
            "later_cancelled_but_accepted_asof_bookings": later_cancelled,
        }

    pace_windows = []
    for window in windows:
        size = window["days"]
        prior_start = _year_before(start)
        aligned_start = start - 364 * _ONE
        pace_windows.append(
            {
                "days": size,
                "current": pace(start, start + size * _ONE, as_of),
                "prior_same_calendar": pace(
                    prior_start, prior_start + size * _ONE, _year_before(as_of)
                ),
                "prior_same_weekday": pace(
                    aligned_start, aligned_start + size * _ONE, as_of - 364 * _ONE
                ),
            }
        )

    def pickup(interval):
        made = [
            record
            for record in records
            if record["_created"] is not None and as_of - interval <= record["_created"] <= as_of
        ]
        paid = [
            record
            for record in made
            if record["_category"] == "accepted"
            and record["_total"] is not None
            and record["_total"] > 0
            and record["id"] not in overlap_ids
        ]
        horizon_nights = {
            day
            for record in paid
            for day in _days(max(start, record["_start"]), min(end, record["_end"]))
        }
        return {
            "created_records": len(made),
            "confirmed_positive_value_bookings": len(paid),
            "confirmed_total_stay_nights": sum(record["_nights"] for record in paid),
            "confirmed_nights_in_horizon": len(horizon_nights),
            "accommodation_cents": sum(record["_total"] for record in paid),
            "pending_created_records": sum(record["_category"] == "request" for record in made),
            "lifecycle_timestamp_creations": sum(
                record["_creation_source"] == "first_known_lifecycle_status" for record in made
            ),
            "creation_timestamps_complete": all(
                record["_created"] is not None for record in records
            ),
            "records_with_unknown_creation_timestamp": sum(
                record["_created"] is None for record in records
            ),
            "pending_records_with_unknown_creation_timestamp": sum(
                record["_category"] == "request" and record["_created"] is None
                for record in records
            ),
        }

    completed = [
        record
        for record in records
        if record["_category"] == "accepted"
        and record["_total"] is not None
        and record["_total"] > 0
        and record["_end"] <= cutoff
        and record["_start"] >= cutoff - 365 * _ONE
        and record["id"] not in overlap_ids
    ]
    leads = [
        (record["_start"] - record["_booked"].astimezone(local_tz).date()).days
        for record in completed
        if record["_booked"] is not None
    ]
    negative_leads = sum(value < 0 for value in leads)
    if negative_leads:
        warnings["negative_booking_lead_days_excluded"] += negative_leads
    leads = [value for value in leads if value >= 0]
    nights = sum(record["_nights"] for record in completed)
    revenue = sum(record["_total"] for record in completed)
    channel_mix = {}
    for platform in sorted({record["platform"] or "unknown" for record in completed}):
        cohort = [record for record in completed if (record["platform"] or "unknown") == platform]
        channel_nights = sum(record["_nights"] for record in cohort)
        channel_mix[platform] = {
            "bookings": len(cohort),
            "nights": channel_nights,
            "nights_pct": _pct(channel_nights, nights),
            "accommodation_cents": sum(record["_total"] for record in cohort),
        }
    review_rows, review_seen = [], set()
    for original in reviews:
        review = normalize_review(original)
        if review["id"] and review["id"] in review_seen:
            continue
        review_seen.add(review["id"])
        stamp, rating = _moment(review["reviewed_at"]), review["rating"]
        if stamp is None or stamp > as_of or rating is None or not 0 < rating <= 5:
            warnings["review_date_or_rating_invalid"] += 1
            continue
        review["_stamp"] = stamp
        review_rows.append(review)
    review_windows = []
    for span in (30, 90, None):
        cohort = [
            row for row in review_rows if span is None or row["_stamp"] >= as_of - span * _ONE
        ]
        categories = defaultdict(list)
        for review in cohort:
            scale = 2 if review["platform"] in {"booking", "booking.com"} else 1
            for detail in review["detailed_ratings"]:
                if detail["rating"] is not None and 0 < detail["rating"] <= 5 * scale:
                    categories[detail["type"]].append(detail["rating"] / scale)
        review_windows.append(
            {
                "trailing_days": span,
                "count": len(cohort),
                "mean_rating_out_of_5": _mean([row["rating"] for row in cohort]),
                "ratings_below_4": sum(row["rating"] < 4 for row in cohort),
                "category_ratings": {
                    key: {"mean": _mean(values), "count": len(values)}
                    for key, values in sorted(categories.items())
                },
            }
        )
    quality_blockers = {
        "reservation_missing_id",
        "conflicting_duplicate_reservation",
        "reservation_property_scope_unverified",
        "reservation_stay_dates_invalid",
        "reservation_stay_dates_excessive",
        "reservation_status_unknown",
        "calendar_reservation_conflict",
        "calendar_reservation_unknown",
        "calendar_price_currency_or_restrictions_unknown",
    }
    return {
        "property": prop,
        "window": {
            "start_date": start.isoformat(),
            "end_date_exclusive": end.isoformat(),
            "days": days,
            "as_of": as_of.isoformat(),
        },
        "daily": daily,
        "windows": windows,
        "forward_months": forward_months,
        "historical_months": historical_months,
        "ytd": {
            "current": current_ytd,
            "prior_same_elapsed_days": prior_ytd,
            "changes_pct": changes,
        },
        "same_lead": {
            "method": "Latest timestamped status at each cutoff, including later cancellations. "
            "Uses current stay dates; not an archived snapshot. Old revenue is not inferred.",
            "windows": pace_windows,
        },
        "pickup": {
            "definition": "Known gross creations, using booking_date when present. "
            "Non-accepted records without it use the first known trustworthy lifecycle status. "
            "Confirmed pickup and lead-time retain actual booking_date; an inquiry timestamp "
            "never substitutes for acceptance. Unknown creation timestamps are reported "
            "separately, so incomplete counts are lower bounds. "
            "Not net pickup after cancellations.",
            "last_24h": pickup(_ONE),
            "last_7d": pickup(7 * _ONE),
            "records_missing_booking_timestamp": sum(
                record["_booked"] is None for record in records
            ),
            "records_with_lifecycle_creation_timestamp": sum(
                record["_creation_source"] == "first_known_lifecycle_status" for record in records
            ),
            "records_with_unknown_creation_timestamp": sum(
                record["_created"] is None for record in records
            ),
        },
        "completed_bookings": {
            "cohort": "Positive-value stays completed before the property local current date "
            "with check-in in the trailing 365 days; overlaps excluded.",
            "bookings": len(completed),
            "nights": nights,
            "accommodation_cents": revenue,
            "adr_cents": _round(Decimal(revenue) / nights) if nights else None,
            "mean_los": _round(Decimal(nights) / len(completed)) if completed else None,
            "one_night_bookings_pct": _pct(
                sum(record["_nights"] == 1 for record in completed), len(completed)
            ),
            "known_lead_count": len(leads),
            "median_lead_days": statistics.median(leads) if leads else None,
            "mean_lead_days": _mean(leads),
            "booked_within_7d_pct": _pct(sum(n <= 7 for n in leads), len(leads)),
            "channel_mix": channel_mix,
        },
        "reviews": {
            "included_records": len(review_rows),
            "source_records": len(reviews),
            "latest_review_date": max((row["reviewed_at"] for row in review_rows), default=None),
            "windows": review_windows,
            "all_time_coverage_verified": False,
        },
        "coverage": {
            "calendar_complete": True,
            "pms_rates_exposed": rates_exposed,
            "pms_arrival_rules_exposed": restrictions_exposed,
            "calendar_days": len(rows),
            "calendar_rows_outside_horizon": len(normalized_days) - len(rows),
            "reservation_source_records": len(reservations),
            "scoped_unique_records": len(records),
            "reservation_source_trusted": reservation_source_trusted,
            "source_pagination_must_be_verified_by_caller": True,
            "analysable": not any(warnings[key] for key in quality_blockers),
            "first_observed_accepted_stay": history_start.isoformat() if history_start else None,
            "listing_operational_start_verified": False,
            "allocation_methods": dict(allocation_modes),
            "overlap_days_excluded": len(overlap_dates),
        },
        "definitions": {
            "money": "Integer cents in property currency. Host accommodation plus signed host "
            "discounts, before fees and taxes. No payout or profit claim.",
            "confirmed_paid": "Accepted reservation with positive accommodation; "
            "not verified cash.",
            "historical_denominator": "All calendar days, including unknown historical owner or "
            "maintenance blocks. Pre-observed-history periods are incomplete.",
            "allocation": "Complete nonnegative nightly breakdown weights, otherwise equal "
            "stay-night weights. Largest remainders preserve exact cents.",
            "zero_value_accepted": "Reserved at zero accommodation. Owner purpose is not assumed.",
        },
        "warnings": [
            {"code": code, "count": count} for code, count in sorted(warnings.items()) if count
        ],
    }
