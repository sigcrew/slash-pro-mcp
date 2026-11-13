"""Ticket tools: list/get (read), create/status/assignee·due/note/handoff/deployed (write),
relate_ticket, verification, and handoff delivery: list_handoffs / update_handoff."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .. import render
from ..client import SlashApiError, SlashClient, path_segment, shown_id
from ..common import (
    CLEAR_WORDS,
    DESTRUCTIVE,
    DESTRUCTIVE_IDEMPOTENT,
    FOLLOW_UP_ERRORS,
    HANDOFF_ROLES,
    HANDOFF_STATES,
    HANDOFF_TRIGGERS,
    MAX_LINKED_KEYS,
    READ,
    RELATION_KINDS,
    TICKET_PRIORITIES,
    WRITE,
    WRITE_IDEMPOTENT,
    check_choice,
    check_due_date,
    check_keys,
    check_writable_status,
    follow_up_deadline,
    split_statuses,
    tool_errors,
)


class VerificationTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=2000)
    result: Literal["passed", "failed"]
    summary: str = Field("", max_length=2000)


class VerificationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    severity: Literal["important", "minor"]
    resolved: bool = False


class VerificationReport(BaseModel):
    # Mirrors the server's `InVerification` (extra fields are rejected there too). No docstring:
    # it would be sent as the schema description in every tools/list.
    model_config = ConfigDict(extra="forbid")
    run_id: UUID | None = Field(None, description="id returned by start_verification")
    verdict: Literal["passed", "failed", "blocked"]
    stage: Literal["self", "review", "fix", "re_review", "smoke"]
    commits: list[str] = Field(default_factory=list, max_length=50)
    prs: list[str] = Field(default_factory=list, max_length=50)
    tests: list[VerificationTest] = Field(default_factory=list, max_length=20)
    issues_found: list[VerificationIssue] = Field(default_factory=list, max_length=100)
    human_todo: list[str] = Field(default_factory=list, max_length=100, description="things only a person can do")
    checklist_passed: list[str] = Field(default_factory=list, max_length=200, description="checklist row ids")
    answers_acknowledged: list[str] = Field(
        default_factory=list, max_length=200, description="all new_answer_question_ids from start_verification"
    )


def _relation_summary(status: int, data: dict[str, Any]) -> str:
    """One line for the relation, plus copied / copyable rejected (✗) rows and server warnings."""
    verb = "이미 있음" if status == 200 else "관계 추가"
    lines = [f"{verb}: {data['from']['key']} {data['kind']} {data['to']['key']}"]
    taken, available = data.get("rejectedTaken"), data.get("rejectedAvailable")
    if taken:
        lines.append(f"{data['to']['key']}에서 ✗ 항목 {taken}개 복사")
    if available:
        lines.append(f"✗ 항목 {available}개 복사 가능: take_rejected=True로 다시 호출")
    lines += [f"경고: {w}" for w in data.get("warnings") or []]
    return "\n".join(lines)


def _sort_key(row: tuple[str, dict]) -> tuple[datetime, str]:
    created = datetime.fromisoformat(str(render.wire_field(row[1], "created_at")).replace("Z", "+00:00"))
    return created, str(row[1].get("id"))


MAX_LIST_LIMIT = 200


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_tickets(
        project_key: str | None = None,
        status: str | None = None,
        prd: str | None = None,
        assignee: str | None = None,
        blocked: bool | None = None,
        limit: int = 50,
    ) -> str:
        """List tickets, newest first; the whole workspace unless project_key. status: `todo,in_progress` (default),
        any comma list (canceled included) or `all`. prd: PRD page id or `none`. assignee: member name, user id or 'me'.
        blocked: true = blocked only, false = unblocked only. limit: max 200."""
        wanted = split_statuses(status)
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        params: dict[str, Any] = {}
        if prd and prd.strip():
            params["prd_page_id"] = prd.strip()
        if assignee and assignee.strip():
            params["assignee_id"] = (await client.resolve_assignee(assignee))["id"]
        if blocked is not None:
            params["is_blocked"] = "true" if blocked else "false"
        projects = await client.list_projects()
        keys = {str(p["id"]): p["key"] for p in projects}
        if project_key:
            matched = [p for p in projects if p["key"].upper() == project_key.strip().upper()]
            if not matched:
                raise ValueError(f"프로젝트 없음: {project_key}")
            path = f"/projects/{path_segment(matched[0]['id'])}/tickets"
            keys = {None: matched[0]["key"]}  # project endpoint: every row is this project
        else:
            path = "/tickets"  # one workspace-wide call instead of one per project
        # The server filters one status at a time: one call per wanted status, merged by id.
        tickets: dict[str, dict] = {}
        for status_value in wanted or [None]:
            query = {**params, "status": status_value} if status_value else params
            for t in await client.get_data(path, params=query) or []:
                tickets.setdefault(str(t.get("id")), t)

        def project_label(ticket: dict) -> str:
            pid = str(render.wire_field(ticket, "project_id"))
            return keys.get(None) or keys.get(pid) or f"?{pid[:8]}"

        rows = [(project_label(t), t) for t in tickets.values() if not wanted or t.get("status") in wanted]
        try:
            # The server orders by per-project number, meaningless across projects.
            rows.sort(key=_sort_key, reverse=True)
        except (TypeError, ValueError):
            pass  # a row without a readable created_at: keep server order
        text = render.render_ticket_list(rows[:limit])
        if len(rows) > limit:
            text += (
                f"\n… 외 {len(rows) - limit}개 — status/assignee/prd로 좁히거나 limit을 올리기 (최대 {MAX_LIST_LIMIT})"
            )
        return render.untrusted("ticket list", text)

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_ticket(key: str) -> str:
        """Full working context of one ticket (description, checklist, evidence, relations, handoffs, activity).
        key: e.g. CORE-23."""
        text = await client.get_text(
            f"/tickets/by-key/{path_segment(key.upper(), 'ticket key')}/context", params={"format": "markdown"}
        )
        return render.untrusted("ticket", text)

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def start_verification(key: str, commits: list[str]) -> str:
        """Call before verifying: pins the current answers and the commits under test (immutable ids such as
        repo@full-SHA, exactly as in the report). Pass the returned id as report.run_id to add_verification. List every
        verification_basis.new_answer_question_ids in report.answers_acknowledged, or the server returns 422."""
        result = await client.ticket_write(key, "POST", "/verification-runs", {"commits": commits})
        return render.untrusted("verification", json.dumps(result.data, ensure_ascii=False))

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def add_verification(key: str, report: VerificationReport) -> str:
        """Record a verification report. A report whose run is stale or missing is kept but does not count
        as the latest pass."""
        payload = report.model_dump(mode="json", exclude_unset=True)
        result = await client.ticket_write(key, "POST", "/notes", {"kind": "verification", "verification": payload})
        client.remember_note(result.data, "verification")
        return render.untrusted("verification", json.dumps(result.data, ensure_ascii=False))

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE)
    @tool_errors
    async def create_ticket(
        project_key: str,
        title: str | None = None,
        description: str | None = None,
        priority: str = "medium",
        status: str | None = None,
        follows: str | None = None,
        request_builder: bool = False,
        to: str | None = None,
        model: str | None = None,
        prd_page_id: str | None = None,
        take_rejected: bool = False,
        parent: str | None = None,
    ) -> str:
        """Create a ticket; returns its key. title or follows is required. priority: low|medium|high|urgent.
        status: backlog|todo|in_progress|review|done, set after creation (canceled: web app only).
        prd_page_id: a PRD of the same project. parent: a regular ticket in the same project (sub-ticket, no nesting).
        follows: origin key(s), comma-separated (max 20), making this a follow-up: the first origin's ✗ checklist rows move
        over and an empty title/description is drafted from it; take_rejected=True also copies the other origins' ✗ rows.
        request_builder=True hands it to the first origin's builder; to (+ model: undated alias) hands it to that agent.
        Ordering: relate_ticket(kind=blocks). If a later step fails, the result still names the new key: don't retry."""
        priority = check_choice("priority", priority, TICKET_PRIORITIES)
        target_status = check_writable_status(status) if status else None
        started = asyncio.get_running_loop().time()
        origin_keys = check_keys((follows or "").replace(" ", ",").split(","), MAX_LINKED_KEYS)
        if not (title and title.strip()) and not origin_keys:
            raise ValueError("title이나 follows가 필요함")
        if model and model.strip() and not (to and to.strip()):
            raise ValueError("model에는 to(인계받을 에이전트)가 필요함")
        project = await client.resolve_project(project_key)
        body: dict[str, Any] = {"priority": priority}
        if title and title.strip():
            body["title"] = title
        if description:
            body["description"] = description
        if parent and parent.strip():
            parent_ticket = (await client.get_ticket_by_key(parent.strip().upper())).data
            body["parentId"] = parent_ticket["id"]
        if origin_keys:
            origin = (await client.get_ticket_by_key(origin_keys[0])).data
            body["followsTicketId"] = origin["id"]
            if to and to.strip():
                body["requestTarget"] = {"toAgent": to.strip(), "toModel": (model or "").strip() or None}
            elif request_builder:
                body["requestBuilder"] = True
        created = await client.request("POST", f"/projects/{path_segment(project['id'])}/tickets", json=body)
        key = render.ticket_key(project["key"], created.data)
        lines: list[str] = []
        ticket = created.data
        steps = [f"follows {k} 연결" for k in origin_keys[1:]] + ["티켓 다시 읽기"]
        if prd_page_id and prd_page_id.strip():
            steps.append("PRD 연결")
        if target_status:
            steps.append(f"상태 {target_status}로 변경")
        done = 0
        try:
            async with follow_up_deadline(started):
                for extra in origin_keys[1:]:
                    attach: dict[str, Any] = {"kind": "follows", "targetKey": extra}
                    if take_rejected:
                        attach["takeRejected"] = True
                    res = await client.request(
                        "POST", f"/tickets/{path_segment(created.data['id'])}/relations", json=attach
                    )
                    lines.append(_relation_summary(res.status, res.data))
                    done += 1
                ticket = (await client.get_ticket_by_key(key)).data
                done += 1
                if prd_page_id and prd_page_id.strip():
                    ticket = (await client.ticket_write(key, "PATCH", "", {"prd_page_id": prd_page_id.strip()})).data
                    done += 1
                if target_status and ticket.get("status") != target_status:
                    ticket = (await client.ticket_write(key, "PATCH", "", {"status": target_status})).data
        except FOLLOW_UP_ERRORS as exc:
            skipped = f"\n건너뜀: {', '.join(steps[done + 1 :])}" if steps[done + 1 :] else ""
            lines.append(f"{key} 생성됨, 그러나 {steps[done]} 실패:\n{str(exc) or type(exc).__name__}{skipped}")
        lines.insert(0, f"티켓 생성: {render.render_ticket_brief(key, ticket)}{render.hierarchy_suffix(ticket)}")
        lines += [f"경고: {w}" for w in created.warnings]
        return render.untrusted("ticket write", "\n".join(lines))

    @mcp.tool(structured_output=False, annotations=WRITE_IDEMPOTENT)
    @tool_errors
    async def relate_ticket(
        key: str, kind: str, target_key: str, reverse: bool = False, take_rejected: bool = False
    ) -> str:
        """Add relation `key kind target_key` (reverse=True: `target_key kind key`). kind=follows: key is a
        follow-up of target (several allowed); take_rejected=True copies the target's uncopied ✗ checklist rows, otherwise
        only their count is reported. kind=blocks: key must finish before target can start."""
        kind = check_choice("kind", kind, RELATION_KINDS)
        ticket = (await client.get_ticket_by_key(key)).data
        body: dict[str, Any] = {"kind": kind, "targetKey": target_key.strip().upper(), "reverse": reverse}
        if take_rejected:
            body["takeRejected"] = True
        res = await client.request("POST", f"/tickets/{path_segment(ticket['id'])}/relations", json=body)
        return render.untrusted("ticket write", _relation_summary(res.status, res.data))

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def update_ticket(
        key: str, status: str | None = None, assignee: str | None = None, due: str | None = None
    ) -> str:
        """Change status, assignee and/or due; omitted ones stay. status: backlog|todo|in_progress|review|done
        (canceled: web app; follow the workspace rules on who sets done). assignee: member name or user id. due: YYYY-MM-DD.
        "none" clears assignee or due."""
        body: dict[str, Any] = {}
        changes: list[str] = []
        if status is not None and status.strip():
            body["status"] = check_writable_status(status)
            changes.append(f"→ {body['status']}")
        if assignee is not None and assignee.strip():
            if assignee.strip().lower() in CLEAR_WORDS:
                body["assignee_id"] = None
                changes.append("담당자 → 없음")
            else:
                who = await client.resolve_assignee(assignee)
                body["assignee_id"] = who["id"]
                changes.append(f"담당자 → {who.get('name') or who['id']}")
        if due is not None and due.strip():
            if due.strip().lower() in CLEAR_WORDS:
                body["due_date"] = None
                changes.append("마감일 → 없음")
            else:
                body["due_date"] = check_due_date(due)
                changes.append(f"마감일 → {body['due_date']}")
        if not body:
            raise ValueError("status, assignee, due 중 하나가 필요함")
        await client.ticket_write(key, "PATCH", "", body)
        return render.untrusted("ticket write", f"{key.strip().upper()} " + " · ".join(changes))

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def add_note(key: str, body: str) -> str:
        """Add a markdown note to a ticket's timeline. Verification: add_verification; handing work on:
        add_handoff."""
        if not body.strip():
            raise ValueError("body가 비어 있음")
        response = await client.ticket_write(key, "POST", "/notes", {"body": body})
        client.remember_note(response.data, "manual")
        return f"노트 추가: {shown_id(response.data['id'])} ({key.strip().upper()})"

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def add_handoff(
        key: str,
        goal: str,
        done: str = "",
        remaining: list[str] | None = None,
        acceptance: list[str] | None = None,
        verification: str = "",
        to: str | None = None,
        model: str | None = None,
        role: str | None = None,
        trigger: str | None = None,
        pr_urls: list[str] | None = None,
        head_shas: list[str] | None = None,
    ) -> str:
        """Add a handoff note. goal is required. verification: how to verify. to: receiving agent label; model:
        undated model alias (usually empty). role: builder|verifier|planner. trigger: human|agent (default agent; a token's
        human is stored as agent). pr_urls / head_shas: the version to verify."""
        if not goal.strip():
            raise ValueError("goal이 비어 있음")
        handoff: dict[str, Any] = {
            "to_agent": to.strip() if to and to.strip() else None,
            "goal": goal,
            "done": done,
            "remaining": [r for r in (remaining or []) if r.strip()],
            "acceptance": [a for a in (acceptance or []) if a.strip()],
            "verification": verification,
        }
        if model and model.strip():
            handoff["to_model"] = model.strip()
        if role:
            handoff["requested_role"] = check_choice("role", role, HANDOFF_ROLES)
        if trigger:
            handoff["trigger"] = check_choice("trigger", trigger, HANDOFF_TRIGGERS)
        prs = [u.strip() for u in (pr_urls or []) if u.strip()]
        shas = [s.strip() for s in (head_shas or []) if s.strip()]
        if prs or shas:
            handoff["anchor"] = {"pr_urls": prs, "head_shas": shas}
        payload = {"kind": "handoff", "handoff": handoff}
        response = await client.ticket_write(key, "POST", "/notes", payload)
        client.remember_note(response.data, "handoff")
        return f"인계 노트 추가: {shown_id(response.data['id'])} ({key.strip().upper()})"

    # -- handoff delivery -----------------------------------------------
    # The server records the token's own label as the agent (`agent: null` in the body).

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_handoffs(to: str | None = None, state: str | None = None) -> str:
        """Handoff inbox, newest first. to: recipient label (aliases match); default SLASH_PRO_AGENT, else every
        recipient. state: `open,claimed` (default), any comma list or `all`. Lapsed claims reopen on read."""
        params: dict[str, Any] = {"limit": 50}
        recipient = (to or client.config.agent or "").strip()
        if recipient:
            params["to"] = recipient
        if state and state.strip():
            wanted = state.strip().lower()
            if wanted != "all":
                parts = [p.strip() for p in wanted.replace("|", ",").split(",") if p.strip()]
                unknown = [p for p in parts if p not in HANDOFF_STATES]
                if unknown:
                    raise ValueError(f"알 수 없는 state: {', '.join(unknown)} (허용: {', '.join(HANDOFF_STATES)})")
                wanted = ",".join(parts)
            params["state"] = wanted
        rows = await client.get_data("/handoffs", params=params)
        return render.untrusted("handoffs", render.render_handoffs(list(rows or [])))

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def update_handoff(
        handoff_id: str,
        action: Literal["claim", "heartbeat", "complete", "block"],
        note_id: str | None = None,
        reason: str | None = None,
    ) -> str:
        """claim: take an open handoff (2-hour lease; the error names a current holder), then get_ticket.
        heartbeat: extend your lease by 2 hours. complete: finish yours; note_id: the result note, optional.
        block: give yours up with a reason (required); a person reopens it."""
        if note_id and note_id.strip() and action != "complete":
            raise ValueError("note_id는 action=complete에서만 받음")
        if action == "block" and not (reason and reason.strip()):
            raise ValueError("reason이 비어 있음")
        if reason and reason.strip() and action != "block":
            raise ValueError("reason은 action=block에서만 받음")
        body: dict[str, Any] = {}
        if action == "complete":
            body["note_id"] = note_id.strip() if note_id and note_id.strip() else None
        elif action == "block":
            body["reason"] = reason
        path = f"/handoffs/{path_segment(handoff_id, 'handoff_id')}/{action}"
        response = await client.request("POST", path, json={"agent": None, **body})
        return render.untrusted("handoff", f"{action}: " + render.render_handoff_brief(response.data))

    @mcp.tool(structured_output=False, annotations=WRITE_IDEMPOTENT)
    @tool_errors
    async def mark_deployed(version: str, keys: list[str]) -> str:
        """Add a `<version>으로 배포됨` system note to each ticket (max 50); tickets that have it are
        skipped."""
        version = version.strip()
        if not version:
            raise ValueError("version이 비어 있음")
        marker = f"{version}으로 배포됨"
        lines: list[str] = []
        for key in check_keys(keys):
            try:
                ticket = (await client.get_ticket_by_key(key, include_notes=True)).data
            except SlashApiError as exc:
                if exc.status == 404:
                    lines.append(f"{key}: 없음 (건너뜀)")
                    continue
                raise
            notes = ticket.get("notes") or []
            if any(n.get("kind") == "system" and n.get("body") == marker for n in notes):
                lines.append(f"{key}: 이미 기록됨")
                continue
            await client.ticket_write(key, "POST", "/notes", {"kind": "system", "body": marker})
            lines.append(f"{key}: 기록함")
        return "\n".join(lines) if lines else "(티켓 없음)"
