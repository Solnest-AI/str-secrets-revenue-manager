"""Assignment tools — force-assign or cancel a cleaner on a project."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..safety import require_confirm


def register(mcp) -> None:
    @mcp.tool
    async def turno_assign_cleaner(
        project_id: int, contractor_id: int, confirm: bool = False
    ) -> Any:
        """DESTRUCTIVE (force-assign): assign/override the cleaner on a project, bypassing
        the normal offer/accept flow. Requires confirm=true. POST /assignments."""
        require_confirm(confirm, "turno_assign_cleaner")
        return await get_client().post(
            "/assignments", json={"project_id": project_id, "contractor_id": contractor_id}
        )

    @mcp.tool
    async def turno_cancel_assignment(project_id: int, confirm: bool = False) -> Any:
        """DESTRUCTIVE: cancel the cleaner assignment on a project. Requires confirm=true.
        DELETE /assignments."""
        require_confirm(confirm, "turno_cancel_assignment")
        return await get_client().delete("/assignments", json={"project_id": project_id})
