import httpx
import pytest
import respx

from turno_mcp.client import TurnoClient
from turno_mcp.config import TurnoConfig
from turno_mcp.errors import TurnoAPIError

ROOT = "https://sandbox.turnoverbnb.com/v2"


def make_config(**kw):
    base = dict(
        api_token="tok",
        partner_id="pid",
        env="sandbox",
        base_url="https://sandbox.turnoverbnb.com",
        api_prefix="/v2",
    )
    base.update(kw)
    return TurnoConfig(**base)


@pytest.fixture
def client():
    return TurnoClient(make_config())


@respx.mock
async def test_get_sends_auth_headers_and_unwraps_data(client):
    route = respx.get(f"{ROOT}/projects/1").mock(
        return_value=httpx.Response(200, json={"data": {"id": 1, "alias": "Beach"}})
    )
    data = await client.get("/projects/1")
    assert data == {"id": 1, "alias": "Beach"}
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["TBNB-Partner-ID"] == "pid"
    assert req.headers["Accept"] == "application/json"


@respx.mock
async def test_params_drop_none_values(client):
    route = respx.get(f"{ROOT}/projects").mock(
        return_value=httpx.Response(200, json={"data": {"items": []}})
    )
    await client.get("/projects", params={"page": 1, "status": None})
    query = route.calls.last.request.url.query.decode()
    assert "page=1" in query
    assert "status" not in query


@respx.mock
async def test_paginate_walks_all_pages(client):
    respx.get(f"{ROOT}/projects").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"data": {"items": [{"id": 1}], "current_page": 1, "last_page": 2}},
            ),
            httpx.Response(
                200,
                json={"data": {"items": [{"id": 2}], "current_page": 2, "last_page": 2}},
            ),
        ]
    )
    items = await client.paginate("/projects")
    assert [i["id"] for i in items] == [1, 2]


@respx.mock
async def test_retries_on_429_then_succeeds(client, monkeypatch):
    import turno_mcp.client as c

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(c.asyncio, "sleep", _no_sleep)
    respx.get(f"{ROOT}/reviews").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"data": {"items": []}}),
        ]
    )
    data = await client.get("/reviews")
    assert data == {"items": []}


@respx.mock
async def test_error_maps_to_turnoapierror_with_hint(client):
    respx.get(f"{ROOT}/projects").mock(
        return_value=httpx.Response(401, json={"message": "Unauthenticated"})
    )
    with pytest.raises(TurnoAPIError) as ei:
        await client.get("/projects")
    assert ei.value.status_code == 401
    assert "TURNO_API_TOKEN" in str(ei.value)


@respx.mock
async def test_delete_204_returns_none(client):
    respx.delete(f"{ROOT}/projects/1").mock(return_value=httpx.Response(204))
    assert await client.delete("/projects/1") is None


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("failure", ["timeout", "server_error"])
async def test_write_is_not_replayed_after_ambiguous_failure(method, failure, monkeypatch):
    """A server may commit the write before its response is lost or fails."""
    import turno_mcp.client as module

    committed = []

    async def no_sleep(*_args):
        return None

    async def handler(request):
        committed.append(request.content)
        if failure == "timeout":
            raise httpx.ReadTimeout("response lost after commit", request=request)
        return httpx.Response(503, json={"message": "response unavailable"})

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = TurnoClient(make_config(), client=transport)
        with pytest.raises(TurnoAPIError):
            await client.request(method, "/projects", json={"property_id": 1})
    assert len(committed) == 1


async def test_read_still_retries_a_lost_response(monkeypatch):
    import turno_mcp.client as module

    attempts = 0

    async def no_sleep(*_args):
        return None

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, json={"data": {"id": 1}})

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = TurnoClient(make_config(), client=transport)
        assert await client.get("/projects/1") == {"id": 1}


@respx.mock
async def test_requests_send_a_browser_user_agent(client):
    """Turno's Cloudflare challenge 403s library User-Agents before auth runs."""
    route = respx.get(f"{ROOT}/projects/1").mock(
        return_value=httpx.Response(200, json={"data": {"id": 1}})
    )
    await client.get("/projects/1")
    ua = route.calls.last.request.headers["User-Agent"]
    assert ua.startswith("Mozilla/5.0") and "python-httpx" not in ua
