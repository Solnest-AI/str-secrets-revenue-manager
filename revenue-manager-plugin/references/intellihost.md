# IntelliHost: the second ranking/visibility source (measured, not guessed)

Measured live on 2026-09-24 against a Premium account, read-only. Structure only; no account
data is recorded here.

## Connection

- MCP endpoint `https://clients.intellihost.co/api/mcp`, HTTP transport, `Authorization: Bearer <token>`.
  The connections kit registers it as `intellihost` and stores the token as `INTELLIHOST_MCP_TOKEN`.
- **Cloudflare blocks some clients.** A request with Python urllib's default signature gets
  HTTP 403 "Error 1010: Access denied". Send a normal `User-Agent` (curl and Claude Code pass).
- **Every data read needs IntelliHost Premium.** Without it, `tools/list` still works (so a
  tools-only check looks green) but every tool call returns `isError: true` with
  "An IntelliHost Premium subscription is required to read your account through the API."
  Treat that exact message as a named gap on the card, never as "no data".
- A Premium token carries scope `mcp:read` and an `expires_at` (about a year out).

## Tools (15)

whoami, list-properties, get-property, get-listing-details, get-optimization-audit,
get-listing-optimization, get-revenue-report, get-revenue-indicator, get-helix-predictions,
get-forecast-budget, get-forecast-variance, get-portfolio-revenue, get-portfolio-health,
get-pricing-gaps, get-portfolio-funnel (each name ends in `-tool`).

There is **no per-date search-ranking tool** (RankBreeze has `get_listing_rankings`). With
IntelliHost the Visibility spoke comes from the funnel; Ranking stays a named gap unless the
funnel covers it.

## Shapes that matter

`list-properties-tool` -> `{count, properties: [{id: int, name, listing_id: str, pms, is_active,
base_price, min_price, max_price, dynamic_pricing_provider}]}`
- **Map by `listing_id`, which is the Airbnb room id** (39 of 50 on the measured account: all
  digits, 15+ long). Match it to the PMS's Airbnb listing id, the same key RankBreeze uses.
- `pms` and `dynamic_pricing_provider` were empty on 41 of 50: never use them to map or to
  detect the pricing tool.
- `base_price`, `min_price`, `max_price` were empty on all 50: IntelliHost is not a pricing source.

`get-portfolio-funnel-tool` -> `{as_of, totals: {properties_active, properties_with_data},
properties: [{property_id: int, name, month, first_page_impressions: int, clicks: int,
click_rate_pct: float, nights_booked: int, comp_benchmark: {...}}]}`
- Monthly, one row per property with data (40 of 50 measured). This is the Visibility spoke:
  impressions and click rate against `comp_benchmark`, the same role RankBreeze's
  `get_listing_metrics_summary` plays.
