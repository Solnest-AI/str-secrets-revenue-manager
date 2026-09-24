"""
AirROI API client — short-term-rental market data.

Single, clean dependency on the AirROI public API. No Firecrawl, no Apify, no
Google Maps, no scraping — just AirROI.

Auth:  AIRROI_API_KEY  (sent as header `X-API-KEY`)
Base:  https://api.airroi.com   (v2.1.1)
Note:  currency defaults to "native" — figures come back in each market's local
       currency (e.g. CAD for Canadian markets, GBP for the UK), which normally
       matches the operator's PMS/PriceLabs currency. "usd" forces USD. Raw ISO
       codes like "cad"/"eur" are NOT accepted by the API (400) — use "native".
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

try:  # .env is convenience for local dev; env vars work without it
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

BASE_URL = os.environ.get("AIRROI_BASE_URL", "https://api.airroi.com")
TIMEOUT = int(os.environ.get("AIRROI_TIMEOUT", "30"))


def _key() -> str:
    return os.environ.get("AIRROI_API_KEY", "")


class AirROIError(RuntimeError):
    def __init__(self, status: int, message: str, body: Optional[dict] = None):
        super().__init__(f"AirROI API {status}: {message}")
        self.status = status
        self.message = message
        self.body = body or {}


def _get(endpoint: str, params: dict) -> dict:
    if not _key():
        raise AirROIError(0, "AIRROI_API_KEY is not set. Add it to .env "
                             "(free key: https://www.airroi.com/api/developer/activate).")
    params = {k: v for k, v in params.items() if v is not None}
    url = BASE_URL.rstrip("/") + endpoint
    with httpx.Client(timeout=TIMEOUT) as c:
        resp = c.get(url, params=params, headers={"X-API-KEY": _key()})
    try:
        data = resp.json()
    except Exception as e:
        raise AirROIError(resp.status_code, f"non-JSON response: {e}")
    if resp.status_code >= 400:
        raise AirROIError(resp.status_code, str(data.get("message") or f"HTTP {resp.status_code}"), data)
    return data


def health_check() -> dict:
    """Tiny live call to confirm the key + connectivity."""
    if not _key():
        return {"ok": False, "error": "AIRROI_API_KEY is not set.",
                "hint": "Free key: https://www.airroi.com/api/developer/activate"}
    try:
        d = _get("/calculator/estimate",
                 {"address": "Nashville, TN", "bedrooms": 2, "baths": 1, "guests": 4, "currency": "native"})
        return {"ok": True, "base_url": BASE_URL, "currency": d.get("currency", "native"),
                "sample_estimate": d.get("revenue")}
    except AirROIError as e:
        return {"ok": False, "error": str(e),
                "hint": "Check AIRROI_API_KEY. Free key: https://www.airroi.com/api/developer/activate"}


def get_estimate(*, bedrooms, baths, guests, address=None, lat=None, lng=None, currency="native") -> dict:
    params = {"bedrooms": bedrooms, "baths": baths, "guests": guests, "currency": currency}
    if address and not (lat and lng):
        params["address"] = address
    else:
        params["lat"] = lat
        params["lng"] = lng
    return _get("/calculator/estimate", params)


def get_comparables(*, bedrooms, baths, guests, address=None, latitude=None, longitude=None,
                    currency="native", radius=None) -> dict:
    params = {"bedrooms": bedrooms, "baths": baths, "guests": guests, "currency": currency, "radius": radius}
    if address and not (latitude and longitude):
        params["address"] = address
    else:
        params["latitude"] = latitude
        params["longitude"] = longitude
    data = _get("/listings/comparables", params)
    listings = data.get("listings") or []
    return {"count": len(listings), "listings": listings}


def get_listing(listing_id, currency="native") -> dict:
    return _get("/listings", {"id": listing_id, "currency": currency})


def get_listing_metrics(listing_id, num_months=12, currency="native") -> dict:
    data = _get("/listings/metrics/all", {"id": listing_id, "num_months": num_months, "currency": currency})
    results = data.get("results") or []
    return {"count": len(results), "results": results}
