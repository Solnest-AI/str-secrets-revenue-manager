#!/usr/bin/env bash
# Build the attendee release zips from the committed tree, so the zip and a GitHub clone are
# byte-identical: `git archive HEAD` ships exactly what is tracked (no .env, no caches, no
# __pycache__, no local venvs). Uncommitted changes are NOT in the zip, so this refuses to run
# on a dirty tree.
#
#   bash scripts/build-release.sh [OUT_DIR]      # OUT_DIR defaults to ../release
#
# Writes OUT_DIR/str-secrets-revenue-manager-v<VERSION>.zip and
#        OUT_DIR/str-secrets-revenue-manager.zip (same bytes), then runs check-release.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="str-secrets-revenue-manager"
OUT="${1:-../release}"

if [ -n "$(git status --porcelain)" ]; then
  echo "❌ uncommitted changes: commit first (the zip is built from HEAD, not the working tree)"
  git status --short
  exit 1
fi

VERSION="$(tr -d '[:space:]' < VERSION)"
[ -n "$VERSION" ] || { echo "❌ VERSION is empty"; exit 1; }

mkdir -p "$OUT"
VERSIONED="$OUT/$NAME-v$VERSION.zip"
LATEST="$OUT/$NAME.zip"
TMP="$(mktemp "${TMPDIR:-/tmp}/$NAME.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

git archive --format=zip --prefix="$NAME/" -o "$TMP" HEAD
cp "$TMP" "$VERSIONED"
cp "$TMP" "$LATEST"
echo "built $VERSIONED"
echo "built $LATEST"
echo "from  $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"

bash scripts/check-release.sh "$OUT"
