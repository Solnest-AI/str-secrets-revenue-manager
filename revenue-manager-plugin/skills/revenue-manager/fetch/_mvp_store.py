"""Private SQLite workbench and metered, read-only HTTP for the analysis runner."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path


class CannotAnalyze(ValueError):
    """Missing or untrustworthy evidence, never a successful empty result."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def identity(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Store:
    """Only normalized evidence enters this store. Raw guest data never does."""

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS source (
                key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                status TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        self.db.commit()

    def cached(self, key, ttl_seconds):
        if ttl_seconds <= 0:
            return None
        with self.lock:
            row = self.db.execute(
                "SELECT fetched_at,payload FROM source WHERE key=?", (key,)
            ).fetchone()
        if row:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
                if 0 <= age <= ttl_seconds:
                    return {"fetched_at": row[0], "data": json.loads(row[1])}
            except (ValueError, TypeError):
                pass
        return None

    def source(self, key, fetched_at, data):
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO source VALUES (?,?,?)", (key, fetched_at, encode(data))
            )
            self.db.commit()

    def save_run(self, run_id, data):
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO run VALUES (?,?,?,?)",
                (run_id, data["started_at"], data["status"], encode(data)),
            )
            self.db.commit()

    def get_run(self, run_id):
        with self.lock:
            row = self.db.execute("SELECT payload FROM run WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise CannotAnalyze("Run ID is not in this local workbench")
        return json.loads(row[0])

    def close(self):
        self.db.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Prevent Authorization, API keys and the RankBreeze cookie leaving their origin.
        return None


class ReadClient:
    """Every physical HTTP attempt counts, including failed requests and retries.

    The caller cannot use this transport for provider writes. The only POSTs allowed
    are PriceLabs listing_prices, read-only Supabase SELECT, and two read-only MCP calls.
    """

    def __init__(self, store, max_calls=40, opener=None):
        self.store = store
        self.max_calls = max_calls
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.lock = threading.RLock()
        self.calls = []
        self.sources = []
        self.memory = {}

    @staticmethod
    def _read_only(provider, operation, method, body, url):
        target = urlsplit(url)
        if target.scheme != "https":
            raise CannotAnalyze("Provider reads require HTTPS")
        if method == "GET":
            return
        allowed = (
            (
                provider == "pricelabs"
                and operation == "listing_prices"
                and target.netloc == "api.pricelabs.co"
                and target.path == "/v1/listing_prices"
            )
            or (
                provider == "supabase"
                and operation == "context"
                and target.netloc == "api.supabase.com"
                and re.fullmatch(r"/v1/projects/[a-z0-9]+/database/query", target.path)
                and isinstance(body, dict)
                and body.get("read_only") is True
                and str(body.get("query", "")).lstrip().upper().startswith("SELECT ")
            )
            or (
                provider == "rankbreeze"
                and operation == "rpc"
                and isinstance(body, dict)
                and (
                    body.get("method") == "initialize"
                    or (
                        body.get("method") == "tools/call"
                        and body.get("params", {}).get("name")
                        in {"get_listing_rankings", "get_user_listings"}
                    )
                )
            )
        )
        if method != "POST" or not allowed:
            raise CannotAnalyze("The analysis transport refuses write operations")

    def request(self, provider, operation, url, *, headers=None, body=None, text=False):
        method = "POST" if body is not None else "GET"
        self._read_only(provider, operation, method, body, url)
        for attempt in range(2):
            with self.lock:
                if len(self.calls) >= self.max_calls:
                    raise CannotAnalyze(f"HTTP call budget ({self.max_calls}) reached")
                item = {
                    "provider": provider,
                    "operation": operation,
                    "attempt": attempt + 1,
                    "started_at": utc_now(),
                    "status": None,
                    "response_bytes": 0,
                }
                self.calls.append(item)
            started = time.monotonic()
            req = urllib.request.Request(
                url,
                data=encode(body).encode() if body is not None else None,
                headers=headers or {},
                method=method,
            )
            try:
                with self.opener.open(req, timeout=60) as response:
                    raw = response.read()
                    item.update(status=response.status, response_bytes=len(raw))
                    result = raw.decode("utf-8") if text else json.loads(raw)
                    return result, dict(response.headers)
            except urllib.error.HTTPError as exc:
                item["status"] = exc.code
                exc.close()
                if exc.code == 429 and attempt == 0:
                    try:
                        delay = float(exc.headers.get("Retry-After", "1"))
                    except (TypeError, ValueError):
                        delay = 1
                    if math.isfinite(delay) and 0 <= delay <= 2:
                        time.sleep(delay)
                        continue
                # Provider bodies/URLs can contain guest data, cookies or credentials.
                raise CannotAnalyze(f"{provider} {operation}: HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                raise CannotAnalyze(
                    f"{provider} {operation}: unreadable response or connection"
                ) from None
            finally:
                item["elapsed_ms"] = round(1000 * (time.monotonic() - started))

    def fetch(self, source, request_identity, loader, *, ttl_seconds=0):
        """Reuse within a run; across runs only explicitly slow-moving sources may cache."""
        if source.startswith(("pms.", "prices", "overrides", "rules", "rankbreeze", "context")):
            ttl_seconds = 0
        key = identity(["mvp-v1", source, request_identity])
        with self.lock:
            if key in self.memory:
                return self.memory[key]["data"]
        cached = self.store.cached(key, ttl_seconds)
        if cached:
            record = {
                "source": source,
                "fetched_at": cached["fetched_at"],
                "cache": "hit",
                "data": cached["data"],
            }
        else:
            # loader must normalize and validate BEFORE its result is persisted.
            data = loader()
            record = {"source": source, "fetched_at": utc_now(), "cache": "miss", "data": data}
            self.store.source(key, record["fetched_at"], data)
        with self.lock:
            self.memory[key] = record
            self.sources.append({k: v for k, v in record.items() if k != "data"})
        return record["data"]

    def metrics(self):
        with self.lock:
            calls = list(self.calls)
            sources = list(self.sources)
        counts = {}
        for item in calls:
            counts[item["provider"]] = counts.get(item["provider"], 0) + 1
        return {
            "http_calls": len(calls),
            "by_provider": counts,
            "cache_hits": sum(s["cache"] == "hit" for s in sources),
            "response_bytes": sum(c["response_bytes"] for c in calls),
            "attempts": calls,
            "sources": sources,
            "external_writes": 0,
        }
