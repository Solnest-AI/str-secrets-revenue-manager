# Solnest Revenue Manager — your STR pricing brain, in one folder

This is the full **Revenue Manager** bundle — an AI revenue manager that runs inside Claude Code, reads your real PMS calendar and your live pricing-tool data, cross-references them, and tells you exactly where you're leaving money on the table. It **recommends, you approve** — always. Nothing ever gets pushed to your pricing tool or your PMS behind your back.

You don't need to be technical. You open Claude Code, drag this folder in, and say **"set this up."** Claude walks you through the rest one step at a time — connecting your tools, wiring up your database, and installing the plugin. Most people are running their first pricing check in under 30 minutes.

Everything you need is **already in this folder.** There's nothing to go grab from somewhere else, no other tool to install first, no separate download. One bundle, start to finish.

> 📺 **[Watch the 5-minute walkthrough](https://www.loom.com/share/738aa85f18144db08004ed9c144df92a)** — the whole thing end to end. You don't need it to start — `SETUP.md` is fully self-contained and walks you through every step.

---

## What's in the box

Here's the whole bundle. You don't have to understand every piece — `SETUP.md` orchestrates all of it for you — but this is the lay of the land:

```
Revenue Manager Full Setup/
├── SETUP.md                    ← the conductor. The one file you run. START HERE.
├── README.md                   ← this page
├── build-pms-mcp.md            ← how Claude connects your property management system
├── build-pricing-ops-mcp.md    ← how Claude connects your pricing + ops tools
│
├── mcp-servers/                ← 5 ready-to-go connectors (source only, no keys baked in)
│   ├── hospitable/             PMS      — Node/TypeScript
│   ├── pricelabs/              Pricing  — Node/TypeScript
│   ├── turno/                  Ops      — Python
│   ├── rankbreeze/             Ranking  — Python
│   └── airroi/                 Comps    — Python
│
└── revenue-manager-plugin/     ← the actual Revenue Manager (the brain)
    ├── skills/revenue-manager/ the skill that runs the analysis
    ├── migrations/             001 + 002 — your Supabase audit tables
    └── references/             the framework + safety + API reference docs the skill reads
        ├── hospitable.md       (already shipped — verified Hospitable API reference)
        └── pricelabs.md        (already shipped — verified PriceLabs API reference)
```

When Claude builds a connector fresh for a tool that isn't in `mcp-servers/` (see below), it writes that tool's API reference into `revenue-manager-plugin/references/<tool>.md` too — same folder as the two that already ship. That's the one home for reference docs; nothing gets written to a separate top-level `references/` folder.

`SETUP.md` reads the other files when it needs them. You never open them by hand.

---

## The two kinds of connector

Your Revenue Manager talks to your tools through **connectors** (MCP servers). There are two flavors in this bundle, and the difference matters — including *where each one lives on your disk*:

### 1. Pre-built and bundled — just add your key

Five connectors ship **already written and tested** in `mcp-servers/`. If you use one of these tools, you don't build anything — you install the bundled version *in place* and paste in your own key:

| Tool | What it covers | Built in |
|---|---|---|
| **Hospitable** | PMS — your calendar + bookings | Node / TypeScript |
| **PriceLabs** | Pricing — comps + rate recommendations | Node / TypeScript |
| **Turno** | Ops — cleaning / turnover scheduling | Python |
| **RankBreeze** | Ranking — Airbnb search visibility | Python |
| **AirROI** | Comps — named-competitor market data | Python |

These are the green light. If your stack is Hospitable + PriceLabs (+ optionally Turno, RankBreeze, AirROI), setup is mostly "install, paste key, restart, verify." These five stay right here inside the bundle, in `mcp-servers/<tool>/`.

### 2. Built fresh on your machine — Claude writes it for you

If you're on a different tool, there's no bundled connector — so Claude **builds one fresh, right on your laptop**, writing the code in front of you from a verified spec that's already baked into the setup files. Nothing to download, and no extra plugin or skill required — Claude writes the server directly, using the bundled connectors in `mcp-servers/` as the working examples.

This covers:

- **Other PMSs:** Hostaway, Guesty (Pro + For Hosts), Hostfully, OwnerRez, Lodgify, Uplisting, Smoobu
- **Other pricing tools:** Wheelhouse, Beyond
- **Other ops tools:** Breezeway

Here's how that build works (Claude does all of it — you just answer the occasional question):

1. **Research.** Claude reads the tool's live API docs, seeded by a verified reference table (base URL, auth header, where your key goes) already embedded in the setup file. That table is the offline starting point, so the build works even if the docs are slow to load.
2. **Write the reference.** Claude saves a full API reference doc to `revenue-manager-plugin/references/<tool>.md` — endpoints, auth, params, pagination, rate limits — same folder as the Hospitable and PriceLabs references that already ship, so it's captured for good.
3. **Build the connector.** Claude writes a fresh MCP server **in TypeScript / Node** — directly from the embedded brief and the bundled examples in `mcp-servers/`, not by calling any outside tool. (Freshly built connectors are always TypeScript/Node, even though three of the five bundled ones happen to be Python; that's just the build template, and it changes nothing about how it runs.) It matches the bundled examples where it counts: reads your key from a local `.env`, wraps API errors with the real status + body, retries automatically on rate limits (429), ships a smoke test, and includes a `.gitignore` so your key never lands in git.
4. **First-run discovery.** On the first successful connect, Claude probes every readable endpoint, records what's actually there into `revenue-manager-plugin/references/<tool>-discovery.md`, and **reuses that file on every future run** instead of re-hunting. It gets smarter about your specific account the first time it runs.

**Where it lands (the "second folder" heads-up):** unlike the five bundled connectors, a built-fresh connector is **not** written inside `mcp-servers/`. Early in the build, Claude asks *"what's the main folder where you keep projects?"* — your Documents or projects folder is perfect — and creates a `<tool>-mcp-server/` folder *there*, separate from this bundle. That second-folder question is expected, not a bug. Claude then registers it with Claude Code by its full absolute path, so it doesn't matter where on your disk it sits. (Its API reference and discovery file still go to `revenue-manager-plugin/references/`, alongside the shipped ones.)

Same end result either way: a working connector, your key kept local, your data flowing into the Revenue Manager.

---

## The Revenue Manager plugin (the brain)

`revenue-manager-plugin/` is the actual product. Once your connectors are live, this is what does the thinking:

- **Auto-detects** whichever PMS and pricing connectors you installed and uses them.
- Pulls a **full year of forward calendar** plus your **complete booking history**.
- Treats the **live PMS calendar as ground truth** for what's actually listed, cross-references it against your pricing tool's recommendations, and figures out your real markup **empirically** — from your own numbers, not a guess.
- Tracks both your **ask rate** (what's on the calendar) and your **cleared rate** (ADR — what actually booked).
- Pulls in **RankBreeze, Turno/Breezeway, and AirROI** automatically when they're connected, for ranking, ops, and competitor context.
- Hands you a plain-English report of where you're underpriced, overpriced, and what to change.
- On request, exports a **multi-tab spreadsheet** — a portfolio summary tab plus one tab per property (full breakdown) — saved to your Desktop. Report-only, like everything else.

---

## The safety layer — why you can trust it

This is the part that lets you sleep. **Every recommendation runs through a built-in safety layer before it ever reaches you, and nothing is ever auto-written.** You approve every change, full stop.

- **Floor & ceiling guards** — your existing pricing-tool min/max become hard limits. Nothing is ever recommended below your floor or above your ceiling.
- **Max-delta cap** — no single recommendation can swing your price more than 25% in one move. No wild jumps.
- **Thin-comp transparency** — if the comp data behind a rec is thin, Claude says so out loud instead of pretending it's confident.
- **Currency gate** — it won't mix currencies and quietly hand you a nonsense number.
- **Explanatory confidence** — every rec comes with the *why* and a confidence level, not just a price.
- **Freshness checks** — stale data gets flagged, not silently used.
- **Human approval, always** — it recommends, you approve, then (and only then) it logs the change. There is no silent push to your PMS or your pricing tool. Ever.

That's the whole promise: recommend-only, you approve every change, nothing pushed silently.

---

## Supabase — your audit trail

The plugin logs **every decision and every approved change** to your own free Supabase database, so your pricing history compounds into something you can actually learn from over time.

Setup runs **two migrations** for you:

- **`migrations/001_revenue_tables.sql`** — creates the core audit tables (market snapshots, the change log, pricing decisions, and the supporting tables).
- **`migrations/002_outcome_columns.sql`** — additive and safe to re-run; adds the outcome columns (booked date, lead time, price delta from rec) that seed the future learning loop.

It's your database, in your account. The data lives with you, not in this repo.

---

## How to use it

1. **(Optional) [Watch the walkthrough Loom](https://www.loom.com/share/738aa85f18144db08004ed9c144df92a)** — a 5-minute overview. The setup is self-contained, so you can start without it.
2. **Put this folder somewhere easy to find** on your computer.
3. **Open a fresh Claude Code session** with this folder open (or paste the contents of `SETUP.md` as your first message).
4. **Say "set this up."**
5. Follow along. Claude asks one question at a time, gives click-by-click instructions for anything that needs a login, and checks each step worked before moving on. If something errors, Claude fixes it — you don't debug anything, and you never paste a key into the chat.

By the end you'll have your PMS connected, your pricing tool connected, a Supabase database logging every decision, and the plugin installed and tested with a real "check my pricing" run.

### What `SETUP.md` runs for you

- **Orient** — confirms Claude can see the folder and checks your prerequisites.
- **PMS connection** — gets you to your PMS API key, then either installs the bundled Hospitable connector or builds yours fresh, and smoke-tests it on your real data.
- **Pricing + ops connection** — same flow for PriceLabs (bundled) or Wheelhouse / Beyond (built fresh), plus optional Turno, RankBreeze, and AirROI.
- **Supabase** — sets up your free audit database and runs both migrations.
- **Install + first run** — installs the plugin, reads your existing pricing-tool min/max as your floor/ceiling, confirms your markup, and runs your first recommend-only pricing report.

> **Heads-up — you'll be asked about two folders.** This bundle folder is one. When Claude builds a connector fresh (any tool that isn't in `mcp-servers/`), it asks for a *second* folder — your normal Documents or projects folder — and installs the little server there. That's expected. The five bundled connectors don't trigger that question; they install right here in place.

### Installing a pre-built connector (the short version)

For any of the five bundled tools, the flow is the same — and Claude does it with you:

1. `cd mcp-servers/<tool>`
2. Build it:
   - **Node** tools (`hospitable`, `pricelabs`): `npm install` then `npm run build`.
   - **Python — Turno:** `uv sync` (Turno uses `uv`).
   - **Python — RankBreeze and AirROI:** `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
   - (The `uv` path and the `venv` + `pip` path are not interchangeable — use the one listed for that tool.)
3. `cp .env.example .env` and paste **your own key** into the `.env`. (RankBreeze is the one exception — it has no API key. Copy `session.txt.example` to `session.txt` and paste your `_godzilla_session` cookie value in there, or set `RANKBREEZE_SESSION` in the `.env` instead. RankBreeze ships `session.txt.example`, not a ready-made `session.txt`, so you create `session.txt` by copying it first.)
4. Register the connector with Claude Code: `claude mcp add <tool> --scope user -- …`, then **fully restart Claude Code**. The path you pass to `claude mcp add` must be the **absolute path** to wherever the connector folder actually sits on your disk — if you ran it in place, that's this bundle folder. (This is load-bearing: get the path wrong and the connector won't register.)
5. Verify the tools show up.

Your keys live only in your local `.env` / `session.txt`, which are gitignored. They are never committed, never pasted into the repo, never sent anywhere except that tool's own API.

---

## Prerequisites

Have these ready before you start (Claude checks them, but it's faster if they're handled):

- **[Claude Code](https://claude.ai/code)** installed.
- **Node.js + Python — you don't pre-install these.** The setup **installs them for you automatically** (Mac or Windows) if they're missing: **Node 18+** for the core connectors, and **Python 3.11+** plus `uv` only if you add a Python-based add-on like Turno.
- A login for your **PMS** (Hospitable, Hostaway, Guesty, Hostfully, OwnerRez, Lodgify, Uplisting, or Smoobu).
- A login for your **pricing tool** (PriceLabs, Wheelhouse, or Beyond).
- A **free Supabase account** — sign up at [supabase.com](https://supabase.com), no card needed for the free tier.
- **AirROI** is optional and free — if you want the named-competitor comp layer, grab a free developer key at [airroi.com/api/developer/activate](https://www.airroi.com/api/developer/activate) (note: AirROI returns each market's native local currency — e.g. CAD for Canadian markets — and covers Canada + international markets, not just the US).

You supply your own keys, locally, during setup. See the note below.

---

## This is a public repo — and there are zero secrets in it

Read this part. It matters.

- **Nothing in this repo is a secret.** It's setup markdown, five connector source folders, the plugin skill, the database migrations, and reference docs. That's it.
- **Every operator supplies their own keys, on their own machine.** Your PMS key, your pricing-tool key, your Supabase key, your RankBreeze cookie — they go into your local `.env` / `session.txt` during setup. They **never** touch this repo, never get committed, never get shared.
- The `.gitignore` keeps `.env` files, `session.txt`, and the like out of git automatically. Claude will never ask you to paste a key into the chat — keys only ever go into your local config, and only ever travel to that tool's own API.

If you ever fork or share this: same rule. No keys, ever. **Before you push or share, run `git status` and confirm no `.env` or `session.txt` is staged** — that's the one-second check that keeps the gitignore honest.

---

## Keeping it updated

This bundle improves over time. To grab the latest:

```bash
git pull
```

(Or re-download the folder and replace your copy.) Your local keys and your Supabase data stay where they are — they live on your machine, not in the repo.

---

## Want to actually learn this, not just run it?

This whole thing came out of the **Solnest AI** community, where we teach business owners — STR operators especially — to wire AI into their business themselves instead of paying someone forever. If you want to go deeper, ask questions live, and stop setting your pricing tool and forgetting it, come hang out: **[skool.com/solnest-ai](https://www.skool.com/solnest-ai)**

---

*Built by Solnest AI. Recommend-only, you approve every change, nothing pushed silently — that's the whole promise.*
