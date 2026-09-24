"""Quick AirROI MCP smoke test: health_check + a Sun Peaks comparables + estimate pull."""
import airroi_client as ar

print("health_check:", ar.health_check())

comps = ar.get_comparables(address="1240 Alpine Road, Sun Peaks, BC, Canada",
                           bedrooms=3, baths=2, guests=7, currency="native")
print(f"comparables: {comps['count']} returned")
for c in comps["listings"][:3]:
    li = c.get("listing_info") or {}
    pm = c.get("performance_metrics") or {}
    pd = c.get("property_details") or {}
    rev = pm.get("ttm_revenue") or pm.get("revenue") or 0
    print(f"  - {(li.get('listing_name') or '')[:44]:44} {pd.get('bedrooms')}BR  TTM=${rev:,.0f}")

est = ar.get_estimate(address="1240 Alpine Road, Sun Peaks, BC, Canada",
                      bedrooms=3, baths=2, guests=7)
print(f"estimate: rev=${est.get('revenue',0):,.0f}  ADR=${est.get('average_daily_rate',0):,.0f}  occ={est.get('occupancy',0):.0%}")
