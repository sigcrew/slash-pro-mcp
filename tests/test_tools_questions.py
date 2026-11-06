"""ask_questions / list_questions."""

import pytest

from slash_pro_mcp.client import SlashClient
from tests.helpers import FakeSlash, call_error, call_untrusted, json_response, ws_path

pytestmark = pytest.mark.asyncio


def q(**extra) -> dict:
    return {
        "id": "q-1",
        "ticketId": "t-3",
        "ticketKey": "ABC-3",
        "ticketTitle": "가입 축소",
        "rubricKey": "acceptance",
        "rubricLabel": "완료 조건",
        "question": "무엇을 보면 끝?",
        "options": ["가입 1분", "이탈 30%"],
        "state": "open",
        "askedByLabel": "Claude Code",
        "askedAt": "2026-09-19T01:00:00+00:00",
        "handoffNoteId": None,
        **extra,
    }


async def test_ask_questions_maps_aliases_and_handoff(client: SlashClient, fake: FakeSlash):
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ABC-3"),
        json_response(200, {"id": "t-3"}, etag='"v1"'),
    )
    fake.on("POST", ws_path("/tickets/t-3/questions"), json_response(201, [q()]))
    text = await call_untrusted(
        client,
        "ask_questions",
        key="abc-3",
        questions=[
            {
                "rubric_key": "완료조건",
                "question": "무엇을 보면 끝?",
                "options": ["가입 1분", "이탈 30%"],
            }
        ],
        handoff_id="h-1",
    )
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {
        "questions": [
            {
                "rubric_key": "acceptance",
                "question": "무엇을 보면 끝?",
                "options": ["가입 1분", "이탈 30%"],
                "source_bullet": None,
            }
        ],
        "handoff_note_id": "h-1",
    }
    assert text == (
        "Asked 1 question(s) (ABC-3) · handoff h-1 → input_required\n"
        "- [open · 완료 조건] 무엇을 보면 끝? (선택: 가입 1분 / 이탈 30%) — q-1"
    )
    assert "unknown rubric_key: 담당" in await call_error(
        client,
        "ask_questions",
        key="ABC-3",
        questions=[{"rubric_key": "담당", "question": "?"}],
    )
    assert "questions must have 1-4 items" in await call_error(client, "ask_questions", key="ABC-3", questions=[])


async def test_ask_questions_surfaces_cap(client: SlashClient, fake: FakeSlash):
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ABC-3"),
        json_response(200, {"id": "t-3"}, etag='"v1"'),
    )
    fake.on(
        "POST",
        ws_path("/tickets/t-3/questions"),
        json_response(
            409,
            raw={
                "message": "열린 질문이 이미 4개입니다",
                "code": "questions_open",
                "open": [q()],
            },
        ),
    )
    text = await call_error(
        client,
        "ask_questions",
        key="ABC-3",
        questions=[{"rubric_key": "goal", "question": "왜?"}],
    )
    assert "HTTP 409: 열린 질문이 이미 4개입니다" in text


async def test_list_questions_by_key_and_inbox(client: SlashClient, fake: FakeSlash):
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ABC-3"),
        json_response(200, {"id": "t-3"}, etag='"v1"'),
    )
    fake.on("GET", ws_path("/tickets/t-3/questions"), json_response(200, [q()]))
    text = await call_untrusted(client, "list_questions", key="ABC-3")
    assert fake.calls[-1].query == {"state": "open"}
    assert text == (
        "- [open · 완료 조건] ABC-3 무엇을 보면 끝? (선택: 가입 1분 / 이탈 30%) — Claude Code "
        "2026-09-19T01:00:00+00:00 (id q-1) → /tickets?ticket=t-3"
    )
    fake.on("GET", ws_path("/questions/inbox"), json_response(200, []))
    assert await call_untrusted(client, "list_questions", to="me") == "(질문 없음)"
    assert fake.calls[-1].query == {"to": "me"}
    assert "state applies only with key" in await call_error(client, "list_questions", state="answered")
    assert "key or to, not both" in await call_error(client, "list_questions", key="ABC-3", to="me")
    member = "00000000-0000-7000-8000-0000000000aa"
    await call_untrusted(client, "list_questions", to=member.upper())
    assert fake.calls[-1].query == {"to": member}
    fake.on("GET", ws_path("/assignees"), json_response(200, [{"id": member, "name": "철수"}]))
    await call_untrusted(client, "list_questions", to="철수")
    assert fake.calls[-1].query == {"to": member}


async def test_readiness_refresh_posts_judge_and_renders(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/tickets/by-key/ABC-3"), json_response(200, {"id": "t-3"}, etag='"v1"'))
    fake.on(
        "POST",
        ws_path("/tickets/t-3/readiness/judge"),
        json_response(
            200,
            {
                "judged": True,
                "skipped": None,
                "remaining": 1,
                "judgedAt": "2026-09-20T01:00:00+00:00",
                "questions": [
                    q(
                        rubricKey="scope",
                        rubricLabel="범위",
                        dimension="scope",
                        question="구현자는 '결제는 제외'라고 가정할 것 같습니다. 맞나요?",
                        options=["결제는 제외", "결제 포함"],
                        askedByLabel="에코",
                    )
                ],
            },
        ),
    )
    text = await call_untrusted(client, "readiness_refresh", key="abc-3")
    (post,) = fake.calls_to("POST", ".*")
    assert post.body in (None, {}, b"")
    assert text.splitlines()[0] == "Remaining assumptions: 1 (ABC-3) · judged"
    assert (
        "[open · 범위] 구현자는 '결제는 제외'라고 가정할 것 같습니다. 맞나요? (선택: 결제는 제외 / 결제 포함) — q-1"
        in text
    )


async def test_readiness_refresh_reports_skips_and_limit(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/tickets/by-key/ABC-3"), json_response(200, {"id": "t-3"}, etag='"v1"'))
    fake.on(
        "POST",
        ws_path("/tickets/t-3/readiness/judge"),
        json_response(
            200,
            {
                "judged": False,
                "skipped": "open_questions",
                "remaining": 2,
                "judgedAt": None,
                "questions": [q(), q(id="q-2")],
            },
        ),
    )
    text = await call_untrusted(client, "readiness_refresh", key="ABC-3")
    assert (
        text.splitlines()[0] == "Remaining assumptions: 2 (ABC-3) · open questions exist; wait for a person to answer"
    )
    fake.on(
        "POST",
        ws_path("/tickets/t-3/readiness/judge"),
        json_response(409, raw={"code": "judge_limit", "message": "오늘 판정 3회를 다 썼습니다"}),
    )
    err = await call_error(client, "readiness_refresh", key="ABC-3")
    assert "judge_limit" in err or "3회" in err
