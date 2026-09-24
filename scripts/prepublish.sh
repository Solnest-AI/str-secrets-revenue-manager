#!/usr/bin/env bash
# Refuse to publish if anything secret-shaped, any .env, or any build product is tracked, or if
# attendee-facing text has an em-dash. Mirrors str-secrets-connections/scripts/prepublish.sh.
# Claude-facing instruction files (the skill, its references, the build docs, CLAUDE/AGENTS,
# the connector READMEs) are the code carve-out; standalone/ is the labelled legacy setup.
set -u; cd "$(dirname "$0")/.."
bad=0
git ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.template$' | grep -v '\.env\.example$' && { echo "❌ a .env is tracked"; bad=1; }
git grep -nE '(fc-[A-Za-z0-9]{20,}|pt_[A-Za-z0-9]{20,}|bpat_[A-Za-z0-9]{20,}|rb_mcp_[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|sbp_[a-f0-9]{30,}|sb_secret_[A-Za-z0-9_-]{10,})' -- . && { echo "❌ secret-shaped string tracked"; bad=1; }
git grep -nE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' -- . ':!mcp-servers' && { echo "❌ a UUID-shaped id is tracked (listing ids are private)"; bad=1; }
git ls-files | grep -E 'node_modules/|/dist/|\.venv/|__pycache__/|\.report-venv/' && { echo "❌ build products tracked"; bad=1; }
git ls-files '*.md' '*.html' | grep -vE '^(revenue-manager-plugin/skills/|revenue-manager-plugin/references/|build-|CLAUDE\.md|AGENTS\.md|mcp-servers/|standalone/)' | xargs grep -l "—" 2>/dev/null && { echo "❌ em-dash in attendee-facing text"; bad=1; }
[ $bad -eq 0 ] && echo "✅ prepublish clean"
exit $bad
