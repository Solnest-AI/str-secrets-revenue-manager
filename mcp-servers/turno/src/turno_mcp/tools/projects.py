"""Project (turnover/cleaning job) tools — the core of Turno."""

from __future__ import annotations

from datetime import date
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

        Dates are 'YYYY-MM-DD' and filter the project's start date inclusively.
        Date filters require fetch_all=true: the API can ignore date parameters,
        so all matching property pages are fetched and filtered locally.
        start/end and date_range_start/date_range_end are equivalent aliases.
        List filters (properties, cleaners, customers,
        property_groups, project_ids) take id lists. Set fetch_all=true to walk
        every page. Returns {items, current_page, last_page, total}, or a flat
        list when fetch_all is true.
        """
        if ((start and date_range_start and start != date_range_start)
                or (end and date_range_end and end != date_range_end)):
            raise ValueError("Conflicting project date filters. Use one start/end pair.")
        lower_text = date_range_start if date_range_start is not None else start
        upper_text = date_range_end if date_range_end is not None else end
        has_date_filter = lower_text is not None or upper_text is not None
        if has_date_filter and not fetch_all:
            raise ValueError("Project date filters require fetch_all=true so all pages can be filtered reliably.")
        if has_date_filter and page != 1:
            raise ValueError("Project date filters require page=1 to include the complete date window.")
        lower = date.fromisoformat(lower_text) if lower_text is not None else None
        upper = date.fromisoformat(upper_text) if upper_text is not None else None
        if lower and upper and lower > upper:
            raise ValueError("Project start date must be on or before the end date.")
        params = {
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
            "limit": limit,
            "page": page,
        }
        client = get_client()
        if fetch_all:
            rows = await client.paginate("/projects", params=params)
            if not has_date_filter:
                return rows
            filtered = []
            for row in rows:
                try:
                    project_date = date.fromisoformat(str(row.get("start"))[:10])
                except (AttributeError, TypeError, ValueError) as exc:
                    raise ValueError("Cannot apply project date window: a project has a missing or invalid start date.") from exc
                if (lower is None or project_date >= lower) and (upper is None or project_date <= upper):
                    filtered.append(row)
            return filtered
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
