"""Read local connection settings without exposing keys or copying broad configs."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from _mvp_store import CannotAnalyze, identity

# The SUMMIT build reads the connection the STR Secrets connections kit registers on
# every attendee machine (str-secrets-connections, connectors/db-supabase.md), pointed at
# their `str-secrets-summit` project. A private build of this engine reads a different
# connection name so the two can never overwrite each other. No fallback.
SUPABASE_SERVER = "supabase-revenue-manager"

KEYS = {
    "HOSPITABLE_API_KEY",
    "HOSPITABLE_TOKEN",
    "PRICELABS_API_KEY",
    "PRICELABS_KEY",
    "AIRROI_API_KEY",
    "RANKBREEZE_SESSION",
    "GUESTY_CLIENT_ID",
    "GUESTY_CLIENT_SECRET",
    "OWNERREZ_EMAIL",
    "OWNERREZ_TOKEN",
    "INTELLIHOST_MCP_TOKEN",
    "HOSTAWAY_ACCOUNT_ID",
    "HOSTAWAY_API_KEY",
    "LODGIFY_API_KEY",
}
PROVIDERS = ("hospitable", "pricelabs", "airroi", "rankbreeze", "guesty", "ownerrez", "intellihost",
             "hostaway", "lodgify")


def read_text(path) -> str:
    """Every local text file (.env, ~/.claude.json, settings) is read as UTF-8 and a Windows
    BOM is dropped. Without this, Notepad's BOM glued itself to the first key name and that
    key silently vanished, and the platform default (cp1252) mangled non-ASCII values."""
    return Path(path).read_text(encoding="utf-8-sig")


def utf8_console() -> None:
    """Windows consoles default to cp1252, where printing a tick raises UnicodeEncodeError
    AFTER the work is done. Every entry point calls this first."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _env_value(path, name):
    try:
        return load_env(Path(path), keys={name}).get(name)
    except OSError:
        return None


def bundle_roots(servers, here=None) -> list:
    """Where this bundle's own root `.env` can live, most specific last.

    The connections kit's fan-out-env.sh merges keys into `$SKILL_PATH_REVENUE_MANAGER/.env`.
    An installed plugin runs from ~/.claude/plugins/cache/<mkt>/revenue-manager/<ver>/..., so
    `parents[4]` of this file is the cache, not the bundle: that path only counts when it
    really is a checkout (it carries revenue-manager-plugin/). Otherwise the bundle is found
    from SKILL_PATH_REVENUE_MANAGER in the environment, or in the kit's own .env, the kit
    being located the same way server paths are: from each server's cwd/command/args in
    ~/.claude.json, walking up to the folder holding fan-out-env.sh."""
    here = Path(here or __file__).resolve()
    roots = []
    checkout = here.parents[4] if len(here.parents) > 4 else None
    if checkout and (checkout / "revenue-manager-plugin").is_dir():
        roots.append(checkout)
    kits = []
    for server in (servers or {}).values():
        if not isinstance(server, dict):
            continue
        for value in [server.get("cwd", ""), server.get("command", ""), *(server.get("args") or [])]:
            p = Path(str(value)).expanduser()
            if not p.is_absolute():
                continue
            for d in [p, *list(p.parents)[:4]]:
                if (d / "fan-out-env.sh").is_file():
                    kits.append(d)
                    break
    for kit in dict.fromkeys(kits):
        found = _env_value(kit / ".env", "SKILL_PATH_REVENUE_MANAGER")
        if found:
            roots.append(Path(found).expanduser())
    if os.environ.get("SKILL_PATH_REVENUE_MANAGER"):
        roots.append(Path(os.environ["SKILL_PATH_REVENUE_MANAGER"]).expanduser())
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


def _bearer(server):
    """An HTTP MCP server registered with a header (IntelliHost's fallback token) carries its
    credential in headers.Authorization, not in env."""
    headers = server.get("headers") if isinstance(server, dict) else None
    auth = next((v for k, v in (headers or {}).items() if str(k).lower() == "authorization"), None)
    if isinstance(auth, str) and auth.lower().startswith("bearer ") and auth[7:].strip():
        return auth[7:].strip()
    return None


def load_env(path, keys=None):
    keys = KEYS if keys is None else keys
    out = {}
    if path.is_file():
        for line in read_text(path).splitlines():
            match = re.match(r"\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if match and match[1] in keys:
                out[match[1]] = match[2].strip("\"'")
    return out


class Connections:
    def __init__(self, env_files=(), config_path=None):
        self.values = {}
        self.servers = {}
        self.paths = {}
        path = Path(config_path).expanduser() if config_path else Path.home() / ".claude.json"
        if path.is_file():
            try:
                self.servers = json.loads(read_text(path)).get("mcpServers", {})
            except (ValueError, OSError):
                raise CannotAnalyze("Local MCP connection configuration is unreadable") from None
        roots = bundle_roots(self.servers)
        for provider in PROVIDERS:
            server = self.servers.get(provider, {})
            dirs = [*(r / "mcp-servers" / provider for r in roots), Path.home() / ".claude/mcp-servers" / provider]
            for value in [
                server.get("cwd", ""),
                server.get("command", ""),
                *server.get("args", []),
            ]:
                p = Path(str(value)).expanduser()
                if p.is_absolute():
                    dirs.extend([p, *list(p.parents)[:3]])
            self.paths[provider] = list(dict.fromkeys(dirs))
            for directory in reversed(self.paths[provider]):
                self.values.update(load_env(directory / ".env"))
            self.values.update({k: v for k, v in server.get("env", {}).items() if k in KEYS})
            if provider == "intellihost" and _bearer(server):
                self.values["INTELLIHOST_MCP_TOKEN"] = _bearer(server)
        # The kit's fan-out-env.sh copies each attendee's keys into this bundle's root .env
        # on summit morning (SKILL_PATH_REVENUE_MANAGER), so read it before the cwd's.
        for root in roots:
            self.values.update(load_env(root / ".env"))
        self.values.update(load_env(Path.cwd() / ".env"))
        for path in env_files:
            self.values.update(load_env(Path(path).expanduser()))
        self.values.update({k: v for k, v in os.environ.items() if k in KEYS})
        if not self.values.get("RANKBREEZE_SESSION"):
            for path in self.paths["rankbreeze"]:
                p = path / "session.txt"
                if p.is_file() and read_text(p).strip():
                    self.values["RANKBREEZE_SESSION"] = read_text(p).strip()
                    break

    def key(self, provider):
        names = {
            "hospitable": ("HOSPITABLE_API_KEY", "HOSPITABLE_TOKEN"),
            "pricelabs": ("PRICELABS_API_KEY", "PRICELABS_KEY"),
            "airroi": ("AIRROI_API_KEY",),
            "rankbreeze": ("RANKBREEZE_SESSION",),
            "guesty": ("GUESTY_CLIENT_ID",),
            "ownerrez": ("OWNERREZ_TOKEN",),
            "intellihost": ("INTELLIHOST_MCP_TOKEN",),
            "hostaway": ("HOSTAWAY_ACCOUNT_ID",),
            "lodgify": ("LODGIFY_API_KEY",),
        }[provider]
        key = next((self.values[n] for n in names if self.values.get(n)), None)
        if not key:
            raise CannotAnalyze(
                f"Missing {names[0]}; put it in the connector .env or use --env-file"
            )
        return key

    def account(self, provider):
        # Cache isolation across operators without persisting the credential itself.
        return identity([provider, self.key(provider)])

    def account_or(self, provider):
        """Like account(), but for a provider reached through a cached token (Guesty's
        shared cache) when no key is configured here: isolate by provider alone."""
        try:
            return self.account(provider)
        except CannotAnalyze:
            return identity([provider, "cached-token"])

    def supabase(self):
        server = self.servers.get(SUPABASE_SERVER, {})
        args = " ".join(server.get("args", []))
        match = re.search(r"--project-ref(?:=|\s+)([a-z0-9]+)", args)
        token = server.get("env", {}).get("SUPABASE_ACCESS_TOKEN")
        return (match[1], token) if match and token else None

    def rankbreeze_url(self):
        return self.servers.get("rankbreeze", {}).get("url")


def normalized_context(raw, property_id):
    rows = raw.get("config") or []
    if len(rows) != 1 or str(rows[0].get("property_id")) != property_id:
        raise CannotAnalyze("No property_config row for this PMS property; run fetch/setup_properties.py "
                            "first (with --pms guesty or --pms ownerrez if that is your PMS)")
    row = rows[0]
    settings = row.get("settings") or {}
    fields = (
        "pms_source",
        "pms_name",
        "pricing_gap",
        "intellihost_property_id",
        "pricelabs_listing_id",
        "rankbreeze_listing_id",
        "airbnb_listing_id",
        "channel_markup_pct",
        "channel_markup_source",
        "max_delta_pct",
    )
    return {
        "property_id": property_id,
        "settings": {k: settings[k] for k in fields if k in settings},
        "updated_at": row.get("updated_at"),
        "changes": raw.get("changes") or [],
        "decisions": raw.get("decisions") or [],
    }


def read_context(client, connections, property_id, settings_file=None):
    if settings_file:
        raw = json.loads(read_text(Path(settings_file).expanduser()))
        if str(raw.get("property_id")) != property_id:
            raise CannotAnalyze("Settings file belongs to another PMS property")
        return normalized_context({"config": [raw]}, property_id)
    connection = connections.supabase()
    if not connection:
        raise CannotAnalyze(
            f"Configure {SUPABASE_SERVER} or pass --settings with confirmed markups"
        )
    project, token = connection
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", property_id):
        raise CannotAnalyze("Invalid PMS property ID")
    query = f"""SELECT json_build_object(
        'config',(SELECT json_agg(x) FROM (SELECT property_id,settings,updated_at
            FROM property_config WHERE property_id='{property_id}') x),
        'changes',(SELECT json_agg(x) FROM (SELECT change_type,field_changed,
            old_value,new_value,created_at FROM pricelabs_change_log
            WHERE listing_id='{property_id}' ORDER BY created_at DESC LIMIT 20) x),
        'decisions',(SELECT json_agg(x) FROM (SELECT decision_date,strategy,
            base_price,final_price,outcome FROM pricing_decisions
            WHERE property_id='{property_id}' ORDER BY decision_date DESC LIMIT 5) x)
        ) AS context"""

    def load():
        raw, _ = client.request(
            "supabase",
            "context",
            f"https://api.supabase.com/v1/projects/{project}/database/query",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            body={"query": query, "read_only": True},
        )
        if (
            not isinstance(raw, list)
            or len(raw) != 1
            or not isinstance(raw[0].get("context"), dict)
        ):
            raise CannotAnalyze("Supabase did not return the requested property context")
        return normalized_context(raw[0]["context"], property_id)

    return client.fetch("context", [project, property_id], load)
