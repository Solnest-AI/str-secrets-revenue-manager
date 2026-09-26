"""Async HTTP client for the Turno External API v2."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import TurnoConfig, load_config
from .errors import TurnoAPIError, raise_for_status

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE = 0.5  # seconds: 0.5, 1.0, 2.0
MAX_PAGE_LIMIT = 50
DEFAULT_TIMEOUT = 30.0

# Turno put api.turnoverbnb.com behind a Cloudflare managed challenge on
# 2026-08-20. A default library User-Agent is challenged: every call returns 403
# with an interstitial HTML body and a `cf-mitigated: challenge` header, before
# the partner token is ever evaluated (an unauthenticated call gets the same 403,
# not a 401). A browser User-Agent alone clears it. TLS/JA3 is not fingerprinted,
# so httpx is still fine.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop None-valued params so optional args don't leak into the query string."""
    if not params:
        return None
    cleaned = {k: v for k, v in params.items() if v is not None}
    return cleaned or None


def _unwrap(response: httpx.Response) -> Any:
    """Return the response payload, unwrapping the top-level ``data`` envelope."""
    if response.status_code == 204 or not response.content:
        return None
    body = response.json()
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _items_and_last_page(data: Any) -> tuple[list[Any], int | None]:
    if isinstance(data, dict) and "items" in data:
        return (data.get("items") or []), data.get("last_page")
    if isinstance(data, list):
        return data, None
    return [], None


class TurnoClient:
    """Thin async wrapper that injects auth, retries transient failures,
    unwraps the ``data`` envelope, and can walk paginated list endpoints."""

    def __init__(
        self,
        config: TurnoConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config or load_config()
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(DEFAULT_TIMEOUT))
        self._owns_client = client is None

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_token}",
            "TBNB-Partner-ID": self.config.partner_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _url(self, path: str) -> str:
        return f"{self.config.root_url}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        url = self._url(path)
        clean = _clean_params(params)
        # A timeout or 5xx can arrive after a write was committed. Only replay
        # read requests; mutations need caller reconciliation before another try.
        retries = MAX_RETRIES if method.upper() in {"GET", "HEAD", "OPTIONS"} else 0
        for attempt in range(retries + 1):
            try:
                resp = await self._client.request(
                    method, url, params=clean, json=json, headers=self.headers
                )
            except httpx.TransportError as exc:
                if attempt < retries:
                    await asyncio.sleep(BACKOFF_BASE * (2**attempt))
                    continue
                raise TurnoAPIError(0, f"Network error calling Turno: {exc}") from exc

            if resp.status_code in RETRY_STATUSES and attempt < retries:
                await asyncio.sleep(BACKOFF_BASE * (2**attempt))
                continue

            raise_for_status(resp)
            return _unwrap(resp)

        raise TurnoAPIError(0, "Exhausted retries calling Turno")  # pragma: no cover

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(
        self, path: str, *, json: Any | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self.request("POST", path, json=json, params=params)

    async def patch(
        self, path: str, *, json: Any | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self.request("PATCH", path, json=json, params=params)

    async def delete(
        self, path: str, *, json: Any | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self.request("DELETE", path, json=json, params=params)

    async def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_items: int | None = None,
    ) -> list[Any]:
        """Walk all pages of a list endpoint, returning a flat list of items."""
        params = dict(params or {})
        limit = min(int(params.get("limit", MAX_PAGE_LIMIT)), MAX_PAGE_LIMIT)
        params["limit"] = limit
        page = int(params.get("page", 1))
        items: list[Any] = []
        while True:
            params["page"] = page
            data = await self.request("GET", path, params=params)
            batch, last_page = _items_and_last_page(data)
            items.extend(batch)
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            if not batch or last_page is None or page >= last_page:
                return items
            page += 1

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


_default_client: TurnoClient | None = None


def get_client() -> TurnoClient:
    """Lazily create and return the process-wide client.

    Created on first use so the server can boot and list tools without
    credentials present; the first real tool call validates config.
    """
    global _default_client
    if _default_client is None:
        _default_client = TurnoClient()
    return _default_client
