"""Opt-in live tests against Turno's sandbox.

Run with real credentials present:
    RUN_INTEGRATION=1 uv run pytest tests/test_integration.py
Skipped by default so the normal suite stays hermetic.
"""

import os

import pytest

from turno_mcp.client import TurnoClient
from turno_mcp.config import load_config

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 (and TURNO_* credentials) to run live sandbox tests",
)


@pytest.fixture
async def client():
    c = TurnoClient(load_config())
    yield c
    await c.aclose()


async def test_list_properties_live(client):
    data = await client.get("/properties", params={"limit": 1})
    assert isinstance(data, dict)
    assert "items" in data


async def test_list_webhook_types_live(client):
    data = await client.get("/webhooks/available-types")
    assert isinstance(data, dict)
    assert "items" in data
