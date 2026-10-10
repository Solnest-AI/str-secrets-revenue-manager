"""S2-007: the customizations table the skill reads shows each season's prices, and the fact
harness proves the reducer kept them. It used to print only "4 seasons" (factcheck counted them),
so neither the card's reader nor the harness could see a $300 seasonal floor under a $435 listing min.
"""

from __future__ import annotations

import copy
import csv
import io
import unittest

import factcheck as fc
import reduce_customizations as rc
from test_seasonal_write import APRIL_ONLY, FULL_YEAR


def table(rules: dict) -> str:
    buf = io.StringIO()
    buf.write("## rules\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(rc.RULE_COLUMNS)
    for r in rc.normalize_rules(rules):
        w.writerow([r[c] for c in rc.RULE_COLUMNS])
    return buf.getvalue()


def csp_row(cfg):
    return next(r for r in rc.normalize_rules({"custom_seasonal_profile": cfg})
                if r["rule"] == "custom_seasonal_profile")


class SeasonPricesAreInTheTable(unittest.TestCase):
    def test_each_season_prints_its_dates_type_and_min_base_max(self):
        row = csp_row(FULL_YEAR)
        self.assertIn("9/1-12/31 fixed min=300 base=495 max=700", row["value"])
        self.assertIn("1/1-3/31 fixed min=275 base=450 max=700", row["value"])
        self.assertEqual(row["window"], "4 seasons")

    def test_an_empty_field_prints_as_the_listing_value(self):
        self.assertIn("4/1-4/30 fixed min=500 base=600 max=listing", csp_row(APRIL_ONLY)["value"])

    def test_a_switched_off_profile_still_shows_what_it_stores(self):
        off = copy.deepcopy(APRIL_ONLY)
        off["custom_seasonal_profile_on"] = False
        row = csp_row(off)
        self.assertEqual(row["toggle"], "OFF")
        self.assertIn("min=500", row["value"])


class FactHarnessSeesSeasonPrices(unittest.TestCase):
    def facts(self, rules, text=None):
        full = fc.customization_facts_full({"customizations": rules})
        reduced = fc.customization_facts_reduced(text if text is not None else table(rules))
        return fc.compare(full, reduced, fc.CUSTOMIZATION_FACTS)

    def test_the_reduced_table_matches_the_raw_seasons(self):
        self.assertEqual(self.facts({"custom_seasonal_profile": FULL_YEAR}), [])
        self.assertEqual(self.facts({"custom_seasonal_profile": APRIL_ONLY}), [])

    def test_a_season_min_changed_by_the_reducer_is_caught(self):
        rules = {"custom_seasonal_profile": FULL_YEAR}
        text = table(rules).replace("min=300 base=495 max=700", "min=350 base=495 max=700", 1)
        bad = self.facts(rules, text)
        self.assertTrue(any(b.startswith("season_digest") for b in bad), bad)


if __name__ == "__main__":
    unittest.main()
