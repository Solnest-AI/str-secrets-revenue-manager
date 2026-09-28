# Revenue Manager: summit setup

Claude: if someone says "set up the revenue manager" or "set this up", follow this file top
to bottom. If they say "finish the revenue manager setup", they already installed and
restarted: go to step 3. Run every command from this folder (the one this file is in), and
start each one with `cd <this folder> &&`: Claude Code can reset the working folder between
commands, and step 2 registers `$PWD` as the plugin source. When step 2 tells them to reopen
Claude Code "in this folder", give them this folder's full path. Say what each step found in
one plain line. Never ask for keys in the chat.

**The connections kit went out the week before the summit**, so most attendees arrive with
their PMS, pricing tool and Supabase already connected. Do not take that on trust: step 0
checks the tools and step 1 checks the connections, and both repair what they find missing
before anything is installed. The attendee never opens a terminal. Every command below is
yours to run through your Bash tool (Git Bash on Windows); the only things they ever do
themselves are paste a key into a file, click Yes on an installer prompt, and restart the app.

## 0. The tools

Run the installer that ships in this folder. It checks Git, Node.js, uv and Python 3.13,
installs whatever is missing (on Windows, Node shows one permission prompt: tell them to click
Yes), and ends with one of three lines:

```bash
bash install-tools.sh
```

- `All set`: go to step 1.
- `RESTART NEEDED`: something was just installed, and the app only sees a new tool after it
  restarts. Tell the attendee, in these words or close to them: **"One tool was missing, so I
  installed it. Now fully quit Claude Code (on Mac press Cmd+Q; on Windows close every Claude
  Code window, and if a Claude icon is still in the system tray, right-click it and quit) and
  reopen it in this folder. Then say: set this up."** Stop here. The next run starts at step 0
  again and finds everything in place.
- A ❌ line that names a `connectors/system-*.md` file: that tool cannot be installed from
  here (a Mac without Homebrew, for example). Open that file in the connections kit (step 1
  shows where the kit is) and follow its Path A for the attendee's OS, then run the installer
  again.

## 1. The connections

Find the connections kit. It was unzipped somewhere easy last week, usually the Desktop, so
look in the usual places and take the first hit:

```bash
KIT=""; for b in "$HOME/Desktop" "$HOME/OneDrive/Desktop" "$HOME/Documents" "$HOME/Downloads" "$HOME"; do [ -z "$KIT" ] && [ -d "$b" ] && KIT="$(find "$b" -maxdepth 3 -name CONNECTIONS.md -path '*str-secrets-connections*' -not -path '*/node_modules/*' -not -path '*/Library/*' 2>/dev/null | head -1)"; done; KIT="${KIT%/CONNECTIONS.md}"; if [ -n "$KIT" ]; then echo "kit: $KIT"; else echo "kit: NOT FOUND"; fi
```

`kit: NOT FOUND` means it is somewhere else or was never downloaded. Ask once where they put
it ("Where did you unzip the STR Secrets connections kit?") and look there. If they never
did, get it for them:

```bash
git clone --quiet https://github.com/Solnest-AI/str-secrets-connections "$HOME/str-secrets-connections" && echo "kit: $HOME/str-secrets-connections"
```

Then follow `<kit>/CONNECTIONS.md` from its start (it says hello, asks four questions and
connects everything) and come back to step 2 once its done message has printed.

With the kit found, run its scoreboard and print the whole board:

```bash
cd "<kit>" && bash check-connections.sh
```

These rows must be ✅ before step 2:

- the PMS API row (Hospitable, Guesty, OwnerRez, Hostaway, Lodgify, Uplisting, Smoobu or Hostfully)
- the pricing API row (PriceLabs or Beyond)
- `Supabase MCP` and `Supabase shared project`

Everything else on the board is optional for the Revenue Manager (RankBreeze, IntelliHost,
AirROI, Turno, Breezeway, Meta, Kie, Gemini, Firecrawl): a missing one is a named gap on each
property card, never a stop. Do not chase those rows here.

A required row that is not ✅:

- `❌ No .env yet`: the kit was downloaded but never run. Follow `<kit>/CONNECTIONS.md` from
  its start, then come back to step 2.
- `❌` or `⚠️`: open the connector file the row names, in `<kit>/connectors/`, and follow its
  section 3 (a key: you open `<kit>/.env` for them, they paste it there, never in the chat) or
  section 4 (a sign-in). Then run the scoreboard again.
- `🔒`: a server was registered and the app has not restarted since. Use the restart words
  from step 0, with "set this up" as the thing to say afterwards, and stop here.

## 2. Install the plugin

One block, run as written from this folder. It finds the `claude` command (on the PATH, in
`~/.local/bin`, or inside the desktop app itself on Windows and Mac), removes the spring kit's
older copy of the plugin if there is one, adds this folder as a plugin source and installs from
it. Running it twice is fine:

```bash
CLAUDE_BIN="$(command -v claude 2>/dev/null)"; for c in "$HOME/.local/bin/claude" "$HOME/.local/bin/claude.exe"; do [ -z "$CLAUDE_BIN" ] && [ -x "$c" ] && CLAUDE_BIN="$c"; done; [ -z "$CLAUDE_BIN" ] && [ -n "${APPDATA:-}" ] && CLAUDE_BIN="$(ls -t "$APPDATA"/Claude/claude-code/*/claude.exe 2>/dev/null | head -1)"; [ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(ls -t "$HOME/Library/Application Support/Claude/claude-code/"*/claude.app/Contents/MacOS/claude 2>/dev/null | head -1)"; if [ -z "$CLAUDE_BIN" ]; then echo "claude: NOT FOUND"; else "$CLAUDE_BIN" plugin uninstall revenue-manager@solnest-revenue-manager >/dev/null 2>&1; "$CLAUDE_BIN" plugin marketplace remove solnest-revenue-manager >/dev/null 2>&1; { "$CLAUDE_BIN" plugin marketplace add "$PWD" 2>/dev/null || "$CLAUDE_BIN" plugin marketplace update str-secrets-revenue-manager; } && { "$CLAUDE_BIN" plugin install revenue-manager@str-secrets-revenue-manager 2>/dev/null || true; } && "$CLAUDE_BIN" plugin update revenue-manager@str-secrets-revenue-manager; fi
```

The last line starts with ✔ when it worked (the update after the install is what records this
folder's version, so a re-run after a newer download shows the new number).

`claude: NOT FOUND` means the command-line copy of Claude Code is not on this computer (the
desktop app alone does not put it on the PATH). Install it, then run the block above again.
It looks frozen for a minute or two; that is the download:

```bash
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex" ;; *) curl -fsSL https://claude.ai/install.sh | bash ;; esac
```

Then stop and tell the operator, in these words or close to them: **"Installed. Now fully
quit Claude Code (on Mac press Cmd+Q; on Windows close every Claude Code window, and if a
Claude icon is still in the system tray, right-click it and quit) and reopen it in this folder. Then say: finish the revenue manager setup."** The skill only
loads on a fresh start, so step 3 cannot run in this session. Do not start step 3 here.

## 3. First run (after the restart)

Claude: if someone says "finish the revenue manager setup", start here. First confirm the
skill loaded (the `revenue-manager` skill is in your skill list). If it is not, the restart
did not happen: ask for a full quit and reopen again, then continue.

Ask the operator, in one message: **which booking sites are your properties on, and what
markup do you add on each one?** (For example Airbnb 16%, VRBO 20%, Booking.com 18%.) Every
OTA they list on needs its markup: they all matter equally, none is optional and none comes
first. Also ask whether it is the same for every property; note any property that differs and
its numbers. If they add a markup in two places (in the PMS and in the pricing tool), get both
and store the combined figure, (1 + first) x (1 + second) - 1: 10% and 5% make 15.5%. Store
exactly what they say. Never work it out from the calendar; a difference between the PMS and
PriceLabs is not a markup.

**Every PMS gets the same setup pass.** It maps each property to its pricing tool (and
RankBreeze or IntelliHost, if connected) and stores the markups. Run it from this folder,
dry run first. Pick the line for the attendee's PMS and write one `--markup <channel>=<percent>`
for every booking site they named (for example `--markup airbnb=16 --markup vrbo=20
--markup booking=18`). Never run a placeholder as written:

| PMS | Setup line (dry run) |
|---|---|
| Hospitable | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms hospitable --markup <CHANNEL>=<PERCENT> --dry-run` |
| Guesty | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms guesty --markup <CHANNEL>=<PERCENT> --dry-run` |
| OwnerRez | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms ownerrez --markup <CHANNEL>=<PERCENT> --dry-run` |
| Hostaway | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms hostaway --markup <CHANNEL>=<PERCENT> --dry-run` |
| Lodgify | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms lodgify --markup <CHANNEL>=<PERCENT> --dry-run` |
| Uplisting | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms uplisting --markup <CHANNEL>=<PERCENT> --dry-run` |
| Smoobu | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms smoobu --markup <CHANNEL>=<PERCENT> --dry-run` |
| Hostfully | `uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms hostfully --markup <CHANNEL>=<PERCENT> --dry-run` |

A property whose markup differs from the rest gets `--markup-for "<exact property name>:<channel>=<percent>"`
(repeat per property and channel). If the dry run looks right, run the same line without
`--dry-run`. Setup checks the answer against the PMS: Hospitable, Guesty and OwnerRez report
every booking site each property is on, and setup stops with "<property> is listed on <site>
with no markup" if one is missing. Ask the operator for that markup and run it again; nothing is
written until every listed site has one. It lists every property: ✅ mapped, or
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
Microsoft Store instead of Python, and `uv run` never touches it. Keep `--with tzdata` too: Windows has no timezone
database of its own, and every card blocks without it.

**Wheelhouse, or no pricing tool at all:** setup still maps the properties, and marks the
pricing tool as a named gap. The skill asks for the markup on its first run and works from
the connected tools directly.

## 4. Test

Say **"check my pricing"**. The first card should open with the Flywheel line, name any
missing piece at the top, show the recommended min price for each listing, and end by
asking whether to apply the changes. Nothing changes unless the answer is yes.

Done. If anything above failed, the line it printed says why; fix that one thing and run
the step again.
