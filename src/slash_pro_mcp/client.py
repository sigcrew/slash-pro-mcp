"""Thin async HTTP client for the Slash Pro REST API.

Every call carries `Authorization: Bearer <token>`; `X-Requested-By` only when
an explicit requester is configured (the server derives it from the token owner
otherwise), and `X-Agent-Model` when an agent model is configured.
Ticket writes go through `ticket_write`, which fetches the ticket by key,
sends its `ETag` as `If-Match`, and retries once on a 409 version conflict.
Non-2xx replies become `EchoApiError` whose text carries the server's `message`
and `errors[]` verbatim — never request headers.

Safety: TLS verification is always on, redirects are never followed, proxy/CA
environment variables are ignored (`trust_env=False`), each request has connect/read
timeouts plus an overall `REQUEST_DEADLINE_SECONDS`, response bodies are capped at
`MAX_RESPONSE_BYTES` (no compression), and every path segment goes through `path_segment`.
"""

from __future__ import annotations

import asyncio
import json as json_lib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID

import httpx

from . import render
from .config import EchoConfig

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
REQUEST_DEADLINE_SECONDS = 60
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
RULES_PATH = "/wiki/agent-instructions"
_RAW_BODY_PREVIEW = 300
MAX_ERROR_MESSAGE = 2000
MAX_SHOWN_ID = 40
MAX_ERROR_LINES = 20
MAX_ERROR_LINE = 500
MAX_CANDIDATES = 10
# Ids, ticket keys and calendar event ids: no `/`, `?`, `#`, `%` or leading dot.
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,255}")


def path_segment(value: object, name: str | None = None) -> str:
    """Validate one URL path segment and percent-encode it, so a model cannot inject path traversal
    (`../x`) or a query string (`a?b=c`). `name` names a tool argument; without it the value came
    from the server and is never echoed."""
    text = str(value).strip()
    if not _SEGMENT.fullmatch(text):
        if name is None:
            raise ValueError("the server returned an invalid id")
        raise ValueError(f"invalid {name}: {text[:80]!r}")
    return quote(text, safe="")


def shown_id(value: object) -> str:
    """A server-returned id for a result line: the canonical UUID, or the id if it is a short plain
    path segment (≤40 chars, no spaces or markup); anything else is fenced."""
    text = str(value).strip()
    try:
        return str(UUID(text))
    except ValueError:
        pass
    if len(text) <= MAX_SHOWN_ID and _SEGMENT.fullmatch(text):
        return text
    return render.untrusted("server id", text[:100])


def canonical_id(value: object) -> str:
    """One spelling per id: a UUID in any accepted form becomes its canonical string, anything
    else is stripped and lowercased."""
    text = str(value).strip()
    try:
        return str(UUID(text))
    except ValueError:
        return text.lower()


@dataclass(eq=False)
class EchoResponse:
    status: int
    data: Any
    text: str
    etag: str | None
    # Server-side warnings from `X-Echo-Warnings` (percent-encoded JSON array); the request succeeded.
    warnings: list[str] = field(default_factory=list)


def _header_warnings(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json_lib.loads(unquote(raw))
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


@dataclass(eq=False)
class EchoApiError(Exception):
    """A non-2xx reply; `str()` is the message the tool returns to the agent."""

    status: int
    message: str
    errors: list[str] = field(default_factory=list)
    # The server's string error code (e.g. `not_empty`) and candidate values, when it sends them.
    code: str | None = None
    candidates: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"HTTP {self.status}: {self.message}"]
        if self.code:
            lines.append(f"code: {self.code}")
        lines.extend(self.errors)
        if self.candidates:
            lines.append("candidates: " + ", ".join(self.candidates[:MAX_CANDIDATES]))
        if self.status == 401:
            lines.append("The token may have expired (90 days) or been revoked; issue a new one in the web app.")
        return "\n".join(lines)


@dataclass(eq=False)
class VersionConflict(EchoApiError):
    """409 on a ticket write: the ticket changed elsewhere since we read it."""

    etag: str | None = None


class NotFound(EchoApiError):
    """A lookup this server does client-side (project key, wiki page) found nothing."""

    def __init__(self, message: str) -> None:
        super().__init__(status=404, message=message)


def _error_lines(payload: Any) -> list[str]:
    errors = payload.get("errors")
    if errors is None and isinstance(payload.get("detail"), list):
        errors = payload["detail"]
    if not isinstance(errors, list):
        return []
    lines: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            lines.append(str(error))
            continue
        loc = error.get("loc") or error.get("field") or "request"
        field_name = ".".join(map(str, loc)) if isinstance(loc, list | tuple) else str(loc)
        text = error.get("msg") or error.get("message") or error.get("reason") or "invalid"
        lines.append(f"{field_name}: {text}")
    return lines


def parse_error(status: int, text: str, etag: str | None = None) -> EchoApiError:
    """Build the error an agent sees from a failed response body."""
    try:
        payload = json_lib.loads(text) if text else {}
    except ValueError:
        payload = None
    code: str | None = None
    candidates: list[str] = []
    if not isinstance(payload, dict):
        message = (text or "Request failed")[:_RAW_BODY_PREVIEW]
        errors: list[str] = []
    else:
        if isinstance(payload.get("code"), str):
            code = payload["code"][:100]
        if isinstance(payload.get("candidates"), list):
            candidates = [str(c)[:200] for c in payload["candidates"][:MAX_CANDIDATES]]
        message = payload.get("message")
        if not message:
            detail = payload.get("detail")
            message = detail if isinstance(detail, str) else "Request failed"
        errors = [line[:MAX_ERROR_LINE] for line in _error_lines(payload)[:MAX_ERROR_LINES]]
        message = str(message)[:MAX_ERROR_MESSAGE]
    if status == 409 and message == "version_conflict":
        return VersionConflict(status=status, message=message, errors=errors, etag=etag)
    return EchoApiError(status=status, message=message, errors=errors, code=code, candidates=candidates)


async def _read_capped(response: httpx.Response) -> bytes:
    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        # We ask for `identity`; a compressed body could inflate past the cap.
        raise EchoApiError(status=response.status_code, message=f"compressed response refused ({encoding[:20]})")
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise EchoApiError(status=response.status_code, message="response too large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise EchoApiError(status=response.status_code, message="response too large")
        chunks.append(chunk)
    return b"".join(chunks)


class EchoClient:
    def __init__(
        self,
        config: EchoConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.config = config
        headers: dict[str, str | bytes] = {
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json, text/markdown;q=0.9, */*;q=0.1",
            # No compression: the size cap then bounds what the server can make us inflate.
            "Accept-Encoding": "identity",
        }
        if config.requested_by:
            # httpx header values must be latin-1; non-ASCII (e.g. 한글) is sent as UTF-8
            # bytes, matching what curl/the shell skill send.
            headers["X-Requested-By"] = config.requested_by.encode("utf-8")
        if config.model_id:
            headers["X-Agent-Model"] = config.model_id
        self._http = httpx.AsyncClient(
            base_url=config.api,
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        # Notes (id -> kind) and evidence this process created (`canonical_id`): the only ones it
        # may edit or delete.
        self.own_notes: dict[str, str] = {}
        self.own_evidence_ids: set[str] = set()

    def remember_note(self, data: object, kind: str) -> None:
        if isinstance(data, dict) and data.get("id"):
            self.own_notes[canonical_id(data["id"])] = kind

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- low level ---------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        workspace: bool = True,
    ) -> EchoResponse:
        """Send one request. `path` is relative to `/workspaces/{ws}` unless
        `workspace=False` (then relative to the API root)."""
        url = f"/workspaces/{self.config.workspace_id}{path}" if workspace else path
        async with asyncio.timeout(REQUEST_DEADLINE_SECONDS):
            async with self._http.stream(method, url, json=json, params=params, headers=headers or {}) as response:
                content = await _read_capped(response)
        etag = response.headers.get("ETag")
        # Defense in depth: never hand our own token back to the model, even if a server echoes it.
        text = content.decode(response.encoding or "utf-8", errors="replace").replace(self.config.token, "[REDACTED]")
        if not 200 <= response.status_code < 300:
            # 3xx included: redirects are not followed, so a moved API is an error.
            raise parse_error(response.status_code, text, etag)
        data: Any = None
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type and text:
            payload = json_lib.loads(text)
            data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        return EchoResponse(
            status=response.status_code,
            data=data,
            text=text,
            etag=etag,
            warnings=[
                w.replace(self.config.token, "[REDACTED]")
                for w in _header_warnings(response.headers.get("X-Echo-Warnings"))
            ],
        )

    async def get_data(self, path: str, *, params: dict[str, Any] | None = None, **kw) -> Any:
        return (await self.request("GET", path, params=params, **kw)).data

    async def get_text(self, path: str, *, params: dict[str, Any] | None = None, **kw) -> str:
        return (await self.request("GET", path, params=params, **kw)).text

    async def fetch_rules(self) -> dict | None:
        """`GET /wiki/agent-instructions`: `{pageId, title, body, truncated}`, or None when the
        workspace has none. Raises on any failure, including a reply of another shape."""
        data = await self.get_data(RULES_PATH)
        if data is not None and not isinstance(data, dict):
            raise ValueError("unexpected workspace rules reply")
        return data

    async def writable_page(self, page_id: object) -> str:
        """Canonical page id for a wiki write, never the workspace rules page. The rules page is
        looked up again on every call (it can be designated or changed at any time); if that
        lookup fails, the write is refused (fail closed)."""
        try:
            canonical = str(UUID(str(page_id).strip()))
        except ValueError as exc:
            raise ValueError(f"invalid page_id: {str(page_id)[:80]!r}") from exc
        try:
            rules = await self.fetch_rules()
            if rules is not None and "pageId" not in rules and "page_id" not in rules:
                raise ValueError("workspace rules reply has no page id")  # unknown shape: fail closed
            rules_page = render.wire_field(rules, "page_id") if rules is not None else None
            rules_id = str(UUID(str(rules_page))) if rules_page else None
        except (EchoApiError, httpx.HTTPError, TimeoutError, ValueError, RecursionError) as exc:
            raise ValueError(
                f"Wiki write refused: could not look up the workspace rules page ({type(exc).__name__}). Try again."
            ) from exc
        if canonical == rules_id:
            raise ValueError("This page can only be edited by people in the web app.")
        return canonical

    # -- lookups -----------------------------------------------------------

    async def list_projects(self) -> list[dict]:
        return list(await self.get_data("/projects") or [])

    async def resolve_project(self, project_key: str) -> dict:
        wanted = project_key.strip().upper()
        for project in await self.list_projects():
            if str(project.get("key", "")).upper() == wanted:
                return project
        raise NotFound(f"Project not found: {project_key}")

    async def get_ticket_by_key(self, key: str, *, include_notes: bool = False) -> EchoResponse:
        params = {"include": "notes"} if include_notes else None
        key = path_segment(key.upper(), "ticket key")
        return await self.request("GET", f"/tickets/by-key/{key}", params=params)

    # -- optimistic-locked writes -----------------------------------------

    async def ticket_write(
        self, key: str, method: str, subpath: str, body: dict | None | Callable[[dict], dict]
    ) -> EchoResponse:
        """GET the ticket → `If-Match: <ETag>` → write; on 409 re-read and retry once.
        `body` may be a function of the freshly read ticket, so a retry rebuilds it."""
        current = await self.get_ticket_by_key(key)
        for attempt in (1, 2):
            headers = {"If-Match": current.etag} if current.etag else {}
            try:
                return await self.request(
                    method,
                    f"/tickets/{path_segment(current.data['id'])}{subpath}",
                    json=body(current.data) if callable(body) else body,
                    headers=headers,
                )
            except VersionConflict as conflict:
                if attempt == 2:
                    raise VersionConflict(
                        status=409,
                        message=f"{key.upper()} changed elsewhere twice during this write; "
                        "re-read it with get_ticket and retry",
                        errors=[conflict.message, *conflict.errors],
                        etag=conflict.etag,
                    ) from conflict
                current = await self.get_ticket_by_key(key)
        raise AssertionError("unreachable")

    # -- people ------------------------------------------------------------

    async def resolve_assignee(self, value: str) -> dict:
        """A person (name or user id) -> `{id, name}`, chosen from active members (`GET /assignees`).

        Same id wins; otherwise an exact name match (ignoring spaces and case); otherwise a name
        containing `value`, if exactly one. None or several -> `ValueError` listing candidates."""
        wanted = value.strip()
        members = list(await self.get_data("/assignees") or [])
        for member in members:
            if str(member.get("id")) == wanted:
                return member

        def squeeze(text: str | None) -> str:
            return "".join((text or "").split()).lower()

        needle = squeeze(wanted)
        exact = [m for m in members if squeeze(m.get("name")) == needle]
        partial = exact or [m for m in members if needle and needle in squeeze(m.get("name"))]
        if len(partial) == 1:
            return partial[0]
        if partial:
            names = ", ".join(f"{m.get('name')}({m.get('id')})" for m in partial)
            raise ValueError(
                f"'{wanted}' matches several members; pass a user id:\n{render.untrusted('members', names)}"
            )
        names = ", ".join(str(m.get("name") or m.get("id")) for m in members) or "(none)"
        raise ValueError(f"No active member matches '{wanted}'. Members:\n{render.untrusted('members', names)}")
