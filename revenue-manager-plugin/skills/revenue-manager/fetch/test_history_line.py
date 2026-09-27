"""SKILL 3.1's historical read is the runner's job, not the model's (2026-09-26).

In 8 of 9 headless runs the model skipped at least one of the four audit tables (market_snapshots
in 8, pricing_decisions in 2). The runner now reads all four for every property on every run and
prints one History line on the card, so the read cannot be skipped and the reply can quote it."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mvp_analysis import render, render_history  # noqa: E402
from _mvp_config import normalized_context, read_context  # noqa: E402
from test_mvp_analysis import METRICS, direct_build, synthetic_bundle  # noqa: E402

PID = "fixture-property"
ROW = {"property_id": PID, "settings": {"pricelabs_listing_id": PID}, "updated_at": "2026-09-26T04:05:58Z"}


class FakeClient:
    def __init__(self, context):
        self.context, self.bodies = context, []

    def fetch(self, name, key, load):
        return load()

    def request(self, provider, op, url, headers=None, body=None):
        self.bodies.append(body)
        return [{"context": self.context}], {}


class FakeConnections:
    def supabase(self):
        return ("projectref", "token")


class RunnerReadsAllFourTables(unittest.TestCase):
    def test_the_query_reads_all_four_tables_read_only(self):
        client = FakeClient({"config": [ROW], "history": {"changes": 0}})
        read_context(client, FakeConnections(), PID)
        body = client.bodies[0]
        self.assertTrue(body["read_only"])
        for table in ("property_config", "pricelabs_change_log", "pricing_decisions", "market_snapshots"):
            self.assertIn(f"FROM {table}", body["query"])

    def test_the_change_log_matches_the_pricelabs_listing_id_too(self):
        client = FakeClient({"config": [ROW]})
        read_context(client, FakeConnections(), PID)
        self.assertIn("settings->>'pricelabs_listing_id'", client.bodies[0]["query"])

    def test_history_counts_pass_through_and_absent_means_not_read(self):
        history = {"changes": 5, "changes_latest": "2026-09-26T06:55:57Z", "decisions": 0,
                   "snapshots": 0}
        ctx = read_context(FakeClient({"config": [ROW], "history": history}), FakeConnections(), PID)
        self.assertEqual(ctx["history"], history)
        self.assertIsNone(normalized_context({"config": [ROW]}, PID)["history"])  # --settings file


class HistoryLine(unittest.TestCase):
    def test_not_read_is_unknown_not_zero(self):
        self.assertIn("unknown, not zero", render_history(None))

    def test_nothing_logged_is_the_baseline(self):
        line = render_history({"changes": 0, "decisions": 0, "snapshots": 0}, "2026-09-26T04:05:58Z")
        self.assertEqual(line, "History (Supabase, all four audit tables): 0 PriceLabs changes logged, "
                               "0 pricing decisions, 0 market snapshots; settings last updated 2026-09-26. "
                               "Nothing logged yet: this run is the baseline.")

    def test_counts_carry_their_latest_date(self):
        line = render_history({"changes": 5, "changes_latest": "2026-09-26T06:55:57Z", "decisions": 1,
                               "decisions_latest": "2026-09-24", "snapshots": 3,
                               "snapshots_latest": "2026-09-24"})
        self.assertIn("5 PriceLabs changes logged (latest 2026-09-26), 1 pricing decision (latest "
                      "2026-09-24), 3 market snapshots (latest 2026-09-24).", line)
        self.assertNotIn("baseline", line)

    def test_every_card_carries_the_line(self):
        pack = direct_build(synthetic_bundle())
        text = render(pack, "run", METRICS)
        self.assertIn("History: not read this run", text)  # the fixture context has no history
        bundle = synthetic_bundle()
        bundle["inputs"]["context"]["history"] = {"changes": 0, "decisions": 0, "snapshots": 0}
        text = render(direct_build(bundle), "run", METRICS)
        self.assertIn("History (Supabase, all four audit tables): 0 PriceLabs changes logged", text)
        self.assertLess(text.index("History"), text.index("occ% = "))


if __name__ == "__main__":
    unittest.main()
