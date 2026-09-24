#!/usr/bin/env python3
"""
RankBreeze MCP server.

Exposes the RankBreeze (Airbnb rank-tracker) dashboard as MCP tools, authenticated
by replaying the user's browser session cookie. No public API exists; see
rankbreeze_client.py for how the data is pulled.
"""
from mcp.server.fastmcp import FastMCP
import rankbreeze_client as rb

mcp = FastMCP("rankbreeze")


@mcp.tool()
def health_check() -> dict:
    """Verify the RankBreeze session cookie works; returns the logged-in account.
    Call this first if other tools error — a dead cookie needs refreshing in session.txt."""
    return rb.health_check()


@mcp.tool()
def list_properties() -> dict:
    """List every Airbnb listing tracked in RankBreeze (id, name, location) plus account/plan info.
    Use the returned ids with the other tools."""
    return rb.list_properties()


@mcp.tool()
def get_rankings(listing_id: str) -> dict:
    """Search-ranking position (city rank: #X of Y, page Z) and booking-funnel
    performance benchmarked against similar listings, for one listing."""
    return rb.get_rankings(listing_id)


@mcp.tool()
def get_metrics(listing_id: str) -> dict:
    """Monthly performance trends for one listing — 1st-page impressions, click-through
    rate, listing views, occupancy, average daily rate and revenue."""
    return rb.get_metrics(listing_id)


@mcp.tool()
def get_calendar_rankings(listing_id: str) -> dict:
    """Per-date / per-guest-count search rankings for one listing, plus the 1st-page
    competitors' average nightly price and rating."""
    return rb.get_calendar_rankings(listing_id)


@mcp.tool()
def get_competitor_rates(listing_id: str) -> dict:
    """Summary of the forward competitor nightly-rate calendar (date span, min/median/max)
    for one listing."""
    return rb.get_competitor_rates(listing_id)


@mcp.tool()
def analyze_property(listing_id: str) -> dict:
    """Full analysis for one listing in a single call: rankings + funnel + monthly
    trends + calendar rankings + competitor rates."""
    return rb.analyze_property(listing_id)


if __name__ == "__main__":
    mcp.run()
