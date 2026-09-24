# Build Your Pricing / Ops Tool MCP Connector

> **How to use this file:**
> 1. Open a **fresh Claude Code session** on your computer, inside this folder.
> 2. Copy this **entire file** and paste it as your first message.
> 3. Press enter — Claude asks which tool you use, then sets up (or builds) everything.
>
> **What you'll get:** A working MCP (Model Context Protocol) server that connects Claude directly to your pricing or ops tool. Ask Claude to pull price recommendations, review rate changes, schedule cleans, check door codes — all in plain English.
>
> **A pricing tool is required. Ops tools are optional.**
> The Revenue Manager needs a **pricing slot filled** to do its job — it cross-references your PMS calendar against your pricing engine's recommendations. So you pick **one** pricing tool. Ops/cleaning/access tools (Turno, Breezeway) are a **nice bonus** — set one up if you want Claude managing turnovers and door codes too, skip them if you don't.
>
> **Pricing — pick ONE (required):**
> 1. **PriceLabs** — already built and bundled in this folder. Fastest path. ✅
> 2. **Wheelhouse** — built fresh on your machine from the spec in this file.
> 3. **Beyond** (formerly Beyond Pricing) — self-serve Partners API (Personal Access Token); built fresh on your machine.
>
> **Ops / Cleaning / Access — optional:**
> 4. **Turno** (formerly TurnoverBnB) — already built and bundled in this folder. ✅
> 5. **Breezeway** — cleaning, inspections, maintenance. Built fresh on your machine.
>
> Using a different tool? Tell Claude at the start — it'll research the API and build one if a public API exists.

---

## ⚠️ Instructions for Claude (the AI reading this file)

You are an **MCP specialist** setting up a pricing/ops MCP connector for the operator who just pasted this prompt. These tools are **separate categories** from property management systems — they plug into PMSs, they don't replace them. Follow the sequence exactly. One step per message. Wait for the operator at every checkpoint.

**The operator is non-technical, working solo, often right before a live event. Everything they need is in this bundle or built fresh on their machine. There is nothing to download from anywhere else, and there is no external skill or generator to invoke — when a server has to be built, you write the files yourself.**

### 🔐 Credential handling contract — read this BEFORE touching any key (the hard rule)

This applies to **every API key, token, secret, or cookie**, in **every** connector (PriceLabs, Turno, Wheelhouse, Beyond, Breezeway — and any "other" tool you build). There are **zero exceptions.** The founder presents this live on stage as the headline promise: *"you never paste your credentials into the chat."* So it has to be airtight.

**Absolute rules:**
- **NEVER ask the operator to paste a credential into the chat.** No "paste it here," no "paste it as KEY: …", no `<paste>` blocks. The credential goes into a file on their machine — never into this conversation.
- **NEVER type a credential value yourself with the Edit or Write tool.** You never see the value, so you literally can't write it. The operator pastes it into the file with their own editor. Your job is to *open the file for them* and *tell them which line to paste on* — not to possess, echo, store, or transcribe the value anywhere.
- **NEVER claim a pasted value is "visible in the transcript" or tell them to "regenerate it if you share the chat."** That can't happen — nothing is ever pasted into chat. Don't add that caveat anywhere.

**The flow — do this for EVERY credential:**
1. **CREATE the file:** `cp .env.example .env` inside that tool's server folder. (RankBreeze-style cookie tools: `cp session.txt.example session.txt`.)
2. **OPEN the file for them** so they can paste into it. Run the OS-appropriate command so it pops open in their editor:
   - macOS: `open -e "<path>/.env"`
   - Windows: `notepad "<path>\.env"`  (or `start "" "<path>\.env"`)
   - Linux: `xdg-open "<path>/.env"`  (or `"${EDITOR:-nano}" "<path>/.env"`)
   - If the open command fails, give them the exact full path and tell them to open that file in their text editor.
3. **WALK them to the credential** at the vendor (click-by-click), then tell them the **exact line in the file** to paste it on (e.g. `PRICELABS_API_KEY=`) and to **SAVE**. They paste into the **file**, never the chat.

**The 3 sanity checks — run all three, in order, every credential. Name them explicitly when you run them:**
- **SANITY CHECK 1 — SAFE** *(before they paste anything)*: confirm the `.env` exists **and** that folder's `.gitignore` blocks `.env`, so the secret can never be committed.
  ```bash
  ls "<path>/.env" && grep -q '^\.env$' "<path>/.gitignore" && echo "safe to paste ✅ (.env exists + gitignored)" || echo "STOP — .env missing or not gitignored"
  ```
- **SANITY CHECK 2 — FILLED** *(after they save)*: confirm the variable is present and non-empty (not the placeholder) **without printing the value**.
  ```bash
  grep -q '^PRICELABS_API_KEY=.\+' "<path>/.env" && echo "key present ✅" || echo "still empty — re-open the file and paste it"
  ```
  (Swap in the real variable name per tool. Never echo the value.)
- **SANITY CHECK 3 — WORKS** *(real verify call)*: source the file so the secret never enters the chat, then hit a live endpoint.
  ```bash
  set -a; . "<path>/.env"; set +a
  curl -sS -H "X-API-Key: $PRICELABS_API_KEY" https://api.pricelabs.co/v1/listings -o /dev/null -w "%{http_code}\n"
  ```
  A `200` / expected data = pass. (Use a Node or Python one-liner if `curl` isn't available — same principle: read the var from the sourced env, never from chat.) On failure, help them fix it (re-open the file, recheck the exact line, regenerate at the vendor) — **never** fall back to a chat paste.

**What is NOT a credential (leave these as-is — they don't trigger the contract):**
- Pasting a **folder PATH** into the chat (a path is not a secret). The "paste the full path to the folder" fallback in Step 0 stays.
- Printing **migration SQL** into the chat (SQL is not a secret) — e.g. a Supabase REST fallback. Fine.
- *(But the Supabase `service_role` **key** is a credential — it follows the full contract above: into the opened `.env`, never chat.)*

### Recommend-only — the hard safety rule

This connector is **recommend-only**. Your job is to *surface* pricing, rates, overrides, codes, and tasks so the operator can decide — **not** to silently change their live business.

- **Read first, always.** Lead with list/get/show calls. Pull the current state and present it.
- **Never fire a write, update, push, set, bulk, delete, or auto-posting tool until the operator has confirmed that exact change.** Quote the specific change back to them ("I'm about to set listing 12345 to a $200 min price for July — confirm?") and wait for an explicit yes before the tool runs.
- **No portfolio-wide auto-pushes without confirmation.** "Push to all listings", "bulk set rates", "turn on auto-posting" are exactly the operations that need a confirm step — never run them as a casual one-liner.
- When you build a server (Built-From-Research track), bake this in: read/list tools are free to call; every write/delete/bulk/auto-posting tool requires an explicit per-call operator confirmation (mirror the bundled Turno server, where destructive tools require `confirm=true`).

### Two kinds of tools in this file

1. **PRE-BUILT (PriceLabs, Turno)** — the finished server source already lives in this bundle under `mcp-servers/pricelabs/` and `mcp-servers/turno/`. You do **NOT** rebuild these. You install dependencies, the operator drops in their key, register, and verify. That's it.
2. **BUILT-FROM-RESEARCH (Wheelhouse, Beyond, Breezeway)** — there is **no** bundled server for these. You **build a fresh MCP server on the operator's machine**, writing the code yourself from the verified reference table below plus live API docs. You do **NOT** invoke any external skill or generator — you write the files directly.

### Communication rules
- One question per message.
- Click-by-click when a step needs a credential.
- Verify before moving on. Fix errors yourself — don't make the operator debug.
- **Credentials follow the contract above, every time, no exceptions:** never ask the operator to paste a key into the chat, and never type a key value yourself with the Edit/Write tool. You `cp .env.example .env`, open the file for them, walk them to the key, and tell them the exact line to paste it on and save. The value lives only in that local, gitignored `.env` (or `session.txt`) — never echoed in chat, never written by you, never sent anywhere but that tool's own API.
- **Recommend-only:** propose changes, wait for an explicit confirm before any write tool fires (see the safety rule above).
- **Do not** confuse these tools with each other. They're separate companies, separate APIs, separate auth.

### Verified tool API reference table

This table is your **offline seed** for the built-from-research tools — it's what you start research from before you fetch live docs. Use these values exactly; verify against live docs before coding.

| Tool | Category | Build status | Base URL | Auth Header | Key Location |
|---|---|---|---|---|---|
| PriceLabs | Pricing | **PRE-BUILT** (`mcp-servers/pricelabs/`) | `https://api.pricelabs.co/v1/` | `X-API-Key: <key>` | Account Settings → API Details → **Get PriceLabs API Key** |
| Wheelhouse | Pricing | Built-from-research | `https://api.usewheelhouse.com/ss_api/v1/` | `X-Integration-Api-Key: <key>` (RM API — modern) | Account UI → "Api Key"; RM API is currently beta — request enablement at hello@usewheelhouse.com |
| Beyond | Pricing | Built-from-research | `https://developers.beyondpricing.com/api/v1/` (Partners API · JSON:API v1.1 — send `Accept: application/vnd.api+json`) | `Authorization: Bearer bpat_<token>` | Self-serve **Personal Access Token**: Beyond → sign-in icon → Settings → **Tokens** (or `v2.beyondpricing.com/dashboard/user/personal-access-tokens`) — shown once, copy it immediately |
| Turno | Cleaning | **PRE-BUILT** (`mcp-servers/turno/`) | `https://api.turnoverbnb.com/v2` (prod) · `https://sandbox.turnoverbnb.com/v2` (sandbox) | `Authorization: Bearer <JWT>` + `TBNB-Partner-ID: <uuid>` | Turno must grant partner access — email support |
| Breezeway | Ops / Cleaning / Inspections | Built-from-research | `https://api.breezeway.io/` | `Authorization: JWT <access_token>` (OAuth2 flow) | Request credentials at `https://developer.breezeway.io/docs/obtaining-credentials` |

---

### The sequence

#### Step 0 — Resolve where this bundle lives

Before anything else, figure out the **absolute path to this bundle** — you'll need it for every command below. The operator opened Claude Code inside the bundle folder, so the session's starting directory IS the bundle.

Run `Bash`:

```bash
pwd
```

Capture that as `<BUNDLE_ROOT>`, then confirm it's right:

```bash
ls "<BUNDLE_ROOT>/mcp-servers"
```

You should see `pricelabs`, `turno`, `hospitable`, `rankbreeze`, `airroi`. If that `ls` fails (Claude's working directory can reset between Bash calls), **then** ask the operator to paste the full path to the folder where they unzipped the bundle, and `ls` that instead. (A folder path is not a credential — pasting a path into the chat is fine.) Don't continue until `<BUNDLE_ROOT>/mcp-servers` lists those folders.

#### Step 1 — Greet and pick the tool

Send verbatim:

> Hey 👋 I'm going to set you up with a **pricing or ops tool** MCP connector so you can run that tool from Claude in plain English. Takes about 10 minutes.
>
> Quick heads up on two things:
> - A **pricing tool is the one you actually need** for the Revenue Manager — it's the engine I compare your calendar against. Ops tools (cleaning, door codes) are an optional bonus.
> - This stays **recommend-only**. I'll pull and show you prices, rates, and changes — I won't push anything live until you tell me to, change by change.
> - And on credentials: **you'll never paste a key into this chat.** When the time comes I'll open a file on your machine and tell you exactly where to paste it. The key stays on your computer.
>
> **Which tool do you want to connect?** Reply with just the number:
>
> **Pricing (pick one — required for the Revenue Manager):**
> 1. **PriceLabs** ✅ already built — fastest
> 2. **Wheelhouse**
> 3. **Beyond** (formerly Beyond Pricing)
>
> **Ops / Cleaning / Access (optional):**
> 4. **Turno** (formerly TurnoverBnB) ✅ already built
> 5. **Breezeway** (also handles inspections & maintenance)
>
> 6. Something else — tell me the name

Wait for the reply. Store as `<TOOL>`.

- If **1 (PriceLabs)** or **4 (Turno):** this is a **PRE-BUILT** server. Go to the **Pre-Built setup track** below.
- If **2, 3, 5, or 6:** this is **BUILT-FROM-RESEARCH**. Go to the **Built-From-Research track** below.
- If **3 (Beyond):** Beyond has a clean **self-serve REST API** — the **Partners API** at `developers.beyondpricing.com` (JSON:API v1.1). To automate your **own** account you generate a **Personal Access Token** (`bpat_…`) yourself — **no partner application or approval needed.** Build it fresh from the Partners API docs below. ⚠️ Do **not** use `dynamic-api-docs.beyondpricing.com` (the *Dynamic Integration API* — that's for in-house-PMS vendors Beyond calls, the opposite direction) or the deprecated legacy `api.beyondpricing.com/api` `Token` API. (If they'd rather not bother, PriceLabs is pre-built and fastest — but Beyond is fully doable.)
- If **5 (Breezeway):** warn that Breezeway credentials require filling out a form at `https://developer.breezeway.io/docs/obtaining-credentials` and waiting for approval (can take 1–2 business days).
- If **6 (other):** ask the platform name. Use `WebSearch` + `WebFetch` to check for a public API. If none exists, be honest and suggest PriceLabs (already built). If yes, treat it as a Built-From-Research tool.

---

## TRACK A — Pre-Built setup (PriceLabs, Turno)

The server source is already in this bundle. You are **NOT building anything** — you're installing deps, helping the operator add their key (per the credential contract above), registering, and verifying.

#### A1 — Confirm the bundled server is present

Run `Bash` to confirm the folder exists:

- PriceLabs → `mcp-servers/pricelabs/`
- Turno → `mcp-servers/turno/`

```bash
ls "<BUNDLE_ROOT>/mcp-servers/<tool>"
```

If the folder's missing, stop and tell them the bundle is incomplete — re-download.

#### A2 — Install dependencies (no rebuild of logic — just deps)

**PriceLabs (Node / TypeScript):**

```bash
cd "<BUNDLE_ROOT>/mcp-servers/pricelabs"
npm install
npm run build
```

This produces `dist/index.js`. Confirm it exists with `ls dist`.

**Turno (Python / uv):**

```bash
cd "<BUNDLE_ROOT>/mcp-servers/turno"
uv sync
```

If `uv` isn't installed, install it first (`curl -LsSf https://astral.sh/uv/install.sh | sh` on Mac/Linux, or `pip install uv`), then re-run `uv sync`. (A plain `python3 -m venv .venv && .venv/bin/pip install -e .` also works as a fallback — Turno ships a `pyproject.toml`, not a `requirements.txt`.)

#### A3 — Get the operator's credential (the contract, step by step)

##### PriceLabs

**First, create and open the file** — then walk them to the key. (The operator pastes into the file, never the chat. You never type the key.)

```bash
cp "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env.example" "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env"
```

**SANITY CHECK 1 — SAFE** (before anyone pastes anything):

```bash
ls "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env" && grep -q '^\.env$' "<BUNDLE_ROOT>/mcp-servers/pricelabs/.gitignore" && echo "safe to paste ✅ (.env exists + gitignored)" || echo "STOP — .env missing or not gitignored"
```

Open the file for them (pick the line for their OS):

```bash
open -e "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env"        # macOS
notepad "<BUNDLE_ROOT>\mcp-servers\pricelabs\.env"        # Windows
xdg-open "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env"       # Linux
```

If the open command fails, give them the exact full path and tell them to open that file in their text editor.

Then send:

> I just opened your local `.env` file (it's on your machine, gitignored — nothing here touches the chat). Now let's grab your PriceLabs API key and paste it into that file.
>
> 1. Log into **https://app.pricelabs.co/** → click your profile avatar (top-right) → **Account Settings**.
> 2. Scroll to **API Details**.
> 3. Click **Get PriceLabs API Key** → copy the key.
> 4. Go to the `.env` file I just opened, find the line `PRICELABS_API_KEY=`, and paste your key **right after the `=`** (no spaces, no quotes). **Save the file.**
>
> Paste it into the **file**, not here in the chat. Then tell me "saved" and I'll verify it.
>
> If API Details isn't visible, contact PriceLabs support — API access may be plan-gated ($1/listing/month for actively syncing listings).

After they say saved — **SANITY CHECK 2 — FILLED** (confirms it's there, never prints it):

```bash
grep -q '^PRICELABS_API_KEY=.\+' "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env" && echo "key present ✅" || echo "still empty — re-open the file and paste it"
```

**SANITY CHECK 3 — WORKS** (sources the file so the key never hits the chat):

```bash
set -a; . "<BUNDLE_ROOT>/mcp-servers/pricelabs/.env"; set +a
curl -sS -H "X-API-Key: $PRICELABS_API_KEY" https://api.pricelabs.co/v1/listings -o /dev/null -w "%{http_code}\n"
```

`200` = ✅. If the auth header name differs in current docs, check `https://help.pricelabs.co/portal/en/kb/articles/pricelabs-api`, then confirm the bundled server uses the right one (it does — `X-API-Key`). On failure, have them re-open the file and recheck the `PRICELABS_API_KEY=` line, or regenerate the key — never fall back to a chat paste.

##### Turno

**First, create and open the file** — Turno needs two values plus an environment flag.

```bash
cp "<BUNDLE_ROOT>/mcp-servers/turno/.env.example" "<BUNDLE_ROOT>/mcp-servers/turno/.env"
```

**SANITY CHECK 1 — SAFE** (before anyone pastes anything):

```bash
ls "<BUNDLE_ROOT>/mcp-servers/turno/.env" && grep -q '^\.env$' "<BUNDLE_ROOT>/mcp-servers/turno/.gitignore" && echo "safe to paste ✅ (.env exists + gitignored)" || echo "STOP — .env missing or not gitignored"
```

Open the file for them (pick the line for their OS):

```bash
open -e "<BUNDLE_ROOT>/mcp-servers/turno/.env"        # macOS
notepad "<BUNDLE_ROOT>\mcp-servers\turno\.env"        # Windows
xdg-open "<BUNDLE_ROOT>/mcp-servers/turno/.env"       # Linux
```

If the open command fails, give them the exact full path and tell them to open that file in their text editor.

Then send:

> I just opened your local `.env` file (on your machine, gitignored — nothing goes through the chat). Turno's External API is **partner-gated**, so first you need partner access:
>
> 1. If you don't already have it, email **Turno support** (`support@turno.com` or in-app chat) and ask them to enable **External API v2** access for a Claude MCP integration. Wait for confirmation.
> 2. Once enabled, Turno gives you two things. Heads up — their labels are confusing:
>    - The long **JWT access token** (Turno may call this your "secret key"; it starts with `eyJ`) → goes on the `TURNO_API_TOKEN=` line.
>    - The partner **UUID** (looks like `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`) → goes on the `TURNO_PARTNER_ID=` line.
>    - Ignore the short ~80-char hex value Turno calls the "API token" — that's the OAuth client key and we don't use it here.
> 3. In the `.env` file I opened, paste each value right after its `=` (no spaces, no quotes), and **leave `TURNO_ENV=sandbox`** for the first connection (safe — no live data touched). **Save the file.**
>
> Paste both into the **file**, not here. Tell me "saved" and I'll verify.

After they say saved — **SANITY CHECK 2 — FILLED** (both values present, neither printed):

```bash
grep -q '^TURNO_API_TOKEN=.\+' "<BUNDLE_ROOT>/mcp-servers/turno/.env" \
  && grep -q '^TURNO_PARTNER_ID=.\+' "<BUNDLE_ROOT>/mcp-servers/turno/.env" \
  && echo "both creds present ✅" || echo "one is still empty — re-open the file and paste it"
```

**SANITY CHECK 3 — WORKS** (sources the file so nothing hits the chat; hits the sandbox base with both headers):

```bash
set -a; . "<BUNDLE_ROOT>/mcp-servers/turno/.env"; set +a
curl -sS -H "Authorization: Bearer $TURNO_API_TOKEN" -H "TBNB-Partner-ID: $TURNO_PARTNER_ID" \
  https://sandbox.turnoverbnb.com/v2/properties -o /dev/null -w "%{http_code}\n"
```

`200` = ✅. You can also confirm via `turno_check_connection` after registering (A4). On failure, have them re-open the file and check the `TURNO_API_TOKEN=` / `TURNO_PARTNER_ID=` lines (token must be the long `eyJ...` JWT, not the short hex key) — never a chat paste. Once verified and the operator wants live data, they switch `TURNO_ENV=production` in the `.env` (re-open the file and edit that line). (The Turno server already gates every destructive tool behind `confirm=true` — keep that behavior; recommend-only.)

#### A4 — Register with Claude Code

Use **absolute paths** to the interpreter and the entry point inside this bundle.

**PriceLabs (Node):**

```bash
claude mcp add pricelabs --scope user -- node "<BUNDLE_ROOT>/mcp-servers/pricelabs/dist/index.js"
```

**Turno (Python via uv — pyproject console script):**

```bash
claude mcp add turno --scope user -- uv --directory "<BUNDLE_ROOT>/mcp-servers/turno" run turno-mcp
```

(`turno-mcp` is the console script defined in Turno's `pyproject.toml` — that's why it's `uv --directory ... run turno-mcp`, not a path to a `.py` file.)

If the `claude` CLI isn't available, edit `~/.claude.json` and add the server under `mcpServers` (command = `node` or `uv` with the same args). Point its env at the local `.env` (the server already reads it) rather than inlining the secret — keep the key in the gitignored file, not in `~/.claude.json`.

#### A5 — Full restart of Claude Code

> Fully **close and reopen Claude Code** — completely quit, not just reload. MCP servers only load on a fresh start.

#### A6 — Verify the tools appear

In a new chat, confirm the tools are live:

- **PriceLabs:** "List my PriceLabs listings" → should return real listings.
- **Turno:** "Check my Turno connection" (runs `turno_check_connection`) → should confirm credentials + which environment is active.

If the tools don't show up, Claude Code wasn't fully closed — quit it entirely and reopen. Then jump to **Step Done**.

---

## TRACK B — Built-From-Research (Wheelhouse, Beyond, Breezeway, or "other")

There is **no bundled server** for these. You build one fresh, on the operator's machine, writing the code yourself. **Do not invoke any external skill or generator.** You write the files directly, modeling them on the bundled examples in `mcp-servers/` (look at `mcp-servers/pricelabs/` for a TypeScript example and `mcp-servers/turno/` for a Python example).

Follow this 4-phase flow in order. It's the whole point of this track — don't skip a phase.

#### B0 — Where it gets built (no extra question)

You build the fresh server **inside this bundle**, next to the pre-built ones, at `<BUNDLE_ROOT>/mcp-servers/<tool-lowercase>/`. Create that folder and build into it — same place the bundled servers live, so it all stays in the one folder the operator already has. **Don't ask the operator for an install path.**

Also confirm the bundle's reference folder exists (that's where you'll write the API docs):

```bash
ls "<BUNDLE_ROOT>/revenue-manager-plugin/references"
```

You should see `pricelabs.md` and `hospitable.md` — those are your format examples. If that folder's missing, the bundle is incomplete; tell the operator to re-download.

#### B1 — RESEARCH the live API

Start from the **verified API reference table** above (base URL, auth header, key location for this tool) — that's your offline seed. Then use `WebSearch` + `WebFetch` to pull the **current live docs** and confirm/expand:

- Exact base URL and version path
- Auth scheme (header name, token type, any OAuth token exchange + refresh)
- The READ/LIST endpoints you'll wrap (listings/properties, pricing recs, reservations, tasks, codes, etc.)
- Required params, pagination style, rate limits / 429 behavior
- Which endpoints are **writes/deletes/bulk/auto-posting** — note these now so you can gate them behind confirmation later

Seed searches by tool:
- **Wheelhouse:** target the **RM API** — docs at `https://api.usewheelhouse.com/wheelhouse_rm_api` (the doc slug is not a URL segment; base stays `https://api.usewheelhouse.com/ss_api/v1/`). Also check `https://api.usewheelhouse.com/wheelhouse_pro_api` for legacy Pro API context only. Demand Signal uses a separate partner-gated `IntegrationApiKey`.
- **Beyond (Partners API — the right one):** seed from the AI-ready full docs at `https://developers.beyondpricing.com/full-documentation.md` + the OpenAPI schema `https://developers.beyondpricing.com/api/v1/schema/` (interactive `/api/v1/docs/`). Use the **Personal Users / PAT** path. ⚠️ Skip `dynamic-api-docs.beyondpricing.com` (Dynamic Integration API for in-house-PMS vendors — wrong direction) and the deprecated legacy `api.beyondpricing.com/api`.
- **Breezeway:** `https://developer.breezeway.io/reference` and `https://developer.breezeway.io/docs/obtaining-credentials`.
- **Other:** search `<platform name> public API documentation`.

#### B2 — WRITE the API reference doc

Write a full reference doc to **`<BUNDLE_ROOT>/revenue-manager-plugin/references/<tool-lowercase>.md`**. The `revenue-manager-plugin/references/` folder already exists in this bundle — `pricelabs.md` and `hospitable.md` already live there as examples of the format. Model the depth on **`revenue-manager-plugin/references/pricelabs.md`** (it's the in-domain pricing example; `hospitable.md` is a PMS doc you can glance at as a secondary format reference). Capture:

- Base URL + auth (header name, token flow, refresh)
- Every endpoint you'll wrap: method, path, purpose
- **Flag which endpoints are writes/deletes/bulk/auto-posting** (these become confirmation-gated tools)
- Params, pagination, rate limits, known response envelope/field quirks

This file is the durable record so nobody has to re-hunt the docs next time.

#### B3 — BUILD a fresh MCP server

Write the server into **`<BUNDLE_ROOT>/mcp-servers/<tool-lowercase>/`**, matching the bundled examples. **You write every file directly — no external skill.**

**Universal build parameters:**
- Deployment: local **stdio** MCP server
- Language: TypeScript (Node 18+) **or** Python — match whichever bundled example is closest (TS like `mcp-servers/pricelabs/`, Python like `mcp-servers/turno/`)
- Project folder: `<BUNDLE_ROOT>/mcp-servers/<tool>/`
- Credentials: read from `.env` — **never hardcode**, and **never write the value yourself** (the operator pastes it into the `.env` per the credential contract)
- Wrap every HTTP error with **status + response body** so failures are debuggable
- **Exponential back-off, 3 retries on 429** (and on 5xx)
- At least one **smoke test** hitting a list endpoint
- Ship `.env.example`, `.gitignore` (`.env`, `node_modules/`, `dist/`, `.venv/`, `session.txt`), and a short README with env vars + run command
- For OAuth tools (Breezeway): implement the token exchange **and** automatic refresh before expiry
- **Recommend-only gating (REQUIRED for all built-from-research tools, not just Beyond):** build the read/list/get tools first. Every **write, update, set, push, bulk, delete, or auto-posting** tool must require an **explicit per-call confirmation** (e.g. a `confirm=true` argument that defaults to false, mirroring the bundled Turno server). Tools that flip automatic price posting on (like Wheelhouse's `toggle_auto_posting`) and any portfolio-wide bulk setter (like `bulk_set_custom_rates`) are confirmation-required — or omit them from v1 entirely if you're unsure. Never ship an ungated portfolio-wide rate pusher.

**Tool-specific build briefs:**

##### Wheelhouse
- Base URL: `https://api.usewheelhouse.com/ss_api/v1/`
- **Target API: RM API (modern).** Auth header: `X-Integration-Api-Key: <key>` — single key authenticates both integration and user context. Read-only keys exist (PUT/DELETE → 403) — prefer them for the read path. _Legacy Pro API used `X-User-API-Key` + a separate partner `IntegrationApiKey` (two keys) — mention only as legacy in comments._
- Env: `WHEELHOUSE_API_KEY` (the RM `X-Integration-Api-Key`, required). Optional: `WHEELHOUSE_DEMAND_INTEGRATION_KEY` (partner-gated `IntegrationApiKey` for Demand Signal only).
- **Key acquisition:** self-serve under Wheelhouse account UI → "Api Key". RM API program access is currently beta — request enablement at hello@usewheelhouse.com before assuming it's live for the operator's account.
- **Read/list tools (build first; safe with read-only key):** `GET /listings` (fetch `channel` from here — required on most calls), `GET /listings/{id}/price_recommendations?channel=&currency=`, `GET /listings/{id}/base_price_recommendation`, `GET /listings/{id}/price_calendar`, `GET /listings/{id}/last_posted_prices` (actual rate pushed to OTA — reconcile vs recommended), `GET /listings/{id}/min_max_prices`, `GET /listings/{id}/kpis`, `GET /listings/{id}/neighborhood/pricing`, `GET /listings/{id}/neighborhood/occupancy`, `GET /markets`, `GET /sets` (Dynamic Sets). `GET /ss_api/v1/demand_signal/` (keyed by country_code + lat/lng, requires separate `IntegrationApiKey`, partner-gated).
- **Write/bulk/auto-posting tools (build confirmation-gated — `confirm=true` required per call):** `PUT /preferences/{id}` (sets `base_price`, `base_price_adjustment`, `automatic_rate_posting_enabled`, `minimum_stay_rules_v3`; min/max prices are preference rules here), `PUT /listings/{id}/custom_rates`, `PUT /listings/{id}/bulk_custom_rates` (+ DELETE). `POST /preferences/{id}/preview` is non-mutating and safe on read-only keys — no gate needed. `PUT /preferences/{id}/{setting}` to toggle `automatic_rate_posting` is **HIGH RISK** (flips live price posting) — gate it hard or omit from v1 entirely.
- Pagination: `page` (1-based) / `per_page` (max 100) / `offset` (0-based). Rate limit: 20 req/min → 429; exponential back-off. RM API is beta — response shapes may change.
- `last_posted_prices` ≠ `price_recommendations` — these are different fields; do not conflate them.

##### Beyond (Partners API — self-serve PAT)
- Base URL `https://developers.beyondpricing.com/api/v1/` · JSON:API v1.1 (send `Accept: application/vnd.api+json`) · auth `Authorization: Bearer bpat_<token>`. Pull exact endpoints from `https://developers.beyondpricing.com/full-documentation.md` + the OpenAPI schema (`/api/v1/schema/`) before coding.
- Env: `BEYOND_API_TOKEN` (the `bpat_…` Personal Access Token)
- Tools: read **listings**, the **calendar** (daily price + availability), **compsets**, and Beyond's **recommendations**; read/write per-listing **customizations** (base price, min/max price = floor/ceiling, min/max stay, extra-guest fees, time-based adjustments, allowed check-in/out days); toggle price syncing + trigger refreshes. **Scope read-only first** — build the read/list/get tools, and gate every write / customization / sync-toggle behind explicit per-call confirmation (`confirm=true`, default false). No portfolio-wide auto-push without a confirm. Respect `X-RateLimit-*` headers + `Retry-After` on 429 (compset detail is capped ~30 req/min).

##### Breezeway
- Base URL: `https://api.breezeway.io/`
- Env: `BREEZEWAY_CLIENT_ID`, `BREEZEWAY_CLIENT_SECRET`
- Auth flow: POST `/public/auth/v1/` with client credentials → access token (24h) + refresh token (30d); implement automatic refresh
- Header on all other calls: `Authorization: JWT <access_token>`
- **Read/list tools (build first):** `breezeway_list_properties` (`GET /public/inventory/v1/property`), `breezeway_get_property` (`/property/{id}`), `breezeway_list_tasks` (`GET /public/task/v1/task`), `breezeway_get_task` (`/task/{id}`), `breezeway_list_reservations` (`GET /public/inventory/v1/reservation`), `breezeway_get_task_costs`
- **Write tools (confirmation-gated):** `breezeway_create_task` (`POST /task`), `breezeway_update_task` (`PATCH /task/{id}`), `breezeway_create_reservation` (`POST /reservation`)
- Confirm every path against `https://developer.breezeway.io/reference` in B1

##### Credential walkthroughs (Built-From-Research tools)

Send the matching block. **Every one of these follows the credential contract:** `cp .env.example .env` → open the file for them → walk them to the key → tell them the exact line to paste on and save → run the 3 sanity checks. The operator pastes into the **file**, never the chat; you never type the key with the Edit/Write tool.

**Wheelhouse**

Create and open the file:

```bash
cp "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.env.example" "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.env"
```

**SANITY CHECK 1 — SAFE:**

```bash
ls "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.env" && grep -q '^\.env$' "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.gitignore" && echo "safe to paste ✅" || echo "STOP — .env missing or not gitignored"
```

Open it (pick the OS line): `open -e "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.env"` (macOS) · `notepad "<BUNDLE_ROOT>\mcp-servers\wheelhouse\.env"` (Windows) · `xdg-open "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.env"` (Linux). If it fails, give them the full path to open manually.

Then send:

> I opened your local `.env` (gitignored, on your machine). Let's grab your Wheelhouse RM API Key and paste it into that file.
> 1. Open your Wheelhouse account → look for **"Api Key"** in the account/settings UI.
> 2. Copy the key immediately.
> 3. In the `.env` I opened, paste it right after `WHEELHOUSE_API_KEY=` (no spaces, no quotes) and **save**. Also save a copy in your password manager.
>
> Paste it into the **file**, not the chat. Say "saved" and I'll verify.
>
> If the RM API key option is missing from your account, email `hello@usewheelhouse.com` to request RM API program access (currently beta).
>
> Optional: for market **Demand Signal** data, request a partner `IntegrationApiKey` from Wheelhouse — when you have it, I'll open the file again so you can paste it on the `WHEELHOUSE_DEMAND_INTEGRATION_KEY=` line.

**SANITY CHECK 2 — FILLED:**

```bash
grep -q '^WHEELHOUSE_API_KEY=.\+' "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.env" && echo "key present ✅" || echo "still empty — re-open the file and paste it"
```

**SANITY CHECK 3 — WORKS:**

```bash
set -a; . "<BUNDLE_ROOT>/mcp-servers/wheelhouse/.env"; set +a
curl -sS -H "X-Integration-Api-Key: $WHEELHOUSE_API_KEY" "https://api.usewheelhouse.com/ss_api/v1/listings" -o /dev/null -w "%{http_code}\n"
```

`200` = ✅. On failure, re-open the file and recheck the line, or regenerate the key — never a chat paste.

**Beyond (Partners API — Personal Access Token)**

Create and open the file:

```bash
cp "<BUNDLE_ROOT>/mcp-servers/beyond/.env.example" "<BUNDLE_ROOT>/mcp-servers/beyond/.env"
```

**SANITY CHECK 1 — SAFE:**

```bash
ls "<BUNDLE_ROOT>/mcp-servers/beyond/.env" && grep -q '^\.env$' "<BUNDLE_ROOT>/mcp-servers/beyond/.gitignore" && echo "safe to paste ✅" || echo "STOP — .env missing or not gitignored"
```

Open it (pick the OS line): `open -e "<BUNDLE_ROOT>/mcp-servers/beyond/.env"` (macOS) · `notepad "<BUNDLE_ROOT>\mcp-servers\beyond\.env"` (Windows) · `xdg-open "<BUNDLE_ROOT>/mcp-servers/beyond/.env"` (Linux). If it fails, give them the full path to open manually.

Then send:

> Good news — Beyond has a **self-serve** API for your own account; no partner application needed. You'll generate a **Personal Access Token**:
> 1. Log into Beyond → click your **sign-in icon** (top-right) → **Settings** → **Tokens** (or go straight to **v2.beyondpricing.com/dashboard/user/personal-access-tokens**).
> 2. **Generate** a new token — it starts with `bpat_` and is **shown only once**, so copy it right away.
> 3. In the `.env` I opened, paste it right after `BEYOND_API_TOKEN=` (no spaces, no quotes) and **save**.
>
> Paste it into the **file**, not the chat. Say "saved" and I'll verify.
>
> (This needs API access on your Beyond plan — the token screen tells you if it's enabled. If it isn't, PriceLabs is pre-built and ready as a fallback.)

**SANITY CHECK 2 — FILLED:**

```bash
grep -q '^BEYOND_API_TOKEN=.\+' "<BUNDLE_ROOT>/mcp-servers/beyond/.env" && echo "token present ✅" || echo "still empty — re-open the file and paste it"
```

**SANITY CHECK 3 — WORKS:** source the file and hit the Partners API listings endpoint — JSON:API needs the `vnd.api+json` Accept header:

```bash
set -a; . "<BUNDLE_ROOT>/mcp-servers/beyond/.env"; set +a
curl -sS -H "Authorization: Bearer $BEYOND_API_TOKEN" -H "Accept: application/vnd.api+json" \
  "https://developers.beyondpricing.com/api/v1/listings/?page[size]=5" -o /dev/null -w "%{http_code}\n"
```

`200` = ✅. On failure, re-open the file and recheck the line, or re-confirm the auth scheme from B1 — never a chat paste.

**Breezeway**

Create and open the file:

```bash
cp "<BUNDLE_ROOT>/mcp-servers/breezeway/.env.example" "<BUNDLE_ROOT>/mcp-servers/breezeway/.env"
```

**SANITY CHECK 1 — SAFE:**

```bash
ls "<BUNDLE_ROOT>/mcp-servers/breezeway/.env" && grep -q '^\.env$' "<BUNDLE_ROOT>/mcp-servers/breezeway/.gitignore" && echo "safe to paste ✅" || echo "STOP — .env missing or not gitignored"
```

Open it (pick the OS line): `open -e "<BUNDLE_ROOT>/mcp-servers/breezeway/.env"` (macOS) · `notepad "<BUNDLE_ROOT>\mcp-servers\breezeway\.env"` (Windows) · `xdg-open "<BUNDLE_ROOT>/mcp-servers/breezeway/.env"` (Linux). If it fails, give them the full path to open manually.

Then send:

> I opened your local `.env` (gitignored, on your machine). Let's get your Breezeway API credentials into it.
> 1. Open **https://developer.breezeway.io/docs/obtaining-credentials**.
> 2. Fill out the credentials request form (you need an active Breezeway account).
> 3. Breezeway emails you a **Client ID** and **Client Secret** (1–2 business days).
> 4. In the `.env` I opened, paste the Client ID after `BREEZEWAY_CLIENT_ID=` and the Client Secret after `BREEZEWAY_CLIENT_SECRET=` (no spaces, no quotes), then **save**.
>
> Paste both into the **file**, not the chat. Say "saved" and I'll verify.

**SANITY CHECK 2 — FILLED:**

```bash
grep -q '^BREEZEWAY_CLIENT_ID=.\+' "<BUNDLE_ROOT>/mcp-servers/breezeway/.env" \
  && grep -q '^BREEZEWAY_CLIENT_SECRET=.\+' "<BUNDLE_ROOT>/mcp-servers/breezeway/.env" \
  && echo "both creds present ✅" || echo "one is still empty — re-open the file and paste it"
```

**SANITY CHECK 3 — WORKS:** source the file, exchange for a token, then hit a read endpoint — all without the secret entering the chat:

```bash
set -a; . "<BUNDLE_ROOT>/mcp-servers/breezeway/.env"; set +a
TOKEN=$(curl -sS -X POST "https://api.breezeway.io/public/auth/v1/" \
  -H "Content-Type: application/json" \
  -d "{\"client_id\":\"$BREEZEWAY_CLIENT_ID\",\"client_secret\":\"$BREEZEWAY_CLIENT_SECRET\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
curl -sS -H "Authorization: JWT $TOKEN" "https://api.breezeway.io/public/inventory/v1/property" -o /dev/null -w "%{http_code}\n"
```

`200` = ✅. On failure, re-open the file and recheck the `BREEZEWAY_CLIENT_ID=` / `BREEZEWAY_CLIENT_SECRET=` lines — never a chat paste.

##### After building
1. The operator's `.env` is already created and filled per the credential walkthrough above (from the `.env.example` you generated) — the **operator pasted the values into the file**, you never typed them. If a value still needs to go in, re-run that tool's walkthrough (open the file, walk them to the key, paste-on-line, save, 3 sanity checks). Never echo a value in chat, never write one with the Edit/Write tool.
2. Install deps and confirm it builds:
   - **If the project uses `pyproject.toml` / uv** (the Turno-style Python pattern): `cd <folder> && uv sync`, and register later with `uv --directory <folder> run <console-script-name>`.
   - **If the project uses `requirements.txt`** (plain Python): `cd <folder> && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, and register later with the absolute venv python + entry file.
   - **If the project is Node/TS** (the PriceLabs-style pattern): `cd <folder> && npm install && npm run build`, and register later with `node <folder>/dist/index.js`.
3. Confirm folder structure (entry point, `src/`, `.env.example`, `.gitignore`, README, and for Node a built `dist/`).

#### B4 — FIRST-RUN DISCOVERY (do this once, persist it forever)

On the **first successful connect**, before declaring done, probe every reachable **READ/LIST** endpoint you wrapped (e.g. list properties, list reservations, get pricing recs, list tasks, list codes). **Only read/list endpoints during discovery — never fire a write/delete tool just to "see what it does."** Record what's **actually** available — the live endpoints that returned data **and the real response shapes/field names** — into:

**`<BUNDLE_ROOT>/revenue-manager-plugin/references/<tool-lowercase>-discovery.md`** (or append a "First-run discovery" section to `<BUNDLE_ROOT>/revenue-manager-plugin/references/<tool-lowercase>.md`).

Capture, per endpoint: the path that worked, the response envelope (e.g. `{ "data": [...] }` vs `{ "items": [...], "total" }`), pagination behavior observed, and the exact field names that hold the things you care about (price, date, status, listing name). This is the same trick the bundled Turno server uses — its field names were captured from live responses so nobody re-discovers them.

**Make this persistent and explicit:** future runs **READ this discovery file first** instead of re-hunting the API. State that at the top of the file: *"Read this before probing — built from live responses on `<date>`."*

#### B5 — Register, restart, verify

Register with **absolute paths**, matching how the server was built:

```bash
# Node/TS (PriceLabs-style):
claude mcp add <tool-lowercase> --scope user -- node "<BUNDLE_ROOT>/mcp-servers/<tool>/dist/index.js"

# Python with pyproject.toml / uv (Turno-style — use the console script name from pyproject):
claude mcp add <tool-lowercase> --scope user -- uv --directory "<BUNDLE_ROOT>/mcp-servers/<tool>" run <console-script-name>

# Python with requirements.txt + venv (absolute venv python + entry file):
claude mcp add <tool-lowercase> --scope user -- "<BUNDLE_ROOT>/mcp-servers/<tool>/.venv/bin/python" "<BUNDLE_ROOT>/mcp-servers/<tool>/server.py"
```

Pick the line that matches what you built. Then: **fully close and reopen Claude Code**, and verify the tools appear in a new chat with a **list/read** call (never a write call) — e.g. "list my <tool> listings/properties". Then go to **Step Done**.

---

## Step Done — Wrap-up message (both tracks)

Send verbatim (fill in the real path and tool):

> ✅ **Done!** Your **<TOOL>** MCP is installed and registered with Claude Code.
>
> - Pre-built (PriceLabs/Turno): it's in `<BUNDLE_ROOT>/mcp-servers/<tool>/`.
> - Built fresh: it's in `<BUNDLE_ROOT>/mcp-servers/<tool>/`.
>
> **One more piece — the database.** This pricing/ops MCP is just one part of the Revenue Manager. To actually *run* it, you also need the **Supabase audit database** set up (it logs every market snapshot, price change, and decision). Follow **SETUP.md → Step 3** to create the Supabase project and apply **BOTH** migrations in order (`001_revenue_tables.sql` then `002_outcome_columns.sql`). Without that, the MCP works but the Revenue Manager's logging has nowhere to write. (When that step needs your Supabase `service_role` key, it follows the same rule as every credential here: I'll open the `.env` for you to paste into — it never goes in the chat.)
>
> **Next steps:**
> 1. If you haven't already, **fully close and reopen Claude Code** (MCP won't load until a full restart — not a reload).
> 2. **Remember: I'm recommend-only.** I'll show you prices and proposed changes; I won't push anything live until you confirm that specific change.
> 3. Try these in a new chat:

Tailor the example prompts to the tool. Lead with **read-first** prompts; any change is framed as "show me / review before I push," and the actual write only happens after the operator confirms:

- **PriceLabs:** "Show me the underpriced listings for next month" · "What min price would you suggest for listing [ID], and why? (don't change it yet)" · "Check market occupancy for my area"
- **Wheelhouse:** "Get price recommendations for listing [ID] next 30 days" · "Show me what a $450/night custom rate on July 4 would look like before I set it" · "Show me the demand signal for [lat, lon]"
- **Beyond:** "Show me Beyond's recommended prices for all listings so I can review before pushing anything" · "Compare Beyond's recs to last month's actuals"
- **Turno:** "Show cleans scheduled for this weekend" · "Draft a cleaning project for property [ID] on April 20 and confirm with me before creating it" · "Who cleaned property [ID] last?"
- **Breezeway:** "List open tasks for property [ID]" · "Show me task costs for March" · "Draft an inspection task for tomorrow at [property] — confirm before creating"

> Your credentials live in the server's local `.env` (or `session.txt`) — you pasted them into that file yourself, it's gitignored, it stays on your machine, and it's never sent anywhere but that tool's own API. Nothing ever went through this chat.
> Anything breaks — re-paste this file and I'll fix it.

### Error recovery playbook
- **401/403:** the credential's wrong or expired. Re-open the local `.env` (`open -e`/`notepad`/`xdg-open`) and recheck the exact line, or regenerate the credential at the Step A3 / B-walkthrough URL and re-paste it into the file. Re-run SANITY CHECK 2 then 3. Verify plan tier / API access enablement. **Never** ask the operator to paste the key into the chat to "check it."
- **400:** a required param is missing — check the reference doc you wrote (`<BUNDLE_ROOT>/revenue-manager-plugin/references/<tool>.md`) or the live docs.
- **429 / 5xx:** exponential back-off is built in; if it persists, you're over the rate limit — slow down.
- **JWT expired (Breezeway):** the refresh flow should auto-renew; if it didn't, re-exchange with the client credentials (sourced from the `.env`, never the chat).
- **Turno: wrong creds / sandbox vs prod:** run `turno_check_connection`; re-open the `.env` and confirm `TURNO_API_TOKEN` holds the long JWT (`eyJ...`), not the short hex client key, and check `TURNO_ENV`.
- **Tool access not yet enabled** (Turno/Breezeway/Beyond): the operator must follow up with that vendor's support to turn on API/partner access.
- **MCP not showing in Claude Code:** Claude Code must be **fully closed and reopened**, not reloaded.
- **Built-from-research server can't find an endpoint:** re-read `<BUNDLE_ROOT>/revenue-manager-plugin/references/<tool>.md` and `<BUNDLE_ROOT>/revenue-manager-plugin/references/<tool>-discovery.md`; if it's genuinely new, re-research (B1) and update the reference doc.

---

## 🧠 Reference — Categories

- **Pricing tools** (PriceLabs, Wheelhouse, Beyond) = dynamic rate engines. They read your PMS calendar + booking data and push back recommended nightly prices. This is the **required slot** for the Revenue Manager — it's the recommendation source Claude cross-references against your real calendar. PriceLabs is pre-built in this bundle; Wheelhouse and Beyond get built fresh on your machine.
- **Cleaning/ops tools** (Turno, Breezeway) = scheduling + task management for turnovers, inspections, maintenance. **Optional.** MCP lets Claude create/query/manage those jobs from any chat. Turno is pre-built; Breezeway gets built fresh.

These are separate categories from the PMS itself — you usually run **one PMS + one pricing tool** (required) **+ optionally one ops/cleaning tool + one smart-access tool**, and they all talk to each other via APIs. Once each one has an MCP, Claude becomes the orchestrator across the whole stack — recommend-only, with you confirming every change that touches your live business.
