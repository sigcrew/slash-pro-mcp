"""EchoClient: headers, error translation, ETag / If-Match with one 409 retry."""

import json

import httpx
import pytest

from slash_pro_mcp.client import (
    EchoApiError,
    EchoClient,
    NotFound,
    VersionConflict,
    parse_error,
)
from slash_pro_mcp.config import EchoConfig
from tests.helpers import (
    API,
    TOKEN,
    WS,
    FakeEcho,
    json_response,
    text_response,
    ws_path,
)

pytestmark = pytest.mark.asyncio

TICKET = {"id": "t-1", "number": 3, "status": "todo", "updated_at": "2026-09-18T00:00:00Z"}


async def test_every_request_carries_bearer_and_no_requested_by_by_default(client: EchoClient, fake: FakeEcho):
    """The requester is derived server-side from the token owner —
    `X-Requested-By` goes out only when a requester override is set explicitly."""
    fake.on("GET", ws_path("/projects"), json_response(200, []))

    await client.list_projects()

    (call,) = fake.calls
    assert call.headers["authorization"] == f"Bearer {TOKEN}"
    assert "x-requested-by" not in call.headers


async def test_request_unwraps_data_envelope_and_keeps_etag(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/ECHO-3"), json_response(200, TICKET, etag='"v1"'))

    response = await client.get_ticket_by_key("echo-3")

    assert response.data == TICKET
    assert response.etag == '"v1"'
    assert fake.calls[0].path.endswith("/tickets/by-key/ECHO-3")


async def test_markdown_response_is_returned_as_text(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/ECHO-3/context"), text_response(200, "# ECHO-3 제목"))

    text = await client.get_text("/tickets/by-key/ECHO-3/context", params={"format": "markdown"})

    assert text == "# ECHO-3 제목"
    assert fake.calls[0].query == {"format": "markdown"}


async def test_workspace_false_hits_api_root(client: EchoClient, fake: FakeEcho):
    fake.on("GET", "/api/meetings/m-1", json_response(200, {"id": "m-1"}))

    assert await client.get_data("/meetings/m-1", workspace=False) == {"id": "m-1"}


async def test_parse_error_carries_message_and_errors_verbatim():
    error = parse_error(
        422,
        '{"message": "Unprocessable Entity", "errors": [{"loc": ["body", "title"], "msg": "too long"}]}',
    )
    assert str(error) == "HTTP 422: Unprocessable Entity\nbody.title: too long"

    detail = parse_error(403, '{"detail": "scope_required: tickets:write"}')
    assert str(detail) == "HTTP 403: scope_required: tickets:write"

    expired = parse_error(401, '{"detail": "Invalid token"}')
    assert "expired" in str(expired)

    raw = parse_error(502, "<html>bad gateway</html>")
    assert str(raw) == "HTTP 502: <html>bad gateway</html>"
    coded = parse_error(
        409, json.dumps({"message": "many", "code": "ambiguous_series", "candidates": [f"t{i}" for i in range(12)]})
    )
    assert str(coded) == "HTTP 409: many\ncode: ambiguous_series\ncandidates: " + ", ".join(f"t{i}" for i in range(10))
    assert coded.code == "ambiguous_series"
    assert "code:" not in str(parse_error(500, json.dumps({"message": "x", "code": 500})))

    conflict = parse_error(409, '{"message": "version_conflict", "current": {}}', '"v2"')
    assert isinstance(conflict, VersionConflict)
    assert conflict.etag == '"v2"'


async def test_http_error_raises_echo_api_error(client: EchoClient, fake: FakeEcho):
    fake.on(
        "GET",
        ws_path("/tickets/by-key/NOPE-1"),
        json_response(404, raw={"message": "Ticket not found"}),
    )

    with pytest.raises(EchoApiError) as excinfo:
        await client.get_ticket_by_key("NOPE-1")
    assert excinfo.value.status == 404
    assert str(excinfo.value) == "HTTP 404: Ticket not found"


async def test_resolve_project_is_case_insensitive_and_404s(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, [{"id": "p-1", "key": "ECHO"}]))

    assert (await client.resolve_project("echo"))["id"] == "p-1"
    with pytest.raises(NotFound, match="CORE"):
        await client.resolve_project("CORE")


async def test_ticket_write_sends_if_match_from_fresh_get(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/ECHO-3"), json_response(200, TICKET, etag='"v1"'))
    fake.on(
        "PATCH",
        ws_path("/tickets/t-1"),
        json_response(200, {**TICKET, "status": "review"}, etag='"v2"'),
    )

    response = await client.ticket_write("echo-3", "PATCH", "", {"status": "review"})

    assert response.data["status"] == "review"
    patch = fake.calls_to("PATCH", ws_path("/tickets/t-1"))[0]
    assert patch.headers["if-match"] == '"v1"'
    assert patch.body == {"status": "review"}
    assert [c.method for c in fake.calls] == ["GET", "PATCH"]


async def test_ticket_write_retries_once_after_409(client: EchoClient, fake: FakeEcho):
    etags = iter(['"v1"', '"v2"'])
    fake.on(
        "GET",
        ws_path("/tickets/by-key/ECHO-3"),
        lambda _r: json_response(200, TICKET, etag=next(etags)),
    )

    def patch(request: httpx.Request) -> httpx.Response:
        if request.headers["If-Match"] == '"v1"':
            return json_response(409, raw={"message": "version_conflict"}, etag='"v2"')
        return json_response(200, {**TICKET, "status": "done"}, etag='"v3"')

    fake.on("PATCH", ws_path("/tickets/t-1"), patch)

    response = await client.ticket_write("ECHO-3", "PATCH", "", {"status": "done"})

    assert response.data["status"] == "done"
    assert [c.method for c in fake.calls] == ["GET", "PATCH", "GET", "PATCH"]
    assert [c.headers["if-match"] for c in fake.calls_to("PATCH", ".*")] == ['"v1"', '"v2"']


async def test_ticket_write_gives_up_after_second_409(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/tickets/by-key/ECHO-3"), json_response(200, TICKET, etag='"v1"'))
    fake.on(
        "POST",
        ws_path("/tickets/t-1/notes"),
        json_response(409, raw={"message": "version_conflict"}, etag='"v9"'),
    )

    with pytest.raises(VersionConflict) as excinfo:
        await client.ticket_write("ECHO-3", "POST", "/notes", {"body": "x"})

    assert "changed elsewhere twice" in str(excinfo.value)
    assert "version_conflict" in str(excinfo.value)
    assert len(fake.calls_to("POST", ".*")) == 2


async def test_explicit_non_ascii_requested_by_is_sent_as_utf8(fake: FakeEcho):
    """A non-ASCII requester override is sent as UTF-8 bytes;
    the header goes out only when configured."""
    config = EchoConfig(api=API, workspace_id=WS, token=TOKEN, requested_by="팀장")
    echo = EchoClient(config, transport=httpx.MockTransport(fake.handle))
    fake.on("GET", ws_path("/projects"), json_response(200, []))
    try:
        await echo.list_projects()
    finally:
        await echo.aclose()
    (call,) = fake.calls
    assert call.headers["x-requested-by"] == "팀장"


async def test_explicit_agent_model_is_sent_on_every_request(fake: FakeEcho):
    config = EchoConfig(api=API, workspace_id=WS, token=TOKEN, model_id="gpt-6-astra")
    echo = EchoClient(config, transport=httpx.MockTransport(fake.handle))
    fake.on("GET", ws_path("/projects"), json_response(200, []))
    try:
        await echo.list_projects()
    finally:
        await echo.aclose()
    assert fake.calls[0].headers["x-agent-model"] == "gpt-6-astra"


async def test_business_conflict_does_not_refresh_etag_and_retry(client, fake):
    fake.on("GET", ws_path("/tickets/by-key/ECHO-23"), json_response(200, {"id": "t"}, etag='"v1"'))
    fake.on("PATCH", ws_path("/tickets/t"), json_response(409, raw={"message": "answer_revalidation_required"}))
    with pytest.raises(EchoApiError) as exc:
        await client.ticket_write("ECHO-23", "PATCH", "", {"status": "review"})
    assert not isinstance(exc.value, VersionConflict)
    assert "answer_revalidation_required" in str(exc.value)
    assert len(fake.calls) == 2
