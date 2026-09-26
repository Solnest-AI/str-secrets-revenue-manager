#!/usr/bin/env bash
# Prove the release zip is exactly the committed tree and every version string agrees.
#
#   bash scripts/check-release.sh [OUT_DIR]      # OUT_DIR defaults to ../release
#   bash scripts/check-release.sh --versions-only
#
# Fails (exit 1) on: VERSION / marketplace.json / plugin.json / CHANGELOG top entry disagreeing,
# a missing zip, the versioned and unversioned zips differing, or ANY file difference between
# the unzipped release and `git archive HEAD` (added, missing or changed files).
set -uo pipefail
cd "$(dirname "$0")/.."
NAME="str-secrets-revenue-manager"
bad=0

# ── 1. versions ────────────────────────────────────────────────────────────────
V_FILE="$(tr -d '[:space:]' < VERSION)"
V_MARKET="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' .claude-plugin/marketplace.json | head -1)"
V_PLUGIN="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' revenue-manager-plugin/.claude-plugin/plugin.json | head -1)"
V_CHANGELOG="$(sed -n 's/^## \([0-9][0-9.]*\).*/\1/p' CHANGELOG.md | head -1)"
echo "VERSION=$V_FILE marketplace.json=$V_MARKET plugin.json=$V_PLUGIN CHANGELOG=$V_CHANGELOG"
for v in "$V_MARKET" "$V_PLUGIN" "$V_CHANGELOG"; do
  [ "$v" = "$V_FILE" ] || { echo "❌ version mismatch (all four must equal VERSION=$V_FILE)"; bad=1; break; }
done
[ $bad -eq 0 ] && echo "✅ versions agree: $V_FILE"
if [ "${1:-}" = "--versions-only" ]; then exit $bad; fi

# ── 2. zips exist and are the same bytes ──────────────────────────────────────
OUT="${1:-../release}"
VERSIONED="$OUT/$NAME-v$V_FILE.zip"
LATEST="$OUT/$NAME.zip"
for z in "$VERSIONED" "$LATEST"; do
  [ -f "$z" ] || { echo "❌ missing $z (run scripts/build-release.sh \"$OUT\")"; exit 1; }
done
cmp -s "$VERSIONED" "$LATEST" || { echo "❌ $VERSIONED and $LATEST differ"; bad=1; }

# ── 3. unzipped release == git archive HEAD ───────────────────────────────────
WORK="$(mktemp -d "${TMPDIR:-/tmp}/$NAME-check.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/zip" "$WORK/git"
unzip -q "$VERSIONED" -d "$WORK/zip" || { echo "❌ could not unzip $VERSIONED"; exit 1; }
git archive --format=tar --prefix="$NAME/" HEAD | tar -x -C "$WORK/git"
if diff -r "$WORK/zip" "$WORK/git" > "$WORK/diff.txt" 2>&1; then
  echo "✅ zip matches git archive HEAD ($(find "$WORK/git" -type f | wc -l | tr -d ' ') files)"
else
  echo "❌ zip differs from git archive HEAD:"; head -40 "$WORK/diff.txt"; bad=1
fi

# The zip comment is the commit it was built from. Same tree but a different commit is fine
# (e.g. an empty or history-only commit); report it so nobody is surprised.
ZIP_COMMIT="$(unzip -z "$VERSIONED" 2>/dev/null | sed -n '2p' | tr -d '[:space:]')"
HEAD_COMMIT="$(git rev-parse HEAD)"
if [ -n "$ZIP_COMMIT" ] && [ "$ZIP_COMMIT" != "$HEAD_COMMIT" ]; then
  echo "⚠️  zip was built from ${ZIP_COMMIT:0:7}, HEAD is ${HEAD_COMMIT:0:7}"
fi

[ $bad -eq 0 ] && echo "✅ release check clean: $VERSIONED"
exit $bad
