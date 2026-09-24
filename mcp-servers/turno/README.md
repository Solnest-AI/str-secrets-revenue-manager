# turno-mcp

An [MCP](https://modelcontextprotocol.io) server that wraps the **Turno External API v2** (formerly TurnoverBnB) so you can manage short-term-rental cleaning/turnover operations directly from Claude (Desktop / Code).

One tool per Turno endpoint (~47) across Projects, Properties, Cleaners, Bookings, Assignments, Blocked Dates, Problems, Checklists, Reviews, and Webhooks. Built with [FastMCP](https://github.com/jlowin/fastmcp) + `httpx`, managed by `uv`.

> 📖 **Turno field cheat-sheet** — response-shape gotchas worth knowing: property name = `alias`, clean date = project `start`, and the `"booking "` key has a trailing space. (The bundled server already handles these for you.)

## Requirements

- Python ≥ 3.11 and [`uv`](https://docs.astral.sh/uv/)
- A Turno **partner** Bearer token + `TBNB-Partner-ID` (the External API is partner-gated — contact Turno for access)

## Setup

```bash
uv sync                  # create venv + install deps
cp .env.example .env     # then fill in your credentials
```

### Configuration (environment variables)

| Var | Required | Default | Notes |
|---|---|---|---|
| `TURNO_API_TOKEN` | ✅ | — | Partner Bearer JWT |
| `TURNO_PARTNER_ID` | ✅ | — | UUID for the `TBNB-Partner-ID` header |
| `TURNO_ENV` | — | `sandbox` | `sandbox` or `production` |
| `TURNO_BASE_URL` | — | env-based | Override host (e.g. if prod host differs) |
| `TURNO_API_PREFIX` | — | `/v2` | Override path prefix (`/v2` vs `/api/v2`) |

> The server **defaults to sandbox**. Set `TURNO_ENV=production` only when you intend to touch live data.

> **Credential mapping (Turno's labels are confusing):** `TURNO_API_TOKEN` must hold the long **JWT access token** — Turno's UI may present this as your *"secret key"* (it starts with `eyJ`). The short ~80-char hex value Turno calls the *"API token"* is the OAuth client key and is **not** used for request auth. `TURNO_PARTNER_ID` is the partner UUID, sent as the `TBNB-Partner-ID` header.

## Run

```bash
uv run turno-mcp        # serves over stdio
```

## Register with Claude

**Claude Desktop** (`claude_desktop_config.json`) or **Claude Code** (`.mcp.json`):

```json
{
  "mcpServers": {
    "turno": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/mcp-servers/turno", "run", "turno-mcp"],
      "env": {
        "TURNO_API_TOKEN": "your_bearer_jwt",
        "TURNO_PARTNER_ID": "your_partner_uuid",
        "TURNO_ENV": "sandbox"
      }
    }
  }
}
```

## Safety

- **Destructive tools** (delete project/property-checklist/booking/blocked-date/webhook, remove/disconnect contractor, force-assign / cancel assignment) require an explicit `confirm=true` argument and are labelled `DESTRUCTIVE`.
- Server is **sandbox-by-default**.
- These sit on top of Claude's own per-tool permission prompts.

## Tools (~47, one per endpoint)

| Resource | Tools |
|---|---|
| Projects | list, get, create, update, delete⚠️, get_checklist, available_types, notify_early_checkout |
| Properties | list, get, create, update, checklist (get/update/delete⚠️), contractors (get/add/update/remove⚠️), disconnect⚠️ |
| Cleaners | list, get_properties, add_to_property, update_property, remove_from_property⚠️ |
| Bookings | list, get, create, update, delete⚠️ |
| Assignments | assign_cleaner (force)⚠️, cancel⚠️ |
| Blocked Dates | list, get, create, update, delete⚠️ |
| Problems | list, create, update |
| Checklists | list |
| Reviews | list |
| Webhooks | list_types, list, get, create, delete⚠️ |

⚠️ = destructive (requires `confirm=true`).

## Development

```bash
uv run pytest                 # unit tests (httpx mocked via respx)
RUN_INTEGRATION=1 uv run pytest tests/test_integration.py   # opt-in live sandbox calls
```

## License

MIT
