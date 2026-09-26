# Write targets: every PMS and pricing tool through one safe writer

Decision (Ryan, 2026-09-25): all 8 PMSs (Hospitable, Guesty, OwnerRez, Hostaway, Lodgify, Uplisting,
Smoobu, Hostfully) and both pricing tools (PriceLabs, Beyond) are read AND write. RankBreeze,
IntelliHost, AirROI, Turno, Breezeway stay read-only.

Rule: no price ever goes out through a raw MCP tool call. Six of the eight PMS servers and Beyond's
server are built by Claude on each attendee's machine, so their tool names and argument shapes differ
per attendee. Every write goes through `apply_change.py` and a target module in `fetch/`, which
enforces the same guarantees for every vendor:

1. Plan on a FRESH read (never cache). Show before and after for every date.
2. Refuse a nightly price below the listing floor. Flag any per-night move over 15% (warning, D8).
3. Plan is content-hashed; apply refuses an edited plan, a plan older than 24h, or a past date.
4. Apply re-reads fresh and refuses if anything the plan saw has moved (drift).
5. Snapshot the before-values to disk BEFORE sending. Journal always written, even on failure.
6. Send. Never retry a write. Never echo a vendor response body (it can echo secrets).
7. Re-read and compare every field written AND every field on those dates not written. An empty or
   unreadable re-read is NOT success.
8. One-step undo from the journal, skipping dates already back to their before-value and past dates.

## Two kinds of target

- **Pricing tools** (PriceLabs today in `_mvp_write.py`; Beyond in `_beyond_write.py`): listing-level
  min / base / max plus date-specific overrides. Same guarantees, vendor-specific semantics.
- **PMS calendars** (`_calendar_write.py` core + one `<Name>CalendarTarget` per PMS): per-date nightly
  price and min stay, written straight to the PMS. Only for listings whose prices the PMS owns.
  If `property_config.settings.pricing_tool` (or the live PMS, where it exposes it) says PriceLabs or
  Beyond manages the listing, a PMS price write is refused with "PriceLabs sets this listing's
  prices, so a PMS change would be overwritten on the next sync. Change it in PriceLabs instead."

## PMS calendar target interface (each `_pms_<name>.py` exposes one)

```python
class <Name>CalendarTarget:
    name: str              # "hospitable" | "guesty" | "ownerrez" | "hostaway" | "lodgify" | ...
    host: str              # the ONE API host this target may talk to
    ALLOWED: tuple         # ((method, compiled path regex), ...) every call it will ever make;
                           # the transport refuses anything else, no redirects, 60s timeout,
                           # call budget, never retries a write

    def __init__(self, connections, opener=None): ...

    def read_calendar(self, listing_id: str, start: date, end: date) -> dict:
        """FRESH read, inclusive dates. Returns
        {"currency": "USD", "days": {"YYYY-MM-DD": {"price": float | None,   # MAJOR units
                                                    "min_stay": int | None,
                                                    "available": bool | None}}}
        Raise CannotWrite on anything unreadable; never return a partial calendar silently."""

    def write_calendar(self, listing_id: str, changes: dict, currency: str) -> None:
        """changes = {"YYYY-MM-DD": {"price": float (major units)?, "min_stay": int?}}.
        Convert to the vendor's unit and shape (cents vs dollars, date ranges vs single days).
        Raise CannotWrite("<Name> <METHOD> <path>: HTTP <code>") with NO response body."""

    def floor(self, listing_id: str) -> float | None:
        """The listing's own min price in major units if the PMS has one, else None
        (the core then uses property_config.settings.min_price)."""

    def pricing_managed(self, listing_id: str) -> str | None:
        """Name of the dynamic pricing tool controlling rates if the API says so, else None."""
```

Every endpoint used must be cited in `revenue-manager-plugin/references/<name>.md` with the doc URL
and the date read, and marked `VERIFIED-LIVE` or `DOCS-ONLY`. Nothing may be guessed.

## Test status the product must disclose

A target that has never written to a live account says so on the card: "first live write for
<Name>: read the after-values carefully". Today only Hospitable and PriceLabs can be live-tested.
