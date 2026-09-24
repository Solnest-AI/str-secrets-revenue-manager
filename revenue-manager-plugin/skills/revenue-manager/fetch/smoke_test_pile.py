#!/usr/bin/env python3
"""Offline tests for PRD D14 (Ryan-stated 2026-09-21) -- no API key, no network.

  (a) the PriceLabs pile (actions + nudges) is fetched every run and PERSISTED to
      Supabase latest-wins, through a single-purpose writer that keeps the runner's
      read-only transport read-only
  (b) it is ONE input, never the basis
  (c) every configured rule is checked for effectiveness against the market yardstick
"""
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import attribution as at  # noqa: E402
import _mvp_recommendations as rec  # noqa: E402
import reduce_customizations as rc  # noqa: E402
from _mvp_analysis import pile_summary  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}: {label}{'' if cond else '  -> ' + detail}")
    if not cond:
        fails.append(label)


print("D14 pile + rule effectiveness smoke test\n")

# --- (a) the writer -------------------------------------------------------------
ACTIONS = [["this-listing", "listing-", "Mine", "min_price_alert", "t", '{"price":1}', '{"price":2}'],
           ["OTHER-LISTING", "listing-", "Theirs", "oba_turned_off", "t", "{}", "{}"]]
NUDGES = [["OTHER-LISTING", "listing-theirs-0002", "smartbnb", "Theirs", "min_1", "min_price",
           425, 403, "decrease", "r", "2026-09-24T04:33:59.000Z", "pending"]]
recs = rec.rows_from_pile("listing-mine-0001", "smartbnb", ACTIONS, NUDGES,
                          rc.ACTION_COLUMNS, rc.NUDGE_COLUMNS, "2026-09-21T00:00:00+00:00", "run-1")
check("every action and nudge becomes one record", len(recs) == 3, f"got {len(recs)}")
check("strays are KEPT and labelled other-listing, not dropped",
      sum(1 for r in recs if r["scope"] == "other-listing") == 2,
      "the stored table must be an honest picture of what PriceLabs said")
check("each record carries the owning listing PriceLabs named",
      recs[2]["owner_listing"] == "listing-theirs-0002")
sql = rec.statement("listing-mine-0001", "smartbnb", recs)
check("the statement supersedes the previous set FIRST (latest-wins)",
      sql.index("UPDATE") < sql.index("INSERT") and "superseded_at = now()" in sql)
check("and only the current set is superseded, never history",
      "AND superseded_at IS NULL" in sql, "history is kept for the learning loop")
check("it inserts every record", sql.count("::jsonb") == 3, f"got {sql.count('::jsonb')}")
check("payload single quotes are escaped, not injected",
      "'" not in rec._lit("it's")[1:-1].replace("''", ""),
      rec._lit("it's"))
for bad in ("x'; DROP TABLE t; --", "a b", "", "x" * 200):
    try:
        rec.statement(bad, "smartbnb", recs)
        check(f"a non-identifier listing_id is refused ({bad[:12]!r})", False, "was accepted")
    except rec.CannotPersist:
        check(f"a non-identifier listing_id is refused ({bad[:12]!r})", True)
try:
    rec.rows_from_pile("l", "p", [], [["this-listing", "l", "p", "n", "", "min_price", 1, 2,
                                       "up", "r", "", "pending"]],
                       rc.ACTION_COLUMNS, rc.NUDGE_COLUMNS, "t", None)
    check("a nudge with no id is refused, never stored unidentifiable", False, "was accepted")
except rec.CannotPersist:
    check("a nudge with no id is refused, never stored unidentifiable", True)
try:
    rec.persist("", "", "l", "p", recs)
    check("no project/token -> refuses loudly rather than silently skipping", False)
except rec.CannotPersist as e:
    check("no project/token -> refuses loudly rather than silently skipping",
          "NOT stored" in str(e), str(e))
check("an empty pile still supersedes the old set (the old one is no longer current)",
      "UPDATE" in rec.statement("l", "p", []) and "INSERT" not in rec.statement("l", "p", []))

# --- (b) one input, not the basis -------------------------------------------------
pile = {"counts": {"actions": 8, "nudges": 1, "actions_other": 7, "nudges_other": 1},
        "this_listing": {"actions": [{"action_type": "last_minute_conservative_vs_market",
                                      "title": "Review Last Minute Prices",
                                      "current": '{"discount_pct":-12.0}',
                                      "recommended": '{"discount_pct":40.0}'}],
                         "nudges": []}}
ps = pile_summary(pile)
check("the brief shows what PriceLabs suggests for THIS listing", len(ps["this_listing"]) == 1)
check("and says how much of the pile belonged to other listings",
      "8 in the pile belong to other listings" in ps["summary"], ps["summary"])
check("no pile -> None, and the brief simply omits the block", pile_summary(None) is None)

# --- (c) rule effectiveness ---------------------------------------------------------
today = date(2026, 9, 21)


def horizon(book_inside_lm, book_far, mkt_lm=60.0, mkt_mid=50.0, mkt_far=33.0):
    rows = []
    for i in range(90):
        d = today + timedelta(days=i)
        if i <= 7:
            booked, mkt = book_inside_lm(i), mkt_lm
        elif i < 60:
            booked, mkt = i % 2 == 0, mkt_mid
        else:
            booked, mkt = book_far(i), mkt_far
        rows.append({"date": d.isoformat(), "days_out": i, "dow": d.weekday(),
                     "booked": booked, "blocked": False, "market_occ": mkt})
    return rows


RULES = {"last_minute_prices": {"last_min_factor_on": True, "last_min_factor_type": "linear",
                                "last_min_factor_value": -12.0, "last_min_factor_dfd": 7},
         "far_out_premium": {"far_out_premium_on": True, "far_out_premium_type": "linear",
                             "far_out_premium_value": 20.0, "far_out_premium_start": 60},
         "seasonality": {"seasonality_customization_on": "false"},
         "demand_factor": {"tone_demand_factor_on": True, "tone_demand_factor": "recommended"}}

eff = {e["rule"]: e for e in at.rule_effectiveness(RULES, horizon(lambda i: i % 4 == 0,
                                                                   lambda i: i % 3 == 0))}
check("a last-minute window booking far below the market is UNDERPERFORMING",
      eff["last_minute_prices"]["verdict"] == "underperforming", str(eff["last_minute_prices"]))
check("a far-out window holding at market is WORKING",
      eff["far_out_premium"]["verdict"] == "working", str(eff["far_out_premium"]))
check("the yardstick is the market, so lead time is controlled for",
      eff["far_out_premium"]["yardstick"] == "market")
check("a rule toggled off (even as a string) is reported OFF, not judged",
      eff["seasonality"]["verdict"] == "off")
check("a whole-horizon rule has no window to split and says so",
      eff["demand_factor"]["verdict"] == "no_window")
check("the verdict names both sides in plain words",
      "inside the window" in eff["last_minute_prices"]["why"])

# blocked and held nights leave the denominator
held = horizon(lambda i: False, lambda i: i % 3 == 0)
for r in held[:8]:
    r["blocked"] = True
eff_h = {e["rule"]: e for e in at.rule_effectiveness(RULES, held)}
check("a window that is entirely blocked/held is UNKNOWN, not underperforming",
      eff_h["last_minute_prices"]["verdict"] == "unknown", str(eff_h["last_minute_prices"]))

# too few dates is a coin flip
short = horizon(lambda i: True, lambda i: True)[:12]
eff_s = {e["rule"]: e for e in at.rule_effectiveness(RULES, short)}
check("fewer than 7 dates on a side is UNKNOWN, never a verdict",
      eff_s["far_out_premium"]["verdict"] == "unknown", str(eff_s["far_out_premium"]))

# no market data -> raw comparison, LABELLED confounded, never silently promoted
raw = horizon(lambda i: i % 4 == 0, lambda i: i % 3 == 0)
for r in raw:
    r["market_occ"] = None
eff_r = {e["rule"]: e for e in at.rule_effectiveness(RULES, raw)}
check("without a market yardstick the comparison is labelled confounded",
      "confounded" in eff_r["last_minute_prices"]["yardstick"], str(eff_r["last_minute_prices"]))
check("and the why says there is NO market yardstick",
      "NO market yardstick" in eff_r["last_minute_prices"]["why"])

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails[:6]))
    sys.exit(1)
print("all checks passed.")
