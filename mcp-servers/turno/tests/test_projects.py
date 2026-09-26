"""Date filtering must not trust silently ignored upstream query parameters."""
import httpx
import pytest

from turno_mcp.client import TurnoClient
from turno_mcp.config import TurnoConfig
from turno_mcp.server import mcp
from turno_mcp.tools import projects


@pytest.fixture
async def list_projects(monkeypatch):
    async def handler(request):
        page = int(request.url.params.get("page", 1))
        rows = {
            1: [{"id": 1, "start": "2025-03-19 11:00:00"}],
            2: [{"id": 2, "start": "2026-08-20T11:00:00-07:00"}, {"id": 3, "start": "2026-10-20 11:00:00"}],
            3: [{"id": 4, "start": "2027-01-22 11:00:00"}],
        }
        return httpx.Response(200, json={"data": {"items": rows[page], "current_page": page, "last_page": 3, "total": 4}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        cfg = TurnoConfig("test-only", "test-partner", "sandbox", "https://sandbox.turnoverbnb.com", "/v2")
        client = TurnoClient(cfg, client=http)
        monkeypatch.setattr(projects, "get_client", lambda: client)
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        yield tools["turno_list_projects"].fn


@pytest.mark.parametrize("filters", [
    {"date_range_start": "2026-08-20", "date_range_end": "2026-10-20"},
    {"start": "2026-08-20", "end": "2026-10-20"},
])
async def test_ignored_api_filters_cannot_include_out_of_window_projects(list_projects, filters):
    rows = await list_projects(fetch_all=True, **filters)
    assert [row["id"] for row in rows] == [2, 3]


async def test_unfiltered_fetch_all_preserves_all_projects(list_projects):
    rows = await list_projects(fetch_all=True)
    assert [row["id"] for row in rows] == [1, 2, 3, 4]


async def test_date_filter_requires_complete_pagination(list_projects):
    with pytest.raises(ValueError, match="fetch_all"):
        await list_projects(date_range_start="2026-08-20")


async def test_date_filter_cannot_skip_earlier_pages(list_projects):
    with pytest.raises(ValueError, match="page"):
        await list_projects(fetch_all=True, page=2, date_range_start="2026-08-20")


async def test_reversed_date_window_is_rejected(list_projects):
    with pytest.raises(ValueError):
        await list_projects(fetch_all=True, date_range_start="2026-10-20", date_range_end="2026-08-20")


async def test_conflicting_date_aliases_are_rejected(list_projects):
    with pytest.raises(ValueError):
        await list_projects(fetch_all=True, start="2026-01-01", date_range_start="2026-08-20")


async def test_undated_project_cannot_silently_enter_a_dated_cost_analysis(list_projects, monkeypatch):
    async def undated(*_args, **_kwargs):
        return [{"id": 1, "start": None}]

    monkeypatch.setattr(projects.get_client(), "paginate", undated)
    with pytest.raises(ValueError, match="start"):
        await list_projects(fetch_all=True, date_range_start="2026-08-20")
