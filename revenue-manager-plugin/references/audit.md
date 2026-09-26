# Audit writes (Step 9)

Part of the revenue-manager skill. SKILL.md says when to read this file. Only when a change actually happened, never on read-only analysis.


After a successful change, write to Supabase. **`apply_change.py apply` already writes the `pricelabs_change_log` rows for every change it applies** (unless run with `--no-audit`); do not insert them twice. You write the `pricing_decisions` row and the `market_snapshots` upsert. For a change the operator made by hand from your exact steps, write the change-log rows yourself once they confirm it is done. INSERT for append-only tables; UPSERT on `(property_id, snapshot_date)` for `market_snapshots`.

### Per change → 1 row in `pricelabs_change_log`
```sql
INSERT INTO pricelabs_change_log
  (property_name, listing_id, change_type, field_changed,
   old_value, new_value, reason, changed_by, notes)
VALUES
  ($1, $2, $3, $4, $5::text, $6::text, $7, 'revenue-manager-skill', $8);
```
One row per individual field change (base, min, max, a bound override, and each DSO/override date counts as its own row).

### Per property per decision → 1 row in `pricing_decisions` (outcome columns nullable, seeded null)
```sql
INSERT INTO pricing_decisions
  (property_id, decision_date, base_price, final_price,
   strategy, signals, reasoning, outcome,
   booked_at, lead_time_days, price_delta_from_rec)
VALUES ($1, CURRENT_DATE, $2, $3, $4, $5::jsonb, $6, 'executed',
        NULL, NULL, NULL);
```
`signals` is a jsonb array of the data points that drove the decision (comp percentile, comp count N, occupancy, STLY delta, ask-vs-cleared spread, etc). Leave `booked_at` / `lead_time_days` / `price_delta_from_rec` NULL — they seed the v2 learning loop and are populated later, not now.

### Per property per day → upsert to `market_snapshots`
```sql
INSERT INTO market_snapshots
  (property_id, snapshot_date, occupancy_pct, avg_comp_rate,
   demand_score, raw_data)
VALUES ($1, CURRENT_DATE, $2, $3, $4, $5::jsonb)
ON CONFLICT (property_id, snapshot_date) DO UPDATE SET
  occupancy_pct = EXCLUDED.occupancy_pct,
  avg_comp_rate = EXCLUDED.avg_comp_rate,
  demand_score  = EXCLUDED.demand_score,
  raw_data      = EXCLUDED.raw_data;
```

### Property config setup / update (bounds, markup, targets, seasons)
Write when the user configures markup, min/base/max bounds (including a plain-English bound override), targets, or season months:
```sql
INSERT INTO property_config
  (property_id, display_name, base_price, min_price, max_price, settings)
VALUES ($1, $2, $3, $4, $5, $6::jsonb)
ON CONFLICT (property_id) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  base_price   = EXCLUDED.base_price,
  min_price    = EXCLUDED.min_price,
  max_price    = EXCLUDED.max_price,
  settings     = EXCLUDED.settings;
```

**Recommended `settings` jsonb shape:**
```jsonc
{
  "channel_markup_pct": {"airbnb": 16, "vrbo": 20},  // operator-stated, per channel; never inferred. The runner reads this key.
  "max_delta_pct": 0.15,
  "pms_platform": "hospitable",
  "pricing_tool": "pricelabs",
  "pricelabs_listing_id": "...",
  "currency": "CAD",
  "target_occupancy_30d": 0.65,
  "peak_months":    [6, 7, 8, 12],
  "shoulder_months":[4, 5, 9, 10],
  "off_months":     [1, 2, 3, 11],
  "weekend_premium_pct": 0.30,        // 20–40% band; 0.30 is a starting default
  "notes": "markup is what the operator told us, per channel"
}
```

### If Supabase isn't connected
Skip the writes. At the end of the report, print:
```
⚠️ Audit logging skipped — Supabase not connected.
   To enable: run the STR Secrets connections kit ("Set up my connections"); its Supabase row
   (connectors/db-supabase.md) registers supabase-revenue-manager. Then fully restart Claude Code.
   You do NOT need to run any SQL by hand — connect a writable Supabase MCP and
   the skill creates all 4 tables itself on the next run (Step 3.0).
```
