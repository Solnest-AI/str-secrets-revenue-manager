"""Offline contracts for the Beyond reader (_beyond.BeyondSource), DOCS-ONLY.

Reads go through the metered ReadClient, which lets a provider GET and nothing else.
The fake is the documented API shape (see test_beyond_write.FakeBeyond), not a recording.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import _beyond as B
from _mvp_store import CannotAnalyze, ReadClient, Store
from test_beyond_write import LID, TODAY, TOKEN, FakeBeyond, Response


class FakeAccount(FakeBeyond):
    """Adds GET /api/v1/listings/ with a page size of 2, to force real pagination."""

    def __init__(self, n=3, **kw):
        super().__init__(**kw)
        self.ids = [LID] + [str(90000 + i) for i in range(1, n)]

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        if req.get_method() == "GET" and url.path == "/api/v1/listings/":
            self.requests.append(("GET", url.path, parse_qs(url.query), None))
            n = int(parse_qs(url.query)["page[number]"][0])
            pages = (len(self.ids) + 1) // 2
            rows = []
            for lid in self.ids[(n - 1) * 2:n * 2]:
                doc = self.listing_doc()["data"]
                rows.append({**doc, "id": int(lid)})
            return Response({"data": rows, "meta": {"pagination": {
                "page": n, "pages": pages, "count": len(self.ids)}}})
        return super().open(req, timeout)


class Conn:
    def key(self, provider):
        assert provider == "beyond"
        return TOKEN

    def account(self, provider):
        return "acct-" + provider


class Reads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "wb.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def source(self, fake, max_calls=40):
        self.client = ReadClient(self.store, max_calls, opener=fake)
        return B.BeyondSource(self.client, Conn())

    def test_listing_is_normalized_in_major_units(self):
        src = self.source(FakeBeyond())
        row = src.listing(LID)
        self.assertEqual((row["id"], row["pms"], row["currency"]), (LID, "beyond", "USD"))
        self.assertEqual((row["min"], row["base"], row["max"], row["min_stay"]), (150.0, 200.0, 400.0, 2))
        self.assertEqual(row["channels"], [{"channel": "airbnb", "channel_id": "777"}])

    def test_calendar_carries_the_price_beyond_pushes_never_divided(self):
        fake = FakeBeyond()
        src = self.source(fake)
        cal = src.calendar(LID, TODAY, 90)
        self.assertEqual(len(cal["data"]), 90)
        first = cal["data"][0]
        self.assertEqual((first["date"], first["price"], first["price_posted"]), ("2026-10-01", 160.0, 160.0))
        self.assertIsNone(first["min_stay"])  # Beyond's calendar has no per-date min stay
        fixed = next(r for r in cal["data"] if r["date"] == "2026-10-10")
        self.assertEqual((fixed["price"], fixed["override_type"]), (250.0, "fixed"))
        self.assertEqual(cal["last_refreshed_at"], "2026-10-01T10:00:00Z")
        q = next(r[2] for r in fake.requests if r[1].endswith("/calendar/"))
        self.assertEqual((q["filter[start-date]"], q["filter[end-date]"]), (["2026-10-01"], ["2026-12-29"]))

    def test_a_calendar_that_is_not_the_asked_window_is_refused(self):
        # documented trap: a misspelt filter returns the default window with a 200
        fake = FakeBeyond()
        raw_rows = [fake.entry((TODAY + timedelta(days=i)).isoformat()) for i in range(10)]
        raw = {"data": raw_rows, "meta": {"pagination": {"page": 1, "pages": 1, "count": 10}}}
        with self.assertRaises(CannotAnalyze):
            B.parse_calendar(raw, TODAY + timedelta(days=1), TODAY + timedelta(days=10))
        raw["meta"]["pagination"]["pages"] = 2
        with self.assertRaises(CannotAnalyze):
            B.parse_calendar(raw, TODAY, TODAY + timedelta(days=9))

    def test_customizations_need_every_family(self):
        fake = FakeBeyond()
        cust = self.source(fake).customizations(LID)
        self.assertEqual(set(cust["raw"]), set(B.AGGREGATE_KEYS))
        del fake.cust["min-stays"]
        with self.assertRaises(CannotAnalyze):
            self.source(fake).customizations(LID)

    def test_overrides_are_per_date_and_window_checked(self):
        src = self.source(FakeBeyond())
        rows = src.overrides(LID, TODAY, 90)
        self.assertEqual(rows, [{"date": "2026-10-10", "price": 250.0},
                                {"date": "2026-11-20", "percentage_adjustment": -10}])
        doc = lambda rows: {"data": {"type": "manual-override-customizations", "id": LID,  # noqa: E731
                                     "attributes": {"overrides": rows}}}
        for bad in ([{"start-date": "2026-10-10", "end-date": "2026-10-12", "price": 250}],
                    [{"start-date": "2026-10-10", "end-date": "2026-10-10", "price": 250,
                      "percentage-adjustment": 5}],
                    [{"start-date": "2026-10-10", "end-date": "2026-10-10"}],
                    [{"start-date": "2026-10-10", "end-date": "2026-10-10", "price": 1}] * 2):
            with self.assertRaises(CannotAnalyze):
                B.parse_overrides(doc(bad), LID)
        with self.assertRaises(CannotAnalyze):
            B.parse_overrides({"data": {"type": "manual-override-customizations", "id": "1",
                                        "attributes": {"overrides": []}}}, LID)

    def test_listings_page_through_and_count(self):
        fake = FakeAccount(n=3)
        rows = self.source(fake).listings()
        self.assertEqual([r["id"] for r in rows], fake.ids)
        pages = [r[2]["page[number]"] for r in fake.requests if r[1] == "/api/v1/listings/"]
        self.assertEqual(pages, [["1"], ["2"]])

    def test_the_read_transport_refuses_a_beyond_write(self):
        client = ReadClient(self.store, 5, opener=FakeBeyond())
        with self.assertRaises(CannotAnalyze):
            client.request("beyond", "base-price", f"https://{B.HOST}/api/v1/listings/{LID}/customizations/base-price/",
                           headers={}, body={"data": {}})

    def test_a_missing_trailing_slash_is_an_error_not_a_redirect(self):
        client = ReadClient(self.store, 5, opener=FakeBeyond())
        with self.assertRaises(CannotAnalyze) as ctx:
            client.request("beyond", "listings", f"https://{B.HOST}/api/v1/listings/{LID}",
                           headers={"Authorization": "Bearer " + TOKEN, "Accept": B.MEDIA})
        self.assertIn("HTTP 301", str(ctx.exception))

    def test_listing_ids_are_whole_numbers(self):
        for bad in ("abc", "1/../2", "", None, True, "1.5"):
            with self.assertRaises(CannotAnalyze):
                B.listing_id(bad)
        self.assertEqual(B.listing_id(48213), "48213")

    def test_no_currency_is_refused(self):
        fake = FakeBeyond(currency=None)
        with self.assertRaises(CannotAnalyze):
            self.source(fake).listing(LID)


if __name__ == "__main__":
    unittest.main()
