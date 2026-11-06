"""relate_ticket · create_ticket(follows, request_builder)."""

import pytest

from tests.helpers import call, call_error, call_untrusted, json_response, unfence, ws_path
from tests.test_tools_tickets_write import PROJECTS, by_key, ticket

pytestmark = pytest.mark.asyncio

RELATION = {
    "id": "r-1",
    "kind": "follows",
    "source": "api",
    "actorLabel": "Claude Code",
    "createdAt": "2026-09-20T00:00:00Z",
    "from": {"key": "ABC-2", "id": "t-2", "title": "B", "status": "todo"},
    "to": {"key": "ABC-1", "id": "t-1", "title": "A", "status": "review"},
}


async def test_relate_ticket_posts_and_renders(client, fake):
    by_key(fake, "ABC-2", ticket(2, "todo"))
    fake.on("POST", ws_path("/tickets/t-2/relations"), json_response(201, RELATION))
    text = await call_untrusted(client, "relate_ticket", key="abc-2", kind="follows", target_key="ABC-1")
    assert text == "Relation added: ABC-2 follows ABC-1"
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {"kind": "follows", "targetKey": "ABC-1", "reverse": False}


async def test_relate_ticket_existing_200_and_reverse(client, fake):
    by_key(fake, "ABC-2", ticket(2, "todo"))
    fake.on(
        "POST",
        ws_path("/tickets/t-2/relations"),
        json_response(200, {**RELATION, "kind": "blocks", "from": RELATION["to"], "to": RELATION["from"]}),
    )
    text = await call_untrusted(client, "relate_ticket", key="ABC-2", kind="blocks", target_key="ABC-1", reverse=True)
    assert text == "Already exists: ABC-1 blocks ABC-2"
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {"kind": "blocks", "targetKey": "ABC-1", "reverse": True}


async def test_relate_ticket_409_surfaces_server_message(client, fake):
    by_key(fake, "ABC-2", ticket(2, "todo"))
    fake.on(
        "POST",
        ws_path("/tickets/t-2/relations"),
        json_response(409, raw={"message": "relation_cycle", "code": 409, "path": ["t-1", "t-2"]}),
    )
    text = await call_error(client, "relate_ticket", key="ABC-2", kind="blocks", target_key="ABC-1")
    assert "relation_cycle" in text
    text = await call_error(client, "relate_ticket", key="ABC-2", kind="relates", target_key="ABC-1")
    assert "kind" in text


async def test_create_ticket_with_follows_and_builder(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    by_key(fake, "ABC-1", ticket(1, "review"))
    fake.on("POST", ws_path("/projects/p-demo/tickets"), json_response(201, ticket(41, "todo")))
    by_key(fake, "ABC-41", ticket(41, "todo"))
    text = await call(client, "create_ticket", project_key="abc", follows="abc-1", request_builder=True)
    assert unfence(text, "ticket write").startswith("Created: ABC-41")
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {"priority": "medium", "followsTicketId": "t-1", "requestBuilder": True}


async def test_create_ticket_needs_title_or_follows(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    text = await call_error(client, "create_ticket", project_key="abc")
    assert "title" in text and "follows" in text
    assert fake.calls_to("POST", ".*") == []


async def test_create_ticket_with_explicit_target_and_model(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    by_key(fake, "ABC-1", ticket(1, "review"))
    fake.on("POST", ws_path("/projects/p-demo/tickets"), json_response(201, ticket(42, "todo")))
    by_key(fake, "ABC-42", ticket(42, "todo"))
    await call(client, "create_ticket", project_key="abc", follows="abc-1", to="Claude Code", model="claude-opus-4-8")
    (post,) = fake.calls_to("POST", ".*")
    assert post.body["requestTarget"] == {"toAgent": "Claude Code", "toModel": "claude-opus-4-8"}
    assert "requestBuilder" not in post.body


# --- 원 티켓 여럿, ✗ 가져오기 동의, 서버 경고 -----------------------------------


async def test_relate_follows_reports_available_rejected_without_consent(client, fake):
    by_key(fake, "ABC-2", ticket(2, "todo"))
    fake.on(
        "POST",
        ws_path("/tickets/t-2/relations"),
        json_response(201, {**RELATION, "rejectedAvailable": 2, "rejectedTaken": 0, "warnings": []}),
    )
    text = await call(client, "relate_ticket", key="ABC-2", kind="follows", target_key="ABC-1")
    assert "Relation added: ABC-2 follows ABC-1" in text
    assert "2 rejected (✗) row(s) can be copied" in text and "take_rejected=True" in text
    (post,) = fake.calls_to("POST", ".*")
    assert "takeRejected" not in post.body  # 동의 없이는 보내지 않는다


async def test_relate_follows_take_rejected_sends_consent_and_reports(client, fake):
    by_key(fake, "ABC-2", ticket(2, "todo"))
    fake.on(
        "POST",
        ws_path("/tickets/t-2/relations"),
        json_response(201, {**RELATION, "rejectedAvailable": 0, "rejectedTaken": 2, "warnings": []}),
    )
    text = await call(client, "relate_ticket", key="ABC-2", kind="follows", target_key="ABC-1", take_rejected=True)
    assert "Copied 2 rejected (✗) row(s) from ABC-1" in text
    (post,) = fake.calls_to("POST", ".*")
    assert post.body["takeRejected"] is True


async def test_relate_blocks_prints_server_warnings(client, fake):
    by_key(fake, "ABC-2", ticket(2, "todo"))
    fake.on(
        "POST",
        ws_path("/tickets/t-2/relations"),
        json_response(
            201,
            {
                **RELATION,
                "kind": "blocks",
                "warnings": ["ABC-1는 이미 review 상태예요. 이어받음(follows)으로 걸어 주세요."],
            },
        ),
    )
    text = await call(client, "relate_ticket", key="ABC-2", kind="blocks", target_key="ABC-1")
    assert "Warning: ABC-1는 이미 review 상태예요" in text


async def test_create_ticket_with_several_origins_relates_the_rest(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    by_key(fake, "ABC-1", ticket(1, "review"))
    fake.on(
        "POST",
        ws_path("/projects/p-demo/tickets"),
        json_response(201, {**ticket(43, "todo"), "id": "t-43"}),
    )
    by_key(fake, "ABC-43", ticket(43, "todo"))
    fake.on(
        "POST",
        ws_path("/tickets/t-43/relations"),
        json_response(201, {**RELATION, "from": {**RELATION["from"], "key": "ABC-43"}, "rejectedAvailable": 1}),
    )
    text = await call(client, "create_ticket", project_key="abc", title="보완", follows="abc-1, abc-9")
    assert unfence(text, "ticket write").startswith("Created: ABC-43")
    assert "1 rejected (✗) row(s) can be copied" in text
    first, second = fake.calls_to("POST", ".*")
    assert first.body["followsTicketId"] == "t-1"
    assert second.body == {"kind": "follows", "targetKey": "ABC-9"}


async def test_create_ticket_prints_server_warning_header(client, fake):
    import json as _json
    from urllib.parse import quote

    import httpx

    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    warning = "지금 맡아 일하는 티켓(ABC-1)이 있어요. 보완이면 follows=ABC-1로 만들어야 해요."
    fake.on(
        "POST",
        ws_path("/projects/p-demo/tickets"),
        httpx.Response(
            201,
            json={"data": ticket(44, "todo")},
            headers={
                "Content-Type": "application/json",
                "X-Echo-Warnings": quote(_json.dumps([warning], ensure_ascii=False), safe=""),
            },
        ),
    )
    by_key(fake, "ABC-44", ticket(44, "todo"))
    text = await call(client, "create_ticket", project_key="abc", title="새 일")
    assert unfence(text, "ticket write").startswith("Created: ABC-44")
    assert f"Warning: {warning}" in text
