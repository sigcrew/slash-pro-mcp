"""Server metadata: tool count, annotations, serverInfo version, per-tool deadline, route contract."""

import asyncio
import json
from importlib.metadata import version

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from slash_pro_mcp import common
from slash_pro_mcp.client import SlashClient
from slash_pro_mcp.config import TOOL_GROUPS
from slash_pro_mcp.server import GROUPS, build_server
from tests.helpers import ROUTE_SCOPES, FakeSlash, call_error, route_template

pytestmark = pytest.mark.asyncio

DESTRUCTIVE = {
    "delete_evidence",
    "delete_note",
    "delete_relation",
    "accept_all_doc_updates",
    "write_wiki_page",
    "replace_checklist",
    "resolve_doc_update",
    "update_outcome",
    "update_project",
    "update_note",
    "update_ticket",
    "remove_ticket_from_outcome",
    "assign_ticket_prd",
    "create_wiki_page",
    "create_ticket",
}


async def test_tools_are_annotated(client: SlashClient):
    tools = await build_server(client).list_tools()
    assert len(tools) == 50
    for tool in tools:
        hints = tool.annotations
        assert hints is not None and hints.readOnlyHint is not None, tool.name
        assert hints.openWorldHint is False, tool.name
        if not hints.readOnlyHint:
            assert hints.destructiveHint is (tool.name in DESTRUCTIVE), tool.name
    read = {t.name for t in tools if t.annotations.readOnlyHint}
    assert {"get_ticket", "list_tickets", "search", "find_transcript_excerpts"} <= read
    assert not read & {"add_note", "create_ticket", "update_handoff", "readiness_refresh"}
    by_name = {t.name: t.annotations for t in tools}
    assert by_name["add_agenda_item"].idempotentHint is True


async def test_tool_groups_filter_registration(client: SlashClient):
    assert set(GROUPS) == set(TOOL_GROUPS)
    names = {t.name for t in await build_server(client, tools=frozenset({"tickets"})).list_tools()}
    assert "get_ticket" in names and "list_meetings" not in names and "search" not in names
    # every tool belongs to exactly one group
    counts = [len(await build_server(client, tools=frozenset({g})).list_tools()) for g in GROUPS]
    assert all(counts) and sum(counts) == 50


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
    assert "시간 초과" in await call_error(client, "list_tickets")


async def test_route_contract_fixture_maps_wire_paths():
    assert ROUTE_SCOPES["GET /workspaces/{workspace_id}/tickets/by-key/{key}"] == "tickets:read"
    assert route_template("GET", "/workspaces/w/meetings/search") == "GET /workspaces/{workspace_id}/meetings/search"
    assert route_template("GET", "/workspaces/w/nope") is None


# Every agent session pays the whole tools/list payload in context before its first call. Keep new
# descriptions terse; raise this only for calling-relevant text.
TOOLS_LIST_BUDGET = 33_000  # ~32k at 0.1.0


async def test_tools_list_payload_stays_within_budget(client: SlashClient):
    tools = await build_server(client).list_tools()
    size = len(json.dumps([t.model_dump(exclude_none=True) for t in tools], ensure_ascii=False))
    assert size <= TOOLS_LIST_BUDGET, f"tools/list is {size} chars (budget {TOOLS_LIST_BUDGET})"
