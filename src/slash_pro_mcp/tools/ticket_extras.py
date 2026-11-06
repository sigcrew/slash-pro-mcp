"""Ticket extras: checklist, evidence thread, own-note edits and relation removal."""

from __future__ import annotations

import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import EchoClient, canonical_id, path_segment, shown_id
from ..common import DESTRUCTIVE, DESTRUCTIVE_IDEMPOTENT, READ, RELATION_KINDS, WRITE, check_choice, tool_errors

# Fields a client may send back for a stored row (checked_by/at are server stamps).
_ROW_FIELDS = ("id", "text", "done", "source_key", "owner", "result", "feedback")
_RELATION_LISTS = {"follows": ("origins", "followers"), "blocks": ("blocked_by", "blocking")}


OWN_NOTES_ONLY = "Only notes this agent wrote in this session can be edited or deleted — use the web app for others."
OWN_EVIDENCE_ONLY = "Only evidence this agent added in this session can be deleted — use the web app for others."
BODY_NOT_EDITABLE = (
    "Handoff and verification note bodies cannot be edited (the server refuses); delete the note and add a new one."
)


def _shown_id(value: object) -> str:
    """A stored row id for an error message: its first 8 printable chars (stored ids are workspace data)."""
    text = "".join(c for c in str(value) if c.isprintable())
    return repr(text[:8] + ("…" if len(text) > 8 else ""))


def _stored(row: dict) -> dict:
    out = {name: render.wire_field(row, name) for name in _ROW_FIELDS}
    out = {k: v for k, v in out.items() if v is not None}
    if not str(out.get("id") or "").strip():
        raise ValueError("The checklist has a row without an id; fix the checklist in the web app")
    return out


def merge_checklist(stored_rows: list[dict], items: list[dict]) -> list[dict]:
    """Agent rows replace the agent-owned part of the checklist; people's rows stay untouched.

    - only rows with `owner: agent` are the agent's (any agent's: other agents' unjudged rows
      are replaced too); every other row (owner human or unset — hand-typed rows may carry no
      owner) is kept as it is, where it is
    - review results (`result`/`feedback`) are people's judgements: carried over by id and
      never set here; dropping a row that has one, or changing its text, is refused
    - the agent block takes the place of the first stored agent row (or the end)
    """
    stored = [_stored(r) for r in stored_rows if isinstance(r, dict)]
    kept_ids = {r["id"] for r in stored if r.get("owner") != "agent"}
    by_id = {r["id"]: r for r in stored if "id" in r}
    agent_rows: list[dict] = []
    for raw in items:
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError("checklist item text must not be empty")
        row_id = str(raw.get("id") or "").strip() or uuid.uuid4().hex[:12]
        if row_id in kept_ids:
            raise ValueError(f"Row {_shown_id(row_id)} is not an agent row and cannot be changed")
        previous = by_id.get(row_id, {})
        if previous.get("result") and previous.get("text") != text:
            raise ValueError(f"Row {_shown_id(row_id)} has a review result; its text cannot be changed")
        row = {"id": row_id, "text": text, "done": raw.get("done") is True, "owner": "agent"}
        for kept in ("source_key", "result", "feedback"):
            if previous.get(kept) is not None:
                row[kept] = previous[kept]
        agent_rows.append(row)
    sent = {r["id"] for r in agent_rows}
    for r in stored:
        if r.get("owner") == "agent" and r.get("result") and r["id"] not in sent:
            raise ValueError(f"Row {_shown_id(r['id'])} has a review result; only a person can remove it")
    if len(sent) != len(agent_rows):
        raise ValueError("duplicate checklist item id")
    merged: list[dict] = []
    placed = False
    for r in stored:
        if r.get("owner") != "agent":
            merged.append(r)
        elif not placed:
            merged.extend(agent_rows)
            placed = True
    if not placed:
        merged.extend(agent_rows)
    return merged


def register(mcp: FastMCP, client: EchoClient) -> None:
    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def replace_checklist(key: str, items: list[dict[str, Any]]) -> str:
        """Replace all agent-owned rows (`owner: agent`, written by any agent) of a ticket's acceptance
        checklist. items: [{text, done?, id?}]; an id updates that agent row, no id adds a row. People's rows
        and review results (✓/✗, feedback) are kept; removing or rewording a reviewed row is refused."""
        if len(items) > 200:
            raise ValueError("at most 200 checklist items")
        response = await client.ticket_write(
            key, "PATCH", "/checklist", lambda ticket: {"items": merge_checklist(ticket.get("checklist") or [], items)}
        )
        rows = (response.data or {}).get("checklist") or []
        lines = [f"{key.strip().upper()} checklist: {len(rows)} rows"]
        lines += [
            f"- [{'x' if r.get('done') else ' '}] {r.get('text')} ({r.get('owner') or '-'}, id {r.get('id')})"
            for r in rows
        ]
        return render.untrusted("checklist", "\n".join(lines))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_evidence(key: str) -> str:
        """A ticket's evidence thread (pinned meeting transcript excerpts), oldest first."""
        ticket = (await client.get_ticket_by_key(key)).data
        rows = await client.get_data(f"/tickets/{path_segment(ticket['id'])}/evidence") or []
        return render.untrusted("evidence", render.render_ticket_evidence(list(rows)))

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def add_evidence(
        key: str,
        meeting_id: str,
        excerpt: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        speaker: str | None = None,
    ) -> str:
        """Pin a meeting transcript excerpt (up to 2000 chars, stripped) as ticket evidence.
        start_ms / end_ms: the span from find_transcript_excerpts."""
        if not excerpt.strip():
            raise ValueError("excerpt must not be empty")
        body: dict[str, Any] = {"meeting_id": path_segment(meeting_id, "meeting_id"), "excerpt": excerpt.strip()}
        for name, value in (("start_ms", start_ms), ("end_ms", end_ms)):
            if value is not None:
                body[name] = max(0, int(value))
        if speaker and speaker.strip():
            body["speaker"] = speaker.strip()
        ticket = (await client.get_ticket_by_key(key)).data
        out = (await client.request("POST", f"/tickets/{path_segment(ticket['id'])}/evidence", json=body)).data or {}
        if out.get("id"):
            client.own_evidence_ids.add(canonical_id(out["id"]))
        return f"Evidence added {shown_id(out.get('id'))} ({key.strip().upper()})"

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE)
    @tool_errors
    async def delete_evidence(key: str, evidence_id: str) -> str:
        """Delete one evidence item that this agent added with add_evidence in this session."""
        path_segment(evidence_id, "evidence_id")
        segment = canonical_id(evidence_id)
        if segment not in client.own_evidence_ids:
            raise ValueError(OWN_EVIDENCE_ONLY)
        ticket = (await client.get_ticket_by_key(key)).data
        await client.request("DELETE", f"/tickets/{path_segment(ticket['id'])}/evidence/{segment}")
        client.own_evidence_ids.discard(segment)
        return f"Evidence deleted {segment} ({key.strip().upper()})"

    def own_note(note_id: str) -> tuple[str, str]:
        """(path segment, kind) for a note this MCP process wrote; anything else is refused (the
        server would let an owner-role token change anyone's notes)."""
        path_segment(note_id, "note_id")
        canonical = canonical_id(note_id)
        if canonical not in client.own_notes:
            raise ValueError(OWN_NOTES_ONLY)
        return path_segment(canonical, "note_id"), client.own_notes[canonical]

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def update_note(key: str, note_id: str, body: str) -> str:
        """Edit the body of a plain note (add_note) this agent wrote in this session. The server allows
        author edits only within 10 minutes of writing (afterwards only the workspace owner can edit).
        Handoff and verification notes cannot be edited, only deleted."""
        if not body.strip():
            raise ValueError("body must not be empty")
        segment, kind = own_note(note_id)
        if kind != "manual":
            raise ValueError(BODY_NOT_EDITABLE)
        out = (await client.ticket_write(key, "PATCH", f"/notes/{segment}", {"body": body})).data or {}
        return f"Note updated {shown_id(out.get('id', segment))} ({key.strip().upper()})"

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE)
    @tool_errors
    async def delete_note(key: str, note_id: str) -> str:
        """Delete a note this agent wrote in this session (plain, handoff or verification). The timeline
        keeps a "note deleted" marker. The server applies the same 10-minute author window as update_note."""
        segment, _ = own_note(note_id)
        await client.ticket_write(key, "DELETE", f"/notes/{segment}", None)
        client.own_notes.pop(canonical_id(segment), None)
        return f"Note deleted {segment} ({key.strip().upper()})"

    def relation_ends(ticket: dict, kinds: tuple[str, ...], other: str) -> list[tuple[str, dict]]:
        relations = ticket.get("relations") or {}
        return [
            (k, end)
            for k in kinds
            for bucket in _RELATION_LISTS[k]
            for end in render.wire_field(relations, bucket) or []
            if str(end.get("key", "")).upper() == other and render.wire_field(end, "relation_id")
        ]

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE)
    @tool_errors
    async def delete_relation(key: str, target_key: str, kind: str | None = None) -> str:
        """Remove the relation (follows|blocks) between two tickets; pass kind when both exist.
        Both tickets get a system note."""
        kinds = (check_choice("kind", kind, RELATION_KINDS),) if kind else RELATION_KINDS
        source = key.strip().upper()
        target = target_key.strip().upper()
        ticket = (await client.get_ticket_by_key(source)).data
        found = relation_ends(ticket, kinds, target)
        if not found:
            # Relation lists on a ticket are capped; the other side may still list it.
            found = relation_ends((await client.get_ticket_by_key(target)).data, kinds, source)
        if not found:
            raise ValueError(f"No relation between {source} and {target}")
        if len({k for k, _ in found}) > 1:
            raise ValueError("Both follows and blocks exist; pass kind")
        rel_kind, end = found[0]
        relation_id = path_segment(render.wire_field(end, "relation_id"))
        await client.request("DELETE", f"/tickets/{path_segment(ticket['id'])}/relations/{relation_id}")
        return f"Relation deleted: {source} {rel_kind} {target}"
