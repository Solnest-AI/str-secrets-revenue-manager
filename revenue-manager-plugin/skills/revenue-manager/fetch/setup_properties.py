#!/usr/bin/env python3
"""First-run setup for the summit Revenue Manager (any of the 8 PMSs, with PriceLabs, Beyond or PMS pricing).

    python3 fetch/setup_properties.py --markup airbnb=16 --markup vrbo=20 --dry-run
    python3 fetch/setup_properties.py --markup airbnb=16 --markup vrbo=20
    python3 fetch/setup_properties.py --markup airbnb=16 --min-price "Lake House=140"
    python3 fetch/setup_properties.py --markup airbnb=16 --pricing beyond   (Beyond users)

The 90-day runner will not price a property without one `property_config` row that maps it
to PriceLabs (and RankBreeze, if connected) and carries the operator's channel markups. A
brand-new `str-secrets-summit` project has none. This builds them:

  1. creates the tables (migrations 001-004 from this plugin, verbatim; all idempotent)
  2. lists every listed Hospitable property (Hospitable is the source of truth for what exists)
  3. checks each one really exists in PriceLabs under the same id, PMS `smartbnb`
     (measured 2026-09-24 on 6 of 6 live listings: PriceLabs uses the Hospitable property id),
     or, with Beyond as the pricing tool (--pricing beyond, or auto when only Beyond is
     connected), finds its Beyond listing: the PMS id on a channel listing named for the PMS,
     else the Airbnb room id on an `airbnb` channel listing, else the exact name. A unique match
     or nothing; two candidates is "not guessed". Stored as settings.beyond_listing_id
     (DOCS-ONLY: Beyond's `channel-listings`, references/beyond.md)
  4. finds its RankBreeze listing by Airbnb room id, if RankBreeze is connected
  5. writes one row per property, merged into any row already there

Ask the operator one question first: what markup they add per channel. A calendar-vs-PriceLabs
ratio is NOT a markup, so never infer it. Airbnb is required; other channels are optional.

Nothing here changes a price. Reads use the runner's read-only transport. The only writes are
the migration files and the property_config upsert, both to the attendee's own Supabase.

Each row also records who owns the nightly prices, `pricing_tool`: "pricelabs" when the
property maps to a PriceLabs listing, "beyond" when Beyond sets them (--pricing beyond, or
auto with only BEYOND_TOKEN connected; --pricing-tool is the same flag), else null (the PMS owns
them). A Beyond property that cannot be mapped, or a Beyond user with no BEYOND_TOKEN, is still
written as "beyond" with a named pricing_gap, so no PMS price write slips through.
The PMS calendar writer refuses a PMS price write while a pricing tool owns the listing. An
optional operator floor, `min_price` (--min-price "<property id or exact name>=<amount>", repeat
per property), is the floor the calendar writer uses when the PMS reports none; without any
floor it refuses price cuts. A property not named keeps whatever min_price it already has.

Exit 0: rows written (or, with --dry-run, shown). Unmapped properties are listed loudly.
Exit 2: cannot set up (no Supabase connection, no key for the chosen PMS, nothing mapped).
No PriceLabs key is NOT a stop (a Beyond user may have none): rows are written with a named
`pricing_gap`, and the 90-day runner says so instead of pricing.
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

from _mvp_config import SUPABASE_SERVER, Connections, utf8_console  # noqa: E402
from _mvp_pms import normalize_property  # noqa: E402
from _mvp_recommendations import _lit  # noqa: E402
from _mvp_sources import Sources  # noqa: E402
from _mvp_store import CannotAnalyze, ReadClient, Store  # noqa: E402
from _beyond import BeyondSource  # noqa: E402

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


def parse_min_prices(items) -> dict:
    """{property id or exact name: floor in the property's own currency}, from
    --min-price "<property>=<amount>". Never a portfolio-wide number: floors are per property."""
    out = {}
    for item in items or []:
        if "=" not in str(item):
            raise SetupError(f'--min-price takes "<property id or exact name>=<amount>" (got {item!r})')
        who, value = str(item).rsplit("=", 1)
        who = who.strip()
        if not who:
            raise SetupError(f"--min-price {item!r} names no property")
        if who.casefold() in {k.casefold() for k in out}:
            raise SetupError(f"--min-price given twice for {who}")
        try:
            amount = float(value)
        except ValueError:
            raise SetupError(f"--min-price for {who}: {value!r} is not a number") from None
        if not math.isfinite(amount) or amount <= 0:
            raise SetupError(f"--min-price for {who} must be above zero")
        out[who] = amount
    return out


def match_min_prices(min_prices: dict, props: list) -> dict:
    """{property id: floor}. Every name given must match exactly one property: a floor that
    silently lands nowhere is a floor the operator thinks they have and do not."""
    out = {}
    for who, amount in min_prices.items():
        hits = [p for p in props if str(p.get("id")) == who
                or str(p.get("name") or "").casefold() == who.casefold()]
        if len(hits) != 1:
            raise SetupError(f"--min-price {who!r} matches {len(hits)} listed properties; use the exact "
                             "property id or name")
        out[str(hits[0]["id"])] = amount
    return out


def pricing_tool_for(mapped_to_pricelabs: bool, stated: str = "auto"):
    """PriceLabs when the property maps to a PriceLabs listing; Beyond when the operator says so;
    otherwise None (the PMS owns the prices)."""
    if mapped_to_pricelabs:
        return "pricelabs"
    return "beyond" if stated == "beyond" else None


def airbnb_id(prop: dict):
    ids = [str(x.get("platform_id")) for x in prop.get("listings") or []
           if str(x.get("platform", "")).lower() == "airbnb" and x.get("platform_id")]
    return ids[0] if len(ids) == 1 else None


def match_rankbreeze(room_id, rb_listings):
    if not room_id:
        return None
    hits = [str(x["id"]) for x in rb_listings if str(x.get("room_id")) == str(room_id) and x.get("id") is not None]
    return hits[0] if len(hits) == 1 else None


PRICELABS_GAP = ("No pricing tool (PriceLabs or Beyond) is connected, so this property has no pricing-tool "
                 "mapping; the 90-day runner cannot price it until one is connected and "
                 "setup_properties.py is run again")
BEYOND_UNCONNECTED_GAP = ("Beyond sets this property's prices, but BEYOND_TOKEN is not connected, so the "
                          "90-day runner cannot read Beyond; connect it and run setup_properties.py "
                          "--pricing beyond again")


def _name(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def match_beyond(prop: dict, listings: list, pms: str):
    """(Beyond listing id, how it matched) or (None, why not). Tried strongest first; a tier
    with two or more candidates STOPS the search ("not guessed") instead of falling through to
    a weaker one. Listings are BeyondSource.listings() rows (channels = channel-listings)."""
    def on(channel, value):
        return [x for x in listings if value and any(
            str(c.get("channel", "")).lower() == channel and str(c.get("channel_id")) == str(value)
            for c in x.get("channels") or [])]
    names = {_name(prop.get("name")), _name(prop.get("public_name"))} - {""}
    for how, hits in ((f"{PMS_LABEL.get(pms, pms)} id", on(pms, prop.get("id"))),
                      ("Airbnb id", on("airbnb", airbnb_id(prop))),
                      ("exact name", [x for x in listings if _name(x.get("name")) in names])):
        if len(hits) == 1:
            return str(hits[0]["id"]), how
        if hits:
            return None, f"{len(hits)} Beyond listings share its {how}; not guessed"
    return None, "no Beyond listing has its PMS id, Airbnb id or exact name"


def build_settings(property_id, airbnb, rankbreeze, markups, now, pms_name=PMS_NAME, pms_source="hospitable",
                   intellihost=None, pricelabs=True, pricing_tool="auto", min_price=None, beyond=None,
                   gap=None) -> dict:
    """`beyond` is the mapped Beyond listing id (the runner reads Beyond through it). `gap`
    names why a property the operator prices in Beyond has no mapping yet."""
    tool = "beyond" if beyond else pricing_tool_for(pricelabs, pricing_tool)
    settings = {
        "pms_source": pms_source,
        # Always written (null when mapped) so a re-run after connecting a pricing tool clears
        # it: the upsert MERGES settings, it never drops a key.
        "pricing_gap": None if (pricelabs or beyond) else (
            gap or (BEYOND_UNCONNECTED_GAP if tool == "beyond" else PRICELABS_GAP)),
        # Always written for the same reason: the calendar writer reads it to refuse PMS price
        # writes that the pricing tool would overwrite on its next sync. The runner's
        # --pricing auto follows it.
        "pricing_tool": tool,
        "max_delta_pct": MAX_DELTA,
        "channel_markup_pct": dict(markups),
        "channel_markup_source": {
            "source_type": "operator_confirmed",
            "confirmed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "note": "Stated by the operator during first-run setup. A calendar sync ratio is not a markup.",
        },
    }
    if pricelabs:
        settings["pms_name"] = pms_name
        settings["pricelabs_listing_id"] = property_id
    if airbnb:
        settings["airbnb_listing_id"] = airbnb
    if rankbreeze:
        settings["rankbreeze_listing_id"] = rankbreeze
    if intellihost:
        settings["intellihost_property_id"] = intellihost
    if beyond:
        settings["beyond_listing_id"] = str(beyond)
    if min_price is not None:
        settings["min_price"] = float(min_price)
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
        if exc.code == 401:
            raise SetupError("Supabase HTTP 401: the Supabase access token expired or wrong; "
                             "re-run the connections kit's Supabase step") from None
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


PMS_LABEL = {"hospitable": "Hospitable", "guesty": "Guesty", "ownerrez": "OwnerRez"}
PRICE_OWNER = {"pricelabs": "PriceLabs", "beyond": "Beyond"}


def main(argv=None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--markup", action="append", default=[], help="channel=percent, e.g. airbnb=16 (repeat)")
    ap.add_argument("--dry-run", action="store_true", help="show the rows; write nothing")
    ap.add_argument("--min-price", action="append", default=[],
                    help='operator floor per property: "<property id or exact name>=<amount>" (repeat)')
    ap.add_argument("--pricing", "--pricing-tool", dest="pricing", choices=("auto", "pricelabs", "beyond"),
                    default="auto", help="who sets the prices: auto (PriceLabs if connected, else Beyond if "
                    "connected, else the PMS), pricelabs, or beyond (maps each property to its Beyond listing)")
    from _pms_registry import SUPPORTED as PMS_SUPPORTED
    ap.add_argument("--pms", default="auto",
                    help="auto (the one connected; required when two PMSs are connected), or one of: "
                         + ", ".join(PMS_SUPPORTED))
    ap.add_argument("--env-file", action="append", default=[])
    default_cache = Path(os.environ.get("RC_CACHE_DIR", str(Path.home() / ".cache/revenue-manager")))
    ap.add_argument("--db", type=Path, default=default_cache / "workbench.sqlite3")
    args = ap.parse_args(argv)
    try:
        markups = parse_markups(args.markup)
        min_prices = parse_min_prices(args.min_price)
        connections = Connections(env_files=args.env_file)
        supabase = connections.supabase()
        if not supabase:
            raise SetupError(f"No {SUPABASE_SERVER} connection. Run the connections kit's Supabase step first.")
        project, token = supabase
        from _pms_registry import choose
        pms = choose(connections, args.pms)
        label = PMS_LABEL.get(pms, pms)
        # Only the chosen PMS's credentials are required. Guesty may run on the kit's cached
        # token and OwnerRez checks its own pair in the adapter; both name themselves.
        if pms == "hospitable":
            connections.key("hospitable")
        has = {}
        for tool_name in ("pricelabs", "beyond"):
            try:
                connections.key(tool_name)
                has[tool_name] = True
            except CannotAnalyze:
                has[tool_name] = False
        pricing = args.pricing
        if pricing == "auto":
            pricing = "pricelabs" if has["pricelabs"] else "beyond" if has["beyond"] else None
        elif pricing == "pricelabs" and not has["pricelabs"]:
            raise SetupError("--pricing pricelabs, but no PRICELABS_API_KEY is set")
        has_pricelabs = pricing == "pricelabs"
        maps_beyond = pricing == "beyond" and has["beyond"]
        args.db.parent.mkdir(parents=True, exist_ok=True)
        client = ReadClient(Store(args.db), max_calls=400)
        sources = Sources(client, connections, pms=pms)
        inventory = (sources._pms.inventory() if sources._pms else
                     sources.pages("/properties", {"include": "listings"}, normalize_property))["data"]
        pl_names = sources.pricelabs_inventory() if has_pricelabs else {}
        beyond_rows = BeyondSource(client, connections).listings() if maps_beyond else []
        props = [p for p in inventory if p.get("listed") is not False]
        floors = match_min_prices(min_prices, props)
        rb, rb_note = [], "RankBreeze not connected (ranking will show as a gap on each card)"
        url = connections.rankbreeze_url()
        if url:
            try:
                rb = rankbreeze_listings(client, url)
                rb_note = f"RankBreeze: {len(rb)} listing(s) found"
            except CannotAnalyze as exc:
                rb_note = f"RankBreeze could not be read ({exc}); ranking will show as a gap"
        ih_map, ih_note = {}, None
        try:
            connections.key("intellihost")
            from _rank_intellihost import IntelliHostSource
            ih_map = IntelliHostSource(client, connections).airbnb_map()
            ih_note = f"IntelliHost: {len(ih_map)} listing(s) found"
        except CannotAnalyze as exc:
            from _rank_intellihost import PREMIUM_GAP
            ih_note = (None if "Missing" in str(exc) else
                       "IntelliHost is connected, but reading it needs IntelliHost Premium; ranking comes from "
                       "RankBreeze or shows as a named gap" if str(exc) == PREMIUM_GAP else f"IntelliHost could not be read ({exc})")
        now = datetime.now(timezone.utc)
        rows, missing = [], []
        for p in props:
            pl_pms, bid, how, unmapped = None, None, None, None
            if maps_beyond:
                bid, how = match_beyond(p, beyond_rows, pms)
                if not bid:
                    # still written as Beyond-priced (so no PMS price write goes through), with
                    # the reason the runner cannot read it yet
                    unmapped = (f"Beyond sets this property's prices, but it could not be matched to a "
                                f"Beyond listing ({how}); fix the name or channel link in Beyond and "
                                "run setup_properties.py --pricing beyond again")
                    missing.append((p.get("name") or p["id"], how))
            if has_pricelabs:
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
                                                    pms_name=pl_pms, pms_source=pms, intellihost=ih_map.get(ab or ""),
                                                    pricelabs=has_pricelabs,
                                                    pricing_tool="beyond" if pricing == "beyond" else "auto",
                                                    min_price=floors.get(str(p["id"])), beyond=bid, gap=unmapped),
                         "match": how})
        print(f"{label}: {len(props)} listed propert{'y' if len(props) == 1 else 'ies'}. {rb_note}."
              + (f" {ih_note}." if ih_note else ""))
        if not pricing:
            print("  ⚠️  No pricing tool (PriceLabs or Beyond) is connected: pricing-tool mapping is a named gap "
                  "on every property below (the 90-day runner cannot price until one is connected and setup "
                  "is run again).")
        elif pricing == "beyond" and not maps_beyond:
            print("  ⚠️  Beyond sets the prices but BEYOND_TOKEN is not connected: every property below is marked "
                  "Beyond-priced (no PMS price write goes through) and the 90-day runner cannot read Beyond "
                  "until the token is connected and setup is run again.")
        elif pricing == "pricelabs" and has["beyond"]:
            print("  Beyond is connected too; this setup maps PriceLabs. Run with --pricing beyond to price "
                  "through Beyond instead.")
        tool_label = {"pricelabs": "PriceLabs", "beyond": "Beyond"}.get(pricing, "Pricing tool")
        for r in rows:
            s = r["settings"]
            mapped = has_pricelabs or "beyond_listing_id" in s
            mark = (f"✅ ({r['match']})" if r.get("match") and mapped else "✅") if mapped else "— (gap)"
            print(f"  {'✅' if mapped or not pricing else '❌'} {r['display_name']}: {tool_label} {mark}  "
                  f"RankBreeze {'✅' if 'rankbreeze_listing_id' in s else '—'}  "
                  f"IntelliHost {'✅' if 'intellihost_property_id' in s else '—'}  "
                  f"Airbnb id {'✅' if 'airbnb_listing_id' in s else '—'}  "
                  f"Prices set by {PRICE_OWNER.get(s['pricing_tool'], label)}"
                  + (f"  Min {s['min_price']:g}" if "min_price" in s else ""))
        for name, why in missing:
            if pricing == "beyond":
                print(f"  ❌ {name}: NOT MAPPED TO BEYOND ({why}). Marked Beyond-priced; the runner cannot "
                      "read it until it is mapped.")
            else:
                print(f"  ❌ {name}: NOT IN PRICELABS under the same {label} id ({why}). The runner cannot price it.")
        if not rows:
            raise SetupError(f"No {label} property maps to a PriceLabs listing, so there is nothing to set up"
                             if has_pricelabs else f"{label} returned no listed property, so there is nothing to set up")
        print(f"Markups: {', '.join(f'{k} {v:g}%' for k, v in markups.items())}")
        if args.dry_run:
            print(f"DRY RUN: nothing written. {len(rows)} row(s) ready for {SUPABASE_SERVER}.")
            return 0
        for f in migration_files():
            post_sql(project, token, f.read_text(encoding="utf-8-sig"))
        print(f"Tables ready ({len(migration_files())} migrations applied, all idempotent).")
        post_sql(project, token, upsert_statement(rows))
        print(f"SETUP DONE: {len(rows)} propert{'y' if len(rows) == 1 else 'ies'} configured. "
              f"Next: uv run --python 3.13 python fetch/analyze90.py --property \"Exact Property Name\"")
        return 0
    except (SetupError, CannotAnalyze, OSError) as exc:
        print(f"CANNOT SET UP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
