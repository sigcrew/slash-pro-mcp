"""add_agenda_item: one agenda line for a meeting occurrence that has not started yet.

The server creates or finds that occurrence's scheduled meeting row (one per event id, as the app
does) and adds the line to the meeting's agenda; once the meeting starts it is that meeting's agenda.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashClient, path_segment
from ..common import WRITE_IDEMPOTENT, tool_errors


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=WRITE_IDEMPOTENT)
    @tool_errors
    async def add_agenda_item(
        text: str,
        series_title: str | None = None,
        event_id: str | None = None,
        evidence: str | None = None,
        project_key: str | None = None,
        ticket_key: str | None = None,
    ) -> str:
        """Add an agenda line to the next occurrence of a recurring meeting (series_title: calendar title) or
        to a calendar instance (event_id); exactly one. Optional target: ticket_key, or project_key (its strategy page).
        The same text again is not duplicated. 409 ambiguous_series lists candidate titles."""
        if bool(series_title) == bool(event_id):
            raise ValueError("series_title과 event_id 중 하나만 지정")
        title = ""
        when = ""
        if series_title:
            nxt = await client.get_data("/meeting-series/next-occurrence", params={"seriesTitle": series_title})
            event_id = str(nxt["eventId"])
            title = str(nxt.get("title") or series_title)
            when = str(nxt.get("scheduledAt") or "")
        body: dict[str, Any] = {"text": text}
        if evidence:
            body["evidence"] = evidence
        if project_key:
            body["projectKey"] = project_key
        if ticket_key:
            body["ticketKey"] = ticket_key
        response = await client.request(
            "POST", f"/events/{path_segment(event_id, 'event_id')}/agenda-questions", json=body
        )
        out = response.data or {}
        where = f"{title} {when}".strip() or f"이벤트 {event_id}"
        verb = "이미 안건에 있음" if out.get("duplicate") else "안건 추가"
        made = " · 예정 회의 생성" if out.get("meetingCreated") else ""
        return render.untrusted(
            "agenda",
            f"{verb}: {where} — {text} "
            f"(회의 {out.get('meetingId')}, 안건 {out.get('questionId')}, "
            f"대상 {out.get('targetType')}{made})",
        )
