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
        """Add one agenda line (text) to the next occurrence of a recurring meeting (series_title: the
        calendar title) or to a calendar instance (event_id). The meeting row need not exist yet.
        Optional target: ticket_key (that ticket) or project_key (its product strategy page); without one the
        line belongs to the meeting itself. evidence: optional supporting text. Sending the same text again
        does not create a duplicate. 409 ambiguous_series lists candidate titles."""
        if bool(series_title) == bool(event_id):
            raise ValueError("pass exactly one of series_title and event_id")
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
        where = f"{title} {when}".strip() or f"event {event_id}"
        verb = "Already on the agenda" if out.get("duplicate") else "Agenda line added"
        made = " · scheduled meeting row created" if out.get("meetingCreated") else ""
        return render.untrusted(
            "agenda",
            f"{verb}: {where} — {text} "
            f"(meeting {out.get('meetingId')}, question {out.get('questionId')}, "
            f"target {out.get('targetType')}{made})",
        )
