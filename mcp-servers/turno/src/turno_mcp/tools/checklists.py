"""Checklist tools."""

from __future__ import annotations

from typing import Any

from ..client import get_client


def register(mcp) -> None:
    @mcp.tool
    async def turno_list_checklists() -> Any:
        """List all checklists on your account. GET /checklists."""
        return await get_client().get("/checklists")
