#!/usr/bin/env python3
"""Plan and apply PriceLabs price changes. PRD D1 (amended 2026-09-23), D8, S2-S4.

    apply_change.py plan     --change a.json [--change b.json ...]   fresh read, prints the cards
    apply_change.py apply    --plan <id> [--plan <id> ...]           applies and verifies each
    apply_change.py rollback --journal <journal file>                 plans the reverse change

Show the operator the card(s) and ask whether to apply. A plain yes applies them; one yes
can cover every card shown together. No codes, no ritual. Without a yes, nothing is applied.

Undo is one command: `apply_change.py rollback --journal <journal or snapshot file>`. It
plans the reverse change from what is live NOW (items already back are skipped, dates now in
the past are dropped, both said on the card) and is shown and applied like any other change.
Only this command can put a whole saved override back (overrides_restore); a change file
cannot carry one. Plans expire after 24 hours: apply refuses and asks for a fresh plan.

--max-delta-pct: the property's property_config.settings.max_delta_pct (15 or 0.15; default
15%). A move above it is a loud warning on the card, never a block (D8).

Exit 0: plans printed, or every plan applied AND verified field by field.
Exit 2: nothing trustworthy to report (refused, drifted, failed, or sent-but-unverified).
Apply stops at the first plan that is not verified; later plans are not attempted.

Change file shape (one listing per file):
    {"listing_id": "...", "pms": "hospitable", "reason": "why, in one line",
     "listing_prices": {"min": 180},
     "overrides_set": [{"date": "2026-10-03", "price": 260, "price_type": "fixed", "min_stay": 2}],
     "overrides_delete": ["2026-10-10"]}

--target (default pricelabs, exactly the behaviour above) picks where the change is written.
A PMS name writes the nightly price / min stay straight to that PMS calendar through
_calendar_write (docs/WRITE-TARGETS.md). `beyond` runs the same plan/apply/rollback flow as
PriceLabs through _beyond_write, and a saved plan, journal or change file whose pms is
"beyond" is routed there even without the flag:

    apply_change.py plan     --target hospitable --change a.json [--settings row.json]
    apply_change.py apply    --target hospitable --plan <id>
    apply_change.py rollback --target hospitable --journal <journal file>
    apply_change.py verify   --target hospitable --journal <journal file>   read-only re-check

`verify` re-reads the calendar and compares every field on every date a journal touched with
what was approved. It sends nothing and changes nothing on disk: it is how a write to a PMS
that applies changes asynchronously (sent-unverified after its settle time) is confirmed later.

    {"listing_id": "<PMS property id>", "target": "hospitable", "reason": "why",
     "calendar_set": [{"date": "2026-10-03", "price": 260, "min_stay": 2}]}

Calendar writes read the property's property_config row FRESH (Supabase, or --settings
<file> holding {"property_id", "settings"}): `pricing_tool` (PriceLabs or Beyond owns the
prices -> a PMS price write is refused), `min_price` (the floor when the PMS has none; without
one a price cut is refused), `max_delta_pct`. A target built on another branch and not in this
version says so instead of failing.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cache import cache_dir  # noqa: E402
from _mvp_config import SUPABASE_SERVER, Connections, normalized_context, read_text, utf8_console  # noqa: E402
from _mvp_recommendations import CannotPersist, _ident, _lit  # noqa: E402
from _mvp_store import CannotAnalyze  # noqa: E402
from _mvp_write import (  # noqa: E402
    CannotWrite, Live, WriteClient, apply_batch, describe, load_envelope, load_plan,
    plan_change, plan_id, rollback_change, save_envelope,
)


def undo_source(arg: str, state: Path) -> Path:
    """A journal or snapshot this writer saved, and nothing else: only these may carry
    overrides_restore, so the file must live in the writer's own journal/snapshots folders."""
    p = Path(arg)
    homes = [(state / d).resolve() for d in ("journal", "snapshots")]
    tries = [p] if p.is_absolute() or p.exists() else [state / "journal" / p.name,
                                                         state / "snapshots" / p.name]
    for c in tries:
        if c.is_file() and c.resolve().parent in homes:
            return c.resolve()
    raise CannotWrite(f"{arg} is not a journal or snapshot this writer saved (they live in "
                      f"{state / 'journal'} and {state / 'snapshots'})")

AUDIT_TABLE = "public.pricelabs_change_log"


def audit_statement(journal: dict) -> str:
    """One INSERT per operation into the existing change log. Values are literals only."""
    env = journal["envelope"]
    t = env["target"]
    lid = _ident(t["listing_id"], "listing_id")
    rows = []
    prefix = "beyond_" if t.get("pms") == "beyond" else ""
    for op in env["operations"]:
        if op["kind"] == "listing_price":
            kind, field = "listing_price", op["field"]
        elif op["kind"] == "override":
            kind = "override_delete" if op["after"] is None else "override_set"
            field = op["date"]
        else:  # a listing-level op with no date (Beyond's listing_min_stay) or a future kind
            kind, field = op["kind"], op.get("field") or op.get("date") or op["kind"]
        kind = prefix + kind
        new = "deleted" if op["after"] is None else json.dumps(op["after"], sort_keys=True)
        rows.append("(" + ", ".join([
            _lit(env.get("listing_name") or lid), _lit(lid), _lit(kind), _lit(field),
            _lit(json.dumps(op["before"], sort_keys=True)), _lit(new), _lit(env["reason"]),
            _lit(f"Claude, approved in chat, plan {journal['plan_id']}"),
            _lit(f"journal {Path(journal['journal_path']).name}; status {journal['status']}"),
        ]) + ")")
    return (f"INSERT INTO {AUDIT_TABLE} (property_name, listing_id, change_type, field_changed, "
            "old_value, new_value, reason, changed_by, notes) VALUES\n  " + ",\n  ".join(rows) + ";")


def audit(connections: Connections, journal: dict) -> str:
    import urllib.error
    import urllib.request
    conf = connections.supabase()
    if not conf:
        raise CannotPersist("no supabase-revenue-manager connection")
    project, token = conf
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{_ident(project, 'project')}/database/query",
        data=json.dumps({"query": audit_statement(journal), "read_only": False}).encode(),
        method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        raise CannotPersist(f"Supabase HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CannotPersist("Supabase unreachable") from None
    return f"audit stored: {len(journal['envelope']['operations'])} row(s) in {AUDIT_TABLE}"


def live_for(connections: Connections, listing_id, pms) -> Live:
    return Live(WriteClient(connections.key("pricelabs")), listing_id, pms)


def print_plans(envs: list, state: Path) -> None:
    for i, env in enumerate(envs):
        save_envelope(env, state)
        if i:
            print("\n" + "-" * 60 + "\n")
        print(describe(env))
    ids = " ".join(f"--plan {plan_id(e)}" for e in envs)
    print(f"\n(Ask the operator: apply {'this' if len(envs) == 1 else f'these {len(envs)} changes'}? "
          f"On a yes: apply_change.py apply {ids})")


def read_change(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    try:
        spec = json.loads(text)
    except ValueError:
        raise CannotWrite(f"{path} is not valid JSON") from None
    if not isinstance(spec, dict):
        raise CannotWrite(f"{path}: the change must be a JSON object")
    return spec


# ------------------------------------------------------------------------------ other targets

# name -> "module:Class". Targets built on other branches resolve only once they land; until
# then the CLI says the writer is not installed instead of raising an ImportError.
CALENDAR_TARGETS = {
    "hospitable": "_pms_hospitable:HospitableCalendarTarget",
    "guesty": "_pms_guesty:GuestyCalendarTarget",
    "ownerrez": "_pms_ownerrez:OwnerRezCalendarTarget",
    "hostaway": "_pms_hostaway:HostawayCalendarTarget",
    "lodgify": "_pms_lodgify:LodgifyCalendarTarget",
    "uplisting": "_pms_uplisting:UplistingCalendarTarget",
    "smoobu": "_pms_smoobu:SmoobuCalendarTarget",
    "hostfully": "_pms_hostfully:HostfullyCalendarTarget",
}
# A pricing tool with a module-level writer mirroring _mvp_write (plan_change, live_for,
# apply_batch, rollback_change, describe, load_plan, save_envelope, plan_id).
PRICING_TARGETS = {"beyond": "_beyond_write"}
PRICING_WRITER_API = ("plan_change", "live_for", "apply_batch", "rollback_change", "describe",
                      "load_plan", "save_envelope", "plan_id")
TARGET_LABELS = {"pricelabs": "PriceLabs", "hospitable": "Hospitable", "guesty": "Guesty",
                 "ownerrez": "OwnerRez", "hostaway": "Hostaway", "lodgify": "Lodgify",
                 "uplisting": "Uplisting", "smoobu": "Smoobu", "hostfully": "Hostfully",
                 "beyond": "Beyond"}
TARGETS = ("pricelabs", *CALENDAR_TARGETS, *PRICING_TARGETS)


def resolve(name: str):
    """The class (or entry point) registered for a target, or CannotWrite naming the gap."""
    spec = CALENDAR_TARGETS.get(name) or PRICING_TARGETS.get(name)
    if not spec:
        raise CannotWrite(f"{name!r} is not a write target ({', '.join(TARGETS)})")
    mod_name, _, attr = spec.partition(":")
    missing = CannotWrite(f"the {TARGET_LABELS.get(name, name)} writer is not installed in this version")
    try:
        module = importlib.import_module(mod_name)
    except ModuleNotFoundError as exc:
        if exc.name == mod_name:
            raise missing from None
        raise
    if not attr:  # a pricing-tool writer module: every entry point the flow uses must exist
        if not all(callable(getattr(module, f, None)) for f in PRICING_WRITER_API):
            raise missing
        return module
    found = getattr(module, attr, None)
    if found is None:
        raise missing
    return found


def _supabase_rows(connections: Connections, sql: str, read_only: bool):
    import urllib.error
    import urllib.request
    conf = connections.supabase()
    if not conf:
        raise CannotPersist(f"no {SUPABASE_SERVER} connection")
    project, token = conf
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{_ident(project, 'project')}/database/query",
        data=json.dumps({"query": sql, "read_only": read_only}).encode(),
        method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        raise CannotPersist(f"Supabase HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CannotPersist("Supabase unreachable") from None
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        raise CannotPersist("Supabase returned an unreadable reply") from None


def property_settings(connections: Connections, listing_id: str, settings_file=None) -> dict:
    """property_config.settings for this PMS property, read FRESH every time (never cached):
    pricing_tool, min_price and max_delta_pct decide what a calendar write may do."""
    if settings_file:
        try:
            raw = json.loads(read_text(Path(settings_file).expanduser()))
        except (OSError, ValueError):
            raise CannotWrite(f"{settings_file} is not a readable settings JSON file") from None
        rows = [raw] if isinstance(raw, dict) else []
    else:
        if not connections.supabase():
            raise CannotWrite(f"Cannot read property_config (no {SUPABASE_SERVER} connection), so whether a "
                              "pricing tool owns this listing is unknown. Pass --settings <file> with "
                              '{"property_id": ..., "settings": {...}}, or connect Supabase.')
        sql = ("SELECT property_id, settings, updated_at FROM public.property_config WHERE property_id = "
               + _lit(_ident(listing_id, "listing_id")))
        try:
            rows = _supabase_rows(connections, sql, read_only=True)
        except CannotPersist as exc:
            raise CannotWrite(f"Cannot read property_config ({exc}); nothing was planned or sent") from None
        if not isinstance(rows, list):
            raise CannotWrite("Supabase did not return property_config rows")
    return normalized_context({"config": rows}, str(listing_id))["settings"]


def calendar_audit_statement(journal: dict) -> str:
    """One INSERT per night into the same change log the PriceLabs writer uses."""
    env = journal["envelope"]
    t = env["target"]
    lid = _ident(t["listing_id"], "listing_id")
    rows = []
    for op in env["operations"]:
        rows.append("(" + ", ".join([
            _lit(lid), _lit(lid), _lit(f"{t['target']}_calendar"), _lit(op["date"]),
            _lit(json.dumps({f: op["before"][f] for f in op["set"]}, sort_keys=True)),
            _lit(json.dumps({f: op["after"][f] for f in op["set"]}, sort_keys=True)),
            _lit(env["reason"]), _lit(f"Claude, approved in chat, plan {journal['plan_id']}"),
            _lit(f"journal {Path(journal['journal_path']).name}; status {journal['status']}; "
                 f"currency {t['currency']}"),
        ]) + ")")
    return (f"INSERT INTO {AUDIT_TABLE} (property_name, listing_id, change_type, field_changed, "
            "old_value, new_value, reason, changed_by, notes) VALUES\n  " + ",\n  ".join(rows) + ";")


def run_calendar(args, connections: Connections, target_name: str) -> int:
    """plan / apply / rollback against one PMS calendar target (_calendar_write)."""
    import _calendar_write as cw
    cls = resolve(target_name)
    label = TARGET_LABELS[target_name]
    state = Path(cache_dir("writes")) / "calendar" / target_name
    targets, settings = {}, {}

    def target_for(lid):
        # one target (one transport, one call budget) per listing per run
        if lid not in targets:
            targets[lid] = cls(connections)
        return targets[lid]

    def settings_for(lid):
        # fresh per call: apply re-reads it, it never trusts what the plan saw
        return property_settings(connections, lid, args.settings)

    def show(envs):
        for i, env in enumerate(envs):
            cw.save_envelope(env, state)
            if i:
                print("\n" + "-" * 60 + "\n")
            print(cw.describe(env, label))
        ids = " ".join(f"--plan {cw.plan_id(e)}" for e in envs)
        print(f"\n(Ask the operator: apply {'this' if len(envs) == 1 else f'these {len(envs)} changes'}? "
              f"On a yes: apply_change.py apply --target {target_name} {ids})")

    if args.cmd == "plan":
        specs = [read_change(c) for c in args.change]
        envs = []
        for sp in specs:
            lid = sp.get("listing_id")
            envs.append(cw.plan_calendar(sp, target_for(lid), settings_for(lid), max_delta=args.max_delta_pct))
        show(envs)
        return 0
    if args.cmd == "verify":
        journal = load_envelope(undo_source(args.journal, state))
        if not isinstance(journal, dict) or "envelope" not in journal:
            raise CannotWrite("verify takes a journal (from the journal folder), not a snapshot")
        env = journal["envelope"]
        result = cw.verify_journal(journal, target_for(env["target"]["listing_id"]))
        for row in result["rows"]:
            print(f"  {'ok ' if row['ok'] else 'BAD'} {row['date']}"
                  + ("" if row["ok"] else f"  differs on {', '.join(row['differs_on'])}"))
        recorded = journal.get("status")
        if result["ok"]:
            print(f"VERIFIED NOW: every field on every date of plan {cw.plan_id(env)} matches what was "
                  f"approved. (The journal still records the apply-time status, {recorded}; nothing "
                  "was written.)")
            return 0
        print(f"STILL NOT MATCHING: {'; '.join(result['problems'])}. Read the live calendar in {label}. "
              f"Undo with: {journal.get('undo') or f'apply_change.py rollback --target {target_name} --journal <file>'}")
        return 2
    if args.cmd == "rollback":
        spec = cw.rollback_change(load_envelope(undo_source(args.journal, state)))
        if spec.get("target") != target_name:
            raise CannotWrite(f"That journal is for {spec.get('target')}; use --target {spec.get('target')}")
        lid = spec["listing_id"]
        show([cw.plan_calendar(spec, target_for(lid), settings_for(lid), max_delta=args.max_delta_pct,
                               rollback=True)])
        return 0
    envs = [cw.load_plan(state, pid) for pid in args.plan]

    def report(journal):
        env = journal["envelope"]
        print(f"APPLIED AND VERIFIED: plan {cw.plan_id(env)} on the {label} calendar for listing "
              f"{env['target']['listing_id']}")
        for v in journal["verification"]:
            print(f"  {'ok ' if v['ok'] else 'BAD'} {v['date']}")
        print(f"  undo: {journal['undo']}")
        if not args.no_audit:
            try:
                _supabase_rows(connections, calendar_audit_statement(journal), read_only=False)
                print(f"  audit stored: {len(env['operations'])} row(s) in {AUDIT_TABLE}")
            except CannotPersist as exc:
                print(f"  AUDIT NOT STORED: {exc}. The change IS live and verified; "
                      f"the journal on disk is the only record: {journal['journal_path']}")

    cw.apply_batch(envs, target_for, settings_for, state_dir=state, on_verified=report)
    return 0


def routes_to_beyond(args, state: Path, specs) -> bool:
    """True when what this command names belongs to Beyond (pms "beyond"), so it is never sent
    to PriceLabs. A mix of PriceLabs and Beyond in one command is refused."""
    if args.cmd == "plan":
        names = {sp.get("pms") for sp in specs}
    elif args.cmd == "apply":
        names = set()
        for pid in args.plan:
            path = state / "plans" / f"{str(pid).strip()}.json"
            try:
                names.add(((json.loads(path.read_text()) or {}).get("target") or {}).get("pms"))
            except (OSError, ValueError, AttributeError):
                names.add(None)  # load_plan reports it properly on the normal path
    else:
        obj = load_envelope(undo_source(args.journal, state))
        if not isinstance(obj, dict):
            return False  # rollback_change refuses it on the normal path
        env = obj.get("envelope", obj)
        names = {obj.get("pms") if "pms" in obj else ((env.get("target") if isinstance(env, dict) else None)
                                                       or {}).get("pms")}
    if "beyond" not in names:
        return False
    if len(names) > 1:
        raise CannotWrite("PriceLabs and Beyond changes cannot go in one command; run them separately")
    return True


def run_beyond(args, connections: Connections, state: Path, specs) -> int:
    """Beyond through its own writer module, the same flow and the same cards as PriceLabs."""
    W = resolve("beyond")

    def live(lid, pms=None):
        return W.live_for(connections, lid, pms)

    def show(envs):
        for i, env in enumerate(envs):
            W.save_envelope(env, state)
            if i:
                print("\n" + "-" * 60 + "\n")
            print(W.describe(env))
        ids = " ".join(f"--plan {W.plan_id(e)}" for e in envs)
        print(f"\n(Ask the operator: apply {'this' if len(envs) == 1 else f'these {len(envs)} changes'}? "
              f"On a yes: apply_change.py apply --target beyond {ids})")

    if args.cmd == "plan":
        show([W.plan_change(sp, live(sp.get("listing_id"), sp.get("pms")), max_delta=args.max_delta_pct)
              for sp in specs])
        return 0
    if args.cmd == "rollback":
        spec = W.rollback_change(load_envelope(undo_source(args.journal, state)))
        show([W.plan_change(spec, live(spec["listing_id"], spec.get("pms")), max_delta=args.max_delta_pct,
                            rollback=True)])
        return 0
    envs = [W.load_plan(state, pid) for pid in args.plan]
    for env in envs:
        if (env.get("target") or {}).get("pms") != "beyond":
            raise CannotWrite(f"plan {W.plan_id(env)} is not a Beyond plan; apply it without --target beyond")

    def report(journal):
        env = journal["envelope"]
        print(f"APPLIED AND VERIFIED: plan {W.plan_id(env)} on Beyond listing "
              f"{env.get('listing_name') or env['target']['listing_id']}")
        for v in journal["verification"]:
            print(f"  {'ok ' if v.get('ok') else 'BAD'} {v.get('field') or v.get('date') or v.get('kind')}")
        print(f"  undo: apply_change.py rollback --target beyond --journal {Path(journal['journal_path']).name}")
        if not args.no_audit:
            try:
                print("  " + audit(connections, journal))
            except CannotPersist as exc:
                print(f"  AUDIT NOT STORED: {exc}. The change IS live and verified; "
                      f"the journal on disk is the only record: {journal['journal_path']}")

    W.apply_batch(envs, lambda lid, pms: live(lid, pms), state_dir=state, on_verified=report)
    return 0


def main(argv=None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", action="append", default=[])
    sub = ap.add_subparsers(dest="cmd", required=True)
    target_help = ("where to write: pricelabs (default), beyond, or a PMS calendar: "
                   + ", ".join(CALENDAR_TARGETS))
    settings_help = ("calendar targets: property_config row JSON {property_id, settings}; "
                     "default reads it fresh from Supabase")
    p = sub.add_parser("plan")
    p.add_argument("--change", action="append", required=True,
                   help="change JSON file (repeat for a batch), or - for stdin")
    p.add_argument("--max-delta-pct", type=float, default=None,
                   help="property_config.settings.max_delta_pct (15 or 0.15)")
    a = sub.add_parser("apply")
    a.add_argument("--plan", action="append", required=True, help="plan id (repeat for a batch)")
    a.add_argument("--no-audit", action="store_true")
    r = sub.add_parser("rollback")
    r.add_argument("--journal", required=True, help="journal or snapshot file")
    r.add_argument("--max-delta-pct", type=float, default=None)
    v = sub.add_parser("verify", help="calendar targets: read-only re-check of a journal")
    v.add_argument("--journal", required=True, help="journal file")
    for sp in (p, a, r, v):
        sp.add_argument("--target", default="pricelabs", choices=TARGETS, help=target_help)
        sp.add_argument("--settings", default=None, help=settings_help)
    args = ap.parse_args(argv)
    state = Path(cache_dir("writes"))
    if args.target in CALENDAR_TARGETS:
        state = state / "calendar" / args.target

    try:
        connections = Connections(env_files=args.env_file)
        if args.target in CALENDAR_TARGETS:
            return run_calendar(args, connections, args.target)
        if args.cmd == "verify":
            raise CannotWrite("verify re-checks a PMS calendar journal; pass --target <pms> "
                              f"({', '.join(CALENDAR_TARGETS)})")
        if args.target == "beyond":
            resolve("beyond")  # "not installed" before anything is read
        specs = [read_change(c) for c in args.change] if args.cmd == "plan" else None
        if args.target == "beyond" or routes_to_beyond(args, state, specs):
            args.target = "beyond"
            return run_beyond(args, connections, state, specs)
        if args.cmd == "plan":
            envs = [plan_change(sp, live_for(connections, sp.get("listing_id"), sp.get("pms")),
                                max_delta=args.max_delta_pct)
                    for sp in specs]
            print_plans(envs, state)
            return 0
        if args.cmd == "rollback":
            spec = rollback_change(load_envelope(undo_source(args.journal, state)))
            print_plans([plan_change(spec, live_for(connections, spec["listing_id"], spec["pms"]),
                                     max_delta=args.max_delta_pct, rollback=True)], state)
            return 0
        # apply: every id is loaded and hash-checked before anything is sent
        envs = [load_plan(state, pid) for pid in args.plan]
        def report(journal):
            env = journal["envelope"]
            print(f"APPLIED AND VERIFIED: plan {plan_id(env)} on "
                  f"{env.get('listing_name') or env['target']['listing_id']}")
            for v in journal["verification"]:
                print(f"  {'ok ' if v['ok'] else 'BAD'} {v.get('field') or v.get('date')}")
            print(f"  undo: apply_change.py rollback --journal {Path(journal['journal_path']).name}")
            if not args.no_audit:
                try:
                    print("  " + audit(connections, journal))
                except CannotPersist as exc:
                    print(f"  AUDIT NOT STORED: {exc}. The change IS live and verified; "
                          f"the journal on disk is the only record: {journal['journal_path']}")

        apply_batch(envs, lambda lid, pms: live_for(connections, lid, pms),
                    state_dir=state, on_verified=report)
        return 0
    except (CannotWrite, CannotAnalyze, CannotPersist, OSError) as exc:
        print(f"CANNOT WRITE: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never a raw traceback after a send
        flag = "" if args.target == "pricelabs" else f"--target {args.target} "
        print(f"CANNOT WRITE: unexpected {type(exc).__name__}. If apply ran, a write may have been "
              f"sent: read the live listing, then check the newest journal in {state / 'journal'} "
              f"and undo with apply_change.py rollback {flag}--journal <that file>.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
