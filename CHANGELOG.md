# Changelog

## 5.0.1

- **Update your installed copy.** Claude Code only refreshes an installed plugin when its
  version number changes, so a copy installed from 5.0.0 keeps the old skill until you
  update it. Put the new files in the same folder you installed from (unzip the new zip over
  it, or `git pull` if you cloned it), then run
  `claude plugin marketplace update str-secrets-revenue-manager` and
  `claude plugin update revenue-manager@str-secrets-revenue-manager` (or just ask Claude to
  "update the revenue manager plugin"). New copy in a different folder? Run step 2 of
  `SETUP.md` from there instead. Then fully quit and reopen Claude Code.
- **All 8 PMSs read and write.** Hostaway, Lodgify, Uplisting, Smoobu and Hostfully join
  Hospitable, Guesty and OwnerRez in the runner and in setup (`--pms <name>`). With two PMSs
  connected, `--pms` is required and the run says so. Hospitable, Guesty and OwnerRez reads
  are live-tested; the other five are built from each vendor's API docs, and each
  `references/<pms>.md` says which is which, endpoint by endpoint.
- **PMS price changes through the safe writer.** `apply_change.py --target <pms>` writes the
  nightly price and min stay straight to the PMS calendar, with the same plan, yes, re-read
  and one-step undo as PriceLabs. It refuses a PMS price change on a listing PriceLabs or
  Beyond manages (the tool would overwrite it on its next sync). A PMS that applies changes
  late (Hospitable, OwnerRez, Uplisting) gets a read-only `verify` to check again later. No
  write has been live-tested yet, so every card says "first live write for" plus the tool's
  name, "read the after-values carefully".
- **Beyond.** Setup and the runner take `--pricing beyond`, and the writer takes
  `--target beyond`. The Beyond card prices and names everything Beyond's API doesn't give
  (market percentiles, the PriceLabs rule check, per-date min stay). Built from Beyond's
  docs. <!-- verify after feat/beyond merge -->
- **Writer fixes.** A PriceLabs override is checked against the live min and max; min, base
  and max are re-checked at apply; an undo is built from what's live now, skipping dates
  already back and dates in the past; the journal is always written; plans expire after 24
  hours and refuse past dates; any error is a plain "CANNOT WRITE", never a traceback.
  The bundled connectors in `mcp-servers/` match: PriceLabs overrides never spill onto child
  listings, and Hospitable's calendar tool sends the documented shape with a 20x sanity
  check.
- **Currency guard.** A Hospitable property in a currency without two decimals (JPY, KRW,
  VND, KWD, BHD and the like) is refused with a plain reason instead of reading 100x off.
- **Check-in/check-out day rules.** Lodgify and Smoobu don't share them, so that's now a
  named gap on the card ("nights are read as having none") instead of a calendar the run
  refuses.
- **Sync mismatches stay on their own date.** When the PMS and PriceLabs disagree on a
  night, only that night's pricing opinion is held back and listed at the top. The whole run
  stops only when more than 20% of open nights disagree.
- **Every run says what your min price should be.** Lower, raise or keep, with the number
  and the plain reason, capped at the 15% move and flagged if bigger. It never asks for a
  breakeven. For a property the PMS prices itself, setup saves the min with
  `--min-price "<property>=<amount>"`, and the writer won't cut a price there without one.
- **The zip and the GitHub copy are now the same files.** Both are built from the same
  commit, and a check refuses to ship a zip that differs by a single byte.
- **Markup is what you tell it.** The skill asks what markup you add per channel and stores
  exactly that. It never works a markup out from a gap between your PMS and PriceLabs.
- **Every price change goes through the safe writer** (plan, apply on a yes, re-read,
  rollback). A tool the writer can't reach yet gets the change as exact steps to do by hand,
  never a raw write.
- **RankBreeze uses its hosted MCP tool names**, and IntelliHost has its own short guide.
- **Setup and the runner use `uv run --python 3.13 python`**, same as the connections kit.
- **A shorter skill.** PMS notes and the long framework moved into `references/`. Every rule
  is still there.
- **Cleaner docs.** Setup order is install, fully quit and reopen, then first run. Example
  addresses and figures are neutral public examples. The old `standalone/` setup is marked
  legacy and no longer walks through the retired RankBreeze browser cookie.

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
- **Guesty and OwnerRez get the tested runner too.** The runner picks the connected PMS on
  its own, and each one was tested read-only on a real account.
- **IntelliHost as a ranking source.** Visibility and Ranking can come from IntelliHost when
  RankBreeze isn't there. A property without IntelliHost Premium gets a named gap, not a
  failure.
- **RankBreeze through its hosted MCP.** The funnel and rankings come from RankBreeze's
  official MCP, the one on every listing plan. No browser cookie.
- **No-rates mode.** If a PMS never exposes nightly prices, the run still checks bookings and
  availability and says it priced without the PMS rate. A PMS that drops only SOME prices is
  still refused.
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
