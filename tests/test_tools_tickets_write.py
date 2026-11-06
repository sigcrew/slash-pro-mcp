"""create_ticket / update_ticket_status / add_note / add_handoff / mark_deployed.

Every ticket write must: resolve by key → send `If-Match` from that GET →
retry once on 409 → surface the server error text unchanged.
"""

import json

import httpx
import pytest

from slash_pro_mcp.client import EchoClient
from tests.helpers import FakeEcho, call, call_error, call_untrusted, json_response, unfence, ws_path

pytestmark = pytest.mark.asyncio

PROJECTS = [{"id": "p-echo", "key": "ECHO"}]
RUN_ID = "00000000-0000-7000-8000-000000000060"


def ticket(number: int = 23, status: str = "todo", **extra) -> dict:
    return {
        "id": f"t-{number}",
        "number": number,
        "status": status,
        "priority": "medium",
        "title": "에코 MCP 서버",
        "updated_at": "2026-09-18T00:00:00Z",
        **extra,
    }


def by_key(fake: FakeEcho, key: str, data: dict, etag: str = '"v1"') -> None:
    fake.on("GET", ws_path(f"/tickets/by-key/{key}"), json_response(200, data, etag=etag))


# -- create_ticket ---------------------------------------------------------------


async def test_create_ticket_returns_key_and_reads_back(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-echo/tickets"), json_response(201, ticket(41, "backlog")))
    by_key(fake, "ECHO-41", ticket(41, "backlog"))

    text = await call(
        client,
        "create_ticket",
        project_key="echo",
        title="에코 MCP 서버",
        description="설명",
        priority="high",
    )

    assert unfence(text, "ticket write") == "Created: ECHO-41 · backlog · medium — 에코 MCP 서버"
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {"title": "에코 MCP 서버", "priority": "high", "description": "설명"}
    assert [c.method for c in fake.calls] == ["GET", "POST", "GET"]
    assert fake.calls[-1].path == ws_path("/tickets/by-key/ECHO-41")


async def test_create_ticket_with_status_patches_after_create(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-echo/tickets"), json_response(201, ticket(42, "backlog")))
    by_key(fake, "ECHO-42", ticket(42, "backlog"), etag='"c1"')
    fake.on(
        "PATCH",
        ws_path("/tickets/t-42"),
        json_response(200, ticket(42, "in_progress"), etag='"c2"'),
    )

    text = await call(client, "create_ticket", project_key="ECHO", title="x", status="in_progress")

    assert unfence(text, "ticket write").startswith("Created: ECHO-42 · in_progress")
    (patch,) = fake.calls_to("PATCH", ".*")
    assert patch.body == {"status": "in_progress"}
    assert patch.headers["if-match"] == '"c1"'


async def test_create_ticket_validation_and_unknown_project(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))

    assert "unknown priority: asap" in await call_error(
        client, "create_ticket", project_key="ECHO", title="x", priority="asap"
    )
    assert "Project not found: NOPE" in await call_error(client, "create_ticket", project_key="NOPE", title="x")
    assert "model needs to" in await call_error(client, "create_ticket", project_key="ECHO", title="x", model="m")
    assert not fake.calls_to("POST", ".*")


async def test_create_ticket_422_passes_field_errors(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "POST",
        ws_path("/projects/p-echo/tickets"),
        json_response(
            422,
            raw={
                "message": "Unprocessable Entity",
                "errors": [{"loc": ["body", "title"], "msg": "String should have at most 500"}],
            },
        ),
    )

    text = await call_error(client, "create_ticket", project_key="ECHO", title="x" * 501)

    assert "HTTP 422: Unprocessable Entity" in text
    assert "body.title: String should have at most 500" in text


# -- update_ticket_status --------------------------------------------------------


async def test_update_ticket_status_uses_if_match(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-23", ticket(), etag='"s1"')
    fake.on("PATCH", ws_path("/tickets/t-23"), json_response(200, ticket(status="review"), etag='"s2"'))

    text = await call(client, "update_ticket_status", key="echo-23", status="review")

    assert text == "ECHO-23 → review"
    (patch,) = fake.calls_to("PATCH", ".*")
    assert patch.headers["if-match"] == '"s1"'
    assert "x-requested-by" not in patch.headers
    assert patch.body == {"status": "review"}


async def test_update_ticket_status_rejects_unknown_status_before_any_call(client: EchoClient, fake: FakeEcho):
    assert "unknown status: merged" in await call_error(client, "update_ticket_status", key="ECHO-23", status="merged")
    assert fake.calls == []


@pytest.mark.parametrize("status", ["canceled", " CANCELED "])
async def test_update_ticket_status_refuses_canceled(client: EchoClient, fake: FakeEcho, status: str):
    """Canceling needs a reason (web app dialog); canceled stays usable as a list filter."""
    text = await call_error(client, "update_ticket_status", key="ECHO-23", status=status)
    assert "canceling needs a reason" in text
    assert fake.calls == []


async def test_create_ticket_refuses_canceled_before_any_call(client: EchoClient, fake: FakeEcho):
    text = await call_error(client, "create_ticket", project_key="ECHO", title="t", status="canceled")
    assert "canceling needs a reason" in text
    assert fake.calls == []


async def test_update_ticket_status_allows_done(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-23", ticket(), etag='"s1"')
    fake.on("PATCH", ws_path("/tickets/t-23"), json_response(200, ticket(status="done"), etag='"s2"'))
    assert await call(client, "update_ticket_status", key="ECHO-23", status="done") == "ECHO-23 → done"
    assert fake.calls_to("PATCH", ".*")[0].body == {"status": "done"}


async def test_create_ticket_keeps_the_new_key_when_a_follow_up_step_fails(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-echo/tickets"), json_response(201, ticket(45, "backlog")))
    by_key(fake, "ECHO-45", ticket(45, "backlog"))
    fake.on("PATCH", ws_path("/tickets/t-45"), json_response(422, raw={"message": "prd_page_invalid"}))
    text = unfence(
        await call(client, "create_ticket", project_key="ECHO", title="x", prd_page_id="prd-9"), "ticket write"
    )
    assert text.splitlines()[0] == "Created: ECHO-45 · backlog · medium — 에코 MCP 서버"
    assert "Created ECHO-45, but attaching the PRD failed:\nHTTP 422: prd_page_invalid" in text


async def test_update_ticket_status_retries_once_on_409_then_fails(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-23", ticket(), etag='"s1"')
    fake.on(
        "PATCH",
        ws_path("/tickets/t-23"),
        json_response(409, raw={"message": "version_conflict", "current": ticket()}, etag='"s9"'),
    )

    text = await call_error(client, "update_ticket_status", key="ECHO-23", status="review")

    assert "ECHO-23 changed elsewhere twice" in text
    assert "version_conflict" in text
    assert [c.method for c in fake.calls] == ["GET", "PATCH", "GET", "PATCH"]


# -- update_ticket (담당·기한) -------------------------------------------

ASSIGNEES = [{"id": "u-hong", "name": "홍길동"}, {"id": "u-cs", "name": "철수"}]


async def test_update_ticket_sets_assignee_by_name_and_due_with_if_match(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/assignees"), json_response(200, ASSIGNEES))
    by_key(fake, "ECHO-339", ticket(339), etag='"a1"')
    fake.on("PATCH", ws_path("/tickets/t-339"), json_response(200, ticket(339), etag='"a2"'))

    text = await call(client, "update_ticket", key="echo-339", assignee="철수", due="2026-10-31")

    assert unfence(text, "ticket write") == "ECHO-339 assignee → 철수 · due → 2026-10-31"
    (patch,) = fake.calls_to("PATCH", ".*")
    assert patch.headers["if-match"] == '"a1"'
    assert patch.body == {"assignee_id": "u-cs", "due_date": "2026-10-31"}


async def test_update_ticket_clears_and_keeps_untouched_fields(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-339", ticket(339), etag='"a1"')
    fake.on("PATCH", ws_path("/tickets/t-339"), json_response(200, ticket(339), etag='"a2"'))

    assert unfence(await call(client, "update_ticket", key="ECHO-339", due="없음"), "ticket write") == (
        "ECHO-339 due → none"
    )
    assert fake.calls_to("PATCH", ".*")[-1].body == {"due_date": None}
    assert unfence(await call(client, "update_ticket", key="ECHO-339", assignee="none"), "ticket write") == (
        "ECHO-339 assignee → none"
    )
    assert fake.calls_to("PATCH", ".*")[-1].body == {"assignee_id": None}
    assert not fake.calls_to("GET", ws_path("/assignees"))


async def test_update_ticket_validation_before_any_write(client: EchoClient, fake: FakeEcho):
    assert "assignee or due is required" in await call_error(client, "update_ticket", key="ECHO-339")
    assert "date must be YYYY-MM-DD" in await call_error(client, "update_ticket", key="ECHO-339", due="10월 31일")
    assert fake.calls == []


async def test_update_ticket_retries_once_on_409_then_fails(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-339", ticket(339), etag='"a1"')
    fake.on(
        "PATCH",
        ws_path("/tickets/t-339"),
        json_response(409, raw={"message": "version_conflict"}, etag='"a9"'),
    )

    text = await call_error(client, "update_ticket", key="ECHO-339", due="2026-10-31")

    assert "ECHO-339 changed elsewhere twice" in text
    assert [c.method for c in fake.calls] == ["GET", "PATCH", "GET", "PATCH"]


# -- add_note --------------------------------------------------------------------


async def test_add_note_posts_body_with_if_match(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-23", ticket(), etag='"n1"')
    fake.on("POST", ws_path("/tickets/t-23/notes"), json_response(201, {"id": "note-1"}, etag='"n2"'))

    text = await call(client, "add_note", key="ECHO-23", body="커밋 abc123")

    assert text == "Note added note-1 (ECHO-23)"
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {"body": "커밋 abc123"}
    assert post.headers["if-match"] == '"n1"'


async def test_add_note_recovers_from_one_409(client: EchoClient, fake: FakeEcho):
    etags = iter(['"n1"', '"n2"'])
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ECHO-23"),
        lambda _r: json_response(200, ticket(), etag=next(etags)),
    )

    def notes(request: httpx.Request) -> httpx.Response:
        if request.headers["If-Match"] == '"n1"':
            return json_response(409, raw={"message": "version_conflict"}, etag='"n2"')
        return json_response(201, {"id": "note-2"}, etag='"n3"')

    fake.on("POST", ws_path("/tickets/t-23/notes"), notes)

    assert await call(client, "add_note", key="ECHO-23", body="x") == "Note added note-2 (ECHO-23)"
    assert [c.headers["if-match"] for c in fake.calls_to("POST", ".*")] == ['"n1"', '"n2"']


async def test_add_note_empty_body_and_missing_ticket(client: EchoClient, fake: FakeEcho):
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ECHO-999"),
        json_response(404, raw={"message": "Ticket not found"}),
    )

    assert "body must not be empty" in await call_error(client, "add_note", key="ECHO-23", body="  ")
    assert "HTTP 404: Ticket not found" in await call_error(client, "add_note", key="ECHO-999", body="x")


# -- add_handoff -----------------------------------------------------------------


async def test_add_handoff_matches_skill_payload(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-23", ticket(), etag='"h1"')
    fake.on("POST", ws_path("/tickets/t-23/notes"), json_response(201, {"id": "note-h"}, etag='"h2"'))

    text = await call(
        client,
        "add_handoff",
        key="ECHO-23",
        goal="MCP 서버 완성",
        to="Codex",
        done="읽기 도구 8개",
        remaining=["쓰기 도구", " ", "문서"],
        acceptance=["pytest 통과"],
        verification="uv run pytest tests/slash_pro_mcp",
    )

    assert text == "Handoff note added note-h (ECHO-23)"
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {
        "kind": "handoff",
        "handoff": {
            "goal": "MCP 서버 완성",
            "to_agent": "Codex",
            "done": "읽기 도구 8개",
            "remaining": ["쓰기 도구", "문서"],
            "acceptance": ["pytest 통과"],
            "verification": "uv run pytest tests/slash_pro_mcp",
        },
    }
    assert post.headers["if-match"] == '"h1"'


async def test_add_handoff_defaults_and_requires_goal(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-23", ticket())
    fake.on("POST", ws_path("/tickets/t-23/notes"), json_response(201, {"id": "note-h2"}))

    await call(client, "add_handoff", key="ECHO-23", goal="g")
    assert fake.calls_to("POST", ".*")[0].body["handoff"] == {
        "goal": "g",
        "to_agent": None,
        "done": "",
        "remaining": [],
        "acceptance": [],
        "verification": "",
    }
    assert "goal must not be empty" in await call_error(client, "add_handoff", key="ECHO-23", goal=" ")


# -- mark_deployed ---------------------------------------------------------------


async def test_mark_deployed_is_idempotent_and_skips_missing(client: EchoClient, fake: FakeEcho):
    by_key(fake, "ECHO-1", ticket(1, "done", notes=[]), etag='"d1"')
    by_key(
        fake,
        "ECHO-2",
        ticket(
            2,
            "done",
            notes=[{"kind": "system", "body": "0.9.0으로 배포됨"}, {"kind": "manual", "body": "x"}],
        ),
    )
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ECHO-3"),
        json_response(404, raw={"message": "Ticket not found"}),
    )
    fake.on("POST", ws_path("/tickets/t-1/notes"), json_response(201, {"id": "sys-1"}))

    text = await call(client, "mark_deployed", version="0.9.0", keys=["echo-1", "ECHO-2", "ECHO-3"])

    assert text.splitlines() == ["ECHO-1: recorded", "ECHO-2: already recorded", "ECHO-3: not found (skipped)"]
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {"kind": "system", "body": "0.9.0으로 배포됨"}
    assert post.headers["if-match"] == '"d1"'
    first_get = fake.calls_to("GET", ws_path("/tickets/by-key/ECHO-1"))[0]
    assert first_get.query == {"include": "notes"}


async def test_mark_deployed_validation_and_other_errors(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/ECHO-1"), json_response(500, raw={"message": "boom"}))

    assert "version must not be empty" in await call_error(client, "mark_deployed", version=" ", keys=["ECHO-1"])
    assert await call(client, "mark_deployed", version="1.0.0", keys=[]) == "(no tickets)"
    too_many = [f"ECHO-{i}" for i in range(51)]
    assert "at most 50" in await call_error(client, "mark_deployed", version="1.0.0", keys=too_many)
    assert "HTTP 500: boom" in await call_error(client, "mark_deployed", version="1.0.0", keys=["ECHO-1"])


async def test_verification_start_is_explicit_and_report_keeps_run_through_get(client, fake):
    import json

    by_key(fake, "ECHO-23", ticket(), etag='"fresh"')
    fake.on(
        "POST",
        ws_path("/tickets/t-23/verification-runs"),
        json_response(201, {"id": "run-1", "payload": {"verification_basis": {"answer_revision": 1}}}),
    )
    started = await call_untrusted(client, "start_verification", key="ECHO-23", commits=["repo@abc"])
    assert json.loads(started)["id"] == "run-1"
    fake.on(
        "POST",
        ws_path("/tickets/t-23/notes"),
        json_response(201, {"id": "note-1", "payload": {"basis_current_at_report": False}}),
    )
    report = {"run_id": RUN_ID, "commits": ["repo@abc"], "stage": "review", "verdict": "passed"}
    response = await call_untrusted(client, "add_verification", key="ECHO-23", report=report)
    assert json.loads(response)["payload"]["basis_current_at_report"] is False
    posts = fake.calls_to("POST", ".*")
    assert len(posts) == 2
    assert posts[1].body["verification"] == report
    assert posts[1].headers["if-match"] == '"fresh"'


async def test_start_verification_surfaces_unread_answers_and_report_passes_them(client, fake):
    """start 응답의 new_answer_question_ids가 모델 맥락에 들어가고,
    add_verification은 그 id들을 그대로 보고에 실어 보낸다."""
    import json

    by_key(fake, "ECHO-89", ticket(), etag='"fresh"')
    fake.on(
        "POST",
        ws_path("/tickets/t-23/verification-runs"),
        json_response(
            201,
            {
                "id": "run-9",
                "body": "검증 시작 — 답변 기준 v1 · 마지막 검증 이후 새로 답변된 질문 1건",
                "payload": {
                    "verification_basis": {
                        "answer_revision": 1,
                        "new_answer_question_ids": ["q-1"],
                    }
                },
            },
        ),
    )
    started = json.loads(await call_untrusted(client, "start_verification", key="ECHO-89", commits=["repo@abc"]))
    assert started["payload"]["verification_basis"]["new_answer_question_ids"] == ["q-1"]
    assert "새로 답변된 질문 1건" in started["body"]

    fake.on("POST", ws_path("/tickets/t-23/notes"), json_response(201, {"id": "note-9"}))
    report = {
        "run_id": RUN_ID,
        "commits": ["repo@abc"],
        "stage": "review",
        "verdict": "passed",
        "answers_acknowledged": ["q-1"],
    }
    await call_untrusted(client, "add_verification", key="ECHO-89", report=report)
    assert fake.calls_to("POST", ".*")[-1].body["verification"] == report


async def test_create_sub_ticket_resolves_parent_and_reports_hierarchy(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    by_key(fake, "ECHO-10", ticket(10))
    child = ticket(51, parent={"id": "t-10", "key": "ECHO-10"})
    fake.on("POST", ws_path("/projects/p-echo/tickets"), json_response(201, child))
    by_key(fake, "ECHO-51", child)
    text = await call(client, "create_ticket", project_key="echo", title="하위 작업", parent=" echo-10 ")
    assert "ECHO-51" in text and "↳ ECHO-10" in text
    (post,) = fake.calls_to("POST", ".*")
    assert post.body["parentId"] == "t-10"
    assert "followsTicketId" not in post.body


async def test_unknown_parent_never_creates_root_ticket(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/tickets/by-key/ECHO-999"), json_response(404, raw={"message": "not_found"}))
    text = await call_error(client, "create_ticket", project_key="ECHO", title="하위 작업", parent="ECHO-999")
    assert "404" in text
    assert not fake.calls_to("POST", ".*")


async def test_parent_validation_error_is_not_retried_as_root(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    by_key(fake, "ECHO-10", ticket(10, parent={"id": "t-1", "key": "ECHO-1"}))
    fake.on("POST", ws_path("/projects/p-echo/tickets"), json_response(422, raw={"message": "parent_is_sub"}))
    text = await call_error(client, "create_ticket", project_key="ECHO", title="하위 작업", parent="ECHO-10")
    assert "parent_is_sub" in text
    assert len(fake.calls_to("POST", ".*")) == 1


async def test_add_verification_schema_rejects_bad_reports_before_any_call(client, fake):
    for bad in (
        {"verdict": "ok", "stage": "review"},
        {"verdict": "passed", "stage": "review", "tests": [{"command": "pytest", "result": "green"}]},
        {"verdict": "passed", "stage": "review", "issues_found": [{"text": "x", "severity": "critical"}]},
        {"verdict": "passed", "stage": "review", "extra": 1},
    ):
        assert "validation error" in await call_error(client, "add_verification", key="ECHO-23", report=bad)
    assert fake.calls == []


async def test_add_verification_schema_lists_enums(client):
    from tests.helpers import build_server

    tools = {t.name: t for t in await build_server(client).list_tools()}
    schema = json.dumps(tools["add_verification"].inputSchema)
    for value in ('"passed"', '"blocked"', '"re_review"', '"important"', '"minor"', '"failed"'):
        assert value in schema
    assert not any("$ref" in json.dumps(t.inputSchema) or "$defs" in json.dumps(t.inputSchema) for t in tools.values())


async def test_create_ticket_follow_up_failure_lists_skipped_steps(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    by_key(fake, "ECHO-1", ticket(1))
    fake.on("POST", ws_path("/projects/p-echo/tickets"), json_response(201, ticket(46)))
    fake.on("POST", ws_path("/tickets/t-46/relations"), json_response(409, raw={"message": "relation_cycle"}))
    text = unfence(
        await call(client, "create_ticket", project_key="ECHO", title="x", follows="ECHO-1,ECHO-2", status="review"),
        "ticket write",
    )
    assert text.startswith("Created: ECHO-46")
    assert "Created ECHO-46, but linking follows ECHO-2 failed:\nHTTP 409: relation_cycle" in text
    assert text.endswith("Skipped: re-reading the ticket, setting status review")
    assert "at most 20" in await call_error(
        client, "create_ticket", project_key="ECHO", follows=",".join(f"ECHO-{i}" for i in range(21))
    )


async def test_create_ticket_slow_follow_up_still_returns_the_key(client, fake, monkeypatch):
    import asyncio

    from slash_pro_mcp import common

    monkeypatch.setattr(common, "TOOL_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(common, "FOLLOW_UP_MARGIN_SECONDS", 0.3)
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-echo/tickets"), json_response(201, ticket(47)))
    real_get = client.get_ticket_by_key

    async def slow_get(key, **kw):
        if key == "ECHO-47":
            await asyncio.sleep(5)
        return await real_get(key, **kw)

    monkeypatch.setattr(client, "get_ticket_by_key", slow_get)
    text = unfence(await call(client, "create_ticket", project_key="ECHO", title="x"), "ticket write")
    assert text.startswith("Created: ECHO-47")
    assert "but re-reading the ticket failed:\nTimeoutError" in text
