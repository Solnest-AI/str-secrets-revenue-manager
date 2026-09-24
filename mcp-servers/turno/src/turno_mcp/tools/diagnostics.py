"""Diagnostic tool — verify connectivity and credentials."""

from __future__ import annotations

from typing import Any

from ..client import get_client
from ..config import ConfigError
from ..errors import TurnoAPIError


def register(mcp) -> None:
    @mcp.tool
    async def turno_check_connection() -> dict[str, Any]:
        """Verify Turno API connectivity and credentials with a lightweight read.

        Reports the active environment and base URL. Use this first to confirm
        setup before calling other tools. Never raises — returns {ok: false, ...}
        on failure so you can see exactly what's wrong.
        """
        try:
            client = get_client()
        except ConfigError as exc:
            return {"ok": False, "error": str(exc)}

        cfg = client.config
        try:
            data = await client.get("/properties", params={"limit": 1})
        except TurnoAPIError as exc:
            return {
                "ok": False,
                "env": cfg.env,
                "root_url": cfg.root_url,
                "status_code": exc.status_code,
                "error": str(exc),
            }
        sample = len(data.get("items", [])) if isinstance(data, dict) else 0
        return {
            "ok": True,
            "env": cfg.env,
            "root_url": cfg.root_url,
            "message": f"Connected to Turno ({cfg.env}). Sample properties returned: {sample}.",
        }
