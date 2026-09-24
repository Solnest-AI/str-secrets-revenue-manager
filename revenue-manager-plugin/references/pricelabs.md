# PriceLabs — Reference & Discovery (revenue-manager skill)

The pricing engine AND the comp engine for v1. Tool prefix: `mcp__pricelabs__pricelabs_*`.

This file is the canonical reference the `revenue-manager` skill consults whenever PriceLabs is the detected pricing tool. It documents (1) every PriceLabs MCP tool — what it returns and the revenue-management use — and (2) the verified field-level facts the safety layer and the framework depend on. Read it before reasoning about any PriceLabs payload.

---

## TL;DR — the load-bearing facts

- **PriceLabs `get_neighborhood_data` IS the comp engine.** ~85-comp set, percentiles by bedroom (25/50/75/90), a 365-day forward ASK-price curve, market occupancy + same-time-last-year (STLY) + 7-day pickup, in native currency. There is no separate market-data tool needed for v1. AirDNA is removed. AirROI (optional) is only a qualitative, named-competitor layer on top.
- **The PriceLabs forward curve = ASK price (listed nightly), NOT cleared.** Confirmed: the PMS calendar nightly price == PriceLabs `recommended` price exactly, because PriceLabs pushes that number to the PMS.
- **GROUND TRUTH for "what's actually listed" = the PMS calendar** (e.g. Hospitable `get_property_calendar`), NOT PriceLabs `user_price`. The `user_price` field is **STALE** — verified divergent from the live calendar. Never quote `user_price` as PMS truth.
- **CLEARED / realized rate = ADR** (from `get_listing_prices` ADR field + `list_reservations`), and it runs **materially higher than ask**. Track BOTH ask (calendar / recommended) and cleared (ADR).
- **Markup is empirical, per property.** Compute the PMS÷PriceLabs ratio from paired dates; do not assume. For some properties it is 1.0 (no markup) — cleaning + channel fees are added at the channel, not on the nightly calendar number.
- **Floor / ceiling for the safety layer come from THIS tool** — `min` / `max` on `list_listings` / `get_listing` (and `get_neighborhood_data` min/max where present). Store them in `property_config` (`min_price` / `max_price`).
- **Writes are gated.** `set_overrides` (and `update_listings` / `delete_overrides`) push real changes — fire them **ONLY on explicit human approval, never silently.** v1 is recommend-only.
- **`get_neighborhood_data` returns LARGE payloads** and requires `(listing_id, pms)`. Parse it compactly (python3 into tables) before reporting; don't dump raw JSON into context.

---

## The 10 PriceLabs MCP tools

Each entry: what it returns, the verified fields, and the revenue-management use.

### 1. `pricelabs_list_listings` — portfolio roster + per-listing pricing & occupancy snapshot
**Returns:** every listing on the account with its current pricing config and a forward-occupancy snapshot.
**Verified fields per listing:**
- `min` / `base` / `max` — the price floor, anchor, and ceiling currently set in PriceLabs (your Min/Base/Max stack).
- `occupancy_next_7` / `occupancy_next_30` / `occupancy_next_60` — YOUR forward occupancy over those windows.
- `market_occupancy_next_7` / `market_occupancy_next_30` / `market_occupancy_next_60` — the market's forward occupancy over the same windows (your pacing vs. the field).
- `recommended_base_price` — what PriceLabs thinks your base SHOULD be (compare to `base` to spot a stale anchor).
- `last_refreshed_at` — when PriceLabs last recomputed this listing (the **freshness** signal — required by the safety layer).
- Plus identity: listing id, name, bedrooms, currency, `pms`.

**Revenue-management use:**
- **Step 0 / Step 3 starting point** — pull this first to get every listing's id, currency, and Min/Base/Max in one call.
- **Floor/ceiling seed (Safety Layer #1):** `min`/`max` are the default bounds → store as `property_config.min_price`/`max_price`.
- **Occupancy health (framework KPI):** your `occupancy_next_*` vs `market_occupancy_next_*`. If market occupancy is materially higher than yours → listing-quality / visibility problem, not pricing (check ranking FIRST — RankBreeze if present).
- **Base-price drift:** `base` vs `recommended_base_price` = the biggest single revenue lever (base price alignment).
- **Freshness gate (Safety Layer #7):** read `last_refreshed_at`; never present a rec on stale/unknown data without saying so.

### 2. `pricelabs_get_listing` — single-listing detail (same fields, one listing)
**Returns:** the same shape as one `list_listings` row, scoped to a single listing id — `min`/`base`/`max`, `occupancy_next_7/30/60`, `market_occupancy_next_7/30/60`, `recommended_base_price`, `last_refreshed_at`, currency, bedrooms.
**Revenue-management use:** deep-dive a single property; re-read bounds before a recommendation so the approval gate shows the live floor/ceiling; confirm `last_refreshed_at` right before presenting a change.

### 3. `pricelabs_get_listing_prices` — the per-date forward price curve (ask) + realized ADR
**Returns:** an array of per-date objects for the listing's forward calendar.
**Verified fields per date:**
- `recommended` (a.k.a. the PriceLabs recommended price) — **the ASK pushed to the PMS.** This equals the live PMS calendar nightly price. This is the number you reason about for "what's listed."
- `user_price` — **STALE. Do NOT trust as PMS truth.** Verified to diverge from the live calendar. Ignore for ground truth; use the PMS calendar instead.
- `uncustomized_price` — the price BEFORE your customizations/overrides (the raw algorithm output). Useful to see how much your overrides/min-max are bending the algo.
- `ADR` — the **realized / cleared** rate (what actually booked), runs **materially higher than ask**. This is the cleared side of the ask-vs-cleared pair.
- `booking_status` — booked vs available for that date (drives pacing, orphan-day, and gap detection).
- STLY fields — same-time-last-year price/occupancy comparators for pacing.
- `demand_desc` — PriceLabs' demand descriptor for the date (demand signal driving the rec).
- (also commonly present: `min_stay`.)

**Revenue-management use:**
- **Ask curve = `recommended`** → this is your forward calendar price; build the monthly price trajectory from it.
- **Cleared = `ADR`** → track ask vs cleared per property; cleared higher than ask is normal and healthy.
- **Markup normalization (empirical):** pair `recommended` against the PMS calendar price per date and compute the ratio. Often 1.0. Never "fix" a difference that matches the configured/empirical markup.
- **Pacing & red flags:** `booking_status` + STLY → 5+ consecutive unbooked within 14d, orphan days, booked-too-fast, pacing-behind-LY.
- **Orphan / last-minute logic:** scan `booking_status` for isolated open single nights and near-in gaps; apply the lead-time table.
- **Never read `user_price` for "what's live."** PMS calendar is ground truth.

### 4. `pricelabs_get_neighborhood_data` — THE COMP ENGINE (large payload)
**Requires:** `(listing_id, pms)`. **Returns LARGE payloads — parse compactly** (python3 → tables) before reporting. PMS-name mapping note: Hospitable's PMS name inside PriceLabs is `smartbnb`.
**Returns the comp set (~85 comps) in native currency, structured as:**

- **Summary Table Base Price** — the comp-set aggregate:
  - percentiles **25 / 50 / 75 / 90**
  - **median booked nightly / weekly / monthly** rates
  - **median LOS** (length of stay)
  - **median lead time**
  - `breakdown_by_br` — the same metrics split by bedroom count (use the same-bedroom slice for a true-comp read).
- **Future Percentile Prices** — the **per-date forward ASK curve** for the market (the comp set's listed prices over time, by percentile). This is the market analog of your own ask curve.
- **Future Occ / New / Canc** — market occupancy forward, with **current + STLY** (new bookings and cancellations too). Your pacing-vs-market and pacing-vs-last-year evidence.
- **Market KPI** (monthly): **booking window, LOS, revenue, booked / available days, future bookings, Bookings STLY, 7-day pickup.**

**Verified python access paths (from the engine):**
```python
data['data']['Summary Table Base Price']['Category']   # comp percentiles by bedroom, median booked/LOS/lead time
data['data']['Future Percentile Prices']['Category']   # forward market ASK curve by percentile
data['data']['Future Occ/New/Canc']['Category']        # market occupancy + STLY (new/cancellations)
data['data']['Market KPI']['Category']                 # monthly booking window, LOS, revenue, pickup, Bookings STLY
```

**Revenue-management use:**
- **Comp-set discipline (framework):** use `breakdown_by_br` (same bedroom ±1) as the true-comp slice; apply the "would a guest pick THAT instead?" test. PriceLabs neighborhood = the aggregate comp layer; AirROI (optional) = the named-competitor layer on top.
- **Pricing position:** where does your `recommended`/`base` sit vs the 25/50/75/90 percentiles by month → ADR-vs-comp-median KPI.
- **Pacing:** Future Occ/New/Canc + Market KPI `Bookings STLY` + `7-day pickup` → are you ahead/behind the market and behind last year.
- **Floor/ceiling cross-check (Safety Layer #1):** min/max context for the bounds lives here too.
- **Thin-comp transparency (Safety Layer #3):** read the comp count. If below ~20 (or the same-BR slice is thin), ALWAYS still produce a number, show N, and flag lower confidence in plain language. No hard refusal.
- **Currency (Safety Layer #4):** this payload is native-currency — anchor every figure to it. AirROI is called with `currency=native` so it normally returns the same currency; still verify AirROI's echoed `currency` field and never let a mismatched-currency figure enter a rec without conversion.

### 5. `pricelabs_get_rate_plans` — rate-plan / LOS-pricing configuration
**Returns:** the listing's configured rate plans (e.g. LOS-based / channel-based rate structures, discounts, multipliers).
**Revenue-management use:** see how length-of-stay and rate-plan rules shape the effective price; sanity-check weekly/monthly discounting against the comp median weekly/monthly rates from neighborhood data; spot rate-plan rules that conflict with min-stay strategy.

### 6. `pricelabs_list_overrides` — current date-specific overrides (DSOs)
**Returns:** all active date-specific overrides / custom rates currently on the listing (the manual layer on top of the algorithm).
**Revenue-management use:**
- Inventory existing DSOs before recommending new ones (don't double-stack, don't fight an old override).
- **Daily-review red flag:** "manual overrides that are no longer relevant" — surface stale DSOs for removal.
- Confirm seasonal / event / weekend overrides are actually in place vs. just intended.

### 7. `pricelabs_set_overrides` — push date-specific overrides (the write path)
**Returns:** confirmation of overrides written. **THIS IS A MUTATION.**
**Revenue-management use (gated):** the mechanism for date-specific overrides — seasonal DSOs, event boosts, weekend premiums, last-minute / orphan discounts.
- **Safety Layer #6 (Approval Gate):** fire ONLY on explicit human approval. NEVER a silent auto-write/push. The gate must first show: current price, recommended price, nearest bound, comp count, currency, and the reasoning.
- **Safety Layer #1 (Floor/Ceiling):** never push a value outside the listing's min/max without surfacing it and asking whether to change the BOUND.
- **Safety Layer #2 (Max-Delta):** a single change may not move price >25% from current by default; larger moves are FLAGGED "large move — confirm," never hidden.
- **Audit:** every pushed change writes one row per field/date to `pricelabs_change_log` and the decision to `pricing_decisions` (writes fire only on a real change).

### 8. `pricelabs_delete_overrides` — remove DSOs (destructive write)
**Returns:** confirmation of overrides removed. **Destructive — always confirm first.**
**Revenue-management use:** clear stale or superseded overrides (e.g. an old event boost on a date that already passed its event, or a manual rate no longer relevant). Same approval + audit discipline as `set_overrides`.

### 9. `pricelabs_list_reservations` — reservations PriceLabs has on file
**Returns:** the reservations PriceLabs holds for the listing(s) — check-in/check-out, booking status, revenue, nights, channel, guest.
**Verified shape (from the engine):** `listing_id`, `listing_name`, `check_in`, `check_out`, `booking_status`, `rental_revenue`, `no_of_days`, `booking_channel`, `guestName`.
**Revenue-management use:**
- **Cleared-rate corroboration:** `rental_revenue ÷ no_of_days` = realized nightly = a second read on ADR (cross-check the `get_listing_prices` ADR field).
- **LOS & lead-time distributions, channel mix** for the property.
- Note: the PMS reservations feed is the primary booking history; PriceLabs reservations are a useful corroboration/secondary source.

### 10. `pricelabs_update_listings` — update listing-level pricing config (the write path)
**Returns:** confirmation of the listing update. **THIS IS A MUTATION.**
**Revenue-management use (gated):** change the listing-level levers — **min / base / max** and other listing settings.
- This is how a **bound change** is executed when the operator approves moving a floor/ceiling (Safety Layer #1: operator can override bounds in plain English; persist the new bound to `property_config`).
- This is how a **base-price alignment** (the #1 lever) is pushed when `base` is drifting from `recommended_base_price`.
- Same approval gate + max-delta + audit discipline as `set_overrides`. Never silent.

---

## Verified field facts (the source of truth table)

| Concept | Where it lives | Trust |
|---|---|---|
| **Floor / ceiling (safety bounds)** | `list_listings`/`get_listing` `min`/`max` (+ neighborhood min/max) → `property_config.min_price`/`max_price` | Ground truth for bounds |
| **Your anchor rate** | `base` | Current setting |
| **What base SHOULD be** | `recommended_base_price` | Algo recommendation |
| **Your forward occupancy** | `occupancy_next_7/30/60` | Ground truth |
| **Market forward occupancy** | `market_occupancy_next_7/30/60` | Ground truth |
| **Freshness** | `last_refreshed_at` | Required for every rec |
| **ASK price per date (= live PMS calendar)** | `get_listing_prices.recommended` | Ground truth for "what's listed" (matches PMS) |
| **"What's actually listed" — definitive** | **PMS calendar** (e.g. Hospitable `get_property_calendar`) | THE ground truth; overrides PriceLabs on conflict |
| **STALE — do not trust** | `get_listing_prices.user_price` | ❌ Never quote as PMS truth |
| **Pre-customization price** | `get_listing_prices.uncustomized_price` | Raw algo output |
| **Cleared / realized rate** | `get_listing_prices.ADR` + `list_reservations` (`rental_revenue ÷ no_of_days`) | Ground truth; runs > ask |
| **Comp set (percentiles, medians, by-BR)** | `get_neighborhood_data` → Summary Table Base Price | The comp engine |
| **Market forward ASK curve** | `get_neighborhood_data` → Future Percentile Prices | Market analog of your ask curve |
| **Market pacing + STLY** | `get_neighborhood_data` → Future Occ/New/Canc, Market KPI (`Bookings STLY`, `7-day pickup`) | Pacing evidence |
| **Comp count (for confidence)** | count from neighborhood payload (use same-BR slice) | Drives thin-comp flag |
| **Native currency** | per-listing currency; neighborhood payload native | Hard gate on mixing |

### Markup — empirical, per property
- Compute `median( PMS_calendar_price ÷ recommended )` across paired forward dates.
- Often **1.0** (no nightly markup) — cleaning + channel fees are added at the channel, not on the calendar number.
- If the inferred markup is inconsistent across dates (high stdev) → flag misconfigured markup or broken sync.
- Persist to `property_config.settings.markup_pct`. **Never recommend a change to "fix" a difference that matches the configured/empirical markup** — that's the markup working.
- **Do NOT use `user_price` in any markup math** — it's stale. Pair `recommended` (PriceLabs) against the live PMS calendar.

---

## Ask vs cleared — the two-number discipline

Track BOTH for every property:
- **ASK** = `recommended` = the live PMS calendar nightly price (what you're listed at).
- **CLEARED** = `ADR` (`get_listing_prices.ADR` and `list_reservations` revenue ÷ nights) = what actually booked, **materially higher than ask.**

When you report ADR-vs-comp-median, be explicit which number you're using. Comp-set percentiles in neighborhood data include both listed and booked medians — match like with like (ask-to-ask, cleared-to-cleared).

---

## How PriceLabs maps onto the framework

- **Pricing Stack (Min → Base → Seasonal → Weekend +20-40% → Event +15-40% → Max; Last-Minute -10-20% within 7-14d; Orphan -15-25%; Far-out +5-15% for 90+d):**
  - Min / Base / Max → `update_listings` (`min`/`base`/`max`).
  - Seasonal / Weekend / Event / Last-Minute / Orphan → `set_overrides` (DSOs).
- **Lead-Time Pricing Logic** (90+ → +5-15% hold; 60-90 → at/slightly above base; 30-60 → at base, watch; 14-30 → small drops if needed; 7-14 → last-minute -10-20%; 0-7 → aggressive discount, drop minimums): drive off `get_listing_prices` `booking_status` + date distance.
- **Pricing Decision Framework (5 ordered questions):** (1) comp set → `get_neighborhood_data`; (2) pacing → Future Occ/New/Canc + Market KPI STLY + your `occupancy_next_*`; (3) events → operator/calendar knowledge + DSOs; (4) lead time → date distance on the price curve; (5) orphan days → `booking_status` scan.
- **30-Day Daily Review:** open the forward curve (`get_listing_prices`) → pacing vs market/LY (neighborhood) → recent bookings (`booking_status` + reservations) → comp set (neighborhood) → adjust (`set_overrides`/`update_listings`, on approval) → log (Supabase).
- **Red-Flags detection** (computed from PriceLabs + PMS): 5+ consecutive unbooked within 14d; date booked within hours (too low); orphan day open 7+ days; all weekends booked / weekdays empty; comp set fully booked while you're open (overpriced or visibility); comp set empty while you're booked (underpriced — hold longer next time).
- **KPIs & benchmarks:** ADR vs comp median (neighborhood percentiles); Occupancy 70-85% peak / 40-60% off (`occupancy_next_*`); RevPAR = ADR × occ; pace vs STLY (Market KPI `Bookings STLY`); Review 4.8+ / below 4.6 = ranking problem; Response <1hr. Page-view/CTR/ranking KPIs (500-600 avg vs 2000-3500+ top) map to **RankBreeze when present, else flag as a manual check** — PriceLabs does not expose ranking.
- **Visibility-before-pricing / Troubleshooting:** if `market_occupancy_next_*` >> your `occupancy_next_*` (zero forward bookings + strong market) → check ranking FIRST (RankBreeze if present, else manual), it's a listing-quality / visibility problem, not pricing.

---

## Operational notes

- **Auth / limits:** API base `https://api.pricelabs.co`, auth `X-API-Key`. Rate limits ~60/min, ~1,000/hr; allow up to a 300s timeout on `get_neighborhood_data` (it's the heavy call).
- **Parse heavy JSON compactly.** `get_listing_prices` (365 dates) and especially `get_neighborhood_data` (~85 comps × categories) → run through python3 into compact tables before putting anything in context. Never dump raw.
- **PMS-name mapping:** when a call needs `pms`, Hospitable = `smartbnb` inside PriceLabs.
- **Writes are append-only to the audit trail.** On any approved change: one `pricelabs_change_log` row per field/date, one `pricing_decisions` row per property-decision (now also seeding the nullable outcome columns `booked_at`, `lead_time_days`, `price_delta_from_rec` for the future learning loop — do not build the loop in v1), and a `market_snapshots` upsert. Writes fire ONLY on a real change.
- **Recommend-only.** v1 never auto-pushes. `set_overrides` / `update_listings` / `delete_overrides` run exclusively after the operator approves at the gate.

---

*PriceLabs is the tested primary pricing + comp engine for Revenue Manager v1. Wheelhouse / Beyond are detect-and-use pluggable alternatives; AirROI is an optional named-comp qualitative layer (returns native local currency via `currency=native` — verify it matches before mixing). AirDNA is not used.*
