# Beyond (formerly Beyond Pricing): API reference for the revenue manager

Status: **DOCS-ONLY.** There is no Beyond test account. Every endpoint below was read from
Beyond's published docs on 2026-09-25 and has never been called with a real token by this
code. The first live write for any Beyond listing is its first live test: the plan card says
"First live write for Beyond: read the after-values carefully" on every Beyond plan.

Code: `fetch/_beyond.py` (reads, `BeyondSource`, GET-only through `ReadClient`) and
`fetch/_beyond_write.py` (plan / apply / rollback with the 8 guarantees of
`docs/WRITE-TARGETS.md`). Tests: `fetch/test_beyond.py`, `fetch/test_beyond_write.py`,
against a fake built from these docs.

## Sources (read 2026-09-25)

| Source | URL | Fingerprint of the copy read |
|---|---|---|
| Full documentation, one file | https://developers.beyondpricing.com/full-documentation.md | sha256 `90e571e63df87045...` |
| OpenAPI 3.1 schema ("Beyond API" 2.0.0) | https://developers.beyondpricing.com/api/v1/schema/ | sha256 `fd40a008bae18b3a...` |
| Connections kit notes | `str-secrets-connections/connectors/pricing-beyond.md` | kit live probes 2026-09-21 |

Section names below are headings in the full documentation; `operationId`s are from the
OpenAPI schema. Docs not used: `dynamic-api-docs.beyondpricing.com` (the other direction, PMS
vendors feeding Beyond) and the legacy `api.beyondpricing.com/api` Token API (deprecated).

## Transport facts

| Fact | Source | Status |
|---|---|---|
| Base `https://developers.beyondpricing.com`, paths under `/api/v1/` | "Base URL" | DOCS-ONLY |
| `Authorization: Bearer <PAT>`; PATs start `bpat_`; env var here is `BEYOND_TOKEN` | "Personal Access Token (PAT)"; kit | DOCS-ONLY |
| PAT needs Beyond Pro; Beyond revokes tokens when Pro ends (every call then 401) | "PAT Rules (Summary)" | DOCS-ONLY |
| PAT acts with its login's permissions; a 403 on PATCH can mean no edit access | "PAT Rules"; customizations "Errors" | DOCS-ONLY |
| JSON:API: `Accept`/`Content-Type: application/vnd.api+json`; `{"data": ...}`; dasherized names | "JSON:API Format" | DOCS-ONLY |
| Write body: `{"data": {"type": <plural dasherized type>, "id": "<listing id>", "attributes": {...}}}`; wrong `type` is a 409 | "Request Format"; "409 -- Conflict" | DOCS-ONLY |
| Every path ends in `/`; without it Beyond answers 301 (our transports never follow redirects, so a 301 is an error) | kit live probe 2026-09-21 | kit probe, not by this code |
| Errors are JSON:API documents whose `detail` can echo channel credentials (422 example); this code never surfaces a body | "Error Handling", "422" | DOCS-ONLY |
| Rate limits per Beyond user for PATs; 429 with `Retry-After`. The writer never retries; the reader retries one 429 (ReadClient) | "Rate Limiting" | DOCS-ONLY |
| Listings without an active channel connection are invisible (404 everywhere) | "List Listings" | DOCS-ONLY |

## Units

- **All prices are major units in the listing's own currency** (`currency` on the listing).
  Calendar: "integers in whole currency units ... never expressed in cents, do not divide by
  100" ("Calendar", "Price Fields"). Recommendations and customizations use the same units.
  Market insights are in the owner's BILLING currency instead ("Market Insights"), which is
  why the reader does not mix them with listing prices.
- `base-price`: integer, minimum 10 (`PatchedBasePriceCustomizationRequest`).
- `min-price`: number (double), minimum 5, nullable; `max-price`: number (double), nullable,
  **null = no ceiling** (`PatchedMinMaxPricesCustomizationRequest`; "Listing attributes").
- Manual override `price`: number (double), minimum 1; `percentage-adjustment`: integer,
  -100..1000, `10` = +10% (`ManualOverrideRequest`).
- `min-stay` (annual): integer, minimum 1, nullable ("If left blank it defaults to 1 night").
- The writer accepts only WHOLE base and fixed-override prices, because Beyond prices and
  pushes nights as integers; `min`/`max` keep two decimals, as documented.

## Endpoints the code calls

Paths are relative to `https://developers.beyondpricing.com`. `{id}` is Beyond's own listing
id, a whole number.

| # | Method + path | operationId | Used for | Doc section | Status |
|---|---|---|---|---|---|
| R1 | `GET /api/v1/listings/?page[number]&page[size]` (max 100) | `list_listings` | reader: account inventory | "List Listings" | DOCS-ONLY |
| R2 | `GET /api/v1/listings/{id}/` | `get_listing` | identity, `currency`, `timezone`, `enabled` (price sync on), `in-active-market`, `sync-status` | "Get Listing Details" | DOCS-ONLY |
| R3 | `GET /api/v1/listings/{id}/customizations/` | `get_listing_all_customizations` | base-price, min-max-prices, min-stays, extra-guest-fees, time-based-adjustments in one read (NOT manual overrides) | "All Customizations" | DOCS-ONLY |
| R4 | `GET /api/v1/listings/{id}/customizations/manual-overrides/?filter[start-date]&filter[end-date]` | `get_listing_manual_overrides_customization` | per-date overrides; one row per date (`start-date == end-date`), at most one of `price` / `percentage-adjustment`; default window today..+365 in the listing's timezone | "Manual Overrides", "Reading overrides back" | DOCS-ONLY |
| R5 | `GET /api/v1/listings/{id}/calendar/?filter[start-date]&filter[end-date]&sort=date&page[size]=366` | `list_listing_calendar` | per night: `price` (what Beyond prices and pushes; a fixed override shows as that amount), `price-posted` (last pushed), `effective-min-price`, `effective-max-price`, `price-override-type` (null/fixed/percentage), `availability`. Page size default 366, max 731. A misspelt filter is IGNORED with a 200 and the default window returned, so the reader checks the dates it got. 400 "not yet clustered" when `in-active-market` is false | "Calendar" | DOCS-ONLY |
| W1 | `PATCH /api/v1/listings/{id}/customizations/base-price/` type `base-price-customizations`, attributes `{"base-price": int}` | `patch_listing_base_price_customization` | set base | "Base Price" | DOCS-ONLY |
| W2 | `PATCH /api/v1/listings/{id}/customizations/min-max-prices/` type `min-max-price-customizations`, attributes `min-price` and/or `max-price` only | `patch_listing_min_max_prices_customization` | set min / max (undo may send `max-price: null`) | "Min/Max Prices"; "Conventions: PATCH updates only the fields you send" | DOCS-ONLY |
| W3 | `PATCH /api/v1/listings/{id}/customizations/min-stays/` type `min-stay-customizations`, attributes `{"min-stay": int}` only | `patch_listing_min_stays_customization` | set the ANNUAL min stay | "Minimum Stays", "Annual Minimum Stay" | DOCS-ONLY |
| W4 | `PATCH /api/v1/listings/{id}/customizations/manual-overrides/` type `manual-override-customizations`, attributes `{"overrides": [{"start-date": d, "end-date": d, "price": n} or {..., "percentage-adjustment": n} or {start, end only = clear}]}` | `patch_listing_manual_overrides_customization` | set / change / clear date overrides. Additive: dates not sent keep their override. Setting one kind clears the other. Past dates (listing timezone) are a 422 | "Manual Overrides" | DOCS-ONLY |

`W*` calls are the only writes the Beyond `WriteClient` can make, each bound to one listing id;
every other method+path (activation, refresh, time-based adjustments, extra guest fees,
users, accounts, webhooks, other listings) is refused before it leaves the machine.

## Beyond behaviour the writer is built around

1. **A fixed override is accepted below the listing minimum**: "an explicit override outranks
   the floor" ("Manual Overrides"; `ManualOverrideRequest.price`: "May be below the listing's
   minimum price"). Beyond will not protect the floor, so the writer refuses any override whose
   night lands below the min (or below that night's own `effective-min-price` when a seasonal /
   day-of-week / Beyond-set floor is higher). Exception, said loudly on the card: an UNDO that
   restores the operator's own previous override exactly.
2. **A min raise does not lift fixed overrides.** The plan lists override nights left below
   the new min (and above a new max).
3. **Percentage adjustments apply after every other pricing factor**, so a negative percent
   can land under the floor; the plan projects the night from the calendar's modeled price.
4. **Beyond auto-accepts its own base-price recommendations by default** ("Recommendations"),
   so a base set here can be replaced later. Said on the card for every base change.
5. **`enabled` false = price syncing off**: Beyond settings change but nothing reaches the
   channel. Said on the card.
6. **min / base / max are two PATCHes** (min-max-prices and base-price). The writer sends them
   in the order that keeps `min <= base <= max` true after each call, and refuses at plan time
   a change where no order does (split it in two).

## Refused, because the docs do not show a safe way

| Request | Why it is refused |
|---|---|
| Min stay on one date | Manual overrides carry no min stay. Per-date min stays live in `seasonal-min-stays` (and other rule lists) inside the min-stays customization; the docs do not say whether PATCHing a list replaces or merges it. |
| `percent_stacked` | Beyond has a single percentage, applied after all factors. |
| Multi-date or weekday-restricted overrides (`days-of-week`) | Supported by Beyond, but the reader cannot verify a range row against the per-date read-back, so the writer only sends and accepts one date per row. |
| Override dates more than 365 days out | Past the documented default override read window, so the write could not be re-read and proven. |
| Clearing `max` from a change file | Only the undo of a ceiling this writer added sends `max-price: null`. |
| Anything needing the calendar when `in-active-market` is false | The calendar answers 400 "not yet clustered", so the nights the change moves cannot be checked. |
| Listing activation, refresh, extra guest fees, time-based adjustments | Not price targets in the WRITE-TARGETS contract; not on the allowlist. |

## Verification (what "verified" means for Beyond)

After a send, the writer re-reads R2, R3 and (if overrides were touched) R4 and checks:
min, base, max and the annual min stay equal their wanted value, written or not; every other
field of every customization family in R3 (seasonal prices, day-of-week floors, min-stay
rules, gap fill, extra guest fees, time-based adjustments) is exactly what it was before the
send; every override date in the window equals the pre-send read with the plan's changes laid
on top. An empty or unreadable re-read is `sent-unverified`, never success.

## The 90-day runner on Beyond (`analyze90.py --pricing beyond`, or auto from setup)

A DEGRADED mode (`fetch/_beyond_runner.py`): the card prices and names, at the top, every
input Beyond's API does not supply. Never a crash, never a silent guess.

| PriceLabs input | Beyond card does instead |
|---|---|
| Market p25-p90 per night | Beyond market insights benchmark AVERAGE posted rate and occupancy (R6 below), only when `meta.currency` is the listing currency (no exchange rate is documented, so nothing is converted; otherwise "market comparison unavailable in Beyond's API"). AirROI trailing-12-month ADR p75 stands in on nights without it. The reference is named on every scenario. |
| Rule attribution | "Rule check is PriceLabs-only; Beyond rules not graded." |
| Rules first, then DSOs | Not built for Beyond (named gap, 2026-09-25). The runner reads no Beyond rule families (seasonal prices, day-of-week floors, time-based adjustments), so it cannot tell which one produced a night; every review night stays a date-override scenario, and the card's scenario header says "no rule stack is read, so there is no rules-first step". Building it needs the R3 customizations read in the runner plus a writer path for those families, which the docs do not show a safe read-back for. |
| Per-night min stay | The PMS sync check runs its price and booking halves; the min-stay half is skipped and said. |
| Calculation timestamp | Freshness is the calendar READ time, labelled "read at". |
| A ceiling | A blank Beyond max is "no ceiling", never invented. |
| The actions/nudges pile | Skipped and said. |

Reconciliation compares the PMS nightly price with Beyond's `price-posted` (what is quoted,
docs tip), falling back to `price`. A night is "at the floor" when Beyond's modeled `price`
sits on that night's `effective-min-price`.

The min is still an output, from the SAME rule as PriceLabs
(`_mvp_analysis.min_price_recommendation`), printed as the same "Recommended min price: lower
to / raise to / keep at X net (currently Y)." line: nights at the floor (a night is "at the
floor" when Beyond's modeled `price` sits on its `effective-min-price`), pace against Beyond's
benchmark occupancy, and AirROI's ADR p25 as the named lower quartile when connected. Moves are
capped at the property's max_delta_pct; a raise never passes base; a blank max is never read.

Review scenarios follow the shared engine too (scoped mismatches, cuts AND raises capped at
max_delta_pct, days_out from the property-local today). A CUT is measured against Beyond's
benchmark average (else AirROI ADR p75); a RAISE needs a lower reference, which only AirROI's
ADR p25 is, so without AirROI the card says there are no raise scenarios.

| # | Method + path | operationId | Used for | Doc section | Status |
|---|---|---|---|---|---|
| R6 | `GET /api/v1/listings/{id}/market-insights/?filter[start-date]&filter[end-date]&filter[compare-to]&page[size]=366` | `get_listing_market_insights` | per stay date, `benchmark` side of `average-posted-rate`, `average-booked-rate`, `adj-occupancy` (else `occupancy`, the documented fallback). `null` = not computed, never 0. `cluster` first; one retry with `market` when the cluster has no benchmark or answers 422 (documented). 30 requests/minute. | "Market Insights" | DOCS-ONLY |

Setup (`setup_properties.py --pricing beyond`, or auto when Beyond is the only pricing tool
connected) maps each PMS property to a Beyond listing through `channel-listings` (R1): the PMS
id on a channel named for the PMS, else the Airbnb room id on an `airbnb` channel, else the
exact title. A tier with two candidates stops ("not guessed"). It stores
`settings.pricing_tool = "beyond"` and `settings.beyond_listing_id`. `--pricing-tool beyond` is
the same flag. A property Beyond prices that cannot be mapped, or a Beyond user with no
BEYOND_TOKEN, is still written with `pricing_tool = "beyond"` and a named `pricing_gap`, so the
PMS calendar writer keeps refusing PMS price writes for it.
