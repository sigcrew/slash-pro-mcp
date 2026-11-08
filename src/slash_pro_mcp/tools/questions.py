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
        """Ask the ticket's owner about missing readiness items, 1-4 questions, multiple choice preferred:
        [{rubric_key: goal|scope|acceptance|evidence|open_questions, question, options? (max 6), source_bullet?}].
        handoff_id puts that handoff in input_required until answered. 409 when 4 are open."""
        if not 1 <= len(questions) <= 4:
            raise ValueError("questions는 1~4개")
        drafts: list[dict[str, Any]] = []
        for raw in questions:
            text = str(raw.get("question", "")).strip()
            if not text:
                raise ValueError("question이 비어 있음")
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
        tail = f" · 인계 {note_id} → input_required" if note_id else ""
        lines = [f"질문 {len(rows)}개 등록 ({key.strip().upper()}){tail}"]
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
        """Readiness questions. key: that ticket's, state open (default)|answered|dismissed|waived|all.
        Without key: open questions for to = "me" (default), a user id or a member name."""
        if key:
            if to and to.strip():
                raise ValueError("key와 to 중 하나만 지정")
            wanted = (state or "open").strip().lower()
            if wanted != "all":
                wanted = check_choice("state", wanted, QUESTION_STATES)
            ticket = (await client.get_ticket_by_key(key)).data
            rows = await client.get_data(f"/tickets/{path_segment(ticket['id'])}/questions", params={"state": wanted})
        else:
            if state and state.strip():
                raise ValueError("state는 key와 함께만 씀: 받은 질문함은 열린 질문만 보여 줌")
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
        """Re-judge a ticket's readiness (remaining assumptions); with open questions, shows them
        instead. Max 3 per ticket per day."""
        ticket = (await client.get_ticket_by_key(key)).data
        response = await client.request("POST", f"/tickets/{path_segment(ticket['id'])}/readiness/judge")
        data = response.data or {}
        skipped = render.wire_field(data, "skipped")
        tail = {
            None: "판정함",
            "open_questions": "열린 질문이 있음: 사람의 답을 기다리기",
            "cache": "변경 없음: 이전 판정 유지",
            "limit": "일일 한도 도달: 이전 판정 유지",
            "stale": "판정 중 설명이 바뀜: 다시 실행",
        }.get(skipped, str(skipped))
        lines = [f"남은 가정: {render.wire_field(data, 'remaining')} ({key.strip().upper()}) · {tail}"]
        for r in data.get("questions") or []:
            options = render.wire_field(r, "options") or []
            opts = f" (선택: {' / '.join(options)})" if options else ""
            lines.append(
                f"- [{render.wire_field(r, 'state')} · {render.wire_field(r, 'rubric_label')}] "
                f"{render.wire_field(r, 'question')}{opts} — {render.wire_field(r, 'id')}"
            )
        return render.untrusted("questions", "\n".join(lines))
