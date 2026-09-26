"""Small, allowlisted RankBreeze dashboard parser for the read-only MVP.

The dashboard headline combines the current and future months. Only the dated
monthly comparison tables are suitable for the visibility gate. No HTML, account
details, listing title, or arbitrary page text leaves this module.
"""
from __future__ import annotations

import html
import math
import re
from datetime import date, datetime
from html.parser import HTMLParser


METRICS = (
    "first_page_impressions", "click_through_rate", "view", "wishlist",
    "booking_rate", "conversion_rate",
)
_LABELS = {
    "1st page impressions": "first_page_impressions",
    "first page impressions": "first_page_impressions",
    "click-through rate": "click_through_rate",
    "listing views": "view",
    "wishlists": "wishlist",
    "wishlist additions": "wishlist",
    "booking rate": "booking_rate",
    "conversion rates": "conversion_rate",
    "conversion rate": "conversion_rate",
    "overall conversion rate": "conversion_rate",
}
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTHS = {name.lower(): n for n, name in enumerate(_MONTH_NAMES, 1)}
_MONTHS.update({name[:3].lower(): n for n, name in enumerate(_MONTH_NAMES, 1)})
_MONTH_PATTERN = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_MONTH_ROW = re.compile(rf"\b({_MONTH_PATTERN})\.?\s*,?\s+(\d{{4}})\b", re.I)
_TABLE = re.compile(
    r"\bPeriod\s+(" + "|".join(re.escape(k) for k in _LABELS)
    + r")\s+Similar\s+Listings(?:\s+data)?\b", re.I,
)
_SYNC = re.compile(r"(?<!Rankings )(?<!Ranking )\bLast\s+sync\s*:\s*", re.I)
_DATE = re.compile(
    rf"^(?:(\d{{4}}-\d{{2}}-\d{{2}})|({_MONTH_PATTERN})\.?\s+"
    r"(\d{1,2}),?\s+(\d{4}))\b", re.I,
)
_NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?"
_MISSING = r"(?:N/?A|No\s+data|--|-)"
_VALUE = rf"(?P<number>{_NUMBER})(?P<percent>\s*%)?|(?P<missing>{_MISSING})"
_VALUE_RE = re.compile(rf"^\s*(?:{_VALUE})(?=\s|$)", re.I)
_CITY_RANK = re.compile(
    r"\bAverage\s+city\s+rankings\s+(\d+)\s+of\s+(\d+)"
    r"\s*\(\s*Page\s+(\d+)\s*\)", re.I,
)
_RANK_DATE = re.compile(
    r"\b(?:Rankings?\s+(?:last\s+sync|updated|date)|Ranking\s+as\s+of)\s*:\s*",
    re.I,
)
_ID_MARKER = re.compile(r"\bdata-(?:listing(?:-id)?|ranking-id)\s*=\s*[\"']?(\d+)\b", re.I)


class _VisibleText(HTMLParser):
    """Read text and numeric subject IDs without retaining attributes or scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored: list[str] = []
        self.subject_ids: set[str] = set()
        self.canonical_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored.append(tag)
            return
        if self.ignored:
            return
        values = dict(attrs)
        for key in ("data-listing", "data-listing-id", "data-ranking-id"):
            value = values.get(key) or ""
            if re.fullmatch(r"\d+", value):
                self.subject_ids.add(value)
        canonical = None
        if tag == "link" and "canonical" in (values.get("rel") or "").lower().split():
            canonical = values.get("href")
        elif tag == "meta" and (values.get("property") or "").lower() == "og:url":
            canonical = values.get("content")
        if canonical:
            match = re.fullmatch(r"https://app\.rankbreeze\.com/rankings/(\d+)/?", canonical)
            if match:
                self.canonical_ids.add(match.group(1))
        self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


def _visible_text(value: str) -> tuple[str, set[str]]:
    parser = _VisibleText()
    parser.feed(value)
    parser.close()
    text = re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()
    # Text captured after an earlier HTML cleanup can retain these numeric IDs.
    ids = parser.canonical_ids or parser.subject_ids or set(_ID_MARKER.findall(text))
    return text, ids


def _date_at_start(value: str) -> date | None:
    match = _DATE.match(value.strip())
    if not match:
        return None
    try:
        if match.group(1):
            return date.fromisoformat(match.group(1))
        return date(int(match.group(4)), _MONTHS[match.group(2).lower()], int(match.group(3)))
    except ValueError:
        return None


def _pair(value: str, metric: str) -> dict | None:
    value = re.sub(r"^\s*CURRENT\b\s*", "", value, flags=re.I)
    rates = metric in {"click_through_rate", "booking_rate", "conversion_rate"}
    numbers: list[float | None] = []
    for _ in range(2):
        match = _VALUE_RE.match(value)
        if not match:
            # A missing peer is retained as unknown, never interpreted as zero.
            if len(numbers) == 1:
                numbers.append(None)
                break
            return None
        number = None
        if match.group("number"):
            number = float(match.group("number").replace(",", ""))
            if (not math.isfinite(number) or bool(match.group("percent")) != rates
                    or (rates and number > 100)):
                number = None
        numbers.append(number)
        value = value[match.end():]
        if not rates:
            value = re.sub(r"^\s*(?:impressions?|views?|wishlists?)\b", "", value, flags=re.I)
    return {"listing": numbers[0], "similar_listings": numbers[1]}


def _freshness(day: date | None, as_of: date, max_age_days: int) -> str | None:
    if day is None:
        return "has no readable Last sync date"
    age = (as_of - day).days
    if age < 0:
        return "has a future Last sync date"
    if age > max_age_days:
        return f"is stale ({age} days since sync; maximum {max_age_days})"
    return None


def _ranking(text: str, as_of: date, max_age_days: int) -> dict:
    result = {
        "status": "requires_separate_source",
        "reason": "no dated city-ranking headline; read current ranking rows separately",
        "city_rank": None, "date": None, "age_days": None, "rows": None,
    }
    match = _CITY_RANK.search(text)
    if not match:
        return result
    position, total, page = map(int, match.groups())
    if not 1 <= position <= total or page < 1:
        result["reason"] = "city-ranking headline contains invalid positions"
        return result
    result["city_rank"] = {"position": position, "of": total, "page": page}
    dates = [_date_at_start(text[m.end():]) for m in _RANK_DATE.finditer(text)]
    day = dates[0] if dates and all(d == dates[0] for d in dates) else None
    if day:
        result.update(date=day.isoformat(), age_days=(as_of - day).days)
    problem = _freshness(day, as_of, max_age_days)
    if problem:
        result["reason"] = f"city ranking {problem}; read current ranking rows separately"
        return result
    result.update(
        status="ok", reason="explicitly dated average city ranking",
        rows=[{"date": day.isoformat(), "position": position, "of": total,
               "page": page, "scope": "average_city"}],
    )
    return result


def parse_booking_funnel(
    html_or_visible_text: str,
    as_of: date,
    *,
    expected_listing_id: str | None = None,
    max_age_days: int = 3,
) -> dict:
    """Return allowlisted monthly comparisons and a freshness-checked visibility row.

    ``status`` describes visibility only. ``visibility_row`` can be passed directly
    to ``flywheel.spoke_visibility`` when status is ``ok``; otherwise it is None.
    ``ranking`` has its own status and rows. Metric Last sync dates never establish
    the freshness of the separate city-ranking headline.

    The caller binds the response to the requested listing. When the response also
    contains a subject ID, expected_listing_id enables a second identity check.
    An absent ID is reported, as authenticated fragments do not always include one.
    Percentages stay on a 0-100 scale. Missing peers stay None. Full years are read
    from every month label; the CURRENT badge and headline totals are ignored.
    """
    if not isinstance(html_or_visible_text, str):
        raise TypeError("RankBreeze response must be HTML or visible text")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise TypeError("as_of must be a date")
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 0:
        raise ValueError("max_age_days must be a nonnegative integer")
    text, subject_ids = _visible_text(html_or_visible_text)
    current_month = as_of.strftime("%Y-%m")
    result = {
        "status": "skipped", "reason": "monthly comparisons are unreadable",
        "source": "rankbreeze_dashboard_monthly_comparisons",
        "current_month": current_month, "last_sync_date": None, "age_days": None,
        "metric_sync_dates": {}, "months": {}, "visibility_row": None,
        "listing_id": next(iter(subject_ids)) if len(subject_ids) == 1 else None,
        "subject_verification": "unavailable" if not subject_ids else "not_requested",
        "ranking": _ranking(text, as_of, max_age_days),
    }
    expected = str(expected_listing_id) if expected_listing_id is not None else None
    if expected is not None and subject_ids:
        if subject_ids != {expected}:
            result["subject_verification"] = "mismatch" if len(subject_ids) == 1 else "ambiguous"
            result["reason"] = "RankBreeze subject does not uniquely match requested listing"
            result["ranking"] = {
                "status": "requires_separate_source", "reason": result["reason"],
                "city_rank": None, "date": None, "age_days": None, "rows": None,
            }
            return result
        result["subject_verification"] = "matched"

    tables = list(_TABLE.finditer(text))
    metric_dates: dict[str, list[date | None]] = {}
    errors: list[str] = []
    months: dict[str, dict] = {}
    previous_end = 0
    for index, table in enumerate(tables):
        metric = _LABELS[table.group(1).lower()]
        prefix = text[previous_end:table.start()]
        syncs = list(_SYNC.finditer(prefix))
        synced = _date_at_start(prefix[syncs[-1].end():]) if syncs else None
        metric_dates.setdefault(metric, []).append(synced)
        segment_end = tables[index + 1].start() if index + 1 < len(tables) else len(text)
        segment = text[table.end():segment_end]
        # Only adjacent rows in this table count. Later modal headings are prose.
        next_sync = _SYNC.search(segment)
        if next_sync:
            segment = segment[:next_sync.start()]
        rows = list(_MONTH_ROW.finditer(segment))
        for row_index, row in enumerate(rows):
            year, month = int(row.group(2)), _MONTHS[row.group(1).lower()]
            if not 1 <= year <= 9999:
                continue
            month_key = f"{year:04d}-{month:02d}"
            row_end = rows[row_index + 1].start() if row_index + 1 < len(rows) else len(segment)
            pair = _pair(segment[row.end():row_end], metric)
            if pair is None:
                continue
            month_data = months.setdefault(month_key, {})
            if metric in month_data and month_data[metric] != pair:
                errors.append(f"conflicting {metric} rows for {month_key}")
            month_data[metric] = pair
        previous_end = table.end()

    result["months"] = dict(sorted(months.items()))
    current = months.get(current_month, {})
    days = []
    for metric in METRICS:
        dates = metric_dates.get(metric, [])
        day = min(dates) if dates and all(d is not None for d in dates) else None
        result["metric_sync_dates"][metric] = day.isoformat() if day else None
        if day:
            days.append(day)
        # Check every duplicate's date, including future timestamps.
        problems = [_freshness(d, as_of, max_age_days) for d in dates or [None]]
        issue = next((problem for problem in problems if problem), None)
        if issue:
            errors.append(f"{metric} {issue}")
        pair = current.get(metric) or {}
        if pair.get("listing") is None or pair.get("similar_listings") is None:
            errors.append(f"{metric} has no complete comparison for {current_month}")
    if days:
        oldest = min(days)
        result.update(last_sync_date=oldest.isoformat(), age_days=(as_of - oldest).days)
    if errors:
        result["reason"] = "; ".join(dict.fromkeys(errors))
        return result
    result.update(
        status="ok", reason="fresh current-month funnel with all six peer comparisons",
        visibility_row={
            "integration_status": "active", "date": result["last_sync_date"],
            "period": current_month, "source": result["source"],
            "similar_listings_comparison": {key: current[key] for key in METRICS},
        },
    )
    return result


def _num_or_none(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _complete_pull(row: dict) -> bool:
    """Active integration, and every one of the six stages has a number for the listing AND
    for similar listings."""
    comparison = row.get("similar_listings_comparison")
    if str(row.get("integration_status") or "").lower() != "active" or not isinstance(comparison, dict):
        return False
    for key in METRICS:
        stage = comparison.get(key)
        if not isinstance(stage, dict):
            return False
        if _num_or_none(stage.get("listing")) is None or _num_or_none(stage.get("similar_listings")) is None:
            return False
    return True


def funnel_from_summary(payload: dict, as_of: date, listing_id: str, max_age_days: int = 3) -> dict:
    """The Visibility spoke from RankBreeze's OFFICIAL hosted MCP (get_listing_metrics_summary,
    interval=daily). Measured live 2026-09-25: the last 3 pull dates, each carrying
    integration_status and all six stages vs similar listings. Replaces the web-cookie scrape
    (RANKBREEZE_SESSION), which the summit connections kit retired.

    Only this listing's rows are used; the newest pull not in the future and no more than
    `max_age_days` old wins. Anything else is `skipped` with a reason: empty is not zero."""
    base = {"status": "skipped", "reason": "RankBreeze returned no funnel summary for this listing",
            "current_month": as_of.strftime("%Y-%m"), "last_sync_date": None, "age_days": None,
            "visibility_row": None, "source": "rankbreeze-mcp"}
    rows = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return base
    mine = []
    for r in rows:
        if not isinstance(r, dict) or str(r.get("listing_id")) != str(listing_id):
            continue
        try:
            pulled = date.fromisoformat(str(r.get("pull_date"))[:10])
        except ValueError:
            continue
        if 0 <= (as_of - pulled).days <= max_age_days:
            mine.append((pulled, r))
    if not mine:
        return base
    # The newest COMPLETE pull wins: RankBreeze's latest pull is often still being collected
    # (a stage missing or null), and an older full pull is a better answer than a newer hole.
    # Only when none in the window is complete does the newest one go through, so the spoke
    # names the gap instead of the runner guessing.
    complete = [x for x in mine if _complete_pull(x[1])]
    pulled, r = max(complete or mine, key=lambda x: x[0])
    comparison = r.get("similar_listings_comparison")
    if not isinstance(comparison, dict) or not comparison:
        return {**base, "reason": "RankBreeze funnel has no similar-listings comparison"}
    return {**base, "status": "ok", "reason": "fresh funnel with peer comparisons (official MCP)",
            "last_sync_date": pulled.isoformat(), "age_days": (as_of - pulled).days,
            "visibility_row": {"integration_status": r.get("integration_status"), "date": pulled.isoformat(),
                               "period": as_of.strftime("%Y-%m"), "source": "rankbreeze-mcp",
                               "similar_listings_comparison": comparison}}

