"""Problem tools — issues cleaners report during a turnover."""

from __future__ import annotations

from typing import Any

from ..client import get_client


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_problems(
        property_id: int | None = None,
        status: str | None = None,
        limit: int = 15,
        page: int = 1,
        fetch_all: bool = False,
    ) -> Any:
        """List problems reported by cleaners. Filter by property_id and status
        (e.g. 'unresolved'). fetch_all=true walks all pages. GET /problems."""
        params = {"property-id": property_id, "status": status, "limit": limit, "page": page}
        client = get_client()
        if fetch_all:
            return await client.paginate("/problems", params=params)
        return await client.get("/problems", params=params)

    @mcp.tool
    async def turno_create_problem(data: dict[str, Any]) -> Any:
        """Create a problem report. `data` is the JSON body (e.g. project_id/property_id,
        description, status). POST /problems."""
        return await get_client().post("/problems", json=data)

    @mcp.tool
    async def turno_update_problem(problem_id: int, data: dict[str, Any]) -> Any:
        """Update a problem (e.g. mark resolved). `data` holds fields to change. PATCH /problems/{id}."""
        return await get_client().patch(f"/problems/{problem_id}", json=data)
