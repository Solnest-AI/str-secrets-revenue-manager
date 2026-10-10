"""S2-007, writer side: a PriceLabs custom seasonal profile sets each night's min/base/max.

A Summit student, 2026-09-29: with full-year seasons the listing-level values never
apply, yet the writer
  - refused overrides below the LISTING min that were above the night's seasonal min (too strict),
  - passed overrides below an April seasonal min because they cleared the listing min (too loose),
  - changed the listing-level min/base/max, read it back as "applied and verified", and no
    price moved.
Numbers are from the report: listing A $435/$510/$1,400 vs seasons $300/$495/$700 (fixed, full
year); listing B's April seasonal min $500 vs listing $350.
"""

from __future__ import annotations

import copy
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from _mvp_write import CannotWrite, describe, plan_change
from test_mvp_write import (LID, PMS, TODAY, Base, FakePriceLabs, Response,  # noqa: F401
                            change, live_for)


def season(name, sm, sd, em, ed, lo=None, base=None, hi=None):
    return {"season_name": name, "start_month": str(sm), "start_day": str(sd),
            "end_month": str(em), "end_day": str(ed),
            "lowest_price": lo, "base_price": base, "highest_price": hi}


def profile(seasons, price_type="fixed", on=True, one_off=None, one_off_type=None):
    inner = {"seasons": seasons, "price_type": price_type}
    if one_off is not None:
        inner["non_repeating_seasons"] = one_off
        inner["price_type_non_repeating"] = one_off_type or price_type
    return {"custom_seasonal_profile_on": on, "custom_seasonal_profile": inner}


# Listing A: four fixed seasons cover every day of the year
FULL_YEAR = profile([
    season("Winter", 1, 1, 3, 31, 275, 450, 700),
    season("Spring", 4, 1, 5, 31, 300, 495, 700),
    season("Summer", 6, 1, 8, 31, 300, 495, 700),
    season("Fall", 9, 1, 12, 31, 300, 495, 700),
])
# Listing B: one April season with its own $500 min; the rest of the year uses the listing's
APRIL_ONLY = profile([season("April", 4, 1, 4, 30, 500, 600, None)])


class FakeSeasons(FakePriceLabs):
    """FakePriceLabs plus GET /v1/customizations/{listing,group,account}. Any write to a
    customization endpoint fails the test: the price writer only READS seasons."""

    def __init__(self, *, csp=None, level="listing", group_id=None, unreadable=(),
                 listing=None, prices=None, **kw):
        super().__init__(**kw)
        self.csp = {"listing": None, "group": None, "account": None}
        self.csp[level] = copy.deepcopy(csp)
        self.unreadable = set(unreadable)
        if group_id is not None:
            self.listing["group_id"] = group_id
        if listing:
            self.listing.update(listing)
        if prices is not None:
            self.prices = {k: float(prices) for k in self.prices}

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        if not url.path.startswith("/v1/customizations/"):
            return super().open(req, timeout)
        method = req.get_method()
        self.requests.append((method, url.path, json.loads(req.data) if req.data else None))
        assert method == "GET", f"the price writer must never write {url.path}"
        q = parse_qs(url.query)
        assert q.get("toggled_on") == ["false"], q
        level = url.path.rsplit("/", 1)[1]
        if level in self.unreadable:
            return Response({"error": "temporarily unavailable"})
        cfg = self.csp[level]
        return Response({"customizations": {} if cfg is None else
                         {"custom_seasonal_profile": copy.deepcopy(cfg)}})


def full_year(**kw):
    return FakeSeasons(csp=FULL_YEAR, listing={"min": 435.0, "base": 510.0, "max": 1400.0},
                       currency="CAD", prices=520, **kw)


def april_only(**kw):
    return FakeSeasons(csp=APRIL_ONLY, listing={"min": 350.0, "base": 450.0, "max": 1200.0},
                       currency="CAD", prices=520, **kw)


def fixed(d, price):
    return {"date": d, "price": price, "price_type": "fixed"}


class OverrideFloorIsTheNightsSeasonalMin(Base):
    def test_override_below_listing_min_but_above_the_seasonal_min_is_planned(self):
        # Too strict before: $320 is under the listing's $435 and was REFUSED, though the night's
        # real min is the Fall season's $300.
        fake = full_year()
        fake.prices = {k: 330.0 for k in fake.prices}
        env = self.plan(fake, change(overrides_set=[fixed("2026-10-22", 320)]))
        self.assertEqual(env["operations"][0]["after"]["price"], "320")

    def test_override_below_the_seasonal_min_is_refused_even_above_the_listing_min(self):
        # Too loose before: $450 clears the listing's $350 but not April's seasonal $500.
        with self.assertRaisesRegex(CannotWrite, r"below .*500\.00") as caught:
            self.plan(april_only(), change(overrides_set=[fixed("2027-04-12", 450)]))
        self.assertIn("season", str(caught.exception).lower())

    def test_outside_the_season_the_listing_min_still_applies(self):
        with self.assertRaisesRegex(CannotWrite, r"below your min of \$350\.00"):
            self.plan(april_only(), change(overrides_set=[fixed("2026-10-20", 340)]))
        env = self.plan(april_only(), change(overrides_set=[fixed("2026-10-20", 360)]))
        self.assertEqual(env["operations"][0]["after"]["price"], "360")

    def test_a_percentage_season_scales_the_listing_min(self):
        # Boho Bliss's real stored shape (live 2026-10-10): percentage, lowest_price 15
        fake = FakeSeasons(csp=profile([season("Spring", 10, 1, 10, 31, 15, 5, None)],
                                       price_type="percentage"))
        # listing min 150 -> seasonal min 172.50 on an October night
        with self.assertRaisesRegex(CannotWrite, r"172\.50"):
            self.plan(fake, change(overrides_set=[fixed("2026-10-15", 170)]))
        env = self.plan(fake, change(overrides_set=[fixed("2026-10-15", 175)]))
        self.assertEqual(env["operations"][0]["after"]["price"], "175")

    def test_a_season_switched_off_is_not_applied(self):
        off = copy.deepcopy(APRIL_ONLY)
        off["custom_seasonal_profile_on"] = "false"  # a string toggle reads as OFF
        fake = FakeSeasons(csp=off, listing={"min": 350.0, "base": 450.0, "max": 1200.0}, prices=520)
        env = self.plan(fake, change(overrides_set=[fixed("2027-04-12", 450)]))
        self.assertEqual(env["operations"][0]["after"]["price"], "450")

    def test_a_one_off_season_beats_the_repeating_one_on_its_dates(self):
        csp = profile([season("Fall", 9, 1, 12, 31, 300, 495, 700)],
                      one_off=[{**season("Bike Week", 10, 18, 10, 25, 600, 800, 1500),
                                "start_year": "2026", "end_year": "2026"}])
        fake = FakeSeasons(csp=csp, listing={"min": 435.0, "base": 510.0, "max": 1400.0}, prices=650)
        with self.assertRaisesRegex(CannotWrite, r"600\.00"):
            self.plan(fake, change(overrides_set=[fixed("2026-10-20", 550)]))
        env = self.plan(fake, change(overrides_set=[fixed("2026-10-28", 550)]))
        self.assertEqual(env["operations"][0]["date"], "2026-10-28")

    def test_a_group_season_applies_when_the_listing_sets_none(self):
        fake = FakeSeasons(csp=APRIL_ONLY, level="group", group_id=77,
                           listing={"min": 350.0, "base": 450.0, "max": 1200.0}, prices=520)
        with self.assertRaisesRegex(CannotWrite, r"500\.00"):
            self.plan(fake, change(overrides_set=[fixed("2027-04-12", 450)]))
        self.assertIn(("GET", "/v1/customizations/group", None), fake.requests)

    def test_an_unreadable_season_level_refuses_rather_than_guessing_the_floor(self):
        fake = FakeSeasons(csp=None, unreadable={"account"})
        with self.assertRaisesRegex(CannotWrite, "(?i)season"):
            self.plan(fake, change(overrides_set=[fixed("2026-10-20", 200)]))
        self.assertEqual(fake.writes(), [])

    def test_seasons_are_never_written(self):
        fake = april_only()
        env = self.plan(fake, change(overrides_set=[fixed("2026-10-20", 360)]))
        self.apply(fake, env)
        self.assertFalse([r for r in fake.requests
                          if r[1].startswith("/v1/customizations/") and r[0] != "GET"])

    def test_seasons_that_changed_since_the_plan_are_refused_at_apply(self):
        fake = april_only()
        env = self.plan(fake, change(overrides_set=[fixed("2027-04-12", 520)]))
        fake.csp["listing"]["custom_seasonal_profile"]["seasons"][0]["lowest_price"] = 550
        with self.assertRaisesRegex(CannotWrite, "(?i)season.*changed since the plan"):
            self.apply(fake, env)
        self.assertEqual(fake.writes(), [])


class ListingBoundsUnderFullYearSeasonsAreNoOps(Base):
    def test_min_change_no_night_uses_is_refused_not_applied_and_verified(self):
        fake = full_year()
        with self.assertRaisesRegex(CannotWrite, r"(?i)no night") as caught:
            self.plan(fake, change(listing_prices={"min": 400}))
        msg = str(caught.exception)
        self.assertIn("seasonal profile", msg)
        self.assertNotIn("—", msg)
        self.assertEqual(fake.writes(), [])

    def test_base_and_max_too(self):
        for field, value in (("base", 520), ("max", 1300)):
            with self.subTest(field=field), self.assertRaisesRegex(CannotWrite, r"(?i)no night"):
                self.plan(full_year(), change(listing_prices={field: value}))

    def test_a_partly_covered_min_says_how_many_nights_it_reaches(self):
        env = self.plan(april_only(), change(listing_prices={"min": 360}))
        scope = env["season_scope"]["min"]
        self.assertEqual(scope["horizon_days"], 365)
        self.assertEqual(scope["season_set"], 30)       # April 2027 is set by the season
        self.assertEqual(scope["moves"], 335)
        card = describe(env)
        self.assertIn("335 of the next 365 nights", card)
        self.assertIn("Apr 1 to Apr 30", card)

    def test_a_percentage_season_still_moves_with_the_listing_value(self):
        fake = FakeSeasons(csp=profile([season("All", 1, 1, 12, 31, 15, 5, 10)],
                                       price_type="percentage"))
        env = self.plan(fake, change(listing_prices={"min": 160}))
        self.assertEqual(env["season_scope"]["min"]["season_set"], 0)
        self.assertEqual(env["season_scope"]["min"]["moves"], 365)

    def test_an_empty_season_field_falls_back_to_the_listing_value(self):
        # The April season leaves max empty, so the listing max prices every night
        env = self.plan(april_only(), change(listing_prices={"max": 1250}))
        self.assertEqual(env["season_scope"]["max"]["moves"], 365)

    def test_no_seasonal_profile_keeps_the_old_plan_exactly(self):
        fake = FakeSeasons(csp=None)
        env = self.plan(fake, change(listing_prices={"min": 170}))
        self.assertEqual(env["operations"], [
            {"kind": "listing_price", "field": "min", "before": 150.0, "after": 170.0}])
        self.assertFalse(env.get("season_scope"))

    def test_the_undo_of_a_covered_change_is_never_refused(self):
        fake = april_only()
        env = self.plan(fake, change(listing_prices={"min": 360}))
        journal = self.apply(fake, env)
        fake.csp["listing"] = FULL_YEAR  # seasons now cover the whole year
        undo = self.plan_undo(fake, journal)
        self.assertEqual(undo["operations"][0]["after"], 350.0)
        self.assertTrue(any("no night" in w.lower() for w in undo["warnings"]), undo["warnings"])


class ApplyReportSaysWhereTheChangeLands(Base):
    def test_verified_line_names_the_nights_the_seasons_keep(self):
        import apply_change
        fake = april_only()
        env = self.plan(fake, change(listing_prices={"min": 360}))
        journal = self.apply(fake, env)
        lines = "\n".join(apply_change.season_scope_lines(journal))
        self.assertIn("335 of the next 365 nights", lines)
        self.assertIn("30", lines)
        self.assertNotIn("—", lines)


if __name__ == "__main__":
    unittest.main()
