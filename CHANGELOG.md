# Changelog

## 5.0.0 (STR Secrets Summit 2.0)

- **The flywheel runs on every call.** Every property card opens with Visibility, Bookings,
  Reviews and Ranking, in that order. A missing piece still gets a price; the card says what
  it priced without, up top. Only a missing PMS calendar stops a property.
- **Min price is an answer, not a question.** Every run says what each listing's min should
  be. It never asks for a breakeven.
- **15% max move** (was 25%). Anything bigger is flagged loudly on the card.
- **Changes on a plain yes.** Every change is shown first, applied on a yes, then read back
  to prove it took. One yes can cover a batch. Nothing changes on its own.
- **Your PriceLabs rules get checked.** Last-minute, far-out, day-of-week and seasonality
  are each measured against the market: working, underperforming, or not enough data.
- **PriceLabs' own suggestions are kept.** Its actions and nudges are saved every run, as one
  input among many.
- **Hospitable + PriceLabs get a tested runner**, with a first-run setup that maps every
  property for you and tells you about any it can't.
- **Evening runs work.** After 5pm Pacific, PriceLabs' market data has already moved to
  tomorrow; the run now starts tomorrow and says so, instead of stopping.
- **Setup is the STR Secrets connections kit** plus a short `SETUP.md`. The old all-in-one
  setup lives in `standalone/`.

## 1.1.0 (plugin 4.1.0)

- **Fresh-Supabase fix.** The skill now bootstraps its own schema (Step 3.0). It checks for the
  four audit tables and applies migrations 001 + 002 itself when they're missing, so a brand-new,
  completely empty Supabase project works on the first run. Previously the pre-flight ran
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, which still errors with
  `relation "pricing_decisions" does not exist` when the table itself was never created.
- **Graceful degrade instead of a crash.** A read-only Supabase MCP, an `anon` key, or the wrong
  project no longer aborts the run. The skill warns once, disables audit logging, and delivers the
  full pricing analysis anyway.
- **Empty history is no longer treated as a failure.** A first run reports "no history yet, this
  run becomes your baseline" and continues instead of stopping.
- **Broader Supabase detection.** Any Supabase MCP flavour is recognised (`mcp__supabase__*`, a
  named server like `mcp__supabase-<name>__*`, or the connector flavour). A project-scoped server
  with no `list_projects` is correctly treated as working.
- **README:** real click-by-click Supabase account walkthrough, an explicit warning not to register
  the MCP with `--read-only`, `service_role` vs `anon` called out, and the manual SQL-Editor step
  removed as a requirement.

## 1.0.0

- First public release.
