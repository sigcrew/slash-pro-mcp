"""Fake Echo REST API (httpx MockTransport) and tool-call helpers for the MCP tests."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

from slash_pro_mcp.client import EchoClient
from slash_pro_mcp.server import build_server

API = "https://echo.test/api"
API_ROOT_PATH = "/api"
WS = "00000000-0000-7000-8000-000000000010"
TOKEN = "et_test_token_value"
AGENT = "Test Agent"


Handler = Callable[[httpx.Request], httpx.Response]

# A subset of the server's API-token scope table (see fixtures/README.md): "METHOD /path/{param}" ->
# scope (null = no token access). Every request the fake serves must match a route with a scope.
ROUTE_SCOPES: dict[str, str | None] = json.loads(
    (Path(__file__).parent / "fixtures" / "route_scopes.json").read_text(encoding="utf-8")
)


def _route_pattern(path: str) -> re.Pattern[str]:
    parts = ("[^/]+" if part.startswith("{") else re.escape(part) for part in path.split("/"))
    return re.compile("^" + "/".join(parts) + "$")


_ROUTE_PATTERNS = [(route.split(" ")[0], _route_pattern(route.split(" ")[1]), route) for route in ROUTE_SCOPES]


def route_template(method: str, path: str) -> str | None:
    """The server route a wire path (without the API root) maps to; literal segments win."""
    matches = [route for m, pattern, route in _ROUTE_PATTERNS if m == method and pattern.match(path)]
    return min(matches, key=lambda r: r.count("{"), default=None)


def json_response(status: int, data: Any = None, *, etag: str | None = None, raw: dict | None = None) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if etag:
        headers["ETag"] = etag
    body = raw if raw is not None else {"data": data}
    return httpx.Response(status, json=body, headers=headers)


def text_response(status: int, text: str, *, etag: str | None = None) -> httpx.Response:
    headers = {"Content-Type": "text/markdown; charset=utf-8"}
    if etag:
        headers["ETag"] = etag
    return httpx.Response(status, text=text, headers=headers)


@dataclass
class Call:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: Any

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.method} {self.path} {self.query} body={self.body}"


@dataclass
class FakeEcho:
    """Route table keyed by (method, path-regex). Records every call."""

    routes: list[tuple[str, re.Pattern[str], Handler]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    # Requests to routes that do not exist for API tokens (checked after each test).
    unscoped: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rules(None)

    def rules(self, page_id: str | None, **extra: Any) -> None:
        """`GET /wiki/agent-instructions`: no rules page by default, or one with `page_id`."""
        data = {"pageId": page_id, "title": "rules", "body": "rules body", **extra} if page_id else None
        self.on("GET", ws_path("/wiki/agent-instructions"), json_response(200, data))

    def on(self, method: str, path_pattern: str, handler: Handler | httpx.Response) -> None:
        """Register a route; registering the same (method, pattern) again replaces it
        (a test can re-point a route mid-way, e.g. "now the list is empty")."""
        response = handler
        if isinstance(response, httpx.Response):

            def handler(_request: httpx.Request, _r: httpx.Response = response) -> httpx.Response:
                return _r

        pattern = re.compile(f"^{path_pattern}$")
        self.routes = [r for r in self.routes if not (r[0] == method.upper() and r[1].pattern == pattern.pattern)]
        self.routes.append((method.upper(), pattern, handler))

    def handle(self, request: httpx.Request) -> httpx.Response:
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode()
        wire = request.url.path.removeprefix(API_ROOT_PATH)
        template = route_template(request.method, wire)
        if template is None or ROUTE_SCOPES[template] is None:
            self.unscoped.append(f"{request.method} {wire}")
        self.calls.append(
            Call(
                method=request.method,
                path=request.url.path,
                query=dict(request.url.params),
                headers={k: v for k, v in request.headers.items()},
                body=body,
            )
        )
        for method, pattern, handler in self.routes:
            if method == request.method and pattern.match(request.url.path):
                return handler(request)
        return httpx.Response(404, json={"message": f"no fake route for {request.method} {request.url.path}"})

    def calls_to(self, method: str, path_pattern: str) -> list[Call]:
        pattern = re.compile(f"^{path_pattern}$")
        return [c for c in self.calls if c.method == method.upper() and pattern.match(c.path)]


def ws_path(suffix: str) -> str:
    """Path under the workspace, as it appears on the wire."""
    return f"/api/workspaces/{WS}{suffix}"


async def call_raw(client: EchoClient, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    """Run one tool over an in-memory MCP session; returns (isError, text)."""
    server = build_server(client)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(name, arguments)
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    return bool(result.isError), text


async def call(client: EchoClient, name: str, **arguments: Any) -> str:
    """Call a tool and return its text; fails the test on isError."""
    is_error, text = await call_raw(client, name, arguments)
    assert not is_error, f"tool {name} failed: {text}"
    return text


async def call_error(client: EchoClient, name: str, **arguments: Any) -> str:
    """Call a tool expecting isError; returns the error text."""
    is_error, text = await call_raw(client, name, arguments)
    assert is_error, f"tool {name} unexpectedly succeeded: {text}"
    return text


UNTRUSTED_SOURCES = {
    "get_ticket": "ticket",
    "list_tickets": "ticket list",
    "get_meeting": "meeting",
    "list_meetings": "meeting list",
    "get_minutes": "meeting minutes",
    "find_transcript_excerpts": "meeting transcript",
    "get_prd": "prd",
    "list_prds": "prd list",
    "get_wiki_page": "wiki page",
    "wiki_tree": "wiki tree",
    "search_wiki": "wiki search",
    "list_handoffs": "handoffs",
    "list_questions": "questions",
    "ask_questions": "questions",
    "readiness_refresh": "questions",
    "add_agenda_item": "agenda",
    "start_verification": "verification",
    "add_verification": "verification",
    "write_wiki_page": "wiki write",
    "assign_ticket_prd": "prd write",
    "relate_ticket": "ticket write",
}
_FENCE = re.compile(r'<(untrusted-workspace-content-[0-9a-f]{8}) source="([^"]*)">\n(.*)\n</\1>', re.DOTALL)


def unfence(text: str, source: str) -> str:
    """Check `text` is exactly one untrusted fence labelled `source`; return its content."""
    match = _FENCE.fullmatch(text)
    assert match and match[2] == source, text
    return match[3]


async def call_untrusted(client: EchoClient, name: str, **arguments: Any) -> str:
    """Call a read tool whose output is fenced as untrusted workspace content; check the
    fence and return the content inside it."""
    return unfence(await call(client, name, **arguments), UNTRUSTED_SOURCES[name])
