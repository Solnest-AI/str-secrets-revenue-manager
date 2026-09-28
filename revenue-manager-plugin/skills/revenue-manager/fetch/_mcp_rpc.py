"""One MCP JSON-RPC session over the runner's read-only transport (RankBreeze's hosted MCP,
IntelliHost's MCP). Three copies of this lived in _mvp_sources, setup_properties and
_rank_intellihost and had drifted: one was thread-safe, two were not.

Thread-safe: analyze90 runs funnel + rankings in a 4-worker pool on ONE source. Each call owns
its request id, `initialize` goes out once under a lock (even when the server returns no session
id: re-sending it on every call spent the read budget twice over), and the session header is
read back from every reply, whether the server answers with plain JSON or an SSE event stream.
"""

from __future__ import annotations

import itertools
import json
import threading

from _mvp_store import CannotAnalyze

PROTOCOL = "2024-11-05"


class McpRpc:
    def __init__(self, client, provider: str, url: str, *, client_name: str, headers=None, label=None):
        self.client, self.provider, self.url = client, provider, url
        self.client_name, self.headers, self.label = client_name, dict(headers or {}), label or provider
        self._session = None
        self._initialized = False
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._init_lock = threading.Lock()

    def call(self, method: str, params: dict) -> dict:
        """One JSON-RPC request: the `result` object, or CannotAnalyze on an error reply."""
        with self._lock:
            rid = next(self._ids)
            session = self._session
        headers = {**self.headers, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        text, resp_headers = self.client.request(
            self.provider, "rpc", self.url, headers=headers, text=True,
            body={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        new_session = next((v for k, v in resp_headers.items() if k.lower() == "mcp-session-id"), None)
        if new_session:
            with self._lock:
                self._session = new_session
        if text.lstrip().startswith("{"):
            result = json.loads(text)
        else:
            events = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
            result = next((x for x in reversed(events) if x.get("id") == rid), {})
        if result.get("error") or "result" not in result:
            raise CannotAnalyze(f"{self.label} RPC returned an error")
        return result["result"]

    def initialize(self) -> None:
        """`initialize` once per session, whatever the thread count."""
        if self._initialized:
            return
        with self._init_lock:
            if not self._initialized:
                self.call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                         "clientInfo": {"name": self.client_name, "version": "1"}})
                self._initialized = True

    def tool(self, name: str, arguments: dict) -> dict:
        """tools/call after the one-time initialize; the raw tool result."""
        self.initialize()
        return self.call("tools/call", {"name": name, "arguments": arguments})
