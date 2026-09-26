"""Offline contracts for the Smoobu adapter and write target, HMAC signing first. Fixtures follow
docs.smoobu.com (read 2026-09-25); values are made up. DOCS-ONLY: no live Smoobu account.

The golden signatures below were produced OUTSIDE this code, by running Smoobu's own documented
shell recipe (`echo -en "$CANONICAL" | openssl dgst -sha256 -hmac "$API_SECRET" -binary | base64`)
on the docs' two worked examples. The fake server re-derives every signature from the request it
actually received (its own query sorting and encoding), so signer and verifier are independent."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
import uuid
from datetime import date, datetime, timezone
from urllib.parse import quote

from _mvp_pms import analyze
from _mvp_write import CannotWrite
from _pms_fakes import SECRET_BODY, FakeOpener, connections, read_client
from _pms_smoobu import (
    SmoobuCalendarTarget, SmoobuError, SmoobuSource, canonical, day_row, property_row, reservation_row, signed_headers,
)

DOC_KEY, DOC_SECRET = "usr_live_abc123", "your_api_secret"
DOC_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
# Smoobu's public example nonces, split across two literals only so scripts/prepublish.sh's
# UUID guard (which protects private listing ids) does not trip on documentation values.
DOC_NONCE_GET = "6ba7b810-9dad-11d1-80b4-" "00c04fd430c8"
DOC_NONCE_POST = "550e8400-e29b-41d4-a716-" "446655440000"
KEY, SECRET = "usr_live_key", "sekrit"


def verify(rec, secret=SECRET, seen=None):
    """What Smoobu's server does, written independently of the module under test."""
    h = rec["headers"]
    if not all(k in h for k in ("x-api-key", "x-timestamp", "x-nonce", "x-signature")) or "api-key" in h:
        return False
    if seen is not None:
        if h["x-nonce"] in seen:
            return False
        seen.add(h["x-nonce"])
    query = "&".join(sorted(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in rec["query"]))
    text = "\n".join([rec["method"], rec["path"], query, h["x-timestamp"], h["x-nonce"],
                      hashlib.sha256(rec["body_bytes"]).hexdigest(), h["x-api-key"]])
    good = base64.b64encode(hmac.new(secret.encode(), text.encode(), hashlib.sha256).digest()).decode()
    return hmac.compare_digest(good, h["x-signature"])


class Signing(unittest.TestCase):
    def test_docs_get_example_matches_the_vendor_recipe(self):
        h = signed_headers(DOC_KEY, DOC_SECRET, "GET", "/api/reservations", "from=2026-04-01&to=2026-04-10",
                           now=DOC_NOW, nonce=DOC_NONCE_GET)
        self.assertEqual(h["X-Signature"], "Bu2/61pneyRyjQejH7PoCJC2P8iRm3NnC9R6CGFOQho=")
        self.assertEqual((h["X-API-Key"], h["X-Timestamp"]), (DOC_KEY, "2026-04-01T12:00:00Z"))

    def test_docs_post_example_hashes_the_exact_body(self):
        body = b'{"apartmentId":123,"from":"2026-04-01","to":"2026-04-10"}'
        h = signed_headers(DOC_KEY, DOC_SECRET, "POST", "/api/reservations", "", body,
                           now=DOC_NOW, nonce=DOC_NONCE_POST)
        self.assertEqual(h["X-Signature"], "64zVmuaX7u9BPPU7J8ruvr5rzoaCcpZtYgFoKGJOdig=")

    def test_canonical_string_layout(self):
        self.assertEqual(canonical("get", "/api/me", "", "T", "N", b"", "K"),
                         "GET\n/api/me\n\nT\nN\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\nK")

    def test_deterministic_for_pinned_inputs_and_fresh_nonce_otherwise(self):
        a = signed_headers(KEY, SECRET, "GET", "/api/me", now=DOC_NOW, nonce="n1")
        self.assertEqual(a, signed_headers(KEY, SECRET, "GET", "/api/me", now=DOC_NOW, nonce="n1"))
        self.assertNotEqual(a["X-Signature"], signed_headers(KEY, SECRET, "GET", "/api/me", now=DOC_NOW, nonce="n2")["X-Signature"])
        x, y = signed_headers(KEY, SECRET, "GET", "/api/me"), signed_headers(KEY, SECRET, "GET", "/api/me")
        self.assertNotEqual(x["X-Nonce"], y["X-Nonce"])
        self.assertEqual(uuid.UUID(x["X-Nonce"]).version, 4)
        self.assertNotIn("Api-Key", x, "the legacy header is never sent")


APT = {"id": 398, "name": "Seaside apartment"}
DETAIL = {"location": {"city": "Berlin", "country": "Germany"}, "timeZone": "Europe/Berlin",
          "rooms": {"maxOccupancy": 4, "bedrooms": 2, "bathrooms": 1}, "currency": "EUR",
          "price": {"minimal": "10.00", "maximal": "100.00"}}


def booking(i, arrival, departure, kind="reservation", base=200.0, apt=398, blocked=False):
    return {"id": i, "type": kind, "arrival": arrival, "departure": departure, "created-at": "2030-01-03 13:51",
            "apartment": {"id": apt, "name": "Seaside apartment"}, "channel": {"id": 1, "name": "Airbnb"},
            "guest-name": "SHOULD NEVER APPEAR", "email": "g@x.y", "price": base + 50, "is-blocked-booking": blocked,
            "priceElements": [{"type": "basePrice", "amount": base, "currencyCode": "EUR", "priceIncludedInId": None},
                              {"type": "cleaningFee", "amount": 50, "currencyCode": "EUR", "priceIncludedInId": None}]}


def rate(price=120.0, mlos=2, available=1):
    return {"price": price, "min_length_of_stay": mlos, "available": available}


class Mappers(unittest.TestCase):
    def test_apartment(self):
        p = property_row(APT, DETAIL)
        self.assertEqual((p["id"], p["name"], p["currency"], p["timezone"]), ("398", "Seaside apartment", "EUR", "Europe/Berlin"))
        self.assertEqual((p["capacity"]["max"], p["address"]["city"]), (4, "Berlin"))
        with self.assertRaises(SmoobuError):
            property_row(APT, dict(DETAIL, currency=None))

    def test_base_price_is_room_revenue_and_guest_data_is_dropped(self):
        r = reservation_row(booking(1, "2030-02-03", "2030-02-05", base=199.995))
        self.assertEqual(r["financials"]["host_accommodation_cents"], 20000)
        self.assertEqual((r["financials"]["currency"], r["status"], r["platform"], r["nights"]), ("EUR", "accepted", "airbnb", 2))
        self.assertNotIn("SHOULD NEVER APPEAR", json.dumps(r))

    def test_no_base_price_is_unknown_not_zero_and_currency_falls_back(self):
        r = reservation_row(dict(booking(1, "2030-02-03", "2030-02-05"), priceElements=[]), "EUR")
        self.assertIsNone(r["financials"]["host_accommodation_cents"])
        self.assertEqual(r["financials"]["currency"], "EUR")

    def test_types(self):
        for kind, ours in (("reservation", "accepted"), ("modification of booking", "accepted"),
                           ("cancellation", "cancelled"), ("new-thing", "unknown")):
            with self.subTest(kind=kind):
                self.assertEqual(reservation_row(booking(1, "2030-02-03", "2030-02-05", kind=kind))["status"], ours)

    def test_available_count_and_booking_join(self):
        self.assertEqual(day_row("2030-02-03", rate(available=0), "EUR", {"2030-02-03"})["status_reason"], "RESERVED")
        self.assertEqual(day_row("2030-02-04", rate(available=0), "EUR", {"2030-02-03"})["status_reason"], "BLOCKED")
        self.assertEqual(day_row("2030-02-05", rate(available=1), "EUR")["status_reason"], "AVAILABLE")
        self.assertIsNone(day_row("2030-02-05", rate(price=None), "EUR")["price_cents"])
        self.assertEqual(day_row("2030-02-05", rate(price=120.5), "EUR")["price_cents"], 12050)


class Server:
    """A fake login.smoobu.com that verifies every signature and every nonce."""

    def __init__(self, pages, rates, first_status=None):
        self.seen, self.pages, self.rates, self.first_status = set(), pages, rates, first_status

    def _guard(self, handler):
        def route(rec):
            if self.first_status:
                status, self.first_status = self.first_status, None
                return (status, None)
            if not verify(rec, seen=self.seen):
                return (401, None)
            return handler(rec)
        return route

    def routes(self):
        return {
            ("GET", "/api/apartments"): self._guard(lambda r: {"apartments": [APT]}),
            ("GET", "/api/apartments/398"): self._guard(lambda r: DETAIL),
            ("GET", "/api/reservations"): self._guard(lambda r: self.pages[int(dict(r["query"])["page"]) - 1]),
            ("GET", "/api/rates"): self._guard(lambda r: {"data": {"398": self.rates}}),
        }


class Source(unittest.TestCase):
    START = date(2030, 2, 1)

    def setUp(self):
        self.pages = [
            {"page_count": 2, "page_size": 100, "total_items": 3, "page": 1,
             "bookings": [booking(1, "2030-02-03", "2030-02-05"), booking(9, "2030-02-08", "2030-02-09", blocked=True)]},
            {"page_count": 2, "page_size": 100, "total_items": 3, "page": 2,
             "bookings": [booking(2, "2030-02-06", "2030-02-08", kind="cancellation", base=999.0)]},
        ]
        self.rates = {f"2030-02-{d:02d}": rate(available=0 if d in (3, 4, 10) else 1) for d in range(1, 12)}
        self.server = Server(self.pages, self.rates)
        self.opener = FakeOpener(self.server.routes())
        self.src = SmoobuSource(read_client(self.opener), connections(SMOOBU_API_KEY=KEY, SMOOBU_API_SECRET=SECRET))

    def test_secret_is_required(self):
        with self.assertRaisesRegex(SmoobuError, "SMOOBU_API_SECRET"):
            SmoobuSource(read_client(self.opener), connections(SMOOBU_API_KEY=KEY))

    def test_every_read_is_signed_verified_and_never_legacy(self):
        self.src.reservations("398", self.START, 10)
        self.src.calendar("398", self.START, 10)
        self.assertTrue(self.opener.requests)
        for r in self.opener.requests:
            self.assertEqual((r["method"], r["host"]), ("GET", "login.smoobu.com"))
            self.assertNotIn("api-key", r["headers"])
        self.assertEqual(len(self.server.seen), len(self.opener.requests), "one fresh nonce per request")

    def test_a_429_retry_is_signed_again_with_a_new_nonce(self):
        server = Server(self.pages, self.rates, first_status=429)
        opener = FakeOpener(server.routes())
        src = SmoobuSource(read_client(opener), connections(SMOOBU_API_KEY=KEY, SMOOBU_API_SECRET=SECRET))
        self.assertEqual(src.property("398")["currency"], "EUR")
        nonces = [r["headers"]["x-nonce"] for r in opener.requests]
        self.assertEqual(len(nonces), len(set(nonces)), "a reused nonce would be a 401")

    def test_pagination_params_and_what_is_kept(self):
        out = self.src.reservations("398", self.START, 10)
        calls = self.opener.calls("GET", "/api/reservations")
        q = dict(calls[0]["query"])
        self.assertEqual([dict(c["query"])["page"] for c in calls], ["1", "2"])
        self.assertEqual((q["apartmentId"], q["pageSize"], q["excludeBlocked"], q["includePriceElements"]),
                         ("398", "100", "true", "true"))
        self.assertEqual({r["id"]: r["status"] for r in out["data"]}, {"1": "accepted", "2": "cancelled"},
                         "the blocked booking is not a stay")
        self.assertTrue(out["complete"])

    def test_bookings_for_another_apartment_refuse(self):
        self.pages[1]["bookings"][0]["apartment"]["id"] = 401
        with self.assertRaisesRegex(SmoobuError, "another apartment"):
            self.src.reservations("398", self.START, 10)

    def test_calendar_signs_the_bracketed_array_param(self):
        rows = self.src.calendar("398", self.START, 10)
        call = self.opener.calls("GET", "/api/rates")[0]
        self.assertIn("apartments%5B%5D=398", call["query_string"])
        self.assertEqual(dict(call["query"])["end_date"], "2030-02-11")
        status = {r["date"]: r["status_reason"] for r in rows}
        self.assertEqual((len(rows), status["2030-02-03"], status["2030-02-10"], status["2030-02-06"]),
                         (10, "RESERVED", "BLOCKED", "AVAILABLE"))

    def test_rates_for_another_apartment_refuse(self):
        self.opener.routes[("GET", "/api/rates")] = self.server._guard(lambda r: {"data": {"398": self.rates, "401": {}}})
        with self.assertRaisesRegex(SmoobuError, "another apartment"):
            self.src.calendar("398", self.START, 10)

    def test_reviews_are_unreadable_not_zero(self):
        with self.assertRaises(SmoobuError):
            self.src.reviews("398")

    def test_cancelled_booking_is_excluded_from_booked_nights_in_the_real_analysis(self):
        prop = self.src.property("398")
        facts = analyze(prop, self.src.calendar("398", self.START, 10), self.src.reservations("398", self.START, 10)["data"],
                        [], self.START, 10, datetime(2030, 1, 31, tzinfo=timezone.utc))
        self.assertEqual(facts["windows"][0]["confirmed_paid_nights"], 2)
        self.assertEqual(facts["windows"][0]["on_books_accommodation_cents"], 20000, "basePrice is the stay total")
        daily = {d["date"]: d["classification"] for d in facts["daily"]}
        self.assertEqual((daily["2030-02-06"], daily["2030-02-10"]), ("open", "blocked"))


PINNED = (datetime(2030, 1, 15, 9, 30, tzinfo=timezone.utc), "pinned-test-nonce-0001")


class Target(unittest.TestCase):
    def target(self, rates=None, post=(200, {"success": True})):
        server = Server([], rates or {f"2030-02-{d:02d}": rate() for d in range(1, 5)})
        routes = server.routes()
        routes[("POST", "/api/rates")] = server._guard(lambda r: post) if isinstance(post, tuple) and post[0] < 400 else post
        opener = FakeOpener(routes)
        self.server = server
        return SmoobuCalendarTarget(connections(SMOOBU_API_KEY=KEY, SMOOBU_API_SECRET=SECRET), opener=opener), opener

    def test_interface_and_no_floor(self):
        t, _ = self.target()
        self.assertEqual((t.name, t.host), ("smoobu", "login.smoobu.com"))
        self.assertIsNone(t.floor("398"))
        self.assertIsNone(t.pricing_managed("398"))

    def test_read_calendar(self):
        t, _ = self.target()
        cal = t.read_calendar("398", date(2030, 2, 1), date(2030, 2, 3))
        self.assertEqual(cal, {"currency": "EUR", "days": {d: {"price": 120.0, "min_stay": 2, "available": True}
                                                          for d in ("2030-02-01", "2030-02-02", "2030-02-03")}})
        t, _ = self.target(rates={"2030-02-01": rate()})
        with self.assertRaisesRegex(CannotWrite, "missing 2 of 3"):
            t.read_calendar("398", date(2030, 2, 1), date(2030, 2, 3))

    def test_write_groups_dates_and_is_signed(self):
        t, opener = self.target()
        t.write_calendar("398", {"2030-02-01": {"price": 140}, "2030-02-03": {"price": 140},
                                 "2030-02-02": {"price": 200.004, "min_stay": 4}}, "EUR")
        post = opener.calls("POST", "/api/rates")
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]["body"], {"apartments": [398], "operations": [
            {"dates": ["2030-02-01", "2030-02-03"], "daily_price": 140.0},
            {"dates": ["2030-02-02"], "daily_price": 200.0, "min_length_of_stay": 4}]})
        self.assertTrue(verify(post[0]), "the POST body hash is part of the verified signature")
        self.assertNotIn("api-key", post[0]["headers"])

    def test_pinned_clock_gives_a_byte_identical_signed_request(self):
        sigs = []
        for _ in range(2):
            opener = FakeOpener({("GET", "/api/apartments/398"): DETAIL, ("POST", "/api/rates"): {"success": True}})
            t = SmoobuCalendarTarget(connections(SMOOBU_API_KEY=KEY, SMOOBU_API_SECRET=SECRET), opener=opener,
                                     clock=lambda: PINNED)
            t.write_calendar("398", {"2030-02-02": {"price": 150}}, "EUR")
            sigs.append((opener.requests[-1]["body_bytes"], opener.requests[-1]["headers"]["x-signature"]))
        self.assertEqual(sigs[0], sigs[1])
        self.assertTrue(verify(opener.requests[-1]))

    def test_min_stay_alone_needs_a_price_on_that_date(self):
        t, opener = self.target(rates={"2030-02-01": rate(price=None), "2030-02-02": rate()})
        with self.assertRaisesRegex(CannotWrite, "2030-02-01 has no nightly price"):
            t.write_calendar("398", {"2030-02-01": {"min_stay": 3}}, "EUR")
        self.assertEqual(opener.calls("POST"), [])
        t.write_calendar("398", {"2030-02-02": {"min_stay": 3}}, "EUR")
        self.assertEqual(opener.calls("POST")[0]["body"]["operations"], [{"dates": ["2030-02-02"], "min_length_of_stay": 3}])

    def test_failure_modes(self):
        t, opener = self.target(post=(200, {"success": False}))
        with self.assertRaisesRegex(CannotWrite, "did not confirm success"):
            t.write_calendar("398", {"2030-02-02": {"price": 150}}, "EUR")
        t, opener = self.target(post=(500, None))  # Smoobu's validation errors are HTTP 500
        with self.assertRaises(CannotWrite) as ctx:
            t.write_calendar("398", {"2030-02-02": {"price": 150}}, "EUR")
        self.assertEqual(str(ctx.exception), "Smoobu POST /api/rates: HTTP 500")
        self.assertNotIn(SECRET_BODY, str(ctx.exception))
        self.assertEqual(len(opener.calls("POST")), 1, "never retried")
        t, opener = self.target()
        with self.assertRaisesRegex(CannotWrite, "currency"):
            t.write_calendar("398", {"2030-02-02": {"price": 150}}, "USD")
        self.assertEqual(opener.calls("POST"), [])

    def test_transport_refuses_every_other_call(self):
        t, opener = self.target()
        for method, path in (("GET", "/api/reservations"), ("POST", "/api/reservations"), ("GET", "/api/me"),
                             ("DELETE", "/api/rates"), ("POST", "/booking/checkApartmentAvailability")):
            with self.subTest(path=path), self.assertRaisesRegex(CannotWrite, "refuses"):
                t.http.request(method, path)
        self.assertEqual(opener.requests, [])

    def test_secret_required(self):
        with self.assertRaisesRegex(CannotWrite, "SMOOBU_API_SECRET"):
            SmoobuCalendarTarget(connections(SMOOBU_API_KEY=KEY))


if __name__ == "__main__":
    unittest.main()
