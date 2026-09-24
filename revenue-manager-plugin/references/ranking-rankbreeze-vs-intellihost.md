# RankBreeze vs IntelliHost: who has the better data

Measured 2026-09-24, read-only: RankBreeze on the Solnest Stays account (15 tools, all
measured earlier), IntelliHost on a Premium account (40 tools, all 33 reads called). They are
different accounts and properties, so this compares what each tool CAN tell you, not the same
listing side by side.

| What the revenue manager needs | RankBreeze | IntelliHost | Better |
|---|---|---|---|
| Search ranking | `get_listing_rankings`: daily, per guest count, page + position, date filters | `get-rank-series`: per scrape date x guest count (23 scrape dates in 90 days measured) | **RankBreeze** (daily) |
| Booking funnel (Visibility) | `get_listing_metrics_summary`: impressions, CTR, **views, wishlists**, booking rate, conversion, each vs similar listings | `get-funnel-dashboard`: first-page impressions, clicks, click rate, click-to-book, each vs comp, **per-step deficit + 90 daily rows** | Split: RankBreeze has more stages; IntelliHost diagnoses which step is short and goes daily |
| Comp market | `get_competitors_pricing`: each named competitor's daily calendar, 180 days | `get-comp-market`: **price distribution p1-p99, comp occupancy, comp lead time** per date, revenue rank vs comps | **IntelliHost** for the market picture; RankBreeze for named competitors |
| A second price engine | `get_price_recommendations`: 180 days, additive breakdown, priority rank | **Helix**: per-night booking probability at current vs proposed price, expected revenue; also passes through PriceLabs/Wheelhouse prices | **IntelliHost**, but only when a pricing provider is configured in it |
| Listing content and optimization | content + **competitor content**, optimization hub (photos, writer, amenities, policies, keywords, review analysis) | listing details, **audit with $ attribution**, AI title/description with scores | Split: RankBreeze sees competitors; IntelliHost puts dollars on each fix |
| What changed and did it work | `get_ab_tests_listing_history`: change journal + A/B tests with impact | `get-change-tracker`: each change with funnel-rate deltas and **estimated revenue impact** | Close; IntelliHost adds dollars |
| Reviews | own **and competitor** reviews | own reviews with ratings | **RankBreeze** |
| Bookings, revenue, pace, live prices, overrides, rules | none (host price calendar only) | reservations, revenue YoY, pickup, live channel prices, overrides, sync status, rules, alerts, market studies | **IntelliHost** (though the revenue manager already reads these from the PMS and pricing tool) |
| Can it change prices | no, read-only | yes, 7 write tools | IntelliHost (behind the plain-yes card only) |
| Access and cost | free on all listing plans; funnel needs the Airbnb integration active | per-property **Premium** for every per-property read; free accounts look connected but read nothing | **RankBreeze** |

## Verdict

- **Ranking spoke: RankBreeze.** Daily search position beats a series scraped every few days.
- **Visibility spoke: a split.** RankBreeze sees more funnel stages (views, wishlists).
  IntelliHost tells you which step is short against the comp set, day by day.
- **Market and pricing context: IntelliHost,** when Premium and a pricing provider are set up.
  Nothing on the RankBreeze side matches its comp distribution or booking probabilities.
- **For an operator choosing one:** RankBreeze is the cheaper, cleaner ranking and funnel feed.
  IntelliHost with Premium is closer to a whole revenue platform, but only for the properties
  that have Premium.
- **For the revenue manager:** both feed the same spokes. With RankBreeze, Visibility comes
  from the funnel summary and Ranking from daily rankings. With IntelliHost, Visibility comes
  from the funnel dashboard and Ranking from the rank series, and Helix and the comp market
  add one more voice. Never the basis, the same rule as PriceLabs' own suggestions.
