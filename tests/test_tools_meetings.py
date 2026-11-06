"""get_meeting / get_minutes / find_transcript_excerpts."""

import pytest

from slash_pro_mcp.client import EchoClient
from tests.helpers import (
    FakeEcho,
    call,
    call_error,
    call_untrusted,
    json_response,
    text_response,
    unfence,
    ws_path,
)

pytestmark = pytest.mark.asyncio

MEETING_ID = "00000000-0000-7000-8000-000000000001"
MEETING = {
    "id": MEETING_ID,
    "title": "데일리 스크럼",
    "scheduled_at": "2026-09-17T10:00:00+09:00",
    "duration_minutes": 30,
    "actual_duration_minutes": 42,
    "description": None,
    "attendees": [{"id": "u1", "name": "홍길동"}, {"id": "u2", "name": None, "email": "b@example.com"}],
    "transcription": {"id": "tr", "duration_seconds": 2520, "word_count": 4100, "script_full": []},
}


SESSIONS_PATH = ws_path(f"/meetings/{MEETING_ID}/recording/sessions")
NO_SESSIONS = json_response(200, {"sessions": []})


async def test_get_meeting_combines_meeting_run_and_versions(client: EchoClient, fake: FakeEcho):
    fake.on("GET", f"/api/meetings/{MEETING_ID}", json_response(200, MEETING))
    fake.on("GET", SESSIONS_PATH, NO_SESSIONS)
    fake.on(
        "GET",
        ws_path(f"/meetings/{MEETING_ID}/analysis/run"),
        json_response(
            200,
            {
                "run": {
                    "status": "running",
                    "stage": "analyzing",
                    "finished_at": None,
                    "error": None,
                }
            },
        ),
    )
    fake.on(
        "GET",
        ws_path(f"/meetings/{MEETING_ID}/analysis/versions"),
        json_response(200, [{"one_line_summary": "MCP 연결 결정"}, {"one_line_summary": "이전"}]),
    )

    text = await call_untrusted(client, "get_meeting", meeting_id=MEETING_ID)

    assert text.splitlines() == [
        "# 데일리 스크럼",
        "",
        f"- id: {MEETING_ID}",
        "- 일시: 2026-09-17T10:00:00+09:00 · 30분 예정 (실제 42분)",
        "- 참석자(2): 홍길동, b@example.com",
        "- 전사: 있음 (42분, 4100단어)",
        "- 최신 분석 run: running / analyzing",
        "- 최신 분석 버전 한 줄 요약: MCP 연결 결정",
        "- 녹음 세션: 없음",
    ]
    (versions,) = fake.calls_to("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/versions"))
    assert versions.query == {"limit": "1"}
    assert [c.path for c in fake.calls] == [
        f"/api/meetings/{MEETING_ID}",
        ws_path(f"/meetings/{MEETING_ID}/analysis/run"),
        ws_path(f"/meetings/{MEETING_ID}/analysis/versions"),
        SESSIONS_PATH,
    ]


async def test_get_meeting_without_transcript_or_runs(client: EchoClient, fake: FakeEcho):
    bare = {**MEETING, "transcription": None, "attendees": [], "actual_duration_minutes": None}
    fake.on("GET", f"/api/meetings/{MEETING_ID}", json_response(200, bare))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/run"), json_response(200, {"run": None}))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/versions"), json_response(200, []))
    fake.on("GET", SESSIONS_PATH, NO_SESSIONS)

    text = await call_untrusted(client, "get_meeting", meeting_id=MEETING_ID)

    assert "- 참석자(0): 없음" in text
    assert "- 전사: 없음" in text
    assert "- 최신 분석 run: 없음 (분석한 적 없음)" in text
    assert "- 분석 버전: 없음" in text


async def test_get_meeting_degrades_when_versions_route_rejects_api_token(client: EchoClient, fake: FakeEcho):
    fake.on("GET", f"/api/meetings/{MEETING_ID}", json_response(200, MEETING))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/run"), json_response(200, {"run": None}))
    fake.on(
        "GET",
        ws_path(f"/meetings/{MEETING_ID}/analysis/versions"),
        json_response(403, raw={"detail": "API tokens cannot access this route"}),
    )
    fake.on("GET", SESSIONS_PATH, NO_SESSIONS)

    text = await call_untrusted(client, "get_meeting", meeting_id=MEETING_ID)

    assert text.splitlines()[-2] == "- 분석 버전: 조회 불가 (토큰에 허용되지 않은 경로)"
    assert text.splitlines()[-1] == "- 녹음 세션: 없음"


async def test_get_meeting_summarises_last_recording_session(client: EchoClient, fake: FakeEcho):
    """마지막 세션 한 줄 — 세션 수 · 마지막 종료 사유 · 시각. 사후 조사 진입점."""
    fake.on("GET", f"/api/meetings/{MEETING_ID}", json_response(200, MEETING))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/run"), json_response(200, {"run": None}))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/versions"), json_response(200, []))
    fake.on(
        "GET",
        SESSIONS_PATH,
        json_response(
            200,
            {
                "sessions": [
                    {
                        "sessionId": "s1",
                        "startedAt": "2026-09-18T09:00:00+00:00",
                        "endedAt": "2026-09-18T09:20:00+00:00",
                        "endReason": "stopped",
                        "producerUserId": "u1",
                        "events": [],
                    },
                    {
                        "sessionId": "s2",
                        "startedAt": "2026-09-18T09:30:00+00:00",
                        "endedAt": "2026-09-18T09:41:12+00:00",
                        "endReason": "producer_timeout",
                        "producerUserId": "u1",
                        "events": [{"event": "join"}, {"event": "claim"}, {"event": "timeout"}],
                    },
                ]
            },
        ),
    )

    text = await call_untrusted(client, "get_meeting", meeting_id=MEETING_ID)

    assert text.splitlines()[-1] == (
        "- 녹음 세션: 2개 · 마지막 종료 사유: producer_timeout "
        "(2026-09-18T09:30:00+00:00 → 2026-09-18T09:41:12+00:00, 이벤트 3)"
    )


async def test_get_meeting_marks_session_without_terminal_event(client: EchoClient, fake: FakeEcho):
    fake.on("GET", f"/api/meetings/{MEETING_ID}", json_response(200, MEETING))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/run"), json_response(200, {"run": None}))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/versions"), json_response(200, []))
    fake.on(
        "GET",
        SESSIONS_PATH,
        json_response(
            200,
            {
                "sessions": [
                    {
                        "sessionId": "s1",
                        "startedAt": "2026-09-18T09:00:00+00:00",
                        "endedAt": None,
                        "endReason": None,
                        "events": [{"event": "join"}],
                    }
                ]
            },
        ),
    )

    text = await call_untrusted(client, "get_meeting", meeting_id=MEETING_ID)

    assert text.splitlines()[-1] == (
        "- 녹음 세션: 1개 · 마지막 종료 사유: 종료 기록 없음(서버 중단 또는 진행 중) "
        "(2026-09-18T09:00:00+00:00 → ?, 이벤트 1)"
    )


async def test_get_meeting_degrades_when_sessions_route_is_missing_or_forbidden(client: EchoClient, fake: FakeEcho):
    fake.on("GET", f"/api/meetings/{MEETING_ID}", json_response(200, MEETING))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/run"), json_response(200, {"run": None}))
    fake.on("GET", ws_path(f"/meetings/{MEETING_ID}/analysis/versions"), json_response(200, []))
    # no fake route for sessions → FakeEcho answers 404 (older server)

    text = await call_untrusted(client, "get_meeting", meeting_id=MEETING_ID)

    assert text.splitlines()[-1] == "- 녹음 세션: 조회 불가"


async def test_get_meeting_404_is_a_tool_error(client: EchoClient, fake: FakeEcho):
    fake.on(
        "GET",
        f"/api/meetings/{MEETING_ID}",
        json_response(404, raw={"message": "Meeting not found"}),
    )

    assert "HTTP 404: Meeting not found" in await call_error(client, "get_meeting", meeting_id=MEETING_ID)


async def test_get_minutes_returns_markdown(client: EchoClient, fake: FakeEcho):
    fake.on(
        "GET",
        ws_path(f"/meetings/{MEETING_ID}/minutes.md"),
        text_response(200, "# 회의록\n- 결정 1"),
    )

    assert await call_untrusted(client, "get_minutes", meeting_id=MEETING_ID) == "# 회의록\n- 결정 1"


async def test_find_transcript_excerpts_posts_title_and_renders_timestamps(client: EchoClient, fake: FakeEcho):
    fake.on(
        "POST",
        ws_path(f"/meetings/{MEETING_ID}/transcript/find-excerpts"),
        json_response(
            200,
            [
                {
                    "start_ms": 3437000,
                    "end_ms": 3452000,
                    "excerpt": "MCP 연결도 열어놔야겠다.",
                    "reason": "직접 언급",
                },
                {"start_ms": 0, "end_ms": 1500, "excerpt": "시작", "reason": ""},
            ],
        ),
    )

    text = await call_untrusted(
        client,
        "find_transcript_excerpts",
        meeting_id=MEETING_ID,
        query="MCP 서버",
        description="읽기+쓰기",
    )

    assert text.splitlines() == [
        "- [57:17–57:32] MCP 연결도 열어놔야겠다.",
        "  - 이유: 직접 언급",
        "- [00:00–00:01] 시작",
    ]
    (post,) = fake.calls
    assert post.body == {"title": "MCP 서버", "description": "읽기+쓰기"}


async def test_find_transcript_excerpts_empty(client: EchoClient, fake: FakeEcho):
    fake.on("POST", ws_path(f"/meetings/{MEETING_ID}/transcript/find-excerpts"), json_response(200, []))

    assert (
        await call_untrusted(client, "find_transcript_excerpts", meeting_id=MEETING_ID, query="x") == "(근거 후보 없음)"
    )
    assert fake.calls[0].body == {"title": "x"}


# -- list_meetings / add_todo ---------------------------------------

SEARCH_PATH = ws_path("/meetings/search")
ASSIGNEES = [
    {"id": "u-hong", "name": "홍길동"},
    {"id": "u-cs", "name": "철수"},
    {"id": "u-cs2", "name": "철수 김"},
]
ROW = {
    "id": MEETING_ID,
    "title": "데일리 스크럼",
    "scheduledAt": "2026-10-13T01:00:00+00:00",
    "durationMinutes": 30,
    "status": "ended",
    "oneLineSummary": "MCP 빈칸 채우기 결정",
    "attendees": [{"id": "u-hong", "name": "홍길동"}, {"id": "u-cs", "name": "철수"}],
    "calendarSeriesId": "series-scrum",
}


async def test_list_meetings_sends_filters_and_renders_kst_lines(client: EchoClient, fake: FakeEcho):
    other = {
        **ROW,
        "id": "m2",
        "scheduledAt": "2026-10-14T10:00:00+09:00",
        "status": "scheduled",
        "oneLineSummary": None,
        "attendees": [],
    }
    fake.on("GET", SEARCH_PATH, json_response(200, [other, ROW]))

    text = await call_untrusted(
        client,
        "list_meetings",
        date_from="2026-10-13",
        date_to=" 2026-10-14 ",
        title="데일리 스크럼",
        series=MEETING_ID,
        attendee="철수",
        limit=500,
    )

    assert text.splitlines() == [
        "- 2026-10-14 10:00 · 데일리 스크럼 · 예정 · 참석 0 (id m2)",
        f"- 2026-10-13 10:00 · 데일리 스크럼 · 종료 · 참석 2 — MCP 빈칸 채우기 결정 (id {MEETING_ID})",
    ]
    (get,) = fake.calls
    assert get.query == {
        "limit": "100",
        "from": "2026-10-13",
        "to": "2026-10-14",
        "q": "데일리 스크럼",
        "series": MEETING_ID,
        "attendee": "철수",
    }


async def test_list_meetings_without_filters_and_empty(client: EchoClient, fake: FakeEcho):
    fake.on("GET", SEARCH_PATH, json_response(200, []))

    assert await call_untrusted(client, "list_meetings") == "(회의 없음)"
    assert fake.calls[0].query == {"limit": "20"}


async def test_list_meetings_surfaces_bad_date(client: EchoClient, fake: FakeEcho):
    fake.on(
        "GET",
        SEARCH_PATH,
        json_response(400, raw={"message": "날짜 형식이 아닙니다: 어제 (YYYY-MM-DD 또는 ISO 8601 일시)"}),
    )

    assert "HTTP 400: 날짜 형식이 아닙니다: 어제" in await call_error(client, "list_meetings", date_from="어제")


TODO_PATH = ws_path(f"/meetings/{MEETING_ID}/action-proposals")


async def test_add_todo_resolves_assignee_and_posts_due_and_evidence(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/assignees"), json_response(200, ASSIGNEES))
    fake.on(
        "POST",
        TODO_PATH,
        json_response(
            201,
            {
                "id": "todo-1",
                "title": "도구 설명 다듬기",
                "suggestedDueDate": "2026-10-20",
                "kind": "other_action",
                "state": "approved",
            },
        ),
    )

    text = await call(
        client,
        "add_todo",
        meeting_id=MEETING_ID,
        title=" 도구 설명 다듬기 ",
        assignee="철수",  # "철수 김"도 포함하지만 정확히 같은 이름이 우선
        due="2026-10-20",
        evidence="  철수가 금요일까지 정리해 줘 ",
    )

    assert unfence(text, "todo") == (
        f"To-do added: 도구 설명 다듬기 · assignee 철수 · due 2026-10-20 (id todo-1, meeting {MEETING_ID})"
    )
    (post,) = fake.calls_to("POST", ".*")
    assert post.body == {
        "title": "도구 설명 다듬기",
        "suggestedAssigneeId": "u-cs",
        "suggestedDueDate": "2026-10-20",
        "sourceExcerpt": "철수가 금요일까지 정리해 줘",
    }


async def test_add_todo_minimal_and_validation(client: EchoClient, fake: FakeEcho):
    fake.on("POST", TODO_PATH, json_response(201, {"id": "todo-2", "title": "회고 잡기"}))

    text = await call(client, "add_todo", meeting_id=MEETING_ID, title="회고 잡기")
    assert unfence(text, "todo") == f"To-do added: 회고 잡기 · no assignee · due none (id todo-2, meeting {MEETING_ID})"
    assert fake.calls[-1].body == {"title": "회고 잡기"}

    assert "date must be YYYY-MM-DD" in await call_error(
        client, "add_todo", meeting_id=MEETING_ID, title="x", due="금요일"
    )
    assert "title must not be empty" in await call_error(client, "add_todo", meeting_id=MEETING_ID, title=" ")


async def test_add_todo_unknown_assignee_is_an_error_and_posts_nothing(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/assignees"), json_response(200, ASSIGNEES))

    text = await call_error(client, "add_todo", meeting_id=MEETING_ID, title="x", assignee="민수")
    assert "No active member matches '민수'. Members:" in text and "\n홍길동, 철수, 철수 김\n" in text
    by_id = await call_error(client, "add_todo", meeting_id=MEETING_ID, title="x", assignee="u-nope")
    assert "No active member matches" in by_id
    assert not fake.calls_to("POST", ".*")


async def test_add_todo_partial_name_ambiguity_lists_candidates(client: EchoClient, fake: FakeEcho):
    fake.on(
        "GET",
        ws_path("/assignees"),
        json_response(200, [{"id": "a", "name": "철수 김"}, {"id": "b", "name": "철수 박"}]),
    )

    text = await call_error(client, "add_todo", meeting_id=MEETING_ID, title="x", assignee="철수")
    assert "matches several members" in text and "철수 김(a)" in text and "철수 박(b)" in text
