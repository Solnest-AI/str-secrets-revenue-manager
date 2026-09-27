"""A $0 booking is asked about on the card, never assumed (Ryan, 2026-09-26).

Live 2026-09-26: Boho Bliss GJVJBI (manual, 5 nights) showed $0 in Hospitable because the guest
paid by Stripe after a Hospitable issue. Read as a $0 stay, the next 7 nights showed 28.57%
booked (really full) and the 30-night pace 33.33% (really 50%), which kept a funnel "break"
that was not one. The engine cannot tell a paid-elsewhere stay from an owner or comp stay, so
the card names each one, asks, and shows pace, funnel verdict and min price both ways."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mvp_analysis import render  # noqa: E402
from _mvp_pms import normalize_reservation  # noqa: E402
from test_mvp_analysis import METRICS, START, direct_build, synthetic_bundle  # noqa: E402

URBAN_NEST = {  # a real RankBreeze break: booking rate 0.97% vs 34.06%
    "first_page_impressions": {"listing": 3259, "similar_listings": 2330},
    "click_through_rate": {"listing": 56.57, "similar_listings": 14.13},
    "view": {"listing": 1499, "similar_listings": 390},
    "wishlist": {"listing": 5, "similar_listings": 2},
    "booking_rate": {"listing": 0.97, "similar_listings": 34.06},
    "conversion_rate": {"listing": 0.42, "similar_listings": 6.27},
}


def with_zero_stay(bundle, offset, nights, **extra):
    """Replace the fixture's 1-night $0 stay with one of `nights` nights starting at `offset`."""
    source = bundle["inputs"]
    stay = next(r for r in source["reservations"]["data"] if r["id"] == "fixture-zero")
    stay.update(extra)
    stay["check_in"] = (START + timedelta(days=offset)).isoformat() + "T16:00:00+00:00"
    stay["check_out"] = (START + timedelta(days=offset + nights)).isoformat() + "T10:00:00+00:00"
    stay["nights"] = nights
    reserved = set(range(offset, offset + nights))
    for i, day in enumerate(source["calendar"]):
        if i == 4 and 4 not in reserved:
            day.update(status_reason="AVAILABLE", available=True)
        if i in reserved:
            day.update(status_reason="RESERVED", available=False)
    for i, row in enumerate(source["prices"]["data"]):
        if i == 4 and 4 not in reserved:
            row["booking_status"] = ""
        if i in reserved:
            row["booking_status"] = "Booked"
    return bundle


class PmsListsEachZeroStay(unittest.TestCase):
    def test_the_stay_is_named_with_its_code_and_dates(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["reservations"]["data"][2]["code"] = "GJVJBI"
        pms = direct_build(bundle)["pms"]
        self.assertEqual(pms["zero_value_stays"], [{
            "id": "fixture-zero", "code": "GJVJBI", "platform": "airbnb", "stay_type": None,
            "owner_stay": None, "check_in": (START + timedelta(days=4)).isoformat(),
            "check_out": (START + timedelta(days=5)).isoformat(), "nights": 1,
            "dates": [(START + timedelta(days=4)).isoformat()]}])

    def test_paid_and_held_stays_are_not_listed(self):
        pms = direct_build(synthetic_bundle())["pms"]
        self.assertEqual([s["id"] for s in pms["zero_value_stays"]], ["fixture-zero"])

    def test_code_falls_back_to_the_platform_id_and_normalizing_twice_is_stable(self):
        raw = {"id": "x", "platform": "airbnb", "platform_id": "HMJF4SME34"}
        once = normalize_reservation(raw)
        self.assertEqual(once["code"], "HMJF4SME34")
        self.assertEqual(normalize_reservation(once), once)
        self.assertIsNone(normalize_reservation({"id": "y"})["code"])


class CardAsksAndShowsBothReadings(unittest.TestCase):
    def test_the_card_asks_and_names_the_stay(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["reservations"]["data"][2]["code"] = "GJVJBI"
        pack = direct_build(bundle)
        q = pack["zero_value_question"]
        self.assertEqual(q["nights_in_window"], 1)
        self.assertEqual(q["stays"][0]["code"], "GJVJBI")
        # 30-night window: 2 confirmed of 29 bookable (one blocked night) as shown, 3 if paid
        self.assertEqual(q["if_paid"]["as_shown_pct"], 6.9)
        self.assertEqual(q["if_paid"]["occupancy_pct"], 10.34)
        text = render(pack, "run", METRICS)
        self.assertIn("QUESTION FOR THE HOST, $0 BOOKING (ask before acting", text)
        self.assertIn("GJVJBI (airbnb):", text)
        self.assertIn("paid outside the PMS (Stripe, e-transfer, cash), or an owner, friends or comp stay",
                      text)
        self.assertIn("If it was paid: next 30 nights 10.34% booked vs the market's 50% (as shown: 6.9%)",
                      text)
        self.assertLess(text.index("QUESTION FOR THE HOST"), text.index("Run run."))

    def test_the_boho_case_flips_the_funnel_verdict(self):
        bundle = with_zero_stay(synthetic_bundle(), 6, 13, code="GJVJBI")
        bundle["inputs"]["funnel"]["visibility_row"]["similar_listings_comparison"] = deepcopy(URBAN_NEST)
        pack = direct_build(bundle)
        self.assertEqual(pack["reconciliation"]["mismatches"], [])
        vis = pack["flywheel"]["spokes"]["visibility"]["diagnosis"]
        self.assertEqual(vis["verdict"], "break")  # as shown: 2 of 29 booked vs the market's 50%
        q = pack["zero_value_question"]
        self.assertEqual((q["if_paid"]["as_shown_pct"], q["if_paid"]["occupancy_pct"]), (6.9, 51.72))
        self.assertEqual((q["if_paid"]["funnel_as_shown"], q["if_paid"]["funnel"]), ("break", "selling"))
        text = render(pack, "run", METRICS)
        self.assertIn("13 night(s), $0 accommodation in the PMS", text)
        self.assertIn("funnel verdict selling (as shown: break)", text)
        self.assertIn("Revenue figures still leave out the amount paid outside the PMS", text)

    def test_the_min_price_call_is_shown_both_ways(self):
        # every open night in the next 30 sits at the 80 min and pace lags as shown (2 of 29 vs
        # 50%), so the min would drop; with the 13 $0 nights paid the pace is even and it stays
        bundle = with_zero_stay(synthetic_bundle(), 6, 13)
        for i, row in enumerate(bundle["inputs"]["prices"]["data"]):
            if 19 <= i < 30:
                row["price"] = 80
                bundle["inputs"]["calendar"][i]["price_cents"] = 8000
        pack = direct_build(bundle)
        self.assertEqual(pack["reconciliation"]["mismatches"], [])
        self.assertEqual((pack["min_price"]["action"], pack["min_price"]["recommended"]), ("lower", 68))
        m = pack["zero_value_question"]["if_paid"]["min_price"]
        self.assertEqual((m["action"], m["recommended"], m["pace"]), ("keep", 80, "even"))
        self.assertIn("min price keep at 80 (as shown: lower to 68)", render(pack, "run", METRICS))

    def test_the_verdict_on_the_card_is_not_changed_by_the_question(self):
        # asking never rewrites the as-shown numbers: the host's answer does, in the reply
        bundle = with_zero_stay(synthetic_bundle(), 6, 13)
        bundle["inputs"]["funnel"]["visibility_row"]["similar_listings_comparison"] = deepcopy(URBAN_NEST)
        pack = direct_build(bundle)
        lead = next(w for w in pack["windows"] if w["days"] == 30)
        self.assertEqual((lead["confirmed"], lead["zero_value"], lead["occupancy_pct"]), (2, 13, 6.9))

    def test_owner_and_maintenance_stays_are_not_asked_about(self):
        for extra in ({"stay_type": "owner_stay"}, {"owner_stay": True}, {"stay_type": "maintenance"}):
            with self.subTest(**extra):
                bundle = synthetic_bundle()
                bundle["inputs"]["reservations"]["data"][2].update(extra)
                pack = direct_build(bundle)
                self.assertIsNone(pack["zero_value_question"])
                self.assertNotIn("QUESTION FOR THE HOST", render(pack, "run", METRICS))

    def test_no_zero_stay_no_question(self):
        bundle = synthetic_bundle()
        bundle["inputs"]["reservations"]["data"][2]["financials"]["host_accommodation_cents"] = 12000
        bundle["inputs"]["prices"]["data"][4]["booking_status"] = "Booked"
        pack = direct_build(bundle)
        self.assertIsNone(pack["zero_value_question"])
        self.assertNotIn("QUESTION FOR THE HOST", render(pack, "run", METRICS))

    def test_a_stay_that_changes_nothing_says_so(self):
        # a $0 night past the 30-night lead window moves neither the pace nor the min price
        bundle = with_zero_stay(synthetic_bundle(), 60, 2)
        text = render(direct_build(bundle), "run", METRICS)
        self.assertIn("Paid or not, the next-30-night pace, funnel verdict and min price below do not "
                      "change.", text)
        self.assertNotIn("If it was paid", text)


if __name__ == "__main__":
    unittest.main()
