# Smoobu: PMS reference (revenue-manager runner + calendar write target)

> **Status: DOCS-ONLY.** Built from docs.smoobu.com on 2026-09-25 with no Smoobu account. Nothing
> below has been read from or written to a live account. The first live write for Smoobu must say
> so on the card: "first live write for Smoobu: read the after-values carefully".

Code: `skills/revenue-manager/fetch/_pms_smoobu.py` (`SmoobuSource`, `SmoobuCalendarTarget`).
Smoobu calls properties "apartments". Env vars (connections kit, `connectors/pms-smoobu.md`):
`SMOOBU_API_KEY` and `SMOOBU_API_SECRET`. **Both are required.**

## Source documents

| Doc | URL | Read |
|---|---|---|
| API reference (single Slate page) | https://docs.smoobu.com/ | 2026-09-25 |
| HMAC auth, required headers, worked examples | https://docs.smoobu.com/#hmac-authentication | 2026-09-25 |
| Changelog (sunset date, rate limit) | https://docs.smoobu.com/ (Change Log section) | 2026-09-25 |
| Connector card | str-secrets-connections `connectors/pms-smoobu.md` (sources read 2026-09-21) | 2026-09-25 |

## Auth: HMAC on every request, from day one

The legacy single `Api-Key` header is deprecated. Changelog 2026-05-29: "will be removed on September 25,
2026". Changelog 2026-09-23: end of support moved to **October 31, 2026** (the auth section now says
the same). The help centre says "add signing before 30 October 2026". **No legacy header is sent
anywhere in this code.** Every request, reads included, carries:

| Header | Value |
|---|---|
| `X-API-Key` | the Key |
| `X-Timestamp` | UTC now, ISO 8601 `2026-04-01T12:00:00Z`, within 5 minutes of Smoobu's clock |
| `X-Nonce` | a fresh UUID v4 per physical request; a reused nonce is a 401 even with a valid signature |
| `X-Signature` | base64(HMAC-SHA256(secret, canonical)) |

`canonical = METHOD \n PATH \n QUERY \n TIMESTAMP \n NONCE \n sha256hex(body) \n API_KEY`, where QUERY is
the params sorted, keys and values RFC 3986 encoded (space `%20`, `[]` `%5B%5D`), and a GET hashes the
empty string. The code builds the query string once and sends exactly what it signed. Pairs are sorted by
(encoded key, encoded value); the docs only say "sort alphabetically", and every call here has one value
per key, so repeated-key ordering never arises. The tests check the signer against the golden values
produced by Smoobu's own shell recipe on the docs' two worked examples. On the runner's one 429 retry the
request is signed again with a new nonce.

## Endpoints used

| Method + path | Doc anchor | Used by | Status |
|---|---|---|---|
| `GET /api/apartments` | #get-apartment-ids | runner inventory, property | DOCS-ONLY |
| `GET /api/apartments/{id}` | #get-apartment | runner property (timezone, rooms, **currency**), target currency check | DOCS-ONLY |
| `GET /api/rates?apartments[]=&start_date=&end_date=` | #get-rates | runner calendar, target `read_calendar`, min-stay pre-check | DOCS-ONLY |
| `POST /api/rates` | #post-rates-api | target `write_calendar` | DOCS-ONLY |
| `GET /api/reservations?apartmentId&from&to&page&pageSize&excludeBlocked&includePriceElements` | #get-bookings-api | runner reservations, calendar RESERVED/BLOCKED join | DOCS-ONLY |

The write transport allows exactly `GET /api/apartments/{id}`, `GET /api/rates`, `POST /api/rates` and
refuses everything else (bookings, messages, availability, cancellations).

## Semantics that matter

- **Rates:** `data[<apartment id>][<date>] = {price (null = no price), min_length_of_stay, available}`;
  `available` is a count, 0 = not available. No currency in the reply: the apartment's `currency` applies.
  Prices are major units (`daily_price` is a double).
- **Write:** `{"apartments": [id], "operations": [{"dates": [...], "daily_price", "min_length_of_stay"}]}`,
  dates with the same values grouped into one operation, one POST. Success is `{"success": true}`;
  validation errors come back as **HTTP 500** with a `detail` (never echoed).
- **Min stay alone:** Smoobu takes a min stay only "if this date has a price or set together with price".
  The target reads the date fresh and refuses a min-stay-only change on a date with no price, before sending.
- **Reservations:** `type` is `reservation` | `modification of booking` | `cancellation`; cancellations are
  left out unless `showCancellation`; blocked bookings are excluded (`excludeBlocked=true`) and any that
  still arrive are dropped. Room revenue is the `basePrice` price element(s) (the stay total, not per night).
  Long-stay discounts and coupons are not netted, because their sign is not documented. `page` is 1-based,
  `pageSize` max 100.
- `created-at` ("2018-01-03 13:51") has no timezone, so pickup windows cannot place Smoobu bookings in time.
- Whether `end_date` is inclusive is not documented: reads ask one extra night and trim.
- Limit: 700 requests per minute (changelog 2026-09-17, down from 1000).

## Assumption, flagged

The rates object documents no closed-to-arrival/departure field. The runner's analysis marks a night
"unknown" unless both are booleans, so Smoobu nights read as **no arrival/departure restriction exposed**
(`NO_ARRIVAL_RULES_EXPOSED = False`). If a Smoobu host uses check-in/check-out-day rules, those nights can
read as open when they are not.

## Undocumented, so refused or left empty

| Need | What happens |
|---|---|
| Reviews | No endpoint. `reviews()` raises; the reviews spoke degrades instead of reading zero. |
| Per-apartment floor | `price.minimal` is website-builder content ("You can create and edit this content in the Smoobu website builder"), not an enforced minimum. `floor()` returns None; the core uses `settings.min_price`. |
| Which pricing tool manages an apartment | Not documented. `pricing_managed()` returns None. |
| Min stay on a date with no price | Refused before sending (documented Smoobu rule). |
