"""Which PMS the runner reads, and how to tell which one an attendee has connected.

The runner's analysis consumes one normalized shape (see _mvp_pms). Hospitable is read by
_mvp_sources directly; every other PMS is an adapter module returning that same shape.
"""

from __future__ import annotations

import os
from pathlib import Path

from _mvp_store import CannotAnalyze

SUPPORTED = ("hospitable", "guesty", "ownerrez")


def _has_guesty(connections) -> bool:
    if connections.values.get("GUESTY_CLIENT_ID") or os.environ.get("GUESTY_TOKEN_CACHE"):
        return True
    return any((Path(d) / ".cache" / "guesty.token").is_file()
               for d in (connections.paths or {}).get("guesty", []))


def connected(connections) -> list:
    out = []
    if connections.values.get("HOSPITABLE_API_KEY") or connections.values.get("HOSPITABLE_TOKEN"):
        out.append("hospitable")
    if _has_guesty(connections):
        out.append("guesty")
    if connections.values.get("OWNERREZ_TOKEN") and connections.values.get("OWNERREZ_EMAIL"):
        out.append("ownerrez")
    return out


def choose(connections, requested: str = "auto") -> str:
    if requested and requested != "auto":
        if requested not in SUPPORTED:
            raise CannotAnalyze(f"{requested} is not a PMS the runner reads yet ({', '.join(SUPPORTED)}); "
                                "the skill works from its connected tools instead")
        return requested
    found = connected(connections)
    if len(found) == 1:
        return found[0]
    if not found:
        raise CannotAnalyze("No PMS connection found for the runner (Hospitable, Guesty or OwnerRez)")
    raise CannotAnalyze(f"More than one PMS is connected ({', '.join(found)}); pass --pms to choose")


def adapter(pms: str, client, connections):
    if pms == "guesty":
        from _pms_guesty import GuestySource
        return GuestySource(client, connections)
    if pms == "ownerrez":
        from _pms_ownerrez import OwnerRezSource
        return OwnerRezSource(client, connections)
    return None
