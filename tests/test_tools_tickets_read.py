"""list_tickets / get_ticket through the MCP client session."""

import pytest

from slash_pro_mcp.client import EchoClient
from tests.helpers import (
    FakeEcho,
    call_error,
    call_untrusted,
    json_response,
    text_response,
    ws_path,
)

pytestmark = pytest.mark.asyncio

PROJECTS = [{"id": "p-echo", "key": "ECHO"}, {"id": "p-core", "key": "CORE"}]


def ticket(number: int, status: str, title: str, **extra) -> dict:
    return {
        "id": f"t-{number}",
        "number": number,
        "status": status,
        "priority": "medium",
        "title": title,
        "is_blocked": False,
        **extra,
    }


async def test_list_tickets_without_project_uses_workspace_calls_per_status(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    rows = [
        ticket(1, "todo", "첫 번째", projectId="p-echo"),
        ticket(2, "done", "끝난 것", projectId="p-echo"),
        ticket(3, "in_progress", "진행 중", projectId="p-echo", is_blocked=True),
        ticket(7, "todo", "코어", projectId="p-core"),
    ]

    def by_status(request):
        wanted = request.url.params.get("status")
        return json_response(200, [r for r in rows if wanted in (None, r["status"])])

    fake.on("GET", ws_path("/tickets"), by_status)

    text = await call_untrusted(client, "list_tickets")

    # no createdAt in the reply → merged server order (todo call, then in_progress call)
    assert text.splitlines() == [
        "- ECHO-1 · todo · medium — 첫 번째",
        "- CORE-7 · todo · medium — 코어",
        "- ECHO-3 · in_progress · medium ⛔ — 진행 중",
    ]
    assert fake.calls_to("GET", ws_path("/projects/.*/tickets")) == []
    assert [c.query for c in fake.calls_to("GET", ws_path("/tickets"))] == [
        {"status": "todo"},
        {"status": "in_progress"},
    ]

    fake.calls.clear()
    assert len((await call_untrusted(client, "list_tickets", status="all")).splitlines()) == 4
    assert [c.query for c in fake.calls_to("GET", ws_path("/tickets"))] == [{}]


async def test_list_tickets_orders_by_created_at_across_projects(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "GET",
        ws_path("/tickets"),
        json_response(
            200,
            [  # server order: per-project number desc
                ticket(9, "todo", "E9", projectId="p-echo", createdAt="2026-10-01T00:00:00Z"),
                ticket(8, "todo", "E8", projectId="p-echo", createdAt="2026-10-03T00:00:00Z"),
                ticket(5, "todo", "C5", projectId="p-core", createdAt="2026-10-02T00:00:00Z"),
                ticket(4, "todo", "C4", projectId="p-core", createdAt="2026-10-03T00:00:00Z"),
            ],
        ),
    )
    text = await call_untrusted(client, "list_tickets")
    # E8 and C4 tie on createdAt → id desc ("t-8" > "t-4")
    assert [line.split(" · ")[0] for line in text.splitlines()] == ["- ECHO-8", "- CORE-4", "- CORE-5", "- ECHO-9"]


async def test_list_tickets_sorts_mixed_offsets_by_time_and_labels_unknown_projects(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    rows = [
        ticket(1, "todo", "older", projectId="p-echo", createdAt="2026-10-01T09:00:00+09:00"),  # 00:00Z
        ticket(2, "todo", "newer", projectId="p-gone-123456", createdAt="2026-10-01T00:30:00Z"),
    ]
    fake.on("GET", ws_path("/tickets"), json_response(200, rows))
    text = await call_untrusted(client, "list_tickets", status="todo")
    assert text.splitlines() == ["- ?P-GONE-1-2 · todo · medium — newer", "- ECHO-1 · todo · medium — older"]


async def test_list_tickets_limit_and_footer(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "GET",
        ws_path("/tickets"),
        json_response(200, [ticket(n, "todo", f"T{n}", projectId="p-core") for n in range(1, 8)]),
    )
    lines = (await call_untrusted(client, "list_tickets", limit=3)).splitlines()
    assert lines[:3] == [
        "- CORE-1 · todo · medium — T1",
        "- CORE-2 · todo · medium — T2",
        "- CORE-3 · todo · medium — T3",
    ]
    assert lines[3] == "… 4 more — narrow with status/assignee/prd or raise limit (max 200)"
    assert len((await call_untrusted(client, "list_tickets", limit=999)).splitlines()) == 7


async def test_list_tickets_assignee_and_blocked_filters(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/assignees"), json_response(200, [{"id": "u-hong", "name": "홍길동"}]))
    fake.on("GET", ws_path("/tickets"), json_response(200, []))
    await call_untrusted(client, "list_tickets", assignee="홍길동", blocked=True, status="todo")
    assert fake.calls_to("GET", ws_path("/tickets"))[-1].query == {
        "status": "todo",
        "assignee_id": "u-hong",
        "is_blocked": "true",
    }
    text = await call_error(client, "list_tickets", assignee="me")
    assert "pass your name" in text


async def test_list_tickets_single_status_is_pushed_to_the_api(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/projects/p-echo/tickets"), json_response(200, [ticket(2, "done", "끝")]))

    text = await call_untrusted(client, "list_tickets", project_key="echo", status="done")

    assert text == "- ECHO-2 · done · medium — 끝"
    (tickets_call,) = fake.calls_to("GET", ws_path("/projects/.*/tickets"))
    assert tickets_call.path == ws_path("/projects/p-echo/tickets")
    assert tickets_call.query == {"status": "done"}


async def test_list_tickets_can_filter_canceled(client: EchoClient, fake: FakeEcho):
    """canceled는 쓰기 도구에선 막히지만 목록 필터로는 쓸 수 있다."""
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "GET",
        ws_path("/projects/p-echo/tickets"),
        json_response(200, [ticket(3, "canceled", "안 함")]),
    )

    text = await call_untrusted(client, "list_tickets", project_key="echo", status="canceled")

    assert text == "- ECHO-3 · canceled · medium — 안 함"
    (tickets_call,) = fake.calls_to("GET", ws_path("/projects/.*/tickets"))
    assert tickets_call.query == {"status": "canceled"}


async def test_list_tickets_all_and_empty(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS[:1]))
    fake.on("GET", ws_path("/tickets"), json_response(200, []))

    assert await call_untrusted(client, "list_tickets", status="all") == "(티켓 없음)"


async def test_list_tickets_rejects_unknown_project_and_status(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))

    assert "Project not found: NOPE" in await call_error(client, "list_tickets", project_key="NOPE")
    assert "unknown status: bogus" in await call_error(client, "list_tickets", status="bogus")


async def test_get_ticket_returns_server_markdown_verbatim(client: EchoClient, fake: FakeEcho):
    markdown = "# ECHO-23 에코 MCP 서버\n\n- 상태: in_progress\n"
    fake.on("GET", ws_path("/tickets/by-key/ECHO-23/context"), text_response(200, markdown, etag='"v"'))

    text = await call_untrusted(client, "get_ticket", key="echo-23")

    assert text == markdown
    (context_call,) = fake.calls
    assert context_call.query == {"format": "markdown"}
    assert context_call.headers["authorization"].startswith("Bearer ")


async def test_get_ticket_error_passes_message_and_errors(client: EchoClient, fake: FakeEcho):
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ECHO-999/context"),
        json_response(
            404,
            raw={"message": "Ticket not found", "errors": [{"field": "key", "reason": "unknown"}]},
        ),
    )

    text = await call_error(client, "get_ticket", key="ECHO-999")

    assert "HTTP 404: Ticket not found" in text
    assert "key: unknown" in text


async def test_list_tickets_shows_parent_and_sub_progress(client: EchoClient, fake: FakeEcho):
    """a sub reads `↳ PARENT`, a parent `서브 done/total` (canceled not counted)."""
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS[:1]))
    fake.on(
        "GET",
        ws_path("/projects/p-echo/tickets"),
        json_response(
            200,
            [
                ticket(
                    9,
                    "todo",
                    "결제 개편",
                    subTickets=[
                        {"key": "ECHO-3", "status": "done"},
                        {"key": "ECHO-4", "status": "todo"},
                        {"key": "ECHO-5", "status": "canceled"},
                    ],
                ),
                ticket(3, "todo", "토스 연동", parent={"id": "t-9", "key": "ECHO-9", "title": "결제 개편"}),
            ],
        ),
    )
    text = await call_untrusted(client, "list_tickets", project_key="echo", status="todo")
    assert "- ECHO-9 · todo · medium — 결제 개편 · 서브 1/2" in text
    assert "- ECHO-3 · todo · medium — 토스 연동 ↳ ECHO-9" in text
