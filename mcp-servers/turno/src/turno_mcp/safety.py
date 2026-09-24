"""Guardrails for destructive operations.

Every destructive tool takes a ``confirm: bool = False`` argument and calls
:func:`require_confirm` before touching the API. This sits on top of the Claude
client's own per-tool permission prompts and the server's sandbox-by-default
behaviour.
"""

from __future__ import annotations


class ConfirmationRequired(RuntimeError):
    """Raised when a destructive tool is called without ``confirm=true``."""


def require_confirm(confirm: bool, action: str) -> None:
    """Block a destructive action unless explicitly confirmed."""
    if not confirm:
        raise ConfirmationRequired(
            f"DESTRUCTIVE action '{action}' was not confirmed. "
            "Re-call this tool with confirm=true to proceed."
        )
