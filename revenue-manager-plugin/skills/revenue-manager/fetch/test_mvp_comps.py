"""Synthetic AirROI contracts using the real metered transport and private store."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from _mvp_comps import fetch_comps
from _mvp_store import CannotAnalyze, ReadClient, Store
from factcheck import AIRROI_FACTS, airroi_facts_full, compare


METADATA = {"latitude": "40.0", "longitude": "-80.0", "no_of_bedrooms": 1, "currency": "CAD"}
PROPERTY = {"capacity": {"bedrooms": 1, "bathrooms": 1, "max": 3}, "currency": "CAD"}


def comp(listing_id, *, currency="CAD", bedrooms=1, baths=1, guests=3, revenue=25000):
    return {
        "listing_info": {"listing_id": listing_id, "listing_name": f"Test home {listing_id}",
                         "description": "PRIVATE_DESCRIPTION_MARKER", "photos": ["PRIVATE_PHOTO"]},
        "property_details": {"bedrooms": bedrooms, "baths": baths, "guests": guests},
        "performance_metrics": {"ttm_revenue": revenue, "ttm_avg_rate": 123.45,
                                "ttm_occupancy": 0.6544, "ttm_revpar": 80.71,
                                "ttm_avg_length_of_stay": 3.5},
        "ratings": {"rating_overall": 4.912, "num_reviews": 32},
        "pricing_info": {"currency": currency},
        "booking_settings": {"min_nights": 1},
        "account_email": "PRIVATE_EMAIL_MARKER",
    }


class Connections:
    def __init__(self, missing=False, account="test-account-fingerprint"):
        self.missing = missing
        self.account_id = account

    def key(self, provider):
        if self.missing:
            raise CannotAnalyze("Missing AIRROI_API_KEY")
        return "synthetic-key-placeholder"

    def account(self, provider):
        return self.account_id


class Response:
    status = 200

    def __init__(self, data):
        self.data = data
        self.headers = {}

    def read(self):
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, data):
        self.data = data
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return Response(self.data)


class CompsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "test.sqlite")
        self.connections = Connections()
        self.opener = Opener({"listings": [comp("100", revenue=40000), comp("101"), comp("102")]})
        self.client = ReadClient(self.store, opener=self.opener)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def fetch(self, **kwargs):
        return fetch_comps(kwargs.pop("client", self.client),
                           kwargs.pop("connections", self.connections),
                           kwargs.pop("listing_metadata", METADATA),
                           kwargs.pop("pms_property", PROPERTY),
                           kwargs.pop("subject_airbnb_id", "100"), **kwargs)

    def stored_payloads(self):
        return [row[0] for row in self.store.db.execute("SELECT payload FROM source").fetchall()]

    def test_native_request_is_metered_and_subject_is_excluded(self):
        result = self.fetch()
        self.assertEqual(result["status"], "ok")
        request = self.opener.requests[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["currency"], ["native"])
        self.assertEqual(query["latitude"], ["40.0"])
        self.assertEqual(query["longitude"], ["-80.0"])
        self.assertEqual(query["bedrooms"], ["1"])
        self.assertEqual(query["guests"], ["3"])
        self.assertEqual(request.get_header("X-api-key"), "synthetic-key-placeholder")
        self.assertEqual(self.client.metrics()["by_provider"], {"airroi": 1})
        self.assertEqual(result["subject_rank_revenue"], 1)
        self.assertTrue(result["subject_in_set"])
        self.assertEqual(result["exclusions"]["subject"], 1)
        self.assertEqual({row["listing_id"] for row in result["comps"]}, {"101", "102"})

    def test_thirteen_fields_preserve_all_existing_decision_facts(self):
        result = self.fetch()
        self.assertTrue(all(len(row) == 13 for row in result["comps"]))
        self.assertEqual(result["comps"][0]["los"], 3.5)
        self.assertEqual(result["currency"], "CAD")
        expected = airroi_facts_full(self.opener.data, subject_id="100")
        observed = {**result["summary"], "currency": result["currency"],
                    "subject_in_set": result["subject_in_set"],
                    "subject_rank_revenue": result["subject_rank_revenue"]}
        self.assertEqual(compare(expected, observed, AIRROI_FACTS), [])
        self.assertEqual(result["period"], "trailing_12_months")
        self.assertIn("not forward", result["use"])

    def test_normalization_happens_before_persistence(self):
        self.fetch()
        self.assertEqual(len(self.stored_payloads()), 1)
        payload = self.stored_payloads()[0]
        for marker in ("PRIVATE_DESCRIPTION", "PRIVATE_PHOTO", "PRIVATE_EMAIL", "synthetic-key"):
            self.assertNotIn(marker, payload)
        self.assertNotIn('"listing_info"', payload)
        self.assertEqual(len(json.loads(payload)["comps"][0]), 13)

    def test_mixed_missing_or_wrong_currency_refuses_before_any_persistence(self):
        for currency in ("USD", None, ""):
            with self.subTest(currency=currency):
                self.opener.data = {"listings": [comp("100", currency=currency), comp("101")]}
                result = self.fetch()
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["comps"], [])
                self.assertEqual(self.stored_payloads(), [])

    def test_bedrooms_baths_and_known_capacity_subset_remain_distinct(self):
        self.opener.data = {"listings": [comp("101", guests=4), comp("102", guests=2),
                                        comp("103", guests=None), comp("104", bedrooms=2),
                                        comp("105", baths=2), comp("106", bedrooms=None)]}
        result = self.fetch()
        self.assertEqual(result["summary"]["comp_count"], 3)
        self.assertEqual(result["capacity_subset"]["count"], 1)
        self.assertEqual(result["capacity_subset"]["listing_ids"], ["101"])
        self.assertEqual(result["capacity_subset"]["smaller_capacity_count"], 1)
        self.assertEqual(result["capacity_subset"]["unknown_capacity_count"], 1)
        self.assertEqual(result["exclusions"]["bedrooms_or_baths"], 2)
        self.assertEqual(result["exclusions"]["unknown_bedrooms_or_baths"], 1)

    def test_seven_day_cache_avoids_second_call_but_refresh_uses_live_read(self):
        first = self.fetch()
        second_client = ReadClient(self.store, opener=self.opener)
        second = self.fetch(client=second_client)
        self.assertEqual(second, first)
        self.assertEqual(second_client.metrics()["http_calls"], 0)
        self.assertEqual(second_client.metrics()["cache_hits"], 1)
        third_client = ReadClient(self.store, opener=self.opener)
        self.fetch(client=third_client, refresh=True)
        self.assertEqual(third_client.metrics()["http_calls"], 1)
        self.assertEqual(len(self.opener.requests), 2)

    def test_cache_isolated_by_subject_account_and_exact_location(self):
        self.fetch()
        for kwargs in ({"subject_airbnb_id": "102"},
                       {"connections": Connections(account="another-account")},
                       {"listing_metadata": {**METADATA, "latitude": "40.000001"}}):
            with self.subTest(keys=list(kwargs)):
                client = ReadClient(self.store, opener=self.opener)
                result = self.fetch(client=client, **kwargs)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(client.metrics()["http_calls"], 1)

    def test_missing_optional_key_never_makes_request(self):
        result = self.fetch(connections=Connections(missing=True))
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["optional"])
        self.assertIn("AIRROI_API_KEY", result["reason"])
        self.assertEqual(self.client.metrics()["http_calls"], 0)

    def test_http_failure_is_optional_metered_and_does_not_expose_provider_body(self):
        class RejectedOpener:
            def open(self, request, timeout):
                error = urllib.error.HTTPError(request.full_url, 403, "PRIVATE_PROVIDER_ERROR",
                                               {}, io.BytesIO())
                error.close()
                raise error

        client = ReadClient(self.store, opener=RejectedOpener())
        result = self.fetch(client=client)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("HTTP 403", result["reason"])
        self.assertNotIn("PRIVATE_PROVIDER_ERROR", json.dumps(result))
        self.assertEqual(client.metrics()["http_calls"], 1)
        self.assertEqual(self.stored_payloads(), [])

    def test_missing_subject_and_inconsistent_property_units_do_not_fetch(self):
        for kwargs in ({"subject_airbnb_id": ""},
                       {"listing_metadata": {**METADATA, "currency": "USD"}},
                       {"listing_metadata": {**METADATA, "no_of_bedrooms": 2}}):
            result = self.fetch(**kwargs)
            self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.client.metrics()["http_calls"], 0)

    def test_studio_and_zero_coordinates_are_preserved(self):
        self.opener.data = {"listings": [comp("101", bedrooms=0, baths=0)]}
        result = self.fetch(listing_metadata={**METADATA, "latitude": 0, "longitude": 0,
                                             "no_of_bedrooms": 0},
                            pms_property={**PROPERTY, "capacity": {"bedrooms": 0,
                                                                   "bathrooms": 0, "max": 3}})
        self.assertEqual(result["status"], "ok")
        query = parse_qs(urlsplit(self.opener.requests[0].full_url).query)
        for field in ("latitude", "longitude", "baths"):
            self.assertEqual(query[field], ["0.0"])
        self.assertEqual(query["bedrooms"], ["0"])

    def test_duplicate_rows_do_not_inflate_market_count(self):
        self.opener.data = {"listings": [comp("101"), comp("101")]}
        result = self.fetch()
        self.assertEqual(result["summary"]["comp_count"], 1)
        self.assertEqual(result["exclusions"]["duplicate_rows"], 1)
        self.assertEqual(result["currency_rows_checked"], 2)

    def test_all_subject_or_no_compatible_properties_is_unavailable(self):
        for rows in ([comp("100")], [comp("101", bedrooms=4)], []):
            self.opener.data = {"listings": rows}
            result = self.fetch()
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(self.stored_payloads(), [])

    def test_invalid_numbers_stay_unknown_and_do_not_poison_summary(self):
        row = comp("101")
        row["performance_metrics"]["ttm_avg_rate"] = float("nan")
        row["performance_metrics"]["ttm_occupancy"] = 75
        self.opener.data = {"listings": [row]}
        result = self.fetch()
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["summary"]["adr_median"])
        self.assertIsNone(result["summary"]["occ_median"])
        self.assertEqual(result["summary"]["rows_with_ttm_adr"], 0)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
