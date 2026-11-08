"""Exercise real camelCase HTTP envelopes through MCP, not only renderer fixtures."""

import pytest

from slash_pro_mcp import render
from tests.helpers import call, call_untrusted, json_response, ws_path

PAGE = {
    "id": "page",
    "title": "규칙",
    "plainText": "출처: 팀장\n본문 검색어",
    "pageType": "general",
    "parentId": "root",
    "sortOrder": 0,
    "meetingId": "meeting",
    "updatedAt": "2026-09-19T00:00:00Z",
}


@pytest.mark.asyncio
async def test_wiki_body_and_body_only_search_from_camelcase(client, fake):
    fake.on("GET", ws_path("/wiki/pages/page"), json_response(200, PAGE))
    text = await call_untrusted(client, "get_wiki_page", page_id="page")
    assert "출처: 팀장\n본문 검색어" in text
    assert "유형: general · 회의: meeting · 수정: 2026-09-19" in text
    assert "L2: 본문 검색어" in await call(client, "search_wiki", page_id="page", text="검색어")


@pytest.mark.asyncio
async def test_wiki_tree_preserves_parent_and_order(client, fake):
    fake.on("GET", ws_path("/projects"), json_response(200, [{"id": "p", "key": "ABC"}]))
    fake.on(
        "GET",
        ws_path("/projects/p/wiki/tree"),
        json_response(
            200,
            [
                PAGE,
                {
                    "id": "root",
                    "title": "부모",
                    "pageType": "root",
                    "sortOrder": 1,
                    "parentId": None,
                },
            ],
        ),
    )
    text = await call_untrusted(client, "wiki_tree", project_key="ABC")
    assert text.splitlines() == ["- 부모 (root) `root`", "  - 규칙 (general) `page`"]


@pytest.mark.asyncio
async def test_meeting_and_excerpts_camelcase(client, fake):
    fake.on(
        "GET",
        "/api/meetings/m",
        json_response(
            200,
            {
                "id": "m",
                "title": "QA",
                "scheduledAt": "2026-09-19T10:00:00Z",
                "durationMinutes": 30,
                "actualDurationMinutes": 0,
                "transcription": {"durationSeconds": 0, "wordCount": 0},
            },
        ),
    )
    fake.on(
        "GET",
        ws_path("/meetings/m/analysis/run"),
        json_response(200, {"run": {"status": "done", "finishedAt": "2026-09-19T11:00:00Z"}}),
    )
    fake.on(
        "GET",
        ws_path("/meetings/m/analysis/versions"),
        json_response(200, [{"oneLineSummary": "회의 결론"}]),
    )
    text = await call_untrusted(client, "get_meeting", meeting_id="m")
    for expected in [
        "2026-09-19T10:00:00Z",
        "30분 예정 (실제 0분)",
        "0분, 0단어",
        "완료 2026-09-19T11:00:00Z",
        "회의 결론",
    ]:
        assert expected in text
    fake.on(
        "POST",
        ws_path("/meetings/m/transcript/find-excerpts"),
        json_response(
            200,
            [{"startMs": 60000, "endMs": 65000, "excerpt": "원문", "reason": "근거"}],
        ),
    )
    assert "[01:00–01:05] 원문" in await call(client, "find_transcript_excerpts", meeting_id="m", query="QA")


def test_wire_values_do_not_fall_back_on_false_or_zero():
    assert render.wire_field({"sortOrder": 0, "sort_order": 9}, "sort_order") == 0
    assert render.wire_field({"isBlocked": False, "is_blocked": True}, "is_blocked") is False
    assert "⛔" in render.ticket_line("ABC", {"number": 1, "isBlocked": True})
