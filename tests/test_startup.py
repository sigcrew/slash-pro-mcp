"""Startup: the workspace comes from the token (`GET /api-tokens/me`), seeds `me`, notes missing scopes."""

import asyncio
from dataclasses import replace

import httpx
import pytest

from slash_pro_mcp import __main__ as cli
from slash_pro_mcp.client import SlashClient
from slash_pro_mcp.config import TOOL_GROUPS, ConfigError, SlashConfig
from slash_pro_mcp.instructions import GROUP_SCOPES, INSTRUCTIONS
from tests.helpers import ROUTE_SCOPES, FakeSlash, json_response, ws_path

OTHER_WS = "00000000-0000-7000-8000-000000000099"
USER = "00000000-0000-7000-8000-000000000042"
ALL_SCOPES = sorted({s for s in ROUTE_SCOPES.values() if s and s != "any"})
TOKEN_ME = "/api/api-tokens/me"


def token_me(fake: FakeSlash, workspace: str, scopes: list[str] = ALL_SCOPES) -> None:
    reply = {"workspaceId": workspace, "userId": USER, "name": "나", "label": "Codex", "scopes": scopes}
    fake.on("GET", TOKEN_ME, json_response(200, reply))


def startup(config: SlashConfig, fake: FakeSlash):
    return asyncio.run(cli._startup(config, transport=httpx.MockTransport(fake.handle)))


def test_workspace_comes_from_the_token(config: SlashConfig, fake: FakeSlash):
    token_me(fake, OTHER_WS)
    resolved, instructions, owner = startup(replace(config, workspace_id=None), fake)
    assert resolved.workspace_id == OTHER_WS
    assert instructions == INSTRUCTIONS  # every scope held: no note
    assert owner == {"id": USER, "name": "나"}
    assert fake.calls_to("GET", f"/api/workspaces/{OTHER_WS}/documents/agent-instructions")
    assert "Authorization" in {k.title() for k in fake.calls_to("GET", TOKEN_ME)[0].headers}


def test_configured_workspace_must_match_the_token(config: SlashConfig, fake: FakeSlash):
    token_me(fake, OTHER_WS)
    with pytest.raises(ConfigError, match="token belongs to workspace"):
        startup(config, fake)


def test_older_server_without_the_route(config: SlashConfig, fake: FakeSlash, capsys):
    # The fake answers 404 for a route it does not serve, like a server before /api-tokens/me.
    resolved, instructions, owner = startup(config, fake)
    assert (resolved.workspace_id, instructions, owner) == (config.workspace_id, INSTRUCTIONS, None)
    assert capsys.readouterr().err == ""
    with pytest.raises(ConfigError, match="set SLASH_PRO_WORKSPACE_ID"):
        startup(replace(config, workspace_id=None), fake)


@pytest.mark.parametrize(("status", "message"), [(401, "invalid, expired"), (403, "membership is inactive")])
def test_refused_token_fails_startup(config: SlashConfig, fake: FakeSlash, status: int, message: str):
    fake.on("GET", TOKEN_ME, json_response(status, raw={"message": "no"}))
    with pytest.raises(ConfigError, match=message):
        startup(config, fake)


def test_network_failure_needs_a_configured_workspace(config: SlashConfig, fake: FakeSlash, capsys):
    def down(_request):
        raise httpx.ConnectError("down")

    fake.on("GET", TOKEN_ME, down)
    assert startup(config, fake)[0].workspace_id == config.workspace_id
    assert "token info not read (ConnectError)" in capsys.readouterr().err
    with pytest.raises(ConfigError, match="ConnectError"):
        startup(replace(config, workspace_id=None), fake)


def test_startup_does_not_wait_for_a_slow_api(config: SlashConfig, fake: FakeSlash, monkeypatch, capsys):
    async def slow(_client, _base):
        await asyncio.sleep(30)

    token_me(fake, config.workspace_id, scopes=["tickets:read"])
    monkeypatch.setattr(cli, "STARTUP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cli, "load_instructions", slow)
    _, instructions, _ = startup(config, fake)
    assert instructions.startswith(INSTRUCTIONS) and "lacks scopes" in instructions  # token part kept
    assert "starting without them" in capsys.readouterr().err

    async def hang(_request):
        await asyncio.sleep(30)

    with pytest.raises(ConfigError, match="not read within"):
        asyncio.run(cli._startup(replace(config, workspace_id=None), transport=httpx.MockTransport(hang)))


def test_missing_scopes_are_noted_for_registered_groups(config: SlashConfig, fake: FakeSlash):
    token_me(fake, config.workspace_id, scopes=["tickets:read", "tickets:write"])
    note = startup(replace(config, tools=frozenset({"tickets", "prds"})), fake)[1].removeprefix(INSTRUCTIONS)
    assert (
        note
        == "\n\nThis token lacks scopes: document:read (prds); document:write (prds). Tools needing them return 403."
    )
    assert set(GROUP_SCOPES) == set(TOOL_GROUPS)
    assert {s for needed in GROUP_SCOPES.values() for s in needed} == set(ALL_SCOPES)


@pytest.mark.asyncio
async def test_seeded_owner_skips_assignees_me(config: SlashConfig, fake: FakeSlash):
    client = SlashClient(config, transport=httpx.MockTransport(fake.handle), me={"id": USER, "name": "나"})
    try:
        assert await client.resolve_assignee("me") == {"id": USER, "name": "나"}
    finally:
        await client.aclose()
    assert fake.calls_to("GET", ws_path("/assignees/me")) == []
