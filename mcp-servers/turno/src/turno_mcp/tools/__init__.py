"""Tool modules for the Turno MCP server.

Each module exposes ``register(mcp)`` which defines its tools on the shared
FastMCP instance. ``register_all`` wires them all up.
"""

from __future__ import annotations

from . import (
    assignments,
    blocked_dates,
    bookings,
    checklists,
    cleaners,
    diagnostics,
    problems,
    projects,
    properties,
    reviews,
    webhooks,
)

_MODULES = (
    projects,
    properties,
    cleaners,
    bookings,
    assignments,
    blocked_dates,
    problems,
    checklists,
    reviews,
    webhooks,
    diagnostics,
)


def register_all(mcp) -> None:
    for module in _MODULES:
        module.register(mcp)
