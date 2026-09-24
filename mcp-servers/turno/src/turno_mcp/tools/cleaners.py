"""Cleaner (contractor) tools, incl. contractor-side property links."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..safety import require_confirm


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_cleaners(
        limit: int = 20, page: int = 1, fetch_all: bool = False
    ) -> Any:
        """List cleaners (contractors) on your account. fetch_all=true walks all pages. GET /contractors."""
        params = {"limit": limit, "page": page}
        client = get_client()
        if fetch_all:
            return await client.paginate("/contractors", params=params)
        return await client.get("/contractors", params=params)

    @mcp.tool
    async def turno_get_cleaner_properties(contractor_id: int) -> Any:
        """List the properties a given cleaner is linked to. GET /contractors/{id}/properties."""
        return await get_client().get(f"/contractors/{contractor_id}/properties")

    @mcp.tool
    async def turno_add_cleaner_to_property(
        contractor_id: int, property_id: int, data: dict[str, Any] | None = None
    ) -> Any:
        """Link a cleaner to a property (contractor-side; mirror of turno_add_property_contractor).
        POST /contractors/{id}/properties/{property_id}."""
        return await get_client().post(
            f"/contractors/{contractor_id}/properties/{property_id}", json=data or {}
        )

    @mcp.tool
    async def turno_update_cleaner_property(
        contractor_id: int, property_id: int, data: dict[str, Any]
    ) -> Any:
        """Update a cleaner↔property link (contractor-side). PATCH /contractors/{id}/properties/{property_id}."""
        return await get_client().patch(
            f"/contractors/{contractor_id}/properties/{property_id}", json=data
        )

    @mcp.tool
    async def turno_remove_cleaner_from_property(
        contractor_id: int, property_id: int, confirm: bool = False
    ) -> Any:
        """DESTRUCTIVE: unlink a cleaner from a property (contractor-side). Requires confirm=true.
        DELETE /contractors/{id}/properties/{property_id}."""
        require_confirm(confirm, "turno_remove_cleaner_from_property")
        return await get_client().delete(
            f"/contractors/{contractor_id}/properties/{property_id}"
        )
