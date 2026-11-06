"""Readiness questions: ask_questions / list_questions.
Answering is people-only (the ticket drawer) — there is no answer tool on purpose."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashClient, path_segment
from ..common import QUESTION_STATES, READ, WRITE, check_choice, rubric_key, tool_errors


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def ask_questions(key: str, questions: list[dict], handoff_id: str | None = None) -> str:
        """Ask the ticket's owner about missing readiness items (1-4 questions, one point each, multiple
        choice preferred). questions: [{rubric_key: goal|scope|acceptance|evidence|open_questions, question,
        options?: [up to 6], source_bullet?: the open-question bullet it came from}]. handoff_id puts that
        handoff in input_required until answered (it then reopens in the inbox). Only people answer.
        409 when 4 questions are already open."""
        if not 1 <= len(questions) <= 4:
            raise ValueError("questions must have 1-4 items")
        drafts: list[dict[str, Any]] = []
        for raw in questions:
            text = str(raw.get("question", "")).strip()
            if not text:
                raise ValueError("question must not be empty")
            options = [str(o).strip() for o in (raw.get("options") or []) if str(o).strip()]
            drafts.append(
                {
                    "rubric_key": rubric_key(str(raw.get("rubric_key", ""))),
                    "question": text,
                    "options": options,
                    "source_bullet": raw.get("source_bullet") or None,
                }
            )
        note_id = handoff_id.strip() if handoff_id and handoff_id.strip() else None
        body: dict[str, Any] = {"questions": drafts, "handoff_note_id": note_id}
        ticket = (await client.get_ticket_by_key(key)).data
        response = await client.request("POST", f"/tickets/{path_segment(ticket['id'])}/questions", json=body)
        rows = list(response.data or [])
        tail = f" · handoff {note_id} → input_required" if note_id else ""
        lines = [f"Asked {len(rows)} question(s) ({key.strip().upper()}){tail}"]
        for r in rows:
            options = render.wire_field(r, "options") or []
            opts = f" (선택: {' / '.join(options)})" if options else ""
            lines.append(
                f"- [{render.wire_field(r, 'state')} · {render.wire_field(r, 'rubric_label')}] "
                f"{render.wire_field(r, 'question')}{opts} — {render.wire_field(r, 'id')}"
            )
        return render.untrusted("questions", "\n".join(lines))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_questions(key: str | None = None, to: str | None = None, state: str | None = None) -> str:
        """Readiness questions. key: one ticket's questions, filtered by state (open|answered|dismissed|
        waived|all, default open). Without key: the open questions a person should answer; to = "me"
        (default, the token owner), a user id, or a member name.
        One line per question: `- [state · item] KEY question (options) — asked by, when (id …)`."""
        if key:
            if to and to.strip():
                raise ValueError("pass key or to, not both")
            wanted = (state or "open").strip().lower()
            if wanted != "all":
                wanted = check_choice("state", wanted, QUESTION_STATES)
            ticket = (await client.get_ticket_by_key(key)).data
            rows = await client.get_data(f"/tickets/{path_segment(ticket['id'])}/questions", params={"state": wanted})
        else:
            if state and state.strip():
                raise ValueError("state applies only with key; the inbox lists open questions")
            person = (to or "me").strip()
            if person.lower() != "me":
                try:
                    person = str(UUID(person))
                except ValueError:
                    person = str((await client.resolve_assignee(person))["id"])
            rows = await client.get_data("/questions/inbox", params={"to": person})
        return render.untrusted("questions", render.render_questions(list(rows or [])))

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def readiness_refresh(key: str) -> str:
        """Re-judge a ticket's readiness (remaining assumptions). With open questions already, shows them
        instead of judging again. Limit: 3 per ticket per day (409 judge_limit)."""
        ticket = (await client.get_ticket_by_key(key)).data
        response = await client.request("POST", f"/tickets/{path_segment(ticket['id'])}/readiness/judge")
        data = response.data or {}
        skipped = render.wire_field(data, "skipped")
        tail = {
            None: "judged",
            "open_questions": "open questions exist; wait for a person to answer",
            "cache": "unchanged; previous judgement kept",
            "limit": "daily limit reached; previous judgement kept",
            "stale": "description changed while judging; run again",
        }.get(skipped, str(skipped))
        lines = [f"Remaining assumptions: {render.wire_field(data, 'remaining')} ({key.strip().upper()}) · {tail}"]
        for r in data.get("questions") or []:
            options = render.wire_field(r, "options") or []
            opts = f" (선택: {' / '.join(options)})" if options else ""
            lines.append(
                f"- [{render.wire_field(r, 'state')} · {render.wire_field(r, 'rubric_label')}] "
                f"{render.wire_field(r, 'question')}{opts} — {render.wire_field(r, 'id')}"
            )
        return render.untrusted("questions", "\n".join(lines))
