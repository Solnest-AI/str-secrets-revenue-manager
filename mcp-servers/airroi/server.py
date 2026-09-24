#!/usr/bin/env python3
"""
AirROI MCP server — short-term-rental market data inside Claude.

Revenue estimates, NAMED comparable listings (with TTM revenue/ADR/occupancy/ratings),
single-listing detail, and monthly metrics — straight from the AirROI API.

Auth: AIRROI_API_KEY (header X-API-KEY). AirROI returns each market's native local currency by default (currency=native).
Setup: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt,
then put AIRROI_API_KEY in .env (free key: https://www.airroi.com/api/developer/activate).
"""
from mcp.server.fastmcp import FastMCP
import airroi_client as ar

mcp = FastMCP("airroi")


@mcp.tool()
def health_check() -> dict:
    """Confirm the AirROI API key works and the service is reachable (returns a sample
    estimate). If ok is false, set AIRROI_API_KEY in .env. AirROI returns each market's native local currency by default (currency=native)."""
    return ar.health_check()


@mcp.tool()
def get_estimate(bedrooms: int, baths: float, guests: int,
                 address: str = "", lat: float = 0.0, lng: float = 0.0,
                 currency: str = "native") -> dict:
    """Revenue estimate for a property: annual revenue, ADR, occupancy, percentiles,
    monthly distribution, and comparable_listings. Provide EITHER address OR lat+lng.
    AirROI returns each market's native local currency by default (currency=native)."""
    return ar.get_estimate(bedrooms=bedrooms, baths=baths, guests=guests,
                           address=address or None, lat=lat or None, lng=lng or None,
                           currency=currency)


@mcp.tool()
def get_comparables(bedrooms: int, baths: float, guests: int,
                    address: str = "", latitude: float = 0.0, longitude: float = 0.0,
                    radius: int = 0, currency: str = "native") -> dict:
    """Up to 25 NAMED competitor listings near a location — each with TTM revenue, ADR,
    occupancy, ratings, amenities and description. Provide EITHER address OR
    latitude+longitude. radius (miles) optionally widens thin markets. Returns native local currency by default (currency=native)."""
    return ar.get_comparables(bedrooms=bedrooms, baths=baths, guests=guests,
                              address=address or None, latitude=latitude or None,
                              longitude=longitude or None, radius=radius or None,
                              currency=currency)


@mcp.tool()
def get_listing(listing_id: int, currency: str = "native") -> dict:
    """Full detail for one Airbnb listing by AirROI listing_id: description, photos, host,
    location, specs, ratings, and performance metrics. Returns native local currency by default (currency=native)."""
    return ar.get_listing(listing_id, currency)


@mcp.tool()
def get_listing_metrics(listing_id: int, num_months: int = 12, currency: str = "native") -> dict:
    """Monthly time-series for one listing: occupancy / ADR / revenue / RevPAR at the
    p25-p90 percentiles over num_months. Returns native local currency by default (currency=native)."""
    return ar.get_listing_metrics(listing_id, num_months, currency)


if __name__ == "__main__":
    mcp.run()
