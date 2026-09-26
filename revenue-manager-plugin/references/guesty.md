# Guesty: calendar write target reference (revenue-manager skill)

`fetch/_pms_guesty.py` holds both the runner's read adapter (`GuestySource`) and the calendar
write target (`GuestyCalendarTarget`). Every PMS price write goes through
`apply_change.py plan|apply|rollback --target guesty` and the `_calendar_write` core
(docs/WRITE-TARGETS.md), never through a raw MCP tool.

Base URL `https://open-api.guesty.com/v1`, OAuth2 bearer token. **Five tokens per 24 h per
client id**: the target uses the cached token first (`<kit>/.cache/guesty.token` or
`GUESTY_TOKEN_CACHE`) and only mints when none is fresh (`_pms_guesty.get_token`). Rate limits
are account-wide: 15/s, 120/min, 5000/hr.

Endpoints read from Guesty's OpenAPI pages (`.md` versions) on **2026-09-25**:

| Call | Doc | Status |
|---|---|---|
| `GET /v1/availability-pricing/api/calendar/listings/{id}?startDate&endDate` | https://open-api-docs.guesty.com/reference/get_availability-pricing-api-calendar-listings-id | **VERIFIED-LIVE 2026-09-24** through the runner's `GuestySource.calendar` (same endpoint and parser shape: `data.days[]` with `date`, `listingId`, `currency`, `price` in WHOLE units, `minNights`, `status` available/unavailable/booked/reserved). **Not re-read on 2026-09-25**: no fresh cached token existed and minting was not allowed. |
| `PUT /v1/availability-pricing/api/calendar/listings` | https://open-api-docs.guesty.com/reference/put_availability-pricing-api-calendar-listings | **DOCS-ONLY** (never written live). Body: array of `{"listingId", "startDate", "endDate", "price"?, "minNights"?, ...}`; `price` is "New price, in the listing's currency"; returns `"ok"`. Docs: "strongly suggested updating a single unique listing ID in a single HTTP request", max 730 days, never the same listing in parallel. The target sends ONE request per plan, one listing, one single-day period per date. |
| `PUT /v1/availability-pricing/api/calendar/listings/{id}` | https://open-api-docs.guesty.com/reference/put_availability-pricing-api-calendar-listings-id | Not used (one range per call would need one request per date). Listed for completeness. |

Units: every live price read was a whole number and the docs say only "number in the
listing's currency", so the target sends WHOLE units and refuses a fractional price at plan
time (`price_step` = 1) rather than guess whether Guesty keeps cents.

Floor / pricing owner: no documented listing min price or dynamic-pricing owner flag is read,
so `floor()` and `pricing_managed()` return None; `property_config.settings.min_price` and
`pricing_tool` decide. Undo writes the before-price back as an explicit price (it does not
restore `isBasePrice`).
