# OwnerRez: calendar write target reference (revenue-manager skill)

`fetch/_pms_ownerrez.py` holds both the runner's read adapter (`OwnerRezSource`) and the
calendar write target (`OwnerRezCalendarTarget`). Every PMS price write goes through
`apply_change.py plan|apply|rollback --target ownerrez` and the `_calendar_write` core
(docs/WRITE-TARGETS.md), never through a raw MCP tool.

Base URL `https://api.ownerrez.com/v2`, HTTP Basic (login email + `pt_` token) plus a
User-Agent (without one OwnerRez answers 403, measured 2026-09-24).

Endpoints read from OwnerRez's API reference on **2026-09-25**:

| Call | Doc | Status |
|---|---|---|
| `GET /v2/calendar/{property_id}?from&to` | https://api.ownerrez.com/help/v2/calendar/get-calendar-property-id | **VERIFIED-LIVE 2026-09-25**: one read-only call through the target, one property, 7 days: `currency_code` (USD), 7 nights, `rate.rent` decimal MAJOR units, `rules.min_nights` integer, `status` mapped to availability. (Also measured 2026-09-24 through the runner, commit b844d3b.) Range max 366 days. Docs: nights with no data are omitted ("a 200 with fewer days than the requested range is expected"); the core refuses any planned night that is missing. Docs: "This data can lag a short time behind live changes": the core re-READS at 0/5/15/30 s after a write and never resends. |
| `PATCH /v2/spotrates` | https://api.ownerrez.com/help/v2/spotrates/patch-spotrates | **DOCS-ONLY** (never written live). "Create and/or partially update multiple spot rates." Body: array of SpotRateModel `{"property_id": int, "date", "amount": decimal (the nightly rate, MAJOR units; null = seasonal default), "currency" (must match the property's), "min_nights"?}`; returns the updated spot rates (not trusted; the re-read decides). The target sends ONE PATCH per plan. `date` is typed date-time in the docs; the target sends `YYYY-MM-DD` (unverified whether OwnerRez wants a time part). |
| `GET /v2/spotrates?property_ids&from&to` | https://api.ownerrez.com/help/v2/spotrates/get-spotrates | Not used. Sparse raw overrides; the calendar is read instead because `rate.rent` is what a spot rate sets. |

Units: `amount` and `rent` are decimals in the property's currency (major units). The target
sends the approved price exactly, refused at plan time if the currency cannot carry it (JPY
15000.5).

Floor / pricing owner: no documented property min rate is read, so `floor()` is None and
`property_config.settings.min_price` applies. `rate.is_spot_rate` is NOT evidence of a pricing
tool (an operator's own spot rate looks the same), so `pricing_managed()` is None and
`property_config.settings.pricing_tool` decides. Undo writes the before-rent back as a spot
rate; it does not delete the spot rate, so the night stays pinned at its old price.
