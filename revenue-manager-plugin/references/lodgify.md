# Lodgify: PMS reference (revenue-manager skill)

> **Status: DOCS-ONLY.** Every endpoint below was read from the OpenAPI definition behind each page of
> Lodgify's API reference (docs.lodgify.com, fetched as `.md`) on **2026-09-25**. None of it has been
> run against a live Lodgify account yet. The first live write for Lodgify must be read carefully:
> check the after-values on the card.

Code: `fetch/_pms_lodgify.py` (`LodgifySource` read adapter, `LodgifyCalendarTarget` write target).
Tests: `fetch/test_pms_lodgify.py` (fake HTTP only).
Credential (connections kit, `connectors/pms-lodgify.md`): `LODGIFY_API_KEY`, sent as header `X-ApiKey`.
Plan gate (per the kit): the Public API is not included on Lodgify's Basic plan.

Host: `https://api.lodgify.com`. v1 and v2 live side by side on the same host and return different shapes.

## Endpoints used

| # | Call | Used by | Doc page (page `updatedAt`; read 2026-09-25) | Status |
|---|---|---|---|---|
| 1 | `GET /v2/properties?page&size&includeCount` (size max 50) | inventory, property | [getallpropertiesasync](https://docs.lodgify.com/reference/getallpropertiesasync) (2026-06-08) | DOCS-ONLY |
| 2 | `GET /v2/properties/{id}/rooms` | room type id, capacity | [propertiesapi_v_getallrooms_get](https://docs.lodgify.com/reference/propertiesapi_v_getallrooms_get) (2026-07-13) | DOCS-ONLY |
| 3 | `GET /v2/rates/calendar?houseId&roomTypeId&startDate&endDate` (both dates inclusive) | nightly price + min stay (analysis and target) | [ratescalendar-v2](https://docs.lodgify.com/reference/ratescalendar-v2) (2026-06-08) | DOCS-ONLY |
| 4 | `GET /v1/availability/{propertyId}/{roomTypeId}?periodStart&periodEnd` | per-night availability | [get_v1-availability-propertyid-roomtypeid](https://docs.lodgify.com/reference/get_v1-availability-propertyid-roomtypeid) (2026-04-21) | DOCS-ONLY |
| 5 | `GET /v2/reservations/bookings?page&size&includeCount&stayFilter=All` | reservations | [getallasync](https://docs.lodgify.com/reference/getallasync) (2026-06-08) | DOCS-ONLY |
| 6 | `POST /v1/rates/savewithoutavailability` | **the only write** | [savetiny](https://docs.lodgify.com/reference/savetiny) (2026-06-08) | DOCS-ONLY |

Also read: [getpropertybyidv2](https://docs.lodgify.com/reference/getpropertybyidv2) (same PropertyDto as #1),
[rate-limits](https://docs.lodgify.com/docs/rate-limits) (2025-09-18), [errors](https://docs.lodgify.com/docs/errors)
(2025-09-18), and the full page index at https://docs.lodgify.com/llms.txt.

The write target's transport allows exactly calls 2, 3, 4 and 6 and refuses everything else.

## Facts the code depends on

- **Rate limits.** 600 requests/minute on v1, 750/minute on v2; 429 on exceed.
- **Errors** are 4xx/5xx with a JSON `{message, code, correlation_id, event_id}`; never echoed by the code.
- **PropertyDto:** `id`, `name`, `internal_name`, `city`, `country`, `country_code`, `currency_code`,
  `rooms [{id, name}]`, `is_active` ("linked to a valid website", so NOT used as "listed"). No timezone,
  no bedroom count, no channel listing ids. Capacity comes from the room: `max_people`, `bedrooms`,
  `bathrooms`, `units`.
- **One room type, one unit.** "most rentals on Lodgify have only a single room type id". Properties
  with several room types, or a room type with `units > 1`, are refused (read and write).
- **Rates calendar v2:** `{calendar_items: [{date, is_default, prices: [{min_stay, max_stay,
  price_per_day, price_per_additional_guest, additional_guests_starts_from}]}], rate_settings:
  {currency_code, ...}}`. `price_per_day` is a decimal number in major units. Several `prices` entries on
  one date are length-of-stay tiers (the write docs: "Multiple price entries within the same date range
  (with different min/max stay values)"); there is no single nightly price then.
- **Availability v1:** periods with `period_start`, `period_end` ("inclusive"), `available`,
  `total_units`, `is_available` ("not closed and has unoccupied units"), `booking_ids`,
  `closed_period_id`. Night status: `is_available` true -> AVAILABLE; a `closed_period_id` -> BLOCKED;
  `is_available` false with `available` 0 -> RESERVED; anything else, or a night two periods claim -> UNKNOWN.
- **Bookings v2:** NO property filter exists, so the account's bookings are paged and filtered to the
  property here. `status` Open | Tentative | Booked | Declined; `canceled_at` set -> cancelled (excluded
  from occupancy and revenue by the engine); `is_deleted` (trash) rows dropped. Booked -> accepted,
  Tentative -> request, Open -> inquiry (docs: reopening a booking "makes the room available again"),
  Declined -> not accepted. `source` enum maps Airbnb/AirbnbIntegration -> airbnb, HomeAway -> vrbo,
  BookingCom -> booking, Manual/OH/PublicApi -> direct.
- **Rates write (v1):** body `{property_id, room_type_id, rates: [{is_default, start_date, end_date,
  price_per_day, min_stay, max_stay, price_per_additional_guest, additional_guests_starts_from}]}`.
  `end_date` is **exclusive** ("the rate does not apply to this night itself"). `price_per_day` minimum 1.
  Overlapping ranges are not allowed. Success answer: `true`.

## Write shape (target)

`write_calendar(listing_id, {"YYYY-MM-DD": {"price": 199.0, "min_stay": 3}}, "USD")`: the target reads
the rates calendar fresh for the changed range, requires the live `rate_settings.currency_code` to equal
the plan's currency, requires exactly one price entry per changed night, then sends ONE POST with one
single-night rate per date (`end_date` = next day). The night's own `max_stay`,
`price_per_additional_guest` and `additional_guests_starts_from` are carried forward from that fresh
read so the write moves only what was asked. Anything other than a literal `true` answer is a failure.
Nothing is ever retried.

## Undocumented, so refused or left unknown (never guessed)

| Gap | What the code does |
|---|---|
| **Reviews**: no reviews endpoint exists anywhere in the API index | `LodgifySource.reviews()` raises a named error; the runner marks the reviews spoke unreadable (a named gap, not zero reviews). |
| **Closed-to-arrival / closed-to-departure per night** | Left unknown (None). Consequence: the analysis engine flags every Lodgify night `calendar_price_currency_or_restrictions_unknown` and will not call the calendar analysable until the engine treats restrictions as optional for a PMS that does not expose them (the same carve-out it already has for OwnerRez rates). Not changed on this branch. |
| Money in bookings (`Money1` is an object with no documented fields) | Only a plain number in `subtotals.stay` is read; any other shape, or a non-zero `promotions` subtotal (sign undocumented), leaves room revenue unknown. |
| Property timezone | None; the engine falls back to UTC and says so (`missing_property_timezone_using_utc`). |
| A listing floor | `floor()` returns None. `min_price` in the property and room schemas is a display summary ("always given in euros"), not a minimum-price setting. The core uses `property_config.settings.min_price`. |
| Which dynamic pricing tool (if any) controls the listing | `pricing_managed()` returns None. The core relies on `property_config.settings.pricing_tool`. |
| Whether a single-night date-specific rate replaces the night's existing rate or is merged | The core's re-read compares every field on the written dates. **Verify on the first live write.** |
| Airbnb listing id | Not in the property schema; no Airbnb mapping from Lodgify (setup shows the ranking gap by name). |
| `periodStart` / `periodEnd` are typed `date-time` | Sent as `YYYY-MM-DD`. |

## Where the kit's build brief disagrees (the vendor docs win)

- Brief lists properties via `GET /v1/properties`; this uses `GET /v2/properties` (documented, paged,
  carries `currency_code`; the kit's own probe also calls v2).
- Brief lists availability via `GET /v2/availability/{propertyId}`; its period `end` has no documented
  inclusivity, so this uses the v1 room-type endpoint whose `period_end` is documented inclusive.
