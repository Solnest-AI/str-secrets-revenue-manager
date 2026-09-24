"""Calendar availability and coverage checks shared by the read-only safety gates."""
from datetime import date, timedelta


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
    unbookable = row.get("unbookable", 0)
    if unbookable in (True, 1, "1", "true"):
        return "BLOCKED"
    if unbookable in (False, 0, "0", "false"):
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
