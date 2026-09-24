"""Shared helpers for tool modules."""

from __future__ import annotations

from typing import Any, Iterable


def csv(values: Iterable[Any] | None) -> str | None:
    """Join an iterable into a comma-separated string.

    Turno uses comma-joined list params (``properties=1,2,3``) on the projects
    and reviews endpoints. Returns ``None`` for ``None`` so the client drops it.
    """
    if values is None:
        return None
    return ",".join(str(v) for v in values)
