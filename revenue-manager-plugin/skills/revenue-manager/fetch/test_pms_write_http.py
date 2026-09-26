"""Offline contracts for the shared PMS write transport and change validation."""

from __future__ import annotations

import re
import unittest
from datetime import date

from _mvp_write import CannotWrite
from _pms_fakes import SECRET_BODY, FakeOpener
from _pms_write_http import TargetHTTP, _NoRedirect, encode_query, major, same_currency, validate_changes

ALLOWED = (("GET", re.compile(r"/things/[0-9]+")), ("POST", re.compile(r"/things/[0-9]+")))


def http(routes, **kw):
    opener = FakeOpener(routes)
    return TargetHTTP("Vendor", "api.vendor.test", ALLOWED, lambda m, p, q, b: {"X-Key": "k"}, opener=opener, **kw), opener


class Query(unittest.TestCase):
    def test_sorted_and_rfc3986(self):
        self.assertEqual(encode_query({"to": "2026-04-10", "from": "2026-04-01"}), "from=2026-04-01&to=2026-04-10")
        self.assertEqual(encode_query({"q": "a b+c", "apartments[]": ["398", "401"]}),
                         "apartments%5B%5D=398&apartments%5B%5D=401&q=a%20b%2Bc")
        self.assertEqual(encode_query({"flag": True, "none": None}), "flag=true")


class Transport(unittest.TestCase):
    def test_refuses_anything_off_the_list_before_sending(self):
        t, opener = http({})
        for method, path in (("DELETE", "/things/1"), ("GET", "/other/1"), ("GET", "/things/1/../../x"),
                             ("PUT", "/things/1"), ("GET", "things/1"), ("GET", "//evil.test/things/1")):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                t.request(method, path)
        self.assertEqual(opener.requests, [])

    def test_error_names_code_never_the_body_and_never_retries(self):
        t, opener = http({("POST", "/things/1"): (500, None)})
        with self.assertRaises(CannotWrite) as ctx:
            t.request("POST", "/things/1", body={"a": 1})
        self.assertEqual(str(ctx.exception), "Vendor POST /things/1: HTTP 500")
        self.assertNotIn(SECRET_BODY, str(ctx.exception))
        self.assertEqual(len(opener.calls("POST")), 1, "a failed write is never resent")

    def test_call_budget(self):
        t, _ = http({("GET", "/things/1"): {}}, max_calls=2)
        t.request("GET", "/things/1"); t.request("GET", "/things/1")
        with self.assertRaisesRegex(CannotWrite, "budget"):
            t.request("GET", "/things/1")

    def test_one_host_https_timeout_json_body(self):
        t, opener = http({("POST", "/things/7"): (202, {"ok": True})})
        self.assertEqual(t.request("POST", "/things/7", params={"b": 2, "a": 1}, body={"x": 1}), (202, {"ok": True}))
        r = opener.requests[0]
        self.assertEqual((r["host"], r["query_string"], r["timeout"], r["body"]), ("api.vendor.test", "a=1&b=2", 60, {"x": 1}))
        self.assertEqual(r["headers"]["content-type"], "application/json")

    def test_redirects_are_not_followed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.test/"))


class Changes(unittest.TestCase):
    TODAY = date(2030, 1, 1)

    def test_normalizes_and_rounds_half_up_to_the_cent(self):
        ops = validate_changes("V", {"2030-01-03": {"min_stay": 2}, "2030-01-02": {"price": 151.255}}, today=self.TODAY)
        self.assertEqual(ops, [("2030-01-02", 151.26, None), ("2030-01-03", None, 2)])
        self.assertEqual(major(100), 100.0)

    def test_refusals(self):
        bad = ({}, None, {"2029-12-31": {"price": 100}}, {"2030-13-01": {"price": 1}}, {"01/02/2030": {"price": 1}},
               {"2030-01-02": {"price": 0}}, {"2030-01-02": {"price": float("nan")}}, {"2030-01-02": {"price": True}},
               {"2030-01-02": {"min_stay": 0}}, {"2030-01-02": {"min_stay": 1.5}}, {"2030-01-02": {"available": False}},
               {"2030-01-02": {}})
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(CannotWrite):
                validate_changes("V", changes, today=self.TODAY)

    def test_currency_must_match_the_live_listing(self):
        self.assertEqual(same_currency("V", "usd", "USD"), "USD")
        for live, planned in (("USD", "EUR"), (None, "USD"), ("USD", None)):
            with self.subTest(live=live, planned=planned), self.assertRaises(CannotWrite):
                same_currency("V", live, planned)


if __name__ == "__main__":
    unittest.main()
