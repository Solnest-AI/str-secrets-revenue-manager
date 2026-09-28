"""One timezone reader for every script: an IANA name or a +HH:MM / +HHMM / +HH offset.

Windows ships no system timezone database, so IANA names resolve only through the `tzdata`
package, which every launcher line pulls in (`uv run --with tzdata --python 3.13 python`).
Without it EVERY name is unknown and every card would block, so a missing database is its
own named error (TimezoneDataMissing), never read as "this property's timezone is bad" and
never a silent UTC fallback (Windows first run, 2026-09-28).
"""

from __future__ import annotations

import re
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

_OFFSET = re.compile(r"([+-])(\d{2}):?(\d{2})?")

TZDATA_HINT = ("this computer has no timezone database: run the script with "
               "`uv run --with tzdata --python 3.13 python ...`")


class TimezoneDataMissing(ValueError):
    """IANA names cannot resolve on this machine at all (Windows without tzdata)."""


def resolve(value) -> tzinfo:
    """tzinfo for an IANA name or a fixed offset. Unreadable -> ValueError; no database ->
    TimezoneDataMissing (a ValueError too, so callers that block on a bad zone still block)."""
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError("timezone is missing")
    if text[0] in "+-":
        m = _OFFSET.fullmatch(text)
        hours, minutes = (int(m.group(2)), int(m.group(3) or 0)) if m else (24, 0)
        total, negative = hours * 60 + minutes, m is not None and m.group(1) == "-"
        if minutes >= 60 or total >= 24 * 60:
            raise ValueError(f"unreadable timezone offset {value!r}")
        offset = timedelta(minutes=total)
        return timezone(-offset if negative else offset)
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        if not available_timezones():
            raise TimezoneDataMissing(TZDATA_HINT) from None
        raise ValueError(f"unknown timezone {value!r}") from None
