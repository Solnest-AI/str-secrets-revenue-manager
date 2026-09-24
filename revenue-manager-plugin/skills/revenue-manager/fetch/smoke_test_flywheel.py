#!/usr/bin/env python3
"""Offline tests for the flywheel gate -- no API key, no network.

Guards the rulings the gate exists to enforce (PRD D3, D4, FW1-FW4):
  1. all four spokes, framework order, before any pricing opinion
  2. an unreadable spoke SKIPS the listing; it never degrades to a footnote
  3. the diagnosis is the benchmark gap, not the raw number
  4. an empty funnel means NOT COLLECTED, never zero
"""
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import flywheel as fw  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}: {label}{'' if cond else '  -> ' + detail}")
    if not cond:
        fails.append(label)


print("flywheel gate smoke test\n")

# The real numbers from a live listing, 2026-09-19.
REAL = {"first_page_impressions": {"listing": 1465, "similar_listings": 1389},
        "click_through_rate": {"listing": 16.32, "similar_listings": 16.04},
        "view": {"listing": 242, "similar_listings": 228},
        "wishlist": {"listing": 0, "similar_listings": 0},
        "booking_rate": {"listing": 4.17, "similar_listings": 29.87},
        "conversion_rate": {"listing": 0.61, "similar_listings": 4.86}}


def summary(comparison=None, status="active"):
    return {"integration_status": status,
            "similar_listings_comparison": comparison if comparison is not None else REAL}


# --- FW3: the benchmark is the diagnosis ------------------------------------
d = fw.funnel_diagnosis(REAL)
check("the live case breaks at booking_rate, not at visibility",
      d["verdict"] == "break" and d["stage"] == "booking_rate",
      f"got {d['verdict']} {d['stage']}")
check("the four upstream stages are reported healthy",
      d["healthy_through"] == ["first_page_impressions", "click_through_rate",
                               "view", "wishlist"],
      f"got {d['healthy_through']}")
check("a break names what it MEANS, not just the metric",
      "do not book" in (d["meaning"] or ""), f"got {d['meaning']!r}")

# the opposite shape: genuinely invisible
invisible = dict(REAL, first_page_impressions={"listing": 120, "similar_listings": 1389})
d2 = fw.funnel_diagnosis(invisible)
check("a listing nobody sees breaks at the FIRST stage",
      d2["stage"] == "first_page_impressions", f"got {d2['stage']}")
check("and reports nothing upstream as healthy", d2["healthy_through"] == [],
      f"got {d2['healthy_through']}")

healthy = {k: {"listing": v["similar_listings"], "similar_listings": v["similar_listings"]}
           for k, v in REAL.items()}
check("a funnel matching its comp set everywhere is healthy",
      fw.funnel_diagnosis(healthy)["verdict"] == "healthy")
check("a zero benchmark is not a break (0 vs 0 cannot be trailed)",
      fw.funnel_diagnosis(REAL)["stage"] != "wishlist",
      "wishlist is 0 against 0 in the real data and must not read as a failure")
check("a missing benchmark stops the walk as UNKNOWN, never as healthy",
      fw.funnel_diagnosis(dict(REAL, view={}))["verdict"] == "unknown",
      "claiming the later stages are fine when one could not be read is a guess")

# --- the visibility spoke ---------------------------------------------------
check("visibility reads when the integration is active and benchmarked",
      fw.spoke_visibility(summary())["ok"])
check("a DEACTIVATED integration fails the spoke: empty is not zero",
      not fw.spoke_visibility(summary(status="deactivated"))["ok"])
check("and says so in words the operator can act on",
      "not being collected" in fw.spoke_visibility(summary(status="deactivated"))["detail"])
check("a summary with no benchmark fails: a bare number cannot diagnose",
      not fw.spoke_visibility(summary(comparison={}))["ok"])
check("no summary row at all fails", not fw.spoke_visibility(None)["ok"])

# --- the other three spokes -------------------------------------------------
BOOKING_ROWS = [{"date": (date(2026, 9, 19) + timedelta(days=i)).isoformat(),
                 "status": {"reason": "AVAILABLE", "available": True}} for i in range(90)]
check("bookings reads from PMS rows", fw.spoke_bookings(BOOKING_ROWS)["ok"])
check("a PMS read failure fails the spoke", not fw.spoke_bookings(None)["ok"])
check("ZERO PMS rows is a read failure, not an empty calendar",
      not fw.spoke_bookings([])["ok"],
      "this is the silent-wrong-answer shape the whole build exists to stop")
check("reviews reads", fw.spoke_reviews([{"r": 5}] * 92, 4.92)["ok"])
check("a rating below 4.6 is flagged as a RANKING problem (framework 6.9)",
      fw.spoke_reviews([{"r": 3}], 4.4).get("flag") == "rating_below_ranking_threshold")
check("a rating at 4.6 is not flagged", "flag" not in fw.spoke_reviews([{"r": 5}], 4.6))
check("unreadable reviews fail the spoke", not fw.spoke_reviews(None)["ok"])
RANK_OK = [{"page": 1, "position": 8}, {"page": 1, "position": 15}]
check("ranking reads", fw.spoke_ranking(RANK_OK)["ok"])
check("no ranking rows is a failure, not a good position",
      not fw.spoke_ranking([])["ok"])
check("page 5+ is flagged: framework 6.8 puts visibility before pricing",
      fw.spoke_ranking([{"page": 6, "position": 2}]).get("flag") == "buried_in_search")

# --- FW1/FW2/D4: the gate ---------------------------------------------------
OK = (fw.spoke_visibility(summary()), fw.spoke_bookings(BOOKING_ROWS),
      fw.spoke_reviews([{"r": 5}] * 92, 4.92), fw.spoke_ranking(RANK_OK))
g = fw.gate("L1", *OK)
check("all four spokes readable -> analysable", g["verdict"] == "analysable")
check("the gate walks the spokes in framework order",
      g["order"] == ["visibility", "bookings", "reviews", "ranking"],
      f"got {g['order']}")
check("an analysable listing carries the funnel headline",
      "booking_rate" in g.get("headline", ""), f"got {g.get('headline')}")

# --- D12 (2026-09-20, SUPERSEDES D4): a missing spoke DEGRADES, it does not skip ---
# These tests used to assert `skipped` with no headline. That rule is one day old and
# was deliberately overturned: RankBreeze supplies two of the four spokes, it is a paid
# tool, and most of a 100-person class will not have it. Do not put the skip back.
for name in ("visibility", "reviews", "ranking"):
    broken = list(OK)
    broken[fw.SPOKES.index(name)] = dict(OK[fw.SPOKES.index(name)], ok=False,
                                         detail="simulated read failure")
    gb = fw.gate("L1", *broken)
    check(f"D12: an unreadable {name} spoke DEGRADES the listing, it does not skip it",
          gb["verdict"] == "degraded" and name in gb["failed"],
          f"got {gb['verdict']} {gb['failed']}")
    check(f"D12: the degraded run names {name} as the gap",
          name in gb["why"], f"got {gb['why']}")
    check(f"D12: a degraded run STILL carries a pricing headline ({name})",
          "headline" in gb,
          "the whole point of D12 is that the class gets output")
    check(f"D12: the gap is printed FIRST, before any number ({name})",
          fw.render(gb).splitlines()[1].startswith("  !! PRICED WITHOUT"),
          f"got {fw.render(gb).splitlines()[1]!r}")

# bookings is the one spoke that still blocks: no calendar means nothing to price
bk_broken = list(OK)
bk_broken[fw.SPOKES.index("bookings")] = dict(OK[1], ok=False, detail="PMS unreadable")
gbk = fw.gate("L1", *bk_broken)
check("an unreadable BOOKINGS spoke still BLOCKS: without the calendar there is nothing to price",
      gbk["verdict"] == "blocked", f"got {gbk['verdict']}")
check("a blocked listing carries NO pricing headline",
      "headline" not in gbk, "blocked means no input, not a withheld opinion")
check("the block says why", "cannot price" in gbk["why"], f"got {gbk['why']}")

gm = fw.gate("L1", *(dict(s, ok=False, detail="x") for s in OK))
check("every spoke failing is blocked (bookings is among them)",
      gm["verdict"] == "blocked" and len(gm["failed"]) == 4, f"got {gm['verdict']} {gm['failed']}")

# --- D13: the market layer is one line, never a gate ------------------------------
gnm = fw.gate("L1", *OK)
check("D13: with no market data the gate is still analysable",
      gnm["verdict"] == "analysable")
check("D13: and prints one line saying the market layer is absent",
      "Market Research not available" in fw.render(gnm), fw.render(gnm))
gwm = fw.gate("L1", *OK, market={"ok": True, "detail": "PG: 63.8% occ, LOS 6-13n, BW 8-27d"})
check("D13: with market data the line shows it",
      "LOS 6-13n" in fw.render(gwm), fw.render(gwm))
check("D13: a broken market layer never changes the verdict",
      fw.gate("L1", *OK, market={"ok": False, "detail": "429"})["verdict"] == "analysable")

# --- FW4: the chain is printed ----------------------------------------------
text = fw.render(g)
check("render prints one line per spoke plus a verdict",
      all(n in text for n in fw.SPOKES) and "analysable" in text, text)
check("render marks a failed spoke FAIL",
      "FAIL" in fw.render(fw.gate("L1", *(list(OK[:2]) + [dict(OK[2], ok=False,
                                                              detail="x")] + list(OK[3:])))))

print()
if fails:
    print(f"{len(fails)} FAILED: " + ", ".join(fails))
    sys.exit(1)
print("all checks passed.")
