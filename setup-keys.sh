#!/usr/bin/env bash
# setup-keys.sh — reads your .env and wires up your keys.
#
# Your keys go from the .env file straight into the tool. They are never printed,
# never sent to the AI, and never written into a chat. This script only ever
# reports OK or MISSING.
set -uo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌ No .env file found in this folder."
  echo "   Copy .env.template to .env, paste your keys into it, and run this again."
  exit 1
fi

# Load the .env WITHOUT printing anything.
set -a; . ./.env; set +a

missing=0
found=0
need() {  # need VAR "Human name"
  if [ -z "${!1:-}" ]; then echo "  ⬜ $2 — blank in .env ($1), skipping"; missing=$((missing+1))
  else echo "  ✅ $2"; found=$((found+1)); fi
}

echo "Checking your .env..."

need PRICELABS_API_KEY "PriceLabs"
need HOSPITABLE_API_KEY "Hospitable"
need TURNO_API_TOKEN "Turno — the long JWT (starts with eyJ)"
need TURNO_PARTNER_ID "Turno — the partner UUID"
need AIRROI_API_KEY "AirROI (free key)"

if [ "$found" -eq 0 ]; then
  echo
  echo "❌ Nothing is filled in yet. Open the .env file, paste your key(s), save, and run this again."
  echo "   (See KEYS.md for where to get each one.)"
  exit 1
fi
echo


# Fan the root .env out to each MCP server folder that expects its own.
for s in pricelabs hospitable turno airroi rankbreeze; do
  [ -d "mcp-servers/$s" ] && cp .env "mcp-servers/$s/.env" 2>/dev/null && echo "  ✅ $s configured"
done
if [ -n "${RANKBREEZE_SESSION:-}" ] && [ -d mcp-servers/rankbreeze ]; then
  printf '%s' "$RANKBREEZE_SESSION" > mcp-servers/rankbreeze/session.txt
fi
echo; echo "Done. Now FULLY QUIT AND REOPEN Claude Code so it picks up your keys."
