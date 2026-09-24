# RankBreeze MCP

Read-only access to your [RankBreeze](https://app.rankbreeze.com) Airbnb rank-tracker
from Claude — search rankings, booking-funnel metrics, monthly performance trends,
calendar rankings and competitor rates, for every listing you track.

RankBreeze has **no public API**. It's a Rails/Devise app, so this server authenticates
by **replaying your browser session cookie** (`_godzilla_session`) and parsing the same
server-rendered HTML / Turbo fragments the dashboard uses — the same pattern as the
Skool and Loom MCPs.

## Tools

| Tool | What it returns |
|------|-----------------|
| `health_check` | Confirms the cookie works; logged-in account |
| `list_properties` | All tracked listings (id, name, location) + plan info |
| `get_rankings(listing_id)` | City rank (#X of Y, page Z) + funnel vs similar listings |
| `get_metrics(listing_id)` | Monthly trends: impressions, CTR, views, occupancy, ADR, revenue |
| `get_calendar_rankings(listing_id)` | Per-date / per-guest rankings + 1st-page competitor price & rating |
| `get_competitor_rates(listing_id)` | Forward competitor nightly-rate calendar summary |
| `analyze_property(listing_id)` | All of the above for one listing, in one call |

## Auth — the session cookie

The cookie is read from (in order): the `RANKBREEZE_SESSION` env var, then `session.txt`
in this folder.

To (re)fresh it:
1. Log into **app.rankbreeze.com** in Chrome.
2. DevTools (⌘⌥I) → **Application** → **Cookies** → `https://app.rankbreeze.com`.
3. Copy the **`_godzilla_session`** value.
4. Paste it into `session.txt` (single line).

The cookie expires periodically; when it does, tools return a clear "session expired"
message — just redo the steps above. Treat `session.txt` like a password (chmod 600,
git-ignored).

## Registration

Registered globally (user scope) so it loads in every project:

```bash
claude mcp add rankbreeze --scope user -- \
  "<this-folder>/.venv/bin/python" \
  "<this-folder>/server.py"
```

Restart Claude Code after registering. Remove with `claude mcp remove rankbreeze`.
