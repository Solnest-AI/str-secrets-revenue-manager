# Revenue Manager: summit setup

Claude: if someone says "set up the revenue manager", follow this file top to bottom.
Run every command from this folder (the one this file is in). Say what each step found in
one plain line. Never ask for keys in the chat.

**Before this file:** the STR Secrets connections kit must be done. That kit is the real
setup: it connects the PMS, the pricing tool, Supabase and the optional tools, and puts
every key where it belongs. If the attendee has not run it, stop here and send them to it
("Set up my connections"). This file only installs the Revenue Manager and gets it ready.

## 1. Check the connections

In the connections kit folder, say "Check my connections". These rows must be green:

- a PMS (Hospitable, Hostaway, Guesty, ...)
- a pricing tool (PriceLabs or Beyond)
- Supabase (`supabase-revenue-manager`)

RankBreeze, IntelliHost, AirROI and Turno are optional. Missing ones show up as a named
gap on each property card; they never stop a run.

## 2. Install the plugin

One block, run as written from this folder. It finds the `claude` command (the desktop app
does not put it on the PATH, but the connections kit installed it), adds this folder as a
plugin source, and installs from it:

```bash
CLAUDE_BIN="$(command -v claude)"; for c in ~/.local/bin/claude ~/.local/bin/claude.exe; do [ -z "$CLAUDE_BIN" ] && [ -x "$c" ] && CLAUDE_BIN="$c"; done; [ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(ls -t ~/Library/Application\ Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude 2>/dev/null | head -1)"; if [ -z "$CLAUDE_BIN" ]; then echo "claude: NOT FOUND (finish the connections kit's Claude Code row first)"; else "$CLAUDE_BIN" plugin marketplace add "$PWD" && "$CLAUDE_BIN" plugin install revenue-manager@str-secrets-revenue-manager; fi
```

Two lines start with ✔ when it worked. Then **fully quit and reopen Claude Code** (Cmd+Q on
Mac, not just closing the window). The skill loads on the next start.

## 3. First run

Ask the operator one question: **what markup do you add per channel?** (For example
Airbnb 16%, VRBO 20%. "No markup" is a real answer: that is 0.) Never work it out from the
calendar; a difference between the PMS and PriceLabs is not a markup.

**Hospitable + PriceLabs:** the skill has a tested runner, and it needs one setup pass that
maps each property to PriceLabs (and RankBreeze, if connected) and stores the markups.
From this folder, dry run first, then for real:

```bash
python3 revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --markup airbnb=16 --markup vrbo=20 --dry-run
python3 revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --markup airbnb=16 --markup vrbo=20
```

It lists every property: ✅ mapped, or ❌ with the reason (a property that is not in
PriceLabs cannot be priced, and it says so instead of skipping it quietly). Running it
again is safe.

**Any other PMS or pricing tool:** no setup pass. The skill asks for the markup on its
first run and works from the connected tools directly.

## 4. Test

Say **"check my pricing"**. The first card should open with the Flywheel line, name any
missing piece at the top, show the recommended min price for each listing, and end by
asking whether to apply the changes. Nothing changes unless the answer is yes.

Done. If anything above failed, the line it printed says why; fix that one thing and run
the step again.
