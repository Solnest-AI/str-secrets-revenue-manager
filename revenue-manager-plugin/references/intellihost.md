# IntelliHost: provider map (measured, not guessed)

Measured live 2026-09-24, read-only, on a Premium account: every one of the 33 read tools was
called on real properties. Structure only; no account data is recorded here.

## Connection and access

- MCP `https://clients.intellihost.co/api/mcp` (server "Intellihost" 0.7.0), HTTP, `Authorization:
  Bearer <token>`. The connections kit registers it as `intellihost`, token `INTELLIHOST_MCP_TOKEN`.
- **40 tools, returned in 3 pages** of `tools/list`. Read only page one and you see 15. Always
  follow `nextCursor`. No resources, no prompts (advertised, but empty).
- **Cloudflare blocks some clients:** Python urllib's default signature gets HTTP 403 "Error 1010".
  Send a normal `User-Agent`.
- **Rate limit 120 requests/minute** (`x-ratelimit-limit`). Pace property loops.
- **Premium is per property, not per account.** `tools/list`, `whoami` and the account-wide
  tools answer on a free account, but every per-property read returns `isError: true` with
  "An IntelliHost Premium subscription is required to read that property through the API."
  A tools-only check reports a free account as connected. On the card, that message is a named
  gap, never "no data". (Measured: 3 of the first 85 properties probed on a large account had it.)
- A Premium token has scope `mcp:read` and an `expires_at` about a year out.

## Mapping

- `list-properties-tool` `listing_id` is the **Airbnb room id** (39 of 50 measured). Match it to the
  PMS's Airbnb listing id, the same key RankBreeze uses. `pms` and `dynamic_pricing_provider` were
  empty on 41 of 50, and base/min/max on all 50: never map or detect a pricing tool from them.
- `list-properties-tool` takes `limit` (default 50) and `include_inactive`.

## Tools (reads 33, writes 7)

| Group | Tools | What came back |
|---|---|---|
| Account | whoami, list-properties | identity + token expiry; property list |
| **Ranking** | get-rank-series (days, guest_count) | per scrape date x guest count: rank, page. 88 rows / 90 days, 23 scrape dates, 4 guest segments on the measured property. get-rank-snapshot is DEPRECATED (returns a notice) |
| **Funnel** | get-funnel-dashboard (days, include_daily), get-booking-funnel, get-portfolio-funnel | impressions, first-page impressions, clicks, click rate, nights booked, click-to-book; each vs comp set, with per-step expected rate, index and deficit; 90 daily rows |
| **Comp market** | get-comp-market (days) | per date p1/p10/p25/median/p75/p90/p99 price, avg occupancy, avg lead time, comp count (60 dates); 30 comp ids; revenue rank percentile vs comps |
| Pricing engine | get-helix-predictions (days), get-pricing-recommendations (days), get-pricing-gaps | Helix: per night current vs proposed price, booking probability at both, expected revenue, push status. Recommendations: PriceLabs/Wheelhouse prices passed through with min-stay and demand. Empty unless a dynamic pricing provider is configured in IntelliHost |
| Live state | get-live-prices, get-active-overrides, get-sync-status, get-pricing-rules | channel price per night + source; overrides in effect; push eligibility; active rules by scope |
| Bookings | get-reservations, get-revenue-report (YoY), get-portfolio-revenue, get-booking-pace | reservation rows with revenue and status; period metrics vs prior year; pickup 7d/30d |
| Calendar | get-calendar | empty on all 3 measured properties: fills only when a PMS is connected inside IntelliHost |
| Listing | get-listing-details, get-optimization-audit, get-listing-optimization (regenerate=false), get-change-tracker, get-reviews | Airbnb listing facts; audit with $ attribution and ranked recommendations; AI title/description with scores; dated changes with funnel-rate deltas and estimated revenue impact; reviews with ratings |
| Portfolio | get-portfolio-health, get-action-items, get-market-data, get-forecast-budget, get-forecast-variance | rating/setup triage; alert feed; saved market studies; budgets (empty if none set) |
| Not working | get-revenue-indicator | "Helix forecasting job is not currently running", on every property |
| **Writes (never called)** | set-property-price-override, set-price-thresholds, set-auto-sync, upsert-pricing-rule, copy-pricing-rules, delete-pricing-rules, resolve-action-item, refresh-audit | change live pricing or state. **IntelliHost is read-only for the revenue manager: never call these.** Price changes go through the PMS or pricing tool, via `fetch/apply_change.py` |

Reservations and reviews carry guest names: never print, store or cache them.
