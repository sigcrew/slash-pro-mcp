"""Server metadata: tool count, annotations, serverInfo version, per-tool deadline, route contract."""

import asyncio
from importlib.metadata import version

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from slash_pro_mcp import common
from slash_pro_mcp.client import SlashClient
from slash_pro_mcp.server import build_server
from tests.helpers import ROUTE_SCOPES, FakeSlash, call_error, route_template

pytestmark = pytest.mark.asyncio

DESTRUCTIVE = {
    "delete_evidence",
    "delete_note",
    "delete_relation",
    "accept_all_doc_updates",
    "write_wiki_page",
    "replace_checklist",
    "accept_doc_update",
    "revert_doc_update",
    "reject_doc_update",
    "update_outcome",
    "update_project",
    "update_note",
    "update_ticket",
    "remove_ticket_from_outcome",
    "assign_ticket_prd",
    "create_wiki_page",
    "update_ticket_status",
    "create_ticket",
}


async def test_tools_are_annotated(client: SlashClient):
    tools = await build_server(client).list_tools()
    assert len(tools) == 59
    for tool in tools:
        hints = tool.annotations
        assert hints is not None and hints.readOnlyHint is not None, tool.name
        assert hints.openWorldHint is False, tool.name
        if not hints.readOnlyHint:
            assert hints.destructiveHint is (tool.name in DESTRUCTIVE), tool.name
    read = {t.name for t in tools if t.annotations.readOnlyHint}
    assert {"get_ticket", "list_tickets", "search", "find_transcript_excerpts"} <= read
    assert not read & {"add_note", "create_ticket", "claim_handoff", "readiness_refresh"}
    by_name = {t.name: t.annotations for t in tools}
    assert by_name["add_agenda_item"].idempotentHint is True


async def test_server_info_reports_the_package_version(client: SlashClient):
    async with create_connected_server_and_client_session(build_server(client)) as session:
        result = await session.initialize()
    assert result.serverInfo.name == "slash-pro"
    assert result.serverInfo.version == version("slash-pro-mcp")


async def test_each_tool_has_an_overall_deadline(client: SlashClient, fake: FakeSlash, monkeypatch):
    async def slow():
        await asyncio.sleep(5)

    monkeypatch.setattr(common, "TOOL_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(client, "list_projects", slow)
    assert "timed out" in await call_error(client, "list_tickets")


async def test_route_contract_fixture_maps_wire_paths():
    assert ROUTE_SCOPES["GET /workspaces/{workspace_id}/tickets/by-key/{key}"] == "tickets:read"
    assert route_template("GET", "/workspaces/w/meetings/search") == "GET /workspaces/{workspace_id}/meetings/search"
    assert route_template("GET", "/workspaces/w/nope") is None
