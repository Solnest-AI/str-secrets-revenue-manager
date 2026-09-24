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

import json
from datetime import date

from _mvp_store import CannotAnalyze, identity

URL = "https://clients.intellihost.co/api/mcp"
UA = "claude-code (revenue-manager)"
RANK_MAX_AGE_DAYS = 7
FUNNEL_DAYS = 30
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
    comparison = {
        "first_page_impressions": _pair(f.get("first_page_search_impressions"), f.get("comp_first_page_search_impressions")),
        "click_through_rate": _pair(f.get("click_rate"), f.get("comp_click_rate")),
        "booking_rate": _pair(f.get("click_to_book_rate"), f.get("comp_click_to_book_rate")),
    }
    return {**base, "status": "ok", "reason": f"IntelliHost funnel, last {FUNNEL_DAYS} days vs comp set",
            "last_sync_date": str(to)[:10],
            "visibility_row": {"integration_status": "active", "date": str(to)[:10], "period": start.strftime("%Y-%m"),
                               "source": "intellihost", "stages": STAGES, "similar_listings_comparison": comparison}}


def rank_rows(payload: dict, start: date, guest_capacity: int = 1) -> list:
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list):
        return []
    dates = sorted({str(r.get("scrape_date"))[:10] for r in series if isinstance(r, dict) and r.get("scrape_date")})
    fresh = [d for d in dates if 0 <= (start - date.fromisoformat(d)).days <= RANK_MAX_AGE_DAYS]
    if not fresh:
        return []
    latest = fresh[-1]
    return [{"date": latest, "guest_count": int(r["guest_count"]), "position": r.get("rank"), "page": r.get("page"),
             "max_age_days": RANK_MAX_AGE_DAYS, "source": "intellihost"}
            for r in series
            if isinstance(r, dict) and str(r.get("scrape_date"))[:10] == latest
            and isinstance(r.get("guest_count"), int) and 1 <= r["guest_count"] <= max(1, int(guest_capacity))]


class IntelliHostSource:
    def __init__(self, client, connections):
        self.client, self.connections = client, connections
        self._token = connections.key("intellihost")
        self._session, self._n = None, 0

    def _rpc(self, method, params):
        self._n += 1
        headers = {"Authorization": "Bearer " + self._token, "User-Agent": UA, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        text, resp_headers = self.client.request("intellihost", "rpc", URL, headers=headers, text=True,
                                                 body={"jsonrpc": "2.0", "id": self._n, "method": method, "params": params})
        self._session = next((v for k, v in resp_headers.items() if k.lower() == "mcp-session-id"), self._session)
        if text.lstrip().startswith("{"):
            result = json.loads(text)
        else:
            events = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
            result = next((x for x in reversed(events) if x.get("id") == self._n), {})
        if result.get("error") or "result" not in result:
            raise CannotAnalyze("IntelliHost RPC returned an error")
        return result["result"]

    def _tool(self, name, args):
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
                rows = self._tool("list-properties-tool", {"include_inactive": inactive, "limit": 100}).get("properties") or []
                for p in rows:
                    lid = str(p.get("listing_id") or "")
                    if lid.isdigit() and p.get("id") is not None:
                        out.setdefault(lid, str(p["id"]))
            return out
        return self.client.fetch("intellihost.map", [identity(["intellihost", self._token])], load, ttl_seconds=86400)

    def funnel(self, ih_id, start):
        def load():
            try:
                payload = self._tool("get-funnel-dashboard", {"property_id": int(ih_id), "days": FUNNEL_DAYS,
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
                raise CannotAnalyze(f"IntelliHost has no rank scrape in the last {RANK_MAX_AGE_DAYS} days")
            return rows
        return self.client.fetch("intellihost.rankings", [identity(["intellihost", self._token]), ih_id, start.isoformat(), guest_capacity], load)
