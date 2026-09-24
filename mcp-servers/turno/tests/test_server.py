import pytest

from turno_mcp.safety import ConfirmationRequired
from turno_mcp.server import mcp

EXPECTED_TOOL_COUNT = 48  # 47 API endpoints + turno_check_connection

DESTRUCTIVE = {
    "turno_delete_project",
    "turno_delete_booking",
    "turno_delete_blocked_date",
    "turno_delete_webhook",
    "turno_delete_property_checklist",
    "turno_remove_property_contractor",
    "turno_remove_cleaner_from_property",
    "turno_disconnect_property",
    "turno_assign_cleaner",
    "turno_cancel_assignment",
}


async def _tools():
    return {t.name: t for t in await mcp.list_tools()}


async def test_expected_tool_count():
    tools = await _tools()
    assert len(tools) == EXPECTED_TOOL_COUNT


async def test_core_tools_present():
    tools = await _tools()
    for name in (
        "turno_list_projects",
        "turno_get_project",
        "turno_list_properties",
        "turno_list_cleaners",
        "turno_create_webhook",
        "turno_check_connection",
    ):
        assert name in tools


async def test_all_tools_namespaced():
    tools = await _tools()
    assert all(name.startswith("turno_") for name in tools)


async def test_destructive_tools_expose_confirm_param():
    tools = await _tools()
    for name in DESTRUCTIVE:
        assert name in tools, f"missing destructive tool {name}"
        props = tools[name].parameters.get("properties", {})
        assert "confirm" in props, f"{name} is missing a confirm parameter"


async def test_destructive_tool_blocks_without_confirm():
    tools = await _tools()
    with pytest.raises(ConfirmationRequired):
        await tools["turno_delete_project"].fn(project_id=1)


async def test_destructive_tool_gate_precedes_network():
    # cancel_assignment must refuse before any client/config work happens
    tools = await _tools()
    with pytest.raises(ConfirmationRequired):
        await tools["turno_cancel_assignment"].fn(project_id=1)
