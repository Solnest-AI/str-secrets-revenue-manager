"""S2-002: a student's copy is never linked to Solnest-AI's GitHub, and it still gets updates.

At the 2026-09-29 summit students said "save" and their Claude tried to commit and push to
Ryan's repos: the README told Claude to `git clone`, and a clone stays linked to our repo. The
Comping Agent fixed it first (Solnest-AI/solnest-str-comping-agent PR #8): download instead
of clone, detach an untouched clone, never push. A detached copy cannot `git pull`, so it
updates by re-downloading, and every live run says when a newer version is out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

FETCH = Path(__file__).resolve().parent
REPO = FETCH.parents[3]
SCRIPT = REPO / "scripts" / "detach_from_github.sh"
PUBLIC = "https://github.com/Solnest-AI/str-secrets-revenue-manager.git"
BASH, GIT = shutil.which("bash"), shutil.which("git")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}
ENV.pop("STR_KEEP_GIT", None)


def git(cwd, *args):
    r = subprocess.run([GIT, *args], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


@unittest.skipIf(BASH is None or GIT is None, "bash and git needed")
class DetachScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def student_copy(self, origin=PUBLIC):
        """What `git clone` of the public repo looks like: main, tracking origin/main."""
        remote = self.root / "remote.git"
        git(self.root, "init", "-q", "--bare", "-b", "main", str(remote))
        work = self.root / "str-secrets-revenue-manager"
        git(self.root, "clone", "-q", str(remote), str(work))
        (work / "scripts").mkdir()
        shutil.copy(SCRIPT, work / "scripts" / SCRIPT.name)
        (work / "SETUP.md").write_text("setup\n", encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "init")
        git(work, "push", "-q", "origin", "main")
        git(work, "remote", "set-url", "origin", origin)
        return work

    def run_script(self, work, **env):
        r = subprocess.run([BASH, str(work / "scripts" / SCRIPT.name)], cwd=work,
                           env={**ENV, **env}, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "setup must never stop on this step")
        self.assertNotIn("—", r.stdout)
        return r.stdout

    def test_a_fresh_clone_is_detached(self):
        work = self.student_copy()
        out = self.run_script(work)
        self.assertFalse((work / ".git").exists())
        self.assertIn("detached", out)
        self.assertIn("update the revenue manager", out)
        self.assertTrue((work / "SETUP.md").exists(), "only .git goes; the tool stays")
        self.assertIn("not attached", self.run_script(work), "running it again is a no-op")

    def test_ssh_and_mixed_case_addresses_count(self):
        work = self.student_copy(origin="git@github.com:solnest-ai/STR-Secrets-Revenue-Manager")
        self.run_script(work)
        self.assertFalse((work / ".git").exists())

    def test_a_working_copy_is_never_touched(self):
        for change in ("edit", "branch", "commit", "stash", "other_origin"):
            with self.subTest(change=change):
                self.tearDown()
                self.setUp()
                work = self.student_copy(
                    origin="https://github.com/someone/fork.git" if change == "other_origin" else PUBLIC)
                if change == "edit":
                    (work / "SETUP.md").write_text("changed\n", encoding="utf-8")
                elif change == "branch":
                    git(work, "branch", "feature")
                elif change == "commit":
                    (work / "new.py").write_text("x = 1\n", encoding="utf-8")
                    git(work, "add", "new.py")
                    git(work, "commit", "-qm", "local work")
                elif change == "stash":
                    (work / "SETUP.md").write_text("wip\n", encoding="utf-8")
                    git(work, "stash", "-q")
                out = self.run_script(work)
                self.assertTrue((work / ".git").is_dir())
                self.assertIn("kept attached", out)
                self.assertEqual(git(work, "remote").strip(), "origin")

    def test_a_developer_can_opt_out(self):
        work = self.student_copy()
        (work / ".git" / "str-keep-attached").touch()
        self.assertIn("still attached", self.run_script(work))
        (work / ".git" / "str-keep-attached").unlink()
        self.assertIn("still attached", self.run_script(work, STR_KEEP_GIT="1"))
        self.assertTrue((work / ".git").is_dir())

    def test_script_keeps_lf_endings(self):
        self.assertNotIn(b"\r\n", SCRIPT.read_bytes())


class DocsSendStudentsToTheDownload(unittest.TestCase):
    def read(self, rel):
        return (REPO / rel).read_text(encoding="utf-8")

    def test_readme_tells_claude_to_download_not_clone(self):
        note = self.read("README.md").split("> **Claude:**", 1)[1].split("\n\n## ", 1)[0]
        self.assertIn("archive/refs/heads/main.tar.gz", note)
        self.assertIn("do **not** `git clone`", note)
        self.assertIn("Never commit, push, fork", note)
        # the one git pull left is the last update of a copy cloned before this fix: a download
        # over a clone looks like local edits, and the unlink step would then keep it linked
        self.assertEqual(note.count("pull --ff-only"), 1)
        self.assertNotIn("git pull", note)

    def test_setup_detaches_before_anything_else(self):
        setup = self.read("SETUP.md")
        self.assertLess(setup.index("detach_from_github.sh"), setup.index("## 0. The tools"))
        self.assertIn("Never commit, push, fork", setup)

    def test_claude_md_forbids_pushing_and_says_how_to_update(self):
        claude = self.read("CLAUDE.md")
        self.assertIn("Never commit, push, fork", claude)
        update = claude.split('## When the user says "update the revenue manager"', 1)[1]
        self.assertIn("archive/refs/heads/main.tar.gz", update)
        self.assertIn("step 2 of `SETUP.md`", update)
        self.assertIn("quit and reopen", update)
        # unlink an old clone BEFORE anything new lands on it, or it reads as local edits
        self.assertLess(update.index("detach_from_github.sh"), update.index("archive/refs/heads/main.tar.gz"))

    def test_the_skill_never_strips_a_developers_git_but_knows_how_to_update(self):
        skill = self.read("revenue-manager-plugin/skills/revenue-manager/SKILL.md")
        self.assertIn("Never commit, push, fork", skill)
        # a pricing run never unlinks anything; only an explicit update request points at CLAUDE.md
        self.assertNotIn("detach_from_github", skill)
        self.assertIn('"update the revenue manager"', skill)
        self.assertIn("claude plugin marketplace list", skill)

    def test_no_em_dash_in_the_student_facing_docs(self):
        for rel in ("README.md", "SETUP.md", "CLAUDE.md"):
            with self.subTest(rel=rel):
                self.assertNotIn("—", self.read(rel))


class UpdateNotice(unittest.TestCase):
    NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)

    def setUp(self):
        import update_check  # imported here so the other classes still run if it breaks
        self.uc = update_check
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "update.json"
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def remote(self, text):
        def fetch(url, timeout):
            self.calls.append((url, timeout))
            if isinstance(text, Exception):
                raise text
            return text
        return fetch

    def notice(self, local, text, now=None):
        return self.uc.update_notice(local, fetch=self.remote(text), cache_file=self.cache,
                                          now=now or self.NOW)

    def test_a_newer_version_on_github_is_named_with_the_words_to_say(self):
        line = self.notice("5.0.10", "5.1.0\n")
        self.assertIn("5.1.0", line)
        self.assertIn("5.0.10", line)
        self.assertIn("update the revenue manager", line)
        self.assertNotIn("—", line)
        self.assertEqual(self.calls[0][0], self.uc.REMOTE_VERSION_URL)
        self.assertLessEqual(self.calls[0][1], 5)

    def test_same_or_older_says_nothing(self):
        self.assertIsNone(self.notice("5.1.0", "5.1.0"))
        self.cache.unlink()
        self.assertIsNone(self.notice("5.1.0", "5.0.10"))

    def test_versions_compare_as_numbers_not_text(self):
        self.assertIsNotNone(self.notice("5.0.9", "5.0.10"))

    def test_github_unreachable_or_garbage_never_breaks_a_run(self):
        self.assertIsNone(self.notice("5.0.10", OSError("offline")))
        self.cache.unlink()
        self.assertIsNone(self.notice("5.0.10", "<html>rate limited</html>"))

    def test_checks_github_at_most_once_a_day(self):
        self.notice("5.0.10", "5.1.0")
        self.assertIsNotNone(self.notice("5.0.10", "9.9.9", now=self.NOW + timedelta(hours=5)))
        self.assertEqual(len(self.calls), 1, "the cached answer is reused within 24 hours")
        self.assertIn("5.1.0", self.notice("5.0.10", "5.1.0", now=self.NOW + timedelta(hours=5)))
        self.notice("5.0.10", "5.2.0", now=self.NOW + timedelta(hours=25))
        self.assertEqual(len(self.calls), 2)

    def test_an_offline_check_is_not_retried_every_run(self):
        self.notice("5.0.10", OSError("offline"))
        self.notice("5.0.10", "5.1.0", now=self.NOW + timedelta(hours=1))
        self.assertEqual(len(self.calls), 1)

    def test_an_unreadable_cache_file_is_ignored(self):
        self.cache.write_text("{not json", encoding="utf-8")
        self.assertIsNotNone(self.notice("5.0.10", "5.1.0"))

    def test_the_local_version_is_the_installed_plugins(self):
        manifest = json.loads((FETCH.parents[2] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(self.uc.local_version(), manifest["version"])


if __name__ == "__main__":
    unittest.main()


class CardEndsWithTheNotice(unittest.TestCase):
    def setUp(self):
        import update_check
        self.uc, self.saved = update_check, update_check.update_notice
        update_check.update_notice = lambda local, **kw: f"UPDATE AVAILABLE: 9.9.9 (this copy is {local})"

    def tearDown(self):
        self.uc.update_notice = self.saved

    def test_a_live_run_prints_the_notice_after_the_card(self):
        import analyze90
        self.assertEqual(analyze90.closing_lines("live"),
                         [f"UPDATE AVAILABLE: 9.9.9 (this copy is {self.uc.local_version()})"])

    def test_fixture_and_replay_runs_never_touch_the_network(self):
        import analyze90
        self.uc.update_notice = lambda *a, **k: self.fail("no update check off a live run")
        self.assertEqual(analyze90.closing_lines("fixture"), [])
        self.assertEqual(analyze90.closing_lines("replay"), [])
