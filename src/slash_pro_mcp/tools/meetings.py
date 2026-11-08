"""Meeting tools: overview/minutes/summary (get_meeting), transcript excerpt search, meeting search
(list_meetings, incl. upcoming) and adding a to-do to a meeting (add_todo)."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashApiError, SlashClient, path_segment
from ..common import READ, WRITE, check_due_date, tool_errors


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_meeting(meeting_id: str, part: Literal["overview", "minutes", "summary"] = "overview") -> str:
        """part=overview: title, time, attendees, transcript, latest analysis run and summary, recording
        sessions. minutes: the latest minutes as markdown. summary: executive summary and check points as JSON."""
        meeting_id = path_segment(meeting_id, "meeting_id")
        if part == "minutes":
            return render.untrusted("meeting minutes", await client.get_text(f"/meetings/{meeting_id}/minutes.md"))
        if part == "summary":
            data = await client.get_data(f"/meetings/{meeting_id}/summary", workspace=False) or {}
            body = {
                "executiveSummary": render.wire_field(data, "executive_summary"),
                "checkPoints": render.wire_field(data, "check_points"),
                "updatedAt": render.wire_field(data, "updated_at"),
            }
            return render.untrusted("meeting summary", json.dumps(body, ensure_ascii=False, indent=1))
        meeting = await client.get_data(f"/meetings/{meeting_id}", workspace=False)
        run_envelope = await client.get_data(f"/meetings/{meeting_id}/analysis/run")
        run = (run_envelope or {}).get("run") if isinstance(run_envelope, dict) else None
        try:
            versions = await client.get_data(f"/meetings/{meeting_id}/analysis/versions", params={"limit": 1}) or []
        except SlashApiError as exc:
            # Older servers gave API tokens 403 here while the meeting itself was readable:
            # degrade instead of failing.
            if exc.status != 403:
                raise
            versions = None
        try:
            envelope = await client.get_data(f"/meetings/{meeting_id}/recording/sessions")
            sessions = list((envelope or {}).get("sessions") or []) if isinstance(envelope, dict) else []
        except SlashApiError as exc:
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
    async def find_transcript_excerpts(meeting_id: str, query: str, description: str | None = None) -> str:
        """Up to 3 transcript passages supporting query (a one-line statement, max 500 chars),
        found by a server LLM: `[mm:ss–mm:ss] excerpt` plus a reason."""
        if not query.strip():
            raise ValueError("query가 비어 있음")
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
        upcoming: bool = False,
        limit: int = 20,
    ) -> str:
        """Find meetings, most recent first. date_from / date_to: YYYY-MM-DD (whole day, KST, inclusive) or ISO
        datetime. title: words in the title. series: a meeting id of a recurring series or a calendar series id. attendee:
        part of a name or a user id. limit: max 100. upcoming=True: scheduled meetings, soonest first, no other filters."""
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        if upcoming:
            if any(v and v.strip() for v in (date_from, date_to, title, series, attendee)):
                raise ValueError("upcoming=True에는 다른 필터를 줄 수 없음")
            rows = await client.get_data("/meetings/upcoming", params=params)
            return render.untrusted("meeting list", render.render_meeting_list(list(rows or [])))
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
        """Add an approved to-do to a meeting (it takes the meeting's project).
        assignee: member name or user id. due: YYYY-MM-DD. evidence: the supporting utterance, verbatim.
        Development work: create_ticket."""
        if not title.strip():
            raise ValueError("title이 비어 있음")
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
            f"할 일 추가: {todo.get('title') or title.strip()}",
            f"담당자 {who.get('name') or who['id']}" if who else "담당자 없음",
            f"마감일 {due_text or '없음'}",
        ]
        return render.untrusted("todo", " · ".join(parts) + f" (id {todo.get('id')}, 회의 {meeting_id})")
