#!/usr/bin/env bash
# SETUP.md step 2: install (or reinstall) the plugin from this folder.
#
#   bash scripts/install_plugin.sh
#
# Finds the `claude` command (on the PATH, in ~/.local/bin, or inside the desktop app on
# Windows and Mac), removes any earlier copy of the plugin (the spring kit's, or one installed
# from another folder), adds this folder as the plugin source and installs from it. Running it
# twice is fine. The automatic updater runs it after every new release lands, so the new
# version loads the next time Claude Code starts.
#
# Exits 0 only when the installed plugin's version is this folder's VERSION.
set -uo pipefail
cd "$(dirname "$0")/.."

CLAUDE_BIN="$(command -v claude 2>/dev/null)"; for c in "$HOME/.local/bin/claude" "$HOME/.local/bin/claude.exe"; do [ -z "$CLAUDE_BIN" ] && [ -x "$c" ] && CLAUDE_BIN="$c"; done; [ -z "$CLAUDE_BIN" ] && [ -n "${APPDATA:-}" ] && CLAUDE_BIN="$(ls -t "$(cygpath -u "$APPDATA" 2>/dev/null || printf '%s' "$APPDATA")"/Claude/claude-code/*/claude.exe 2>/dev/null | head -1)"; [ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(ls -t "$HOME/Library/Application Support/Claude/claude-code/"*/claude.app/Contents/MacOS/claude 2>/dev/null | head -1)"
if [ -z "$CLAUDE_BIN" ]; then
    echo "claude: NOT FOUND"
    exit 1
fi
for old in revenue-manager@solnest-revenue-manager revenue-manager@str-secrets-revenue-manager; do "$CLAUDE_BIN" plugin uninstall "$old" >/dev/null 2>&1; done
for m in solnest-revenue-manager str-secrets-revenue-manager; do "$CLAUDE_BIN" plugin marketplace remove "$m" >/dev/null 2>&1; done
"$CLAUDE_BIN" plugin marketplace add "$PWD" && "$CLAUDE_BIN" plugin install revenue-manager@str-secrets-revenue-manager && "$CLAUDE_BIN" plugin update revenue-manager@str-secrets-revenue-manager >/dev/null 2>&1
LINE="$("$CLAUDE_BIN" plugin list 2>/dev/null | grep -A1 'revenue-manager@str-secrets' | grep Version)"
echo "plugin: $LINE"
WANT="$(tr -d '[:space:]' < VERSION)"
case "$LINE" in *"Version: $WANT") exit 0 ;; *) exit 1 ;; esac
