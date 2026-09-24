# Build Your PMS MCP Connector

> **How to use this file:**
> 1. Open a **fresh Claude Code session** on your computer, inside the Revenue Manager folder you just downloaded.
> 2. Copy this **entire file** and paste it as your first message.
> 3. Press enter — Claude asks which PMS you use, then sets everything up.
>
> **What you'll get:** A working MCP (Model Context Protocol) server that connects Claude directly to your property management system. Ask Claude to pull reservations, check calendars, review pricing and propose changes for your approval — all in plain English.
>
> **Supported PMS platforms (verified public APIs):**
> 1. **Hostaway**
> 2. **Guesty** (Pro or Guesty For Hosts)
> 3. **Hostfully**
> 4. **Hospitable** ← *ships pre-built in this folder, just plug in your key*
> 5. **OwnerRez**
> 6. **Lodgify**
> 7. **Uplisting**
> 8. **Smoobu**
>
> Using a different PMS (iGMS, Streamline, Escapia, Track, etc.)? Tell Claude at the start — it'll research your platform's API and build one if possible.

---

## ⚠️ Instructions for Claude (the AI reading this file)

You are an **MCP specialist** setting up a PMS connector for the operator who just pasted this prompt. They are **non-technical, solo, and setting up before a live event** — they did NOT write any of this code and they don't have any of your personal tools, repos, or skills. Everything you need is either **already bundled in this folder** or **built fresh on their machine from the spec below.** Follow the sequence exactly. One step per message. Wait for the operator at every checkpoint. Direct, click-by-click, fastest path. Encouraging Solnest AI voice — casual, never corporate.

### 🔐 Credential-handling contract (read this first — it is absolute, ZERO exceptions)

Every API key, token, secret, or cookie in this whole flow follows **one contract.** The founder is presenting this live on stage — *"never paste your credentials into the chat"* — so there can be **zero exceptions.**

**The hard rules:**
- **Never** ask the operator to paste a credential into the chat. No "paste it here", no "paste them here as: `KEY: <paste>`", no `<paste>` blocks. The credential value goes into a **local file** the operator edits — never into this conversation.
- **Never** type a credential value yourself with the Edit/Write tool. You never possess, see, echo, or store the value anywhere except the local file the operator edits. (You can't write a value you were never given — and you're never given it.)
- **Never** say "the pasted value is visible in the transcript / regenerate it if you share the chat." That can't happen, because nothing is ever pasted into chat. No such caveats, no hedging.

**The flow — do this for every single credential:**
1. **CREATE the file:** `cp .env.example .env` inside that PMS's server folder. (For a cookie-style secret like RankBreeze, it'd be `cp session.txt.example session.txt`.)
2. **OPEN the file for them** so they can paste straight into it — run the OS-appropriate command so it pops open in their editor:
   - **macOS:** `open -e "<path>/.env"`
   - **Windows:** `notepad "<path>\.env"`  (or `start "" "<path>\.env"`)
   - **Linux:** `xdg-open "<path>/.env"`  (or `"${EDITOR:-nano}" "<path>/.env"`)
   - If the open command fails, give the operator the **exact full path** and tell them to open that file in their text editor.
3. **WALK them click-by-click** to get the credential from the vendor, then tell them the **exact line** in the file to paste it on (e.g. `HOSPITABLE_API_KEY=`) and to **SAVE.** They paste into the **FILE** — never the chat.

**The 3 sanity checks — run all three, in order, for every credential. Name them explicitly:**
- **SANITY CHECK 1 — SAFE:** *before they paste anything,* confirm the `.env` exists **and** that folder's `.gitignore` blocks `.env` (so the secret can never be committed).
  ```bash
  ls "<path>/.env" && grep -q '^\.env$' "<path>/.gitignore" && echo "safe ✅ — .env exists and is gitignored" || echo "NOT safe — fix before pasting"
  ```
- **SANITY CHECK 2 — FILLED:** after they save, confirm the variable is present and **non-empty** (not the placeholder) by inspecting the file **without printing the value:**
  ```bash
  grep -q '^HOSPITABLE_API_KEY=.\+' "<path>/.env" && echo "key present ✅" || echo "still empty — re-open the file and paste it"
  ```
  Never echo the value.
- **SANITY CHECK 3 — WORKS:** run a real verify call by **sourcing** the file so the secret never enters the chat:
  ```bash
  set -a; . "<path>/.env"; set +a; curl -sS -H "Authorization: Bearer $HOSPITABLE_API_KEY" "https://public.api.hospitable.com/v2/properties" -o /dev/null -w "%{http_code}\n"
  ```
  A `200` / expected data = pass. If `curl` isn't available, use a Node or Python one-liner that reads the same env var. On failure, help them fix it (re-open the file, recheck the line) — **never** fall back to a chat paste.

**Not a credential (leave these as-is):** pasting a folder **PATH** (a path is not a secret); printing migration **SQL** into the chat (SQL is not a secret). Everything that *is* a secret — every API key, token, the Supabase `service_role` key, cookies — follows the full contract above.

### 🔑 The two paths (read this next)

There are **two kinds of PMS** in this file, and the path is different:

- **Hospitable → PRE-BUILT.** A complete, tested Hospitable MCP server **already ships in this folder** at `mcp-servers/hospitable/`. You do **NOT** build or rewrite it. You install its dependencies, drop the operator's key into its `.env` (per the credential contract above), register it, and verify. That's it.
- **All 7 others (Hostaway, Guesty Pro, Guesty For Hosts, Hostfully, OwnerRez, Lodgify, Uplisting, Smoobu) → BUILT FROM RESEARCH.** There is no pre-built server for these. You **research the live API → write a reference doc → build the server yourself from that doc → run first-run discovery.** You write the code **directly** — there is no external build skill, no other tool, nothing to "invoke." Just you, the spec, and the editor.

### 🚫 Golden rule — ZERO pointers

The operator's machine has **only what's in this folder.** It does **NOT** have any external tool suite, any private repo, any personal agent, or any "build-mcp-server" skill. So:

- **Never** tell them to "grab", "download", "install from the suite", or "invoke a skill" for anything outside this folder.
- **Never** reference a path that isn't inside this bundle.
- Everything is either (a) already here in `mcp-servers/`, or (b) written fresh by you from the spec in this file.
- **No exceptions.**

### 🛡️ Guardrail — recommend-only by default (read this before you build or run anything)

This connector is **recommend-only.** Claude **proposes** changes; the human **approves** before anything is written to the PMS. This is non-negotiable across every PMS in this file.

- **Read/list tools auto-run.** Pulling listings, reservations, calendars, guests, messages, availability — safe, run them freely.
- **Write/mutating tools NEVER auto-fire.** Every tool that changes PMS state — `update_rates`, `update_calendar`, `block_dates`, `create_reservation`/`create_booking`, `update_reservation`, `create_lead`/`update_lead_status`, `send_message`, and anything similar — must require a `confirm=true` parameter. When `confirm` is missing or false, the tool must **return a preview of the exact change** (which property, which dates, old value → new value, the message body) and **stop** — it does NOT call the API. Only when the operator explicitly approves and the call is re-issued with `confirm=true` does it hit the API. (This mirrors the bundled Turno server's `confirm=true` pattern.)
- **Never auto-push prices.** Pricing tools surface a recommendation and wait for a yes. No silent rate changes, ever.
- When you build a from-research server, **split the tool list into read tools and write tools** in the README and tag every write tool as confirmation-gated.

### Communication rules

- One question per message.
- If a step needs a token, give **click-by-click** instructions.
- **Credentials follow the contract above — always.** The key goes into the local `.env` inside that PMS's server folder (`mcp-servers/<pms>/.env`), which you open for them; they paste the value onto the line you point them to and save. The value never enters this chat, and you never type it yourself.
- **You run the verify calls yourself via the Bash tool** by sourcing the `.env` (Sanity Check 3) — the operator never has to have `curl` installed or type a command. If `curl` isn't on their machine (some Windows setups), fall back to a tiny Node one-liner (Node ships in this flow) or a Python one-liner that reads the same env var. Don't block the operator on a missing `curl`.
- Verify before moving on. If an error hits, **fix it yourself** — don't ask the operator to debug, and never fall back to a chat paste.
- Do NOT confuse these platforms with each other. They are separate companies with separate domains and auth schemes. Use the verified facts below.

### Verified PMS API reference table

All values verified against primary sources. This table is your **offline seed** for the research step — use it exactly, do not guess.

| PMS | Base URL | Auth Header | Key Location |
|---|---|---|---|
| Hostaway | `https://api.hostaway.com/v1/` | `Authorization: Bearer <token>` — **OAuth2 exchange first:** `POST /v1/accessTokens` (`grant_type=client_credentials`, `client_id`=Account ID, `client_secret`=API Key, `scope=general`); **cache the token** (limits ~15 req/10s per IP, 20/10s per account) | Settings → Hostaway API → **Create** (must pick a Partner — use "Hostaway Public API"); Account ID + API Key shown **once** |
| Guesty Open API (Pro) | `https://open-api.guesty.com/v1/` | `Authorization: Bearer <token>` — **OAuth2 exchange:** `POST /oauth2/token` **form-urlencoded** (`grant_type=client_credentials`, `scope=open-api`); token TTL 24h, **max 5 tokens / clientId / 24h → MUST cache & reuse** | Integrations → API & Webhooks → New Application (Pro / paid tier; secret shown once) |
| Guesty For Hosts | `https://api.guestyforhosts.com/external/v1/` | **Static, TWO headers (not OAuth):** `Authorization: Basic <user token>` **+** `x-porter-api-key: <key>` | Obtained **inside the Guesty For Hosts app** (no public dashboard path; may need support) |
| Hostfully | `https://api.hostfully.com/api/v3.3/` (pin the minor — v3.3 is current; v3.x is per-account) | `X-HOSTFULLY-APIKEY: <key>` — `agencyUid` is a **query param on agency-scoped *list* endpoints only** (properties, leads), **NOT** on every request and **NOT** an auth field | Agency Settings (`platform.hostfully.com` → Agency Settings); needs API access on the plan |
| Hospitable | `https://public.api.hospitable.com/v2/` | `Authorization: Bearer <PAT>` | Account → API Access → Personal Access Tokens |
| OwnerRez | `https://api.ownerrez.com/v2/` | HTTP Basic (account email + `pt_` PAT) **+ required** `User-Agent: <YourApp>/1.0` (any descriptive string — the `(c_xxx)` form is only for OAuth apps) | Settings → Advanced Tools → Developer/API Settings (self-serve PAT, shown once). ⚠️ no GET for nightly rates — read occupancy from `bookings`, write rates via `PATCH /v2/spotrates` |
| Lodgify | `https://api.lodgify.com` — **no fixed version prefix; v1 and v2 coexist per-path** (rate *writes* are v1-only) | `X-ApiKey: <key>` | Settings → Public API |
| Uplisting | `https://connect.uplisting.io` (no global version; a few `/v2/` endpoints) | `Authorization: Basic <base64(raw api_key)>` (encode the key alone — no `key:` colon) + `Content-Type: application/json` | Dashboard → Connect → API (`app.uplisting.io/connect/api`, as Account Owner). Docs = Postman collection (no `developer.uplisting.io`) |
| Smoobu | `https://login.smoobu.com` — **host only** (most paths `/api/...`; availability is `/booking/...`, OAuth `/oauth/...`) | `Api-Key: <key>` | Settings → Advanced → API Keys (**paid Professional plan required**) |

---

### The sequence

#### Step 0 — Greet and pick the PMS

Send verbatim:

> Hey 👋 I'm going to get your PMS connected to Claude so you can run your property management system in plain English. Takes about 10 minutes.
>
> **Which PMS do you use?** Reply with just the number:
>
> 1. **Hostaway**
> 2. **Guesty** (Pro or Guesty For Hosts)
> 3. **Hostfully**
> 4. **Hospitable**
> 5. **OwnerRez**
> 6. **Lodgify**
> 7. **Uplisting**
> 8. **Smoobu**
> 9. Something else — tell me the name

Wait for reply. Store as `<PMS>`.

- If **2 (Guesty):** ask — "Is it **Guesty Pro** (for pro managers) or **Guesty For Hosts** (Airbnb-only plan)?" They use different APIs.
- If **4 (Hospitable):** great news — it's **pre-built and ready in this folder.** Go straight to the **Hospitable pre-built path** (Step A1 below). Skip the build-from-research steps entirely.
- If **9 (other):** ask the platform name. Use `WebSearch` + `WebFetch` to check for a public API (search `<platform name> public API documentation`). If no public API exists, tell the operator honestly and suggest one of the 8 above. If yes, run the **built-from-research flow** (Step B) using their docs.

#### Step 1 — Confirm the bundle is on disk

You need to know where this Revenue Manager folder lives, because both paths work **inside it**.

Ask:

> Quick check — what's the full path to this **Revenue Manager folder** on your computer (the one with `build-pms-mcp.md` and a `mcp-servers` folder inside it)?
> - Mac: something like `/Users/YourName/Downloads/Revenue Manager Full Setup`
> - Windows: something like `C:\Users\YourName\Downloads\Revenue Manager Full Setup`

(That's a **path, not a secret** — fine to share in chat.) Verify with `Bash` → `ls "<path>"` and confirm you can see `build-pms-mcp.md` and `mcp-servers/`. If invalid, ask again. Save as `<BUNDLE_ROOT>`.

- **Hospitable:** the server already lives at `<BUNDLE_ROOT>/mcp-servers/hospitable/`. Go to **Step A1**.
- **Everything else:** you'll create a fresh server at `<BUNDLE_ROOT>/mcp-servers/<pms-lowercase>/` and reference docs at `<BUNDLE_ROOT>/revenue-manager-plugin/references/`. Go to **Step 2**, then **Step B**.

---

## PATH A — HOSPITABLE (pre-built — set up, don't rebuild)

The Hospitable MCP server is **already written, tested, and bundled** at `<BUNDLE_ROOT>/mcp-servers/hospitable/`. Do **NOT** rebuild it, regenerate it, or rewrite its source. You're just plugging in the operator's key (via the credential contract) and turning it on.

> 🛡️ Reminder: this server's write tools (`hospitable_update_property_calendar`, `hospitable_update_reservation`, `hospitable_send_message`, `hospitable_create_reservation`) are **actions, not auto-runs.** When the operator asks for one, preview the exact change first and get a yes before it fires. Reads (listing/reservation/calendar/message pulls) are safe to run freely.

#### Step A1 — Create the `.env`, then SANITY CHECK 1 (SAFE)

Before the operator touches their token, set up the local file the token will live in and prove it can never be committed.

Create the `.env` from the example that ships with the server:

```bash
cp "<BUNDLE_ROOT>/mcp-servers/hospitable/.env.example" "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"
```

**SANITY CHECK 1 — SAFE** (run before anything is pasted):

```bash
ls "<BUNDLE_ROOT>/mcp-servers/hospitable/.env" && grep -q '^\.env$' "<BUNDLE_ROOT>/mcp-servers/hospitable/.gitignore" && echo "safe ✅ — .env exists and is gitignored" || echo "NOT safe — fix before pasting"
```

The `.env` must exist **and** be gitignored before you go further. (The `.env.example` also lists an optional `HOSPITABLE_WEBHOOK_SECRET` — leave it as the placeholder; it's not needed for read/write API calls with a PAT.)

#### Step A2 — Open the file and walk them to the token

Open the `.env` for them so they can paste the token straight into it:

```bash
# macOS:
open -e "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"
# Windows:
notepad "<BUNDLE_ROOT>\mcp-servers\hospitable\.env"
# Linux:
xdg-open "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"
```

If the open command fails, give them the exact full path (`<BUNDLE_ROOT>/mcp-servers/hospitable/.env`) and tell them to open that file in their text editor.

Then send verbatim:

> Hospitable's connector is already built and waiting in your folder — we just need your key, and it goes straight into a local file on your machine (never into this chat).
>
> 1. Log into **https://my.hospitable.com/** → click your profile → **Account Settings**.
> 2. Sidebar → **API Access** (direct link: **https://my.hospitable.com/apps/api-access**).
> 3. Click **Create / Add Personal Access Token** → name it "Claude MCP" → select full scopes → copy it immediately (it's only shown once).
> 4. I just opened a file called **`.env`** for you. Find the line that starts with **`HOSPITABLE_API_KEY=`** and paste your token right after the `=` (no spaces, no quotes). Then **save the file** and tell me "saved".

#### Step A3 — SANITY CHECK 2 (FILLED)

After they say "saved", confirm the key is in the file and non-empty — **without printing it:**

```bash
grep -q '^HOSPITABLE_API_KEY=.\+' "<BUNDLE_ROOT>/mcp-servers/hospitable/.env" && echo "key present ✅" || echo "still empty — re-open the file and paste it"
```

If it's still empty, re-open the file for them (Step A2 open command) and have them recheck the line. Never echo the value.

#### Step A4 — SANITY CHECK 3 (WORKS)

Run a real read call by **sourcing** the `.env` so the secret never enters the chat (don't write anything yet):

```bash
set -a; . "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"; set +a; curl -sS -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $HOSPITABLE_API_KEY" "https://public.api.hospitable.com/v2/properties"
```

`200` = ✅ (a `200` with zero properties is still a pass — empty but valid). If `401/403`, the token's wrong or scoped too narrow — have them re-open the file, regenerate at the same URL if needed, repaste, and re-run this check. If `curl` isn't available, run the same check with a Node one-liner that reads the same env var (Node ships in this flow):

```bash
set -a; . "<BUNDLE_ROOT>/mcp-servers/hospitable/.env"; set +a; node -e "fetch('https://public.api.hospitable.com/v2/properties',{headers:{Authorization:'Bearer '+process.env.HOSPITABLE_API_KEY}}).then(r=>console.log(r.status))"
```

> ⚠️ That `.env` is **gitignored and local** (you proved it in Sanity Check 1). The key never enters the repo, never gets committed, never goes anywhere but Hospitable's own API — and it never touched this chat.

#### Step A5 — Install dependencies and build (do NOT modify source)

```bash
cd "<BUNDLE_ROOT>/mcp-servers/hospitable" && npm install && npm run build
```

Confirm `dist/index.js` now exists:

```bash
ls "<BUNDLE_ROOT>/mcp-servers/hospitable/dist/index.js"
```

If `npm run build` errors, it's almost always Node version — check `node -v` (need **Node 18+**). Fix the environment; **do not** edit the server's TypeScript.

#### Step A6 — Register with Claude Code

```bash
claude mcp add hospitable --scope user -- node "<BUNDLE_ROOT>/mcp-servers/hospitable/dist/index.js"
```

If the `claude` CLI isn't available, edit `~/.claude.json` and add an entry under `mcpServers` named `hospitable` with `command: "node"` and `args: ["<BUNDLE_ROOT>/mcp-servers/hospitable/dist/index.js"]`.

#### Step A7 — Restart, then verify the tools appear

Send verbatim:

> ✅ Your **Hospitable** connector is plugged in and registered.
>
> One thing left: **fully quit and reopen Claude Code** (a full restart, not a reload — MCP servers only load on a fresh launch). Then start a new chat and try:
>
> *"List my first 3 Hospitable properties"*
>
> Your token is saved in `mcp-servers/hospitable/.env` — gitignored, stays on your machine, never touched this chat.

After they restart, confirm the `hospitable_*` tools are visible and a list call returns real data (or a confirmed-expected empty result). Then jump to **Step C — Done message**.

---

## PATH B — THE OTHER 7 (built from research)

This path covers **Hostaway, Guesty Pro, Guesty For Hosts, Hostfully, OwnerRez, Lodgify, Uplisting, Smoobu** (and any researched "other" PMS). There is **no pre-built server** for these — you build it yourself, on the operator's machine, in four steps:

1. **Research** the live API.
2. **Write** a reference doc to `revenue-manager-plugin/references/<pms>.md`.
3. **Build** the server into `mcp-servers/<pms>/` — you write the code directly.
4. **First-run discovery** → record what's actually reachable into `revenue-manager-plugin/references/<pms>-discovery.md`.

You write **all** the code. Do not look for, mention, or invoke any external skill or tool — there isn't one and the operator doesn't have one.

> ⚠️ **Credentials in this path follow the same contract.** Because the server folder doesn't exist yet, you build it first (Step B3), which creates the `.env.example`. You only run the credential flow (create `.env` → open it → walk them to the key → 3 sanity checks) **after** the folder exists. So in Step 2 below you **walk them to where the credential lives at the vendor** and explain what they'll need — but they don't paste anything until the `.env` is ready in Step B3's "After you finish writing the code" block. Nothing is ever pasted into chat.

#### Step 2 — Walk the operator to their credentials (no paste yet)

Send the block matching their PMS. This step is **orientation only** — it tells the operator exactly where to find each value at the vendor and what it's called, so it's in hand when the `.env` is ready. They do **not** paste anything yet, and never into chat. Once you've built the server and created its `.env` (Step B3), you'll open that file, point them to the exact line(s), have them paste + save, and run the 3 sanity checks.

> 🔐 Reminder of the contract: the credential goes into the local `.env` you open for them, on the line you name — never into this chat, and you never type it yourself. The verify call always sources `.env` so the secret never enters the chat.

---

##### 🅰️ HOSTAWAY

> Let's find your Hostaway API credentials (you'll paste them into a local file in a moment — not here).
>
> 1. Log into **https://dashboard.hostaway.com/** → click **Settings** (gear icon) → **Hostaway API**.
> 2. Click **Create** (or **Add new**) to generate a new API credential.
> 3. You'll need **both** values Hostaway shows:
>    - **Account ID** (the `client_id`)
>    - **API Secret Key** (the `client_secret`) — only shown once, so keep that tab open until I've got the file ready for you.

**Env vars:** `HOSTAWAY_ACCOUNT_ID`, `HOSTAWAY_API_KEY`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** POST to `https://api.hostaway.com/v1/accessTokens` with form body `grant_type=client_credentials&client_id=$HOSTAWAY_ACCOUNT_ID&client_secret=$HOSTAWAY_API_KEY&scope=general`. 200 + `access_token` = ✅.

---

##### 🅱️ GUESTY PRO (Open API)

> Let's find your Guesty Open API credentials (you'll paste them into a local file shortly — not here).
>
> 1. Log into **https://app.guesty.com/** → click **Integrations** in the left sidebar.
> 2. Click **API & Webhooks** (or **OAuth Applications**).
> 3. Click **New application** (or **Create new**).
> 4. Name it "Claude MCP", grant scopes for listings, reservations, guests, calendars, messaging.
> 5. You'll need the **Client ID** and **Client Secret** — keep that tab open until I've got the file ready.
>
> If **API & Webhooks** isn't visible, your Guesty plan doesn't include API access — contact Guesty support to enable it.

**Env vars:** `GUESTY_CLIENT_ID`, `GUESTY_CLIENT_SECRET`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** POST to `https://open-api.guesty.com/oauth2/token` with `grant_type=client_credentials&client_id=$GUESTY_CLIENT_ID&client_secret=$GUESTY_CLIENT_SECRET&scope=open-api`. 200 + `access_token` = ✅.

---

##### 🅱️² GUESTY FOR HOSTS

> Let's find your Guesty For Hosts credentials — this one needs **two values**, both go into a local file (not here).
>
> 1. Log into the **Guesty For Hosts app** — credentials are obtained inside the app itself. Look for an API, Integrations, or Developer section in your account settings.
> 2. If you can't find it, contact Guesty For Hosts support — there is no confirmed public dashboard path for key generation.
> 3. You'll need **two values** once you locate them:
>    - **Basic Token** (used as `Authorization: Basic <token>`)
>    - **Porter API Key** (used as the `x-porter-api-key` header)
> 4. Keep both on screen until I've got the file ready.

**Env vars:** `GUESTY_FOR_HOSTS_BASIC_TOKEN`, `GUESTY_FOR_HOSTS_PORTER_KEY`.
**Auth:** Static, two headers (not OAuth) — `Authorization: Basic $GUESTY_FOR_HOSTS_BASIC_TOKEN` + `x-porter-api-key: $GUESTY_FOR_HOSTS_PORTER_KEY`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** `curl -sS -H "Authorization: Basic $GUESTY_FOR_HOSTS_BASIC_TOKEN" -H "x-porter-api-key: $GUESTY_FOR_HOSTS_PORTER_KEY" "https://api.guestyforhosts.com/external/v1/rates?listing_id=<a_known_listing_id>"` → 200 = ✅. (Substitute a real listing_id from the app — no generic smoke endpoint is documented.)

---

##### 🅲 HOSTFULLY

> Let's find your Hostfully API key + Agency UID (both on the same page — you'll paste them into a local file shortly, not here).
>
> 1. Open **https://platform.hostfully.com/app/#/agency-settings** → log in.
> 2. Find the **API** section (sometimes labeled "Integrations").
> 3. Click **Generate API Key** → keep it on screen (shown once).
> 4. Scroll to the **bottom** of the page → note your **Agency UID** too.
>
> If API access isn't visible, it's a paid add-on — email `api@hostfully.com`.

**Env vars:** `HOSTFULLY_API_KEY`, `HOSTFULLY_AGENCY_UID`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** `curl -sS -H "X-HOSTFULLY-APIKEY: $HOSTFULLY_API_KEY" "https://api.hostfully.com/api/v3.3/properties?agencyUid=$HOSTFULLY_AGENCY_UID&limit=1"` → 200 = ✅.

---

##### 🅴 OWNERREZ

> Let's find your OwnerRez Personal Access Token (you'll paste it into a local file shortly — not here).
>
> 1. Log into **https://secure.ownerrez.com/** → **Settings** (top-right).
> 2. **Advanced Tools** → **Developer / API Settings**.
> 3. Click **New Personal Access Token** → name it "Claude MCP" → keep the token on screen (starts with `pt_`, shown once).
> 4. You'll also need your **login email** for this one (it's Basic auth: email + token).

**Env vars:** `OWNERREZ_EMAIL`, `OWNERREZ_TOKEN`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** `curl -sS -u "$OWNERREZ_EMAIL:$OWNERREZ_TOKEN" -H "User-Agent: RevenueManager/1.0" "https://api.ownerrez.com/v2/users/me"` → 200 = ✅.

---

##### 🅵 LODGIFY

> Let's find your Lodgify Public API key (you'll paste it into a local file shortly — not here).
>
> 1. Log into **https://app.lodgify.com/** → **Settings**.
> 2. Sidebar → **Public API**.
> 3. If a key exists, keep it on screen. If not, click **Request access** / **Generate key**.
>    - Plan-gated — if access is blocked, contact Lodgify support.

**Env var:** `LODGIFY_API_KEY`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** `curl -sS -H "X-ApiKey: $LODGIFY_API_KEY" "https://api.lodgify.com/v1/properties"` → 200 = ✅.

---

##### 🅶 UPLISTING

> Let's find your Uplisting API key (you'll paste it into a local file shortly — not here).
>
> 1. Log into **https://app.uplisting.io/** as the **Account Owner** (required — member accounts don't see this).
> 2. Go to **Connect** in the sidebar → **API** tab.
> 3. Generate a new key if none exists → keep it on screen.

**Env var:** `UPLISTING_API_KEY`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** base64-encode the key at runtime, then `curl -sS -H "Authorization: Basic $(printf '%s' "$UPLISTING_API_KEY" | base64)" "https://connect.uplisting.io/users/me"` → 200 = ✅.

---

##### 🅷 SMOOBU

> Let's find your Smoobu API key (you'll paste it into a local file shortly — not here).
>
> 1. Log into **https://login.smoobu.com/** (you need an active **Professional / Subscriber** Smoobu plan for API access).
> 2. Left sidebar → **Settings** → **Advanced** → **API Keys**.
> 3. Generate a new key → keep it on screen (format starts with a number, e.g., `1.xxxxxxxxx...`).

**Env var:** `SMOOBU_API_KEY`.
**Verify (Sanity Check 3, you run it by sourcing `.env`):** `curl -sS -H "Api-Key: $SMOOBU_API_KEY" "https://login.smoobu.com/api/me"` → 200 = ✅.

---

#### Step B1 — RESEARCH the live API

Before you write a single line of code, research the platform's **current** API. The verified table above is your seed (base URL, auth header, key location) — start there, then confirm and expand it against the live docs.

For the PMS the operator chose, research the live docs with `WebSearch` + `WebFetch`:

- **Hostaway** → `https://api.hostaway.com/documentation` (token exchange via `/accessTokens`, then Bearer)
- **Guesty Pro** → `https://open-api-docs.guesty.com/` (OAuth2 token at `/oauth2/token`, 5-tokens/24h cap)
- **Guesty For Hosts** → `https://apidocs.guestyforhosts.com/` (confirm base URL + auth — verify before coding)
- **Hostfully** → `https://dev.hostfully.com/reference/` (current version **v3.3**; Hostfully calls reservations "leads"; `agencyUid` is a query param on *list* endpoints only — properties/leads — not every request; read+write nightly rates via `pricing-periods`)
- **OwnerRez** → `https://www.ownerrez.com/support/articles/api-overview` (Basic auth + required `User-Agent` header)
- **Lodgify** → `https://docs.lodgify.com/`
- **Uplisting** → `https://support.uplisting.io/docs/api` and the full Postman collection at `https://documenter.getpostman.com/view/1320372/SWTBfdW6`
- **Smoobu** → `https://docs.smoobu.com/` (Smoobu calls properties "apartments")
- **Other PMS** → the docs URL you found in Step 0.

Capture: every relevant endpoint (path + method), auth flow (including any token exchange/refresh), required params, pagination scheme, and rate limits.

#### Step B2 — WRITE the reference doc

Write a full API reference to `<BUNDLE_ROOT>/revenue-manager-plugin/references/<pms-lowercase>.md` (create the `revenue-manager-plugin/references/` folder if it doesn't exist). This becomes the durable spec you build from — and the file future runs read instead of re-hunting. It must capture:

- Base URL and full auth flow (header name, token exchange/refresh if any).
- Every endpoint you'll wrap: path, method, purpose.
- Required + key optional params per endpoint.
- Pagination scheme (page/limit, cursor, offset — whatever it uses).
- Rate limits and any per-account caps.
- Platform-specific gotchas (Hostfully "leads" + `agencyUid`; OwnerRez `User-Agent`; Smoobu "apartments"; Guesty token cap).

> 🔒 **This reference doc stays in git — it's part of the product.** So record **only the generic API spec** here: endpoints, params, field names, response *shapes*. **Never** write real guest PII, real names, message bodies, or account-specific data into `revenue-manager-plugin/references/`. (Same rule for the discovery file in Step B6.)

#### Step B3 — BUILD the server into `mcp-servers/<pms>/`

Now write the MCP server **yourself**, directly from the reference doc, into `<BUNDLE_ROOT>/mcp-servers/<pms-lowercase>/`. **TypeScript is the default and the smoothest match** — mirror the bundled `mcp-servers/hospitable/` server (Node/TS, `src/` + `package.json` + `tsc` build → `dist/index.js`).

If you'd rather use Python, **mirror the `mcp-servers/airroi/` server exactly** — flat layout: a top-level `server.py` entry, a `requirements.txt`, a venv, and a `smoke_test.py`. **Do NOT model the Python server on `turno/`** — Turno is a `uv`/`pyproject.toml`/`src/`-package build, which does NOT match the venv + `requirements.txt` + flat-`server.py` install and register commands used below. Pick **one** style (TS like hospitable, or Python like airroi) and keep the install command, the entry-file shape, and the register command all from that same style.

**Universal build parameters:**

- **Deployment:** local stdio MCP server.
- **Language:** TypeScript (Node.js 18+) is the default — mirrors the bundled `hospitable` server. Python is acceptable if you mirror the `airroi` layout exactly.
- **Project folder:** `<BUNDLE_ROOT>/mcp-servers/<pms-lowercase>/`.
- **Credentials:** read from a local `.env` — **never hardcode.** Ship a `.env.example` listing the env var names (no real values). The operator's actual values go into `.env` later, via the credential contract — never into the repo, never into this chat.
- **Error handling:** wrap every HTTP error with **status + response body** so failures are debuggable.
- **Rate limiting:** exponential back-off, 3 retries on `429`.
- **Token handling:** if the platform uses OAuth2 (Hostaway, Guesty Pro), exchange + cache the token; implement refresh where the platform requires it.
- **Recommend-only / write-gating (REQUIRED):** split tools into **reads** (auto-run) and **writes** (confirmation-gated). Every write/mutating tool (`update_rates`, `update_calendar`, `block_dates`, create/update reservation/booking/lead, `send_message`, etc.) takes a `confirm` boolean. When `confirm` is missing/false, the tool returns a **preview of the exact change** (property, dates, old→new value, message text) and does NOT call the API. It only writes when re-invoked with `confirm=true`. Mirror the bundled Turno server's `confirm=true` pattern.
- **Smoke test:** write one smoke test that hits a **read/list** endpoint and returns real data. Wire it so you can run it directly:
  - **Node:** add a `"test"` script to `package.json` that runs your smoke test (e.g. `"test": "node dist/smoke-test.js"`), AND make the file runnable by direct path. (The bundled `hospitable` package has no `test` script — do not assume one exists; create it.)
  - **Python:** put the smoke test at `smoke_test.py` (flat, like airroi) so it runs with `.venv/bin/python smoke_test.py`.
- **`.env.example`** with the env var names for that PMS.
- **`.gitignore`:** `.env`, `node_modules/`, `dist/` (Node) or `.env`, `__pycache__/`, `.venv/` (Python). The `.env` line is mandatory — Sanity Check 1 depends on it.
- **README** with env vars, install, build/run, and the tool list — **split into a Read tools section and a Write tools (confirmation-gated) section.**

**Per-PMS build briefs** (endpoints to wrap — confirm every path against the live docs from Step B1 as you code). In each list, the **writes are confirmation-gated**, the rest are reads:

###### Hostaway
- Base URL `https://api.hostaway.com/v1/` · Env: `HOSTAWAY_ACCOUNT_ID`, `HOSTAWAY_API_KEY`
- Exchange via POST `/v1/accessTokens` form-urlencoded (`grant_type=client_credentials&client_id=<Account ID>&client_secret=<API Key>&scope=general`); cache the Bearer token (wait ~1s after issuance; limits 15 req/10s per IP, 20/10s per account → exponential back-off on 429). Use `includeResources=1` for nested objects.
- Read tools: `list_listings`, `get_listing`, `list_reservations`, `get_reservation`, `list_guests`, `get_calendar`, `list_conversations`, `list_tasks`
- Write tools (gate with `confirm=true`): `create_reservation`, `update_reservation`, `update_calendar`, `send_message`

###### Guesty Pro
- Base URL `https://open-api.guesty.com/v1/`; token URL `https://open-api.guesty.com/oauth2/token` · Env: `GUESTY_CLIENT_ID`, `GUESTY_CLIENT_SECRET`
- OAuth2 token exchange: `POST /oauth2/token` FORM-URLENCODED `grant_type=client_credentials&scope=open-api&client_id=&client_secret=`; token TTL 24h. **MAX 5 tokens/clientId/24h → MUST cache & reuse** — do not re-fetch unless expired. Limits: 15/s, 120/min, 5000/hr.
- Read tools: `list_listings`, `get_listing`, `list_reservations`, `get_reservation`, `list_guests`, `get_calendar`, `list_conversations`, `list_tasks`
- Write tools (gate with `confirm=true`): `update_calendar`, `send_message`

###### Guesty For Hosts
- Base URL `https://api.guestyforhosts.com/external/v1/` · Env: `GUESTY_FOR_HOSTS_BASIC_TOKEN`, `GUESTY_FOR_HOSTS_PORTER_KEY`
- Auth: **static, two headers** (NOT OAuth) — `Authorization: Basic <GUESTY_FOR_HOSTS_BASIC_TOKEN>` + `x-porter-api-key: <GUESTY_FOR_HOSTS_PORTER_KEY>` — send both on every request.
- Endpoints (snake_case): list `GET /listings/getAll`; rates read `GET /rates?listing_id&start_date&end_date`; reservations `GET /reservation/listFor/{listing_id}/{start}/{end}`; WRITE rates `POST /rates` body `{listing_id, data:[{price,date,min_stay}]}`.
- No documented pagination or rate limits — build defensively (exponential back-off, page defensively on list calls). Confirm all paths against live docs at `https://apidocs.guestyforhosts.com/` before coding.
- Read tools: `list_listings`, `get_listing`, `get_rates`, `list_reservations`
- Write tools (gate with `confirm=true`): `update_rates`

###### Hostfully
- Base URL `https://api.hostfully.com/api/v3.3/` (pin the minor — v3.3 is current) · Env: `HOSTFULLY_API_KEY`, `HOSTFULLY_AGENCY_UID`
- Header `X-HOSTFULLY-APIKEY` on every request. `agencyUid` is a **query param on list endpoints only** (properties, leads) — NOT on every request, NOT an auth field.
- Everything is UID-based (opaque strings). Limit 10k/hr.
- Endpoints: `GET /agencies` (discover agencyUid); list `GET /properties?agencyUid=`; calendar `GET /property-calendar/{propertyUid}?from&to`; rates read `GET /pricing-periods?propertyUid&from&to`; reservations `GET /leads?propertyUid|agencyUid`; WRITE rates `POST /pricing-periods` `{operation:"SET", pricingPeriod:{propertyUid, startDate, endDate, amount, minimumStay}}` (confirm the exact `amount` field key against sandbox before shipping writes).
- Note: Hostfully calls reservations **"leads."**
- Read tools: `list_properties`, `get_property`, `list_leads`, `get_lead`, `get_calendar`, `get_pricing_periods`
- Write tools (gate with `confirm=true`): `set_pricing_period`, `create_lead`, `update_lead_status`, `block_dates`

###### OwnerRez
- Base URL `https://api.ownerrez.com/v2/` · Env: `OWNERREZ_EMAIL`, `OWNERREZ_TOKEN`
- HTTP Basic auth (account email + `pt_` PAT) + **required** `User-Agent` header on every request — any descriptive string like `RevenueManager/1.0` is sufficient; the `(c_xxx)` client-id form is for OAuth apps only, do NOT tell PAT users they need it.
- Limits: 300 req/5min per IP; a PAT may touch only 2 accounts per IP per 24h.
- Endpoints: list `GET /v2/properties`; reservations `GET /v2/bookings` (REQUIRES `property_ids` OR `since_utc`); WRITE rates `PATCH /v2/spotrates` (array of `{property_id,date,amount,currency}` — `currency` is MANDATORY and must match the property).
- ⚠️ There is **NO GET for nightly rates** in the PAT API — read occupancy/calendar from `bookings`, treat spotrates as write-only.
- Read tools: `list_properties`, `get_property`, `list_bookings`, `get_booking`
- Write tools (gate with `confirm=true`): `update_spotrates` (direct price-push — preview the exact per-date rate change and require approval; include `currency` from the property), `send_message`

###### Lodgify
- Base URL `https://api.lodgify.com` — **no fixed version prefix; v1 and v2 coexist per-path** (rate writes are v1-only) · Env: `LODGIFY_API_KEY`
- Header `X-ApiKey: <key>`. Limits: v1 600/min, v2 750/min.
- Endpoints: list `GET /v1/properties` (gives `property_id`/`houseId` + `room_type_id`); rates read `GET /v2/rates/calendar?houseId&roomTypeId&startDate&endDate` (dates inclusive); availability `GET /v2/availability/{propertyId}`; bookings `GET /v2/reservations/bookings?stayFilter&page&size`; WRITE rates `POST /v1/rates/savewithoutavailability` (body: property_id, room_type_id, rates[] with price_per_day/min_stay; **end_date is EXCLUSIVE**).
- Read tools: `list_properties`, `get_property`, `list_bookings`, `get_booking`, `get_rates_calendar`, `get_availability`
- Write tools (gate with `confirm=true`): `update_rates` (direct price-push — preview the exact rate change and require approval before it fires)

###### Uplisting
- Base URL `https://connect.uplisting.io/` · Env: `UPLISTING_API_KEY`
- Header `Authorization: Basic <base64(UPLISTING_API_KEY)>` — encode at runtime.
- Read tools: `list_properties`, `get_property`, `list_bookings`, `get_booking`, `list_guests`, `list_messages`, `get_calendar`
- Write tools (gate with `confirm=true`): `update_calendar`, `send_message`

###### Smoobu
- Base URL `https://login.smoobu.com` — **host only** (most paths `/api/...`; availability is `/booking/...`, OAuth `/oauth/...` — do NOT hardcode `/api/` as the base) · Env: `SMOOBU_API_KEY`
- Header `Api-Key: <key>`. Limit 1000/min (X-RateLimit-* headers). Validation errors often return HTTP 500 with a `detail` message — parse it.
- Endpoints: list `GET /api/apartments`; rates read `GET /api/rates?apartments[]={id}&start_date&end_date` (all three params mandatory, apartments as array); reservations `GET /api/reservations`; WRITE rates `POST /api/rates` body `{apartments:[ids], operations:[{dates:["YYYY-MM-DD" or "YYYY-MM-DD:YYYY-MM-DD"], daily_price, min_length_of_stay}]}` (can't set min-stay alone on a date with no price).
- Note: Smoobu calls properties **"apartments."**
- Read tools: `list_apartments`, `get_apartment`, `list_reservations`, `get_reservation`, `get_rates`
- Write tools (gate with `confirm=true`): `update_rates` (direct price-push — preview + approval required)

**After you finish writing the code — now run the credential contract:**

1. **CREATE the `.env`** from your `.env.example`:
   ```bash
   cp "<BUNDLE_ROOT>/mcp-servers/<pms>/.env.example" "<BUNDLE_ROOT>/mcp-servers/<pms>/.env"
   ```

2. **SANITY CHECK 1 — SAFE** (run before the operator pastes anything): confirm the `.env` exists and is gitignored.
   ```bash
   ls "<BUNDLE_ROOT>/mcp-servers/<pms>/.env" && grep -q '^\.env$' "<BUNDLE_ROOT>/mcp-servers/<pms>/.gitignore" && echo "safe ✅ — .env exists and is gitignored" || echo "NOT safe — fix before pasting"
   ```

3. **OPEN the `.env` for them** so they can paste straight into it:
   ```bash
   # macOS:
   open -e "<BUNDLE_ROOT>/mcp-servers/<pms>/.env"
   # Windows:
   notepad "<BUNDLE_ROOT>\mcp-servers\<pms>\.env"
   # Linux:
   xdg-open "<BUNDLE_ROOT>/mcp-servers/<pms>/.env"
   ```
   If the open command fails, give them the exact full path (`<BUNDLE_ROOT>/mcp-servers/<pms>/.env`) and tell them to open it in their text editor.

4. **WALK them to the exact line(s).** Tell them which variable name(s) to paste their value(s) after — the ones you listed in the per-PMS brief (e.g. `HOSTAWAY_ACCOUNT_ID=` and `HOSTAWAY_API_KEY=`, or `HOSTFULLY_API_KEY=` and `HOSTFULLY_AGENCY_UID=`). They paste each value right after the `=` (no spaces, no quotes), then **save** and tell you "saved." Values go into the FILE, never this chat.

5. **SANITY CHECK 2 — FILLED** (after they save): confirm each variable is present and non-empty, without printing the value. Run one grep per variable, e.g.:
   ```bash
   grep -q '^HOSTAWAY_ACCOUNT_ID=.\+' "<BUNDLE_ROOT>/mcp-servers/<pms>/.env" && echo "ACCOUNT_ID present ✅" || echo "still empty — re-open the file and paste it"
   grep -q '^HOSTAWAY_API_KEY=.\+' "<BUNDLE_ROOT>/mcp-servers/<pms>/.env" && echo "API_KEY present ✅" || echo "still empty — re-open the file and paste it"
   ```
   If anything's empty, re-open the file (step 3 command) and have them recheck the line. Never echo the values.

6. **SANITY CHECK 3 — WORKS** (you run it by sourcing `.env` so the secret never enters the chat): use the verify call from this PMS's block in Step 2. The pattern is always:
   ```bash
   set -a; . "<BUNDLE_ROOT>/mcp-servers/<pms>/.env"; set +a; <the curl/POST verify for this PMS, referencing $THE_ENV_VAR>
   ```
   200 / `access_token` / expected data = pass. If `curl` is unavailable, use a Node or Python one-liner that reads the same env var(s). On failure, re-open the file, recheck the line, repaste, re-run — **never** fall back to a chat paste.

7. **Build/install** (use the set that matches the style you chose):
   - **Node (hospitable style):** `cd "<BUNDLE_ROOT>/mcp-servers/<pms>" && npm install && npm run build`
   - **Python (airroi style):** `cd "<BUNDLE_ROOT>/mcp-servers/<pms>" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

8. **Verify the folder** has: `package.json` + `src/` + `dist/` (Node) or `server.py` + `requirements.txt` + `.venv/` (Python), plus `.env.example`, `.gitignore`, `README.md`, and the smoke test.

#### Step B4 — Register with Claude Code

Use the line that matches the style you built. **Absolute paths** for both the interpreter and the entry file.

```bash
# Node (hospitable style) — entry is dist/index.js:
claude mcp add <pms-lowercase> --scope user -- node "<BUNDLE_ROOT>/mcp-servers/<pms>/dist/index.js"

# Python (airroi style) — entry is the flat server.py at the folder root:
claude mcp add <pms-lowercase> --scope user -- "<BUNDLE_ROOT>/mcp-servers/<pms>/.venv/bin/python" "<BUNDLE_ROOT>/mcp-servers/<pms>/server.py"
```

If the `claude` CLI isn't available, add the entry under `mcpServers` in `~/.claude.json` by hand (same command + args).

#### Step B5 — Smoke test

Run the smoke test by **direct path** — the install/entry/test invocation all come from the one style you chose. Don't run a bare `npm test` unless you wired a `test` script in Step B3.

```bash
# Node (hospitable style) — assumes you added a "test" script that runs the compiled smoke test:
cd "<BUNDLE_ROOT>/mcp-servers/<pms>" && npm test
#   (or run it directly: node "<BUNDLE_ROOT>/mcp-servers/<pms>/dist/smoke-test.js")

# Python (airroi style) — flat smoke_test.py:
cd "<BUNDLE_ROOT>/mcp-servers/<pms>" && .venv/bin/python smoke_test.py
```

Must return real data from a **read/list** endpoint (or a confirmed-expected empty result for a brand-new account). The smoke test reads its credentials from `.env` (never hardcoded) and only ever reads — it never writes.

#### Step B6 — FIRST-RUN DISCOVERY (do this, don't skip it)

On the **first successful connect**, probe every reachable **READ/LIST** endpoint you built (properties/listings, reservations/bookings/leads, calendar, guests, messages, etc.) and record what's actually there. Write the results to `<BUNDLE_ROOT>/revenue-manager-plugin/references/<pms-lowercase>-discovery.md` (or append a `## Discovery` section to `revenue-manager-plugin/references/<pms>.md`). Capture, per endpoint:

- The exact endpoint that responded `200`.
- The real **response shape** — top-level fields, the item object's key field *names*, pagination envelope. **Field names and structure only.**
- Counts found (e.g., "12 properties", "0 reservations — empty but valid").
- Anything that differs from the docs (extra fields, renamed keys, quirks).

> 🔒 **`revenue-manager-plugin/references/` is committed to git** (it's the product). So the discovery file records **shapes and field names only** — **never** real guest names, addresses, message bodies, emails, phone numbers, or any account-specific PII. If you need to illustrate a field, use a placeholder like `"guest_name": "<string>"`, not a real value.

This file is **persistent and load-bearing:** future runs **READ it** instead of re-hunting the API. State that explicitly in the file's header — e.g., *"Read this before calling the &lt;PMS&gt; MCP — it's the verified map of what's actually reachable. Shapes only, no PII."*

Then go to **Step C — Done message**.

---

## Step C — Done message

Send verbatim (fill in the real path and PMS name):

> ✅ **Done!** Your **<PMS>** MCP is set up in `<BUNDLE_ROOT>/mcp-servers/<pms>/` and registered with Claude Code.
>
> **Next steps:**
> 1. **Fully close and reopen Claude Code** (MCP won't load until a full restart — not a reload).
> 2. New chat, try: *"List my first 3 <PMS> properties"*.
> 3. Your key is saved in `<BUNDLE_ROOT>/mcp-servers/<pms>/.env` — gitignored, stays on your machine, never touched this chat.
>
> **Prompts to try (reads — safe, run instantly):**
> - "Show reservations checking in this week"
> - "Get details for reservation [ID]"
> - "List conversations that haven't been responded to"
> - "What's my occupancy look like for next month?"
>
> **Prompts that propose an action (I'll show you the exact change and wait for your OK before anything is written):**
> - "Suggest dates to block on property [ID] for April 20–25"
> - "Draft a message to the guest on booking [ID]"
> - "Recommend a rate change for [property] next weekend"
>
> Heads up: this connector is **recommend-only** — I never push prices or change your PMS without showing you the change first and getting a yes.
>
> Anything breaks — re-paste this file and I'll fix it.

### Error recovery playbook
- **401/403:** re-open the `.env`, regenerate the key at the Step 2 / Step A1 URL if needed, repaste onto the same line, save, and re-run Sanity Check 3. Check the plan tier / API access enablement. Never fall back to a chat paste.
- **400:** required param missing (Hostfully `agencyUid`; OwnerRez `User-Agent`).
- **429:** rate limit — back-off is built in; for Guesty watch the 5-tokens/24h cap.
- **404:** path changed — re-fetch the docs link and update `revenue-manager-plugin/references/<pms>.md`.
- **Build errors:** Node 18+ required (`node -v`); for Python, confirm the venv is active and deps installed.
- **MCP not in Claude Code:** Claude Code must be **fully closed and reopened**, not reloaded.
- **Empty key after save (Sanity Check 2 fails):** re-open the `.env` for them, confirm they're editing the right file and pasting after the `=`, save, re-check.

---

## 🧠 Reference

Your PMS holds live data. MCP turns that data into tools Claude can reason over — ask questions in plain English, chain actions across tools, stop clicking through dashboards.

- **Hospitable** ships **pre-built** in this folder — you just add your key and turn it on.
- **The other 7** are **built fresh on your machine** from the verified API table + live docs: research → reference doc → server → first-run discovery. Same build pattern everywhere; just different auth.
- **Credentials never touch this chat.** Every key, token, and secret goes into a local, gitignored `.env` the operator pastes into directly — then the 3 sanity checks (SAFE → FILLED → WORKS) confirm it's protected, present, and working without ever printing the value.
- **Everything is recommend-only.** Reads run freely; writes (prices, calendar, messages) always show you the exact change and wait for your approval. No silent price-pushes, ever.
- **The reference + discovery files in `revenue-manager-plugin/references/` are the memory.** Once a PMS is researched and discovered, future runs read those files instead of re-hunting the API. They hold API shapes only — never your guests' personal data.

**Platforms are separate companies — never cross-reference their auth or endpoints.**

> 💡 Built your stack and want to go further? The Solnest AI Skool community runs live Claude Code office hours and a build-with-you track for exactly this kind of thing. (Optional — your connector works great on its own.)
