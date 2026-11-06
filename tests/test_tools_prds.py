"""list_prds / get_prd / create_prd / assign_ticket_prd + list_tickets(prd=)."""

import pytest

from slash_pro_mcp.client import SlashClient
from tests.helpers import FakeSlash, call, call_error, call_untrusted, json_response, text_response, unfence, ws_path

pytestmark = pytest.mark.asyncio

PROJECTS = [{"id": "p-demo", "key": "ABC"}]


def prd(**extra) -> dict:
    return {
        "pageId": "00000000-0000-7000-8000-000000000033",
        "title": "온보딩 개선",
        "ticketTotal": 3,
        "ticketDone": 1,
        "updatedAt": "2026-09-19T01:00:00+00:00",
        **extra,
    }


async def test_list_prds(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "GET",
        ws_path("/projects/p-demo/prds"),
        json_response(
            200, [prd(), prd(pageId="00000000-0000-7000-8000-000000000034", title="결제", ticketTotal=0, ticketDone=0)]
        ),
    )
    text = await call_untrusted(client, "list_prds", project_key="abc")
    assert (
        text
        == "- 온보딩 개선 · 1/3 (id 00000000-0000-7000-8000-000000000033)\n- 결제 · 0/0 (id 00000000-0000-7000-8000-000000000034)"
    )
    fake.on("GET", ws_path("/projects/p-demo/prds"), json_response(200, []))
    assert await call_untrusted(client, "list_prds", project_key="ABC") == "(PRD 없음)"
    assert "Project not found: NOPE" in await call_error(client, "list_prds", project_key="NOPE")


async def test_get_prd_proxies_markdown(client: SlashClient, fake: FakeSlash):
    fake.on(
        "GET",
        ws_path("/prds/00000000-0000-7000-8000-000000000033"),
        text_response(200, "# 온보딩 개선\n\n## 소속 티켓 (1/3)"),
    )
    assert (
        await call_untrusted(client, "get_prd", prd_page_id=" 00000000-0000-7000-8000-000000000033 ")
        == "# 온보딩 개선\n\n## 소속 티켓 (1/3)"
    )
    assert fake.calls[-1].query == {"format": "markdown"}


async def test_create_prd_builds_sections_and_assigns_keys(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-demo/prds"), json_response(201, prd(ticketTotal=0, ticketDone=0)))
    fake.on("GET", ws_path("/tickets/by-key/ABC-3"), json_response(200, {"id": "t-3"}, etag='"v1"'))
    fake.on(
        "PATCH",
        ws_path("/tickets/t-3"),
        json_response(200, {"id": "t-3", "prdPageId": "00000000-0000-7000-8000-000000000033"}, etag='"v2"'),
    )
    text = await call(
        client,
        "create_prd",
        project_key="ABC",
        title="온보딩 개선",
        problem="이탈 40%",
        scope=["가입"],
        out_of_scope=["결제", " "],
        verification="가입 1분",
        ticket_keys=["abc-3"],
    )
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {
        "title": "온보딩 개선",
        "sections": {"문제": "이탈 40%", "투입 범위 · 식욕": "- 가입", "범위 밖": "- 결제", "결과 확인": "가입 1분"},
    }
    (patch,) = fake.calls_to("PATCH", ".*")
    assert patch.body == {"prd_page_id": "00000000-0000-7000-8000-000000000033"} and patch.headers["if-match"] == '"v1"'
    assert unfence(text, "prd write") == (
        "PRD created: 온보딩 개선 (id 00000000-0000-7000-8000-000000000033)\nAttached: ABC-3"
    )
    assert "title must not be empty" in await call_error(client, "create_prd", project_key="ABC", title=" ")
    too_many = [f"ABC-{i}" for i in range(21)]
    assert "at most 20" in await call_error(client, "create_prd", project_key="ABC", title="t", ticket_keys=too_many)


async def test_create_prd_keeps_the_new_id_when_attaching_fails(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-demo/prds"), json_response(201, prd()))
    fake.on("GET", ws_path("/tickets/by-key/ABC-3"), json_response(200, {"id": "t-3"}, etag='"v1"'))
    fake.on("PATCH", ws_path("/tickets/t-3"), json_response(422, raw={"message": "prd_page_invalid"}))
    text = unfence(await call(client, "create_prd", project_key="ABC", title="t", ticket_keys=["ABC-3"]), "prd write")
    assert text.startswith("PRD created: 온보딩 개선 (id 00000000-0000-7000-8000-000000000033)\n")
    assert "but attaching ABC-3 failed:\nHTTP 422: prd_page_invalid" in text


async def test_assign_ticket_prd_and_unassign(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/tickets/by-key/ABC-3"), json_response(200, {"id": "t-3"}, etag='"v1"'))
    fake.on(
        "PATCH",
        ws_path("/tickets/t-3"),
        json_response(
            200,
            {"id": "t-3", "prd": {"pageId": "00000000-0000-7000-8000-000000000033", "title": "온보딩 개선"}},
            etag='"v2"',
        ),
    )
    assert (
        await call_untrusted(
            client, "assign_ticket_prd", key="abc-3", prd_page_id="00000000-0000-7000-8000-000000000033"
        )
        == "ABC-3 → PRD 온보딩 개선"
    )
    assert fake.calls[-1].body == {"prd_page_id": "00000000-0000-7000-8000-000000000033"}
    fake.on("PATCH", ws_path("/tickets/t-3"), json_response(200, {"id": "t-3", "prd": None}, etag='"v3"'))
    assert await call_untrusted(client, "assign_ticket_prd", key="ABC-3") == "ABC-3 → PRD none"
    assert fake.calls[-1].body == {"prd_page_id": None}


async def test_list_tickets_forwards_prd_filter(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/projects/p-demo/tickets"), json_response(200, []))
    await call_untrusted(client, "list_tickets", project_key="ABC", prd="00000000-0000-7000-8000-000000000033")
    assert fake.calls[-1].query.get("prd_page_id") == "00000000-0000-7000-8000-000000000033"
    await call_untrusted(client, "list_tickets", project_key="ABC", prd="none")
    assert fake.calls[-1].query.get("prd_page_id") == "none"


async def test_create_prd_with_markdown_replaces_skeleton(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-demo/prds"), json_response(201, prd(ticketTotal=0, ticketDone=0)))
    fake.on(
        "PUT",
        ws_path("/wiki/pages/00000000-0000-7000-8000-000000000033/content"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000033"}),
    )
    md = "## 문제\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] 할 일"
    text = await call(client, "create_prd", project_key="ABC", title="온보딩 개선", markdown=md)
    assert fake.calls[-1].body == {"format": "markdown", "content": md, "mode": "replace"}
    assert unfence(text, "prd write") == "PRD created: 온보딩 개선 (id 00000000-0000-7000-8000-000000000033)"
    assert "cannot be combined" in await call_error(
        client, "create_prd", project_key="ABC", title="x", problem="y", markdown=md
    )
