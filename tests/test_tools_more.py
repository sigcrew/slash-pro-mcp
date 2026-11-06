"""Outcomes, ticket extras (checklist / evidence / notes / relations), search, meeting
summary and upcoming, AI doc updates."""

from datetime import UTC, datetime

import httpx
import pytest

from slash_pro_mcp.client import EchoClient
from slash_pro_mcp.tools.ticket_extras import merge_checklist
from tests.helpers import FakeEcho, call, call_error, json_response, unfence, ws_path

pytestmark = pytest.mark.asyncio

PROJECTS = [{"id": "p", "key": "CORE"}]
OID = "00000000-0000-7000-8000-000000000040"
PAGE = "00000000-0000-7000-8000-000000000041"
RULES = "00000000-0000-7000-8000-000000000020"
UPD = "00000000-0000-7000-8000-000000000042"
MEETING = "00000000-0000-7000-8000-000000000043"
OUTCOME = {
    "id": OID,
    "key": "CORE-45",
    "title": "신규 사용자가 첫날 회의를 녹음한다",
    "stage": "executing",
    "revision": 3,
    "resultStatus": None,
    "ownerName": "홍길동",
    "description": "설명",
    "tickets": [{"key": "CORE-12", "status": "todo", "title": "온보딩"}],
    "evidence": [{"body": "주간 활성 +5%", "url": "https://example.com/m"}],
    "judgements": [],
}
TICKET = {"id": "t-12", "number": 12, "status": "todo", "outcomeId": None}


async def fenced(client: EchoClient, name: str, source: str, **arguments) -> str:
    return unfence(await call(client, name, **arguments), source)


# -- outcomes ------------------------------------------------------------------


async def test_list_and_get_outcome_by_key(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/projects/p/outcomes"), json_response(200, [OUTCOME]))
    assert await fenced(client, "list_outcomes", "outcome list", project_key="core") == (
        f"- CORE-45 · 실행 중 — 신규 사용자가 첫날 회의를 녹음한다 (티켓 1, id {OID})"
    )
    fake.on("GET", ws_path("/keys/CORE-45"), json_response(200, {"kind": "outcome", "id": OID}))
    fake.on("GET", ws_path(f"/outcomes/{OID}"), json_response(200, OUTCOME))
    text = await fenced(client, "get_outcome", "outcome", outcome="core-45")
    assert text.startswith("# CORE-45 신규 사용자가 첫날 회의를 녹음한다")
    assert "revision 3" in text and "  - CORE-12 · todo — 온보딩" in text and "주간 활성 +5%" in text


async def test_get_outcome_key_that_is_a_ticket_is_an_error(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/keys/CORE-12"), json_response(200, {"kind": "ticket", "id": "t-12"}))
    assert "CORE-12 is not an outcome key" in await call_error(client, "get_outcome", outcome="CORE-12")
    assert "invalid outcome key" in await call_error(client, "get_outcome", outcome="../x")


async def test_create_and_update_outcome_send_revision(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p/outcomes"), json_response(201, OUTCOME))
    await call(client, "create_outcome", project_key="CORE", title=" 변화 ", target_date="2026-12-01")
    assert fake.calls[-1].body == {"title": "변화", "stage": "draft", "targetDate": "2026-12-01"}
    assert "stage" in await call_error(client, "create_outcome", project_key="CORE", title="x", stage="closed")

    fake.on("GET", ws_path(f"/outcomes/{OID}"), json_response(200, OUTCOME))
    fake.on("PATCH", ws_path(f"/outcomes/{OID}"), json_response(200, {**OUTCOME, "revision": 4}))
    text = await fenced(client, "update_outcome", "outcome", outcome=OID, scope="온보딩만")
    assert text.endswith("(revision 4)")
    assert fake.calls[-1].body == {"expectedRevision": 3, "scope": "온보딩만"}
    assert "at least one field" in await call_error(client, "update_outcome", outcome=OID)


async def test_outcome_evidence_and_membership(client: EchoClient, fake: FakeEcho):
    fake.on("POST", ws_path(f"/outcomes/{OID}/evidence"), json_response(200, OUTCOME))
    await call(client, "add_outcome_evidence", outcome=OID, body="관찰", meeting_id=MEETING)
    assert fake.calls[-1].body == {"body": "관찰", "sourceMeetingId": MEETING}
    for bad in ("http://example.com/x", "javascript:alert(1)", "https://"):
        assert "https://" in await call_error(client, "add_outcome_evidence", outcome=OID, body="b", url=bad)

    fake.on("GET", ws_path(f"/outcomes/{OID}"), json_response(200, OUTCOME))
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, {**TICKET, "outcomeId": "o-old"}))
    fake.on(
        "PUT", ws_path(f"/outcomes/{OID}/tickets/t-12"), json_response(200, {"id": OID, "revision": 4, "move": None})
    )
    await call(client, "add_ticket_to_outcome", outcome=OID, ticket_key="core-12")
    assert fake.calls[-1].body == {"expectedRevision": 3}
    fake.on(
        "PUT",
        ws_path(f"/outcomes/{OID}/tickets/t-12"),
        json_response(200, {"id": OID, "revision": 4, "move": {"ticketId": "t-12", "fromOutcomeId": "o-old"}}),
    )
    text = await fenced(client, "add_ticket_to_outcome", "outcome", outcome=OID, ticket_key="CORE-12", move=True)
    assert fake.calls[-1].body == {"expectedRevision": 3, "expectedCurrentOutcomeId": "o-old"}
    assert text == "CORE-12 added to outcome CORE-45 (moved from o-old) (revision 4)"

    fake.on("DELETE", ws_path(f"/outcomes/{OID}/tickets/t-12"), json_response(200, {"id": OID, "revision": 5}))
    text = await fenced(client, "remove_ticket_from_outcome", "outcome", outcome=OID, ticket_key="CORE-12")
    assert text == "CORE-12 removed from outcome CORE-45 (revision 5)"
    assert fake.calls[-1].body == {"expectedRevision": 3}


# -- checklist -----------------------------------------------------------------

STORED = [
    {"id": "h1", "text": "사람 확인", "owner": "human", "done": False},
    {"id": "a1", "text": "테스트", "owner": "agent", "done": True, "result": "ok"},
    {"id": "a2", "text": "문서", "owner": "agent", "done": False},
    {"id": "u1", "text": "주인 없음", "done": False},
]


async def test_merge_keeps_people_rows_and_review_results():
    merged = merge_checklist(STORED, [{"id": "a1", "text": "테스트", "done": True}, {"text": "새 항목", "done": "yes"}])
    assert [r["id"] for r in merged][:2] == ["h1", "a1"]
    assert merged[1] == {"id": "a1", "text": "테스트", "done": True, "owner": "agent", "result": "ok"}
    assert merged[2]["done"] is False  # only a real True counts
    assert merged[2]["text"] == "새 항목" and merged[2]["owner"] == "agent"
    assert merged[3]["id"] == "u1"  # unowned row kept
    assert len(merged) == 4  # a2 dropped (agent row without a review result)


async def test_merge_refuses_touching_people_rows_or_dropping_judged_rows():
    with pytest.raises(ValueError, match="not an agent row"):
        merge_checklist(STORED, [{"id": "h1", "text": "덮어쓰기"}, {"id": "a1", "text": "x"}])
    with pytest.raises(ValueError, match="review result; only a person"):
        merge_checklist(STORED, [{"text": "a1 없음"}])
    with pytest.raises(ValueError, match="text cannot be changed"):
        merge_checklist(STORED, [{"id": "a1", "text": "테스트 통과"}])


async def test_merge_errors_explain_and_sanitise_ids():
    with pytest.raises(ValueError, match="web app"):
        merge_checklist([{"text": "id 없음", "owner": "agent"}], [{"text": "x"}])
    weird = "\x1b[31m" + "x" * 100
    with pytest.raises(ValueError) as excinfo:
        merge_checklist([{"id": weird, "text": "사람", "owner": "human"}], [{"id": weird, "text": "y"}])
    assert "\x1b" not in str(excinfo.value) and "x" * 9 not in str(excinfo.value)


async def test_replace_checklist_rebuilds_on_etag_retry(client: EchoClient, fake: FakeEcho):
    reads = iter(
        [
            json_response(200, {**TICKET, "checklist": STORED[:3]}, etag='"v1"'),
            json_response(
                200,
                {**TICKET, "checklist": [*STORED[:3], {"id": "h2", "text": "새 사람 항목", "owner": "human"}]},
                etag='"v2"',
            ),
        ]
    )
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), lambda _r: next(reads))

    def patch(request: httpx.Request) -> httpx.Response:
        if request.headers["If-Match"] == '"v1"':
            return json_response(409, raw={"message": "version_conflict"})
        return json_response(200, {**TICKET, "checklist": [{"id": "x", "text": "t", "done": False, "owner": "agent"}]})

    fake.on("PATCH", ws_path("/tickets/t-12/checklist"), patch)
    await fenced(client, "replace_checklist", "checklist", key="CORE-12", items=[{"id": "a1", "text": "테스트"}])
    sent = fake.calls_to("PATCH", ".*")[-1].body["items"]
    assert [r["id"] for r in sent] == ["h1", "a1", "h2"]  # second read's people row survives


# -- evidence, notes, relations ---------------------------------------------------


async def test_ticket_evidence_round_trip(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, TICKET))
    fake.on(
        "GET",
        ws_path("/tickets/t-12/evidence"),
        json_response(
            200,
            [
                {
                    "id": "e1",
                    "meetingTitle": "스크럼",
                    "startMs": 60000,
                    "endMs": 65000,
                    "excerpt": "원문",
                    "source": "user",
                }
            ],
        ),
    )
    assert (
        await fenced(client, "list_evidence", "evidence", key="CORE-12") == "- 스크럼 [01:00–01:05] 원문 (user, id e1)"
    )
    fake.on("POST", ws_path("/tickets/t-12/evidence"), json_response(201, {"id": "e2"}))
    await call(client, "add_evidence", key="CORE-12", meeting_id=MEETING, excerpt=" 원문 ", start_ms=60000)
    assert fake.calls[-1].body == {"meeting_id": MEETING, "excerpt": "원문", "start_ms": 60000}
    fake.on("DELETE", ws_path("/tickets/t-12/evidence/e2"), httpx.Response(204))
    assert await call(client, "delete_evidence", key="CORE-12", evidence_id="e2") == "Evidence deleted e2 (CORE-12)"
    assert "invalid evidence_id" in await call_error(client, "delete_evidence", key="CORE-12", evidence_id="../x")


async def test_evidence_delete_is_limited_to_this_session(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, TICKET))
    text = await call_error(client, "delete_evidence", key="CORE-12", evidence_id="e9")
    assert "Only evidence this agent added" in text
    assert fake.calls == []


NOTE = "00000000-0000-7000-8000-000000000050"
OWN_ONLY = "Only notes this agent wrote in this session can be edited or deleted — use the web app for others."


async def test_notes_written_in_this_session_can_be_edited(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, TICKET, etag='"v1"'))
    fake.on("POST", ws_path("/tickets/t-12/notes"), json_response(201, {"id": NOTE.upper()}))
    await call(client, "add_note", key="CORE-12", body="원래")
    fake.on("PATCH", ws_path(f"/tickets/t-12/notes/{NOTE}"), json_response(200, {"id": NOTE}))
    # hyphen-less spelling of our own id is normalised to the canonical id (and path)
    text = await call(client, "update_note", key="CORE-12", note_id=NOTE.replace("-", ""), body="고침")
    assert text == f"Note updated {NOTE} (CORE-12)"
    assert fake.calls[-1].headers["if-match"] == '"v1"' and fake.calls[-1].body == {"body": "고침"}
    fake.on("DELETE", ws_path(f"/tickets/t-12/notes/{NOTE}"), httpx.Response(204))
    await call(client, "delete_note", key="CORE-12", note_id=f"urn:uuid:{NOTE}")
    assert fake.calls[-1].method == "DELETE" and fake.calls[-1].headers["if-match"] == '"v1"'
    # deleted → no longer ours
    assert OWN_ONLY in await call_error(client, "update_note", key="CORE-12", note_id=NOTE, body="x")


async def test_recent_same_label_note_not_from_this_session_is_refused(client: EchoClient, fake: FakeEcho):
    recent = {
        "id": NOTE,
        "kind": "manual",
        "actorLabel": "Test Agent",
        "createdAt": datetime.now(UTC).isoformat(),
    }
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, TICKET))
    fake.on("GET", ws_path("/tickets/t-12/notes"), json_response(200, [recent]))
    for tool, extra in (("update_note", {"body": "x"}), ("delete_note", {})):
        assert OWN_ONLY in await call_error(client, tool, key="CORE-12", note_id=NOTE, **extra)
    assert fake.calls == []  # refused before any request
    assert "invalid note_id" in await call_error(client, "delete_note", key="CORE-12", note_id="../x")


async def test_delete_relation_finds_the_relation_id(client: EchoClient, fake: FakeEcho):
    relations = {
        "origins": [{"key": "CORE-3", "id": "t-3", "relationId": "r-1"}],
        "blocking": [{"key": "CORE-3", "id": "t-3", "relationId": "r-2"}],
        "followers": [],
        "blockedBy": [],
    }
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, {**TICKET, "relations": relations}))
    assert "pass kind" in await call_error(client, "delete_relation", key="CORE-12", target_key="core-3")
    fake.on("DELETE", ws_path("/tickets/t-12/relations/r-2"), httpx.Response(204))
    assert await call(client, "delete_relation", key="CORE-12", target_key="core-3", kind="blocks") == (
        "Relation deleted: CORE-12 blocks CORE-3"
    )
    fake.on("GET", ws_path("/tickets/by-key/CORE-9"), json_response(200, {"id": "t-9", "relations": {}}))
    assert "No relation between" in await call_error(client, "delete_relation", key="CORE-12", target_key="CORE-9")


async def test_delete_relation_looks_at_the_other_side_when_lists_are_capped(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, {**TICKET, "relations": {"followers": []}}))
    other = {"id": "t-3", "relations": {"origins": [{"key": "CORE-12", "id": "t-12", "relationId": "r-7"}]}}
    fake.on("GET", ws_path("/tickets/by-key/CORE-3"), json_response(200, other))
    fake.on("DELETE", ws_path("/tickets/t-12/relations/r-7"), httpx.Response(204))
    assert await call(client, "delete_relation", key="CORE-12", target_key="CORE-3") == (
        "Relation deleted: CORE-12 follows CORE-3"
    )


# -- search, meetings ------------------------------------------------------------


async def test_search_forwards_filters(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "GET",
        ws_path("/search"),
        json_response(
            200,
            {
                "results": [
                    {
                        "kind": "ticket",
                        "id": "t-12",
                        "title": "온보딩",
                        "ticketKey": "CORE-12",
                        "status": "todo",
                        "projectKey": "CORE",
                        "hits": [{"itemKind": "body", "snippet": "첫날 녹음"}],
                    }
                ],
                "contentReady": True,
            },
        ),
    )
    text = await fenced(
        client, "search", "search", query="녹음", kinds=["ticket", "doc"], project_key="CORE", sort="recent"
    )
    assert text.splitlines() == ["- [ticket] CORE-12 온보딩 · todo · CORE · - (id t-12)", "  - body: 첫날 녹음"]
    call_ = fake.calls_to("GET", ws_path("/search"))[0]
    assert call_.query["q"] == "녹음" and call_.query["project"] == "p" and call_.query["sort"] == "recent"
    assert "unknown kind" in await call_error(client, "search", query="x", kinds=["user"])


async def test_upcoming_and_summary(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/meetings/upcoming"), json_response(200, []))
    assert await fenced(client, "list_upcoming_meetings", "meeting list", limit=500) == "(회의 없음)"
    assert fake.calls[-1].query == {"limit": "100"}
    base = {"scheduledAt": "2026-11-05T01:00:00+00:00", "title": "스크럼", "attendees": [], "status": "scheduled"}
    rows = [
        {**base, "id": "m1", "hasStarted": False, "hasEnded": False},
        {**base, "id": "m2", "hasStarted": True, "hasEnded": False},
        {**base, "id": "m3", "hasStarted": True, "hasEnded": True},
        {**base, "id": "m4", "hasStarted": False, "hasEnded": False, "calendarCancelledAt": "2026-11-01T00:00:00Z"},
    ]
    fake.on("GET", ws_path("/meetings/upcoming"), json_response(200, rows))
    text = await fenced(client, "list_upcoming_meetings", "meeting list")
    assert text.splitlines() == [
        "- 2026-11-05 10:00 · 스크럼 · 예정 · 참석 0 (id m1)",
        "- 2026-11-05 10:00 · 스크럼 · 진행 중 · 참석 0 (id m2)",
        "- 2026-11-05 10:00 · 스크럼 · 종료 · 참석 0 (id m3)",
        "- 2026-11-05 10:00 · 스크럼 · 캘린더에서 취소됨 · 참석 0 (id m4)",
    ]
    fake.on(
        "GET",
        f"/api/meetings/{MEETING}/summary",
        json_response(200, {"executiveSummary": {"text": "요약"}, "checkPoints": ["a"], "updatedAt": "2026-11-01"}),
    )
    text = await fenced(client, "get_meeting_summary", "meeting summary", meeting_id=MEETING)
    assert '"요약"' in text and '"checkPoints"' in text


# -- doc updates -----------------------------------------------------------------

UPDATE = {
    "id": UPD,
    "pageId": PAGE,
    "state": "proposed",
    "op": "append",
    "anchorHeading": "결정",
    "newContent": [{"type": "paragraph", "content": [{"type": "text", "text": "stdio로 간다"}]}],
    "meetingTitle": "스크럼",
}


async def test_doc_updates_list_and_accept(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path(f"/wiki/pages/{PAGE}/doc-updates"), json_response(200, [UPDATE]))
    text = await fenced(client, "list_doc_updates", "doc updates", page_id=PAGE)
    assert text == f"- [proposed] append @ 결정: stdio로 간다 (id {UPD}, page {PAGE}, 회의 스크럼)"
    fake.on("POST", ws_path(f"/wiki/doc-updates/{UPD}/accept"), json_response(200, {**UPDATE, "state": "accepted"}))
    assert (await fenced(client, "accept_doc_update", "doc update", update_id=UPD, page_id=PAGE)).startswith(
        "accept: - [accepted]"
    )
    assert "exactly one of" in await call_error(client, "list_doc_updates")


async def test_doc_update_writes_are_guarded(client: EchoClient, fake: FakeEcho):
    fake.rules(RULES)
    for tool, args in (
        ("accept_doc_update", {"update_id": UPD, "page_id": RULES}),
        ("revert_doc_update", {"update_id": UPD, "page_id": RULES.upper()}),
        ("accept_all_doc_updates", {"page_id": RULES}),
    ):
        assert "only be edited by people" in await call_error(client, tool, **args)
    assert {c.method for c in fake.calls} == {"GET"}  # only the rules lookups
    # an update id that is not on the named page is refused before any write
    fake.on("GET", ws_path(f"/wiki/pages/{PAGE}/doc-updates"), json_response(200, []))
    assert "is not among the latest" in await call_error(client, "accept_doc_update", update_id=UPD, page_id=PAGE)
    assert fake.calls_to("POST", ".*") == []


async def test_reject_checks_the_page_and_refresh_needs_none(client: EchoClient, fake: FakeEcho):
    fake.rules(RULES)
    assert "only be edited by people" in await call_error(client, "reject_doc_update", update_id=UPD, page_id=RULES)
    fake.on("GET", ws_path(f"/wiki/pages/{PAGE}/doc-updates"), json_response(200, [UPDATE]))
    fake.on("POST", ws_path(f"/wiki/doc-updates/{UPD}/reject"), json_response(200, {**UPDATE, "state": "rejected"}))
    assert "[rejected]" in await call(client, "reject_doc_update", update_id=UPD, page_id=PAGE)
    assert "invalid update_id" in await call_error(client, "refresh_doc_update", update_id="../x")


async def test_accept_all_reports_skipped_rows(client: EchoClient, fake: FakeEcho):
    fake.on(
        "POST",
        ws_path(f"/wiki/pages/{PAGE}/doc-updates/accept-all"),
        json_response(200, raw={"data": [UPDATE], "skipped": [{"id": "u-9", "code": "duplicate"}]}),
    )
    text = await fenced(client, "accept_all_doc_updates", "doc updates", page_id=PAGE)
    assert text.splitlines()[0] == "Accepted 1, skipped 1"
    assert text.splitlines()[-1] == "- skipped u-9: duplicate"


async def test_page_of_error_mentions_the_200_window(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path(f"/wiki/pages/{PAGE}/doc-updates"), json_response(200, []))
    assert "latest 200 updates" in await call_error(client, "revert_doc_update", update_id=UPD, page_id=PAGE)


async def test_handoff_and_verification_note_bodies_are_not_editable(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/CORE-12"), json_response(200, TICKET, etag='"v1"'))
    fake.on("POST", ws_path("/tickets/t-12/notes"), json_response(201, {"id": NOTE}))
    await call(client, "add_handoff", key="CORE-12", goal="g")
    text = await call_error(client, "update_note", key="CORE-12", note_id=NOTE, body="x")
    assert "Handoff and verification note bodies cannot be edited" in text
    assert fake.calls_to("PATCH", ".*") == []
    fake.on("DELETE", ws_path(f"/tickets/t-12/notes/{NOTE}"), httpx.Response(204))
    await call(client, "delete_note", key="CORE-12", note_id=NOTE)  # deleting stays possible


async def test_doc_update_revert_refresh_and_by_meeting(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path(f"/meetings/{MEETING}/doc-updates"), json_response(200, [UPDATE]))
    assert "[proposed]" in await fenced(client, "list_doc_updates", "doc updates", meeting_id=MEETING)
    fake.on("GET", ws_path(f"/wiki/pages/{PAGE}/doc-updates"), json_response(200, [UPDATE]))
    for action in ("revert", "refresh"):
        fake.on("POST", ws_path(f"/wiki/doc-updates/{UPD}/{action}"), json_response(200, UPDATE))
    assert (await fenced(client, "revert_doc_update", "doc update", update_id=UPD, page_id=PAGE)).startswith("revert:")
    assert (await fenced(client, "refresh_doc_update", "doc update", update_id=UPD)).startswith("refresh:")
