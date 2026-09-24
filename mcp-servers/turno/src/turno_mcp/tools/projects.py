"""Project (turnover/cleaning job) tools — the core of Turno."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..safety import require_confirm
from ._common import csv


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_projects(
        start: str | None = None,
        end: str | None = None,
        properties: list[int] | None = None,
        cleaners: list[int] | None = None,
        customers: list[int] | None = None,
        property_groups: list[int] | None = None,
        project_ids: list[int] | None = None,
        time_type: str | None = None,
        time_value: int | None = None,
        none: bool | None = None,
        integration_only: int | None = None,
        integration_uid: str | None = None,
        date_range_start: str | None = None,
        date_range_end: str | None = None,
        limit: int = 20,
        page: int = 1,
        fetch_all: bool = False,
    ) -> Any:
        """List cleaning projects (turnovers), filtered by date and by properties/cleaners/customers.

        Dates are 'YYYY-MM-DD'. List filters (properties, cleaners, customers,
        property_groups, project_ids) take id lists. Set fetch_all=true to walk
        every page. Returns {items, current_page, last_page, total}, or a flat
        list when fetch_all is true.
        """
        params = {
            "start": start,
            "end": end,
            "properties": csv(properties),  # Turno /projects uses CSV encoding, e.g. "1,2,3"
            "cleaners": csv(cleaners),
            "customers": csv(customers),
            "property_groups": csv(property_groups),
            "project_ids": csv(project_ids),
            "time_type": time_type,
            "time_value": time_value,
            "none": none,
            "integration_only": integration_only,
            "integration_uid": integration_uid,
            "date_range_start": date_range_start,
            "date_range_end": date_range_end,
            "limit": limit,
            "page": page,
        }
        client = get_client()
        if fetch_all:
            return await client.paginate("/projects", params=params)
        return await client.get("/projects", params=params)

    @mcp.tool
    async def turno_get_project(project_id: int) -> Any:
        """Get a single cleaning project by id."""
        return await get_client().get(f"/projects/{project_id}")

    @mcp.tool
    async def turno_create_project(data: dict[str, Any]) -> Any:
        """Create a cleaning project. `data` is the JSON body (e.g. property_id,
        start_date, end_date, project_type_id, contractor_id). See Turno API POST /projects."""
        return await get_client().post("/projects", json=data)

    @mcp.tool
    async def turno_update_project(project_id: int, data: dict[str, Any]) -> Any:
        """Update a cleaning project. `data` holds the fields to change. PATCH /projects/{id}."""
        return await get_client().patch(f"/projects/{project_id}", json=data)

    @mcp.tool
    async def turno_delete_project(project_id: int, confirm: bool = False) -> Any:
        """DESTRUCTIVE: delete a cleaning project. Requires confirm=true. DELETE /projects/{id}."""
        require_confirm(confirm, "turno_delete_project")
        return await get_client().delete(f"/projects/{project_id}")

    @mcp.tool
    async def turno_get_project_checklist(project_id: int) -> Any:
        """Get the checklist for a project. GET /projects/{id}/checklist."""
        return await get_client().get(f"/projects/{project_id}/checklist")

    @mcp.tool
    async def turno_get_available_project_types() -> Any:
        """List available project types (e.g. cleaning, inspection). GET /projects/available-types."""
        return await get_client().get("/projects/available-types")

    @mcp.tool
    async def turno_notify_early_checkout(project_id: int) -> Any:
        """Notify the assigned cleaner of an early checkout. POST /projects/{id}/notify-early-checkout."""
        return await get_client().post(f"/projects/{project_id}/notify-early-checkout")
