#!/usr/bin/env python3
"""One metered, read-only 90-day revenue analysis. Python 3.11+, stdlib only.

Run from the skill directory:
    python3 fetch/analyze90.py --property "Property Name"
    python3 fetch/analyze90.py --show RUN_ID --details

Default stdout is compact. Complete normalized evidence and daily calculations are
saved in a private SQLite workbench outside the plugin. No provider writes or LLM API
calls are made. Unreadable evidence produces a saved blocked run and exit status 2.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from _mvp_analysis import build, render
from _mvp_config import Connections, read_context
from _mvp_pms import analyze
from _mvp_sources import MarketRolledOver, Sources
from _mvp_store import CannotAnalyze, ReadClient, Store, encode, utc_now


def local_date(prop, as_of):
    value = prop.get("timezone")
    try:
        if isinstance(value, str) and value[:1] in {"-", "+"}:
            clean = value.replace(":", "")
            offset = timedelta(hours=int(clean[1:3]), minutes=int(clean[3:5]))
            tz = timezone(-offset if clean[0] == "-" else offset)
        else:
            tz = ZoneInfo(value)
        return as_of.astimezone(tz).date()
    except (ValueError, TypeError, KeyError):
        raise CannotAnalyze("PMS property timezone is missing or unreadable") from None


def compute(inputs, as_of, start, days):
    # PRD D12 (2026-09-20): reviews is a CONTEXT spoke, not a required input. Missing
    # or unreadable reviews fail that spoke and degrade the run; they do not block it.
    # The inputs listed here are the ones without which there is nothing to price.
    required = (
        "property",
        "calendar",
        "reservations",
        "context",
        "listing",
        "prices",
        "market",
        "overrides",
        "rules",
    )
    missing = [name for name in required if name not in inputs]
    if missing:
        return {
            "status": "blocked",
            "blockers": ["Unreadable required sources: " + ", ".join(missing)],
        }
    reservations = inputs["reservations"]
    if not reservations.get("complete") or len(reservations["data"]) != reservations.get("total"):
        return {"status": "blocked", "blockers": ["Reservation pagination is incomplete"]}
    reviews_in = inputs.get("reviews") or {}
    # "Unreadable" is still not "zero reviews": an empty sample with a non-zero total
    # means the fetch did not deliver what exists. That used to block here, before the
    # flywheel ran. It now fails the reviews SPOKE, which the gate degrades (D12).
    reviews_unreadable = ("reviews" not in inputs
                          or (not reviews_in.get("data") and reviews_in.get("total") != 0))
    pms = analyze(
        inputs["property"],
        inputs["calendar"],
        reservations["data"],
        reviews_in.get("data") or [],
        start,
        days,
        as_of,
    )
    pms["reviews"]["all_time_coverage_verified"] = reviews_in.get("complete", False)
    pms["reviews"]["total_reported"] = reviews_in.get("total")
    pms["reviews"]["unreadable_sample"] = reviews_unreadable
    result = build(
        pms,
        inputs["listing"],
        inputs["prices"],
        inputs["market"],
        inputs["overrides"],
        inputs["rules"],
        inputs.get("funnel", {"status": "skipped"}),
        inputs.get("rankings", []),
        inputs["context"],
        as_of,
        pile=inputs.get("pile"),
    )
    result["named_comps"] = inputs.get("comps", {"status": "unavailable"})
    return result


def market_start(probe, start):
    """Start tomorrow, and say so, when PriceLabs' market data has already rolled over.

    After UTC midnight the property can still be on today while PriceLabs' market data
    starts tomorrow. Refusing then blocks every evening run (5pm Pacific onwards). Only
    the exact rollover signature moves the start; any other market failure is left for
    the market job to report, and the window never moves silently."""
    try:
        probe(start)
    except MarketRolledOver:
        nxt = start + timedelta(days=1)
        return nxt, (f"Tonight ({start.isoformat()}) is not analysed: PriceLabs' market data has "
                     f"already moved to {nxt.isoformat()} (UTC midnight), so the window starts "
                     f"{nxt.isoformat()}.")
    except (CannotAnalyze, ValueError, KeyError, TypeError):
        pass
    return start, None


def run_live(args, client, connections, as_of):
    sources = Sources(client, connections)
    prop = sources.property(args.property)
    start = local_date(prop, as_of)
    if args.start:
        requested = date.fromisoformat(args.start)
        if requested != start:
            raise CannotAnalyze(
                "Live analysis starts on the property local current date; use replay for prior runs"
            )
    pid = prop["id"]
    context = read_context(client, connections, pid, args.settings)
    settings = context["settings"]
    lid = str(settings.get("pricelabs_listing_id") or pid)
    pms_name = settings.get("pms_name") or "smartbnb"
    rid = str(settings.get("rankbreeze_listing_id") or "")
    inputs = {"property": prop, "context": context}
    errors = []
    start, rollover_note = market_start(
        lambda s: sources.neighborhood(lid, pms_name, prop["capacity"]["bedrooms"],
                                       prop["currency"], s, args.days, args.refresh_context),
        start)
    if rollover_note:
        errors.append(rollover_note)
    jobs = {
        "calendar": lambda: sources.calendar(pid, start, args.days),
        "reservations": lambda: sources.reservations(pid, start, args.days),
        "reviews": lambda: sources.reviews(pid),
        "listing": lambda: sources.listing(lid, pms_name),
        "prices": lambda: sources.prices(lid, pms_name, prop["currency"], start, args.days),
        "overrides": lambda: sources.overrides(lid, pms_name, start, args.days),
        "rules": lambda: sources.rules(lid, pms_name),
        # D14: the pile, every run. One input, never the basis.
        "pile": lambda: sources.pile(lid, pms_name),
        "market": lambda: sources.neighborhood(
            lid,
            pms_name,
            prop["capacity"]["bedrooms"],
            prop["currency"],
            start,
            args.days,
            args.refresh_context,
        ),
    }
    if rid:
        jobs["funnel"] = lambda: sources.funnel(rid, start)
        jobs["rankings"] = lambda: sources.rankings(rid, start, prop["capacity"]["max"])
    else:
        errors.append("No verified RankBreeze listing mapping")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(loader): name for name, loader in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                inputs[name] = future.result()
            except (CannotAnalyze, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{name}: {exc}")
    if "listing" in inputs:
        try:
            from _mvp_comps import fetch_comps

            inputs["comps"] = fetch_comps(
                client,
                connections,
                inputs["listing"],
                prop,
                str(settings.get("airbnb_listing_id") or ""),
                refresh=args.refresh_context,
            )
        except (ImportError, CannotAnalyze, ValueError, KeyError, TypeError) as exc:
            inputs["comps"] = {"status": "unavailable", "reason": str(exc)}
    return inputs, start, errors


def lid_of(inputs):
    return str((inputs.get("listing") or {}).get("id") or "")


def pms_of(inputs):
    return str((inputs.get("listing") or {}).get("pms") or "smartbnb")


def persist_pile(inputs, connections, lid, pms, run_id):
    """Store this run's PriceLabs pile (D14a). Returns a one-line note for the brief.

    Never silent. A pile that was fetched but not stored is exactly the state D14
    exists to prevent, so a failure is a loud note, not a swallowed exception. It
    never changes the analysis status: the pile is one input, not the basis (D14b).
    """
    from _mvp_recommendations import CannotPersist, now_iso, persist, rows_from_pile

    pile = inputs.get("pile")
    if not isinstance(pile, dict):
        return "PILE NOT STORED: actions/nudges were not fetched this run"
    if not lid:
        return "PILE NOT STORED: no listing id to file it under"
    connection = connections.supabase()
    if not connection:
        return "PILE NOT STORED: no supabase-revenue-manager connection configured"
    project, token = connection
    try:
        records = rows_from_pile(lid, pms, pile["actions"], pile["nudges"],
                                 pile["action_columns"], pile["nudge_columns"],
                                 now_iso(), run_id)
        result = persist(project, token, lid, pms, records)
    except CannotPersist as exc:
        return f"PILE NOT STORED: {exc}"
    c = pile.get("counts", {})
    return (f"pile stored: {result['inserted']} rows "
            f"({c.get('actions', 0)} actions, {c.get('nudges', 0)} nudges; "
            f"{c.get('actions_other', 0) + c.get('nudges_other', 0)} belong to other listings), "
            "previous set superseded")


def exit_code(status: str) -> int:
    """Exit 2 means "cannot produce a trustworthy answer this run", never "there is
    none". A DEGRADED run (PRD D12) produced an answer with its gaps named at the top,
    so it exits 0 like an analysable one; the status line carries the word. Only
    `blocked` exits 2. Mapping degraded to 2 would make every run without RankBreeze
    read as a failure, which is exactly what D12 exists to prevent."""
    return 0 if status in ("analysable", "degraded") else 2


def parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--property", help="Exact Hospitable property name or UUID")
    scope.add_argument("--show", metavar="RUN_ID", help="Read a saved result offline")
    scope.add_argument("--replay", metavar="RUN_ID", help="Recalculate a saved snapshot offline")
    scope.add_argument(
        "--inputs", type=Path, help="Offline normalized fixture bundle, including as_of and start"
    )
    ap.add_argument("--days", type=int, default=90, help="Forward calendar days, default 90 (7-90)")
    ap.add_argument("--start", help="Assert property local current date, YYYY-MM-DD")
    default_cache = Path(
        os.environ.get("RC_CACHE_DIR", str(Path.home() / ".cache/revenue-manager"))
    )
    ap.add_argument("--db", type=Path, default=default_cache / "workbench.sqlite3")
    ap.add_argument(
        "--env-file",
        action="append",
        default=[],
        help="Additional local credential file; repeatable",
    )
    ap.add_argument(
        "--connection-config", type=Path, help="Local MCP configuration (default ~/.claude.json)"
    )
    ap.add_argument(
        "--settings", type=Path, help="Property-scoped confirmed settings when Supabase is absent"
    )
    ap.add_argument(
        "--refresh-context",
        action="store_true",
        help="Also refresh slow-moving comps; core reads are always fresh",
    )
    ap.add_argument(
        "--max-calls", type=int, default=40, help="Hard HTTP-attempt budget, including retries"
    )
    ap.add_argument(
        "--details",
        action="store_true",
        help="Emit full computed facts as JSON, without source payloads",
    )
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    if not 7 <= args.days <= 90 or args.max_calls < 1:
        parser().error("--days must be 7-90 and --max-calls must be positive")
    store = Store(args.db)
    if args.show:
        try:
            saved = store.get_run(args.show)
            if not args.details:
                print(f"SAVED RUN: observed {saved.get('as_of', 'unknown')}; no live refresh.")
            print(
                encode(saved["facts"]) if args.details else saved["brief"],
                end="\n" if args.details else "",
            )
            return exit_code(saved["status"])
        finally:
            store.close()
    started = time.monotonic()
    run_id = str(uuid.uuid4())[:12]
    as_of = datetime.now(timezone.utc)
    run = {
        "id": run_id,
        "started_at": utc_now(),
        "status": "running",
        "inputs": {},
        "scope_days": args.days,
        "mode": "live",
    }
    client = ReadClient(store, args.max_calls)
    try:
        if args.replay:
            prior = store.get_run(args.replay)
            inputs = prior["inputs"]
            as_of = datetime.fromisoformat(prior["as_of"])
            start = date.fromisoformat(prior["start"])
            args.days = prior["scope_days"]
            run.update(
                mode="replay", replay_of=args.replay, original_sources=prior["metrics"]["sources"]
            )
            errors = []
        elif args.inputs:
            fixture = json.loads(args.inputs.read_text())
            inputs = fixture["inputs"]
            as_of = datetime.fromisoformat(fixture["as_of"])
            start = date.fromisoformat(fixture["start"])
            args.days = fixture.get("days", args.days)
            run["mode"] = "fixture"
            errors = []
        else:
            inputs, start, errors = run_live(
                args, client, Connections(args.env_file, args.connection_config), as_of
            )
        run.update(
            inputs=inputs, as_of=as_of.isoformat(), start=start.isoformat(), scope_days=args.days
        )
        facts = compute(inputs, as_of, start, args.days)
        if errors:
            facts.setdefault("notes", []).extend(errors)
        if run["mode"] == "live":
            # D14a: store the pile, latest-wins. Only on a LIVE run; a replay or a
            # fixture would overwrite "current" with something that is not.
            facts.setdefault("notes", []).append(
                persist_pile(inputs, Connections(args.env_file, args.connection_config),
                             lid_of(inputs), pms_of(inputs), run_id))
    except (CannotAnalyze, ValueError, KeyError, TypeError, OSError) as exc:
        facts = {"status": "blocked", "blockers": [str(exc)]}
    run["status"] = facts["status"]
    run["facts"] = facts
    run["metrics"] = client.metrics()
    run["metrics"].update(elapsed_seconds=round(time.monotonic() - started, 3), llm_api_calls=0)
    facts["sources"] = run.get("original_sources", run["metrics"]["sources"])
    brief = render(facts, run_id, run["metrics"])
    if run["mode"] != "live":
        brief = f"OFFLINE {run['mode'].upper()}: saved evidence, not a live refresh.\n" + brief
    run["metrics"].update(
        brief_bytes=len(brief.encode()),
        estimated_brief_tokens=round(len(brief.encode()) / 3.6),
        token_estimate_method="UTF-8 bytes / 3.6; payload estimate, not provider billing",
    )
    run["brief"] = brief
    run["completed_at"] = utc_now()
    store.save_run(run_id, run)
    store.close()
    print(encode(facts) if args.details else brief, end="\n" if args.details else "")
    return exit_code(facts["status"])


if __name__ == "__main__":
    sys.exit(main())
