"""Offline request-contract regressions. Run with python -m unittest discover -s tests."""
import importlib.util
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import airroi_client as ar

spec = importlib.util.spec_from_file_location("airroi_server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
logging.getLogger("httpx").setLevel(logging.WARNING)


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"AIRROI_API_KEY": "test-only"}).start()
        self.client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.client.close)
        patch.object(ar.httpx, "Client", return_value=self.client).start()

    @staticmethod
    def respond(request):
        params = dict(request.url.params)
        if request.url.path == "/listings/comparables":
            return httpx.Response(200, json={"listings": [{"params": params}]})
        if request.url.path == "/listings/metrics/all":
            return httpx.Response(200, json={"results": [{"params": params}]})
        return httpx.Response(200, json={"params": params})

    def test_estimate_preserves_zero_latitude(self):
        result = server.get_estimate(2, 1, 4, lat=0.0, lng=36.0)
        self.assertEqual(result["params"]["lat"], "0.0")
        self.assertEqual(result["params"]["lng"], "36.0")

    def test_comparables_preserve_zero_longitude(self):
        result = server.get_comparables(2, 1, 4, latitude=51.48, longitude=0.0)
        self.assertEqual(result["listings"][0]["params"]["longitude"], "0.0")

    def test_missing_coordinate_is_rejected_before_http(self):
        with self.assertRaises(ValueError):
            server.get_estimate(2, 1, 4, lat=45.0)

    def test_location_is_required(self):
        with self.assertRaises(ValueError):
            server.get_estimate(2, 1, 4)

    def test_address_and_coordinates_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            server.get_comparables(2, 1, 4, address="Greenwich", latitude=51.48, longitude=0.0)

    def test_address_request_still_works(self):
        result = server.get_estimate(2, 1, 4, address="Greenwich")
        self.assertEqual(result["params"]["address"], "Greenwich")
        self.assertNotIn("lat", result["params"])

    def test_listing_uses_documented_listing_id_parameter(self):
        result = server.get_listing(43036533)
        self.assertEqual(result["params"].get("listing_id"), "43036533")
        self.assertNotIn("id", result["params"])

    def test_listing_metrics_use_documented_listing_id_parameter(self):
        result = server.get_listing_metrics(43036533, num_months=24)
        self.assertEqual(result["results"][0]["params"].get("listing_id"), "43036533")
        self.assertEqual(result["results"][0]["params"]["num_months"], "24")


if __name__ == "__main__":
    unittest.main()
