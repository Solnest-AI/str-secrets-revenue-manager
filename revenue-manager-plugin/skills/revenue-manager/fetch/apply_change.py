#!/usr/bin/env python3
"""Plan and apply PriceLabs price changes. PRD D1 (amended 2026-09-23), D8, S2-S4.

    apply_change.py plan     --change a.json [--change b.json ...]   fresh read, prints the cards
    apply_change.py apply    --plan <id> [--plan <id> ...]           applies and verifies each
    apply_change.py rollback --journal <journal file>                 plans the reverse change

Show the operator the card(s) and ask whether to apply. A plain yes applies them; one yes
can cover every card shown together. No codes, no ritual. Without a yes, nothing is applied.

Every rollback snapshot is itself a change file, so undoing any applied change is one
command: `apply_change.py plan --change <snapshot>`, shown and applied like any other.

Exit 0: plans printed, or every plan applied AND verified field by field.
Exit 2: nothing trustworthy to report (refused, drifted, failed, or sent-but-unverified).
Apply stops at the first plan that is not verified; later plans are not attempted.

Change file shape (one listing per file):
    {"listing_id": "...", "pms": "hospitable", "reason": "why, in one line",
     "listing_prices": {"min": 180},
     "overrides_set": [{"date": "2026-10-03", "price": 260, "price_type": "fixed", "min_stay": 2}],
     "overrides_delete": ["2026-10-10"]}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cache import cache_dir  # noqa: E402
from _mvp_config import Connections, utf8_console  # noqa: E402
from _mvp_recommendations import CannotPersist, _ident, _lit  # noqa: E402
from _mvp_store import CannotAnalyze  # noqa: E402
from _mvp_write import (  # noqa: E402
    CannotWrite, Live, WriteClient, apply_batch, describe, load_envelope, load_plan,
    plan_change, plan_id, rollback_change, save_envelope,
)

AUDIT_TABLE = "public.pricelabs_change_log"


def audit_statement(journal: dict) -> str:
    """One INSERT per operation into the existing change log. Values are literals only."""
    env = journal["envelope"]
    t = env["target"]
    lid = _ident(t["listing_id"], "listing_id")
    rows = []
    for op in env["operations"]:
        if op["kind"] == "listing_price":
            kind, field = "listing_price", op["field"]
        else:
            kind = "override_delete" if op["after"] is None else "override_set"
            field = op["date"]
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


def main(argv=None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", action="append", default=[])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--change", action="append", required=True,
                   help="change JSON file (repeat for a batch), or - for stdin")
    a = sub.add_parser("apply")
    a.add_argument("--plan", action="append", required=True, help="plan id (repeat for a batch)")
    a.add_argument("--no-audit", action="store_true")
    r = sub.add_parser("rollback")
    r.add_argument("--journal", required=True)
    args = ap.parse_args(argv)
    state = Path(cache_dir("writes"))

    try:
        connections = Connections(env_files=args.env_file)
        if args.cmd == "plan":
            specs = [read_change(c) for c in args.change]
            envs = [plan_change(sp, live_for(connections, sp.get("listing_id"), sp.get("pms")))
                    for sp in specs]
            print_plans(envs, state)
            return 0
        if args.cmd == "rollback":
            jp = Path(args.journal)
            if not jp.is_absolute() and not jp.exists():
                jp = state / "journal" / jp.name
            spec = rollback_change(load_envelope(jp))
            print_plans([plan_change(spec, live_for(connections, spec["listing_id"], spec["pms"]))], state)
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


if __name__ == "__main__":
    sys.exit(main())
