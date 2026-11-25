"""A member's copy updates by tagged release, keeps their own files, and can go back.

scripts/update.py is the Comping Agent's release updater (Solnest-AI/solnest-str-comping-agent),
with the Revenue Manager's own protected files: every .env (the connectors in mcp-servers/
keep their own), connector logins, installed tools and caches.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

V1 = {"VERSION": "5.1.1\n", "SETUP.md": "v1\n", "scripts/old.sh": "echo old\n",
      "revenue-manager-plugin/skills/revenue-manager/fetch/old.py": "x = 1\n",
      "mcp-servers/pricelabs/.env.example": "PRICELABS_API_KEY=\n", ".env.template": "A=\n"}
V2 = {"VERSION": "5.2.0\n", "SETUP.md": "v2\n", "scripts/update.py": "print('new')\n",
      "mcp-servers/pricelabs/.env.example": "PRICELABS_API_KEY=\nNEW=\n", ".env.template": "A=\nB=\n"}
# Everything the member owns. None of it is in a release, and no release may touch it.
USER = {".env": "SUPABASE_KEY=keep-me\n", ".env.local": "X=1\n",
        "mcp-servers/pricelabs/.env": "PRICELABS_API_KEY=keep\n",
        "mcp-servers/hospitable/.env": "HOSPITABLE_API_KEY=keep\n",
        "mcp-servers/rankbreeze/session.txt": "cookie\n",
        "mcp-servers/pricelabs/node_modules/pkg/index.js": "module\n",
        "mcp-servers/pricelabs/dist/index.js": "built\n",
        "mcp-servers/turno/.venv/bin/python": "py\n",
        "revenue-manager-plugin/skills/revenue-manager/fetch/.pl_cache/a.json": "{}",
        ".claude/settings.local.json": "{}", ".mcp.json": "{}"}


def _tarball(files, top="str-secrets-revenue-manager-5.2.0"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _copy(tmp_path, files=V1, manifest=True):
    root = tmp_path / "str-secrets-revenue-manager"
    for rel, body in {**files, **USER}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body.encode())
    if manifest:
        update.write_manifest(root, list(files))
    return root


def _fake(monkeypatch, releases=None, latest="v5.2.0"):
    releases = releases or {"v5.2.0": V2}
    calls = []
    monkeypatch.setattr(update, "latest_tag", lambda timeout=20: latest)

    def download(tag, timeout=60):
        calls.append(tag)
        if tag not in releases:
            raise update.UpdateError(f"could not download {tag}")
        return _tarball(releases[tag], top=f"str-secrets-revenue-manager-{tag.lstrip('v')}")
    monkeypatch.setattr(update, "download", download)
    return calls


def _snap(root):
    return {str(p.relative_to(root)).replace(os.sep, "/"): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_update_replaces_the_tool_and_keeps_every_member_file(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    _fake(monkeypatch)
    r = update.update(root)
    assert (r["status"], r["from"], r["to"]) == ("updated", "5.1.1", "5.2.0")
    assert (root / "SETUP.md").read_text(encoding="utf-8") == "v2\n"
    assert "NEW=" in (root / "mcp-servers/pricelabs/.env.example").read_text(encoding="utf-8")
    assert "B=" in (root / ".env.template").read_text(encoding="utf-8")
    assert not (root / "scripts/old.sh").exists()
    assert not (root / "revenue-manager-plugin/skills/revenue-manager/fetch/old.py").exists()
    for rel, body in USER.items():
        assert (root / rel).read_text(encoding="utf-8") == body, f"{rel} must survive an update"


def test_a_release_can_never_write_over_member_files(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    _fake(monkeypatch, releases={"v5.2.0": {**V2, **{rel: "from the release\n" for rel in USER}}})
    update.update(root)
    for rel, body in USER.items():
        assert (root / rel).read_text(encoding="utf-8") == body, f"{rel} was overwritten"
    assert not set(USER) & set(update.load_manifest(root))


def test_a_copy_without_a_manifest_rebuilds_it_from_its_own_release(tmp_path, monkeypatch):
    """Every copy before 5.2.0: no manifest, so the files 5.1.1 shipped come from the v5.1.1 tag."""
    root = _copy(tmp_path, manifest=False)
    calls = _fake(monkeypatch, releases={"v5.2.0": V2, "v5.1.1": V1})
    r = update.update(root)
    assert calls == ["v5.2.0", "v5.1.1"]
    assert "scripts/old.sh" in r["removed"]
    for rel, body in USER.items():
        assert (root / rel).read_text(encoding="utf-8") == body


def test_undo_puts_back_exactly_the_previous_version(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    before = _snap(root)
    _fake(monkeypatch)
    update.update(root)
    r = update.undo(root)
    assert (r["to"], r["from"]) == ("5.1.1", "5.2.0")
    assert _snap(root) == before


def test_already_current_downloads_nothing(tmp_path, monkeypatch):
    root = _copy(tmp_path, files={**V1, "VERSION": "5.2.0\n"})
    calls = _fake(monkeypatch)
    assert update.update(root)["status"] == "current" and calls == []


def test_a_folder_still_linked_to_git_is_refused(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    (root / ".git").mkdir()
    calls = _fake(monkeypatch)
    with pytest.raises(update.UpdateError, match="git pull"):
        update.update(root)
    assert calls == []


def test_a_release_labelled_with_the_wrong_version_is_refused(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    before = _snap(root)
    _fake(monkeypatch, releases={"v5.2.0": {**V2, "VERSION": "5.1.1\n"}})
    with pytest.raises(update.UpdateError, match="5.1.1"):
        update.update(root)
    assert _snap(root) == before


def test_nothing_the_repo_ships_is_protected():
    """A shipped file the updater refused to write would never update."""
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    assert [rel for rel in out.splitlines() if update._protected(rel)] == []
