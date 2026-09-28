# Revenue Manager: summit setup

Claude: if someone says "set up the revenue manager" or "set this up", follow this file top
to bottom. If they say "finish the revenue manager setup", the plugin is already installed: go to
step 3. Run every command from this folder (the one this file is in), and
start each one with `cd <this folder> &&`: Claude Code can reset the working folder between
commands, and step 2 registers `$PWD` as the plugin source. If step 0 or step 1 asks for a
restart, give them this folder's full path to reopen Claude Code in. Say what each step found in
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

With the kit found, run its scoreboard, keeping only the rows the Revenue Manager runs on.
The kit checks a dozen tools for the other summit skills; none of those is this skill's
business, so the command drops them and you never bring them up:

```bash
cd "<kit>" && bash check-connections.sh 2>/dev/null | grep -E "(Hospitable|Guesty|OwnerRez|Hostaway|Lodgify|Uplisting|Smoobu|Hostfully|PriceLabs|Beyond) API|Supabase MCP|Supabase shared project|RankBreeze MCP|IntelliHost MCP|Turno API|Breezeway API|No \.env yet"
```

Print those lines as they are, in two groups:

- **Required**: the PMS API row, the pricing API row, `Supabase MCP`, `Supabase shared
  project`. All four ✅ means the connections are good: go to step 2.
- **Optional, used when present**: `RankBreeze MCP` or `IntelliHost MCP` (ranking and the
  visibility funnel), `Turno API` or `Breezeway API` (cleaning). A ➖ or ❌ here is not a stop
  and is not chased in this setup: say in one line what it means for them ("no ranking tool is
  connected, so each card will say it priced without ranking; the connections kit adds one any
  time") and move on.

Do not mention any other tool, key or row, on the board or off it (Kie, AirROI, Meta,
Firecrawl, Gemini): the other summit skills set those up, and the Revenue Manager never asks
for them.

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
`~/.local/bin`, or inside the desktop app itself on Windows and Mac), removes any earlier copy
of the plugin (the spring kit's, or one installed from another folder), adds this folder as
the plugin source and installs from it. Running it twice is fine:

```bash
CLAUDE_BIN="$(command -v claude 2>/dev/null)"; for c in "$HOME/.local/bin/claude" "$HOME/.local/bin/claude.exe"; do [ -z "$CLAUDE_BIN" ] && [ -x "$c" ] && CLAUDE_BIN="$c"; done; [ -z "$CLAUDE_BIN" ] && [ -n "${APPDATA:-}" ] && CLAUDE_BIN="$(ls -t "$APPDATA"/Claude/claude-code/*/claude.exe 2>/dev/null | head -1)"; [ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(ls -t "$HOME/Library/Application Support/Claude/claude-code/"*/claude.app/Contents/MacOS/claude 2>/dev/null | head -1)"; if [ -z "$CLAUDE_BIN" ]; then echo "claude: NOT FOUND"; else for old in revenue-manager@solnest-revenue-manager revenue-manager@str-secrets-revenue-manager; do "$CLAUDE_BIN" plugin uninstall "$old" >/dev/null 2>&1; done; for m in solnest-revenue-manager str-secrets-revenue-manager; do "$CLAUDE_BIN" plugin marketplace remove "$m" >/dev/null 2>&1; done; "$CLAUDE_BIN" plugin marketplace add "$PWD" && "$CLAUDE_BIN" plugin install revenue-manager@str-secrets-revenue-manager && "$CLAUDE_BIN" plugin update revenue-manager@str-secrets-revenue-manager >/dev/null 2>&1; echo "plugin: $("$CLAUDE_BIN" plugin list 2>/dev/null | grep -A1 'revenue-manager@str-secrets' | grep Version)"; fi
```

Two lines start with ✔ (the source, then the plugin) and the last line reads
`plugin: Version: <this folder's VERSION>`. Anything else: read the error, fix that one thing,
run the block again.

`claude: NOT FOUND` means the command-line copy of Claude Code is not on this computer (the
desktop app alone does not put it on the PATH). Install it, then run the block above again.
It looks frozen for a minute or two; that is the download:

```bash
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex" ;; *) curl -fsSL https://claude.ai/install.sh | bash ;; esac
```

Then go straight on to step 3, in this same chat. No restart: step 3 runs this folder's own
scripts, and step 4 reads the skill from this folder. From the next time they open Claude
Code, the skill loads on its own.

## 3. Booking sites and markups

Claude: if someone says "finish the revenue manager setup", start here. Nothing in this step
needs the skill loaded: these are this folder's own scripts.

**This question is never skipped, and it is asked on every setup run**, even when the database
already holds markups (an earlier run, a teammate on the same portfolio, an older version). When
some are stored, show them in the same message ("I have Airbnb 18.34%, VRBO 20%, Booking.com 22%,
direct 10%: still right?") and use what they answer. Markups only ever reach the database through
the setup line below; never write `property_config` by hand.

Do not ask which booking sites they are on: the PMS knows. Read them first (nothing is written):

```bash
uv run --with tzdata --python 3.13 python revenue-manager-plugin/skills/revenue-manager/fetch/setup_properties.py --pms <PMS> --list-sites
```

Then ask one question, naming exactly the sites it printed plus their direct site: **"What
markup do you add on Airbnb, VRBO, Booking.com and your direct booking site?"** (For example
Airbnb 16%, VRBO 20%, Booking.com 18%, direct 10%.) Every site named needs a number: they all
matter equally, none is optional. If the PMS reports no sites (Lodgify, Smoobu, Uplisting),
ask which sites they list on in the same breath. Also ask whether it is the same for every
property; note any property that differs and its numbers. If they add a markup in two places (in the PMS and in the pricing tool), get both
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

## 4. Done: the green check, then the first run

When the setup pass has run for real (not the dry run) and lists their properties, say, with
the names it printed: "✅ **Revenue Manager is set up.** It mapped <N> properties: <names>."
Name any ❌ property in one plain line with the reason it gave. Then ask:
**"Want to run it on one of your properties now? Which one?"** (suggest the first mapped one).

When they pick one, run it right here. Read `revenue-manager-plugin/skills/revenue-manager/SKILL.md`
in this folder with your Read tool and follow it as if the skill had been called with "check my
pricing for <that property>" (that file's folder is the skill's folder, for every path it
names). The first card opens with the Flywheel line, names any missing piece at the top, shows
the recommended min price, and ends by asking whether to apply the changes. Nothing changes
unless the answer is yes.

From the next time they open Claude Code, they just say **"check my pricing"**, or ask about one
property by name.

If anything above failed, the line it printed says why; fix that one thing and run the step
again.
