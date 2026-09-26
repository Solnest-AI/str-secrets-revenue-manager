# Uplisting: PMS reference (revenue-manager runner + calendar write target)

> **Status: DOCS-ONLY.** Built from Uplisting's published API docs on 2026-09-25 with no Uplisting
> account. Nothing below has been read from or written to a live account. The first live write
> for Uplisting must say so on the card: "first live write for Uplisting: read the after-values
> carefully".

Code: `skills/revenue-manager/fetch/_pms_uplisting.py` (`UplistingSource`, `UplistingCalendarTarget`).
Uplisting is part of AirDNA. Env var (connections kit, `connectors/pms-uplisting.md`): `UPLISTING_API_KEY`.

## Source documents

| Doc | URL | Read |
|---|---|---|
| API reference (Postman collection "Uplisting API [Public]") | https://documenter.getpostman.com/view/1320372/SWTBfdW6 | 2026-09-25 |
| Same collection, machine-readable (what the fixtures were built from) | https://documenter.gw.postman.com/api/collections/1320372/SWTBfdW6?segregateAuth=true&versionTag=latest | 2026-09-25 |
| Connector card (auth shape, key page, limits) | str-secrets-connections `connectors/pms-uplisting.md` (sources read 2026-09-21) | 2026-09-25 |

## Auth

`Authorization: Basic <base64 of the API key ALONE>` (no `key:` colon, no trailing newline) plus
`Content-Type: application/json` on every call. Host: `https://connect.uplisting.io`. DOCS-ONLY.

## Endpoints used

| Method + path | Used by | What it returns / takes | Status |
|---|---|---|---|
| `GET /properties` | runner inventory, property | JSON:API `data[]` + `included[]` (address). No pagination documented; a reply that signals a next page is refused. | DOCS-ONLY |
| `GET /properties/:id` | target currency check | `data.attributes.currency` | DOCS-ONLY |
| `GET /bookings/:listing_id?from&to&page` | runner reservations, calendar RESERVED/BLOCKED join | 50 per page, `page` 0-based, `meta.total`, `meta.total_pages`; cancelled bookings included (`status: cancelled`); money `accomodation_total` (Uplisting's spelling) | DOCS-ONLY |
| `GET /calendar/:listing_id?from&to` | runner calendar, target `read_calendar` | `calendar.days[]`: `available`, `day_rate` (property currency, major units), `minimum_length_of_stay`, `closed_for_arrival/departure`. Max 12 months per call. | DOCS-ONLY |
| `POST /calendar/:listing_id` | target `write_calendar` | body `{"calendar": {"days": [{date, day_rate?, minimum_length_of_stay?}]}}`; answers **202 + request_id** | DOCS-ONLY |

The write transport allows exactly `GET /properties/{id}`, `GET /calendar/{id}`, `POST /calendar/{id}`
(numeric ids) and refuses everything else. It never sends `available`, so it cannot open or close a night.

## Semantics that matter

- **Writes are asynchronous.** Uplisting: the payload "will be applied to the property asynchronously ...
  typically less than 1 minute". `UplistingCalendarTarget.APPLIES_ASYNC = True`, `SETTLE_SECONDS = 60`:
  the core should wait before its verifying re-read. A re-read inside that window can still show the
  before-values without the write having failed.
- **`day_rate` is the commission-free base rate.** Uplisting adds each channel's markup when it syncs.
- **Past dates are silently ignored** by Uplisting ("Any dates before today in UTC will be ignored"), so
  the target refuses them before sending.
- **Why a night is closed is not given.** A closed night inside a live (not cancelled) booking is
  RESERVED, any other closed night BLOCKED, joined from `/bookings`.
- Whether `to` is inclusive is not documented: reads ask one extra night and trim.
- Limits: 5 req/s and 100 req/min per IP, 15 req/min per property.

## Undocumented, so refused or left empty

| Need | What happens |
|---|---|
| Reviews | No REST endpoint (only the hosted MCP has `reviews:read`). `reviews()` raises, so the runner's reviews spoke degrades instead of reading zero reviews. |
| Per-listing minimum price (floor) | Not documented. `floor()` returns None; the core falls back to `property_config.settings.min_price`. |
| Which pricing tool manages a listing | Not documented. `pricing_managed()` returns None; the core relies on `property_config.settings.pricing_tool`. |
| Airbnb listing id, active/listed flag | Not on the documented property; `listings` is `[]`, `listed` unknown. |
