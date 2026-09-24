#!/usr/bin/env python3
"""Offline tests for the customization layer -- no API key, no network.

Guards the traps that make a customization write dangerous:
  1. ce is exact in total but must never be attributed to a single rule
     without the co-incidence test passing
  2. a market-driven rule's direction is unknowable, so it can never be
     "confirmed"
  3. day-of-week days omitted from a write reset to 0
  4. the sign is accepted either way, so an out-of-range or wrong-signed
     value must be caught before the request is built
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import attribution as at  # noqa: E402

FIX = json.load(open(HERE / "test_fixture_customizations.json"))
RULES = FIX["customizations"]
TODAY = "2026-09-18"
fails = []

def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}: {label}{'' if cond else '  -> ' + detail}")
    if not cond:
        fails.append(label)

print("customization layer smoke test\n")

# --- ce_rows ----------------------------------------------------------------
rows = at.ce_rows(FIX["price_rows"], TODAY)
check("sentinel and zero-denominator rows are dropped", len(rows) == 9,
      f"got {len(rows)}, want 9")
check("ce is price / uncustomized_price",
      abs(rows[0]["ce"] - 0.90) < 1e-9, f"got {rows[0]['ce']}")
check("2026-09-21 is Monday (dow 0)", rows[0]["dow"] == 0, f"got {rows[0]['dow']}")
check("days_out counts from today",
      rows[0]["days_out"] == 3, f"got {rows[0]['days_out']}")

# --- group_ce ---------------------------------------------------------------
by_dow = at.group_ce(rows, "dow")
check("Monday group median ce is 0.90",
      abs(by_dow["0"]["median_ce"] - 0.90) < 1e-9, f"got {by_dow['0']}")
check("Friday group median ce is 1.15",
      abs(by_dow["4"]["median_ce"] - 1.15) < 1e-9, f"got {by_dow['4']}")
check("Wednesday group is unmoved",
      abs(by_dow["2"]["median_ce"] - 1.00) < 1e-9, f"got {by_dow['2']}")

# --- rule_covers ------------------------------------------------------------
mon = rows[0]
wed = [r for r in rows if r["dow"] == 2][0]
check("day-of-week covers a day with a non-zero value",
      at.rule_covers("day_of_week_adjustment", RULES["day_of_week_adjustment"], mon))
check("day-of-week does NOT cover a day whose value is 0",
      not at.rule_covers("day_of_week_adjustment", RULES["day_of_week_adjustment"], wed))
check("last-minute covers dates inside its window",
      at.rule_covers("last_minute_prices", RULES["last_minute_prices"], mon))
far = dict(mon, days_out=200)
check("far-out covers dates past its start",
      at.rule_covers("far_out_premium", RULES["far_out_premium"], far))
check("far-out does not cover near dates",
      not at.rule_covers("far_out_premium", RULES["far_out_premium"], mon))
check("a rule toggled OFF still covers, because off is not off",
      at.rule_covers("seasonality", RULES["seasonality"], mon),
      "off hands the date to the market-driven default, which is still an effect")

# --- rule_direction ---------------------------------------------------------
check("negative day-of-week value reads as down",
      at.rule_direction("day_of_week_adjustment", RULES["day_of_week_adjustment"], mon) == "down")
check("positive day-of-week value reads as up",
      at.rule_direction("day_of_week_adjustment", RULES["day_of_week_adjustment"],
                        [r for r in rows if r["dow"] == 4][0]) == "up")
check("a market-driven type has unknown direction",
      at.rule_direction("demand_factor", RULES["demand_factor"]) == "unknown",
      "a market-driven rule's sign cannot be read from its config")

# --- off-handling for day_of_week_adjustment --------------------------------
# Create a copy with dow_factor_on = False, leaving stale per-day values untouched
dow_off = {k: v for k, v in RULES["day_of_week_adjustment"].items()}
dow_off["dow_factor_on"] = False
check("off day-of-week still covers a day with value=0",
      at.rule_covers("day_of_week_adjustment", dow_off, wed))
check("off day-of-week returns unknown direction for a day with negative stale value",
      at.rule_direction("day_of_week_adjustment", dow_off, mon) == "unknown",
      "an off rule is market-driven, so direction is unknowable")

# --- classify: the co-incidence test ----------------------------------------
affected = {r["date"] for r in rows if r["ce"] < 1.0}     # the four Mon/Tue dates
res = {c["rule"]: c for c in at.classify(affected, rows, RULES)}

check("day-of-week is CONFIRMED when it covers every affected date and no other",
      res["day_of_week_adjustment"]["verdict"] == "confirmed",
      f"got {res.get('day_of_week_adjustment')}")
check("last-minute is only a CANDIDATE: it also covers unaffected dates",
      res["last_minute_prices"]["verdict"] == "candidate",
      f"got {res.get('last_minute_prices')}")
check("a candidate reports how many unaffected dates it also covers",
      res["last_minute_prices"]["covered_unaffected"] > 0)
check("a market-driven rule can never be confirmed",
      res["demand_factor"]["verdict"] != "confirmed",
      "unknown direction must cap the verdict at candidate")
check("far-out is excluded entirely: it covers none of the affected dates",
      res["far_out_premium"]["verdict"] == "excluded",
      f"got {res.get('far_out_premium')}")
check("every rule gets a verdict, including the off ones",
      len(res) == 6, f"got {len(res)} rules classified, want 6")

# Test that off day_of_week_adjustment cannot be confirmed
rules_with_off_dow = {
    "day_of_week_adjustment": dow_off,
    "last_minute_prices": RULES["last_minute_prices"],
    "far_out_premium": RULES["far_out_premium"],
    "demand_factor": RULES["demand_factor"],
    "seasonality": RULES["seasonality"],
    "custom_seasonal_profile": RULES["custom_seasonal_profile"],
}
res_off = {c["rule"]: c for c in at.classify(affected, rows, rules_with_off_dow)}
check("off day-of-week is NOT confirmed (unknown direction caps at candidate)",
      res_off["day_of_week_adjustment"]["verdict"] != "confirmed",
      f"got {res_off['day_of_week_adjustment']['verdict']}")

# --- classify: coverage, and never claiming "excluded" from zero evidence ---
# A booked or blocked date renders as an empty price in reduce_prices, to_number("")
# is None, and ce_rows drops the row. Without a coverage field the verdicts are
# silently computed over whatever survived.
zero_ev = at.classify({"2026-12-24", "2026-12-25"}, rows, RULES)
zres = {c["rule"]: c for c in zero_ev}
check("zero usable ce rows -> every verdict is unknown, never excluded",
      all(c["verdict"] == "unknown" for c in zero_ev),
      f"got {sorted({c['verdict'] for c in zero_ev})}")
check("zero evidence still returns one verdict per rule", len(zero_ev) == 6,
      f"got {len(zero_ev)}")
check("zero evidence reports affected_with_ce == 0",
      zres["far_out_premium"]["coverage"]["affected_with_ce"] == 0,
      f"got {zres['far_out_premium']['coverage']}")
check("zero evidence still reports how many dates were asked about",
      zres["far_out_premium"]["coverage"]["affected_requested"] == 2,
      f"got {zres['far_out_premium']['coverage']}")

# Partial coverage is the quieter, worse case: ask about six dates, two computable.
partial_ask = affected | {"2026-12-24", "2026-12-25"}
pres = {c["rule"]: c for c in at.classify(partial_ask, rows, RULES)}
check("partial coverage discloses requested vs computable",
      pres["day_of_week_adjustment"]["coverage"] == {
          "affected_requested": len(partial_ask), "affected_with_ce": len(affected)},
      f"got {pres['day_of_week_adjustment']['coverage']}")
check("every verdict carries coverage, not just the interesting ones",
      all("coverage" in c for c in res.values()),
      "a verdict without coverage cannot be surfaced in Step 5.5")
check("full coverage reports requested == with_ce",
      res["day_of_week_adjustment"]["coverage"]["affected_requested"]
      == res["day_of_week_adjustment"]["coverage"]["affected_with_ce"])

# --- reduce_customizations: normalization -----------------------------------
import reduce_customizations as rc  # noqa: E402

# --- actions and nudges are ACCOUNT-WIDE under a per-listing header ---------
# Synthetic ids. Never put a real listing UUID in a committed file -- the repo
# is public, and the leak scan flags the 8-4-4-4-12 hex SHAPE, so even a made-up
# UUID trips it. Opaque strings exercise the same code paths.
MINE = "listing-mine-0001"
THEIRS = "listing-theirs-0002"
ACTIONS_FIX = [
    {"listing_details": {"listing_id": MINE, "pms": "smartbnb", "listing_name": "Mine"},
     "actions": [{"action_type": "min_price_alert", "title": "t",
                  "metadata": {"current": {"price": 1}, "recommended": {"price": 2}}}]},
    {"listing_details": {"listing_id": THEIRS, "pms": "smartbnb", "listing_name": "Theirs"},
     "actions": [{"action_type": "min_price_alert", "title": "t",
                  "metadata": {"current": {"price": 425}, "recommended": {"price": 403}}}]},
]
NUDGES_FIX = {"nudges": [{"listing_id": THEIRS, "pms_name": "smartbnb",
                          "listing_name": "Theirs", "nudge_type": "min_price",
                          "nudge_id": "min_1", "current_value": 425,
                          "suggested_value": 403, "direction": "decrease",
                          "reason": "r", "expiration": "2026-09-24T04:33:59.000Z",
                          "status": "pending"}]}

arows = rc.flatten_actions(ACTIONS_FIX, MINE)
check("an action for another listing is scoped OTHER-LISTING, not printed as ours",
      [r[0] for r in arows] == ["this-listing", "OTHER-LISTING"],
      f"got {[r[0] for r in arows]}")
check("every action row carries the listing that owns it",
      arows[1][2] == "Theirs", f"got {arows[1][:3]}")
check("foreign() counts the strays for the header", rc.foreign(arows) == 1,
      f"got {rc.foreign(arows)}")
check("an action row has one cell per ACTION_COLUMNS",
      all(len(r) == len(rc.ACTION_COLUMNS) for r in arows))

nrows = rc.flatten_nudges(NUDGES_FIX, MINE)
check("a nudge belonging to another property is scoped OTHER-LISTING",
      nrows[0][0] == "OTHER-LISTING", f"got {nrows[0][0]}")
check("nudge reads nudge_type, not the non-existent `field`",
      nrows[0][5] == "min_price", f"got {nrows[0][5]!r}")
check("nudge reads expiration, not the non-existent `expires_at`",
      nrows[0][10].startswith("2026-09-24"), f"got {nrows[0][10]!r}")
check("nudge carries listing_id and pms_name, both required by accept_nudge",
      nrows[0][1] == THEIRS and nrows[0][2] == "smartbnb", f"got {nrows[0][1:3]}")
check("a nudge row has one cell per NUDGE_COLUMNS",
      len(nrows[0]) == len(rc.NUDGE_COLUMNS),
      f"got {len(nrows[0])} vs {len(rc.NUDGE_COLUMNS)}")
check("a nudge for THIS listing scopes as ours",
      rc.flatten_nudges(NUDGES_FIX, THEIRS)[0][0] == "this-listing")

norm = {r["rule"]: r for r in rc.normalize_rules(RULES)}
check("all six rules normalize, including the off ones", len(norm) == 6,
      f"got {sorted(norm)}")
check("an OFF rule is flagged in upper case so it cannot be skimmed past",
      norm["seasonality"]["toggle"] == "OFF", f"got {norm['seasonality']['toggle']!r}")
check("an ON rule reads lower case", norm["demand_factor"]["toggle"] == "on")
check("the day-of-week value lists all seven days",
      norm["day_of_week_adjustment"]["value"].count("=") == 7,
      f"got {norm['day_of_week_adjustment']['value']!r}")
check("the last-minute window is expressed in days from check-in",
      norm["last_minute_prices"]["window"] == "<=14d",
      f"got {norm['last_minute_prices']['window']!r}")
check("the far-out window is expressed as days out",
      norm["far_out_premium"]["window"] == ">=180d",
      f"got {norm['far_out_premium']['window']!r}")
check("no `effective` column is rendered: the API does not return that field",
      "effective" not in norm["last_minute_prices"] and "effective" not in rc.RULE_COLUMNS,
      f"got columns {rc.RULE_COLUMNS}")
check("a dormant seasonal profile reports its stored season count",
      "2 seasons" in norm["custom_seasonal_profile"]["window"],
      f"got {norm['custom_seasonal_profile']['window']!r}")

# a rule row must render every declared column, and nothing else
bare = {"demand_factor": {"tone_demand_factor_on": True, "tone_demand_factor": "recommended"}}
check("a rule row carries exactly the declared columns",
      sorted(rc.normalize_rules(bare)[0]) == sorted(rc.RULE_COLUMNS),
      f"got {sorted(rc.normalize_rules(bare)[0])}")

# a toggle that came back as a string must not read as ON
check("a toggle returned as the STRING 'false' renders OFF, not on",
      rc.normalize_rules({"seasonality": {"seasonality_customization_on": "false"}})[0]["toggle"]
      == "OFF",
      "bool('false') is True in Python, and an off rule is the dangerous one")
check("a toggle returned as the STRING 'true' still renders on",
      rc.normalize_rules({"seasonality": {"seasonality_customization_on": "true"}})[0]["toggle"]
      == "on")

# --- clean_text: the shared free-text helper behind all five vendor-text columns ----
check("clean_text renders an explicit None as the fallback, not the string 'None'",
      rc.clean_text(None) == "", f"got {rc.clean_text(None)!r}")
check("clean_text renders an empty string as the fallback too",
      rc.clean_text("") == "", f"got {rc.clean_text('')!r}")
check("clean_text honors a custom fallback for None",
      rc.clean_text(None, "(no effective block returned)") == "(no effective block returned)")
check("clean_text scrubs an embedded newline in real text",
      rc.clean_text("line one\nline two") == "line one line two",
      f"got {rc.clean_text('line one' + chr(10) + 'line two')!r}")

# title (flatten_actions) and name (flatten_profiles): the two sites the reviewer
# proved print the literal text "None" for an explicit null. Both must now render an
# empty cell for a null AND for a missing key -- .get() makes those indistinguishable
# by the time clean_text sees them, so both need their own check.
actions_null_title = {"data": [{"actions": [{"action_type": "x", "title": None, "metadata": {}}]}]}
actions_missing_title = {"data": [{"actions": [{"action_type": "x", "metadata": {}}]}]}
# Index by NAME, never by position. These two checks were written when `title` was
# column 1; adding the scope/listing_id/listing_name columns moved it to 4, and the
# checks silently started asserting on the (empty) listing_id instead -- passing
# vacuously while the guard they exist for went untested.
_TITLE = rc.ACTION_COLUMNS.index("title")
check("flatten_actions renders an explicit null title as an empty cell, not 'None'",
      rc.flatten_actions(actions_null_title)[0][_TITLE] == "",
      f"got {rc.flatten_actions(actions_null_title)[0][_TITLE]!r}")
check("flatten_actions renders a missing title as an empty cell",
      rc.flatten_actions(actions_missing_title)[0][_TITLE] == "",
      f"got {rc.flatten_actions(actions_missing_title)[0][_TITLE]!r}")
check("the title check is anchored to the column NAME, so a reorder cannot hide it",
      rc.flatten_actions({"data": [{"actions": [
          {"action_type": "x", "title": "real title", "metadata": {}}]}]})[0][_TITLE]
      == "real title")

profiles_null_name = {"profiles": {"minstay": [{"id": 1, "name": None, "archived": False}]}}
profiles_missing_name = {"profiles": {"minstay": [{"id": 1, "archived": False}]}}
check("flatten_profiles renders an explicit null name as an empty cell, not 'None'",
      rc.flatten_profiles(profiles_null_name)[0][2] == "",
      f"got {rc.flatten_profiles(profiles_null_name)[0][2]!r}")
check("flatten_profiles renders a missing name as an empty cell",
      rc.flatten_profiles(profiles_missing_name)[0][2] == "",
      f"got {rc.flatten_profiles(profiles_missing_name)[0][2]!r}")

# --- factcheck round trip ----------------------------------------------------
import io  # noqa: E402
import factcheck as fc  # noqa: E402

check("customizations is a registered fact source", "customizations" in fc.SOURCES)

full = fc.customization_facts_full({"customizations": RULES})
check("full extractor counts all six rules", full["rules_total"] == 6, f"got {full}")
check("full extractor counts the off ones", full["rules_off"] == 2, f"got {full}")
check("full extractor sums the day-of-week magnitude",
      abs(full["dow_abs_total"] - 50.0) < 1e-9,
      f"got {full['dow_abs_total']}, want 50 (10+10+15+15)")
check("full extractor records the dormant season count",
      full["stored_seasons"] == 2, f"got {full['stored_seasons']}")

# render the same rules through the reducer's own table, then read it back
buf = io.StringIO()
buf.write("## rules\n")
w = __import__("csv").writer(buf, lineterminator="\n")
w.writerow(rc.RULE_COLUMNS)
for r in rc.normalize_rules(RULES):
    w.writerow([r[c] for c in rc.RULE_COLUMNS])
reduced = fc.customization_facts_reduced(buf.getvalue())
bad = fc.compare(full, reduced, fc.CUSTOMIZATION_FACTS)
check("every customization fact survives the reducer", not bad, f"changed: {bad}")

# --- fix round 1, finding 1: sums/counts are blind to mislabeling -----------
# dow_abs_total is a sum and rules_on/off are counts. Both are invariant under
# permutation: swap which day carries which value, or which two rules are off, and the
# number does not move. Only a digest over the actual (label, value) pairs can see it.

swap_buf = io.StringIO()
swap_buf.write("## rules\n")
sw = __import__("csv").writer(swap_buf, lineterminator="\n")
sw.writerow(rc.RULE_COLUMNS)
for r in rc.normalize_rules(RULES):
    row = dict(r)
    if row["rule"] == "day_of_week_adjustment":
        # simulate a reducer bug that trades Monday's and Friday's printed values while
        # every day label stays in its normal position: abs(-10)+abs(15) either way, so
        # dow_abs_total cannot see this, but dow_digest must.
        row["value"] = "mon=15 tue=-10 wed=0 thu=0 fri=-10 sat=15 sun=0"
    sw.writerow([row[c] for c in rc.RULE_COLUMNS])
swap_reduced = fc.customization_facts_reduced(swap_buf.getvalue())
check("a day-value permutation leaves dow_abs_total unchanged (the blind spot)",
      abs(swap_reduced["dow_abs_total"] - full["dow_abs_total"]) < 1e-9,
      f"full={full['dow_abs_total']} swapped={swap_reduced['dow_abs_total']}")
swap_bad = fc.compare(full, swap_reduced, fc.CUSTOMIZATION_FACTS)
check("but dow_digest catches the permutation",
      any(b.startswith("dow_digest") for b in swap_bad), f"got bad={swap_bad}")

rules_swap_buf = io.StringIO()
rules_swap_buf.write("## rules\n")
rsw = __import__("csv").writer(rules_swap_buf, lineterminator="\n")
rsw.writerow(rc.RULE_COLUMNS)
for r in rc.normalize_rules(RULES):
    row = dict(r)
    if row["rule"] == "seasonality":             # really OFF in RULES
        row["toggle"] = "on"
    elif row["rule"] == "demand_factor":          # really on in RULES
        row["toggle"] = "OFF"
    rsw.writerow([row[c] for c in rc.RULE_COLUMNS])
rules_swap_reduced = fc.customization_facts_reduced(rules_swap_buf.getvalue())
check("swapping which two rules are off leaves rules_on/rules_off unchanged (the blind spot)",
      rules_swap_reduced["rules_on"] == full["rules_on"]
      and rules_swap_reduced["rules_off"] == full["rules_off"],
      f"got on={rules_swap_reduced['rules_on']} off={rules_swap_reduced['rules_off']}")
rules_swap_bad = fc.compare(full, rules_swap_reduced, fc.CUSTOMIZATION_FACTS)
check("but rules_digest catches which specific rules moved",
      any(b.startswith("rules_digest") for b in rules_swap_bad), f"got bad={rules_swap_bad}")

# --- fix round 1, finding 2: :g print-precision must not cause a false mismatch --
# the reducer prints each day value with :g (6 significant digits). Comparing a raw
# full-precision float against that truncation with exact != is a false mismatch waiting
# to happen. PRECISION exists for exactly this; dow_abs_total and dow_digest must use it.

hp_rules = {**RULES, "day_of_week_adjustment": dict(RULES["day_of_week_adjustment"],
                                                     dow_factor_value_mon=14.285714285714286)}
hp_full = fc.customization_facts_full({"customizations": hp_rules})
hp_buf = io.StringIO()
hp_buf.write("## rules\n")
hpw = __import__("csv").writer(hp_buf, lineterminator="\n")
hpw.writerow(rc.RULE_COLUMNS)
for r in rc.normalize_rules(hp_rules):
    hpw.writerow([r[c] for c in rc.RULE_COLUMNS])
hp_reduced = fc.customization_facts_reduced(hp_buf.getvalue())
hp_bad = fc.compare(hp_full, hp_reduced, fc.CUSTOMIZATION_FACTS)
check("a high-precision day value (14.285714285714286 prints as '14.2857') round-trips "
      "without a false mismatch",
      not hp_bad, f"changed: {hp_bad}")

# --- fix round 1, finding 3: sentinel collision on customization config values ----
# attribution.SENTINELS = {-1, -2} is PriceLabs' "no value" marker on a PRICE field. Every
# customization config reader used to run through to_number(), which applies that filter
# to percentages too -- but -1%/-2% is an ordinary, real value there (documented range
# -75..1000), not "no data". to_setting() is the sentinel-free config parser; to_number()
# now serves price fields only.

check("to_setting(-1.0) returns -1.0, not None (a real -1% setting is not a price sentinel)",
      at.to_setting(-1.0) == -1.0, f"got {at.to_setting(-1.0)!r}")
check("to_setting(-2.0) returns -2.0, not None",
      at.to_setting(-2.0) == -2.0, f"got {at.to_setting(-2.0)!r}")
check("to_setting(None) is still None (genuinely missing stays missing)",
      at.to_setting(None) is None)
check("to_setting('not a number') is still None (unparseable stays unparseable)",
      at.to_setting("not a number") is None)

# reading: a live -1% Monday must be seen as covering the date and pushing it down, not
# read as an absent/market-driven rule
live_dow = {"dow_factor_on": True,
            "dow_factor_value_mon": -1.0, "dow_factor_value_tue": -2.0,
            "dow_factor_value_wed": 0.0, "dow_factor_value_thu": 0.0,
            "dow_factor_value_fri": 0.0, "dow_factor_value_sat": 0.0,
            "dow_factor_value_sun": 0.0}
check("a live -1% Monday is read as covering the date, not sentinel-stripped to absent",
      at.rule_covers("day_of_week_adjustment", live_dow, mon))
check("a live -1% Monday reads its real direction (down), not collapsed to 'none'",
      at.rule_direction("day_of_week_adjustment", live_dow, mon) == "down")

# reading through the printed table: the reducer must render -1/-2, not blank them to 0
live_norm = rc.normalize_rules({"day_of_week_adjustment": live_dow})[0]
check("the reducer prints a live -1% Monday as -1, not silently blanked to 0",
      "mon=-1" in live_norm["value"], f"got {live_norm['value']!r}")
check("the reducer prints a live -2% Tuesday as -2, not silently blanked to 0",
      "tue=-2" in live_norm["value"], f"got {live_norm['value']!r}")

# writing: the read-modify-write hazard. customization_write.merge_dow (Task 4) has not
# been built yet, but the shape of the bug does not need it -- ANY code that carries a
# day's CURRENT value forward through the wrong parser during a partial update will zero
# it. Reproduce that exact read-modify-write shape with the correct parser and prove the
# live values survive; a real merge_dow, once built, must show the same result.
def _simulate_partial_write(current: dict, changes: dict, parser) -> dict:
    return {k: (changes[k] if k in changes else parser(current.get(k)) or 0.0)
            for k in at.DOW_KEYS}

merged = _simulate_partial_write(live_dow, {"dow_factor_value_sat": 20.0}, at.to_setting)
check("a live -1% Monday survives a partial day-of-week write untouched, not zeroed",
      merged["dow_factor_value_mon"] == -1.0, f"got {merged['dow_factor_value_mon']}")
check("a live -2% Tuesday survives a partial day-of-week write untouched, not zeroed",
      merged["dow_factor_value_tue"] == -2.0, f"got {merged['dow_factor_value_tue']}")
check("the actually-changed Saturday value applies",
      merged["dow_factor_value_sat"] == 20.0, f"got {merged['dow_factor_value_sat']}")

# the identical read-modify-write shape using the OLD price parser reproduces the exact
# bug the reviewer found. Kept as a permanent regression guard: if this ever stops
# zeroing mon/tue, to_number()'s SENTINELS set changed underneath this test.
broken = _simulate_partial_write(live_dow, {"dow_factor_value_sat": 20.0}, at.to_number)
check("using the price parser on config data reproduces the exact bug (why the split matters)",
      broken["dow_factor_value_mon"] == 0.0 and broken["dow_factor_value_tue"] == 0.0,
      f"got mon={broken['dow_factor_value_mon']} tue={broken['dow_factor_value_tue']}")

# the PRICE-field meaning of -1/-2 must be unchanged by this fix: ce_rows still drops them
check("PRICE-field -1/-2 sentinels are still dropped by ce_rows (unchanged by this fix)",
      not any(r["date"] in ("2026-10-01", "2026-10-02") for r in rows),
      f"got dates={[r['date'] for r in rows]}")

# --- customization_write -----------------------------------------------------
import customization_write as cw  # noqa: E402

# the trap: a partial day-of-week write silently zeroes the days you left out
partial = {"dow_factor_value_fri": 20.0, "dow_factor_value_sat": 20.0}
merged = cw.merge_dow(RULES["day_of_week_adjustment"], partial)
check("merge_dow emits all seven days", sum(1 for k in merged if k.startswith("dow_factor_value")) == 7,
      f"got {sorted(k for k in merged if k.startswith('dow_factor_value'))}")
check("merge_dow preserves days the caller did not mention",
      merged["dow_factor_value_mon"] == -10.0,
      "a partial write would have reset Monday to 0")
check("merge_dow applies the days the caller did mention",
      merged["dow_factor_value_fri"] == 20.0)
check("merge_dow keeps the toggle", merged["dow_factor_on"] is True)

# range validation, all-or-nothing
check("a day-of-week value below -75 is rejected",
      cw.validate({"day_of_week_adjustment": dict(merged, dow_factor_value_mon=-80)}),
      "-80 is outside the -75..1000 range and must not reach the API")
check("a day-of-week value of 1000 is allowed",
      not cw.validate({"day_of_week_adjustment": dict(merged, dow_factor_value_mon=1000)}))
check("a last-minute discount over 75 is rejected",
      cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                          "last_min_factor_type": "linear",
                                          "last_min_factor_value": -80,
                                          "last_min_factor_dfd": 7}}))
check("a far-out start over 999 is rejected",
      cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                       "far_out_premium_type": "linear",
                                       "far_out_premium_value": 10,
                                       "far_out_premium_start": 1500,
                                       "far_out_premium_step": 1}}))
check("a valid payload returns no errors",
      not cw.validate({"day_of_week_adjustment": merged}), f"got {cw.validate({'day_of_week_adjustment': merged})}")
check("an unknown rule name is rejected rather than sent",
      cw.validate({"not_a_rule": {}}))

# The echo check: the API accepts either sign, so confirm against the CONFIG FIELDS on a
# fresh re-read. There is no `effective` block -- measured live, GET
# /v1/customizations/listing returns no such key anywhere in the payload.
MON_DOWN = {"dow_factor_on": True, "dow_factor_value_mon": -10.0}
check("echo_diff is silent when the re-read field matches intent",
      not cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                        "magnitude": 10, "day": "Mon"}, MON_DOWN))
check("echo_diff catches a discount that came back as a premium",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                    "magnitude": 10, "day": "Mon"},
                   {"dow_factor_on": True, "dow_factor_value_mon": 10.0}),
      "this is the inverted-sign failure and it returns HTTP 200")
check("echo_diff reports an empty re-read rather than passing it",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                    "magnitude": 10, "day": "Mon"}, {}))
check("echo_diff does NOT depend on an `effective` key, which the live API never returns",
      not cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                        "magnitude": 10, "day": "Mon"}, MON_DOWN),
      "a real re-read carries no `effective`; requiring one made Step 8 unpassable")
check("echo_diff refuses an intent that does not name its rule",
      cw.echo_diff({"direction": "down", "magnitude": 10, "day": "Mon"}, MON_DOWN))

# the action sign convention is INFERRED, not proven, so it is gated
value, confirmed = cw.signed_from_action(
    {"action_type": "last_minute_conservative_vs_market",
     "metadata": {"current": {"discount_pct": -12.0}, "recommended": {"discount_pct": 40.0}}})
check("an action's recommended discount is negated into a signed value",
      value == -40.0, f"got {value}, want -40.0 (a 40% discount, not a 40% premium)")
check("the conversion reports that it is unconfirmed",
      confirmed is False, "the convention is inferred and must not be auto-applied")

# --- customization_write: merge_dow regression guard (not in the brief) -----------
# The brief's interface line named `attribution._num`, which has never existed in shipped
# code -- attribution.py exposes to_number() (price fields, filters PriceLabs' -1/-2 "no
# value" sentinels) and to_setting() (customization config fields, no sentinel filtering,
# because -1%/-2% is an ordinary day-of-week value; see live_dow and
# _simulate_partial_write above). customization_write.merge_dow is built on to_setting().
# This closes the gap the comment above _simulate_partial_write called out explicitly:
# prove the REAL merge_dow does what the simulation only modeled -- a live -1%/-2% day
# survives a partial day-of-week write. If to_setting() were ever swapped back for
# to_number() inside merge_dow, to_number()'s SENTINELS filter would read -1.0/-2.0 as
# "no value", fall through to the `or 0.0`-style default, and this check would fail.
live_merged = cw.merge_dow(live_dow, {"dow_factor_value_sat": 20.0})
check("merge_dow preserves a live -1% Monday through a partial write, not zeroed",
      live_merged["dow_factor_value_mon"] == -1.0,
      f"got {live_merged['dow_factor_value_mon']}; to_number() here would silently wipe it")
check("merge_dow preserves a live -2% Tuesday through a partial write, not zeroed",
      live_merged["dow_factor_value_tue"] == -2.0,
      f"got {live_merged['dow_factor_value_tue']}; to_number() here would silently wipe it")
check("merge_dow still applies the day that was actually changed",
      live_merged["dow_factor_value_sat"] == 20.0, f"got {live_merged['dow_factor_value_sat']}")

# --- customization_write: fix round 1 (coordinator review, Groups A-D) -----------
# Independently re-verified against the shipped module before implementing any fix (see
# the fix report). All ten findings reproduced as described, with one correction: the
# literal claim that listing_id="../../x" "escapes out_dir" did not reproduce -- it
# raised FileNotFoundError instead (the "snapshot_" prefix glued onto the crafted value
# breaks a clean ".." path component in every craft tried). The underlying concern is
# still real (an unsanitized listing_id can crash write_snapshot in a confusing way) so
# the sanitization fix ships anyway; see the fix report for the full evidence.

# Group A: validate() must not return [] for the exact failures it exists to prevent.
# These four payloads are the coordinator's own, used verbatim.
check("A1: a partial day-of-week write (Fri only, no toggle restated) is rejected",
      cw.validate({"day_of_week_adjustment": {"dow_factor_value_fri": 20.0}}),
      "THE MEASURED WIPE: 6 unstated days would reset to 0 on a live calendar")
check("A2: an out-of-range last-minute value/dfd is rejected even with no toggle stated",
      cw.validate({"last_minute_prices": {"last_min_factor_value": -80,
                                          "last_min_factor_dfd": 900}}),
      "-80 exceeds the 75-point discount cap and 900 exceeds the 90-day dfd cap")
check("A4: last_min_factor_type='fix' (the far_out enum) is rejected -- INVERSE ENUM TRAP",
      cw.validate({"last_minute_prices": {"last_min_factor_value": -20,
                                          "last_min_factor_dfd": 14,
                                          "last_min_factor_type": "fix"}}),
      "last_minute_prices uses `fixed`; `fix` is the far_out_premium spelling")
check("A(4th payload): a stray dow_factor_value_* key survives seven valid days undetected no more",
      cw.validate({"day_of_week_adjustment": dict(
          RULES["day_of_week_adjustment"], dow_factor_value_monday=-80.0)}),
      "the typo key is not one of the seven canonical DOW_KEYS and must not be invisible")

# A3: an unknown or missing far_out_premium_type used to silently skip every check.
check("A3: far-out value 9999 and start -50 are rejected when the type is missing",
      cw.validate({"far_out_premium": {"far_out_premium_value": 9999,
                                       "far_out_premium_start": -50}}),
      "a missing type used to skip range checks entirely, not just the type check")

# A3/design correctness: a market-driven type is a legitimate, real PriceLabs value and
# must NOT be flagged as "unknown" just because it carries no numeric field to
# range-check. Written to guard the else-branch added for A3 from becoming a new false
# positive. Toggle included so the assertion isolates the type check (fix round 3: a
# bare type with no toggle now also trips the F4 toggle-required check, which is a
# different failure mode than what these two tests exist to guard).
check("a market-driven far_out_premium_type ('recommended') is not flagged as unknown",
      not cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                           "far_out_premium_type": "recommended"}}),
      f"got {cw.validate({'far_out_premium': {'far_out_premium_on': True, 'far_out_premium_type': 'recommended'}})}")
check("a market-driven last_min_factor_type ('conservative') is not flagged as unknown",
      not cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                              "last_min_factor_type": "conservative"}}))

# a genuinely unrecognized type (not the enum trap, not market-driven, not a real type)
# must still be caught -- this is what A3's else branch is actually for
check("a genuinely unknown far_out_premium_type is rejected",
      cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                       "far_out_premium_type": "moonbeam",
                                       "far_out_premium_value": 5}}),
      "the else branch added for A3 must still fire on real garbage")

# --- Group B: echo_diff must not be blind to a per-day inversion -----------------
# The seven days are seven independent fields. Confirming the wrong one confirms nothing,
# so a day-of-week echo without a day is refused rather than guessed at.
DOW_AFTER = {"dow_factor_on": True,
             "dow_factor_value_mon": -10.0, "dow_factor_value_tue": -10.0,
             "dow_factor_value_wed": 0.0, "dow_factor_value_thu": 0.0,
             "dow_factor_value_fri": 15.0, "dow_factor_value_sat": 15.0,
             "dow_factor_value_sun": 0.0}

check("B1: echo_diff scoped to Fri catches a discount intent against an actual Fri premium",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                    "magnitude": 15, "day": "Fri"}, DOW_AFTER),
      "Mon/Tue being discounts must not mask Friday's inversion")
check("B1: echo_diff scoped to Fri is silent when the intent (premium) matches",
      not cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "up",
                        "magnitude": 15, "day": "Fri"}, DOW_AFTER))
check("B1: echo_diff scoped to Mon is silent when the intent (discount) matches",
      not cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                        "magnitude": 10, "day": "Mon"}, DOW_AFTER))
check("B1: echo_diff scoped to Mon catches a premium intent against an actual Mon discount",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "up",
                    "magnitude": 10, "day": "Mon"}, DOW_AFTER))
check("B1: a day that came back at 0 reads as cleared, not as confirmed",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                    "magnitude": 10, "day": "Wed"}, DOW_AFTER),
      "Wednesday is 0.0: the adjustment was cleared, and silence would hide that")
check("B1: a day-of-week echo with no day is refused, never guessed",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                    "magnitude": 10}, DOW_AFTER))
check("B1: a missing day field is reported, not treated as 0",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                    "magnitude": 10, "day": "Mon"},
                   {"dow_factor_on": True, "dow_factor_value_fri": 15.0}))
check("B1: a single-statement rule needs no day",
      not cw.echo_diff({"rule": "last_minute_prices", "direction": "down",
                        "magnitude": 12},
                       {"last_min_factor_on": True, "last_min_factor_type": "linear",
                        "last_min_factor_value": -12.0}))

# B2: a direction that is neither "down" nor "up" used to skip every check and return []
check("B2: echo_diff rejects an unrecognized direction rather than silently passing a "
      "literal inversion",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "sideways",
                    "magnitude": 10, "day": "Mon"},
                   {"dow_factor_on": True, "dow_factor_value_mon": 10.0}),
      "direction='sideways' used to make every branch a no-op and return []")

# B3 (Critical 4 follow-ons): the shapes the runbook itself recommends
check("B3: the suppression write (type `none`, toggle ON) does not raise KeyError",
      not cw.echo_diff({"rule": "last_minute_prices", "direction": "none"},
                       {"last_min_factor_on": True, "last_min_factor_type": "none"}),
      "echo_diff used to require intent['magnitude'] and died on the runbook's own case")
check("B3: a suppression that did NOT take is reported",
      cw.echo_diff({"rule": "last_minute_prices", "direction": "none"},
                   {"last_min_factor_on": True, "last_min_factor_type": "linear",
                    "last_min_factor_value": -12.0}))
check("B3: a market-driven type is never reported as a confirmed directional write",
      cw.echo_diff({"rule": "far_out_premium", "direction": "up", "magnitude": 20},
                   {"far_out_premium_on": True, "far_out_premium_type": "recommended",
                    "far_out_premium_value": 20.0}),
      "PriceLabs sets the number; the value carries no sign we wrote")
check("B3: a toggle that came back as the STRING 'false' reads as OFF",
      cw.echo_diff({"rule": "last_minute_prices", "direction": "down", "magnitude": 12},
                   {"last_min_factor_on": "false", "last_min_factor_type": "linear",
                    "last_min_factor_value": -12.0}),
      "`if \"false\":` is True in Python and would read an OFF rule as live")
check("B3: a rule with no single signed number is never reported as echo-checked",
      cw.echo_diff({"rule": "custom_seasonal_profile", "direction": "up",
                    "magnitude": 10}, {"seasons": []}))
check("B3: a live -1% setting survives the echo check (to_setting, not to_number)",
      not cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "down",
                        "magnitude": 1, "day": "Mon"},
                       {"dow_factor_on": True, "dow_factor_value_mon": -1.0}),
      "-1 is a PRICE sentinel, never a config sentinel")

# --- Group F: the string-toggle bug, fixed in 4 modules and missed in the 5th ----
# bool("false") is True. attribution.py is the module that ISSUES the verdicts and it
# was the one reading its toggles raw, so an OFF rule came back `confirmed`.
_F_ROWS = [{"date": "2026-10-05", "ce": 0.90, "dow": 0, "days_out": 16, "month": "2026-10"},
           {"date": "2026-10-06", "ce": 0.90, "dow": 1, "days_out": 17, "month": "2026-10"}]
_F_AFFECTED = {"2026-10-05", "2026-10-06"}
_F_DOW = {"dow_factor_value_mon": -10.0, "dow_factor_value_tue": -10.0,
          "dow_factor_value_wed": 0.0, "dow_factor_value_thu": 0.0,
          "dow_factor_value_fri": 0.0, "dow_factor_value_sat": 0.0,
          "dow_factor_value_sun": 0.0}


def _dow_verdict(toggle):
    cfg = dict(_F_DOW, dow_factor_on=toggle)
    return {c["rule"]: c for c in
            at.classify(_F_AFFECTED, _F_ROWS, {"day_of_week_adjustment": cfg})
            }["day_of_week_adjustment"]


for _tog in (False, "false", "False", "FALSE", 0, "", None):
    check(f"F: an OFF day-of-week toggle ({_tog!r}) can never reach `confirmed`",
          _dow_verdict(_tog)["verdict"] != "confirmed",
          f"got {_dow_verdict(_tog)} -- an off rule is market-driven, direction unknowable")
for _tog in (True, "true", "True", 1):
    check(f"F: an ON day-of-week toggle ({_tog!r}) still confirms",
          _dow_verdict(_tog)["verdict"] == "confirmed",
          f"got {_dow_verdict(_tog)} -- the guard must not break the working case")
check("F: rule_direction reads a string 'false' as unknown for last-minute",
      at.rule_direction("last_minute_prices",
                        {"last_min_factor_on": "false", "last_min_factor_type": "linear",
                         "last_min_factor_value": -12.0}, _F_ROWS[0]) == "unknown")
check("F: rule_direction reads a string 'false' as unknown for far-out",
      at.rule_direction("far_out_premium",
                        {"far_out_premium_on": "false", "far_out_premium_type": "linear",
                         "far_out_premium_value": 20.0}, _F_ROWS[0]) == "unknown")
check("F: attribution exposes the same toggle reader as the other four modules",
      at.toggle_is_on("false") is False and at.toggle_is_on("true") is True
      and at.toggle_is_on(False) is False and at.toggle_is_on(True) is True)

# --- Group G: validate() and echo_diff cells the suite never reached -------------
check("G: a legal toggle-off arriving as the STRING 'false' is not flagged incomplete",
      not cw.validate({"day_of_week_adjustment": {"dow_factor_on": "false"}}),
      "this is the 'just turn it off' write, and it was blocked")
check("G: a toggle-off as a bool is still not flagged",
      not cw.validate({"day_of_week_adjustment": {"dow_factor_on": False}}))
check("G: a toggle ON with zero days is STILL caught (the round-2 fix must survive)",
      cw.validate({"day_of_week_adjustment": {"dow_factor_on": True}}))
check("G: a toggle ON as the string 'true' with zero days is caught too",
      cw.validate({"day_of_week_adjustment": {"dow_factor_on": "true"}}))
for _bad in ("oops", ["a"], 7):
    check(f"G: a non-dict rule config ({type(_bad).__name__}) returns an error, never raises",
          cw.validate({"day_of_week_adjustment": _bad}),
          "validate()'s contract is to RETURN problems, not raise them")
check("G: clearing a day to 0 (direction='none') confirms instead of returning gibberish",
      not cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "none", "day": "Fri"},
                       {"dow_factor_on": True, "dow_factor_value_fri": 0}),
      "day-of-week has no type_key, so the suppression branch short-circuited on None")
check("G: a day that did NOT clear is reported",
      cw.echo_diff({"rule": "day_of_week_adjustment", "direction": "none", "day": "Fri"},
                   {"dow_factor_on": True, "dow_factor_value_fri": 12.0}))
check("G: a non-numeric magnitude is reported, never raised",
      cw.echo_diff({"rule": "last_minute_prices", "direction": "down", "magnitude": "20%"},
                   {"last_min_factor_on": True, "last_min_factor_type": "linear",
                    "last_min_factor_value": -20.0}),
      "float('20%') used to raise ValueError out of the Step 8 echo check")
check("G: a seasonal write carrying ONLY non_repeating_seasons still warns",
      cw.destructive_warnings({"custom_seasonal_profile": {
          "custom_seasonal_profile_on": True,
          "custom_seasonal_profile": {"non_repeating_seasons": [{"a": 1}]}}}),
      "that list is the other half of the stored set and it was invisible")
check("G: MARKET_DRIVEN is the plain set, not an identity intersection",
      cw.MARKET_DRIVEN == {"recommended", "conservative", "aggressive"})

# --- Group H: the printed window must agree with the verdict engine --------------
_H_CFG = {"last_min_factor_on": True, "last_min_factor_type": "linear",
          "last_min_factor_value": -12.0, "last_min_factor_dfd": 0}
check("H: a zero-day last-minute window prints as <=0d, not as the whole horizon",
      rc.rule_window("last_minute_prices", _H_CFG) == "<=0d",
      f"got {rc.rule_window('last_minute_prices', _H_CFG)!r}")
check("H: and attribution scopes it the same way",
      at.rule_covers("last_minute_prices", _H_CFG, {"days_out": 0, "dow": 4})
      and not at.rule_covers("last_minute_prices", _H_CFG, {"days_out": 91, "dow": 4}),
      "the table and the verdict engine must not disagree about one rule")
check("H: a zero-day far-out window prints as >=0d",
      rc.rule_window("far_out_premium", {"far_out_premium_start": 0}) == ">=0d",
      f"got {rc.rule_window('far_out_premium', {'far_out_premium_start': 0})!r}")
check("H: an ABSENT threshold still prints as the whole horizon",
      rc.rule_window("last_minute_prices", {"last_min_factor_on": True}) == "all")

# --- Group I: the price sentinel set ---------------------------------------------
check("I: the string forms '-1.0'/'-2.0' are price sentinels too",
      at.to_number("-1.0") is None and at.to_number("-2.0") is None,
      "factcheck.SENTINELS already had them; attribution and reduce_prices did not")
check("I: to_setting still keeps -1/-2, which are REAL config percentages",
      at.to_setting(-1.0) == -1.0 and at.to_setting("-2") == -2.0,
      "this split is the whole point of having two parsers")

# --- Group E: factcheck must not invent a mismatch on an absent dow rule ---------
import factcheck as fc  # noqa: E402

NO_DOW_RAW = {"customizations": {"seasonality": {"seasonality_customization_on": True,
                                                 "seasonality_type": "recommended"}}}
NO_DOW_TXT = ("## rules\nrule,toggle,type,value,window\n"
              "seasonality,on,recommended,-,all\n")
f_full = fc.customization_facts_full(NO_DOW_RAW)
f_red = fc.customization_facts_reduced(NO_DOW_TXT)
check("E1: a listing with no day-of-week rule produces no factcheck mismatch",
      f_full == f_red,
      f"differ on {[k for k in f_full if f_full[k] != f_red[k]]}")
check("E1: an absent dow rule means no pairs, not seven zeros",
      f_full["dow_digest"] == f_red["dow_digest"],
      "seven (day, 0.0) pairs on the full side never matched the reduced side's empty list")

DOW_RAW = {"customizations": {"day_of_week_adjustment": {
    "dow_factor_on": True, "dow_factor_value_mon": -10.0, "dow_factor_value_tue": 0.0,
    "dow_factor_value_wed": 0.0, "dow_factor_value_thu": 0.0,
    "dow_factor_value_fri": 12.0, "dow_factor_value_sat": 12.0,
    "dow_factor_value_sun": 0.0}}}
DOW_TXT = ("## rules\nrule,toggle,type,value,window\n"
           "day_of_week_adjustment,on,-,mon=-10 tue=0 wed=0 thu=0 fri=12 sat=12 sun=0,"
           '"mon,fri,sat"\n')
check("E1: a real dow rule still cross-checks clean",
      fc.customization_facts_full(DOW_RAW) == fc.customization_facts_reduced(DOW_TXT),
      "the empty-list fix must not stop the digest catching a real drift")
check("E1: a changed dow value still fails the cross-check",
      fc.customization_facts_full(DOW_RAW)["dow_digest"]
      != fc.customization_facts_reduced(DOW_TXT.replace("fri=12", "fri=13"))["dow_digest"],
      "the digest must still be load-bearing")
check("E2: a toggle returned as the STRING 'false' counts as off on the full side",
      fc.customization_facts_full(
          {"customizations": {"seasonality": {"seasonality_customization_on": "false"}}}
      )["rules_on"] == 0,
      "bool('false') is True, so the full side read it on while the reducer read it off")

# --- Group D: gated fields nest, and every seasonal write is destructive ---------
CSP_GATED = {"custom_seasonal_profile": {
    "custom_seasonal_profile_on": True,
    "custom_seasonal_profile": {"price_type": "percentage",
                                "seasons": [{"season_name": "x", "inherit_cico": True}]}}}
check("D1: a feature-gated field NESTED inside a season is caught",
      any("inherit_cico" in e for e in cw.validate(CSP_GATED)),
      f"got {cw.validate(CSP_GATED)}  -- one gated field rejects the WHOLE request")
check("D1: the error names the dotted path, not just the rule",
      any("seasons[0].inherit_cico" in e for e in cw.validate(CSP_GATED)),
      f"got {cw.validate(CSP_GATED)}")
check("D1: a gated field one level down (not in a list) is caught too",
      any("non_repeating_seasons" in e for e in cw.validate(
          {"custom_seasonal_profile": {"custom_seasonal_profile_on": True,
                                       "custom_seasonal_profile": {
                                           "non_repeating_seasons": [{"a": 1}]}}})))
check("D1: all four inherit_* fields are gated, per the vendor spec",
      {"inherit_baseprice", "inherit_cico", "inherit_minstay",
       "inherit_priceprofile"} <= cw.FEATURE_GATED,
      f"got {sorted(cw.FEATURE_GATED)}")
check("D1: a clean nested payload still validates",
      not cw.validate({"custom_seasonal_profile": {
          "custom_seasonal_profile_on": True,
          "custom_seasonal_profile": {"price_type": "percentage",
                                      "seasons": [{"season_name": "x", "start_month": "1",
                                                   "start_day": "1", "end_month": "2",
                                                   "end_day": "28"}]}}}),
      "the recursive scan must not invent errors on a good payload")

WIPE = {"custom_seasonal_profile": {"custom_seasonal_profile_on": True,
                                    "custom_seasonal_profile": {"seasons": []}}}
check("D2: an empty seasonal replacement also raises a destructive warning",
      any("EMPTY" in w for w in cw.destructive_warnings(WIPE)),
      f"got {cw.destructive_warnings(WIPE)}")
check("D2: an enabled seasonal profile cannot have both season arrays empty",
      any("at least one season" in error for error in cw.validate(WIPE)),
      "the bundled API schema requires at least one season; warnings do not make it valid")
check("D2: any seasonal write warns that it replaces the WHOLE set",
      any("REPLACES the whole season set" in w for w in cw.destructive_warnings(
          {"custom_seasonal_profile": {"custom_seasonal_profile_on": True,
                                       "custom_seasonal_profile": {
                                           "seasons": [{"season_name": "x"}]}}})),
      "there is no partial-season write, so a toggle-off is not the only dangerous shape")
check("D2: a payload that does not touch the seasonal profile stays quiet",
      not any("custom_seasonal_profile" in w for w in cw.destructive_warnings(
          {"day_of_week_adjustment": {"dow_factor_on": True}})))

check("D3: a toggle-off arriving as the STRING 'false' still warns",
      cw.destructive_warnings({"last_minute_prices": {"last_min_factor_on": "false"}}),
      "bool('false') is True, so the warning was skipped on exactly the write "
      "that needed it")
check("D3: a toggle ON as the STRING 'true' does not warn",
      not cw.destructive_warnings({"last_minute_prices": {"last_min_factor_on": "true"}}))

# --- Group C: the kill switch -----------------------------------------------------
import os  # noqa: E402
import tempfile as _tempfile  # noqa: E402
import datetime as _datetime_module  # noqa: E402

# C1: snapshot_payload must deep-copy, not alias, current
c1_source = {"day_of_week_adjustment": {"dow_factor_value_mon": -10.0}}
c1_snap = cw.snapshot_payload("cz-listing1", "smartbnb", c1_source)
c1_source["day_of_week_adjustment"]["dow_factor_value_mon"] = 999.0
check("C1: snapshot_payload deep-copies current -- mutating the source afterward does "
      "not change the snapshot",
      c1_snap["customizations"]["day_of_week_adjustment"]["dow_factor_value_mon"] == -10.0,
      f"got {c1_snap['customizations']['day_of_week_adjustment']['dow_factor_value_mon']}, "
      "want -10.0 -- a live reference would let the rollback restore the BROKEN state")

# C2: write_snapshot needs real coverage -- round trip, atomic write, no clobber, no
# path escape. Zero of this existed before fix round 1.
with _tempfile.TemporaryDirectory() as c2_tmp:
    c2_payload = cw.snapshot_payload("cz-listing2", "smartbnb",
                                     {"day_of_week_adjustment": {"dow_factor_value_mon": -5.0}})
    c2_path = cw.write_snapshot(c2_payload, c2_tmp)
    check("write_snapshot returns a path that exists", os.path.isfile(c2_path))
    check("write_snapshot leaves no stray .tmp file behind after a clean write",
          not os.path.exists(c2_path + ".tmp"))
    c2_roundtrip = json.load(open(c2_path))
    check("write_snapshot round-trips the exact payload (the rollback restores exactly "
          "what was snapshotted)",
          c2_roundtrip == c2_payload, f"got {c2_roundtrip}")

    # force a deterministic same-instant collision by freezing the module's clock --
    # two real calls microseconds apart would almost never collide on their own, which
    # would make this test flaky rather than a real guarantee
    class _FrozenDatetime(_datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return _datetime_module.datetime(2026, 1, 1, 12, 0, 0, 0, tzinfo=tz)

    _real_datetime = cw.datetime
    cw.datetime = _FrozenDatetime
    try:
        c2_frozen_payload = cw.snapshot_payload("cz-frozen", "smartbnb", {"x": 1})
        c2_first_path = cw.write_snapshot(c2_frozen_payload, c2_tmp)
        collision_exc = None
        try:
            cw.write_snapshot(c2_frozen_payload, c2_tmp)
        except FileExistsError as exc:
            collision_exc = exc
        check("a second write at the identical microsecond raises, never overwrites",
              collision_exc is not None,
              "write_snapshot must refuse to clobber an existing snapshot file")
        check("the collision raises a clear operator-facing message, not a bare OS error",
              collision_exc is not None and "must not proceed" in str(collision_exc),
              f"got: {collision_exc}")
        check("the original snapshot is unchanged after the refused second write",
              json.load(open(c2_first_path)) == c2_frozen_payload)
    finally:
        cw.datetime = _real_datetime

    # sanitization: a crafted listing_id must never change which directory the file
    # lands in, whatever the exact OS-level failure mode of the unsanitized version was
    c2_traversal_payload = cw.snapshot_payload("../../x", "smartbnb", {})
    c2_traversal_path = cw.write_snapshot(c2_traversal_payload, c2_tmp)
    check("a crafted listing_id cannot change which directory write_snapshot writes into",
          os.path.dirname(os.path.abspath(c2_traversal_path)) == os.path.abspath(c2_tmp),
          f"got {c2_traversal_path}")
    check("write_snapshot succeeds (no confusing crash) on a crafted listing_id",
          os.path.isfile(c2_traversal_path))

# --- Group D: merge_dow ------------------------------------------------------------
# D1: an absent dow_factor_on must not silently default to True (or False) -- raise.
try:
    cw.merge_dow({}, {"dow_factor_value_mon": -10.0})
    d1_raised = False
except ValueError:
    d1_raised = True
check("D1: merge_dow refuses to guess an absent dow_factor_on rather than defaulting "
      "it to True",
      d1_raised,
      "a missing toggle silently defaulting to True would enable a rule with no evidence")

# D2: the toggle round-trip must be proven both ways, not just the True fixture value --
# the original test passed identically against a version that hardcoded True.
d2_off_current = dict(RULES["day_of_week_adjustment"], dow_factor_on=False)
d2_off_merged = cw.merge_dow(d2_off_current, {"dow_factor_value_fri": 5.0})
check("D2: merge_dow preserves an explicit dow_factor_on=False, not just True",
      d2_off_merged["dow_factor_on"] is False,
      f"got {d2_off_merged['dow_factor_on']}; the old test only ever proved True round-trips")

# D3: an unrecognized key in `changes` must raise, not vanish silently. Reproduces the
# coordinator's exact typo ('_monday' vs '_mon') through the real merge_dow, the layer
# that is supposed to prevent this shape from ever reaching validate() at all.
try:
    cw.merge_dow(RULES["day_of_week_adjustment"], {"dow_factor_value_monday": -80.0})
    d3_raised = False
except ValueError:
    d3_raised = True
check("D3: merge_dow raises on an unrecognized key in changes rather than dropping it",
      d3_raised,
      "a typo'd key would otherwise vanish silently: not applied, not flagged, and "
      "invisible to validate() too -- a successful write that changed nothing")

# --- customization_write: fix round 2 (coordinator review) ------------------------
# Round 1 fixed the toggle-gating anti-pattern in validate() (range/type checks running
# even when the caller only restated the fields they changed) but the same rewrite --
# "check if the toggle is on" -> "check what is present" -- silently converted every
# required-FIELD-missing case into a pass, because an absent field is never present.
# Presence is the right gate for a range check (only check what you were given) and the
# wrong gate for a completeness check (the whole point is detecting what you were NOT
# given). All ground truth below is read directly from
# references/pricelabs-api/customizations.md, not paraphrased.

# Axis 1 (fix round 3, F4, supersedes round 2's version): {toggle absent, True,
# False} x {day count 0-7}, 24 cells, expected outcome DERIVED from ground truth, not
# hardcoded per cell -- this is the "loop over toggle x type x field-subset" shape the
# round-3 review asked for, applied to day-of-week.
#
# Round 2's version of this sweep asserted "0/7 days with NO toggle key is clean" --
# that was wrong: references/pricelabs-api/customizations.md's POST body tables mark
# dow_factor_on `req = Y` (a required body field), so an absent toggle is a malformed
# request regardless of how many days are present. Independently reproduced against
# the shipped module before this fix: it returned [] for both 0 and 7 days with no
# toggle key, confirming the round-2 test's claim was false and has been corrected.
#
# The remaining logic is unchanged from round 2: toggle True + <7 days is caught
# (0 days is THE CRITICAL hole that fix closed); toggle False + 0 days is clean (F4's
# OTHER half -- day-of-week keeps its stored values on toggle-off, so "just turn it
# off" with nothing else restated is legitimate, and this is the cell that locks in
# truthiness over presence for cfg.get("dow_factor_on") inside the completeness check).
for _toggle in (None, True, False):     # None = dow_factor_on key absent entirely
    for _n in range(8):
        _cfg = {cw.DOW_KEYS[i]: -10.0 for i in range(_n)}
        if _toggle is not None:
            _cfg["dow_factor_on"] = _toggle
        _result = cw.validate({"day_of_week_adjustment": _cfg})
        if _toggle is None:
            _expect_clean = False                  # F4: toggle is a required field
        elif _n == 7:
            _expect_clean = True                    # a complete write, any toggle value
        elif _n == 0:
            _expect_clean = not _toggle              # off+0 days legit; on+0 days is the hole
        else:
            _expect_clean = False                   # 1-6 days always caught, any toggle
        _label = (f"day sweep: toggle={_toggle!r} days={_n} -> expect "
                 f"{'clean' if _expect_clean else 'caught'}")
        if _expect_clean:
            check(_label, not _result, f"got {_result}")
        else:
            check(_label, _result, f"must be caught, got {_result}")

# Axis 2: one test per omitted-required-field shape from the coordinator's table, all
# ground-truthed against references/pricelabs-api/customizations.md's own "Required
# for ..." text on each field, not against the coordinator's paraphrase of it.
check("last-minute linear with value but no dfd is rejected (dfd: \"Required for "
      "linear/linear_gradual/fixed\")",
      cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                          "last_min_factor_type": "linear",
                                          "last_min_factor_value": -20}}),
      "last_min_factor_dfd omitted must not silently pass")
check("far-out linear with value but no start/step is rejected (both: \"Required for "
      "linear\")",
      cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                       "far_out_premium_type": "linear",
                                       "far_out_premium_value": 25}}),
      "far_out_premium_start and _step omitted must not silently pass")
check("far-out fix with value but no start is rejected (\"Required for linear/fix\")",
      cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                       "far_out_premium_type": "fix",
                                       "far_out_premium_value": 25}}),
      "far_out_premium_start omitted must not silently pass, even for type fix")
check("far-out linear with start+step but no value is rejected (\"Required for "
      "linear/fix\")",
      cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                       "far_out_premium_type": "linear",
                                       "far_out_premium_start": 180,
                                       "far_out_premium_step": 1}}),
      "far_out_premium_value omitted must not silently pass")

# Axis 3: anti-over-correction guards, alongside each axis-2 case, so the next person
# cannot "fix" a missed required field by making the validator reject everything.
check("a COMPLETE linear last-minute payload (value+dfd both present) still returns []",
      not cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                              "last_min_factor_type": "linear",
                                              "last_min_factor_value": -20,
                                              "last_min_factor_dfd": 14}}))
check("a valid `fixed` last-minute payload still returns []",
      not cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                              "last_min_factor_type": "fixed",
                                              "last_min_factor_value": 50,
                                              "last_min_factor_dfd": 7}}))
check("a COMPLETE linear far-out payload (value+start+step all present) still returns []",
      not cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                           "far_out_premium_type": "linear",
                                           "far_out_premium_value": 25,
                                           "far_out_premium_start": 180,
                                           "far_out_premium_step": 1}}))
check("a valid `fix` far-out payload with NO step (step is not required for fix) "
      "still returns []",
      not cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                           "far_out_premium_type": "fix",
                                           "far_out_premium_value": 25,
                                           "far_out_premium_start": 180}}))
check("a full valid seven-day write still returns []",
      not cw.validate({"day_of_week_adjustment": dict(
          RULES["day_of_week_adjustment"], dow_factor_value_mon=-10.0)}))
# F5 (fix round 3) corrects this loop: attribution.MARKET_DRIVEN is a superset across
# all four type fields -- moderately_conservative/moderately_aggressive are real
# values on seasonality and demand_factor ONLY, per docs/pricelabs/customer-api.json's
# resolved enums. The six-spelling loop that USED to run here asserted both were also
# valid for last_min_factor_type and far_out_premium_type; independently reproduced
# against the shipped module before this fix -- they validated clean, which PriceLabs
# would have rejected outright (all-or-nothing write). Split by field below, checked
# against the authoritative per-field constant, not the shared superset.
for _spelling in cw.LAST_MIN_FACTOR_TYPES - {"linear", "linear_gradual", "fixed"}:
    check(f"last-minute type {_spelling!r} (LAST_MIN_FACTOR_TYPES, non-concrete) "
          "still returns [] (no value/dfd required for these)",
          not cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                                   "last_min_factor_type": _spelling}}),
          f"got {cw.validate({'last_minute_prices': {'last_min_factor_on': True, 'last_min_factor_type': _spelling}})}")
for _spelling in cw.FAR_OUT_PREMIUM_TYPES - {"linear", "fix"}:
    check(f"far-out type {_spelling!r} (FAR_OUT_PREMIUM_TYPES, non-concrete) "
          "still returns [] (no value/start/step required for these)",
          not cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                               "far_out_premium_type": _spelling}}))
# the direct regression guard: these two spellings are real PriceLabs values, but only
# on seasonality/demand_factor (exercised further down) -- last-minute and far-out
# must REJECT them, which is exactly what round 2's tests got backwards.
for _spelling in ("moderately_conservative", "moderately_aggressive"):
    check(f"last-minute REJECTS {_spelling!r} (valid on seasonality/demand_factor "
          "only, not here -- round 2 wrongly asserted otherwise)",
          cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                              "last_min_factor_type": _spelling}}))
    check(f"far-out REJECTS {_spelling!r} (valid on seasonality/demand_factor only, "
          "not here -- round 2 wrongly asserted otherwise)",
          cw.validate({"far_out_premium": {"far_out_premium_on": True,
                                           "far_out_premium_type": _spelling}}))

# --- customization_write: fix round 3 (coordinator review) ------------------------
# F5's authoritative enums come from docs/pricelabs/customer-api.json (resolving
# CapiLastMinutePricesLastMinFactorType, CapiFarOutPremiumFarOutPremiumType,
# CapiSeasonalitySeasonalityType, CapiDemandFactorToneDemandFactor). F1/F2's
# toggle-off-resets claim comes from references/pricelabs-gotchas.md. F4's
# every-toggle-is-required claim comes from references/pricelabs-api/
# customizations.md's own POST body tables. All independently re-read and confirmed
# before writing any fix; see the fix report for the primary-source quotes.

# F1: re-enabling last-minute/far-out with ONLY the toggle restated (no type) is the
# same hole as day-of-week's zero-days case (fixed in round 2), and just as dangerous:
# toggling either off resets its stored config to a zeroed state (last-minute to type
# linear/value 0; far-out to value 0/start 999), so re-enabling with the toggle alone
# leaves that live. Independently reproduced against the shipped (pre-round-3) module
# before this fix -- both returned [].
check("F1: last_minute_prices toggle True alone (no type/value/dfd) is caught",
      cw.validate({"last_minute_prices": {"last_min_factor_on": True}}),
      "re-enabling with only the toggle leaves a live 0% rule (toggle-off resets to "
      "type linear, value 0)")
check("F1: far_out_premium toggle True alone (no type/value/start/step) is caught",
      cw.validate({"far_out_premium": {"far_out_premium_on": True}}),
      "toggle-off resets far-out to value 0 / start 999; re-enabling with only the "
      "toggle leaves that live")

# F2: toggling last-minute/far-out OFF is a LEGAL write that DESTROYS the stored
# configuration and returns HTTP 200 -- validate() correctly does not (cannot) block
# it, since it is not invalid, but it must not be silent either. Lives in its own
# channel so validate()'s list[str]/empty-means-safe contract is unchanged for Task 7.
check("F2: validate() does not (and should not) block a toggle-off write -- it is "
      "legal, not invalid",
      not cw.validate({"last_minute_prices": {"last_min_factor_on": False}}))
check("F2: destructive_warnings() catches the last-minute toggle-off that validate() "
      "correctly lets through",
      cw.destructive_warnings({"last_minute_prices": {"last_min_factor_on": False}}),
      "a legal write that resets stored config must not be silent")
check("F2: destructive_warnings() catches the far-out toggle-off too",
      cw.destructive_warnings({"far_out_premium": {"far_out_premium_on": False}}))
check("F2: destructive_warnings() is silent for day-of-week toggle-off "
      "(it keeps its stored values, not destructive)",
      not cw.destructive_warnings({"day_of_week_adjustment": {"dow_factor_on": False}}))
check("F2: destructive_warnings() is silent for seasonality toggle-off "
      "(it keeps its stored values too)",
      not cw.destructive_warnings({"seasonality": {"seasonality_customization_on": False}}))
check("F2: destructive_warnings() is silent when the toggle is True (not a toggle-off)",
      not cw.destructive_warnings({"last_minute_prices": {"last_min_factor_on": True}}))
check("F2: destructive_warnings() is silent when the rule is not present at all",
      not cw.destructive_warnings({}))
check("F2: validate()'s contract is unchanged by adding destructive_warnings() -- "
      "still list[str], still empty on a clean payload",
      cw.validate({"day_of_week_adjustment": dict(
          RULES["day_of_week_adjustment"], dow_factor_value_mon=-10.0)}) == [])

# F4, explicit named lock-in (beyond the day-sweep loop above, which covers this cell
# implicitly at toggle=False/days=0): truthiness, not presence, is the correct gate
# for the day-of-week toggle inside the COMPLETENESS check specifically.
# {"dow_factor_on": False} with zero days is a legitimate, minimal "just turn it off"
# write -- day-of-week keeps its stored per-day values on toggle-off, unlike
# last-minute/far-out (see F2 above). A future "tightening" of
# cfg.get("dow_factor_on") to "dow_factor_on" in cfg would break this exact legal
# write with a fully green suite otherwise; this module has shipped that class of bug
# twice already (rounds 1 and 2).
check("F4: an explicit dow_factor_on=False with zero days is a legitimate toggle-off, "
      "not flagged as incomplete",
      not cw.validate({"day_of_week_adjustment": {"dow_factor_on": False}}),
      f"got {cw.validate({'day_of_week_adjustment': {'dow_factor_on': False}})}")

# F4: every rule's own toggle is a required body field -- confirmed generically
# across all six rules, including custom_seasonal_profile, not just day-of-week.
for _rule, _toggle_key in cw.TOGGLE_KEY.items():
    check(f"{_rule}: an empty payload is rejected for missing {_toggle_key} "
          "(every *_on field is `req = Y` per the POST body tables)",
          cw.validate({_rule: {}}),
          f"an empty {_rule} object is missing its required toggle")

# F3: seasonality and demand_factor previously had no rule-specific validation at all
# (only the generic FEATURE_GATED scan). Both now get: toggle-required (F4, tested
# generically above), type-required-when-touched (the same "touched" shape as the
# other three rules), and enum validation against their OWN authoritative set --
# which is also where moderately_conservative/moderately_aggressive actually ARE
# valid (contrast with the last-minute/far-out REJECTS tests above).
check("F3: seasonality with a bogus seasonality_type is rejected",
      cw.validate({"seasonality": {"seasonality_customization_on": True,
                                   "seasonality_type": "zzz"}}),
      "a bogus type used to validate clean and would kill the whole request at the API")
check("F3: seasonality toggle True alone (no type restated) is rejected",
      cw.validate({"seasonality": {"seasonality_customization_on": True}}),
      "re-enabling with no type restated must not silently pass")
for _spelling in cw.SEASONALITY_TYPES:
    check(f"F3/F5: seasonality accepts {_spelling!r} (the full authoritative enum, "
          "incl. moderately_conservative/moderately_aggressive)",
          not cw.validate({"seasonality": {"seasonality_customization_on": True,
                                           "seasonality_type": _spelling}}),
          f"got {cw.validate({'seasonality': {'seasonality_customization_on': True, 'seasonality_type': _spelling}})}")

check("F3: demand_factor with a bogus tone_demand_factor is rejected",
      cw.validate({"demand_factor": {"tone_demand_factor_on": True,
                                     "tone_demand_factor": "zzz"}}))
check("F3: demand_factor toggle True alone (no type restated) is rejected",
      cw.validate({"demand_factor": {"tone_demand_factor_on": True}}))
for _spelling in cw.TONE_DEMAND_FACTOR_TYPES:
    check(f"F3/F5: demand_factor accepts {_spelling!r} (the full authoritative enum, "
          "incl. the space-separated 'no demand factor')",
          not cw.validate({"demand_factor": {"tone_demand_factor_on": True,
                                             "tone_demand_factor": _spelling}}),
          f"got {cw.validate({'demand_factor': {'tone_demand_factor_on': True, 'tone_demand_factor': _spelling}})}")

# Generalized test axis (the reviewer's explicit ask): a loop over {toggle absent,
# True, False} x {each concrete type} x {required fields present, partially present,
# absent}. This is what would have caught rounds 1 and 2 before the coordinator did.
# Closes two named coverage gaps directly: linear_gradual had zero missing-field
# coverage (it is one of the three concrete types iterated below), and far-out
# `linear` with value+start but no step -- the likeliest real operator slip, copying
# a `fix` config to `linear` -- is the "no_step" subset below.
_LAST_MIN_CONCRETE = ("linear", "linear_gradual", "fixed")
# The value is TYPE-DEPENDENT. `linear`/`linear_gradual` take a signed percentage, so
# -10 is a 10% discount. `fixed` takes an ABSOLUTE nightly price, where -10 is not a
# discount, it is a negative price. The sweep used -10 for all three and so asserted
# that a negative absolute price validates clean, which locked the missing `fixed`
# range check in as correct behaviour.
_LAST_MIN_VALUE = {"linear": -10.0, "linear_gradual": -10.0, "fixed": 180.0}
_LAST_MIN_FIELD_SUBSETS = {
    "both": {"value": True, "last_min_factor_dfd": 14},
    "value_only": {"value": True},
    "dfd_only": {"last_min_factor_dfd": 14},
    "neither": {},
}
for _toggle in (None, True, False):
    for _kind in _LAST_MIN_CONCRETE:
        for _subset_name, _fields in _LAST_MIN_FIELD_SUBSETS.items():
            _cfg = {k: v for k, v in _fields.items() if k != "value"}
            if _fields.get("value"):
                _cfg["last_min_factor_value"] = _LAST_MIN_VALUE[_kind]
            _cfg["last_min_factor_type"] = _kind
            if _toggle is not None:
                _cfg["last_min_factor_on"] = _toggle
            _result = cw.validate({"last_minute_prices": _cfg})
            _expect_clean = _subset_name == "both" and _toggle is not None
            _label = (f"last-minute sweep: toggle={_toggle!r} type={_kind} "
                     f"fields={_subset_name} -> expect "
                     f"{'clean' if _expect_clean else 'caught'}")
            if _expect_clean:
                check(_label, not _result, f"got {_result}")
            else:
                check(_label, _result, f"must be caught, got {_result}")

# type `fixed` had NO value validation at all: -10 and 999999 both returned [].
check("F6: type `fixed` rejects a NEGATIVE absolute nightly price",
      cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                          "last_min_factor_type": "fixed",
                                          "last_min_factor_value": -10,
                                          "last_min_factor_dfd": 7}}),
      "-10 is not a 10% discount here, it is a negative price")
check("F6: type `fixed` rejects an absurd absolute price",
      cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                          "last_min_factor_type": "fixed",
                                          "last_min_factor_value": 999999,
                                          "last_min_factor_dfd": 7}}),
      "a 999999 typo must not reach a live calendar")
check("F6: type `fixed` accepts a real nightly rate",
      not cw.validate({"last_minute_prices": {"last_min_factor_on": True,
                                              "last_min_factor_type": "fixed",
                                              "last_min_factor_value": 180,
                                              "last_min_factor_dfd": 7}}),
      "the bound exists to reject nonsense, not to second-guess a real rate")

_FAR_OUT_FIELD_SUBSETS = {
    "linear": {
        "all": {"far_out_premium_value": 25, "far_out_premium_start": 180,
                "far_out_premium_step": 1},
        "no_value": {"far_out_premium_start": 180, "far_out_premium_step": 1},
        "no_start": {"far_out_premium_value": 25, "far_out_premium_step": 1},
        "no_step": {"far_out_premium_value": 25, "far_out_premium_start": 180},
        "none": {},
    },
    "fix": {
        # step is not required for fix ("ignored for fix, always 1"), so "all" here
        # has only value+start -- deliberately different from linear's "all".
        "all": {"far_out_premium_value": 25, "far_out_premium_start": 180},
        "no_value": {"far_out_premium_start": 180},
        "no_start": {"far_out_premium_value": 25},
        "none": {},
    },
}
for _toggle in (None, True, False):
    for _kind, _subsets in _FAR_OUT_FIELD_SUBSETS.items():
        for _subset_name, _fields in _subsets.items():
            _cfg = dict(_fields, far_out_premium_type=_kind)
            if _toggle is not None:
                _cfg["far_out_premium_on"] = _toggle
            _result = cw.validate({"far_out_premium": _cfg})
            _expect_clean = _subset_name == "all" and _toggle is not None
            _label = (f"far-out sweep: toggle={_toggle!r} type={_kind} "
                     f"fields={_subset_name} -> expect "
                     f"{'clean' if _expect_clean else 'caught'}")
            if _expect_clean:
                check(_label, not _result, f"got {_result}")
            else:
                check(_label, _result, f"must be caught, got {_result}")

# --- summary ----------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + ", ".join(fails))
    sys.exit(1)
print("all checks passed.")
