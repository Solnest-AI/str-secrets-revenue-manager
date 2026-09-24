"""Persist the PriceLabs pile (actions + nudges) to Supabase, latest-wins. PRD D14.

WHY A SEPARATE WRITER. The runner's HTTP transport (_mvp_store.Client) is deliberately
READ-ONLY: it forces `read_only: true` on every Supabase query and refuses every other
POST. That is a safety property worth keeping, so this module does not go through it.
It owns exactly ONE write, to exactly ONE table, with exactly ONE statement shape, and
it refuses anything else.

WHAT IT WRITES. One Management-API call per listing per run:

    UPDATE ... SET superseded_at = now() WHERE listing_id = $1 AND superseded_at IS NULL;
    INSERT ... (one row per action, one per nudge)

so the table always holds the last pull as "current" and keeps history underneath.

WHAT IT REFUSES. Any listing_id or pms that is not a plain identifier, any payload that
is not the flattened row shape reduce_customizations produces, and any attempt to write
when the project ref or token is missing. A refusal is exit 2 upstream, never a silent
skip, because "we have no stored recommendation" is exactly the state D14 exists to
prevent.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

TABLE = "public.pricelabs_recommendations"
_IDENT = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class CannotPersist(Exception):
    pass


def _ident(value, what: str) -> str:
    text = str(value or "")
    if not _IDENT.match(text):
        raise CannotPersist(f"{what} {text!r} is not a plain identifier; refusing to build SQL")
    return text


def _lit(text: str) -> str:
    """A SQL string literal. Single quotes doubled; nothing else is interpolated raw."""
    return "'" + str(text).replace("'", "''") + "'"


def rows_from_pile(listing_id: str, pms: str, action_rows: list, nudge_rows: list,
                   action_columns: list, nudge_columns: list, pulled_at: str,
                   run_id: str | None) -> list[dict]:
    """Turn the reducer's flattened CSV rows into insert-ready records.

    Every row carries `scope` (this-listing | OTHER-LISTING) from the reducer, because
    /v1/actions and /v1/nudges/available are ACCOUNT-WIDE and a stray row acted on
    moves the wrong property's floor. We keep the strays too, labelled, so the table is
    an honest picture of what PriceLabs said, not a filtered one.
    """
    out = []
    for row in action_rows:
        rec = dict(zip(action_columns, row))
        out.append({
            "listing_id": listing_id, "pms": pms, "kind": "action",
            "external_id": str(rec.get("action_type") or ""),
            "scope": "this-listing" if rec.get("scope") == "this-listing" else "other-listing",
            "owner_listing": str(rec.get("listing_id") or ""),
            "payload": rec, "pulled_at": pulled_at, "run_id": run_id,
        })
    for row in nudge_rows:
        rec = dict(zip(nudge_columns, row))
        out.append({
            "listing_id": listing_id, "pms": pms, "kind": "nudge",
            "external_id": str(rec.get("nudge_id") or ""),
            "scope": "this-listing" if rec.get("scope") == "this-listing" else "other-listing",
            "owner_listing": str(rec.get("listing_id") or ""),
            "payload": rec, "pulled_at": pulled_at, "run_id": run_id,
        })
    for rec in out:
        if not rec["external_id"]:
            raise CannotPersist(f"a {rec['kind']} row has no external id; refusing to store "
                                "an unidentifiable recommendation")
    return out


def statement(listing_id: str, pms: str, records: list[dict]) -> str:
    """The one statement shape this module ever produces."""
    lid, p = _ident(listing_id, "listing_id"), _ident(pms, "pms")
    parts = [f"UPDATE {TABLE} SET superseded_at = now() "
             f"WHERE listing_id = {_lit(lid)} AND pms = {_lit(p)} AND superseded_at IS NULL;"]
    if records:
        values = []
        for r in records:
            if r["kind"] not in ("nudge", "action") or r["scope"] not in ("this-listing", "other-listing"):
                raise CannotPersist("record has an unexpected kind or scope")
            values.append("(" + ", ".join([
                _lit(lid), _lit(p), _lit(r["kind"]), _lit(r["external_id"]), _lit(r["scope"]),
                _lit(r["owner_listing"]), _lit(json.dumps(r["payload"], separators=(",", ":"))) + "::jsonb",
                _lit(r["pulled_at"]) + "::timestamptz",
                _lit(r["run_id"]) if r.get("run_id") else "NULL",
            ]) + ")")
        parts.append(f"INSERT INTO {TABLE} (listing_id, pms, kind, external_id, scope, "
                     "owner_listing, payload, pulled_at, run_id) VALUES\n  "
                     + ",\n  ".join(values) + ";")
    return "\n".join(parts)


def persist(project: str, token: str, listing_id: str, pms: str, records: list[dict],
            timeout: int = 60) -> dict:
    """Run the supersede-and-insert against the Supabase Management API.

    Returns {"superseded": True, "inserted": n}. Raises CannotPersist on anything else.
    """
    if not project or not token:
        raise CannotPersist("no Supabase project ref or token; the pile was fetched but "
                            "NOT stored, and the run must say so")
    sql = statement(listing_id, pms, records)
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{_ident(project, 'project')}/database/query",
        data=json.dumps({"query": sql, "read_only": False}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        raise CannotPersist(f"Supabase HTTP {exc.code} storing the pile: {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise CannotPersist(f"storing the pile failed: {exc}") from exc
    return {"superseded": True, "inserted": len(records)}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
