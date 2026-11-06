"""add_agenda_item: next occurrence by series title, or a calendar instance id."""

import httpx
import pytest

from slash_pro_mcp.client import SlashClient
from tests.helpers import FakeSlash, call_error, call_untrusted, json_response, ws_path

pytestmark = pytest.mark.asyncio

NEXT = {
    "eventId": "scrum_20261015T010000Z",
    "title": "데일리 스크럼",
    "scheduledAt": "2026-10-15T10:00:00+09:00",
    "recurringEventId": "scrum",
    "meetingId": None,
}
ADDED = {
    "eventId": "scrum_20261015T010000Z",
    "meetingId": "00000000-0000-7000-8000-000000000009",
    "meetingCreated": True,
    "questionId": "00000000-0000-7000-8000-00000000000a",
    "agendaItemId": "00000000-0000-7000-8000-00000000000b",
    "targetType": "product",
    "targetId": "00000000-0000-7000-8000-00000000000c",
    "duplicate": False,
}


async def test_series_title_finds_next_occurrence_then_posts(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/meeting-series/next-occurrence"), json_response(200, NEXT))
    fake.on("POST", ws_path("/events/[^/]+/agenda-questions"), json_response(201, ADDED))

    text = await call_untrusted(
        client,
        "add_agenda_item",
        series_title="데일리 스크럼",
        text="철수님 요청",
        project_key="ABC",
        evidence="10/12 회의",
    )

    assert fake.calls_to("GET", ws_path("/meeting-series/next-occurrence"))[0].query == {"seriesTitle": "데일리 스크럼"}
    post = fake.calls_to("POST", ws_path("/events/[^/]+/agenda-questions"))[0]
    assert post.path == ws_path("/events/scrum_20261015T010000Z/agenda-questions")
    assert post.body == {"text": "철수님 요청", "evidence": "10/12 회의", "projectKey": "ABC"}
    assert text.startswith("Agenda line added: 데일리 스크럼 2026-10-15T10:00:00+09:00 — 철수님 요청")
    assert "scheduled meeting row created" in text


async def test_event_id_posts_directly_and_reports_duplicate(client: SlashClient, fake: FakeSlash):
    fake.on(
        "POST",
        ws_path("/events/[^/]+/agenda-questions"),
        json_response(200, {**ADDED, "meetingCreated": False, "duplicate": True}),
    )
    text = await call_untrusted(client, "add_agenda_item", event_id="evt_1", text="같은 안건", ticket_key="ABC-3")
    assert fake.calls_to("GET", ws_path("/meeting-series/next-occurrence")) == []
    post = fake.calls_to("POST", ws_path("/events/evt_1/agenda-questions"))[0]
    assert post.body == {"text": "같은 안건", "ticketKey": "ABC-3"}
    assert text.startswith("Already on the agenda: event evt_1 — 같은 안건")


async def test_needs_exactly_one_of_title_or_event_and_surfaces_server_errors(client: SlashClient, fake: FakeSlash):
    assert "exactly one of" in await call_error(client, "add_agenda_item", text="x")
    assert "exactly one of" in await call_error(client, "add_agenda_item", text="x", event_id="e", series_title="t")
    fake.on(
        "POST",
        ws_path("/events/[^/]+/agenda-questions"),
        httpx.Response(
            409,
            json={"message": "이 회차 아젠다에 붙일 대상이 없습니다", "code": "no_agenda_target"},
        ),
    )
    err = await call_error(client, "add_agenda_item", event_id="e", text="x")
    assert "붙일 대상이 없습니다" in err and "code: no_agenda_target" in err
