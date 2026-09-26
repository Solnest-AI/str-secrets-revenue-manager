# Hostfully: PMS reference (revenue-manager runner + calendar write target)

> **Status: DOCS-ONLY.** Built from Hostfully's v3.3 OpenAPI pages on 2026-09-25 with no Hostfully
> account. Nothing below has been read from or written to a live account. The first live write for
> Hostfully must say so on the card: "first live write for Hostfully: read the after-values carefully".

Code: `skills/revenue-manager/fetch/_pms_hostfully.py` (`HostfullySource`, `HostfullyCalendarTarget`).
Hostfully calls reservations "leads". Env vars (connections kit, `connectors/pms-hostfully.md`):
`HOSTFULLY_API_KEY`, `HOSTFULLY_AGENCY_UID`.

## Source documents

Every page below is the `.md` export of the dev.hostfully.com reference (append `.md` to the page URL),
which embeds that operation's OpenAPI definition. All read 2026-09-25.

| Operation | URL |
|---|---|
| Authentication (`X-HOSTFULLY-APIKEY`) | https://dev.hostfully.com/reference/authentication |
| Getting started (rate limit, cursor pagination) | https://dev.hostfully.com/reference/getting-started |
| List properties by agency | https://dev.hostfully.com/reference/findbyagencyuid |
| Get property details | https://dev.hostfully.com/reference/findbypropertyuid_1 |
| Get property calendar | https://dev.hostfully.com/reference/findbypropertyuid |
| Get pricing periods | https://dev.hostfully.com/reference/findbypropertyuid_2 |
| Modify pricing period (SET / REMOVE) | https://dev.hostfully.com/reference/post_8 |
| Search leads with filters | https://dev.hostfully.com/reference/findleads |
| Get orders with filters | https://dev.hostfully.com/reference/getall_9 |
| List guest reviews | https://dev.hostfully.com/reference/getall_4 |
| Full index | https://dev.hostfully.com/llms.txt |

## Auth and base path

Header `X-HOSTFULLY-APIKEY: <agency API key>`. Base `https://api.hostfully.com/api/v3.3/`. The connector
card notes a wrong version under `/api/` answers **401, not 404**, so a 401 can mean a bad path, not a
bad key. `agencyUid` is a query parameter on the agency-wide property LIST only; every other call is
scoped by `propertyUid`. (The older build brief was right about this; it was wrong about the pricing
period shape, see below.)

## Endpoints used

| Method + path | Used by | Status |
|---|---|---|
| `GET /api/v3.3/properties?agencyUid` | runner inventory, property | DOCS-ONLY |
| `GET /api/v3.3/properties/{propertyUid}` | target currency check | DOCS-ONLY |
| `GET /api/v3.3/property-calendar/{propertyUid}?from&to` | runner calendar, target `read_calendar` | DOCS-ONLY |
| `GET /api/v3.3/leads?propertyUid&checkInFrom&checkInTo` | runner reservations | DOCS-ONLY |
| `GET /api/v3.3/orders?propertyUid` | runner reservation money (joined on `leadUid`) | DOCS-ONLY |
| `GET /api/v3.3/reviews?propertyUid` | runner reviews | DOCS-ONLY |
| `GET /api/v3.3/pricing-periods?propertyUid&from&to` | target, before each write (carry-forward) | DOCS-ONLY |
| `POST /api/v3.3/pricing-periods` | target `write_calendar`, operation `SET` only | DOCS-ONLY |

The write transport allows exactly the four target calls above and refuses everything else: leads, bulk
pricing, pricing rules, property edits, other API versions. It only ever builds `"operation": "SET"`.

## Semantics that matter

- **Pagination** is cursor-based: `_cursor` in, `_paging._nextCursor` out, `_metadata.totalCount`. No
  maximum `_limit` is documented, so none is sent. A repeated cursor is refused. A count short of
  `totalCount` returns `complete: false` (the runner then blocks rather than analysing a partial set).
- **Calendar:** per entry `pricing.value` + `pricing.currency` (major units), `availability.unavailable` +
  `unavailabilityReason`. `BOOKING` is RESERVED; `BLOCK`, `BLOCK_BY_OWNER`, `INQUIRY`,
  `PROPERTY_AVAILABILITY_SETTINGS`, `OTHER` are BLOCKED; closed with no reason is UNKNOWN (the analysis
  refuses it).
- **Pricing periods are one per DATE**: `{propertyUid, date, price, minimumStay, availableForCheckIn,
  availableForCheckOut, name}`. The older build brief's `startDate/endDate/amount` shape is not what v3.3
  documents. The target sends one `SET` per date and stops at the first failure without retrying. The error
  says how many dates were sent, and the core's re-read shows what landed.
- **Carry-forward:** whether a `SET` clears a field it is not sent is not documented. So the target sends
  back every field an existing period on that date already has (`price`, `minimumStay`, check-in/out flags,
  `name`), overriding only what the change sets.
- **Leads** carry no money. Room revenue is the order's `rent.rentNetPrice` (the schema gives no field
  description, so whether it is before or after `rent.discount` is unverified) with `rent.rentBreakdowns[]`
  as the per-night split. Lead status: BOOKED accepted, CANCELLED cancelled, DECLINED/IGNORED/CLOSED/DUPLICATE
  not accepted, NEW/ON_HOLD/PENDING* request or inquiry. BLOCK leads and SAMPLE leads are not stays.
- `bookedUtcDateTime` is read as UTC (its name); a stamp without an offset gets `Z`.
- **Reviews:** `rating` is an integer with no documented scale. Only 1 to 5 from a non-Booking.com source is
  kept, so scales never mix.
- Whether `to` is inclusive is not documented: reads ask one extra night and trim.
- **Not verified:** whether the calendar's `pricing.value` equals the pricing period `price` on weekends,
  given the property's `weekendAdjustmentRate`. The core's verifying re-read would catch a mismatch.
- Limits: 10,000 calls an hour per client (the FAQ says 1,000; plan for the lower).

## Undocumented, so refused or left empty

| Need | What happens |
|---|---|
| Per-listing minimum price (floor) | Only `useMinimumPriceRule` (a boolean) is documented, no value. `floor()` returns None; the core uses `settings.min_price`. |
| Min stay alone on a date with no pricing period | What price such a new period would carry is not documented. Refused before anything is sent: "include a price for that date". |
| Which pricing tool manages a property | Not documented. `pricing_managed()` returns None. |
| `REMOVE` a pricing period | Never used. Undo restores the before-values with `SET`. |
