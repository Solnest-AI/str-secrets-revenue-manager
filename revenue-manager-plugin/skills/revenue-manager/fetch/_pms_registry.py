"""Which PMS the runner reads, and how to tell which one an attendee has connected.

The runner's analysis consumes one normalized shape (see _mvp_pms). Hospitable is read by
_mvp_sources directly; every other PMS is an adapter module returning that same shape.
"""

from __future__ import annotations

import os
from pathlib import Path

from _mvp_store import CannotAnalyze

SUPPORTED = ("hospitable", "guesty", "ownerrez", "hostaway", "lodgify", "uplisting", "smoobu", "hostfully")


def _has_guesty(connections) -> bool:
    """Credentials, or a token cache that still holds a FRESH token. Live 2026-09-25: a bare
    cache FILE counted, expired or not, so a Hospitable operator who once tried Guesty had
    every card stop with "More than one PMS is connected (hospitable, guesty)"."""
    from _pms_guesty import token_from_cache
    if connections.values.get("GUESTY_CLIENT_ID"):
        return True
    caches = [os.environ["GUESTY_TOKEN_CACHE"]] if os.environ.get("GUESTY_TOKEN_CACHE") else []
    caches += [Path(d) / ".cache" / "guesty.token" for d in (connections.paths or {}).get("guesty", [])]
    return any(token_from_cache(Path(c).expanduser()) for c in caches)


def connected(connections) -> list:
    out = []
    if connections.values.get("HOSPITABLE_API_KEY") or connections.values.get("HOSPITABLE_TOKEN"):
        out.append("hospitable")
    if _has_guesty(connections):
        out.append("guesty")
    if connections.values.get("OWNERREZ_TOKEN") and connections.values.get("OWNERREZ_EMAIL"):
        out.append("ownerrez")
    if connections.values.get("HOSTAWAY_ACCOUNT_ID") and connections.values.get("HOSTAWAY_API_KEY"):
        out.append("hostaway")
    if connections.values.get("LODGIFY_API_KEY"):
        out.append("lodgify")
    if connections.values.get("UPLISTING_API_KEY"):
        out.append("uplisting")
    if connections.values.get("SMOOBU_API_KEY") and connections.values.get("SMOOBU_API_SECRET"):
        out.append("smoobu")  # both: every Smoobu call is HMAC-signed with the secret
    if connections.values.get("HOSTFULLY_API_KEY") and connections.values.get("HOSTFULLY_AGENCY_UID"):
        out.append("hostfully")
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
        raise CannotAnalyze(f"No PMS connection found for the runner ({', '.join(SUPPORTED)})")
    raise CannotAnalyze(f"More than one PMS is connected ({', '.join(found)}); pass --pms to choose")


def adapter(pms: str, client, connections):
    if pms == "guesty":
        from _pms_guesty import GuestySource
        return GuestySource(client, connections)
    if pms == "ownerrez":
        from _pms_ownerrez import OwnerRezSource
        return OwnerRezSource(client, connections)
    if pms == "hostaway":
        from _pms_hostaway import HostawaySource
        return HostawaySource(client, connections)
    if pms == "lodgify":
        from _pms_lodgify import LodgifySource
        return LodgifySource(client, connections)
    if pms == "uplisting":
        from _pms_uplisting import UplistingSource
        return UplistingSource(client, connections)
    if pms == "smoobu":
        from _pms_smoobu import SmoobuSource
        return SmoobuSource(client, connections)
    if pms == "hostfully":
        from _pms_hostfully import HostfullySource
        return HostfullySource(client, connections)
    return None
