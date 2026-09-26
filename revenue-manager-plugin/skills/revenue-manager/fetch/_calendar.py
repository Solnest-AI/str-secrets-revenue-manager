"""Calendar availability and coverage checks shared by the read-only safety gates."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def unbookable_flag(value):
    """PriceLabs' `unbookable`: True, False, or None when unreadable.

    One reading for the runner (pricelabs_status) AND the reducers (reduce_prices), so an
    unbookable night with an empty booking_status is BLOCKED in both. The reducers used to
    read only booking_status and counted these nights as bookable-and-unsold.
    """
    if value in (True, 1, "1", "true", "True"):
        return True
    if value in (False, 0, "0", "false", "False"):
        return False
    return None


def local_today(tz=None, now=None) -> date:
    """Today's date in the property's timezone (IANA name or +HH:MM offset).

    Without a timezone this is the computer's local date, which is the old behaviour and
    is wrong whenever the operator is not in the property's timezone. Unreadable -> error,
    never a silent fallback.
    """
    if not tz:
        return now.astimezone().date() if now else date.today()
    now = now or datetime.now(timezone.utc)
    text = str(tz).strip()
    if text[:1] in {"+", "-"}:
        clean = text.replace(":", "")
        try:
            offset = timedelta(hours=int(clean[1:3]), minutes=int(clean[3:5] or 0))
        except ValueError:
            raise ValueError(f"unreadable timezone offset {tz!r}") from None
        return now.astimezone(timezone(-offset if clean[0] == "-" else offset)).date()
    try:
        return now.astimezone(ZoneInfo(text)).date()
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone {tz!r}") from None


def pms_status(row) -> str:
    if not isinstance(row, dict) or not isinstance(row.get("status"), dict):
        return "UNKNOWN"
    status = row["status"]
    reason = str(status.get("reason") or "").strip().upper()
    if reason not in {"AVAILABLE", "RESERVED", "BLOCKED"}:
        return "UNKNOWN"
    if "available" in status and status["available"] is not (reason == "AVAILABLE"):
        return "UNKNOWN"
    return reason


def pricelabs_status(row) -> str:
    if not isinstance(row, dict) or not isinstance(row.get("booking_status"), str):
        return "UNKNOWN"
    status = row["booking_status"].strip().lower()
    if status.startswith("booked"):
        return "RESERVED"
    if status == "blocked":
        return "BLOCKED"
    if status not in {"", "available"}:
        return "UNKNOWN"
    flag = unbookable_flag(row.get("unbookable", 0))
    if flag is True:
        return "BLOCKED"
    if flag is False:
        return "AVAILABLE"
    return "UNKNOWN"


def validate_calendar(rows, label, status_reader, start=None, end=None):
    """Require one known-status row for every inclusive date in the window."""
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label} calendar has no readable date rows")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label} calendar contains a non-object row")
        raw_date = row.get("date")
        try:
            when = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            raise ValueError(f"{label} calendar contains an invalid date") from None
        if when.isoformat() != raw_date or raw_date in seen:
            raise ValueError(f"{label} calendar contains a duplicate or non-canonical date: {raw_date}")
        if status_reader(row) == "UNKNOWN":
            raise ValueError(f"{label} calendar has an unknown or contradictory status on {raw_date}")
        seen.add(raw_date)
    first = date.fromisoformat(start or min(seen))
    last = date.fromisoformat(end or max(seen))
    if last < first:
        raise ValueError(f"{label} calendar window ends before it starts")
    expected = {(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)}
    if seen != expected:
        raise ValueError(f"{label} calendar coverage is incomplete: expected {len(expected)} dates, "
                         f"received {len(seen)}; missing={len(expected - seen)}, "
                         f"outside_window={len(seen - expected)}")
