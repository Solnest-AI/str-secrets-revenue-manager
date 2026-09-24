# Hospitable — PMS Reference (revenue-manager skill)

> **Status: SHIPPED reference.** This file is hand-verified against the live Hospitable MCP tool surface and is the source of truth for how the revenue-manager skill reads/writes a Hospitable-connected operator. For any PMS *without* a shipped reference, the skill auto-discovers the tool surface on first run and writes its own reference file in this same shape — Hospitable, however, is pre-documented here; do **not** re-discover it.

**Tool prefix:** `mcp__hospitable__hospitable_*`
**Detection:** presence of any `mcp__hospitable__` tool → PMS = Hospitable.
**PriceLabs PMS-name mapping:** inside PriceLabs, a Hospitable account is named **`smartbnb`** (Hospitable's prior brand). When matching PriceLabs listings to Hospitable properties, the PriceLabs `pms`/integration field reads `smartbnb`, not `hospitable`.

---

## CRITICAL GROUND-TRUTH FACTS (verified — do not re-derive)

These govern every pricing read on a Hospitable stack. They override generic assumptions.

1. **Calendar price = the listed ASK nightly rate.** `get_property_calendar` returns the price that is *actually showing to guests* for that night. This is the ASK price (listed nightly), not the cleared/realized rate.
2. **The ASK price == the PriceLabs pushed price.** PriceLabs pushes its recommended nightly price to the PMS calendar. So the Hospitable calendar nightly value *is* the PriceLabs recommended price for that date. They are the same number by construction.
3. **`price.amount` is in CENTS — divide by 100.** A calendar night showing `price.amount = 28500` is **285.00** in the property's native currency. Never present cents as dollars.
4. **Currency is native** (the property's own currency, e.g. **CAD**). Read it from the property/calendar payload (`currency` field) and carry it through every figure. Never silently mix currencies (hard gate — see safety layer).
5. **The PMS calendar is GROUND TRUTH for "what's actually listed."** When the Hospitable calendar and PriceLabs disagree on the listed price, **trust the Hospitable calendar.** Specifically: PriceLabs `user_price` is STALE — it has been verified to diverge from the live calendar. Do **not** use `user_price` as "what's listed." Use `get_property_calendar`.
6. **Ask vs cleared are different numbers — track BOTH.**
   - **Ask** (listed nightly) = Hospitable `get_property_calendar` price = PriceLabs forward curve. What you're *asking*.
   - **Cleared/realized ADR** = computed from `list_reservations` (and/or PriceLabs listing-prices ADR field). What you actually *got*. Cleared runs materially **higher** than ask in healthy markets (low-ask dates sell first, premium dates clear at premium).
7. **Markup is EMPIRICAL, per property — do not assume.** Compute the PMS-calendar ÷ PriceLabs ratio from paired dates. For many Hospitable listings it is **1.0 (no markup)** because PriceLabs pushes the exact price and cleaning + channel fees are added *at the channel* (Airbnb/VRBO), not baked into the nightly calendar value. Never "correct" a difference that is just the configured markup; equally, never assume a markup exists.

---

## Tool surface — what each returns and the revenue need it serves

All tools are read-only except `update_property_calendar`, `create_reservation`, `update_reservation`, `respond_to_review`, and `send_message`. Under v1 RECOMMEND-ONLY posture, the skill **reads** freely and **never writes** to the calendar/reservations without an explicit human approval gate.

### Property structure

| Tool | Returns | Revenue-management need it serves |
|---|---|---|
| `hospitable_list_properties` | All properties on the account: IDs, names, bedrooms, city, **currency**, status. | **Step 0 / entry point.** Enumerate the portfolio, get per-property `currency` (drives the currency gate) and bedroom count (drives PriceLabs percentile-by-bedroom matching). |
| `hospitable_get_property` | One property in full detail: bedrooms, capacity, address/market, currency, listing metadata, channel connections. | Per-property profile for the comp match (bedroom band, market) and to confirm native currency before any figure is shown. |
| `hospitable_get_property_images` | The property's listing photos (URLs/order). | **Listing-quality / flywheel (visibility) check.** When a property has strong market occupancy but weak forward bookings → it's a listing problem, not pricing. Photo count/quality is a leading visibility input (framework: 50–70 photos, sunset shots, refresh every 2.5–3 yrs). Lightweight signal; RankBreeze is the real ranking spoke when present. |

### Forward calendar — the ASK engine

| Tool | Returns | Revenue-management need it serves |
|---|---|---|
| `hospitable_get_property_calendar` | `data.days[]` — one object per date: `date`, `min_stay`, `status` (with `status.reason` = `AVAILABLE` / `RESERVED`), and `price.amount` (**CENTS** → ÷100) in **native currency**. | **The core forward read.** This is GROUND TRUTH for the listed ASK nightly price and the listed min-stay. Pull the next **365 days** for: forward occupancy by rolling window (7/30/60/90d), the ask-price curve to lay against the PriceLabs forward curve, min-stay audit by season, orphan-day detection (single `AVAILABLE` night between two `RESERVED`), and the empirical markup ratio (calendar ÷ PriceLabs). **Recency:** note how fresh the calendar is and surface it (freshness gate). |
| `hospitable_update_property_calendar` | Writes nightly price / availability / min-stay for date(s). | **The ONLY price-mutation path on Hospitable.** v1 is recommend-only: the skill proposes a change, the **approval gate** shows current→recommended, nearest floor/ceiling, comp count, currency, reasoning — and only on explicit approval does it call this. Most pushes go through PriceLabs (which then syncs to this calendar); this tool is the direct-to-PMS override path for date-specific moves. Every successful write → Supabase audit row. |

### Historical demand — occupancy, ADR, LOS, lead-time, channel mix

| Tool | Returns | Revenue-management need it serves |
|---|---|---|
| `hospitable_list_reservations` | All reservations the account exposes (aim 2+ yrs back): check-in/out dates, nights, total/payout amounts, channel/source, status, guest. | **The cleared-rate + demand-history engine.** Computes: booked nights by month (this year / last year / two back) for **pace vs STLY**; **realized ADR** (the cleared rate — track against ask); **LOS distribution** (1/2/3/4+ nights); **lead-time distribution** (same-day, 1–7d, 8–30d, 30–90d, 90+d — feeds the Lead-Time Pricing table); **channel mix** (Airbnb/VRBO/direct — drives whether channel fees explain any calendar-vs-PriceLabs gap). |
| `hospitable_get_reservation` | One reservation in full: financial breakdown, fees, guest, dates, status. | Drill-down when a single booking looks anomalous (e.g., a date that "booked within hours" → was underpriced; framework red flag). Confirms fee structure feeding the empirical-markup question. |
| `hospitable_create_reservation` | Creates a reservation (block/owner-stay/manual booking). | Not a pricing read. Out of scope for v1 recommend-only pricing; never called by the analysis path. Listed for completeness. |
| `hospitable_update_reservation` | Modifies an existing reservation. | Same — not in the pricing critical path. Completeness only. |

### Money — payouts / realized revenue

| Tool | Returns | Revenue-management need it serves |
|---|---|---|
| `hospitable_list_transactions` | Financial transactions / payouts (≥ last 12 months): amounts, currency, type, associated reservation. | **Realized-revenue & payout truth.** Cross-checks reservation totals against actual money in (RevPAR sanity, owner-report net payout). Native currency — convert/flag if ever compared to a foreign-currency figure. |
| `hospitable_get_transaction` | One transaction in detail. | Line-item drill-down for an owner statement or to reconcile a specific payout. |

### Reviews — the flywheel's review/ranking signal

| Tool | Returns | Revenue-management need it serves |
|---|---|---|
| `hospitable_list_reviews` | Recent reviews (pull ~last 100): rating, text, date, response status. | **Flywheel review signal.** Average rating gates ranking health: **4.8+ target; below 4.6 = a ranking problem, not a pricing problem** (framework KPI). A fresh bad review + a forward-booking dip = diagnose the review/ranking spoke before touching price. |
| `hospitable_respond_to_review` | Posts a public response to a review. | The framework's bad-review playbook: respond professionally and briefly, fix the underlying issue, push for 2–3 strong reviews. A guest-experience action, not a price write — surfaced as a recommendation, executed only on approval. |

### Guest comms — response-time & inquiry/conversion signal

| Tool | Returns | Revenue-management need it serves |
|---|---|---|
| `hospitable_list_messages` | Messages for a reservation/thread. | **Response-time KPI** (target **under 1 hour**; a high-impact ranking factor) and conversion context — slow responses depress the booking-rate ranking input. |
| `hospitable_send_message` | Sends a guest message. | Ops action, not pricing. Completeness; never in the pricing path. |
| `hospitable_list_inquiries` | Pre-booking inquiries (demand that hasn't converted). | **Demand / conversion signal.** Lots of inquiries but few bookings → conversion problem (price too high, or listing content) rather than a visibility problem. Pairs with calendar occupancy to localize the flywheel break. |
| `hospitable_get_inquiry` | One inquiry in detail. | Drill-down on a specific lost/open inquiry (dates requested, quoted price). |
| `hospitable_get_quote` | A price quote for given dates/guests — the all-in guest-facing price incl. cleaning + channel fees. | **Ask-vs-all-in reconciliation.** The nightly calendar value is the bare nightly ask; the quote shows what the guest actually pays (nightly × nights + cleaning + channel fees). Use to explain why a calendar-vs-PriceLabs nightly can be 1.0 markup yet the guest still pays more — fees are added at the channel, not the nightly. |

### Account

| Tool | Returns | Revenue-management need it serves |
|---|---|---|
| `hospitable_get_user` | The connected Hospitable account/user: identity, default settings. | Confirms which account is connected and account-level defaults (e.g., default currency). Sanity check at detection. |

---

## How Hospitable plugs into the skill's pipeline

- **Step 0 (detection):** `mcp__hospitable__` present → PMS = Hospitable; map to PriceLabs as **`smartbnb`**.
- **Parallel PMS pull (Agent 1):** `list_properties` → per-property `get_property_calendar` (365d forward) + `list_reservations` (all history) + `list_reviews` (≤100) + `list_transactions` (≥12mo). Parse `data.days[]`, **÷100 on `price.amount`**, carry native `currency`.
- **Cross-reference vs PriceLabs:** lay the Hospitable ASK curve (calendar) against the PriceLabs forward curve (also ASK). They should match where PriceLabs pushes. **Calendar wins on any disagreement; ignore PriceLabs `user_price` (stale).**
- **Empirical markup:** `median(hospitable_calendar_price ÷ pricelabs_price)` over paired forward dates. Often **1.0**. Store per property in `property_config.settings`. If stdev is high, flag a sync/config issue.
- **Cleared vs ask:** ADR from `list_reservations` (cleared) tracked separately from the calendar (ask). Report both.

---

## Mandatory safety layer on a Hospitable stack (all REQUIRED in v1)

1. **Floor/ceiling** — default to the listing's existing PriceLabs min/max; store in `property_config` (`min_price`/`max_price`). Never silently recommend outside the bound; if a rec wants to breach it, surface it and ask whether to move the **bound**.
2. **Max-delta** — a single recommended move may not shift a calendar price more than **25%** from current (configurable). Larger moves are flagged "large move — confirm," never hidden.
3. **Thin-comp transparency** — always produce a number; when comp count < ~20, show the count and flag lower confidence in plain language. No hard refusal.
4. **Currency hard gate** — auto-detect the property's native currency (e.g. CAD) from `list_properties`/calendar. Never let a foreign-currency figure (e.g. an AirROI comp whose echoed currency differs from the property's) enter a recommendation without explicit conversion; on mismatch, convert or clearly flag.
5. **Explanatory confidence** — every rec states its inputs in plain language ("based on 23 comps (8 same-bedroom); market median $X; your forward occupancy Y%"), not a bare badge.
6. **Approval gate** — recommend-only. No silent write to `update_property_calendar` or PriceLabs. The gate shows current price, recommended price, nearest bound, comp count, currency, and reasoning; flags anomalies.
7. **Freshness** — surface PriceLabs `last_refreshed_at` and Hospitable calendar recency; never present a rec on stale/unknown data without saying so.
8. **Audit** — on a real change only, write the existing 4 Supabase tables; `pricing_decisions` now carries the 3 nullable outcome columns (`booked_at`, `lead_time_days`, `price_delta_from_rec`) for the future learning loop.

---

## Field cheat-sheet

```
list_properties[]            → id, name, bedrooms, city/market, currency, status
get_property_calendar.data.days[]:
    date                     → ISO date
    price.amount             → ASK nightly in CENTS  → ÷100
    price.currency / currency→ native (e.g. CAD)
    min_stay                 → listed minimum nights
    status.reason            → AVAILABLE | RESERVED
list_reservations[]          → check_in, check_out, nights, total/payout, channel/source, status, guest
list_transactions[]          → amount, currency, type, reservation_id
list_reviews[]               → rating, text, date, response
get_quote                    → all-in guest price (nightly × nights + cleaning + channel fees)
```

**Remember:** calendar `price.amount` is the ASK in **cents** and **equals** the PriceLabs pushed price; the calendar is ground truth; `user_price` in PriceLabs is stale; track ask (calendar) *and* cleared (ADR from reservations) separately; markup is empirical (often 1.0).
