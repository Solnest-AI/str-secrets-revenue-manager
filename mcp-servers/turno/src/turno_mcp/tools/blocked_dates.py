"""Blocked-date tools (owner blocks / non-booking occupancy)."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..safety import require_confirm


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_blocked_dates(
        properties: list[int] | None = None,
        checkin_from: str | None = None,
        checkout_from: str | None = None,
        checkin_to: str | None = None,
        checkout_to: str | None = None,
        sort: str | None = None,
        order: str | None = None,
        limit: int = 20,
        page: int = 1,
        fetch_all: bool = False,
    ) -> Any:
        """List blocked dates. Filter by date windows ('YYYY-MM-DD') and a properties
        id list. fetch_all=true walks all pages. GET /blocked-dates."""
        params: dict[str, Any] = {
            "checkin_from": checkin_from,
            "checkout_from": checkout_from,
            "checkin_to": checkin_to,
            "checkout_to": checkout_to,
            "sort": sort,
            "order": order,
            "limit": limit,
            "page": page,
        }
        if properties:
            params["properties[]"] = properties  # Turno /blocked-dates uses bracket-array encoding, not CSV
        client = get_client()
        if fetch_all:
            return await client.paginate("/blocked-dates", params=params)
        return await client.get("/blocked-dates", params=params)

    @mcp.tool
    async def turno_get_blocked_date(blocked_date_id: int) -> Any:
        """Get a single blocked date by id. GET /blocked-dates/{id}."""
        return await get_client().get(f"/blocked-dates/{blocked_date_id}")

    @mcp.tool
    async def turno_create_blocked_date(data: dict[str, Any]) -> Any:
        """Create a blocked date. `data` is the JSON body (e.g. property_id, start_date,
        end_date, summary, external_blocked_date_id). POST /blocked-dates."""
        return await get_client().post("/blocked-dates", json=data)

    @mcp.tool
    async def turno_update_blocked_date(blocked_date_id: int, data: dict[str, Any]) -> Any:
        """Update a blocked date. `data` holds fields to change. PATCH /blocked-dates/{id}."""
        return await get_client().patch(f"/blocked-dates/{blocked_date_id}", json=data)

    @mcp.tool
    async def turno_delete_blocked_date(blocked_date_id: int, confirm: bool = False) -> Any:
        """DESTRUCTIVE: delete a blocked date. Requires confirm=true. DELETE /blocked-dates/{id}."""
        require_confirm(confirm, "turno_delete_blocked_date")
        return await get_client().delete(f"/blocked-dates/{blocked_date_id}")
