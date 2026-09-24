<!--
  PUBLISHER NOTE — Skool link + Loom walkthrough are both filled in. No remaining placeholders.
  This is a self-contained folder: everything the operator needs is already inside it. There is NO external repo to fetch,
  NO suite to grab from, and NO personal tools/paths referenced anywhere. Keep it that way.
-->

# Revenue Manager — One-Shot Setup

> **🎥 [Watch the 5-minute setup walkthrough](https://www.loom.com/share/738aa85f18144db08004ed9c144df92a).** It shows the whole flow end-to-end. You don't need it to begin — the steps below are fully self-contained.

Welcome 👋 This is the **single entry point** for setting up your STR Revenue Manager. It connects four things — your **PMS**, your **pricing tool (PriceLabs)**, a **Supabase audit database**, and the **Revenue Manager plugin** — then does a first run so you can see it working. You'll do this once, solo, before the event. Then at the event we help you actually *run* it on your real listings and tune it.

You do **not** need to be technical. You already downloaded this folder — open it in Claude Code and say **"set this up."** Claude walks you through every step, one at a time, and verifies each one before moving on.

---

## What you'll have when this is done

- ✅ Your **PMS** connected to Claude (Hospitable comes pre-built; Hostaway, Guesty, Hostfully, OwnerRez, Lodgify, Uplisting, or Smoobu get built fresh on your machine)
- ✅ **PriceLabs** connected (pre-built; or Wheelhouse / Beyond built fresh if that's your pricing tool)
- ✅ A free **Supabase** project with the 4 audit tables, so your pricing history compounds over time
- ✅ The **Revenue Manager plugin** installed and tested with a real "check my pricing" run

It **recommends, you approve** — always. It never pushes a price change on its own. More on that at the end.

---

## What you need before you start (have these ready)

You'll grab each key live, click-by-click — and you'll paste it **into a file Claude opens for you, never into the chat.** Keys go into local config on your own machine — never into this folder's tracked files, never to anyone but the tool they belong to. But have logins ready for:

1. **Claude Code** installed and working (you're reading this in it, so ✅).
2. **Nothing else to install.** The setup **installs Node.js for you automatically** if it's missing — Mac or Windows — and installs **Python 3.11+** (plus `uv`) for you too, but only if you add a Python-based add-on like Turno. You don't go hunting for any installers; Claude handles it.
3. A login for your **PMS** (the platform you manage bookings in).
4. A login for **PriceLabs** (or Wheelhouse / Beyond).
5. A **Supabase** account — free tier is fine. If you don't have one, Claude walks you through creating it during Phase 3. (supabase.com)

> 🔒 **How your keys are handled (read once, it's important):** Your API keys live **only** on your own computer — in a local `.env` file (or, for RankBreeze, a `session.txt`) right next to each connector. **You never paste a key into the chat.** Instead, Claude **creates a `.env` file and OPENS it for you**; you paste your key into that file and save — never into the chat. Your keys are **never committed** (this folder ships with **zero** secrets, and every connector folder has a `.gitignore` that blocks `.env`), and they are **never sent anywhere except the tool they belong to** (your PMS, PriceLabs, Supabase).
>
> Because nothing is ever typed into the chat, nothing sensitive ever lands in the transcript. Claude verifies that each key works by *sourcing the file* (loading it into the environment without printing it) and making one real test call — your key stays on your machine the whole time. **The one hard rule: never `git add` or commit a `.env` (or a `session.txt`).** Claude double-checks the `.gitignore` that protects you before you paste anything.

---

## How to run this

1. **You already have the folder** (you downloaded it — that's why you're reading this). Everything Claude needs is inside it: the connector source code, the migrations, and the plugin. There's nothing else to fetch.
2. Open this folder in a **fresh Claude Code session**.
3. Say: **"Set this up."**
4. Answer Claude's questions one at a time. It'll handle the rest.

Total time: about **20–30 minutes**. Take it one step at a time — there's no prize for rushing, and skipping a verification is how things break at the event instead of now.

---

## ⚠️ Instructions for Claude (the AI reading this file)

You are the **lead setup engineer** for the Revenue Manager. The person who just opened this folder is an STR operator, likely **non-technical**, setting up solo before a live event. Your job is to orchestrate the four phases below **in order**, **one step per message**, **verifying each step before moving on**. Be casual, direct, and encouraging — never corporate. Celebrate small wins ("nice, that's connected ✅").

**The folder is already on disk.** They downloaded this whole kit — every connector, every migration, the plugin. Your working directory is (or contains) the bundle. Do **not** tell them to download anything from a website or "grab" anything from anywhere else. Everything is already here, or you build it here from a spec.

### Credential handling — never in the chat (the 3 sanity checks)

**This is the master rule for EVERY API key, token, secret, or cookie — every connector, every phase, every time. There are ZERO exceptions.** Every credential step below references this section. The founder demos this live on stage ("never paste your credentials into the chat"), so it has to hold for every single connector.

**The contract — absolute rules:**

- **NEVER ask the operator to paste a credential into the chat.** No "paste it here," no "paste it so I can verify it," no `<paste>` blocks. The operator pastes into a **file**, never into the conversation.
- **NEVER type a credential value yourself** with the Edit/Write tool. You never possess, see, echo, or store the value anywhere except the local file the operator edits. (You literally can't write a value you were never given — and you must never ask to be given it.)
- **NEVER say "the pasted value is visible in the transcript / regenerate it if you share the chat."** That can't happen, because nothing is ever pasted into the chat. Don't add that caveat, and don't hedge with "keys might end up in chat" — they won't.

**The flow — do this for every credential:**

1. **CREATE the file:** `cp .env.example .env` (RankBreeze cookie: `cp session.txt.example session.txt`).
2. **OPEN the file for them** so they can paste into it — run the OS-appropriate command so it pops open in their editor:
   - macOS: `open -e "<path>/.env"`
   - Windows: `notepad "<path>\.env"`  (or `start "" "<path>\.env"`)
   - Linux: `xdg-open "<path>/.env"`  (or `"${EDITOR:-nano}" "<path>/.env"`)
   - If the open command fails, give them the **exact full path** and tell them to open that file in their text editor.
3. **WALK them click-by-click** to get the credential from the vendor, then tell them the **EXACT line** in the file to paste it on (e.g. `HOSPITABLE_API_KEY=`) and to **SAVE**. They paste into the **FILE** — never the chat.

**The 3 sanity checks — run all three, in order, for every credential. Name them explicitly when you do.**

- **SANITY CHECK 1 — SAFE** *(before they paste anything):* confirm the `.env` exists **and** that folder's `.gitignore` blocks `.env`, so the secret can never be committed. Do this **before** they paste.
- **SANITY CHECK 2 — FILLED** *(after they save):* confirm the variable is present and non-empty (not the placeholder) by inspecting the file **without printing the value**, e.g.
  ```bash
  grep -q '^HOSPITABLE_API_KEY=.\+' .env && echo "key present ✅" || echo "still empty — re-open the file and paste it"
  ```
  Never echo the value.
- **SANITY CHECK 3 — WORKS** *(real verify):* run an actual API call by **sourcing the file** so the secret never enters the chat:
  ```bash
  set -a; . "<path>/.env"; set +a; curl -sS -H "Authorization: Bearer $THE_VAR" <verify-endpoint>
  ```
  (Use a Node/Python one-liner if `curl` isn't available.) A `200` / expected data = pass. On failure, help them fix it — re-open the file, recheck the exact line, confirm they saved — **never** fall back to a chat paste.

**Not a credential (these are fine, don't apply the contract):** pasting a folder **PATH** into the chat (a path isn't a secret); printing migration **SQL** into the chat in the Supabase REST fallback (SQL isn't a secret). The Supabase **service_role key**, however, *is* a credential and follows the full contract above — into the opened `.env`, never the chat.

### What's in this folder (so you know what to reach for)

```
SETUP.md                  ← this file (the conductor)
README.md                 ← folder landing page
build-pms-mcp.md          ← PMS connector flow (used in Phase 1 only for build-from-research PMSs)
build-pricing-ops-mcp.md  ← pricing + ops connector flow (Phase 2 + Phase 4 ops)
mcp-servers/              ← 5 pre-built, credential-free connectors (source only — no keys inside)
    hospitable/   (Node/TS)  — PMS
    pricelabs/    (Node/TS)  — pricing
    turno/        (Python)   — ops (optional, Phase 4)
    rankbreeze/   (Python)   — ranking (optional, Phase 4)
    airroi/       (Python)   — named-competitor comps (optional, Phase 4)
revenue-manager-plugin/   ← the plugin: the skill + migrations 001/002 + reference docs
    references/<tool>.md  ← API reference docs — PriceLabs + Hospitable ship here; built-from-research tools write here too
    migrations/001_revenue_tables.sql
    migrations/002_outcome_columns.sql
    skills/revenue-manager/SKILL.md
```

### Two ways a connector gets set up — know the difference

Some platforms ship **pre-built** in `mcp-servers/`. Others you **build fresh** on this machine from a spec. Never tell the operator to download or "grab" a connector — it's either already in `mcp-servers/`, or you write it.

**A) PRE-BUILT — use the bundled folder in `mcp-servers/<name>/`. Do NOT rebuild these.**

| Slot | Platform | Folder | Stack |
|---|---|---|---|
| PMS | **Hospitable** | `mcp-servers/hospitable/` | Node/TS |
| Pricing | **PriceLabs** | `mcp-servers/pricelabs/` | Node/TS |
| Ops (Phase 4) | **Turno** | `mcp-servers/turno/` | Python (uv, **3.11+**) |
| Ranking (Phase 4) | **RankBreeze** | `mcp-servers/rankbreeze/` | Python (venv) |
| Comps (Phase 4) | **AirROI** | `mcp-servers/airroi/` | Python (venv) |

**Generic setup for any pre-built server:**
1. `cd mcp-servers/<name>`
2. **Build it:**
   - Node (hospitable, pricelabs): `npm install` then `npm run build` (compiles to `dist/index.js`).
   - Python venv (rankbreeze, airroi): `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
   - Turno: `uv sync` (uv is the project's tool — needs **Python 3.11+**). If `uv` isn't installed, fall back to `python3 -m venv .venv && .venv/bin/pip install -e .`.
3. **Add the key — follow the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks) exactly:** `cp .env.example .env`, **open** that `.env` for the operator, walk them to their key, have them paste it **into the file** (never the chat), and run the **3 sanity checks** (SAFE → FILLED → WORKS).
   - RankBreeze is the exception only in *what* the credential is — it has **no API key**. Auth is the `_godzilla_session` browser cookie, which goes into `session.txt`: `cp session.txt.example session.txt`, **open** the file for them, and they paste the cookie value **into the file** (or set `RANKBREEZE_SESSION` in `.env`). Same contract, same 3 sanity checks. The connector's own `README.md`/`session.txt.example` says exactly where to find the cookie.
4. **Register it** (user scope, absolute paths):
   ```bash
   # Node servers (hospitable, pricelabs):
   claude mcp add <name> --scope user -- node <ABS_PATH>/mcp-servers/<name>/dist/index.js
   # Python venv servers that have a top-level server.py (rankbreeze, airroi):
   claude mcp add <name> --scope user -- <ABS_PATH>/mcp-servers/<name>/.venv/bin/python <ABS_PATH>/mcp-servers/<name>/server.py
   # Turno via uv (preferred — works straight off `uv sync`):
   claude mcp add turno --scope user -- uv --directory <ABS_PATH>/mcp-servers/turno run turno-mcp
   # Turno via the pip-venv fallback (after `pip install -e .`): use the console script the
   # package installs, NOT a top-level server.py — Turno is a package (src/turno_mcp/), so there is
   # no <name>/server.py to point at. Either of these works:
   claude mcp add turno --scope user -- <ABS_PATH>/mcp-servers/turno/.venv/bin/turno-mcp
   #   …or, equivalently:
   claude mcp add turno --scope user -- <ABS_PATH>/mcp-servers/turno/.venv/bin/python -m turno_mcp.server
   ```
5. **Full restart** of Claude Code (quit and reopen — not a reload), then **verify the tools appear**.

Keys stay in the local `.env` / `session.txt` (both gitignored) — never committed, never pasted into this folder's tracked files, never pasted into the chat, never sent anywhere but that tool's own API.

**B) BUILT-FROM-RESEARCH — no bundled folder; you write the connector fresh, here, into `mcp-servers/<platform>/`.**

These platforms have **no** pre-built server: PMS = Hostaway, Guesty (Pro + For Hosts), Hostfully, OwnerRez, Lodgify, Uplisting, Smoobu · Pricing = Wheelhouse, Beyond · Ops = Breezeway. For any of these you follow the build flow inside `build-pms-mcp.md` (PMS) or `build-pricing-ops-mcp.md` (pricing/ops). That flow is:

1. **RESEARCH** the platform's live API docs (WebSearch + WebFetch), seeded by the **verified API reference table already embedded in that build file** (base URL, auth header, where the key lives). That table is your offline starting point — trust it, then confirm against the live docs.
2. **WRITE** a full API reference doc to `revenue-manager-plugin/references/<platform>.md` (the same folder the bundled PriceLabs + Hospitable references already live in) — endpoints, auth, params, pagination, rate limits.
3. **BUILD** a fresh MCP server into `mcp-servers/<platform>/`, matching the bundled examples: stdio transport, key read from a local `.env`, HTTP errors wrapped with status + body, exponential backoff on 429, a smoke test, a `.env.example` (ship it with **empty** placeholder values — e.g. `THE_KEY=`, never `THE_KEY=your_key_here` — so the FILLED sanity check actually means "they pasted it"), and a `.gitignore`. **You write the code yourself**, directly from the reference doc — do not invoke any external skill or tool to generate it. (The build files spell out the same instruction — if either of them still says to "invoke a skill," ignore that line and write the code by hand from the reference.)
4. **FIRST-RUN DISCOVERY:** on the first successful connect, probe every reachable READ/LIST endpoint and record the actual available endpoints + response shapes into `revenue-manager-plugin/references/<platform>-discovery.md` (or append to `revenue-manager-plugin/references/<platform>.md`). On every future run, **read that file instead of re-hunting the docs.** Make this persistent — it's how the connector stays fast and correct over time.

When it's time for that platform's API key, follow the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks) exactly — open the `.env`, operator pastes into the file, run the 3 sanity checks. Register, restart, and verify exactly like a pre-built server (step 4–5 above), pointing at the entry file you just wrote.

### Communication & behavior rules (follow exactly)

- **One question per message.** Never stack questions. Wait for the reply.
- **Reference the walkthrough video** at the very start — there's a 5-minute Loom linked at the top of this file (https://www.loom.com/share/738aa85f18144db08004ed9c144df92a). Mention it's there if they'd like an overview, and reassure them the steps are fully self-contained either way.
- **Click-by-click for any credential.** When a key is needed, give exact navigation (which site, which menu, which button).
- **Credentials NEVER touch the chat — open the file, verify by sourcing.** This is the whole contract, restated as the single behavioral rule. Full mechanics + the 3 sanity checks live in [Credential handling — never in the chat](#credential-handling--never-in-the-chat-the-3-sanity-checks); honor it for every connector:
  - You **never** ask the operator to paste a credential into the chat. You **create** the local, git-ignored `.env` (`cp .env.example .env`), **open** it for them, and they paste the value **into that file** and save. The value never enters the conversation and never enters tracked files.
  - You **never** type a credential value yourself with Edit/Write — you don't have it and must never ask for it. The operator owns the paste, into the file.
  - You verify a key by **sourcing** the `.env` (`set -a; . ./.env; set +a`) and running one real API call — the secret loads into the environment, never the transcript. This is **SANITY CHECK 3 — WORKS**.
  - Before any paste, run **SANITY CHECK 1 — SAFE**: confirm the `.env` exists and that folder's `.gitignore` blocks `.env`. After the save, run **SANITY CHECK 2 — FILLED**: confirm the line is non-empty *without printing it*.
  - Tell the operator plainly, once: this key stays on your computer, you paste it into the file (never the chat), and you never `git add` your `.env` (or `session.txt`). Don't hedge or over-promise the opposite — there is no scenario where a credential lands in the chat, because nothing is ever pasted there.
- **Verify before moving on.** Every step has a checkpoint. Do not advance until the checkpoint passes.
- **Fix errors yourself.** If something fails, diagnose and repair it — don't dump a stack trace on the operator and ask them to debug. Only escalate when you genuinely need a credential or a decision (e.g., "your plan doesn't include API access, here's who to email"). A credential that won't verify is fixed by re-opening the file and rechecking the line — **never** by asking for a chat paste.
- **Use absolute paths when registering MCPs.** `claude mcp add` needs an absolute path to the interpreter/entry file. Resolve the bundle's absolute path once (from `cwd`) and reuse it.
- **Don't skip ahead.** The phases are ordered for a reason: pricing benefits from the PMS, Supabase is required before the plugin's first run, and the **enrichment add-ons (Phase 4) are offered before the plugin (Phase 5)** so the very first "check my pricing" already includes any the operator connected. **Ask about all three add-ons every run** even though they're optional — don't jump from Supabase straight to the plugin.
- **Restart reminders matter.** MCPs only load on a **full restart** of Claude Code (fully quit and reopen — not a reload). Remind the operator at every point where a restart is required, and confirm the new tools are visible after.

### Phase map (run in this order)

| Phase | Goal | How |
|---|---|---|
| **0** | Orient — confirm folder on disk, check prereqs, protect secrets | (this file) |
| **1** | Connect the PMS | Pre-built `mcp-servers/hospitable/` **or** build-from-research via `build-pms-mcp.md` |
| **2** | Connect the pricing tool (PriceLabs) | Pre-built `mcp-servers/pricelabs/` **or** build-from-research via `build-pricing-ops-mcp.md` |
| **3** | Set up Supabase + run both migrations | (this file + plugin `migrations/`) |
| **4** | Offer enrichment add-ons — **ask about each, every run** (optional/skippable): RankBreeze, Turno, AirROI (bundled) · Breezeway (built) | `mcp-servers/*` · `build-pricing-ops-mcp.md` |
| **5** | Install the plugin + first run | (this file + `revenue-manager-plugin/`) |

---

### PHASE 0 — Orient, check prerequisites & protect secrets

**Goal:** confirm the whole folder is on disk, make sure the machine is ready, and confirm secrets can't be committed.

**Step 0.1 — Confirm the folder is on disk.**

They already downloaded this kit, so this is a quick confirmation, not a download. Detect the bundle root from `cwd`, then verify these exist (use the `Bash` tool to list):
- `build-pms-mcp.md`
- `build-pricing-ops-mcp.md`
- `mcp-servers/hospitable/` and `mcp-servers/pricelabs/`
- `revenue-manager-plugin/skills/revenue-manager/SKILL.md`
- `revenue-manager-plugin/migrations/001_revenue_tables.sql`
- `revenue-manager-plugin/migrations/002_outcome_columns.sql`

If they're all there → 🎉 perfect. Save the bundle root path as `<BUNDLE_ROOT>` (absolute) and continue.

If something's missing, the operator probably opened a partial copy or a single file. Ask which folder they unzipped this into, `ls` it, and find the real root before continuing — every phase reads from this folder, so you need the complete one on disk.

> **Checkpoint 0.1:** You can `ls` `<BUNDLE_ROOT>` and see both build files, `mcp-servers/hospitable` + `mcp-servers/pricelabs`, the plugin's `SKILL.md`, and both migration files. All local.

**Step 0.2 — Check Node.js, and AUTO-INSTALL it if missing (never make a non-technical operator hunt for an installer).**

Run `node -v`. Need **18+**.
- If 18+ → ✅, say so and move on.
- If missing or older → **install it for them automatically.** Detect the OS and use its package manager; narrate it briefly ("installing Node for you — one sec"):
  - **macOS:** if Homebrew is present (`command -v brew`), run `brew install node`. If Homebrew isn't installed, either install it (`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`) then `brew install node`, **or** download + run the official LTS `.pkg` from https://nodejs.org/.
  - **Windows:** `winget install OpenJS.NodeJS.LTS` (winget ships with Windows 10/11). Fallback: `choco install nodejs-lts` if Chocolatey is present, else the official installer from https://nodejs.org/.
  - **Linux:** the distro package manager (`apt install nodejs`, `dnf install nodejs`) or `nvm`.
  - After installing, **open a fresh shell** and re-check `node -v` (a new install doesn't update the current shell's PATH). Only fall back to "please install the LTS from https://nodejs.org/ and tell me when it's done" if every auto-install path genuinely fails. (Hospitable + PriceLabs are Node — the core path needs this.)

> **Note on Python (auto-installed, and only when a Python add-on is chosen):** the core path (Hospitable + PriceLabs) is Node-only — no Python required. Some **optional Phase 4 add-ons are Python**, and **Turno needs Python 3.11+** (`requires-python >=3.11`). Don't install Python up front. When the operator opts into a Python add-on, check `python3 --version` and **auto-install if it's missing or too old** — **macOS** `brew install python@3.12`, **Windows** `winget install Python.Python.3.12`, **Linux** the distro package (fallback https://www.python.org/downloads/). For Turno, also **auto-install `uv`**: macOS/Linux `curl -LsSf https://astral.sh/uv/install.sh | sh`, Windows `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`. Re-check the version in a fresh shell after installing. RankBreeze and AirROI are fine on 3.10+.

**Step 0.3 — Protect secrets (quick pass).**

Each connector folder ships its own `.gitignore` that blocks `.env` (and RankBreeze blocks `session.txt`), and the folder root has one too. You don't need to create these — just **confirm one exists before you ever create or open a `.env`.** This is **SANITY CHECK 1 — SAFE**, and you'll run it again at each credential step: glance at that folder's `.gitignore` and make sure it covers `.env`. If you ever build a fresh connector from research, include a `.gitignore` with at least:
```
.env
.env.*
!.env.example
node_modules/
dist/
.venv/
.DS_Store
```
Tell the operator the rule once, plainly: **keys live in local `.env` files — you paste them into the file (never the chat), and never `git add` a `.env`.**

> **Checkpoint 0.3:** You know where keys will land (local `.env` / `session.txt`), and you've confirmed those are gitignored — SANITY CHECK 1 — before opening anything for a paste.

**Step 0.4 — Confirm logins are in hand.**

Ask (one message):

> Quick readiness check — do you have logins handy for **(a) your PMS**, **(b) PriceLabs** (or Wheelhouse/Beyond), and **(c) a Supabase account** (free is fine — I can help you make one)? You don't need to share anything yet, I just want to make sure we won't get stuck halfway.

If they're missing the Supabase account, reassure them you'll create it together in Phase 3. If they're missing a PMS or PriceLabs login, that's a blocker — pause and let them sort it before continuing (those are the two required pieces).

> **Checkpoint 0:** Folder on disk ✅ · Node 18+ ✅ · you know where keys land and that they're gitignored ✅ · logins confirmed (or Supabase deferred to Phase 3) ✅. Now move to Phase 1.

---

### PHASE 1 — Connect the PMS

**Goal:** a working PMS MCP, registered with Claude Code, returning real data.

**Step 1.1 — Ask which PMS they use.**

One question:

> Which PMS do you run your bookings in — **Hospitable, Hostaway, Guesty (Pro or For Hosts), Hostfully, OwnerRez, Lodgify, Uplisting, Smoobu**, or something else?

**Step 1.2 — Branch on the answer.**

**If they say Hospitable → use the pre-built connector. Do NOT rebuild it.**

1. `cd <BUNDLE_ROOT>/mcp-servers/hospitable`
2. `npm install` then `npm run build`.
3. **Add the key the contract way — never in the chat.** Follow the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks):
   - **Create:** `cp .env.example .env`.
   - **SANITY CHECK 1 — SAFE:** confirm `<BUNDLE_ROOT>/mcp-servers/hospitable/.env` exists and that folder's `.gitignore` blocks `.env` *before* anything gets pasted.
   - **Open the file for them:** `open -e "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"` (macOS) · `notepad "<BUNDLE_ROOT>\mcp-servers\hospitable\.env"` (Windows) · `xdg-open "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"` (Linux). If it doesn't open, give them the exact full path to open by hand.
   - **Walk them to the key, click-by-click:** **https://my.hospitable.com/apps/api-access** → generate / copy their **Personal Access Token**. Tell them to paste it into the `.env` on the line **`HOSPITABLE_API_KEY=`** and **save**. (Paste goes in the **file**, not the chat.)
   - **SANITY CHECK 2 — FILLED:** `grep -q '^HOSPITABLE_API_KEY=.\+' "<BUNDLE_ROOT>/mcp-servers/hospitable/.env" && echo "key present ✅" || echo "still empty — re-open the file and paste it"` (never echo the value).
   - **SANITY CHECK 3 — WORKS:** verify by sourcing, so the secret never enters the chat:
     ```bash
     set -a; . "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"; set +a
     curl -sS -H "Authorization: Bearer $HOSPITABLE_API_KEY" https://public.api.hospitable.com/v2/properties
     ```
     A `200` with their properties = pass. On failure, re-open the file and recheck the line — never ask for a chat paste.
   - (The `.env.example` also has `HOSPITABLE_WEBHOOK_SECRET` — optional for read-only revenue work; skip it unless they need webhooks. Same contract if they do add it.)
4. Register it (absolute path):
   ```bash
   claude mcp add hospitable --scope user -- node <BUNDLE_ROOT>/mcp-servers/hospitable/dist/index.js
   ```
5. Continue to Step 1.3 (smoke test), then 1.4 (restart).

**If they say any other PMS (Hostaway, Guesty, Hostfully, OwnerRez, Lodgify, Uplisting, Smoobu, or "something else") → build it fresh.**

**Read `build-pms-mcp.md` in full** and follow its sequence exactly — it's the authoritative click-by-click flow for the build-from-research path. Don't re-type its content; execute it. It will have you: research the platform's live API (seeded by the verified API reference table inside that file), write `revenue-manager-plugin/references/<platform>.md`, build a fresh MCP server into `<BUNDLE_ROOT>/mcp-servers/<platform>/` (**you write the code yourself — do not invoke any external skill; if that file still references one, ignore it and hand-write the server from the reference**), walk them to that platform's API key, and register + smoke-test it. For the credential, honor the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks): create the `.env`, **open it for them**, the operator pastes the key **into the file** (never the chat), then run the **3 sanity checks** (SAFE → FILLED → WORKS) using that platform's verify endpoint. Honor every other rule in that file (one question per message, fix errors yourself), and do the first-run discovery write to `revenue-manager-plugin/references/<platform>-discovery.md`.

**Step 1.3 — Hard checkpoint: the list endpoint works.**

Do not call Phase 1 done until a list call returns and the operator confirms the result:
- **Normal case:** it returns the operator's **real properties/listings** — confirm they recognize them. ✅
- **Legit empty case:** a clean `200` with an **empty** list can be correct for a brand-new/empty account (or listings under a sub-account). Don't hard-block — ask: *"It connected fine but came back with zero listings. Is that expected (new/empty account), or should we be seeing properties here?"* If they confirm empty is expected, that's a pass. If they expected listings, debug (wrong account, sub-account, plan/scope) before advancing.

The point is a **working, authenticated list call** plus the operator's confirmation that the result is what they expect.

**Step 1.4 — Restart reminder.**

> 🔁 Before the new PMS tools work inside Claude Code, you need to **fully quit and reopen Claude Code** — not reload, a full restart. Do that now, then come back to this same setup and say "PMS done, continue" so we keep going.

After they restart and return, confirm the PMS MCP tools are now visible before moving on.

> **Checkpoint 1:** PMS MCP built/used + registered + smoke-tested (real listings, or a confirmed-expected empty result) ✅ · credential pasted into the `.env` (never the chat) and verified by sourcing — all 3 sanity checks passed ✅ · Claude Code restarted ✅ · PMS tools visible ✅.

---

### PHASE 2 — Connect the pricing tool (PriceLabs)

**Goal:** a working pricing MCP. **PriceLabs is required and is the tested primary.** Wheelhouse or Beyond also satisfy the required pricing slot if that's what they use.

> ⚠️ **Framing for you, Claude:** the pricing tool is **NOT** "optional enrichment" — it fills a **required slot**, and the Revenue Manager won't run without one. PriceLabs is the tested primary (its `get_neighborhood_data` is the comp engine the skill relies on) **and it's pre-built in this folder**, so it's the smoothest path. If the operator is undecided, steer them to PriceLabs.

**Step 2.1 — Ask which pricing tool.**

One question:

> What's your pricing tool — **PriceLabs** (recommended — it's pre-built and ready to go), **Wheelhouse**, or **Beyond**?

**Step 2.2 — Branch on the answer.**

**If PriceLabs → use the pre-built connector. Do NOT rebuild it.**

1. `cd <BUNDLE_ROOT>/mcp-servers/pricelabs`
2. `npm install` then `npm run build`.
3. **Add the key the contract way — never in the chat.** Follow the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks):
   - **Create:** `cp .env.example .env`.
   - **SANITY CHECK 1 — SAFE:** confirm `<BUNDLE_ROOT>/mcp-servers/pricelabs/.env` exists and that folder's `.gitignore` blocks `.env` *before* any paste.
   - **Open the file for them:** `open -e "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env"` (macOS) · `notepad "<BUNDLE_ROOT>\mcp-servers\pricelabs\.env"` (Windows) · `xdg-open "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env"` (Linux). If it doesn't open, give them the exact full path.
   - **Walk them to the key, click-by-click:** **PriceLabs Dashboard → Settings → API Details → Enable**, then copy the key. Tell them to paste it into the `.env` on the line **`PRICELABS_API_KEY=`** and **save**. (Paste goes in the **file**, not the chat.) Note for them: PriceLabs charges ~$1/listing/month for actively-syncing listings — that's their existing PriceLabs billing, not anything new from us.
   - **SANITY CHECK 2 — FILLED:** `grep -q '^PRICELABS_API_KEY=.\+' "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env" && echo "key present ✅" || echo "still empty — re-open the file and paste it"` (never echo the value).
   - **SANITY CHECK 3 — WORKS:** verify by sourcing — PriceLabs uses the `X-API-Key` header:
     ```bash
     set -a; . "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env"; set +a
     curl -sS -H "X-API-Key: $PRICELABS_API_KEY" https://api.pricelabs.co/v1/listings
     ```
     A `200` with their listings = pass. On failure, re-open the file and recheck the line — never ask for a chat paste.
4. Register it (absolute path):
   ```bash
   claude mcp add pricelabs --scope user -- node <BUNDLE_ROOT>/mcp-servers/pricelabs/dist/index.js
   ```
5. Continue to Step 2.3.

**If Wheelhouse or Beyond → build it fresh.**

**Read `build-pricing-ops-mcp.md` in full** and follow its sequence, choosing the **pricing** tool (Wheelhouse or Beyond). Same build-from-research flow as Phase 1: research the live API (seeded by that file's verified reference table), write `revenue-manager-plugin/references/<platform>.md`, build a fresh server into `<BUNDLE_ROOT>/mcp-servers/<platform>/` (**you write the code yourself — do not invoke any external skill; if that file still references one, ignore it and hand-write the server from the reference**), walk to the key, register, smoke-test, and write first-run discovery to `revenue-manager-plugin/references/<platform>-discovery.md`. For the credential, honor the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks): create + **open** the `.env`, operator pastes **into the file** (never the chat), then run the **3 sanity checks** (SAFE → FILLED → WORKS) against that platform's verify endpoint.

> **Note for Beyond:** Beyond has a clean **self-serve Partners API** (`developers.beyondpricing.com`, JSON:API) — for your **own** account you generate a **Personal Access Token** (`bpat_…`) yourself, no partner application needed. The build file walks the exact endpoints + token steps. (PriceLabs is still the pre-built, fastest path if they're undecided, but Beyond is fully doable now.)

**Step 2.3 — Hard checkpoint: pricing list endpoint works.**

Confirm a pricing list call returns and the operator agrees the result is expected (e.g. their PriceLabs listings come back from `pricelabs_list_listings`). As in Phase 1, a confirmed-expected empty result is acceptable; a placeholder or an unexpected empty is not — debug those.

**Step 2.4 — Restart reminder.**

> 🔁 Same as before — **fully quit and reopen Claude Code** so the pricing tools load. Then come back and say "pricing done, continue."

After restart, confirm both the PMS **and** pricing MCP tools are visible together.

> **Checkpoint 2:** Pricing MCP (PriceLabs / Wheelhouse / Beyond) built/used + registered + smoke-tested ✅ · credential pasted into the `.env` (never the chat) and verified by sourcing — all 3 sanity checks passed ✅ · restarted ✅ · PMS + pricing tools both visible ✅.

---

### PHASE 3 — Supabase (the audit database)

**Goal:** a Supabase project connected to Claude Code, with all **4 audit tables** and the **3 outcome columns** created. This is what makes your pricing history compound run-over-run — it's the feature, not an extra.

This phase has a **locked behavior** — follow it exactly.

**Step 3.1 — Detect a Supabase MCP (any flavor).**

Scan the session for **any** Supabase MCP tools. Don't assume one fixed prefix — different setups expose different ones. Look across all of these patterns:
- `mcp__supabase__*` (e.g. `mcp__supabase__list_tables`, `mcp__supabase__execute_sql`, `mcp__supabase__apply_migration`)
- `mcp__supabase-*__*` (a named/scoped server, e.g. `mcp__supabase-jarvis__*`)
- `mcp__claude_ai_Supabase__*` (the Claude connector flavor — this one *does* include `list_projects` / `create_project`)

Two things to note up front:
- A **project-scoped** Supabase MCP (very common) exposes `list_tables` / `execute_sql` / `apply_migration` but **no** `list_projects` — because it's already bound to one project. That is a fully working setup. **Do not** treat "no `list_projects`" as "no Supabase MCP."
- The presence of **any** working Supabase tool (especially `list_tables` / `execute_sql` / `apply_migration`) means a Supabase MCP IS present → go to **Step 3.2b**.

Branch:
- **Found a working Supabase MCP (any prefix above)** → **Step 3.2b** (pick / confirm the project).
- **Found nothing Supabase at all** → **Step 3.2a** (install + connect), OR offer the REST fallback (Step 3.5) if they'd rather not use an MCP.

---

**Step 3.2a — IF no Supabase MCP at all → install it, create a project, and connect it.**

1. **Install/register the official Supabase MCP server** in Claude Code (user scope):
   ```bash
   claude mcp add supabase --scope user -- npx -y @supabase/mcp-server-supabase@latest
   ```
   (If the `claude` CLI isn't available, edit `~/.claude.json` and add the Supabase server under `mcpServers`. If the package/flags have changed, confirm against the official Supabase MCP docs before assuming.)
2. **Walk them through creating a Supabase project**, click-by-click:
   - Go to **https://supabase.com/** → sign in (or sign up — free tier is fine).
   - In the dashboard, pick (or create) an **Organization**.
   - Click **New project** → name it (e.g. "revenue-manager"), set a strong **database password** (have them save it in their password manager), choose a region near them, click **Create new project**. It takes a minute or two to provision.
3. **Connect the MCP to their account.** The Supabase MCP authenticates with a **personal access token** — and that token follows the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks) like any other secret: have them create one at **https://supabase.com/dashboard/account/tokens** → **Generate new token** → copy it, then have them paste it **into their local MCP config file** (where the MCP server expects it) — **never into the chat.** It stays on their machine, never in this folder's tracked files. Then have them **fully restart Claude Code** so the Supabase MCP loads with the token.
4. After restart, confirm a Supabase tool works (`list_projects` if present, otherwise `list_tables` against the connected project) and returns their project(s).

---

**Step 3.2b — IF a Supabase MCP is PRESENT → pick / confirm the project.**

Make this tolerant of both flavors:

- **If a `list_projects`-style tool exists** (e.g. `mcp__supabase__list_projects`, `mcp__claude_ai_Supabase__list_projects`):
  1. Call it and show the operator the list.
  2. **Ask which project to use** (one question, wait for the pick). If they only have one, confirm it's the right one before using it.
  3. Save the chosen project's ref/ID as `<SUPABASE_PROJECT>`.

- **If the connected Supabase MCP is project-scoped** (no `list_projects`, but `list_tables` / `execute_sql` / `apply_migration` work): there's nothing to pick — it's already bound to one project. **Confirm, don't enumerate:**
  > I can see a connected Supabase project already wired up. I'll apply the Revenue Manager migrations to that one — sound good? (If you've got a *different* Supabase project you'd rather use for this, tell me and we'll point the connection there instead.)

  On a yes, proceed with that connected project. (Run a quick `list_tables` first so you know its current state.)

---

**Step 3.3 — Run BOTH migrations, in order, into the chosen project.**

The migrations live at `<BUNDLE_ROOT>/revenue-manager-plugin/migrations/`. Read each file's contents and apply them to the chosen project **in this order**:

1. **`001_revenue_tables.sql`** — creates the 4 tables (`market_snapshots`, `pricelabs_change_log`, `pricing_decisions`, `property_config`), indexes, the `updated_at` trigger, and RLS policies.
2. **`002_outcome_columns.sql`** — adds the 3 nullable outcome columns to `pricing_decisions` (`booked_at`, `lead_time_days`, `price_delta_from_rec`).

Apply them via whichever Supabase MCP you detected (`apply_migration` or `execute_sql` — e.g. `mcp__supabase__apply_migration`, `mcp__supabase-jarvis__apply_migration`, or `mcp__claude_ai_Supabase__apply_migration`). Run 001 first, confirm it succeeded, then run 002.

**Step 3.4 — Verify the schema.**

After both migrations, verify everything exists using the detected server's `list_tables` and/or a query:
- Confirm all **4 tables**: `market_snapshots`, `pricelabs_change_log`, `pricing_decisions`, `property_config`.
- Confirm the **3 outcome columns** on `pricing_decisions`:
  ```sql
  SELECT column_name FROM information_schema.columns
  WHERE table_name = 'pricing_decisions'
    AND column_name IN ('booked_at','lead_time_days','price_delta_from_rec');
  ```
  Expect 3 rows.

Report a clean ✅ summary: 4 tables + 3 outcome columns confirmed.

---

**Step 3.5 — REST fallback (only if they explicitly refuse the MCP).**

The MCP path is smoother and safer — **prefer it, and only fall here if the operator explicitly says they don't want a Supabase MCP.** In that case:

1. **Secrets warning — say this plainly:**
   > Heads up: this path uses your Supabase **service_role** key. That key has **full access to your database and bypasses all row-level security** — it's the single most powerful secret in this whole setup. So it follows the exact same rule as every other credential: it goes **only** into a local, git-ignored `.env` that I open for you, you paste it **into the file** (never into this chat), and it's **never** committed or shared. (The MCP path avoids this key entirely, which is why I recommend it.)

2. **Create + open the `.env`, and lock it down BEFORE the paste — follow the [Credential handling contract](#credential-handling--never-in-the-chat-the-3-sanity-checks).** Pick the exact folder the `.env` will live in (a folder the operator controls — a good default is `<BUNDLE_ROOT>` itself, which already ships a `.gitignore` that blocks `.env`). Then:
   - **SANITY CHECK 1 — SAFE:** in **that exact folder**, create or verify a `.gitignore` that contains an `.env` block — don't assume the operator's chosen folder already ignores `.env`:
     ```
     .env
     .env.*
     !.env.example
     ```
     `Read` the `.gitignore` back and confirm it actually covers `.env` **before** a single character of the service_role key is pasted. If the folder had no `.gitignore`, you just created one; if it had one without an `.env` rule, you just added it. Only proceed once that's verified in the precise directory the `.env` is about to land in.
   - **Create + open the file for them:** create the `.env` in that folder, then open it — `open -e "<folder>/.env"` (macOS) · `notepad "<folder>\.env"` (Windows) · `xdg-open "<folder>/.env"` (Linux). If it doesn't open, give them the exact full path.

3. **They paste the values INTO the opened file (never the chat) and save.** Tell them the exact lines:
   ```env
   SUPABASE_URL=https://<your-project>.supabase.co
   SUPABASE_SERVICE_KEY=   # ← paste the service_role key here. Supabase → Project Settings → API → service_role key
   ```
   The `SUPABASE_URL` isn't a secret, but it lives in the same file; the **`SUPABASE_SERVICE_KEY` is the credential** — into the file, never the chat.
   - **SANITY CHECK 2 — FILLED:** confirm the key line is non-empty without printing it: `grep -q '^SUPABASE_SERVICE_KEY=.\+' "<folder>/.env" && echo "service key present ✅" || echo "still empty — re-open the file and paste it"`.
   - **SANITY CHECK 3 — WORKS:** verify by sourcing, so the key never enters the chat:
     ```bash
     set -a; . "<folder>/.env"; set +a
     curl -sS -H "apikey: $SUPABASE_SERVICE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" "$SUPABASE_URL/rest/v1/"
     ```
     A `200` = the key + URL are good. On failure, re-open the file and recheck the lines — never ask for a chat paste.

4. **Run the migrations by hand — and do the hand-holding. The SQL is NOT a secret, so you print it into the chat (this is the one thing that's fine to paste in chat — it's schema, not a credential).** A non-technical operator won't know to open `.sql` files and copy them, so **you** do the legwork: `Read` both migration files from `<BUNDLE_ROOT>/revenue-manager-plugin/migrations/` and **print the full SQL of each into the chat** for them to copy. Then walk it click-by-click:
   - Open **https://supabase.com/** → your project → **SQL Editor** → **New query**.
   - Paste the **001** block → click **Run**. Wait for success.
   - **New query** again → paste the **002** block → **Run**.
   - **New query** again → paste this verification SELECT → **Run**, and tell me what comes back:
     ```sql
     SELECT table_name FROM information_schema.tables
       WHERE table_schema='public'
       AND table_name IN ('market_snapshots','pricelabs_change_log','pricing_decisions','property_config');
     SELECT column_name FROM information_schema.columns
       WHERE table_name='pricing_decisions'
       AND column_name IN ('booked_at','lead_time_days','price_delta_from_rec');
     ```
   - Confirm: **4 tables** in the first result, **3 columns** in the second.

> **Checkpoint 3:** Supabase MCP detected/installed (any flavor) — or REST fallback chosen ✅ · any Supabase credential (PAT or service_role key) pasted into a git-ignored `.env`/config (never the chat) and verified — 3 sanity checks ✅ · project chosen/confirmed ✅ · `001` then `002` applied ✅ · 4 tables + 3 outcome columns verified ✅.

---

### PHASE 4 — Offer the enrichment add-ons (ASK about each, every time — optional)

**Goal:** the operator has been **explicitly offered all three bundled enrichment add-ons — RankBreeze, Turno, and AirROI — one at a time**, and connected the ones they want, BEFORE the plugin's first run (so the very first "check my pricing" already includes them). They are **optional**: the operator can skip any or all — but you must **ask about each one, every run.** Never silently skip the offer, and never skip straight to the plugin without going through these three.

> Frame it: *"Before I install the Revenue Manager, let's hook up any extras you want — they make the report richer. I'll run through three quick ones; say yes to set one up, or skip to move on. All optional, and you can add any later."*

**Ask about each of these three, in order, one at a time.** For each: name it, give the one-line value, and ask **"set it up now, or skip?"**
- If **yes** → walk the full setup with the **same discipline as Phases 1–3**: read the bundled `README` / `.env.example`, `cp` the example file, **open** it for them, the operator pastes the value **into the file** (never the chat), run the 3 sanity checks (SAFE → FILLED → WORKS), register with absolute paths, and **full-restart** Claude Code.
- If **skip** → acknowledge, note they can add it any time later (the skill auto-detects it on the next run), and move to the next one.

**1️⃣ RankBreeze — ranking / visibility** *(pre-built)* — the visibility spoke of the flywheel (search rank + page-view signal); without it, ranking stays a manual check.
`cd mcp-servers/rankbreeze` → `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. **No API key** — auth is the `_godzilla_session` browser cookie in `session.txt`. Contract: `cp session.txt.example session.txt`, **open** it (`open -e session.txt` / `notepad session.txt` / `xdg-open session.txt`), the operator pastes the cookie value **into the file** (never the chat), save. 3 sanity checks (SAFE: `.gitignore` blocks `session.txt`; FILLED: `grep -q '.\+' session.txt`; WORKS: register + a real `health_check`/list call). Its README / `session.txt.example` says where to find the cookie. Register the venv interpreter pointed at `server.py`, restart.

**2️⃣ Turno — cleaning / ops** *(pre-built, needs Python 3.11+)* — turnover-cost signal; flags margin leaks from too many 1-night stays.
First check `python3 --version` is **3.11+** (Turno's `pyproject.toml` requires it). `cd mcp-servers/turno` → `uv sync` (or, no uv, `python3 -m venv .venv && .venv/bin/pip install -e .`). Contract: `cp .env.example .env`, **open** it, the operator pastes **into the file** — `TURNO_API_TOKEN` (the JWT starting `eyJ`) + `TURNO_PARTNER_ID` (the UUID), and keep `TURNO_ENV=sandbox`. **Turno's API is partner-gated** — if they don't have access yet, tell them to email `support@turno.com` to enable External API v2 and **skip for now** (they can add it later). 3 sanity checks (FILLED via `grep` per var; WORKS via `turno_check_connection`). Register: uv → `uv --directory <ABS>/mcp-servers/turno run turno-mcp`; pip-venv fallback → `<ABS>/mcp-servers/turno/.venv/bin/turno-mcp` (console script — Turno is a package, no top-level `server.py`). Restart.

**3️⃣ AirROI — named-competitor comps** *(pre-built)* — named competitors a guest would actually compare, each with TTM revenue / ADR / occupancy / ratings, on top of PriceLabs' aggregate percentiles. Returns figures in **native local currency** by default (CAD for Canadian markets, etc.) and **covers Canada + international markets, not just the US.**
`cd mcp-servers/airroi` → `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. Contract: `cp .env.example .env`, **open** it, the operator grabs a **free** key at **https://www.airroi.com/api/developer/activate** and pastes it **into the file** on `AIRROI_API_KEY=`, save. 3 sanity checks (FILLED via `grep`; WORKS via a real comp/list call). Register the venv interpreter pointed at `server.py`, restart.

**Mention only if they ask, or if they use Breezeway instead of Turno:** Breezeway is **built fresh** from `build-pricing-ops-mcp.md` (you write the code — no external skill), credential via the same contract. Offer as the ops alternative for non-Turno operators.

**AirROI currency note:** AirROI returns each market's **native local currency** by default (the connector calls it with `currency=native`) — so a Canadian operator gets CAD comps that already match PriceLabs, no conversion needed. It covers Canada and international markets, not just the US. The skill's currency gate still **verifies** the returned currency and only converts/flags on a genuine mismatch (e.g. a cross-border comp in a different currency) — never silently mixes. (RankBreeze and Turno have no currency issue.)

After each add-on the operator connects: **full restart** of Claude Code, then confirm the new tools appear (e.g. `mcp__rankbreeze__*`, `mcp__turno__*`, `mcp__airroi__*`). The plugin's first run in Phase 5 then shows the new spoke in its detection report automatically.

> **Checkpoint 4:** all three add-ons were **explicitly offered, one at a time** ✅ · each one the operator chose is set up via the credential contract + 3 sanity checks and its tools appear after a full restart ✅ · skipped ones acknowledged (operator knows they can add later) ✅.

---

### PHASE 5 — Install the plugin & first run

**Goal:** the Revenue Manager plugin installed, configured for their properties, and a successful recommend-only first run.

> **Reminder:** the plugin installs from the **local folder** at `<BUNDLE_ROOT>/revenue-manager-plugin`. It's already on disk (Phase 0 confirmed it), so this is straightforward.

**Step 5.1 — Install the plugin.**

Two ways — pick whichever fits:

**Option A — local plugin install (preferred):**
```
/plugin install <BUNDLE_ROOT>/revenue-manager-plugin
```

**Option B — manual copy:**
Copy the `revenue-manager-plugin` folder into the Claude Code plugins directory:
- **Mac/Linux:** `~/.claude/plugins/`
- **Windows:** `%USERPROFILE%\.claude\plugins\`

**Step 5.2 — Restart reminder.**

> 🔁 One more time — **fully quit and reopen Claude Code** so the plugin loads. Then come back and say "plugin installed, continue."

After restart, confirm the **revenue-manager** skill is available (it should trigger on pricing/revenue talk).

**Step 5.3 — First-run property config.**

Now configure each property so the skill can tell real markup from price drift. Run the config pass and explain it plainly. Cover three things:

1. **Floor/ceiling — zero effort.** The skill reads each listing's **existing PriceLabs min/max** live and stores them as the **floor/ceiling** in `property_config`. Reassure the operator: *they don't have to enter these — it reads them automatically.*
2. **Markup % — ask them.** One question:

   > What's your **PriceLabs markup %**? A common setup is **16%** (Airbnb 14% + a 2% buffer). If you don't add a nightly markup at all, that's totally normal too — just say "no markup" and I'll measure it.

   Store it (it confirms/overrides the empirically measured value).
3. **Optional Tier-2 cost-based floor.** Offer it, don't push it:

   > Optional: I can also set a **cost-based floor** — your breakeven (cleaning + fixed nightly cost) so the floor never drops below what the night actually costs you. Want that, or skip for now?

Also note the **default max-delta is 25%** (no single recommended change moves a price more than 25% at once unless they confirm) — mention it once so they know the guardrail exists. They can change any of this in plain English later.

A clean way to kick this off (mirror the plugin's first-run line):

> "Set up property config for all my listings. My PriceLabs markup is 16% (Airbnb 14% + 2% buffer)."

**Step 5.4 — Test run: "check my pricing" (recommend-only).**

Run a real, recommend-only pass. Have the operator say:

> Check my pricing

Then confirm the skill does all of this (this is the proof it's wired up right):
- **Detects the stack** and prints the detection report (PMS ✅, Pricing ✅, Supabase ✅, plus any optional enrichment it happens to find).
- **Reads Supabase history** (prior decisions / change log / snapshots / config — empty on the first run, that's expected).
- **Pulls PMS + PriceLabs in parallel** — full-year forward calendar + history from the PMS, and listings + recommendations + neighborhood comp data from PriceLabs.
- **Produces a recommend-only report** with the **safety layer** visibly applied: floor/ceiling shown, comp count (N) transparency, native currency, and data freshness (PriceLabs `last_refreshed_at`).
- **Never auto-writes.** Every proposed change sits behind the approval gate — nothing is pushed to PriceLabs or the PMS without an explicit yes.
- **Offers a spreadsheet at the end.** After the recommendations, the skill asks if you want a workbook — Summary tab + one tab per property (full breakdown), saved to your Desktop. Say yes to see it; it's report-only and pushes nothing. (First time, it quietly sets up a small local `openpyxl` venv; if that can't install, it falls back to CSVs.)

If the detection report is missing the PMS or pricing tool, **stop and fix it** (usually a skipped restart, or an MCP that didn't register) before declaring done.

> **If Supabase shows "not connected" on this first run even though Phase 3 passed:** this is almost always a **detection-prefix gap, not a setup failure.** The plugin's own first-run check currently looks specifically for the `mcp__supabase__*` flavor; if you connected Supabase under a *scoped or Claude-connector* prefix (e.g. `mcp__supabase-jarvis__*` or `mcp__claude_ai_Supabase__*`), the plugin may report it as missing even though the migrations ran fine and the tables exist. Don't send the operator back to redo Phase 3. Confirm the 4 tables + 3 columns are really there (a quick `list_tables` / SELECT on the connected server), tell the operator the audit DB is good, and — if they want the plugin to auto-detect it cleanly every run — either register Supabase under the plain `supabase` name or note it as a known prefix-matching quirk. Only treat it as a real failure if the tables genuinely don't exist.

> **Checkpoint 5:** Plugin installed + restarted ✅ · property config set (floors/ceilings auto-read, markup captured, max-delta 25%) ✅ · "check my pricing" produced a recommend-only report with the safety layer and **no** auto-write ✅. (A Supabase "not connected" notice that turns out to be a prefix-detection quirk — tables actually present — counts as a pass.)

---

### DONE — send this message (Solnest AI voice)

Once the core phases are done — PMS, pricing, Supabase, the Phase 4 add-on offer, and the plugin's first run — send the operator a wrap-up like this (warm, casual, encouraging — adjust the specifics to what actually got connected):

> 🎉 **That's it — your Revenue Manager is live.** Honestly, that's freaking awesome. Here's what you just wired up:
>
> - ✅ **<PMS name>** — your real bookings + calendar, straight into Claude
> - ✅ **<PriceLabs / Wheelhouse / Beyond>** — your pricing engine + comp data
> - ✅ **Supabase** — your audit database (4 tables, all set), so every run builds on the last one
> - ✅ **Any enrichment add-ons you connected** (RankBreeze · Turno · AirROI) — folded into every report automatically (skipped any? add them anytime later)
> - ✅ **The Revenue Manager plugin** — tested with a real "check my pricing" run
>
> **The deal — and this is the important part:** it's **recommend-only.** It tells you exactly where you're leaving money on the table, shows you the comps, the floor/ceiling, the currency, and how fresh the data is — and then **you approve every single change.** Nothing gets pushed to PriceLabs or your PMS silently, ever. You're always in the driver's seat.
>
> **Try these next:**
> - "How are my properties doing?"
> - "Am I underpriced for next month?"
> - "Compare my prices vs last year's same week"
> - "Recommend min/base/max updates for the 2-bedrooms"
>
> **To update later:** grab the latest version of this folder when one's shared, drop it in, reinstall the plugin, and restart Claude Code. Your keys live in your local `.env` files and never need to move — just never `git add` a `.env`.
>
> 🙌 **Want to actually master this — not just run it?** This whole build came out of the **Solnest AI community**, where we teach STR operators to wire AI into their business themselves. If you want to go deeper, tune your strategy, and join the live calls, come hang out: **[skool.com/solnest-ai](https://www.skool.com/solnest-ai)**
>
> See you at the event — bring your real numbers and we'll tune it together. 🚀

---

## Quick reference (for the operator)

| Phase | What it connects | If it breaks |
|---|---|---|
| 0 | Orientation + prereqs | Re-open this folder, say "set this up" |
| 1 | Your PMS | Hospitable: re-run the `mcp-servers/hospitable` build · others: re-run `build-pms-mcp.md` |
| 2 | PriceLabs (pricing) | PriceLabs: re-run the `mcp-servers/pricelabs` build · others: re-run `build-pricing-ops-mcp.md` |
| 3 | Supabase audit DB | Re-check the MCP + re-run both migrations |
| 4 *(optional — always offered)* | Enrichment: RankBreeze · Turno · AirROI · Breezeway | Re-add the tool, fully restart |
| 5 | Revenue Manager plugin | Reinstall plugin, **fully restart** Claude Code |

**Three golden rules:** your keys go into a local `.env` / `session.txt` that Claude opens for you — **you paste into the file, never into the chat** — and they never get committed (never `git add` a `.env`) · always **fully restart** Claude Code after adding an MCP or the plugin · it **recommends, you approve** — nothing pushes on its own.

**To update:** drop in the latest version of this folder when one's shared, reinstall the plugin, restart Claude Code — and never `git add` your `.env`.
