"""Review tools — cleaner reviews."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ._common import csv


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_reviews(
        properties: list[int] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> Any:
        """List cleaner reviews. Filter by a properties id list and a date range
        ('YYYY-MM-DD'). GET /reviews."""
        params = {"properties": csv(properties), "start": start, "end": end}
        return await get_client().get("/reviews", params=params)
