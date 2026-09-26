"""IntelliHost as the Visibility and Ranking source. Read-only, MCP over HTTP.

Measured live 2026-09-24 on Premium properties (see references/intellihost.md):
  - 40 tools over 3 tools/list pages; this adapter calls only whoami, list-properties,
    get-funnel-dashboard and get-rank-series-tool (the transport refuses everything else,
    including IntelliHost's 7 write tools)
  - Cloudflare error 1010 blocks a request without a normal User-Agent
  - Premium is PER PROPERTY; a non-Premium property answers isError with
    "An IntelliHost Premium subscription is required ...". That becomes a named gap on the
    card, never "no data". Use whatever tier the operator pays for.
  - list-properties `listing_id` is the Airbnb room id: the mapping key
  - get-funnel-dashboard: first-page impressions, click rate, click-to-book, each vs comp set.
    No views or wishlists, so the funnel walk covers the three stages IntelliHost measures.
  - get-rank-series-tool: rank + page per scrape date x guest count, scraped every few days,
    so a scrape up to 7 days old is accepted and shown with its own date.
"""

from __future__ import annotations

import itertools
import json
import threading
from datetime import date, timedelta

from _mvp_store import CannotAnalyze, identity

URL = "https://clients.intellihost.co/api/mcp"
UA = "claude-code (revenue-manager)"
RANK_MAX_AGE_DAYS = 7
# Same limit as _mvp_analysis.build()'s fresh-funnel check (3 days). At 7, a 4-7 day old funnel
# passed here as "ok" and build() then printed it as unreadable under this module's SUCCESS reason.
FUNNEL_MAX_AGE_DAYS = 3
FUNNEL_DAYS = 30
LIST_LIMIT = 200  # list-properties-tool's maximum; it has no offset, so 200 is the ceiling
PREMIUM_GAP = "IntelliHost is connected, but this property needs IntelliHost Premium to read its data"
STAGES = ["first_page_impressions", "click_through_rate", "booking_rate"]


def premium_refusal(text: str) -> bool:
    return "premium subscription is required" in str(text or "").lower()


def _pair(mine, theirs):
    return {"listing": mine, "similar_listings": theirs}


def funnel_from_dashboard(payload: dict, start: date) -> dict:
    base = {"status": "skipped", "reason": "IntelliHost returned no funnel dashboard",
            "current_month": start.strftime("%Y-%m"), "last_sync_date": None, "visibility_row": None}
    f = payload.get("funnel") if isinstance(payload, dict) else None
    to = ((payload or {}).get("period") or {}).get("to") if isinstance(payload, dict) else None
    if not isinstance(f, dict) or not to:
        if isinstance(payload, dict) and payload.get("message"):
            base["reason"] = f"IntelliHost: {str(payload['message'])[:160]}"
        return base
    try:
        ended = date.fromisoformat(str(to)[:10])
    except ValueError:
        return {**base, "reason": "IntelliHost funnel period end is unreadable"}
    age = (start - ended).days
    if age > FUNNEL_MAX_AGE_DAYS:
        # Measured in the audit: a dashboard whose data stopped 116 days earlier came back
        # as "ok". Old data is a named gap with its own date, never the current funnel.
        return {**base, "reason": f"IntelliHost funnel data ends {ended.isoformat()}, {age} days old "
                                  f"(limit {FUNNEL_MAX_AGE_DAYS}); IntelliHost has stopped syncing this listing"}
    # Live 2026-09-25 (Outliers, 3 Premium listings): the raw comp rates are NOT the benchmark.
    # IntelliHost's own note: click rate and click-to-book fall as impressions rise, and own booking
    # rate carries a 0.68x level offset against the comp series. Raw comp read two listings at 2.1x
    # and 1.6x of par on click rate as a click_through_rate BREAK. step_benchmarks.<step>.expected_rate
    # is the comp-set rate adjusted to this listing's visibility; a missing one is a named gap
    # (funnel_diagnosis -> unknown), never a fallback to the raw comp rate.
    sb = payload.get("step_benchmarks") if isinstance(payload.get("step_benchmarks"), dict) else {}

    def expected(step):
        row = sb.get(step)
        return row.get("expected_rate") if isinstance(row, dict) else None
    comparison = {
        "first_page_impressions": _pair(f.get("first_page_search_impressions"), f.get("comp_first_page_search_impressions")),
        "click_through_rate": _pair(f.get("click_rate"), expected("ctr")),
        "booking_rate": _pair(f.get("click_to_book_rate"), expected("book")),
    }
    return {**base, "status": "ok",
            "reason": f"IntelliHost funnel, last {FUNNEL_DAYS} days vs its visibility-adjusted comp-set benchmark",
            "last_sync_date": str(to)[:10],
            "visibility_row": {"integration_status": "active", "date": str(to)[:10], "period": start.strftime("%Y-%m"),
                               "source": "intellihost", "stages": STAGES, "similar_listings_comparison": comparison}}


def _guests(value):
    """guest_count arrives as an int or as a numeric string ("2"); anything else is unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def rank_rows(payload: dict, start: date, guest_capacity: int = 1) -> list:
    """Rows from the newest scrape no more than RANK_MAX_AGE_DAYS old. When IntelliHost says the
    series was `truncated`, every row carries series_truncated=True so the card can say so."""
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list):
        return []
    truncated = bool(payload.get("truncated"))
    dates = sorted({d for d in (_day(r.get("scrape_date")) for r in series if isinstance(r, dict)) if d})
    fresh = [d for d in dates if 0 <= (start - d).days <= RANK_MAX_AGE_DAYS]
    if not fresh:
        return []
    latest = fresh[-1]
    cap = max(1, int(guest_capacity))
    out = []
    for r in series:
        if not isinstance(r, dict) or _day(r.get("scrape_date")) != latest:
            continue
        guests = _guests(r.get("guest_count"))
        if guests is None or not 1 <= guests <= cap:
            continue
        row = {"date": latest.isoformat(), "guest_count": guests, "position": r.get("rank"), "page": r.get("page"),
               "max_age_days": RANK_MAX_AGE_DAYS, "source": "intellihost"}
        if truncated:
            row["series_truncated"] = True
        out.append(row)
    return out


class IntelliHostSource:
    def __init__(self, client, connections):
        self.client, self.connections = client, connections
        self._token = connections.key("intellihost")
        # analyze90 runs funnel + rankings in a 4-worker pool on ONE instance. A shared,
        # mutable request counter let thread A look for thread B's id in its own response
        # (audit REPRO: funnel "skipped"). Each call now owns its id, and the one-time
        # initialize runs under a lock so it happens once.
        self._session = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._init_lock = threading.Lock()

    def _rpc(self, method, params):
        with self._lock:
            rid = next(self._ids)
            session = self._session
        headers = {"Authorization": "Bearer " + self._token, "User-Agent": UA, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        text, resp_headers = self.client.request("intellihost", "rpc", URL, headers=headers, text=True,
                                                 body={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        new_session = next((v for k, v in resp_headers.items() if k.lower() == "mcp-session-id"), None)
        if new_session:
            with self._lock:
                self._session = new_session
        if text.lstrip().startswith("{"):
            result = json.loads(text)
        else:
            events = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
            result = next((x for x in reversed(events) if x.get("id") == rid), {})
        if result.get("error") or "result" not in result:
            raise CannotAnalyze("IntelliHost RPC returned an error")
        return result["result"]

    def _tool(self, name, args):
        if not self._session:
            with self._init_lock:
                if not self._session:
                    self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                             "clientInfo": {"name": "revenue-manager", "version": "1"}})
        r = self._rpc("tools/call", {"name": name, "arguments": args})
        text = " ".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
        if r.get("isError"):
            raise CannotAnalyze(PREMIUM_GAP if premium_refusal(text) else "IntelliHost refused the read")
        try:
            return json.loads(text)
        except ValueError:
            raise CannotAnalyze("IntelliHost returned an unreadable payload") from None

    def airbnb_map(self) -> dict:
        """Airbnb room id -> IntelliHost property id, for every listed property."""
        def load():
            out = {}
            for inactive in (False, True):
                # Live 2026-09-25: limit 100 dropped 10 of Outliers' 110 ACTIVE listings (2 of its 5
                # Premium ones) from setup's mapping, silently. Active first, so they win the ceiling.
                rows = self._tool("list-properties-tool", {"include_inactive": inactive, "limit": LIST_LIMIT}).get("properties") or []
                for p in rows:
                    lid = str(p.get("listing_id") or "")
                    if lid.isdigit() and p.get("id") is not None:
                        out.setdefault(lid, str(p["id"]))
            return out
        # v2: a map cached by the limit-100 build must not be served for another 24 hours
        return self.client.fetch("intellihost.map.v2", [identity(["intellihost", self._token])], load, ttl_seconds=86400)

    def funnel(self, ih_id, start):
        def load():
            try:
                # Live 2026-09-25: end_date WITHOUT start_date is ignored; the window ended on IntelliHost's
                # UTC today, a day after the property-local start every US evening, so build() read the
                # funnel as not current and every card said "visibility: unreadable". Both dates pin it.
                payload = self._tool("get-funnel-dashboard", {
                    "property_id": int(ih_id), "start_date": (start - timedelta(days=FUNNEL_DAYS - 1)).isoformat(),
                    "end_date": start.isoformat(), "include_daily": False})
            except CannotAnalyze as exc:
                return {"status": "skipped", "reason": str(exc), "current_month": start.strftime("%Y-%m"),
                        "last_sync_date": None, "visibility_row": None}
            return funnel_from_dashboard(payload, start)
        return self.client.fetch("intellihost.funnel", [identity(["intellihost", self._token]), ih_id, start.isoformat()], load)

    def rankings(self, ih_id, start, guest_capacity=1):
        def load():
            payload = self._tool("get-rank-series-tool", {"property_id": int(ih_id), "days": 14})
            rows = rank_rows(payload, start, guest_capacity)
            if not rows:
                cut = (" (IntelliHost truncated the series it returned, so a newer scrape may be missing)"
                       if isinstance(payload, dict) and payload.get("truncated") else "")
                raise CannotAnalyze(f"IntelliHost has no rank scrape in the last {RANK_MAX_AGE_DAYS} days{cut}")
            return rows
        return self.client.fetch("intellihost.rankings", [identity(["intellihost", self._token]), ih_id, start.isoformat(), guest_capacity], load)
