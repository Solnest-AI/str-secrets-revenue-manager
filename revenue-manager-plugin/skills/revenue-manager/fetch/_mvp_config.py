"""Read local connection settings without exposing keys or copying broad configs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from _mvp_store import CannotAnalyze, identity

# The SUMMIT build reads the connection the STR Secrets connections kit registers on
# every attendee machine (str-secrets-connections, connectors/db-supabase.md), pointed at
# their `str-secrets-summit` project. Ryan's own Solnest Stays build reads a different
# name (`supabase-solnest-stays`) so the two can never overwrite each other. No fallback.
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
}
PROVIDERS = ("hospitable", "pricelabs", "airroi", "rankbreeze", "guesty", "ownerrez", "intellihost")


def load_env(path):
    out = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.match(r"\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if match and match[1] in KEYS:
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
                self.servers = json.loads(path.read_text()).get("mcpServers", {})
            except (ValueError, OSError):
                raise CannotAnalyze("Local MCP connection configuration is unreadable") from None
        root = Path(__file__).resolve().parents[4]
        for provider in PROVIDERS:
            server = self.servers.get(provider, {})
            dirs = [root / "mcp-servers" / provider, Path.home() / ".claude/mcp-servers" / provider]
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
        # The kit's fan-out-env.sh copies each attendee's keys into this bundle's root .env
        # on summit morning (SKILL_PATH_REVENUE_MANAGER), so read it before the cwd's.
        self.values.update(load_env(root / ".env"))
        self.values.update(load_env(Path.cwd() / ".env"))
        for path in env_files:
            self.values.update(load_env(Path(path).expanduser()))
        self.values.update({k: v for k, v in os.environ.items() if k in KEYS})
        if not self.values.get("RANKBREEZE_SESSION"):
            for path in self.paths["rankbreeze"]:
                p = path / "session.txt"
                if p.is_file() and p.read_text().strip():
                    self.values["RANKBREEZE_SESSION"] = p.read_text().strip()
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
        raise CannotAnalyze("No unique property configuration for this PMS property")
    row = rows[0]
    settings = row.get("settings") or {}
    fields = (
        "pms_name",
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
        raw = json.loads(Path(settings_file).expanduser().read_text())
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
