"""Says when a newer Revenue Manager release is out (S2-002). Never updates anything itself.

Updates normally install themselves: SETUP.md step 2 registers the folder with the Solnest
auto-updater, which installs each new GitHub release once a day in the background. So this
line only shows up when that is off or failing on this computer; the student says "update the
revenue manager" and Claude follows CLAUDE.md. It compares against the latest published
RELEASE (the same thing the updater installs), never main, so a push to main that is not
released yet does not nag anyone.

One HEAD request to github.com/<repo>/releases/latest (it redirects to the tag; unlike the API
it has no 60-an-hour limit per network), at most once a day (the answer is cached), with a
short timeout. Offline, rate-limited, or anything unreadable: no line, never an error. It does
not go through the read client: it is not a provider read and does not count against
--max-calls.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REMOTE_VERSION_URL = "https://github.com/Solnest-AI/str-secrets-revenue-manager/releases/latest"
TIMEOUT_SECONDS = 4
CHECK_EVERY = timedelta(hours=24)
_VERSION = re.compile(r"^\d+(\.\d+){0,3}$")


def parse(text):
    """'5.0.10' -> (5, 0, 10); anything else (an HTML error page, an empty body) -> None."""
    text = str(text or "").strip()
    return tuple(int(x) for x in text.split(".")) if _VERSION.match(text) else None


def local_version(plugin_root: Path | None = None):
    """The installed plugin's version: <plugin>/.claude-plugin/plugin.json. This file runs from
    the plugin, so that manifest is the version actually in use."""
    root = plugin_root or Path(__file__).resolve().parents[3]
    try:
        return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _fetch(url, timeout):
    """The latest release's version: /releases/latest redirects to /releases/tag/v<version>."""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "str-secrets-revenue-manager-update-check"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final = resp.geturl()
    m = re.search(r"/releases/tag/v?([^/?#]+)$", final)
    return m.group(1) if m else ""


def _cache_path():
    from _cache import cache_dir
    return Path(cache_dir("update")) / "latest-version.json"


def update_notice(local, *, fetch=None, cache_file=None, now=None):
    """One plain line when GitHub's VERSION is newer than `local`, else None. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        cache_file = Path(cache_file) if cache_file else _cache_path()
        remote, fresh = None, False
        try:
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            checked = datetime.fromisoformat(saved["checked_at"])
            if timedelta(0) <= now - checked < CHECK_EVERY:
                remote, fresh = saved.get("remote"), True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if not fresh:
            try:
                remote = (fetch or _fetch)(REMOTE_VERSION_URL, TIMEOUT_SECONDS).strip()
            except Exception:  # noqa: BLE001 - offline or blocked is not the student's problem
                remote = None
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"checked_at": now.isoformat(), "remote": remote}),
                                      encoding="utf-8")
            except OSError:
                pass
        have, latest = parse(local), parse(remote)
        if not have or not latest or latest <= have:
            return None
        return (f"UPDATE AVAILABLE: Revenue Manager {remote} is out (this copy is {local}). Say "
                "\"update the revenue manager\" to get it. Your keys, markups and history stay as "
                "they are.")
    except Exception:  # noqa: BLE001 - an update notice must never cost a pricing card
        return None
