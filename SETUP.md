# Revenue Manager: summit setup

Claude: if someone says "set up the revenue manager" or "set this up", follow this file top
to bottom. If they say "finish the revenue manager setup", they already installed and
restarted: go to step 3. Run every command from this folder (the one this file is in), and
start each one with `cd <this folder> &&`: Claude Code can reset the working folder between
commands, and step 2 registers `$PWD` as the plugin source. When step 2 tells them to reopen
Claude Code "in this folder", give them this folder's full path. Say what each step found in
one plain line. Never ask for keys in the chat.

**Before this file:** the STR Secrets connections kit must be done. That kit is the real
setup: it connects the PMS, the pricing tool, Supabase and the optional tools, and puts
every key where it belongs. If the attendee has not run it, stop here and send them to it
("Set up my connections"). This file only installs the Revenue Manager and gets it ready.

## 1. Check the connections

In the connections kit folder, say "Check my connections". These rows must be green:

- a PMS (Hospitable, Guesty, OwnerRez, Hostaway, Lodgify, Uplisting, Smoobu or Hostfully)
- a pricing tool (PriceLabs or Beyond)
- Supabase (`supabase-revenue-manager`)

RankBreeze, IntelliHost, AirROI, Turno and Breezeway are optional (and read-only). Missing ones show up as a named
gap on each property card; they never stop a run.

## 2. Install the plugin

One block, run as written from this folder. It finds the `claude` command (the desktop app
does not put it on the PATH, but the connections kit installed it), adds this folder as a
plugin source, and installs from it:

```bash
CLAUDE_BIN="$(command -v claude)"; for c in ~/.local/bin/claude ~/.local/bin/claude.exe; do [ -z "$CLAUDE_BIN" ] && [ -x "$c" ] && CLAUDE_BIN="$c"; done; [ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(ls -t ~/Library/Application\ Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude 2>/dev/null | head -1)"; if [ -z "$CLAUDE_BIN" ]; then echo "claude: NOT FOUND (finish the connections kit's Claude Code row first)"; else "$CLAUDE_BIN" plugin marketplace add "$PWD" && "$CLAUDE_BIN" plugin install revenue-manager@str-secrets-revenue-manager; fi
```

Two lines start with ✔ when it worked.

Then stop and tell the operator, in these words or close to them: **"Installed. Now fully
quit Claude Code (Cmd+Q on Mac, or close every window and end it in the tray on Windows) and
reopen it in this folder. Then say: finish the revenue manager setup."** The skill only
loads on a fresh start, so step 3 cannot run in this session. Do not start step 3 here.

## 3. First run (after the restart)

Claude: if someone says "finish the revenue manager setup", start here. First confirm the
skill loaded (the `revenue-manager` skill is in your skill list). If it is not, the restart
did not happen: ask for a full quit and reopen again, then continue.

Ask the operator one question: **what markup do you add per channel?** (For example
Airbnb 16%, VRBO 20%. "No markup" is a real answer: that is 0.) Store exactly what they
say. Never work it out from the calendar; a difference between the PMS and PriceLabs is not
a markup.

**Every PMS gets the same setup pass.** It maps each property to its pricing tool (and
RankBreeze or IntelliHost, if connected) and stores the markups. Run it from this folder,
dry run first. Pick the line for the attendee's PMS, put their Airbnb markup in place of
`<AIRBNB_PERCENT>`, and never run a placeholder as written:

| PMS | Setup line (dry run) |
|---|---|
| Hospitable | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms hospitable --markup airbnb=<AIRBNB_PERCENT> --dry-run` |
| Guesty | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms guesty --markup airbnb=<AIRBNB_PERCENT> --dry-run` |
| OwnerRez | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms ownerrez --markup airbnb=<AIRBNB_PERCENT> --dry-run` |
| Hostaway | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms hostaway --markup airbnb=<AIRBNB_PERCENT> --dry-run` |
| Lodgify | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms lodgify --markup airbnb=<AIRBNB_PERCENT> --dry-run` |
| Uplisting | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms uplisting --markup airbnb=<AIRBNB_PERCENT> --dry-run` |
| Smoobu | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms smoobu --markup airbnb=<AIRBNB_PERCENT> --dry-run` |
| Hostfully | `uv run --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms hostfully --markup airbnb=<AIRBNB_PERCENT> --dry-run` |

If the dry run looks right, run the same line without `--dry-run`. Add one more
`--markup <channel>=<percent>` per other channel they named (for example
`--markup vrbo=<VRBO_PERCENT>`); Airbnb is required. It lists every property: ✅ mapped, or
❌ with the reason (a property that is not in the pricing tool cannot be priced, and it says
so instead of skipping it quietly). Running it again is safe.

- **More than one PMS connected?** Then `--pms` is required, as above (`auto` stops and
  names the ones it found). Use the same `--pms` on every run after this, too.
- **Beyond instead of PriceLabs:** add `--pricing beyond` to the setup line. It maps each
  property to its Beyond listing by PMS id, then Airbnb id, then exact name, and never
  guesses between two.
- **The PMS sets the prices itself** (no PriceLabs or Beyond on that property): no PMS
  shares its min price through its API, so the writer won't cut a price there until a min is
  saved. Don't ask the operator for one. On the first run Claude recommends a min for each
  property, and on a yes saves it by running the setup line again (without `--dry-run`) with
  `--min-price "<exact property name>=<amount>"` added, once per property.

**A named gap is not an error.** Some PMSs don't share everything through their API:
Lodgify, Uplisting and Smoobu have no reviews, and Lodgify and Smoobu have no check-in or
check-out day rules. The card still prices. Its first line says `degraded` instead of
`analysable`, and the gap is spelled out, like this:

```
PRICED WITHOUT reviews (Review sample is unreadable or absent, not empty)
reviews: Smoobu's API documents no reviews endpoint; reviews are not read from Smoobu
Your PMS does not expose check-in or check-out day rules, so nights are read as having none. If you block arrivals on certain days, check those nights yourself.
```

Say it to the operator in one plain line and keep going. A real stop looks different: the
first line says `blocked` and gives the reason (for example no PMS calendar, so there are no
nights to price).

`uv` is what the connections kit installed and uses for every Python step, so nothing else
needs installing. **Windows:** run these exactly as written (Claude's Bash tool is Git
Bash). Don't swap in `python` or `python3`: on a fresh Windows machine that opens the
Microsoft Store instead of Python, and `uv run` never touches it.

**Wheelhouse, or no pricing tool at all:** setup still maps the properties, and marks the
pricing tool as a named gap. The skill asks for the markup on its first run and works from
the connected tools directly.

## 4. Test

Say **"check my pricing"**. The first card should open with the Flywheel line, name any
missing piece at the top, show the recommended min price for each listing, and end by
asking whether to apply the changes. Nothing changes unless the answer is yes.

Done. If anything above failed, the line it printed says why; fix that one thing and run
the step again.
