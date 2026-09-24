#!/usr/bin/env python3
"""First-run setup for the summit Revenue Manager (Hospitable + PriceLabs attendees).

    python3 fetch/setup_properties.py --markup airbnb=16 --markup vrbo=20 --dry-run
    python3 fetch/setup_properties.py --markup airbnb=16 --markup vrbo=20

The 90-day runner will not price a property without one `property_config` row that maps it
to PriceLabs (and RankBreeze, if connected) and carries the operator's channel markups. A
brand-new `str-secrets-summit` project has none. This builds them:

  1. creates the tables (migrations 001-004 from this plugin, verbatim; all idempotent)
  2. lists every listed Hospitable property (Hospitable is the source of truth for what exists)
  3. checks each one really exists in PriceLabs under the same id, PMS `smartbnb`
     (measured 2026-09-24 on 6 of 6 live listings: PriceLabs uses the Hospitable property id)
  4. finds its RankBreeze listing by Airbnb room id, if RankBreeze is connected
  5. writes one row per property, merged into any row already there

Ask the operator one question first: what markup they add per channel. A calendar-vs-PriceLabs
ratio is NOT a markup, so never infer it. Airbnb is required; other channels are optional.

Nothing here changes a price. Reads use the runner's read-only transport. The only writes are
the migration files and the property_config upsert, both to the attendee's own Supabase.

Exit 0: rows written (or, with --dry-run, shown). Unmapped properties are listed loudly.
Exit 2: cannot set up (no Supabase connection, no Hospitable or PriceLabs key, nothing mapped).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _mvp_config import SUPABASE_SERVER, Connections  # noqa: E402
from _mvp_pms import normalize_property  # noqa: E402
from _mvp_recommendations import _lit  # noqa: E402
from _mvp_sources import Sources  # noqa: E402
from _mvp_store import CannotAnalyze, ReadClient, Store  # noqa: E402

PMS_NAME = "smartbnb"  # PriceLabs' name for Hospitable
MAX_DELTA = 0.15       # PRD D8
MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CHANNEL = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class SetupError(Exception):
    """Exit 2: setup cannot produce rows it can stand behind."""


# ------------------------------------------------------------------------------ pure parts

def parse_markups(items) -> dict:
    out = {}
    for item in items or []:
        if "=" not in str(item):
            raise SetupError(f"--markup takes channel=percent, e.g. airbnb=16 (got {item!r})")
        channel, value = str(item).split("=", 1)
        channel = channel.strip().lower()
        if not _CHANNEL.match(channel):
            raise SetupError(f"{channel!r} is not a channel name")
        if channel in out:
            raise SetupError(f"{channel} given twice")
        try:
            pct = float(value)
        except ValueError:
            raise SetupError(f"{channel} markup {value!r} is not a number") from None
        if not math.isfinite(pct) or not 0 <= pct <= 500:
            raise SetupError(f"{channel} markup must be between 0 and 500 percent")
        out[channel] = pct
    if "airbnb" not in out:
        raise SetupError("The Airbnb markup is required (use --markup airbnb=0 if there is none)")
    return out


def airbnb_id(prop: dict):
    ids = [str(x.get("platform_id")) for x in prop.get("listings") or []
           if str(x.get("platform", "")).lower() == "airbnb" and x.get("platform_id")]
    return ids[0] if len(ids) == 1 else None


def match_rankbreeze(room_id, rb_listings):
    if not room_id:
        return None
    hits = [str(x["id"]) for x in rb_listings if str(x.get("room_id")) == str(room_id) and x.get("id") is not None]
    return hits[0] if len(hits) == 1 else None


def build_settings(property_id, airbnb, rankbreeze, markups, now, pms_name=PMS_NAME, pms_source="hospitable") -> dict:
    settings = {
        "pms_source": pms_source,
        "pms_name": pms_name,
        "pricelabs_listing_id": property_id,
        "max_delta_pct": MAX_DELTA,
        "channel_markup_pct": dict(markups),
        "channel_markup_source": {
            "source_type": "operator_confirmed",
            "confirmed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "note": "Stated by the operator during first-run setup. A calendar sync ratio is not a markup.",
        },
    }
    if airbnb:
        settings["airbnb_listing_id"] = airbnb
    if rankbreeze:
        settings["rankbreeze_listing_id"] = rankbreeze
    return settings


def upsert_statement(rows) -> str:
    values = []
    for r in rows:
        if not _ID.match(str(r["property_id"])):
            raise SetupError(f"{r['property_id']!r} is not a plain identifier; refusing to build SQL")
        values.append(f"({_lit(r['property_id'])}, {_lit(r['display_name'] or '')}, "
                      f"{_lit(json.dumps(r['settings'], sort_keys=True))}::jsonb)")
    return ("INSERT INTO public.property_config (property_id, display_name, settings) VALUES\n  "
            + ",\n  ".join(values)
            + "\nON CONFLICT (property_id) DO UPDATE SET display_name = EXCLUDED.display_name, "
              "settings = property_config.settings || EXCLUDED.settings, updated_at = now();")


def migration_files():
    files = sorted(MIGRATIONS.glob("0*.sql"))
    if not files:
        raise SetupError(f"No migration files found in {MIGRATIONS}; the bundle is incomplete")
    return files


# ------------------------------------------------------------------------------ I/O

def post_sql(project: str, token: str, sql: str) -> None:
    if not _ID.match(project or ""):
        raise SetupError("The Supabase project ref is unreadable")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{project}/database/query",
        data=json.dumps({"query": sql, "read_only": False}).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise SetupError(f"Supabase HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SetupError("Supabase is unreachable") from None


def rankbreeze_listings(client: ReadClient, url: str) -> list:
    """Every RankBreeze listing, paged by cursor, through the runner's read-only transport."""
    session, counter = None, 0

    def rpc(method, params):
        nonlocal session, counter
        counter += 1
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        text, resp_headers = client.request("rankbreeze", "rpc", url, headers=headers, text=True,
                                            body={"jsonrpc": "2.0", "id": counter, "method": method, "params": params})
        session = next((v for k, v in resp_headers.items() if k.lower() == "mcp-session-id"), session)
        if text.lstrip().startswith("{"):
            result = json.loads(text)
        else:
            events = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
            result = next((x for x in reversed(events) if x.get("id") == counter), {})
        if result.get("error") or "result" not in result:
            raise CannotAnalyze("RankBreeze RPC returned an error")
        return result["result"]

    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "revenue-manager-setup", "version": "1"}})
    out, cursor = [], None
    for _ in range(20):
        args = {"status": "all", "limit": 60, **({"cursor": cursor} if cursor else {})}
        result = rpc("tools/call", {"name": "get_user_listings", "arguments": args})
        texts = [x["text"] for x in result.get("content", []) if x.get("type") == "text"]
        if result.get("isError") or len(texts) != 1:
            raise CannotAnalyze("Unreadable RankBreeze listing inventory")
        raw = json.loads(texts[0])
        if not isinstance(raw.get("listings"), list):
            raise CannotAnalyze("RankBreeze listing inventory has no listings array")
        out.extend(raw["listings"])
        cursor = raw.get("nextCursor")
        if not cursor:
            return out
    raise CannotAnalyze("RankBreeze listing inventory did not finish paging")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--markup", action="append", default=[], help="channel=percent, e.g. airbnb=16 (repeat)")
    ap.add_argument("--dry-run", action="store_true", help="show the rows; write nothing")
    ap.add_argument("--pms", default="auto", help="auto (the one connected), hospitable, guesty or ownerrez")
    ap.add_argument("--env-file", action="append", default=[])
    default_cache = Path(os.environ.get("RC_CACHE_DIR", str(Path.home() / ".cache/revenue-manager")))
    ap.add_argument("--db", type=Path, default=default_cache / "workbench.sqlite3")
    args = ap.parse_args(argv)
    try:
        markups = parse_markups(args.markup)
        connections = Connections(env_files=args.env_file)
        supabase = connections.supabase()
        if not supabase:
            raise SetupError(f"No {SUPABASE_SERVER} connection. Run the connections kit's Supabase step first.")
        project, token = supabase
        connections.key("hospitable")
        connections.key("pricelabs")
        args.db.parent.mkdir(parents=True, exist_ok=True)
        client = ReadClient(Store(args.db), max_calls=400)
        from _pms_registry import choose
        pms = choose(connections, args.pms)
        sources = Sources(client, connections, pms=pms)
        inventory = (sources._pms.inventory() if sources._pms else
                     sources.pages("/properties", {"include": "listings"}, normalize_property))["data"]
        pl_names = sources.pricelabs_inventory()
        props = [p for p in inventory if p.get("listed") is not False]
        rb, rb_note = [], "RankBreeze not connected (ranking will show as a gap on each card)"
        url = connections.rankbreeze_url()
        if url:
            try:
                rb = rankbreeze_listings(client, url)
                rb_note = f"RankBreeze: {len(rb)} listing(s) found"
            except CannotAnalyze as exc:
                rb_note = f"RankBreeze could not be read ({exc}); ranking will show as a gap"
        now = datetime.now(timezone.utc)
        rows, missing = [], []
        for p in props:
            pl_pms = pl_names.get(p["id"])
            if not pl_pms:
                missing.append((p.get("name") or p["id"], "no PriceLabs listing has this PMS id"))
                continue
            try:
                sources.listing(p["id"], pl_pms)
            except CannotAnalyze as exc:
                missing.append((p.get("name") or p["id"], str(exc)))
                continue
            ab = airbnb_id(p)
            rows.append({"property_id": p["id"], "display_name": p.get("name"),
                         "settings": build_settings(p["id"], ab, match_rankbreeze(ab, rb) if rb else None, markups, now,
                                                    pms_name=pl_pms, pms_source=pms)})
        print(f"{pms.capitalize()}: {len(props)} listed propert{'y' if len(props) == 1 else 'ies'}. {rb_note}.")
        for r in rows:
            s = r["settings"]
            print(f"  ✅ {r['display_name']}: PriceLabs ✅  RankBreeze {'✅' if 'rankbreeze_listing_id' in s else '—'}  "
                  f"Airbnb id {'✅' if 'airbnb_listing_id' in s else '—'}")
        for name, why in missing:
            print(f"  ❌ {name}: NOT IN PRICELABS under the same id ({why}). The runner cannot price it.")
        if not rows:
            raise SetupError("No Hospitable property maps to a PriceLabs listing, so there is nothing to set up")
        print(f"Markups: {', '.join(f'{k} {v:g}%' for k, v in markups.items())}")
        if args.dry_run:
            print(f"DRY RUN: nothing written. {len(rows)} row(s) ready for {SUPABASE_SERVER}.")
            return 0
        for f in migration_files():
            post_sql(project, token, f.read_text())
        print(f"Tables ready ({len(migration_files())} migrations applied, all idempotent).")
        post_sql(project, token, upsert_statement(rows))
        print(f"SETUP DONE: {len(rows)} propert{'y' if len(rows) == 1 else 'ies'} configured. "
              f"Next: python3 fetch/analyze90.py --property \"Exact Property Name\"")
        return 0
    except (SetupError, CannotAnalyze, OSError) as exc:
        print(f"CANNOT SET UP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
