# PMS, pricing-tool and enrichment field reference

Part of the revenue-manager skill. SKILL.md says when to read this file. Every rule here is
still binding. Deeper, measured references sit next to this file: `hospitable.md`,
`pricelabs.md`, `intellihost.md`, `ranking-rankbreeze-vs-intellihost.md`.

**Writes are not in this file on purpose.** Every price change, for every PMS and pricing
tool, goes through the safe writer (`fetch/apply_change.py plan`, then `apply` on a plain
yes, `rollback` to undo). Never call a PMS or pricing-tool write tool directly. If the
writer cannot reach a tool yet, give the operator the exact change to make by hand
(where, which field, old value, new value), never a raw MCP write. See SKILL.md Step 8.

## PMS field reference (platform-specific parsing)

| PMS | Bookings source | Calendar read |
|---|---|---|
| Hostaway | `reservations` | `/listings/{id}/calendar` |
| Guesty Pro | `reservations` | calendar endpoint |
| Hostfully | `leads` | calendar endpoint |
| Hospitable | transactions for history (see below) | `hospitable_get_property_calendar` |
| OwnerRez | `bookings` | `GET /v2/calendar` |
| Lodgify | `reservations/bookings` | calendar endpoint |
| Uplisting | reservations | calendar endpoint |
| Smoobu | reservations (apartments) | rates endpoint |

Hospitable, Guesty and OwnerRez have tested read adapters in the runner (`fetch/_pms_*.py`,
`fetch/_mvp_pms.py`). For the others, read through the connected tools directly.

### Hostaway
- Properties → `listings` · Bookings → `reservations`
- Reservation fields: `id`, `arrivalDate`, `departureDate`, `totalPrice`, `channelName`, `status`
- Calendar: `/listings/{id}/calendar` → `date`, `status`, `price`, `minimumStay`

### Guesty Pro
- Reservation fields: `_id`, `checkIn`, `checkOut`, `money.fareAccommodation`, `source`, `status`
- Calendar: `date`, `status`, `price`, `minNights`
- Guesty ignores the `listingId` query param on `/reservations`; only the `filters` JSON narrows
  it. Check every row belongs to the listing you asked about.
- Access tokens are capped at 5 per 24 hours: reuse the cached token, never loop logins.
- Guesty For Hosts is sunset (2026-01-15): route those operators to Guesty Pro.

### Hostfully
- Bookings called "leads" (Hostfully terminology)
- Requires `agencyUid` on every call

### Hospitable
- Calendar **read** (GROUND TRUTH for listed price): `hospitable_get_property_calendar` →
  `data.days[]` with `date`, `min_stay`, `status.reason` (`RESERVED`/`AVAILABLE`),
  `price.amount`. **`price.amount` is in cents: divide by 100.**
- **History:** `hospitable_list_reservations` returns ONLY upcoming/active reservations. Past
  and completed bookings are NOT there. Route all historical and cleared-rate pulls (booked
  nights by month, realized ADR by month, YoY/STLY, channel-mix history) to
  `hospitable_list_transactions` (and/or `pricelabs_list_reservations`). Other PMSs may expose
  full history via their reservations endpoint; this routing rule is Hospitable-specific.
- A by-hand change in the Hospitable dashboard is entered in dollars, not cents.
- PMS name inside PriceLabs is `smartbnb`
- Read tools: `hospitable_get_property_calendar`, `hospitable_list_reservations`
  (forward/active only), `hospitable_list_transactions` (history), `hospitable_list_reviews`

### OwnerRez
- Bookings → `bookings` (fields: `id`, `arrival`, `departure`, `total`, `channel`, `status`)
- Requires `User-Agent` header on every request
- An empty collection is `{limit, offset}` with no `items` key.
- `/reviews` ignores `property_ids`: fetch account-wide and filter locally.
- The Airbnb id lives only on the property detail (`listing_numbers.Airbnb`).

### Lodgify
- Bookings → `reservations/bookings` · fields: `id`, `arrival`, `departure`, `total_amount`, `source`, `status`

### Uplisting
- Auth: `Authorization: Basic <base64(api_key)>`

### Smoobu
- Properties called "apartments" · Auth header is `Api-Key` (exact case)

### No-rates mode
If a PMS returns NO nightly price or min-stay on any night, the runner switches to no-rates
mode: reconciliation checks bookings and availability only, and the card says it priced
without the PMS rate. A PMS that drops SOME prices is refused, not treated as no-rates.

## Pricing-tool field reference

### PriceLabs (primary: the comp engine + the recommendation source)
- Base URL: `https://api.pricelabs.co` · Auth: `X-API-Key`
- PMS name mapping for Hospitable: `smartbnb`
- Rate limits: 60/min, 1,000/hr. Timeout: 300s for neighborhood_data.

Key structures:
```python
# Neighborhood = the comp engine (~85 comps, percentiles by bedroom, native currency)
data['data']['Summary Table Base Price']['Category']   # Comp by bedroom count
data['data']['Future Occ/New/Canc']['Category']        # Market occ + STLY + 7-day pickup
data['data']['Future Percentile Prices']['Category']    # 25/50/75/90 percentile bands

# Per-date pricing (forward ASK curve = what PriceLabs pushes to the PMS)
listing['data']  # Array of date objects
#   date, price (ASK / recommended), uncustomized_price, min_stay, booking_status, ADR (CLEARED)
#   reason.listing_info: nhood_occ, minimum_price, maximum_price, base_price
#   reason.market_factors: seasonality, demand_factor
#   user_price ("user price (from PMS)"): freshness varies by listing. MEASURE it against the
#     live PMS calendar per property (Step 5); don't assume it's current. PMS calendar is ground truth.

# Reservations (CLEARED-rate inputs)
# listing_id, listing_name, check_in, check_out, booking_status,
# rental_revenue, no_of_days, booking_channel, guestName
```
Read tools: `pricelabs_list_listings`, `pricelabs_get_listing`, `pricelabs_get_listing_prices`,
`pricelabs_get_neighborhood_data`, `pricelabs_list_reservations`, `pricelabs_list_overrides`,
`pricelabs_get_rate_plans`. The write tools (`pricelabs_update_listings`,
`pricelabs_set_overrides`, `pricelabs_delete_overrides`) are what the safe writer uses; never
call them directly.

### Beyond (pricing tool, read and write)
- Beyond's official MCP (Neyoba) is read-only. The connections kit builds a `beyond` server on
  the operator's machine from Beyond's **Partners API** (`developers.beyondpricing.com`,
  JSON:API, self-serve Personal Access Token `bpat_…`) following the kit's
  `build/build-pricing-ops-mcp.md`. It exposes listings, the price + availability calendar,
  compsets, Beyond's recommendations, and per-listing customizations (base/min/max price,
  min/max stay, fees, time-based adjustments).
- Changes go through the safe writer like every other tool. Until the writer supports Beyond,
  give the operator the exact change to make in Beyond's dashboard.
- Often the PMS calendar already contains Beyond's pushed prices, so you can also read from the
  PMS side.

### Wheelhouse (optional, not in the summit connections kit)
- Base URL: `https://api.usewheelhouse.com/ss_api/v1/` · Auth: `X-User-API-Key`
- Uses "custom rates" instead of "DSOs". Demand Signal endpoint = richer market data (separate
  `IntegrationApiKey`). Changes: exact by-hand steps only.

## Optional enrichment reference (read-only; detect-and-use; never a critical path)

### RankBreeze (Visibility and Ranking spokes), hosted MCP `rankbreeze`
- Read-only, 15 tools: `lookup_current_user`, `get_user_listings`, `get_listing_rankings`,
  `get_listing_metrics_summary`, `get_listing_metrics`, `get_listing_content`,
  `get_listing_pricing`, `get_listing_reviews`, `get_listing_optimization`,
  `get_price_recommendations`, `get_competitors_pricing`, `get_competitors_content`,
  `get_competitor_reviews`, `get_ab_tests_listing_history`,
  `search_rankbreeze_knowledge_base`.
- **Mapping:** call `get_user_listings` FIRST to map each PMS property to its RankBreeze
  `listing_id` (by Airbnb room id, NOT the PMS property id), then loop the per-listing tools.
  No match for a property → manual ranking check for that one property; never block.
- Visibility = `get_listing_metrics_summary` (impressions, click-through, views, wishlists,
  booking rate, conversion, each vs similar listings). Ranking = `get_listing_rankings`.
- Ask for yesterday, not today: RankBreeze collects nightly and today is usually partial.
- Impressions, click-through, views, wishlists and booking rate all 0 means the listing is not
  connected to Airbnb Hosting inside RankBreeze ("not tracked", not "no activity"). Say so.

### IntelliHost (Visibility and Ranking spokes), MCP `intellihost`
- Read-only for this skill. It has write tools; never call them.
- Map by Airbnb room id (`list-properties-tool` `listing_id`), the same key RankBreeze uses.
- Visibility = `get-funnel-dashboard`; Ranking = `get-rank-series-tool` (a scrape every few days,
  so show its date). `get-rank-snapshot-tool` is deprecated.
- Premium is per property: a per-property read without it returns an error saying Premium is
  required. That is a named gap on the card, never "no data".
- Helix and the comp market are one more voice, never the basis.
- Full measured map: `intellihost.md`. Which one is better for what:
  `ranking-rankbreeze-vs-intellihost.md`.

### Turno / Breezeway (ops signals, read-only)
- **Turno:** `turno_list_projects` / `turno_list_bookings` for cleaning/turnover cost. Flag
  turnover cost as a revenue leak when too many 1-night stays. (Call `turno_check_connection`
  first.)
- **Breezeway:** task costs. Rising maintenance explains margin drop even with strong occupancy.
- Append as an "Operational signals" section at the end of the report.

### AirROI (named-competitor comps, native local currency, read-only), `mcp__airroi__*`
- MCP tools: `get_comparables` (≤25 named comps w/ TTM revenue/ADR/occ/ratings), `get_estimate`
  (revenue projection + percentiles + comps), `get_listing` (full listing detail),
  `get_listing_metrics` (monthly occ/ADR/rev/RevPAR), `health_check`. If `mcp__airroi__*` isn't
  connected, **skip silently**: it only enriches PriceLabs, never required.
- Use for the **qualitative** named-competitor comp layer ON TOP of PriceLabs' aggregate
  neighborhood data. **PriceLabs neighborhood data remains the quantitative comp engine.** If
  AirROI ever contradicts PriceLabs, NEVER override PriceLabs silently: surface the
  disagreement and explain it.
- **Native currency:** always call with **`currency=native`** (the connector default). Do NOT
  pass raw ISO codes like `cad`/`eur`: the API 400s on those. Read the `currency` field AirROI
  echoes and confirm it matches the operator's currency. On a genuine mismatch (a cross-border
  comp), convert first (named live FX source + timestamp, gate 2.4) or flag-and-exclude. Never
  silently mix currencies.
- Never hard-code a personal absolute path.
