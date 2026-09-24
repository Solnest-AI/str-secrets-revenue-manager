"""Property tools, incl. property checklists and property↔contractor links."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..safety import require_confirm


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_properties(
        sort: str | None = None,
        order: str | None = None,
        limit: int = 20,
        page: int = 1,
        fetch_all: bool = False,
    ) -> Any:
        """List your properties. sort/order optional; fetch_all=true walks all pages. GET /properties."""
        params = {"sort": sort, "order": order, "limit": limit, "page": page}
        client = get_client()
        if fetch_all:
            return await client.paginate("/properties", params=params)
        return await client.get("/properties", params=params)

    @mcp.tool
    async def turno_get_property(property_id: int) -> Any:
        """Get a single property by id. GET /properties/{id}."""
        return await get_client().get(f"/properties/{property_id}")

    @mcp.tool
    async def turno_create_property(data: dict[str, Any]) -> Any:
        """Create a property. `data` is the JSON body (e.g. alias, address, bedrooms, bathrooms). POST /properties."""
        return await get_client().post("/properties", json=data)

    @mcp.tool
    async def turno_update_property(property_id: int, data: dict[str, Any]) -> Any:
        """Update a property. `data` holds fields to change. PATCH /properties/{id}."""
        return await get_client().patch(f"/properties/{property_id}", json=data)

    @mcp.tool
    async def turno_get_property_checklist(property_id: int) -> Any:
        """Get the checklist(s) for a property. GET /properties/{id}/checklists."""
        return await get_client().get(f"/properties/{property_id}/checklists")

    @mcp.tool
    async def turno_update_property_checklist(
        property_id: int, checklist_id: int, data: dict[str, Any]
    ) -> Any:
        """Update a property checklist. PATCH /properties/{id}/checklists/{checklist_id}."""
        return await get_client().patch(
            f"/properties/{property_id}/checklists/{checklist_id}", json=data
        )

    @mcp.tool
    async def turno_delete_property_checklist(property_id: int, confirm: bool = False) -> Any:
        """DESTRUCTIVE: delete a property's checklist. Requires confirm=true. DELETE /properties/{id}/checklists.

        WARNING: this removes ALL checklists for the property, not a single one.
        To update a specific checklist use turno_update_property_checklist instead."""
        require_confirm(confirm, "turno_delete_property_checklist")
        return await get_client().delete(f"/properties/{property_id}/checklists")

    @mcp.tool
    async def turno_get_property_contractors(property_id: int) -> Any:
        """List cleaners (contractors) linked to a property. GET /properties/{id}/contractors."""
        return await get_client().get(f"/properties/{property_id}/contractors")

    @mcp.tool
    async def turno_add_property_contractor(
        property_id: int, contractor_id: int, data: dict[str, Any] | None = None
    ) -> Any:
        """Link a cleaner to a property (property-side; mirror of turno_add_cleaner_to_property).
        `data` may carry offer/payment settings. POST /properties/{id}/contractors/{contractor_id}."""
        return await get_client().post(
            f"/properties/{property_id}/contractors/{contractor_id}", json=data or {}
        )

    @mcp.tool
    async def turno_update_property_contractor(
        property_id: int, contractor_id: int, data: dict[str, Any]
    ) -> Any:
        """Update a property↔cleaner link (property-side). PATCH /properties/{id}/contractors/{contractor_id}."""
        return await get_client().patch(
            f"/properties/{property_id}/contractors/{contractor_id}", json=data
        )

    @mcp.tool
    async def turno_remove_property_contractor(
        property_id: int, contractor_id: int, confirm: bool = False
    ) -> Any:
        """DESTRUCTIVE: unlink a cleaner from a property (property-side). Requires confirm=true.
        DELETE /properties/{id}/contractors/{contractor_id}."""
        require_confirm(confirm, "turno_remove_property_contractor")
        return await get_client().delete(
            f"/properties/{property_id}/contractors/{contractor_id}"
        )

    @mcp.tool
    async def turno_disconnect_property(property_id: int, confirm: bool = False) -> Any:
        """DESTRUCTIVE: disconnect a property (removes integration link/sync). Requires confirm=true.
        POST /properties/{id}/disconnect."""
        require_confirm(confirm, "turno_disconnect_property")
        return await get_client().post(f"/properties/{property_id}/disconnect")
