# Revenue framework: pricing stack, lead time, decisions, reviews, red flags, KPIs, owner reports

Part of the revenue-manager skill. SKILL.md says when to read this file. Every rule here is still binding.

## 6.2 The Pricing Stack (build every rate from the base)

Rates build up from Base. Factors above add; factors below reduce. Min/Max are hard limits (the floor/ceiling guard, 2.1).

```
MAX PRICE         The ceiling. Peak-demand cap. Hitting it often → raise it.
EVENT BOOST       +15–40% for holidays, local events, school breaks, festivals.
WEEKEND PREMIUM   +20–40% for Fri/Sat nights.
SEASONAL FACTOR   Peak vs shoulder vs off: adjust to the market's pattern.
⭐ BASE PRICE ⭐    Anchor rate. Set from comp data. Mid-week, mid-season, average demand.
LAST MINUTE       -10–20% for dates within 7–14 days. Better to fill than earn $0.
ORPHAN DAY        -15–25% for isolated single nights between bookings.
MIN PRICE         The floor. The skill recommends what it should be (2.1); never ask for a breakeven.
```
Also: **far-out pricing +5–15% above base for dates 90+ days out** (early bookers are planners willing to pay more; you can always lower later).

**Min-stay defaults:** 2-night on weekends, 3-night on holidays, 1-night for last-minute / orphan fills.

## 6.3 Lead-Time Pricing Logic

| Days until check-in | Pricing approach | Signal |
|---|---|---|
| **90+ days** | Price 5–15% above base. Hold firm. | Early bookers pay more. No discount needed. |
| **60–90 days** | At or slightly above base. | Normal window. Monitor pacing vs last year. |
| **30–60 days** | At base. Start watching closely. | If behind pace, begin small adjustments here. |
| **14–30 days** | Evaluate carefully. Small drops if needed. | Decision window. Compare to comp availability. |
| **7–14 days** | Enable last-minute discounts (10–20%). | Still open → better to fill at a discount than $0. |
| **0–7 days** | Aggressive discounting if still open. | Drop minimums to 1 night. Accept 1-night stays. Fill at any reasonable rate. |

## 6.4 The Pricing Decision Framework (5 ordered questions)

For any date, ask these IN ORDER:
1. **What does the comp set say?** (PriceLabs neighborhood percentiles by bedroom; AirROI named comps if present.) 30% above comps and not booking is a signal.
2. **What does pacing look like?** Booked nights vs same period last year (STLY). Behind → consider down. Ahead → hold or raise.
3. **Are there events or demand spikes?** Local events, holidays, school breaks. Price into them; don't leave them at base.
4. **What is the lead time?** A date 90 days out unbooked is not urgent. The same date 14 days out unbooked is a problem. (See 6.3.)
5. **Are there orphan days?** Single-night gaps need special handling: drop minimums or discount to fill.

## 6.5 Comp-set discipline

A comp is a property **a guest would realistically choose instead of yours.** The test: *"Would a family of 6 looking for a weekend cabin realistically choose THAT property instead of ours?"* If it's not a clear yes, it's not a comp.

**Match on:** similar bedroom count (within 1 BR), similar key amenities (hot tub, pool, game room), similar guest capacity, similar location/drive time to attractions, similar quality and condition.
**Do NOT match on:** same zip code alone, just being the same property type with wildly different amenities, similar nightly rates (rate is an output, not a filter), same management company, similar review count.

Reading comp data: for each comp compare ADR, occupancy, revenue (= ADR × occ, the ultimate comparison), reviews, photos.

**Map to tools:** PriceLabs neighborhood data = the **aggregate** comp engine (percentiles, market occ, STLY). **AirROI (optional) = the named, qualitative** layer: specific competitors a guest would compare. Remember AirROI returns native local currency (called with `currency=native`; verify the echoed currency per gate 2.4) and never overrides PriceLabs silently.

## 6.6 The 30-Day Daily Review (the core habit, 6 steps)

When the operator runs a daily review, walk the next 30 days for every property:
1. **Open the calendar view** (PMS calendar = ground truth + PriceLabs forward curve). Scan for unbooked gaps, prices that look off, orphan days.
2. **Check pacing**: booked nights this month vs same month last year (STLY from PriceLabs neighborhood + PMS history). Ahead → hold/raise. Behind → investigate and adjust.
3. **Review recent bookings**: anything book in the last 24h? Booked right after a drop → dropped too far. Nothing booking despite availability → price may be too high (or a visibility problem: check ranking).
4. **Check the comp set**: what are comps charging for the same dates? In line, above, below? Still available or booked?
5. **Make adjustments**: specific changes, each with an articulable reason (and each passing the safety layer).
6. **Log everything**: every adjustment to Supabase (audit, Step 9). This is how patterns compound and how you report to owners.

## 6.7 Red-Flag auto-detection (compute each from PMS + PriceLabs data)

| Red flag | Detection rule (from your pulled data) | What to do |
|---|---|---|
| 5+ consecutive unbooked days within 14 days | Count consecutive `AVAILABLE` calendar days where the run starts ≤14 days out | Price too high OR visibility problem. Check comps; drop 10–15%; **check ranking first** (RankBreeze or IntelliHost, else manual). |
| Date booked within hours of going live | Reservation `created_at` − calendar/price-publish time < ~24h | Price was too low. Raise base + min for similar future dates. Money left on the table. |
| Orphan day sitting 7+ days | Single `AVAILABLE` night flanked by `RESERVED` on both sides, unbooked for 7+ days | Drop min-stay to 1; discount 15–25%; fill it. |
| All weekends booked, all weekdays empty | Fri/Sat occupancy high while Mon–Thu occupancy low over forward window | Weekday rates too high. Drop weekday rates; consider 5+ night discounts. |
| Comp set fully booked, you are not | PriceLabs market occupancy high (e.g. 75/90 percentile booked) while your forward occ is low | Overpriced or a listing/ranking issue. Match comp pricing; audit listing quality + ranking. |
| Comp set empty, you are booked | Your forward occ high while market occupancy low | Possibly underpriced: comps held firm and you didn't. Hold pricing longer before discounting next time. |

## 6.8 Troubleshooting playbook

- **Property not booking →** check **ranking FIRST** (RankBreeze or IntelliHost, else manual Airbnb search). If it's on page 5+, pricing isn't the primary problem; visibility is. Then check pricing vs comps (even 10–15% over can kill bookings), then listing quality, then min-stay.
- **Booked too fast →** prices were too low. Raise base + min for that range; check what comps are still charging; set a reminder to pre-adjust next year.
- **Seasonal transition →** start adjusting **30–45 days BEFORE** the season shifts, not after bookings dry up. Drop rates gradually, lower min-stay, enable aggressive last-minute discounts, refresh listing content for the new season.
- **Bad review hits →** read it carefully, fix the underlying issue immediately, respond professionally and briefly in public, push for 2–3 strong reviews to bury it, audit the guest-communication flow.

## 6.9 KPIs & benchmarks

| Metric | Target / benchmark |
|---|---|
| ADR (cleared) | At or above comp-set median. Track monthly trend. |
| Occupancy | **70–85% peak, 40–60% off-season** (market dependent). |
| RevPAR (ADR × occ) | The single best efficiency metric. Higher = better. |
| Booking pace vs STLY | Ahead → hold/raise. Behind → adjust down or promote. |
| Page views | **500–600 average; top performers 2,000–3,500+** in peak. (RankBreeze or IntelliHost if present, else manual.) |
| Click-through rate | Higher = better photos/title. Low → rotate photos / rewrite title. |
| Conversion rate | Low → pricing too high or listing content needs work. |
| Review score | Target **4.8+. Below 4.6 = a ranking problem.** |
| Response time | **Under 1 hour.** Automate initial responses. |

## Revenue levers (ranked by expected impact)

1. **Base price alignment**: biggest single lever
2. **Max price ceiling**: fully booked → ceiling too low
3. **Min price floor**: too many dates pinned to it → lower or trust the algo
4. **Seasonal DSOs**: holidays, events, peak/shoulder/off
5. **Min-stay rules**: turnover cost vs fill rate (defaults: 2-night weekends, 3-night holidays, 1-night last-minute/orphan)
6. **Last-minute discounts**: fill 3–7 day gaps
7. **Weekend premiums**: leisure markets

## Owner report output (Lead with wins → Context → Honest → Plan)

If the operator asks for an owner report (or you're producing a monthly summary), frame it as a story, not a data dump. Owners want: How am I doing? Why? What are you doing about it?

1. **Lead with wins**: higher ADR, more bookings than last year, strong review score.
2. **Provide context**: always compare to something, like last month, last year (STLY) or the comp-set average. Numbers without context are meaningless.
3. **Be honest**: if pacing is behind, say so plainly. Owners respect honesty over spin.
4. **Show the plan**: end with what you're doing next (rate adjustments, listing refresh, photo test).

Include: gross revenue, ADR (cleared), occupancy, bookings, comp comparison, and key actions taken. Keep it to 3–4 takeaways. Sample good framing: *"February was strong: revenue hit $4,200, up 18% over last February. ADR rose to $245 (from $215) thanks to comp-based adjustments. Occupancy held at 58%, right in line with the market. March pacing is solid and we've already adjusted rates for spring-break demand."*
