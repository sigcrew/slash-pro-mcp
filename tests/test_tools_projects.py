"""create_project / update_project and create_ticket(prd_page_id)."""

import pytest

from slash_pro_mcp.client import SlashClient
from tests.helpers import FakeSlash, call_raw, json_response, unfence, ws_path


async def ok(client, tool, args):
    is_error, text = await call_raw(client, tool, args)
    assert not is_error, text
    return text


async def err(client, tool, args):
    is_error, text = await call_raw(client, tool, args)
    assert is_error, text
    return text


pytestmark = pytest.mark.asyncio


async def test_create_project(client: SlashClient, fake: FakeSlash):
    fake.on("POST", ws_path("/projects"), json_response(201, {"id": "p-1", "key": "DEMO", "name": "Demo"}))

    text = await ok(client, "create_project", {"key": "demo", "name": "Demo", "description": "데모 앱"})

    assert unfence(text, "project write") == "Project created: DEMO — Demo `p-1`"
    assert fake.calls[-1].body == {"key": "DEMO", "name": "Demo", "description": "데모 앱"}
    for bad in (" ", "A", "1AB", "A-B", "A" * 21):
        assert "must match" in await err(client, "create_project", {"key": bad, "name": "x"})
    assert "name must not be empty" in await err(client, "create_project", {"key": "AB", "name": " "})


async def test_create_project_403_without_scope(client: SlashClient, fake: FakeSlash):
    fake.on("POST", ws_path("/projects"), json_response(403, raw={"detail": "scope_required: projects:write"}))

    assert "scope_required: projects:write" in await err(client, "create_project", {"key": "XY", "name": "x"})


async def test_update_project(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, [{"id": "p-1", "key": "DEMO"}]))
    fake.on("PATCH", ws_path("/projects/p-1"), json_response(200, {"key": "DEMO", "name": "Demo 2"}))

    text = await ok(client, "update_project", {"key": "demo", "name": "Demo 2"})

    assert unfence(text, "project write") == "Project updated: DEMO — Demo 2"
    assert fake.calls[-1].body == {"name": "Demo 2"}
    assert "name or description is required" in await err(client, "update_project", {"key": "DEMO"})


async def test_create_ticket_with_prd_links_after_create(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, [{"id": "p-1", "key": "DEMO"}]))
    fake.on("POST", ws_path("/projects/p-1/tickets"), json_response(201, {"id": "t-1", "number": 1}))
    fake.on(
        "GET",
        ws_path("/tickets/by-key/DEMO-1"),
        json_response(200, {"id": "t-1", "number": 1, "title": "T", "status": "todo"}, etag='"v1"'),
    )
    fake.on(
        "PATCH",
        ws_path("/tickets/t-1"),
        json_response(200, {"id": "t-1", "number": 1, "title": "T", "status": "todo"}, etag='"v2"'),
    )

    text = await ok(client, "create_ticket", {"project_key": "DEMO", "title": "T", "prd_page_id": "prd-9"})

    assert unfence(text, "ticket write").startswith("Created: DEMO-1")
    patch = fake.calls[-1]
    assert patch.method == "PATCH" and patch.body == {"prd_page_id": "prd-9"}
    assert patch.headers["if-match"] == '"v1"'
