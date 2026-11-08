"""Outcome tools: list / get / create / update, evidence, ticket membership.

Stage transitions, stop reasons, judgements, related tickets, documents and promotion are
human-only on the server (API tokens get 403), so there are no tools for them."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashClient, path_segment
from ..common import DESTRUCTIVE_IDEMPOTENT, READ, WRITE, WRITE_IDEMPOTENT, check_due_date, tool_errors

CREATE_STAGES = ("draft", "executing", "paused")


async def resolve_outcome(client: SlashClient, ref: str) -> str:
    """Outcome id from a UUID or an outcome key like `CORE-45` (tickets and outcomes share numbers)."""
    raw = ref.strip()
    try:
        return str(UUID(raw))
    except ValueError:
        pass
    found = await client.get_data(f"/keys/{path_segment(raw.upper(), 'outcome key')}")
    if not isinstance(found, dict) or found.get("kind") != "outcome":
        raise ValueError(f"{raw.upper()[:40]}: 아웃컴 키가 아님")
    return path_segment(found["id"])


async def fetch_outcome(client: SlashClient, ref: str) -> dict:
    return await client.get_data(f"/outcomes/{await resolve_outcome(client, ref)}") or {}


def _dates(body: dict[str, Any], start_date: str | None, target_date: str | None) -> None:
    if start_date and start_date.strip():
        body["startDate"] = check_due_date(start_date)
    if target_date and target_date.strip():
        body["targetDate"] = check_due_date(target_date)


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_outcomes(project_key: str) -> str:
        """A project's outcomes (desired user changes), recently updated first."""
        project = await client.resolve_project(project_key)
        rows = await client.get_data(f"/projects/{path_segment(project['id'])}/outcomes") or []
        return render.untrusted("outcome list", render.render_outcome_list(list(rows)))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_outcome(outcome: str) -> str:
        """One outcome with its tickets, evidence and judgements. outcome: key (e.g. CORE-45) or id."""
        return render.untrusted("outcome", render.render_outcome(await fetch_outcome(client, outcome)))

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def create_outcome(
        project_key: str,
        title: str,
        description: str | None = None,
        stage: str = "draft",
        scope: str | None = None,
        verification_method: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
    ) -> str:
        """Create an outcome; title: the desired user change. stage: draft|executing|paused.
        Dates: YYYY-MM-DD. Stage changes and judgements: web app."""
        if not title.strip():
            raise ValueError("title이 비어 있음")
        stage = stage.strip().lower()
        if stage not in CREATE_STAGES:
            raise ValueError(f"알 수 없는 stage: {stage[:40]} (허용: {', '.join(CREATE_STAGES)})")
        project = await client.resolve_project(project_key)
        body: dict[str, Any] = {"title": title.strip(), "stage": stage}
        for name, value in (
            ("description", description),
            ("scope", scope),
            ("verificationMethod", verification_method),
        ):
            if value and value.strip():
                body[name] = value
        _dates(body, start_date, target_date)
        created = (
            await client.request("POST", f"/projects/{path_segment(project['id'])}/outcomes", json=body)
        ).data or {}
        return render.untrusted(
            "outcome", f"아웃컴 생성: {created.get('key')} — {created.get('title')} (id {created.get('id')})"
        )

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def update_outcome(
        outcome: str,
        title: str | None = None,
        description: str | None = None,
        scope: str | None = None,
        verification_method: str | None = None,
        target: str | None = None,
        investment_limit: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
    ) -> str:
        """Change the given outcome fields; "" clears a text field (dates cannot be cleared). 409 if it
        changed meanwhile: re-read with get_outcome and retry. Stage: web app."""
        current = await fetch_outcome(client, outcome)
        body: dict[str, Any] = {"expectedRevision": current["revision"]}
        if title is not None:
            if not title.strip():
                raise ValueError("title이 비어 있음")
            body["title"] = title.strip()
        for name, value in (
            ("description", description),
            ("scope", scope),
            ("verificationMethod", verification_method),
            ("target", target),
            ("investmentLimit", investment_limit),
        ):
            if value is not None:
                body[name] = value
        _dates(body, start_date, target_date)
        if len(body) == 1:
            raise ValueError("바꿀 필드를 하나 이상 지정")
        updated = (await client.request("PATCH", f"/outcomes/{path_segment(current['id'])}", json=body)).data or {}
        return render.untrusted(
            "outcome",
            f"아웃컴 수정: {updated.get('key')} — {updated.get('title')} (revision {updated.get('revision')})",
        )

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def add_outcome_evidence(
        outcome: str, body: str, url: str | None = None, meeting_id: str | None = None
    ) -> str:
        """Add evidence (observation, metric, user reaction) to an outcome. url: https only.
        meeting_id: the source meeting."""
        if not body.strip():
            raise ValueError("body가 비어 있음")
        payload: dict[str, Any] = {"body": body}
        if url and url.strip():
            parts = urlsplit(url.strip())
            if parts.scheme != "https" or not parts.netloc:
                raise ValueError("url은 https:// URL이어야 함")
            payload["url"] = url.strip()
        if meeting_id and meeting_id.strip():
            payload["sourceMeetingId"] = path_segment(meeting_id, "meeting_id")
        oid = await resolve_outcome(client, outcome)
        out = (await client.request("POST", f"/outcomes/{oid}/evidence", json=payload)).data or {}
        return render.untrusted("outcome", f"근거 추가: {out.get('key')} (근거 {len(out.get('evidence') or [])}개)")

    async def membership(outcome: str, ticket_key: str, *, remove: bool, move: bool, confirm_prd: bool) -> str:
        current = await fetch_outcome(client, outcome)
        ticket = (await client.get_ticket_by_key(ticket_key)).data
        body: dict[str, Any] = {"expectedRevision": current["revision"]}
        if not remove:
            if move:
                # The ticket's current outcome as we just read it; the server 409s if it moved since.
                body["expectedCurrentOutcomeId"] = render.wire_field(ticket, "outcome_id")
            if confirm_prd:
                body["confirmLeavesLegacyPrd"] = True
        res = await client.request(
            "DELETE" if remove else "PUT",
            f"/outcomes/{path_segment(current['id'])}/tickets/{path_segment(ticket['id'])}",
            json=body,
        )
        out = res.data or {}
        verb = "에서 뺌" if remove else "에 넣음"
        move = out.get("move")
        moved = f" ({move.get('fromOutcomeId')}에서 이동)" if isinstance(move, dict) else ""
        return render.untrusted(
            "outcome",
            f"{ticket_key.strip().upper()}: 아웃컴 {current.get('key')}{verb}{moved} (revision {out.get('revision')})",
        )

    @mcp.tool(structured_output=False, annotations=WRITE_IDEMPOTENT)
    @tool_errors
    async def add_ticket_to_outcome(
        outcome: str, ticket_key: str, move: bool = False, confirm_leaves_legacy_prd: bool = False
    ) -> str:
        """Add a ticket to an outcome (same project; needs tickets:read, tickets:write, wiki:read).
        A ticket in another outcome gives 409 ticket_in_other_outcome unless move=True. Leaving a PRD-inherited outcome
        needs confirm_leaves_legacy_prd=True."""
        return await membership(outcome, ticket_key, remove=False, move=move, confirm_prd=confirm_leaves_legacy_prd)

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def remove_ticket_from_outcome(outcome: str, ticket_key: str) -> str:
        """Remove a ticket from an outcome (same scopes as add_ticket_to_outcome)."""
        return await membership(outcome, ticket_key, remove=True, move=False, confirm_prd=False)
