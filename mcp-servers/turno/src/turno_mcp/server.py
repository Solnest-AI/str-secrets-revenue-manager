"""FastMCP server for the Turno (TurnoverBnB) External API v2."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import register_all

mcp = FastMCP(
    name="turno",
    instructions=(
        "Tools for the Turno (formerly TurnoverBnB) short-term-rental cleaning / "
        "turnover API. Call turno_check_connection first to verify credentials and "
        "which environment is active. Destructive tools (delete/remove/disconnect/"
        "force-assign/cancel) require confirm=true. The server targets the SANDBOX "
        "environment unless TURNO_ENV=production is set."
    ),
)

register_all(mcp)


def main() -> None:
    """Console-script entry point: serve over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
