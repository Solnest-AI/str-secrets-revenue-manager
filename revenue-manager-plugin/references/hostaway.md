# Hostaway: PMS reference (revenue-manager skill)

> **Status: DOCS-ONLY.** Every endpoint below was read from Hostaway's own API reference on
> **2026-09-25** (its changelog runs to 2026-09-10). None of it has been run against a live
> Hostaway account yet. The first live write for Hostaway must be read carefully: check the
> after-values on the card.

Code: `fetch/_pms_hostaway.py` (`HostawaySource` read adapter, `HostawayCalendarTarget` write target).
Tests: `fetch/test_pms_hostaway.py` (fake HTTP only).
Credentials (connections kit, `connectors/pms-hostaway.md`): `HOSTAWAY_ACCOUNT_ID`, `HOSTAWAY_API_KEY`.

Doc root: https://api.hostaway.com/documentation (one long page; linked anchors below appear in the page's own links, the rest are cited by section heading)

## Endpoints used

| # | Call | Used by | Doc section (read 2026-09-25) | Status |
|---|---|---|---|---|
| 1 | `POST /v1/accessTokens` form `grant_type=client_credentials&client_id=<account id>&client_secret=<API key>&scope=general` | token (read + write) | [#authentication](https://api.hostaway.com/documentation#authentication); section "Working with authorization token" | DOCS-ONLY |
| 2 | `GET /v1/listings?limit&offset` | inventory, property | [#retrieve-a-listings-list](https://api.hostaway.com/documentation#retrieve-a-listings-list), [#listing-object](https://api.hostaway.com/documentation#listing-object) | DOCS-ONLY |
| 3 | `GET /v1/listings/{listingId}` | currency before a calendar read or write | section "Retrieve a listing" | DOCS-ONLY |
| 4 | `GET /v1/listings/{listingId}/calendar?startDate&endDate` | calendar read (analysis and target) | sections "Retrieve a calendar", "Calendar day object", "Calendar day statuses" | DOCS-ONLY |
| 5 | `PUT /v1/listings/{listingId}/calendarIntervals` (JSON array, max 200 intervals) | **the only write** | section "Batch calendar update" | DOCS-ONLY |
| 6 | `GET /v1/reservations?listingId&limit&afterId` | reservations | [#retrieve-a-reservations-list](https://api.hostaway.com/documentation#retrieve-a-reservations-list), [#reservation-object](https://api.hostaway.com/documentation#reservation-object), section "Reservation statuses", [#reservation-channels](https://api.hostaway.com/documentation#reservation-channels), [#finance-fields](https://api.hostaway.com/documentation#finance-fields) | DOCS-ONLY |
| 7 | `GET /v1/reviews?type=guest-to-host&limit&offset` | reviews | [#get-reviews-list](https://api.hostaway.com/documentation#get-reviews-list) | DOCS-ONLY |

The write target's transport allows exactly calls 1, 3, 4 and 5 and refuses everything else.

## Facts the code depends on (all quoted or paraphrased from the doc sections above)

- **Token.** `{token_type, expires_in, access_token}`. "The token will be valid 1 second after being
  returned", so a fresh mint waits 1 second. TTL "24 months" in prose, but trust `expires_in`. A **403**
  means the token is no longer valid: drop it and mint once more. The code re-mints once for READS only;
  a write that gets 403 is reported, never resent.
- **Envelope.** `{status: "success"|"fail", result, count, limit, offset, page, totalPages}`.
  `status: fail` is treated as an error even on HTTP 200.
- **Types.** "boolean type should be considered as integer 0 or 1 value"; "all time values should be
  specified in UTC timezone" (so `reservationDate` / `submittedAt` become `+00:00` moments).
- **Rate limits** ([#rate-limits](https://api.hostaway.com/documentation#rate-limits)): 200 requests per
  10 seconds per account and per IP for regular endpoints, sliding window. A 429 carries
  `X-RateLimit-Retry-After` as a **Unix timestamp**, not seconds. The kit brief's "15/20 per 10s" is out
  of date; the docs and the kit's connector file agree on 200.
- **Listing money.** `currencyCode` is the listing's currency. Calendar `price` is a float in that
  currency, whole units (major). The code converts to cents for analysis and hands major units to the core.
- **Calendar statuses.** `available` -> AVAILABLE; `reserved`, `mreserved`, `pending` -> RESERVED;
  `blocked`, `mblocked`, `hardBlock` -> BLOCKED; `conflicted` (deprecated) -> UNKNOWN. When `status`
  and `isAvailable` disagree the night is UNKNOWN (never a pick).
- **Multi-unit listings** carry non-null `countAvailableUnits` etc. They are refused (read and write):
  per-night availability there is a unit count.
- **Reservation statuses.** new/modified -> accepted; cancelled -> cancelled (excluded from occupancy and
  revenue by the engine); pending/awaitingPayment/awaitingGuestVerification/unconfirmed -> request;
  declined/expired/unknown -> not accepted; inquiry* -> inquiry; ownerStay -> accepted at zero value
  (an owner block earns nothing). `isDatesUnspecified=1` means the dates are fake ("set to yesterday"),
  so those dates are dropped.
- **Room revenue** = the one non-deleted `financeField` line named `baseRate`, using `total` ("please use
  `total` field for amount value").
- **Reviews.** `rating` is on a 0-10 scale (`ratingMin`/`ratingMax` "(0-10)"); divided by 2 for the
  runner's 1-5 scale, the original kept in `rating_platform_original`.
- **Channels.** 2018 airbnbOfficial, 2002/2009/2010 Vrbo/HomeAway, 2005 Booking.com, 2007 Expedia,
  2000 direct, 2013 booking engine.

## Write shape (target)

`write_calendar(listing_id, {"YYYY-MM-DD": {"price": 199.0, "min_stay": 3}}, "USD")` becomes one
`PUT /v1/listings/{id}/calendarIntervals` per 200 dates with body
`[{"startDate": d, "endDate": d, "price": 199.0, "minimumStay": 3}, ...]`. Only the fields asked for
are sent. Before sending: the listing is re-read and its `currencyCode` must equal the plan's currency,
an archived listing is refused (docs 2025-11-15: changes to archived listings "return an error"), and
every input is validated (positive finite price, whole min stay >= 1, real ISO dates, only price and
min_stay). The response must be empty or `status: success`.

## Undocumented, so refused or left unknown (never guessed)

| Gap | What the code does |
|---|---|
| Whether `endDate` on GET calendar and on calendar updates is inclusive | Reads ask for one extra day and keep only the window. Writes use single-day intervals (`startDate == endDate`), which cannot spill onto a neighbouring night; if Hostaway reads them as empty, the core's re-read reports the date as not applied. **Verify on the first live write.** |
| Whether fields omitted from a calendar interval are preserved | Only asked-for fields are sent; the core's re-read compares every field on the written dates. |
| A listing floor / minimum price | `floor()` returns None (the Listing object has `price`, the base rate, and no floor). The core uses `property_config.settings.min_price`. |
| Which dynamic pricing tool (if any) controls the listing | `pricing_managed()` returns None. The core relies on `property_config.settings.pricing_tool`. |
| Sign of `financeField` lines of type `discount` | A reservation with a live discount line has unknown room revenue (None), not a guessed net. |
| `closedOnArrival` / `closedOnDeparture` when null | The docs' own day and update samples use `null` on an ordinary night, so a present null reads as "not closed". An absent key stays unknown. **Verify on the first live read.** |
| Array encoding of `listingMapIds` on GET /v1/reviews | Reviews are read account-wide (documented limit/offset) and filtered to the listing locally. |
| `submittedAt` on a review: named by the list's `sortBy` and `submittedAtStart/End` filters but missing from the Review object table | Read when present (UTC per the docs); a review without it is dropped as undated and counted by the engine, never dated from the stay. |
| An Airbnb listing id field | Parsed from `airbnbListingUrl` (`/rooms/<digits>`) when present; otherwise no Airbnb mapping. |
