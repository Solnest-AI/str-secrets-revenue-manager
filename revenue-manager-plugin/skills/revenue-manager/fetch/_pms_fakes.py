"""Fake HTTP for the PMS adapter and write-target tests. Nothing here touches the network.

FakeOpener stands in for urllib's opener: it records every physical request (method, host,
path, query pairs, lower-cased headers, parsed JSON body) and answers from a route table.
A route answer >= 400 raises a real urllib HTTPError whose body carries SECRET_BODY, so a test
can prove no error message ever echoes a vendor response.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

SECRET_BODY = "SECRET-BODY-guest@example.com-token-abc"


class _Resp:
    def __init__(self, status, payload, headers=None):
        self.status = status
        self.headers = headers or {}
        self._raw = b"" if payload is None else (payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """routes: {(METHOD, path): answer | [answers...] | callable(record) -> answer}; an answer is
    (status, payload) or just a payload (status 200). A list is consumed in order."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = []

    def open(self, req, timeout=None):
        u = urlsplit(req.full_url)
        body = req.data
        rec = {"method": req.get_method(), "host": u.netloc, "path": u.path, "query_string": u.query,
               "query": parse_qsl(u.query, keep_blank_values=True),
               "headers": {k.lower(): v for k, v in req.header_items()},
               "body_bytes": body or b"", "body": json.loads(body) if body else None, "timeout": timeout}
        self.requests.append(rec)
        route = self.routes.get((rec["method"], rec["path"]))
        if route is None:
            raise AssertionError(f"unexpected request {rec['method']} {rec['path']}")
        if isinstance(route, list):
            answer = route.pop(0) if len(route) > 1 else route[0]
        elif callable(route):
            answer = route(rec)
        else:
            answer = route
        status, payload = answer if isinstance(answer, tuple) else (200, answer)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", {"Retry-After": "0"},
                                         io.BytesIO(json.dumps({"detail": SECRET_BODY}).encode()))
        return _Resp(status, payload)

    def calls(self, method=None, path=None):
        return [r for r in self.requests if (method is None or r["method"] == method)
                and (path is None or r["path"] == path)]


def connections(**values):
    return SimpleNamespace(values=dict(values), paths={}, account=lambda p: f"acct-{p}",
                           account_or=lambda p: f"acct-{p}")


def read_client(opener, max_calls=40):
    """The real metered ReadClient over a throwaway in-memory-ish store."""
    import tempfile
    from pathlib import Path

    from _mvp_store import ReadClient, Store
    tmp = tempfile.TemporaryDirectory()
    client = ReadClient(Store(Path(tmp.name) / "w.sqlite"), max_calls=max_calls, opener=opener)
    client._tmp = tmp  # keep the directory alive with the client
    return client
