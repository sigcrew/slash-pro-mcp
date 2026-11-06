"""Meeting tools: summary, minutes markdown, transcript excerpt search, meeting search
(list_meetings) and adding a to-do to a meeting (add_todo)."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import EchoApiError, EchoClient, path_segment
from ..common import READ, WRITE, check_due_date, tool_errors


def register(mcp: FastMCP, client: EchoClient) -> None:
    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_meeting(meeting_id: str) -> str:
        """Meeting overview: title, time, attendees, whether a transcript exists, latest analysis run
        status and stage, latest analysis summary, recording sessions and how the last one ended."""
        meeting_id = path_segment(meeting_id, "meeting_id")
        meeting = await client.get_data(f"/meetings/{meeting_id}", workspace=False)
        run_envelope = await client.get_data(f"/meetings/{meeting_id}/analysis/run")
        run = (run_envelope or {}).get("run") if isinstance(run_envelope, dict) else None
        try:
            versions = await client.get_data(f"/meetings/{meeting_id}/analysis/versions", params={"limit": 1}) or []
        except EchoApiError as exc:
            # Older servers gave API tokens 403 here while the meeting itself was readable:
            # degrade instead of failing.
            if exc.status != 403:
                raise
            versions = None
        try:
            envelope = await client.get_data(f"/meetings/{meeting_id}/recording/sessions")
            sessions = list((envelope or {}).get("sessions") or []) if isinstance(envelope, dict) else []
        except EchoApiError as exc:
            # Older server (404) or a token without the scope (403): the summary
            # still stands, only the session line degrades.
            if exc.status not in (403, 404):
                raise
            sessions = None
        return render.untrusted(
            "meeting",
            render.render_meeting(meeting, run, None if versions is None else list(versions), sessions),
        )

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_minutes(meeting_id: str) -> str:
        """The meeting's latest minutes as markdown."""
        minutes = await client.get_text(f"/meetings/{path_segment(meeting_id, 'meeting_id')}/minutes.md")
        return render.untrusted("meeting minutes", minutes)

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def find_transcript_excerpts(meeting_id: str, query: str, description: str | None = None) -> str:
        """Find up to 3 transcript passages that support query (a one-line statement such as a ticket
        title, up to 500 chars), using a server-side LLM. Returns `[mm:ss–mm:ss] excerpt` plus a reason."""
        if not query.strip():
            raise ValueError("query must not be empty")
        body: dict[str, Any] = {"title": query.strip()[:500]}
        if description:
            body["description"] = description
        response = await client.request(
            "POST", f"/meetings/{path_segment(meeting_id, 'meeting_id')}/transcript/find-excerpts", json=body
        )
        return render.untrusted("meeting transcript", render.render_excerpts(list(response.data or [])))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_meetings(
        date_from: str | None = None,
        date_to: str | None = None,
        title: str | None = None,
        series: str | None = None,
        attendee: str | None = None,
        limit: int = 20,
    ) -> str:
        """Find meetings, most recent first (e.g. "yesterday's daily standup", "meetings with Alex").
        date_from / date_to: YYYY-MM-DD (a whole day in Korea time; date_to is inclusive) or an ISO datetime.
        title: words in the title (spaces ignored, up to 200 chars). series: a meeting id of a recurring series
        (same calendar recurrence, else same title) or a calendar series id. attendee: part of a name or a
        user id. limit: 1-100 (default 20). No filters: the latest meetings.
        One line each: `time (KST) · title · status · attendees N — one-line summary (id …)`."""
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        for name, value in (
            ("from", date_from),
            ("to", date_to),
            ("q", (title or "")[:200]),
            ("series", series),
            ("attendee", attendee),
        ):
            if value and value.strip():
                params[name] = value.strip()
        rows = await client.get_data("/meetings/search", params=params)
        return render.untrusted("meeting list", render.render_meeting_list(list(rows or [])))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_upcoming_meetings(limit: int = 10) -> str:
        """Upcoming meetings, soonest first (limit 1-100, default 10). Past meetings and date ranges:
        list_meetings."""
        rows = await client.get_data("/meetings/upcoming", params={"limit": max(1, min(int(limit), 100))})
        return render.untrusted("meeting list", render.render_meeting_list(list(rows or [])))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_meeting_summary(meeting_id: str) -> str:
        """The meeting's executive summary and check points as JSON. Full minutes: get_minutes."""
        meeting_id = path_segment(meeting_id, "meeting_id")
        data = await client.get_data(f"/meetings/{meeting_id}/summary", workspace=False) or {}
        body = {
            "executiveSummary": render.wire_field(data, "executive_summary"),
            "checkPoints": render.wire_field(data, "check_points"),
            "updatedAt": render.wire_field(data, "updated_at"),
        }
        return render.untrusted("meeting summary", json.dumps(body, ensure_ascii=False, indent=1))

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def add_todo(
        meeting_id: str,
        title: str,
        assignee: str | None = None,
        due: str | None = None,
        evidence: str | None = None,
        description: str | None = None,
    ) -> str:
        """Add an approved to-do to a meeting, like "+ add to-do" in the minutes (find the id with
        list_meetings; to-dos without a meeting are not supported). assignee: member name or user id.
        due: YYYY-MM-DD. evidence: the supporting utterance, verbatim. The project is the meeting's project.
        For development work use create_ticket."""
        if not title.strip():
            raise ValueError("title must not be empty")
        meeting_id = path_segment(meeting_id, "meeting_id")
        body: dict[str, Any] = {"title": title.strip()}
        who: dict | None = None
        if assignee and assignee.strip():
            who = await client.resolve_assignee(assignee)
            body["suggestedAssigneeId"] = who["id"]
        if due and due.strip():
            body["suggestedDueDate"] = check_due_date(due)
        if evidence and evidence.strip():
            body["sourceExcerpt"] = evidence.strip()
        if description and description.strip():
            body["description"] = description
        response = await client.request("POST", f"/meetings/{meeting_id}/action-proposals", json=body)
        todo = response.data or {}
        due_text = render.wire_field(todo, "suggested_due_date") or body.get("suggestedDueDate")
        parts = [
            f"To-do added: {todo.get('title') or title.strip()}",
            f"assignee {who.get('name') or who['id']}" if who else "no assignee",
            f"due {due_text or 'none'}",
        ]
        return render.untrusted("todo", " · ".join(parts) + f" (id {todo.get('id')}, meeting {meeting_id})")
