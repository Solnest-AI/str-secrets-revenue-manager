# Revenue Manager — Claude Code Plugin

Your STR portfolio's revenue manager, running inside Claude Code. It reads your real PMS calendar and your PriceLabs comp data, cross-references them, and tells you exactly where you're leaving money on the table — with a hard safety layer so it never does anything dumb behind your back.

It **recommends, you approve.** Always. v1 never auto-writes a price to PriceLabs or your PMS. Every change is human-approved, every approved change gets logged to Supabase, and history compounds run over run.

---

## What it does

1. **Auto-detects your stack** — one PMS MCP + one pricing-tool MCP (PriceLabs primary; Wheelhouse or Beyond also qualify), both required, plus optional enrichment tools if you happen to have them connected.
2. **Pulls a full year forward + all the history it can reach** — your live PMS calendar, all reservations, reviews, transactions, plus PriceLabs listings, recommendations, and neighborhood comp data.
3. **Cross-references PMS reality vs PriceLabs** — markup-aware, using your real calendar as ground truth (not a stale field).
4. **Reads every prior decision from Supabase** — so it knows what you tried last time, what worked, and how this week compares to the same week last year.
5. **Recommends specific moves** — base, min, max, seasonal DSOs, min-stay, weekend premiums — each tied to real data signals.
6. **Logs the outcome** when you approve a change — never on a read-only run.

---

## Required core (the two things you actually need)

| Layer | Tool | Required? |
|---|---|---|
| **PMS** | Hostaway · Guesty (Pro / For Hosts) · Hostfully · Hospitable · OwnerRez · Lodgify · Uplisting · Smoobu | **Required** (one) |
| **Pricing** | **PriceLabs** (primary) · Wheelhouse · Beyond | **Required** (one) |
| **Audit** | Supabase (MCP or REST) | Strongly recommended — history is the feature |

PriceLabs is the tested, primary pricing engine. Its `get_neighborhood_data` IS the comp engine — roughly an 85-comp set, percentiles by bedroom (25/50/75/90), a 365-day forward ask-price curve, market occupancy, same-time-last-year, and 7-day pickup, all in your property's native currency. Wheelhouse and Beyond fill the same required pricing-tool slot in place of PriceLabs (detect-and-use) — PriceLabs is just the tested primary.

If either required piece is missing, the skill stops and tells you how to set it up. No PMS or no pricing tool = no run.

---

## Optional enrichment (detect-and-use — never required, never in the critical path)

These make the report richer when they're connected. If they're absent, the skill degrades gracefully and just notes it. None of them is ever a hard dependency.

| Tool | What it adds |
|---|---|
| **RankBreeze** (`mcp__rankbreeze__*`) | Ranking / visibility — the visibility spoke of the flywheel. When absent, ranking is flagged as a manual check. |
| **Ops MCPs** — Turno (`mcp__turno__*`), Breezeway | Turnover cost + operational signals (turnover-cost leak on too many 1-night stays, etc.) |
| **AirROI** | Named-competitor comps — a richer qualitative comp layer on top of PriceLabs' aggregate neighborhood data. Returns figures in **native local currency** by default (`currency=native` — CAD for Canadian markets, etc.), normally matching your PMS/PriceLabs; the currency gate verifies it and never silently mixes currencies or contradicts PriceLabs without explanation. Covers Canada + international markets. (Now a proper MCP, `mcp__airroi__*`.) |

---

## The safety layer (this is the whole point)

Every recommendation runs through a hardened safety layer. This is what makes it trustworthy enough to act on — every item is on by default in v1:

1. **Floor / ceiling per listing.** Defaults to each listing's existing PriceLabs min/max (read live), stored in `property_config`. It never silently recommends outside your floor or ceiling — if a rec wants to break a bound, it surfaces it and asks whether you want to change the *bound*. You can override bounds in plain English and it persists.
2. **Max-delta guardrail.** A single recommended change can't move a price more than **15%** from its current value by default (configurable per property). Bigger moves aren't hidden — they're flagged loudly on the card.
3. **Thin-comp transparency.** You always get a number, even when comps are thin. When the comp count is below ~20, it shows the count and flags lower confidence in plain language. No hard refusals — always estimate, always show N.
4. **Currency gate.** It auto-detects each property's native currency. A figure in another currency (e.g. an AirROI comp whose currency differs from the property's) can never enter a recommendation or approval without explicit conversion. On a mismatch it converts or flags — never silently mixes.
5. **Explanatory confidence.** No bare "LOW CONFIDENCE" badges. Every rec states its inputs in plain English — e.g. *"based on 23 comps (8 same-bedroom); market median $X; your forward occupancy Y%"* — so you can calibrate for yourself.
6. **Nothing changes without your yes.** There is **no** silent push to PriceLabs or your PMS; a plain yes in chat applies a change, then it is re-read to prove it took. The approval gate shows current price, recommended price, nearest bound, comp count, currency, and the reasoning, and flags anomalies before you say yes.
7. **Freshness.** It never presents a rec on stale or unknown data without saying so — it surfaces PriceLabs' `last_refreshed_at` and calendar recency.
8. **Audit.** Every approved change is written to Supabase. The `pricing_decisions` table now carries three nullable outcome columns (`booked_at`, `lead_time_days`, `price_delta_from_rec`) to seed a future learning loop. Writes still only fire on real changes.

---

## How the data engine works (the part most tools get wrong)

- **Ground truth for what's actually listed = your PMS calendar** (e.g. Hospitable `get_property_calendar`), **not** PriceLabs' `user_price` field — that field is stale and drifts from the live calendar.
- **The PriceLabs forward curve = ask price** (the nightly rate listed), not the cleared rate. The PMS calendar nightly price equals the PriceLabs recommended price (PriceLabs pushes to the PMS).
- **Cleared / realized rate = ADR** (from PriceLabs listing-prices ADR + reservations) and it runs materially higher than ask. The skill tracks **both** ask (calendar) and cleared (ADR).
- **Markup is computed empirically per property** — the PMS/PriceLabs ratio, measured, not assumed. For some properties it's 1.0 (no markup); cleaning and channel fees are added at the channel, not on the nightly calendar price.

---

## The framework baked in

The whole revenue-management discipline is wired into how the skill thinks, generalized off any one PMS onto your supported stack:

- **The Revenue Flywheel** — Visibility → Bookings → Reviews → Ranking → back to Visibility. Visibility comes before pricing. Ranking/visibility maps to RankBreeze when present, else flagged as a manual check.
- **The Pricing Stack** — Min → Base → Seasonal → Weekend (+20–40%) → Event (+15–40%) → Max, with Last-Minute (−10–20% inside 7–14 days), Orphan-day (−15–25%), and Far-out (+5–15% for 90+ days).
- **Lead-Time Pricing Logic** — how to treat the same open night at 90+, 60–90, 30–60, 14–30, 7–14, and 0–7 days out.
- **The Pricing Decision Framework** — five ordered questions: comp set → pacing → events → lead time → orphan days.
- **The 30-Day Daily Review** — the six-step daily habit, plus a Red-Flags auto-detection table (each flag computed from PMS + PriceLabs data) and a troubleshooting playbook (not-booking → check ranking FIRST; booked-too-fast; seasonal transition starts 30–45 days early; bad review).
- **Comp-Set discipline** — true-comp criteria, the "would a guest pick THAT instead?" test, and how to read comp data — mapped to PriceLabs neighborhood (aggregate) plus AirROI (named, optional).
- **KPIs & benchmarks** — ADR vs comp median; occupancy 70–85% peak / 40–60% off; RevPAR; pace vs same-time-last-year; page views (500–600 avg vs 2,000–3,500+ top); CTR; conversion; review 4.8+ (below 4.6 = a ranking problem); response under 1 hour.
- **Owner Communication** — an optional owner-report output following Lead with wins → Context → Honest → Plan.
- **Spreadsheet export** — on request at the end of a run, a multi-tab Excel workbook: a portfolio **Summary** tab plus **one tab per property** (full breakdown — current vs recommended base/min/max, comps, ask-vs-cleared, KPIs, red flags, reasoning, safety-layer footer). Saved to your Desktop; degrades to a folder of CSVs if `openpyxl` can't be installed. Report-only — nothing is pushed.

---

## What it logs (Supabase — 4 tables)

Every approved change writes an immutable audit trail:

| Table | Purpose |
|---|---|
| `market_snapshots` | Per-property daily snapshot of occupancy %, avg comp rate, demand score |
| `pricelabs_change_log` | Every individual field change pushed to PriceLabs (old → new + reason) |
| `pricing_decisions` | Per-property per-date decisions — base, final, strategy, signals, reasoning, outcome (+ `booked_at`, `lead_time_days`, `price_delta_from_rec`) |
| `property_config` | Per-property settings — markup %, min/base/max (floor/ceiling), max-delta, season months, targets |

Reads happen every run (for historical context). Writes happen **only when a change is made.**

---

## Prerequisites

- [Claude Code](https://claude.ai/code) installed
- **One PMS MCP** + **the PriceLabs MCP** installed and connected
- **One Supabase project** — free tier, no card. You do **not** need to create any tables yourself; the skill does it on first run (see below).
- Node.js 18+ for MCP servers

### Don't have MCPs yet?

Use the companion installer files to set them up (ask your community admin for the latest copies):

- **`build-pms-mcp.md`** — installs your PMS MCP
- **`build-pricing-ops-mcp.md`** — installs your PriceLabs / optional ops MCPs

---

## Installation

### Step 1 — Get a Supabase project (free, about 3 minutes)

Supabase is where your pricing history lives so it compounds run over run. The free tier is
plenty and it does not ask for a card.

**Start from nothing? Do exactly this:**

1. Go to **[supabase.com](https://supabase.com)** and click **Start your project**. Sign up with
   GitHub or email.
2. Click **New project**. Pick your org, give it a name (`revenue-manager` is fine), and choose a
   region close to you.
3. It generates a **database password**. Save it in your password manager. You will not need it
   for this plugin, but losing it is annoying later.
4. Wait about two minutes while it provisions. When the dashboard goes green, it's ready.

**You are done. Do not open the SQL Editor. Do not run any migrations by hand.**
The skill creates all four tables for you on its first run (see Step 3).

### Step 2 — Connect Supabase to Claude Code

Two options, either works, the skill auto-detects whichever you have.

**Option A (Recommended) — the Supabase MCP**

1. Create a personal access token: **[supabase.com/dashboard/account/tokens](https://supabase.com/dashboard/account/tokens)**
   → **Generate new token** → copy it.
2. Register the server (user scope):
   ```bash
   claude mcp add supabase --scope user -- npx -y @supabase/mcp-server-supabase@latest
   ```
3. Put the token in your MCP config as `SUPABASE_ACCESS_TOKEN`. **Never paste a token into the
   chat window.**
4. **Fully quit and reopen Claude Code.** MCP servers only load at startup.

> ⚠️ **Do not add `--read-only`.** A read-only server can read your history but cannot create the
> tables or write the audit trail, so the plugin silently drops to analysis-only. If you already
> registered it read-only, re-register it without that flag and restart.

**Option B — REST API fallback**

Add to your project's `.env`:
```env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOi...   # Supabase → Project Settings → API → service_role key
```
Use the **`service_role`** key, not the `anon` key. The `anon` key cannot create tables. The skill
calls Supabase REST directly with curl.

### Step 2.5 — The tables (automatic, nothing for you to do)

On its first run the skill checks your project, sees an empty database, and applies both
migrations itself:

| Table | What it holds |
|---|---|
| `property_config` | Per-property bounds, markup, targets, seasons |
| `pricing_decisions` | Every recommendation, with the reasoning behind it |
| `pricelabs_change_log` | Every approved change actually pushed |
| `market_snapshots` | Point-in-time comp and demand data |

It reports one line (`🗄️ Audit schema: created 4 tables (first run)`) and carries on. Both
migrations are idempotent, so this is safe on every later run too.

**Prefer to run them yourself?** You can. Open **SQL Editor → New query** and run
`migrations/001_revenue_tables.sql`, then `migrations/002_outcome_columns.sql`. The skill will
detect they already exist and skip ahead.

**No Supabase at all?** The plugin still runs. You get the full pricing analysis, you just lose
the compounding history, and it tells you so at the end.

### Step 3 — Install the plugin

**Local install:**
```
/plugin install /path/to/revenue-manager-plugin
```

**Manual install:**
Copy this entire folder to:
- Mac/Linux: `~/.claude/plugins/`
- Windows: `%USERPROFILE%\.claude\plugins\`

Then restart Claude Code.

### Step 4 — Configure each property (first run only)

Before analysis, tell the skill your markup per property so it can tell real price drift from intentional markup. The skill also reads each listing's existing PriceLabs min/max and stores them as your floor/ceiling. Just say:

> "Set up property config for all my listings. My PriceLabs markup is 16% (Airbnb 14% + 2% buffer)."

You can adjust bounds or the max-delta in plain English any time — it persists in `property_config`.

---

## Usage

```
> Check my pricing
> How are my properties doing?
> Am I underpriced?
> Set up pricing for the entire next year
> Compare my current prices vs last year's same-week
> Analyze my summer peak strategy
> Recommend min/base/max updates for the 2-bedrooms
```

---

## How it works

1. **Detection** — scans connected MCPs for your PMS, PriceLabs, Supabase, and any optional enrichment tools (RankBreeze, ops, AirROI, other pricing tools).
2. **Historical read** from Supabase — all prior decisions, change log, market snapshots, config.
3. **Parallel pull** — two agents spawn at once:
   - PMS agent: full-year forward calendar + all historical reservations + reviews + transactions
   - PriceLabs agent: listings, per-date recommendations, neighborhood comp set, overrides, freshness
4. **Markup normalization** — computes the PMS/PriceLabs ratio empirically per property; PMS calendar is ground truth; PriceLabs `user_price` is ignored.
5. **Pattern analysis** — seasonality, year-over-year, weekday/weekend spreads, lead-time distribution, LOS mix, comp-set percentile by month, ask vs cleared (ADR).
6. **Recommendation** — ranked levers, every one routed through the safety layer (floor/ceiling, max-delta, thin-comp flag, currency gate, explanatory confidence, freshness).
7. **Approval gate** — it presents current → recommended, nearest bound, comp count, currency, reasoning, anomalies. You approve. Nothing pushes on its own.
8. **Audit write** — on approval only: `pricelabs_change_log` + `pricing_decisions` + `market_snapshots`.

---

## Roadmap (v2 — not built yet)

- **Other-pricing-tool auto-setup + first-run discovery audit** — for operators on Wheelhouse / Beyond, auto-configure the tool and run a first-run discovery audit into a reference file.
- **Closed learning loop** — use the new `pricing_decisions` outcome columns (`booked_at`, `lead_time_days`, `price_delta_from_rec`) to learn which recommendations actually booked and feed that back into future recs.

---

## Security

- All API keys live in local `.env` files on your machine.
- Supabase service role key never leaves your environment.
- No telemetry. No data leaves your own infrastructure.
- Never commit `.env` files to git.

---

## License

MIT.

---

Want to actually learn this stuff — not just run it? This whole revenue-manager build came out of the Solnest AI community, where we teach operators to wire AI into their business themselves. If you're an STR operator who wants to stop setting PriceLabs and forgetting it, come hang out: [skool.com/solnest-ai](https://www.skool.com/solnest-ai)
