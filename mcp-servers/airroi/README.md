# AirROI MCP

Short-term-rental market data from [AirROI](https://www.airroi.com) inside Claude —
revenue estimates, **named** comparable listings (with TTM revenue / ADR / occupancy /
ratings), single-listing detail, and monthly metrics.

Clean and **credential-free**: the only thing it needs is *your own* free AirROI API key.
No Firecrawl, no scraping, no other services — just the AirROI API. Ships with **zero secrets**.

> AirROI returns figures in each market's **native local currency** by default (`currency=native`) — e.g. CAD for Canadian markets, GBP for the UK. It covers Canada and international markets, not just the US. (Pass `currency=usd` to force USD; raw ISO codes like `cad`/`eur` are not accepted by the API.)

## One-time setup
```bash
cd <this-folder>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then paste your key into the AIRROI_API_KEY= line
```
Get a free key: https://www.airroi.com/api/developer/activate

Verify it works:
```bash
.venv/bin/python smoke_test.py
```

## Register with Claude Code
```bash
claude mcp add airroi --scope user -- "<this-folder>/.venv/bin/python" "<this-folder>/server.py"
```
Fully restart Claude Code (quit + reopen), then try: *"AirROI health check"* or
*"Pull AirROI comps for a 3BR in Sun Peaks, BC."*

## Tools
| Tool | Returns |
|------|---------|
| `health_check` | confirms the key + connectivity (sample estimate) |
| `get_estimate` | annual revenue, ADR, occupancy, percentiles, monthly distribution + comps (address OR lat+lng) |
| `get_comparables` | up to 25 **named** competitor listings w/ TTM revenue, ADR, occ, ratings (address OR latitude+longitude, optional radius) |
| `get_listing` | full listing detail: description, photos, host, ratings, performance metrics |
| `get_listing_metrics` | monthly occupancy / ADR / revenue / RevPAR (p25–p90) |

## Security
Your AirROI key lives only in this local `.env` (gitignored). It is never committed and
never sent anywhere except AirROI. This folder is safe to share as-is — it contains no secrets.
