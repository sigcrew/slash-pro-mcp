"""Shared helpers for the tool modules: error translation, argument checks, tool annotations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from functools import wraps

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import render
from .client import SlashApiError

# MCP hints (https://modelcontextprotocol.io/specification/server/tools). A write without an
# explicit destructiveHint=False would be read as destructive.
# openWorldHint=False: every tool talks only to the configured SLASH workspace.
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)
MAX_RESULT_CHARS = 200_000  # one tool result; a longer one is cut (`render.truncate`)
TOOL_DEADLINE_SECONDS = 120  # a tool may make several requests, each with its own 60 s deadline
MAX_KEYS = 50  # ticket keys per mark_deployed call
MAX_LINKED_KEYS = 20  # follows origins (create_ticket) and ticket_keys (create_prd)
# Failures of a follow-up step after a create; caught so the result still names what was created.
FOLLOW_UP_ERRORS = (SlashApiError, httpx.HTTPError, TimeoutError, ValueError)
FOLLOW_UP_MARGIN_SECONDS = 5

DEFAULT_LIST_STATUSES = ("todo", "in_progress")
# list filters
TICKET_STATUSES = ("backlog", "todo", "in_progress", "review", "done", "canceled")
# what a write may set; canceling needs a reason, chosen in the web app's cancel dialog
WRITABLE_TICKET_STATUSES = ("backlog", "todo", "in_progress", "review", "done")
CANCELED_WRITE_ERROR = "canceled는 여기서 설정할 수 없음: 취소 사유는 웹 앱에서 선택"
TICKET_PRIORITIES = ("low", "medium", "high", "urgent")
# ticket relations
RELATION_KINDS = ("follows", "blocks")
HANDOFF_ROLES = ("builder", "verifier", "planner")
HANDOFF_TRIGGERS = ("human", "agent")
HANDOFF_STATES = (
    "open",
    "claimed",
    "input_required",
    "completed",
    "blocked",
    "expired",
    "canceled",
)
# readiness questions
RUBRIC_KEYS = ("goal", "scope", "acceptance", "evidence", "open_questions")
RUBRIC_KEY_ALIASES = {
    "목표": "goal",
    "왜": "goal",
    "범위": "scope",
    "완료조건": "acceptance",
    "완료 조건": "acceptance",
    "근거": "evidence",
    "열린질문": "open_questions",
    "열린 질문": "open_questions",
}
QUESTION_STATES = ("open", "answered", "dismissed", "waived")


def rubric_key(value: str) -> str:
    """Accept the English key or its Korean label; anything else is a ValueError."""
    key = RUBRIC_KEY_ALIASES.get(value.strip(), value.strip().lower())
    if key not in RUBRIC_KEYS:
        raise ValueError(f"알 수 없는 rubric_key: {value} (허용: {', '.join(RUBRIC_KEYS)})")
    return key


def tool_errors[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Turn API/validation failures into MCP tool errors (isError=true), within an overall
    `TOOL_DEADLINE_SECONDS`."""

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            async with asyncio.timeout(TOOL_DEADLINE_SECONDS):
                result = await fn(*args, **kwargs)
            if isinstance(result, str):
                result = render.truncate(result, MAX_RESULT_CHARS)  # type: ignore[assignment]
            return result
        except SlashApiError as exc:
            # The server's message can quote user-written text: fence it like any other content.
            raise ToolError(f"SLASH API 오류:\n{render.untrusted('api error', str(exc))}") from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except TimeoutError as exc:
            raise ToolError("SLASH API 요청 시간 초과") from exc
        except httpx.HTTPError as exc:
            # Transport failures (DNS, TLS, timeout). Never carries request headers.
            detail = render.untrusted("transport error", str(exc)[:300])
            raise ToolError(f"SLASH API 연결 실패: {type(exc).__name__}\n{detail}") from exc

    return wrapper


def split_statuses(status: str | None) -> list[str] | None:
    """`"todo|review"` / `"todo,review"` → list; `"all"` → None; None → default."""
    if status is None or not status.strip():
        return list(DEFAULT_LIST_STATUSES)
    if status.strip().lower() == "all":
        return None
    parts = [p.strip().lower() for p in status.replace("|", ",").split(",") if p.strip()]
    unknown = [p for p in parts if p not in TICKET_STATUSES]
    if unknown:
        raise ValueError(f"알 수 없는 status: {', '.join(unknown)} (허용: {', '.join(TICKET_STATUSES)})")
    return parts


def check_writable_status(value: str) -> str:
    """Status a write tool may set; `canceled` gets a pointer to the web app."""
    if value.strip().lower() == "canceled":
        raise ValueError(CANCELED_WRITE_ERROR)
    return check_choice("status", value, WRITABLE_TICKET_STATUSES)


def check_due_date(value: str) -> str:
    """A `YYYY-MM-DD` date, as sent to the server."""
    raw = value.strip()
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError(f"날짜는 YYYY-MM-DD 형식이어야 함: {raw[:40]}") from exc


def check_keys(keys: list[str] | None, limit: int = MAX_KEYS) -> list[str]:
    """Ticket keys, stripped and upper-cased, empty ones dropped, at most `limit`."""
    cleaned = [k.strip().upper() for k in keys or [] if k.strip()]
    if len(cleaned) > limit:
        raise ValueError(f"티켓 키는 한 번에 최대 {limit}개")
    return cleaned


def follow_up_deadline(started: float) -> asyncio.Timeout:
    """A deadline for the steps after a create that ends before the tool's own deadline, so a slow
    step raises a catchable TimeoutError instead of cancelling the tool and losing the new key/id.
    `started` is the event-loop time when the tool began."""
    return asyncio.timeout_at(started + TOOL_DEADLINE_SECONDS - FOLLOW_UP_MARGIN_SECONDS)


# words that clear the assignee / due date in update_ticket
CLEAR_WORDS = ("없음", "none", "null", "-")


def check_choice(name: str, value: str, allowed: tuple[str, ...]) -> str:
    value = value.strip().lower()
    if value not in allowed:
        raise ValueError(f"알 수 없는 {name}: {value[:40]} (허용: {', '.join(allowed)})")
    return value


# Wiki section (fixed root) -> server page_type. `product` cannot hold child pages: it maps to
# the project's single product strategy page (ensure-one-pager).
WIKI_SECTIONS = {
    "product": "product",
    "feature-specs": "feature_spec",
    "technical-docs": "technical",
    "decisions": "decision",
    "notes": "note",
    "meetings": "meeting",
    "perspectives": "perspective",
}
