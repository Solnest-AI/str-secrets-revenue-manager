#!/usr/bin/env bash
# Everything the kit runs on, installed from inside the Claude Code desktop app.
#
#   bash install-tools.sh            install whatever is missing, one line per tool
#   bash install-tools.sh --check    report only, install nothing
#
# Exit 0: all present, carry on.  Exit 3: something was installed (or was installed by hand earlier
# and is not on this app's PATH yet) and the app has to be quit and reopened before it can see it
# (PATH and environment reach a running app only at launch); --check exits 3 for that too.
# Exit 1: something could not be installed here; the line says which connectors/system-*.md to open.
#
# Windows facts this script is built around (2026-09-28 attendee report, reproduced on uv 0.12.19):
#   * The desktop app is a Microsoft Store (MSIX) app. Anything it writes under AppData\Roaming or
#     AppData\Local is silently redirected into the app's own sandbox folder. uv's default Python
#     home is AppData\Roaming\uv\python, and `uv python install` fails there with "Missing expected
#     target directory for Python minor version link". So Python goes under the user profile
#     (%USERPROFILE%\.uv\python) and UV_PYTHON_INSTALL_DIR points at it, persisted with setx.
#   * `python` and `python3` on a fresh Windows box are Microsoft Store shims that open the Store.
#     Nothing here ever calls them. Every Python in this kit runs through uv.
#   * A tool installed mid-session is not on the app's PATH until the app restarts, so this script
#     drives the fresh installs by absolute path and then asks for the one restart.
set -u
cd "$(dirname "$0")"
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1
INSTALLED=0; BROKEN=0
# OFF_PATH: a tool that is on disk but not on this app's PATH (installed by hand mid-session, e.g.
# winget). The kit's later commands call it by bare name, so that needs the same restart an
# install does; the 2026-09-28 report's "uv ❌ until the app restarted" was exactly this.
OFF_PATH=""
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) WIN=1 ;; *) WIN=0 ;; esac

ok()   { printf '✅ %s\n' "$1"; }
bad()  { printf '❌ %s\n' "$1"; BROKEN=1; }
info() { printf '   %s\n' "$1"; }
warn() { printf '⚠️  %s\n' "$1"; }

# ---------------------------------------------------------------- git --
# Git counts only when it answers. On a Mac /usr/bin/git is always there, but until Apple's command
# line tools are installed it is a stub that prints no version and exits 1, so `command -v git`
# alone passed on Macs with no Git at all (2026-09-28 audit).
GIT_V="$(git --version 2>/dev/null | awk '{print $3}')"
if [ -n "$GIT_V" ]; then
  ok "Git $GIT_V"
elif [ $WIN -eq 1 ] && { [ -x /mingw64/bin/git.exe ] || [ -x /cmd/git.exe ] || [ -x "/c/Program Files/Git/cmd/git.exe" ]; }; then
  # Inside the desktop app the Bash tool IS Git Bash, so Git is installed even when this
  # shell's PATH does not list it.
  ok "Git (installed with Git Bash)"
elif [ $WIN -eq 1 ]; then
  bad "Git: not found. connectors/system-git.md (the Git for Windows installer, keep every default)"
elif [ $CHECK -eq 1 ]; then
  warn "Git: Apple's command line tools are missing (install mode opens Apple's installer)"
else
  # A Mac needs Git only for updates once this folder is here, so this opens Apple's installer
  # and carries on instead of stopping the setup on a 5 to 15 minute download.
  xcode-select --install >/dev/null 2>&1
  warn "Git: Apple's command line tools are missing. Apple's installer window just opened: click Install, then Agree. It finishes on its own in 5 to 15 minutes; nothing in this setup waits for it."
fi

# --------------------------------------------------------------- node --
node_ok() { local v; v="$("$1" -v 2>/dev/null | tr -d 'v\r')"; [ -n "$v" ] && [ "${v%%.*}" -ge 20 ] 2>/dev/null && printf '%s' "$v"; }
NODE_BIN=""
for c in "$(command -v node 2>/dev/null)" "/c/Program Files/nodejs/node.exe" "/usr/local/bin/node" "/opt/homebrew/bin/node"; do
  [ -n "$c" ] && [ -x "$c" ] && NODE_BIN="$c" && break
done
NODE_V=""; [ -n "$NODE_BIN" ] && NODE_V="$(node_ok "$NODE_BIN")"
if [ -n "$NODE_V" ] && ! command -v node >/dev/null 2>&1; then
  ok "Node.js v$NODE_V (installed, but this app started before it was; restart needed)"; OFF_PATH="$OFF_PATH Node.js"
elif [ -n "$NODE_V" ]; then
  ok "Node.js v$NODE_V"
elif [ $CHECK -eq 1 ]; then
  bad "Node.js: missing or older than 20 (install mode adds it)"
elif [ $WIN -eq 1 ] && command -v winget >/dev/null 2>&1; then
  info "installing Node.js LTS with winget. Windows shows a permission prompt (User Account Control): click Yes."
  # --source winget: without it winget also queries the Microsoft Store source and aborts with
  # 0x8a15003b when the Store is unreachable. Its last line is kept, so a failure says why.
  WG_SAID="$(winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-source-agreements --accept-package-agreements --silent 2>&1 | tr '\r' '\n' | grep -v '^[[:space:]]*$' | tail -1)"
  NODE_V="$(node_ok "/c/Program Files/nodejs/node.exe")"
  if [ -n "$NODE_V" ]; then ok "Node.js v$NODE_V (just installed)"; INSTALLED=1
  else bad "Node.js: winget could not install it (winget said: ${WG_SAID:-nothing}). connectors/system-node.md"; fi
elif [ $WIN -eq 0 ] && command -v brew >/dev/null 2>&1; then
  info "installing Node.js with Homebrew"
  brew install node >/dev/null 2>&1
  NODE_V="$(node_ok "$(command -v node 2>/dev/null || echo /opt/homebrew/bin/node)")"
  if [ -n "$NODE_V" ]; then ok "Node.js v$NODE_V (just installed)"; INSTALLED=1
  else bad "Node.js: brew could not install it. connectors/system-node.md"; fi
else
  bad "Node.js: missing and no package manager here. connectors/system-node.md (the LTS installer from nodejs.org)"
fi

# ----------------------------------------------------------------- uv --
UV_BIN=""
for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv.exe" "$HOME/.local/bin/uv" "${LOCALAPPDATA:-}/Microsoft/WinGet/Links/uv.exe"; do
  [ -n "$c" ] && [ -x "$c" ] && UV_BIN="$c" && break
done
if [ -z "$UV_BIN" ] && [ $CHECK -eq 0 ]; then
  info "installing uv (Astral's installer, into $HOME/.local/bin)"
  if [ $WIN -eq 1 ]; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" >/dev/null 2>&1
    [ -x "$HOME/.local/bin/uv.exe" ] && UV_BIN="$HOME/.local/bin/uv.exe"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    [ -x "$HOME/.local/bin/uv" ] && UV_BIN="$HOME/.local/bin/uv"
  fi
  [ -n "$UV_BIN" ] && INSTALLED=1
fi
if [ -n "$UV_BIN" ] && [ $INSTALLED -eq 1 ] && ! command -v uv >/dev/null 2>&1; then
  ok "uv $("$UV_BIN" --version 2>/dev/null | awk '{print $2}') (just installed)"
elif [ -n "$UV_BIN" ] && ! command -v uv >/dev/null 2>&1; then
  ok "uv $("$UV_BIN" --version 2>/dev/null | awk '{print $2}') (installed, but this app started before it was; restart needed)"; OFF_PATH="$OFF_PATH uv"
elif [ -n "$UV_BIN" ]; then
  ok "uv $("$UV_BIN" --version 2>/dev/null | awk '{print $2}')"
else
  bad "uv: missing. connectors/system-python-uv.md"
fi

# ------------------------------------------------------------- python --
# Where uv keeps Python. Windows: under the profile, never AppData (see the header). Anything the
# attendee already set in UV_PYTHON_INSTALL_DIR wins.
PY_HOME=""
if [ $WIN -eq 1 ]; then
  WINHOME="${USERPROFILE:-$(cygpath -w "$HOME")}"
  PY_HOME="${UV_PYTHON_INSTALL_DIR:-$WINHOME\\.uv\\python}"
fi
py_probe() {  # prints the 3.13 version uv can run right now, without downloading anything
  [ -n "$UV_BIN" ] || return 1
  if [ -n "$PY_HOME" ]; then
    UV_PYTHON_INSTALL_DIR="$PY_HOME" UV_PYTHON_DOWNLOADS=never "$UV_BIN" run --no-project --python 3.13 python -c 'import sys; print(sys.version.split()[0])' 2>/dev/null && return 0
  fi
  UV_PYTHON_DOWNLOADS=never "$UV_BIN" run --no-project --python 3.13 python -c 'import sys; print(sys.version.split()[0])' 2>/dev/null
}
PY_V="$(py_probe | tr -d '\r' | tail -1)"
if [ -n "$PY_V" ]; then
  ok "Python $PY_V (through uv)"
elif [ -z "$UV_BIN" ]; then
  bad "Python 3.13: needs uv first"
elif [ $CHECK -eq 1 ]; then
  bad "Python 3.13: not installed yet (install mode adds it through uv)"
else
  info "installing Python 3.13 through uv (never the Microsoft Store)"
  if [ $WIN -eq 1 ]; then
    UV_PYTHON_INSTALL_DIR="$PY_HOME" "$UV_BIN" python install 3.13 >/dev/null 2>&1
    # SSC_NO_PERSIST=1 (tests only): a sandboxed run must not touch the real user environment.
    [ "${SSC_NO_PERSIST:-}" = 1 ] || setx UV_PYTHON_INSTALL_DIR "$PY_HOME" >/dev/null 2>&1
  else
    "$UV_BIN" python install 3.13 >/dev/null 2>&1
  fi
  PY_V="$(py_probe | tr -d '\r' | tail -1)"
  if [ -n "$PY_V" ]; then ok "Python $PY_V (through uv, just installed)"; INSTALLED=1
  else bad "Python 3.13: uv could not install it. connectors/system-python-uv.md section 6"; fi
fi

echo
if [ $BROKEN -eq 1 ]; then
  echo "Something above is ❌. Open the connector file named on that line, then run this script again."
  exit 1
fi
if [ $INSTALLED -eq 1 ] || [ -n "$OFF_PATH" ]; then
  if [ $INSTALLED -eq 1 ]; then echo "RESTART NEEDED: something new was installed, and the app only sees new tools after it restarts."
  else echo "RESTART NEEDED: installed but not visible to this app yet:$OFF_PATH. The app only sees a new tool after it restarts."; fi
  echo "Quit the Claude Code desktop app fully (not just the window), open it again, open this same folder, and say \"Set up my connections\" again. It picks up right here."
  exit 3
fi
echo "All set. Nothing to install."
exit 0
