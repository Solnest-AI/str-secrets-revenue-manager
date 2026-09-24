#!/usr/bin/env python3
"""V1: every command printed in the runbook is executed as written. No network.

The runbook is the brain. Its commands were prose that nobody ran, so it shipped a
MANDATORY safety gate whose command exits 2 because of a wrong path prefix, and the
next four lines define exit 2 as "the check could not run, do not proceed". The gate
could never run, and its failure was indistinguishable from a real gate failure.

What this asserts, for every shell command in SKILL.md and the reference files:

  1. the script it names EXISTS relative to the skill directory
  2. every long flag it passes is a flag that script actually accepts
  3. the file is syntactically valid Python and its argparse can be built
  4. NO command depends on a `cd` from an earlier block

Rule 4 is why the gate was broken. The reconciliation command was written for a cwd a
previous block had set, and the workbook command for a cwd two blocks earlier. An agent
that runs anything in between, or starts at a different step, lands somewhere else and
the command fails in a way that reads like a real refusal. Every command is therefore
self-contained and runs from the skill directory.

It does not call the network. `--help` is enough to prove a script loads and to read
its real flag list out of argparse.

Placeholders like <id> and <pms> are expected and are not resolved; this checks the
SHAPE of the command, which is the part that was wrong.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../skills/revenue-manager/fetch
SKILL = HERE.parent                             # .../skills/revenue-manager
DOCS = [SKILL / "SKILL.md"] + sorted((SKILL / "references").rglob("*.md"))

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}: {label}{'' if cond else '  -> ' + detail}")
    if not cond:
        fails.append(label)


def resolve_cd(target: str, cwd: Path) -> Path:
    """Resolve a `cd` whose prefix is a placeholder like <plugin>/skills/...

    Walk the target's segments from the right and take the longest tail that exists
    under the skill directory. `cd <plugin>/skills/revenue-manager/fetch` and
    `cd report` both land correctly, and an unknown target leaves cwd alone rather
    than silently pretending the command runs somewhere it does not.
    """
    parts = [p for p in target.strip("/").split("/") if p and not p.startswith("<")]
    for i in range(len(parts)):
        candidate = SKILL.joinpath(*parts[i:])
        if candidate.is_dir():
            return candidate
    candidate = cwd / target
    return candidate if candidate.is_dir() else cwd


def commands(text: str):
    """Every shell command, plus any `cd` the block performs.

    There is exactly one working directory: the skill directory. A `cd` is yielded so
    the caller can fail it, not so the caller can follow it.
    """
    for block in re.findall(r"```(?:bash|sh|shell)\n(.*?)```", text, re.S):
        joined = block.replace("\\\n", " ")
        for raw in joined.splitlines():
            line = raw.strip().lstrip("> ").strip()
            if not line or line.startswith("#"):
                continue
            yield line


def script_of(cmd: str) -> str | None:
    """The .py a command runs. Comments are stripped first: a `# or: python3 x.py`
    aside is documentation, not a second command to verify."""
    body = cmd.split("#")[0]
    m = re.search(r"(?:python3|python)\s+(\S+\.py)", body)
    return m.group(1) if m else None


def long_flags(cmd: str) -> list[str]:
    # strip anything after a shell comment or a redirect before reading flags
    body = cmd.split("#")[0].split(">")[0]
    return sorted(set(re.findall(r"(?<!\w)--[a-z][a-z0-9-]+", body)))


_help_cache: dict[tuple, str] = {}


def help_text(path: Path, sub: str | None = None) -> str:
    key = (path, sub)
    if key not in _help_cache:
        argv = [sys.executable, "-B", str(path), *([sub] if sub else []), "--help"]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        _help_cache[key] = proc.stdout + proc.stderr
    return _help_cache[key]


def subcommand_of(cmd: str, script: str, top_help: str) -> str | None:
    """`x.py apply --code ...` -> "apply", when the script's usage lists {..,apply,..}.
    Flags must then be read from THAT subcommand's help: the top-level help of a
    subcommand script shows only its description, and a docstring that mentions a
    flag would otherwise pass a flag the parser no longer accepts."""
    after = cmd.split("#")[0].split(script, 1)[1].split()
    word = after[0] if after else ""
    choices = re.search(r"\{([a-z0-9_,-]+)\}", top_help)
    if word and not word.startswith("-") and choices and word in choices.group(1).split(","):
        return word
    return None


print("runbook command smoke test (V1)\n")

seen = 0
for doc in DOCS:
    if not doc.is_file():
        continue
    rel = doc.relative_to(SKILL)
    for cmd in commands(doc.read_text(encoding="utf-8")):
        if cmd.startswith("cd "):
            check(f"[{rel}] no command depends on a cd: {cmd[:56]}", False,
                  "every command must run from the skill directory as written; a cwd "
                  "carried across blocks breaks the moment a step is run on its own")
            continue
        script = script_of(cmd)
        if not script:
            continue
        seen += 1
        short = cmd[:64]
        resolved = SKILL / script
        ok = resolved.is_file()
        detail = ""
        if not ok:
            hit = next((c for c in (HERE, SKILL, SKILL / "report")
                        if (c / Path(script).name).is_file()), None)
            detail = (f"{script!r} does not exist from the skill directory; it lives in "
                      f"{hit.name}/" if hit else f"{script!r} does not exist anywhere")
        check(f"[{rel}] script resolves: {short}", ok, detail)
        if not ok:
            continue

        text = help_text(resolved)
        check(f"[{rel}] argparse builds: {Path(script).name}",
              "usage:" in text.lower(), text[:160])
        sub = subcommand_of(cmd, script, text)
        if sub:
            text = help_text(resolved, sub)
            check(f"[{rel}] subcommand builds: {Path(script).name} {sub}",
                  "usage:" in text.lower(), text[:160])
        for flag in long_flags(cmd):
            check(f"[{rel}] {Path(script).name}{' ' + sub if sub else ''} accepts {flag}",
                  flag in text, f"not in --help for {script}{' ' + sub if sub else ''}")

check("the runbook actually contains commands to check", seen > 0,
      "if this fires, the extractor stopped matching and everything above is vacuous")

# Every reducer must be syntactically importable, whether or not the runbook names it.
for path in sorted(HERE.glob("*.py")):
    if path.name.startswith("smoke_test"):
        continue
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        ok, why = True, ""
    except SyntaxError as exc:
        ok, why = False, str(exc)
    check(f"parses: {path.name}", ok, why)

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails[:6]))
    sys.exit(1)
print("all checks passed.")
