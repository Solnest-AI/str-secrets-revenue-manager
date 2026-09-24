"""Offline contracts for fresh reads, request budgets, privacy and provider scope."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from _mvp_config import Connections, load_env, normalized_context, read_context
from _mvp_analysis import markups
from _mvp_sources import Sources
from _mvp_store import CannotAnalyze, ReadClient, Store, identity, utc_now
from analyze90 import compute


PROPERTY = "prop-test-primary-0001"  # not UUID-shaped on purpose: the leak scan flags the 8-4-4-4-12 shape
OTHER_PROPERTY = "prop-test-other-0002"
START = date(2025, 12, 20)


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class Opener:
    """A finite fake response queue that fails if unexpected requests are made."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected HTTP attempt in offline test")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeConnections:
    def key(self, provider):
        return "synthetic-key-for-" + provider

    def account(self, provider):
        return "synthetic-account-for-" + provider

    def rankbreeze_url(self):
        return "https://example.invalid/synthetic-rankbreeze-rpc"


def page(rows, *, current=1, last=1, total=None):
    return {
        "data": rows,
        "meta": {
            "current_page": current,
            "last_page": last,
            "total": len(rows) if total is None else total,
        },
    }


def reservation(record_id="reservation-1", property_id=PROPERTY):
    return {
        "id": record_id,
        "platform": "airbnb",
        "status": "accepted",
        "nights": 2,
        "properties": [{"id": property_id, "guest_email": "PRIVATE_PROPERTY_EMAIL"}],
        "booking_date": "2025-12-10T18:00:00Z",
        "arrival_date": "2025-12-21",
        "departure_date": "2025-12-23",
        "guest": {"name": "PRIVATE_GUEST_NAME"},
        "phone": "PRIVATE_PHONE",
        "notes": "PRIVATE_RESERVATION_NOTES",
        "reservation_status": {
            "current": {
                "status": "accepted",
                "created_at": "2025-12-10T18:00:00Z",
                "guest": "PRIVATE_STATUS_GUEST",
            }
        },
        "financials": {
            "currency": "CAD",
            "host": {
                "accommodation": {"amount": 24000, "name": "PRIVATE_FINANCIAL_NAME"},
                "discounts": [{"amount": -2000, "label": "PRIVATE_DISCOUNT_LABEL"}],
                "accommodation_breakdown": [
                    {"date": "2025-12-21", "amount": 12000, "note": "PRIVATE_NIGHT_NOTE"},
                    {"date": "2025-12-22", "amount": 12000},
                ],
            },
        },
    }


def price_payload(**changes):
    result = {
        "id": PROPERTY,
        "pms": "smartbnb",
        "currency": "CAD",
        "last_refreshed_at": "2025-12-20T08:00:00Z",
        "data": [
            {
                "date": (START + timedelta(days=index)).isoformat(),
                "price": 120 + index,
                "uncustomized_price": 130,
                "min_stay": 1,
                "booking_status": "",
                "unbookable": 0,
                "user_price": 999,
                "reason": {"guest": "PRIVATE_PRICE_REASON"},
            }
            for index in range(2)
        ],
    }
    result.update(changes)
    return [result]


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "workbench.sqlite")
        self.addCleanup(self.store.close)
        # Even accidental fallbacks must never reach the network.
        self.network = patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("Network is forbidden in these tests"),
        )
        self.network.start()
        self.addCleanup(self.network.stop)

    def client(self, *responses, max_calls=40):
        self.opener = Opener(*responses)
        return ReadClient(self.store, max_calls=max_calls, opener=self.opener)

    def cached_payloads(self):
        return "\n".join(row[0] for row in self.store.db.execute("SELECT payload FROM source"))


class StoreTests(StoreCase):
    def test_critical_sources_are_fresh_across_runs_but_reused_within_run(self):
        sources = (
            "pms.property",
            "pms.calendar",
            "pms.reservations",
            "pms.reviews",
            "prices.metadata",
            "prices",
            "overrides",
            "rules",
            "rankbreeze.funnel",
            "rankbreeze.rankings",
            "context",
        )
        for source in sources:
            with self.subTest(source=source):
                old = self.client()
                old.fetch(source, [PROPERTY], lambda: {"value": "earlier"}, ttl_seconds=3600)
                current = self.client()
                loader = Mock(return_value={"value": "current"})
                self.assertEqual(
                    current.fetch(source, [PROPERTY], loader, ttl_seconds=3600),
                    {"value": "current"},
                )
                self.assertEqual(
                    current.fetch(source, [PROPERTY], loader, ttl_seconds=3600),
                    {"value": "current"},
                )
                loader.assert_called_once_with()

    def test_slow_comp_cache_reused_without_loader_or_http(self):
        self.client().fetch(
            "neighborhood", [PROPERTY, "CAD"], lambda: {"p50": 123}, ttl_seconds=3600
        )
        current = self.client()
        loader = Mock(side_effect=AssertionError("A fresh comp cache should be reused"))
        self.assertEqual(
            current.fetch("neighborhood", [PROPERTY, "CAD"], loader, ttl_seconds=3600), {"p50": 123}
        )
        loader.assert_not_called()
        self.assertEqual(current.metrics()["cache_hits"], 1)
        self.assertEqual(current.metrics()["http_calls"], 0)

    def test_expired_and_future_timestamps_cannot_be_cache_hits(self):
        now = datetime.now(timezone.utc)
        for stamp in (now - timedelta(hours=2), now + timedelta(hours=2)):
            with self.subTest(stamp=stamp.isoformat()):
                key = identity(["mvp-v1", "neighborhood", [PROPERTY]])
                self.store.source(key, stamp.isoformat(), {"p50": 1})
                loader = Mock(return_value={"p50": 123})
                result = self.client().fetch("neighborhood", [PROPERTY], loader, ttl_seconds=3600)
                self.assertEqual(result["p50"], 123)
                loader.assert_called_once_with()

    def test_cache_identity_separates_account_currency_and_window(self):
        first = ["account-a", PROPERTY, "CAD", "2025-12-20", 90]
        self.client().fetch("neighborhood", first, lambda: {"p50": 123}, ttl_seconds=3600)
        for request_id in (
            ["account-b", *first[1:]],
            [*first[:2], "USD", *first[3:]],
            [*first[:3], "2025-12-21", 90],
            [*first[:4], 30],
        ):
            with self.subTest(identity=request_id):
                loader = Mock(return_value={"p50": 456})
                self.assertEqual(
                    self.client().fetch("neighborhood", request_id, loader, ttl_seconds=3600)[
                        "p50"
                    ],
                    456,
                )
                loader.assert_called_once_with()

    def test_failed_normalization_does_not_persist_a_source(self):
        client = self.client()
        with self.assertRaisesRegex(CannotAnalyze, "incomplete"):
            client.fetch(
                "pms.calendar", [PROPERTY], Mock(side_effect=CannotAnalyze("incomplete source"))
            )
        self.assertEqual(self.cached_payloads(), "")
        self.assertEqual(client.metrics()["sources"], [])

    def test_private_store_roundtrip_and_missing_run(self):
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        run = {"started_at": utc_now(), "status": "complete", "evidence": {"nights": 90}}
        self.store.save_run("synthetic-run", run)
        self.assertEqual(self.store.get_run("synthetic-run"), run)
        with self.assertRaises(CannotAnalyze):
            self.store.get_run("absent")

    def test_retry_and_failure_are_counted_as_physical_attempts(self):
        err = HTTPError(
            "https://example.invalid/private-token",
            429,
            "PRIVATE_MESSAGE",
            {"Retry-After": "0"},
            io.BytesIO(b"PRIVATE_PROVIDER_BODY"),
        )
        self.addCleanup(err.close)
        client = self.client(err, Response({"data": []}))
        with patch("_mvp_store.time.sleep"):
            self.assertEqual(
                client.request("hospitable", "reviews", "https://example.invalid")[0], {"data": []}
            )
        metrics = client.metrics()
        self.assertEqual(metrics["http_calls"], 2)
        self.assertEqual(metrics["by_provider"], {"hospitable": 2})
        self.assertEqual([r["status"] for r in metrics["attempts"]], [429, 200])
        self.assertEqual([r["attempt"] for r in metrics["attempts"]], [1, 2])
        self.assertNotIn("PRIVATE", json.dumps(metrics))

    def test_call_budget_prevents_retry_and_later_requests(self):
        err = HTTPError("https://example.invalid", 429, "rate", {"Retry-After": "0"}, None)
        self.addCleanup(err.close)
        client = self.client(err, Response({}), max_calls=1)
        with patch("_mvp_store.time.sleep"), self.assertRaisesRegex(CannotAnalyze, "budget"):
            client.request("hospitable", "reviews", "https://example.invalid")
        self.assertEqual(len(self.opener.requests), 1)
        with self.assertRaisesRegex(CannotAnalyze, "budget"):
            client.request("hospitable", "calendar", "https://example.invalid")
        self.assertEqual(client.metrics()["http_calls"], 1)

    def test_http_and_connection_errors_never_expose_body_url_or_credentials(self):
        secret_url = "https://example.invalid/PRIVATE_URL?token=PRIVATE_TOKEN"
        errors = (
            HTTPError(secret_url, 403, "PRIVATE_MESSAGE", {}, io.BytesIO(b"PRIVATE_BODY")),
            URLError("PRIVATE_CONNECTION_ERROR " + secret_url),
            Response(b"PRIVATE_INVALID_JSON"),
        )
        for response in errors:
            with self.subTest(response=type(response).__name__):
                if isinstance(response, HTTPError):
                    self.addCleanup(response.close)
                client = self.client(response)
                with self.assertRaises(CannotAnalyze) as caught:
                    client.request(
                        "hospitable",
                        "reviews",
                        secret_url,
                        headers={"Authorization": "Bearer PRIVATE_KEY"},
                    )
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertNotIn("PRIVATE", json.dumps(client.metrics()))
                self.assertEqual(client.metrics()["http_calls"], 1)

    def test_mutations_are_rejected_before_http(self):
        cases = (
            ("pricelabs", "overrides", "https://api.pricelabs.co/v1/listings/test/overrides", {}),
            ("hospitable", "calendar", "https://public.api.hospitable.com/v2/calendar", {}),
            (
                "supabase",
                "context",
                "https://api.supabase.com/v1/projects/test/database/query",
                {"read_only": True, "query": "DELETE FROM property_config"},
            ),
            (
                "rankbreeze",
                "rpc",
                "https://example.invalid/mcp",
                {"method": "tools/call", "params": {"name": "update_listing"}},
            ),
        )
        for provider, operation, url, body in cases:
            with self.subTest(provider=provider):
                client = self.client()
                with self.assertRaises(CannotAnalyze):
                    client.request(provider, operation, url, body=body)
                self.assertEqual(client.metrics()["http_calls"], 0)
                self.assertEqual(self.opener.requests, [])

    def test_read_label_cannot_authorize_a_mutation_url_or_foreign_origin(self):
        for url in (
            "https://api.pricelabs.co/v1/listings",
            "https://api.pricelabs.co/v1/listings/test/overrides",
            "https://example.invalid/v1/listing_prices",
        ):
            with self.subTest(url=url):
                client = self.client(Response({"success": True}))
                with self.assertRaises(CannotAnalyze):
                    client.request(
                        "pricelabs",
                        "listing_prices",
                        url,
                        body={"listings": [{"id": PROPERTY, "base": 1}]},
                    )
                self.assertEqual(client.metrics()["http_calls"], 0)
                self.assertEqual(self.opener.requests, [])

    def test_read_only_price_post_remains_usable(self):
        client = self.client(Response({"data": []}))
        client.request(
            "pricelabs",
            "listing_prices",
            "https://api.pricelabs.co/v1/listing_prices",
            body={
                "listings": [
                    {
                        "id": PROPERTY,
                        "pms": "smartbnb",
                        "dateFrom": "2025-12-20",
                        "dateTo": "2025-12-21",
                    }
                ]
            },
        )
        self.assertEqual(self.opener.requests[0].method, "POST")
        self.assertEqual(client.metrics()["http_calls"], 1)


class SourceTests(StoreCase):
    def sources(self, *responses):
        client = self.client(*(Response(value) for value in responses))
        return Sources(client, FakeConnections())

    def test_reservations_are_normalized_before_persistence(self):
        sources = self.sources(page([reservation()]))
        result = sources.reservations(PROPERTY, START, 90)
        row = result["data"][0]
        self.assertEqual(row["property_ids"], [PROPERTY])
        self.assertEqual(row["financials"]["host_accommodation_cents"], 24000)
        self.assertEqual(row["financials"]["host_discounts"][0]["amount_cents"], -2000)
        self.assertEqual(
            row["financials"]["host_accommodation_breakdown"][0]["amount_cents"], 12000
        )
        self.assertNotIn("PRIVATE", self.cached_payloads())
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_ninety_day_analysis_fetches_a_year_of_future_pickup_evidence(self):
        later = reservation("reservation-after-calendar")
        later.update(
            booking_date="2025-12-20T10:00:00Z",
            arrival_date=(START + timedelta(days=120)).isoformat(),
            departure_date=(START + timedelta(days=122)).isoformat(),
        )
        sources = self.sources(page([reservation(), later]))
        result = sources.reservations(PROPERTY, START, 90)
        query = parse_qs(urlsplit(self.opener.requests[0].full_url).query)
        requested_end = date.fromisoformat(query["end_date"][0])
        self.assertGreaterEqual((requested_end - START).days, 365)
        self.assertTrue(result["complete"])
        self.assertEqual(result["total"], 2)
        saved_later = next(row for row in result["data"] if row["id"] == later["id"])
        self.assertEqual(saved_later["booking_date"], "2025-12-20T10:00:00Z")
        self.assertEqual(saved_later["arrival_date"], later["arrival_date"])

    def test_foreign_or_unscoped_reservations_fail_without_cache(self):
        for property_id in (OTHER_PROPERTY, None):
            with self.subTest(property_id=property_id):
                sources = self.sources(page([reservation(property_id=property_id)]))
                with self.assertRaisesRegex(CannotAnalyze, "scope"):
                    sources.reservations(PROPERTY, START, 90)
                self.assertEqual(self.cached_payloads(), "")

    def test_pagination_rejects_duplicate_ids_and_changed_totals(self):
        cases = (
            page([reservation()], current=2, last=2, total=2),
            page([reservation("reservation-2")], current=2, last=2, total=3),
        )
        for second in cases:
            with self.subTest(second=second["meta"]):
                sources = self.sources(page([reservation()], last=2, total=2), second)
                with self.assertRaises(CannotAnalyze):
                    sources.reservations(PROPERTY, START, 90)
                self.assertEqual(self.cached_payloads(), "")

    def test_pagination_requires_complete_consistent_metadata(self):
        bad = (
            {"data": [reservation()]},
            page([reservation()], current=2),
            page([reservation()], total=2),
            page([reservation()], total="1"),
            page([reservation()], total=True),
            page([reservation()], last=True),
            page([reservation()], current=True),
        )
        for payload in bad:
            with self.subTest(meta=payload.get("meta")):
                sources = self.sources(payload)
                with self.assertRaises(CannotAnalyze):
                    sources.reservations(PROPERTY, START, 90)
                self.assertEqual(self.cached_payloads(), "")

    def test_missing_and_null_ids_cannot_make_a_complete_collection(self):
        for record_id in (None, ""):
            with self.subTest(record_id=record_id):
                before = self.cached_payloads()
                sources = self.sources(page([reservation(record_id)]))
                with self.assertRaises(CannotAnalyze):
                    sources.reservations(PROPERTY, START, 90)
                self.assertEqual(self.cached_payloads(), before)

    def test_complete_empty_and_paginated_histories_are_distinguishable(self):
        empty = self.sources(page([])).reservations(PROPERTY, START, 90)
        self.assertEqual(empty, {"data": [], "total": 0, "complete": True, "pages": 1})
        sources = self.sources(
            page([reservation()], last=2, total=2),
            page([reservation("reservation-2")], current=2, last=2, total=2),
        )
        complete = sources.reservations(PROPERTY, START, 90)
        self.assertTrue(complete["complete"])
        self.assertEqual(complete["pages"], 2)
        self.assertEqual(len(complete["data"]), 2)

    def test_review_limit_reports_partial_coverage_and_discards_text(self):
        reviews = [
            {
                "id": f"review-{i}",
                "reviewed_at": "2025-12-19T12:00:00Z",
                "public": {"rating": 5, "review": "PRIVATE_REVIEW_TEXT"},
                "guest": {"name": "PRIVATE_REVIEW_GUEST"},
                "private": {
                    "text": "PRIVATE_REVIEW_NOTE",
                    "detailed_ratings": [
                        {"type": "cleanliness", "rating": 5, "note": "PRIVATE_CATEGORY_NOTE"}
                    ],
                },
            }
            for i in range(100)
        ]
        sources = self.sources(page(reviews, last=2, total=101))
        result = sources.reviews(PROPERTY)
        self.assertFalse(result["complete"])
        self.assertEqual((len(result["data"]), result["total"], result["pages"]), (100, 101, 1))
        self.assertEqual(sources.client.metrics()["http_calls"], 1)
        self.assertNotIn("PRIVATE", self.cached_payloads())

    def test_property_identity_and_active_state_are_required(self):
        for raw in ({"id": OTHER_PROPERTY, "listed": True}, {"id": PROPERTY, "listed": False}):
            with self.subTest(raw=raw):
                sources = self.sources({"data": raw})
                with self.assertRaises(CannotAnalyze):
                    sources.property(PROPERTY)
                self.assertEqual(self.cached_payloads(), "")

    def test_prices_reject_wrong_listing_pms_currency_and_missing_dates(self):
        cases = (
            price_payload(id=OTHER_PROPERTY),
            price_payload(pms="foreign-pms"),
            price_payload(currency="USD"),
            price_payload(currency=None),
            price_payload(data=price_payload()[0]["data"][:1]),
            price_payload(data=price_payload()[0]["data"] * 2),
        )
        for payload in cases:
            with self.subTest(payload=payload[0].get("currency")):
                sources = self.sources(payload)
                with self.assertRaises(ValueError):
                    sources.prices(PROPERTY, "smartbnb", "CAD", START, 2)
                self.assertEqual(self.cached_payloads(), "")

    def test_price_storage_drops_stale_ask_and_bulk_reason(self):
        sources = self.sources(price_payload())
        result = sources.prices(PROPERTY, "smartbnb", "CAD", START, 2)
        self.assertEqual([row["price"] for row in result["data"]], [120, 121])
        self.assertNotIn("user_price", self.cached_payloads())
        self.assertNotIn("PRIVATE", self.cached_payloads())
        self.assertNotIn('"reason"', self.cached_payloads())

    def test_whole_float_bedrooms_use_the_exact_integer_market_category(self):
        dates = [(START + timedelta(days=i)).isoformat() for i in range(2)]
        raw = {
            "data": {
                "currency": "CAD",
                "Future Percentile Prices": {
                    "Labels": [
                        "25th Percentile",
                        "50th Percentile",
                        "75th Percentile",
                        "90th Percentile",
                    ],
                    "Category": {
                        "1": {
                            "Listings Used": 25,
                            "X_values": dates,
                            "Y_values": [[90, 90], [100, 100], [130, 130], [150, 150]],
                        }
                    },
                },
                "Future Occ/New/Canc": {
                    "Labels": ["Occupancy"],
                    "Category": {
                        "1": {
                            "X_values": dates,
                            "Y_values": [[[60, 60]]],
                        }
                    },
                },
                "Summary Table Base Price": {
                    "Labels": [
                        "25th Percentile",
                        "50th Percentile",
                        "75th Percentile",
                        "90th Percentile",
                    ],
                    "Category": {"1": {"Y_values": [90, 100, 130, 150]}},
                },
            }
        }
        result = self.sources(raw).neighborhood(PROPERTY, "smartbnb", 1.0, "CAD", START, 2)
        self.assertEqual(result["category"], "1")
        self.assertEqual(result["listings_used"], 25)
        self.assertEqual([r["p50"] for r in result["data"]], [100, 100])

    def test_market_utc_rollover_does_not_shift_the_property_local_window(self):
        dates = [(START + timedelta(days=i)).isoformat() for i in (1, 2)]
        raw = {
            "data": {
                "currency": "CAD",
                "Future Percentile Prices": {
                    "Labels": [
                        "25th Percentile",
                        "50th Percentile",
                        "75th Percentile",
                        "90th Percentile",
                    ],
                    "Category": {
                        "1": {
                            "X_values": dates,
                            "Y_values": [[90, 90], [100, 100], [130, 130], [150, 150]],
                        }
                    },
                },
                "Future Occ/New/Canc": {
                    "Labels": ["Occupancy"],
                    "Category": {"1": {"X_values": dates, "Y_values": [[[60, 60]]]}},
                },
            }
        }
        with self.assertRaisesRegex(CannotAnalyze, "missing 1 requested date.*2025-12-20"):
            self.sources(raw).neighborhood(PROPERTY, "smartbnb", 1, "CAD", START, 2)
        self.assertEqual(self.cached_payloads(), "")

    def test_hosted_rankings_refuse_explicit_foreign_listing_identity(self):
        row = {"date": START.isoformat(), "guest_count": 1, "page": 1, "position": 5}
        for raw in (
            {"listing_id": 999, "rankings": [row]},
            {"listing_id": 123, "rankings": [dict(row, listing_id=999)]},
        ):
            with self.subTest(raw=raw):
                sources = self.sources(
                    {"jsonrpc": "2.0", "id": 1, "result": {}},
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {"content": [{"type": "text", "text": json.dumps(raw)}]},
                    },
                )
                with self.assertRaises(CannotAnalyze):
                    sources.rankings("123", START)
                self.assertEqual(self.cached_payloads(), "")

    def test_hosted_rankings_keep_current_dated_numeric_evidence_only(self):
        raw = {
            "listing_id": 123,
            "rankings": [
                {
                    "listing_id": 123,
                    "date": START.isoformat(),
                    "guest_count": 1,
                    "page": 1,
                    "position": 5,
                    "guest_name": "PRIVATE_RANKING_GUEST",
                }
            ],
        }
        sources = self.sources(
            {"jsonrpc": "2.0", "id": 1, "result": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": json.dumps(raw)}]},
            },
        )
        self.assertEqual(
            sources.rankings("123", START),
            [{"date": START.isoformat(), "guest_count": 1, "page": 1, "position": 5}],
        )
        self.assertNotIn("PRIVATE", self.cached_payloads())
        self.assertEqual(sources.client.metrics()["http_calls"], 2)

    def ranking_sources(self, *pages):
        responses = [{"jsonrpc": "2.0", "id": 1, "result": {}}]
        responses.extend(
            {
                "jsonrpc": "2.0",
                "id": index + 2,
                "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
            }
            for index, payload in enumerate(pages)
        )
        return self.sources(*responses)

    def test_sixty_mixed_date_ranking_rows_need_no_history_pull_when_today_is_complete(self):
        rows = [
            {
                "listing_id": 123,
                "date": (START - timedelta(days=days_ago)).isoformat(),
                "guest_count": guests,
                "page": 1,
                "position": 5 + guests,
                "guest": "PRIVATE_HISTORICAL_RANKING_GUEST",
            }
            for days_ago in range(20)
            for guests in (1, 2, 3)
        ]
        sources = self.ranking_sources(
            {
                "listing_id": 123,
                "rankings": rows,
                "total_count": 468,
                "nextCursor": "synthetic-cursor",
            }
        )
        result = sources.rankings("123", START, guest_capacity=3)
        self.assertEqual(len(result), 3)
        self.assertEqual({r["guest_count"] for r in result}, {1, 2, 3})
        self.assertEqual({r["date"] for r in result}, {START.isoformat()})
        self.assertEqual(sources.client.metrics()["http_calls"], 2)
        self.assertNotIn("PRIVATE", self.cached_payloads())

    def test_missing_current_guest_category_is_fetched_or_explicitly_refused(self):
        first = {
            "listing_id": 123,
            "rankings": [
                {"date": START.isoformat(), "guest_count": guest, "page": 1, "position": 6}
                for guest in (1, 2)
            ],
            "total_count": 468,
            "nextCursor": "synthetic-cursor",
        }
        second = {
            "listing_id": 123,
            "rankings": [{"date": START.isoformat(), "guest_count": 3, "page": 1, "position": 7}],
            "total_count": 468,
            "nextCursor": None,
        }
        sources = self.ranking_sources(first, second)
        try:
            result = sources.rankings("123", START, guest_capacity=3)
        except CannotAnalyze:
            self.assertEqual(self.cached_payloads(), "")
        else:
            self.assertEqual({r["guest_count"] for r in result}, {1, 2, 3})
            self.assertEqual(sources.client.metrics()["http_calls"], 3)

    def test_only_historical_rankings_cannot_be_current_evidence(self):
        raw = {
            "listing_id": 123,
            "rankings": [
                {
                    "date": (START - timedelta(days=1)).isoformat(),
                    "guest_count": 1,
                    "page": 1,
                    "position": 5,
                }
            ],
            "total_count": 1,
            "nextCursor": None,
        }
        sources = self.ranking_sources(raw)
        with self.assertRaises(CannotAnalyze):
            sources.rankings("123", START, guest_capacity=1)
        self.assertEqual(self.cached_payloads(), "")


class ConfigurationTests(unittest.TestCase):
    def test_env_parser_only_loads_known_connection_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                'export PRICELABS_API_KEY="synthetic-price-key"\n'
                "HOSPITABLE_TOKEN='synthetic-pms-token'\n"
                "UNRELATED_PASSWORD=private-unrelated\n"
            )
            self.assertEqual(
                load_env(path),
                {
                    "PRICELABS_API_KEY": "synthetic-price-key",
                    "HOSPITABLE_TOKEN": "synthetic-pms-token",
                },
            )

    def test_explicit_env_file_overrides_connector_and_environment_overrides_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "mcp.json"
            explicit = root / "explicit.env"
            config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "pricelabs": {"env": {"PRICELABS_API_KEY": "synthetic-connector"}},
                            "rankbreeze": {"env": {"RANKBREEZE_SESSION": "synthetic-session"}},
                        }
                    }
                )
            )

            def fake_env(path):
                return {"PRICELABS_API_KEY": "synthetic-explicit"} if path == explicit else {}

            with (
                patch("_mvp_config.load_env", side_effect=fake_env),
                patch.object(Path, "home", return_value=root),
                patch.object(Path, "cwd", return_value=root),
                patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(
                    Connections(config_path=config).key("pricelabs"), "synthetic-connector"
                )
                self.assertEqual(
                    Connections([explicit], config).key("pricelabs"), "synthetic-explicit"
                )
                with patch.dict(os.environ, {"PRICELABS_API_KEY": "synthetic-environment"}):
                    self.assertEqual(
                        Connections([explicit], config).key("pricelabs"), "synthetic-environment"
                    )

    def test_account_cache_identity_contains_no_plaintext_key(self):
        connections = Connections.__new__(Connections)
        connections.values = {"PRICELABS_API_KEY": "synthetic-secret-a"}
        first = connections.account("pricelabs")
        self.assertNotIn("synthetic-secret", first)
        self.assertEqual(len(first), 64)
        connections.values["PRICELABS_API_KEY"] = "synthetic-secret-b"
        self.assertNotEqual(connections.account("pricelabs"), first)

    def test_supabase_uses_named_revenue_connection_only(self):
        connections = Connections.__new__(Connections)
        connections.servers = {
            "supabase": {
                "args": ["--project-ref=wrong-project"],
                "env": {"SUPABASE_ACCESS_TOKEN": "wrong-token"},
            }
        }
        self.assertIsNone(connections.supabase())
        connections.servers["supabase-revenue-manager"] = {
            "args": ["--project-ref=syntheticproject"],
            "env": {"SUPABASE_ACCESS_TOKEN": "synthetic-token"},
        }
        self.assertEqual(connections.supabase(), ("syntheticproject", "synthetic-token"))

    def test_context_has_unique_property_and_allowlisted_settings(self):
        row = {
            "property_id": PROPERTY,
            "settings": {
                "channel_markup_pct": {"airbnb": 18.5},
                "channel_markup_source": "operator-confirmed",
                "api_key": "PRIVATE_KEY",
                "markup_pct": 0,
            },
            "guest": "PRIVATE_GUEST",
        }
        result = normalized_context({"config": [row]}, PROPERTY)
        self.assertEqual(result["settings"]["channel_markup_pct"], {"airbnb": 18.5})
        self.assertNotIn("markup_pct", result["settings"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        for rows in ([], [row, row], [dict(row, property_id=OTHER_PROPERTY)]):
            with self.subTest(count=len(rows)), self.assertRaises(CannotAnalyze):
                normalized_context({"config": rows}, PROPERTY)

    def test_wrong_property_settings_and_unsafe_ids_do_not_call_network(self):
        client = Mock()
        connections = Mock()
        connections.supabase.return_value = ("syntheticproject", "synthetic-token")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"property_id": OTHER_PROPERTY}))
            with self.assertRaises(CannotAnalyze):
                read_context(client, connections, PROPERTY, path)
        with self.assertRaises(CannotAnalyze):
            read_context(client, connections, "unsafe'; DELETE FROM data;--")
        client.request.assert_not_called()
        client.fetch.assert_not_called()


class MarkupProvenanceTests(unittest.TestCase):
    as_of = datetime(2025, 12, 20, 12, tzinfo=timezone.utc)

    def context(self, values=None, source=None):
        return {
            "settings": {
                "channel_markup_pct": {"airbnb": 18.5} if values is None else values,
                "channel_markup_source": {
                    "source_type": "user-confirmed screenshot",
                    "confirmed_at": "2025-12-19T12:00:00Z",
                }
                if source is None
                else source,
            }
        }

    def test_legacy_zero_and_inferred_calendar_ratios_cannot_be_markups(self):
        cases = (
            {"settings": {"markup_pct": 0}},
            self.context({"all": 0}),
            self.context(
                {"airbnb": 0},
                {"source_type": "calendar_sync_ratio", "confirmed_at": "2025-12-19T12:00:00Z"},
            ),
            self.context(
                {"airbnb": 0}, {"source_type": "unknown", "confirmed_at": "2025-12-19T12:00:00Z"}
            ),
        )
        for context in cases:
            with self.subTest(context=context), self.assertRaises(CannotAnalyze):
                markups(context, self.as_of)

    def test_explicit_dated_operator_confirmation_accepts_real_zero_or_positive_markup(self):
        for values in ({"airbnb": 0}, {"airbnb": 18.5, "direct": 10}):
            with self.subTest(values=values):
                self.assertEqual(markups(self.context(values), self.as_of), values)

    def test_missing_malformed_future_and_naive_confirmation_fail_cleanly(self):
        sources = (
            "operator-confirmed",
            [],
            {},
            {"source_type": "operator_confirmed"},
            {"source_type": "operator_confirmed", "confirmed_at": "2025-12-21T00:00:00Z"},
            {"source_type": "operator_confirmed", "confirmed_at": "2025-12-19T00:00:00"},
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(CannotAnalyze):
                markups(self.context(source=source), self.as_of)

    def test_nonfinite_negative_or_boolean_markup_cannot_enter_money_math(self):
        for value in (float("nan"), float("inf"), -1, True, None):
            with self.subTest(value=value), self.assertRaises(CannotAnalyze):
                markups(self.context({"airbnb": value}), self.as_of)


class AnalysisBoundaryTests(unittest.TestCase):
    """Exercise real PMS calculation and pricing gates together using synthetic data."""

    as_of = datetime(2025, 12, 20, 12, tzinfo=timezone.utc)

    def inputs(self):
        dates = [(START + timedelta(days=i)).isoformat() for i in range(7)]
        comparisons = {
            key: {"listing": 10, "similar_listings": 10}
            for key in (
                "first_page_impressions",
                "click_through_rate",
                "view",
                "wishlist",
                "booking_rate",
                "conversion_rate",
            )
        }
        return {
            "property": {
                "id": PROPERTY,
                "name": "Synthetic Suite",
                "currency": "CAD",
                "timezone": "UTC",
                "capacity": {"bedrooms": 1, "max": 2},
            },
            "calendar": [
                {
                    "date": d,
                    "price_cents": 12000,
                    "currency": "CAD",
                    "min_stay": 1,
                    "available": True,
                    "status_reason": "AVAILABLE",
                    "closed_for_checkin": False,
                    "closed_for_checkout": False,
                }
                for d in dates
            ],
            "reservations": {"data": [], "total": 0, "complete": True, "pages": 1},
            "reviews": {
                "data": [{"id": "review-1", "rating": 4.9, "reviewed_at": "2025-12-19T12:00:00Z"}],
                "total": 1,
                "complete": True,
                "pages": 1,
            },
            "context": {
                "settings": {
                    "channel_markup_pct": {"airbnb": 18.5},
                    "channel_markup_source": {
                        "source_type": "user-confirmed screenshot",
                        "confirmed_at": "2025-12-19T12:00:00Z",
                    },
                }
            },
            "listing": {"id": PROPERTY, "currency": "CAD", "min": 100, "base": 120, "max": 160},
            "prices": {
                "last_refreshed_at": "2025-12-20T08:00:00Z",
                "data": [
                    {
                        "date": d,
                        "price": 120,
                        "uncustomized_price": 130,
                        "min_stay": 1,
                        "booking_status": "",
                        "unbookable": 0,
                    }
                    for d in dates
                ],
            },
            "market": {
                "listings_used": 25,
                "base_percentiles": {"base_p50": 110},
                "data": [
                    {
                        "date": d,
                        "p25": 90,
                        "p50": 100,
                        "p75": 130,
                        "p90": 150,
                        "occ": 60,
                        "occ_stly": 60,
                    }
                    for d in dates
                ],
            },
            "overrides": [],
            "rules": {"raw": {}, "summary": []},
            "funnel": {
                "status": "ok",
                "last_sync_date": START.isoformat(),
                "current_month": "2025-12",
                "visibility_row": {
                    "integration_status": "active",
                    "similar_listings_comparison": comparisons,
                },
            },
            "rankings": [{"date": START.isoformat(), "page": 1, "position": 5}],
        }

    def test_verified_sources_produce_daily_coverage_and_bounded_review_ranges(self):
        result = compute(self.inputs(), self.as_of, START, 7)
        self.assertEqual(result["status"], "analysable")
        self.assertEqual(len(result["daily"]), 7)
        self.assertTrue(result["candidates"])
        for row in result["candidates"]:
            lower, upper = row["review_net_range"]
            self.assertTrue(100 <= lower <= upper <= 160)
            self.assertGreaterEqual(lower / row["net"], 0.85)

    def test_entirely_invalid_reviews_are_an_unreadable_spoke_not_zero_reviews(self):
        # Still an UNREADABLE spoke, never "zero reviews". Under PRD D12 (2026-09-20)
        # an unreadable context spoke degrades the run; it no longer blocks it.
        inputs = self.inputs()
        inputs["reviews"]["data"][0]["rating"] = "unreadable"
        result = compute(inputs, self.as_of, START, 7)
        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["flywheel"]["spokes"]["reviews"]["ok"])
        self.assertTrue(result["notes"][0].startswith("PRICED WITHOUT"))
        self.assertIn("reviews", result["notes"][0])

    def test_verified_empty_review_history_is_distinct_from_unreadable_reviews(self):
        inputs = self.inputs()
        inputs["reviews"] = {"data": [], "total": 0, "complete": True, "pages": 1}
        result = compute(inputs, self.as_of, START, 7)
        self.assertEqual(result["status"], "analysable")
        self.assertTrue(result["flywheel"]["spokes"]["reviews"]["ok"])

    def test_missing_funnel_degrades_but_sync_drift_still_blocks(self):
        # These were one test and they are two different things. A missing funnel is a
        # missing CONTEXT spoke: D12 degrades it and prices anyway. A PMS/PriceLabs
        # price drift is a RECONCILIATION failure, which is a gate on the input itself
        # and still blocks. Pricing over an unexplained mismatch is what the Step 4.9
        # gate exists to prevent.
        inputs = self.inputs()
        del inputs["funnel"]
        result = compute(inputs, self.as_of, START, 7)
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["notes"][0].startswith("PRICED WITHOUT"))
        self.assertIn("visibility", result["notes"][0])

        inputs = self.inputs()
        inputs["prices"]["data"][0]["price"] = 119
        result = compute(inputs, self.as_of, START, 7)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["candidates"], [])

    def test_fixed_override_above_ceiling_never_emits_an_out_of_bound_range(self):
        inputs = self.inputs()
        for row in inputs["calendar"]:
            row["price_cents"] = 20000
        for row in inputs["prices"]["data"]:
            row["price"] = 200
        inputs["overrides"] = [
            {"date": row["date"], "price": 200, "price_type": "fixed"} for row in inputs["calendar"]
        ]
        result = compute(inputs, self.as_of, START, 7)
        for row in result["candidates"]:
            lower, upper = row["review_net_range"]
            self.assertTrue(
                100 <= lower <= upper <= 160, "A range within 15% cannot fit below this ceiling"
            )
        self.assertTrue(result["blockers"] or any(row["flags"] for row in result["daily"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
