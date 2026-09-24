"""Webhook tools — register callbacks for Turno events (e.g. Project Completed)."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..safety import require_confirm


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_webhook_types() -> Any:
        """List available webhook event types (id + name). GET /webhooks/available-types."""
        return await get_client().get("/webhooks/available-types")

    @mcp.tool
    async def turno_list_webhooks(
        type_id: int | None = None,
        search: str | None = None,
        sort: str | None = None,
        order: str | None = None,
        limit: int = 20,
        page: int = 1,
        fetch_all: bool = False,
    ) -> Any:
        """List registered webhooks. Filter by type_id and search (callback URL substring).
        fetch_all=true walks all pages. GET /webhooks."""
        params = {
            "type_id": type_id,
            "search": search,
            "sort": sort,
            "order": order,
            "limit": limit,
            "page": page,
        }
        client = get_client()
        if fetch_all:
            return await client.paginate("/webhooks", params=params)
        return await client.get("/webhooks", params=params)

    @mcp.tool
    async def turno_get_webhook(webhook_id: int) -> Any:
        """Get a registered webhook by id. GET /webhooks/{id}."""
        return await get_client().get(f"/webhooks/{webhook_id}")

    @mcp.tool
    async def turno_create_webhook(
        callback_url: str, type_id: int, data: dict[str, Any] | None = None
    ) -> Any:
        """Register a webhook: Turno POSTs to callback_url when the event type fires.
        Get type_id from turno_list_webhook_types. POST /webhooks."""
        body: dict[str, Any] = {"callback_url": callback_url, "type_id": type_id}
        if data:
            body.update(data)
        return await get_client().post("/webhooks", json=body)

    @mcp.tool
    async def turno_delete_webhook(webhook_id: int, confirm: bool = False) -> Any:
        """DESTRUCTIVE: delete a registered webhook. Requires confirm=true. DELETE /webhooks/{id}."""
        require_confirm(confirm, "turno_delete_webhook")
        return await get_client().delete(f"/webhooks/{webhook_id}")
