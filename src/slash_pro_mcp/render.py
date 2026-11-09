"""Markdown renderers for tool results (pure functions, easy to test)."""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

UNTRUSTED_TAG = "untrusted-workspace-content"


def untrusted(source: str, text: str) -> str:
    """Fence content written by people (workspace members, meeting participants, server
    messages) so the agent reads it as data, not instructions. The tag carries a fresh
    random suffix per call, so content cannot forge the closing tag."""
    tag = f"{UNTRUSTED_TAG}-{secrets.token_hex(4)}"
    label = re.sub(r"[^A-Za-z0-9 _.-]", "", source)
    return f'<{tag} source="{label}">\n{text}\n</{tag}>'


def truncate(text: str, limit: int) -> str:
    """`text` cut to `limit` chars; untrusted fences opened in the kept part are closed, so the
    cut never leaves workspace content outside its fence."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    tags = re.findall(rf"<({UNTRUSTED_TAG}-[0-9a-f]{{8}}) ", head)
    closing = "".join(f"\n</{t}>" for t in reversed(tags) if f"</{t}>" not in head)
    return f"{head}{closing}\n… 잘림 (전체 {len(text)}자)"


def wire_field(row: dict, name: str, default: Any = None) -> Any:
    """REST uses camelCase; accept old snake_case fixtures/servers without losing 0/False."""
    first, *rest = name.split("_")
    alias = first + "".join(part.title() for part in rest)
    return row[alias] if alias in row else row.get(name, default)


def mmss(ms: int | None) -> str:
    if ms is None:
        return "--:--"
    total = max(0, int(ms)) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def ticket_key(project_key: str, ticket: dict) -> str:
    return f"{project_key.upper()}-{ticket['number']}"


def hierarchy_suffix(ticket: dict) -> str:
    """` ↳ CORE-9` for a sub-ticket, ` · 서브 2/5` for a parent
    (denominator leaves canceled subs out, like the board)."""
    parent = ticket.get("parent")
    if isinstance(parent, dict) and parent.get("key"):
        return f" ↳ {parent['key']}"
    subs = wire_field(ticket, "sub_tickets") or []
    counted = [s for s in subs if s.get("status") != "canceled"]
    if not counted:
        return ""
    done = sum(1 for s in counted if s.get("status") == "done")
    return f" · 서브 {done}/{len(counted)}"


def ticket_line(project_key: str, ticket: dict) -> str:
    flags = " ⛔" if wire_field(ticket, "is_blocked") else ""
    return (
        f"- {ticket_key(project_key, ticket)} · {ticket.get('status')} · "
        f"{ticket.get('priority')}{flags} — {ticket.get('title')}{hierarchy_suffix(ticket)}"
    )


def render_ticket_list(rows: list[tuple[str, dict]]) -> str:
    if not rows:
        return "(티켓 없음)"
    return "\n".join(ticket_line(project_key, ticket) for project_key, ticket in rows)


def render_prd_list(rows: list[dict]) -> str:
    """`- 제목 · done/전체 (id …)` per PRD."""
    if not rows:
        return "(PRD 없음)"
    return "\n".join(
        f"- {wire_field(r, 'title')} · {wire_field(r, 'ticket_done')}/{wire_field(r, 'ticket_total')}"
        f" (id {wire_field(r, 'page_id')})"
        for r in rows
    )


def render_ticket_brief(key: str, ticket: dict) -> str:
    return f"{key} · {ticket.get('status')} · {ticket.get('priority')} — {ticket.get('title')}"


def render_recording_sessions(sessions: list[dict] | None) -> str:
    """One line for the post-mortem entry point: count + last session's end."""
    if sessions is None:
        return "- 녹음 세션: 조회 불가"
    if not sessions:
        return "- 녹음 세션: 없음"
    last = sessions[-1]
    reason = wire_field(last, "end_reason") or "종료 기록 없음(서버 중단 또는 진행 중)"
    started = wire_field(last, "started_at") or "?"
    ended = wire_field(last, "ended_at") or "?"
    events = len(last.get("events") or [])
    return f"- 녹음 세션: {len(sessions)}개 · 마지막 종료 사유: {reason} ({started} → {ended}, 이벤트 {events})"


def render_meeting(
    meeting: dict,
    run: dict | None,
    versions: list[dict] | None,
    sessions: list[dict] | None = None,
) -> str:
    lines = [f"# {meeting.get('title')}", ""]
    lines.append(f"- id: {meeting.get('id')}")
    lines.append(
        f"- 일시: {wire_field(meeting, 'scheduled_at')} · {wire_field(meeting, 'duration_minutes')}분 예정"
        + (
            f" (실제 {wire_field(meeting, 'actual_duration_minutes')}분)"
            if wire_field(meeting, "actual_duration_minutes") is not None
            else ""
        )
    )
    attendees = meeting.get("attendees") or []
    names = [a.get("name") or a.get("email") or str(a.get("id")) for a in attendees]
    lines.append(f"- 참석자({len(names)}): {', '.join(names) if names else '없음'}")
    if meeting.get("description"):
        lines.append(f"- 설명: {meeting['description']}")

    transcription = meeting.get("transcription")
    if transcription:
        detail = []
        if wire_field(transcription, "duration_seconds") is not None:
            detail.append(f"{int(wire_field(transcription, 'duration_seconds')) // 60}분")
        if wire_field(transcription, "word_count") is not None:
            detail.append(f"{wire_field(transcription, 'word_count')}단어")
        suffix = f" ({', '.join(detail)})" if detail else ""
        lines.append(f"- 전사: 있음{suffix}")
    else:
        lines.append("- 전사: 없음")

    if run:
        run_line = f"- 최신 분석 run: {run.get('status')}"
        if run.get("stage"):
            run_line += f" / {run['stage']}"
        if wire_field(run, "finished_at"):
            run_line += f" · 완료 {wire_field(run, 'finished_at')}"
        if run.get("error"):
            run_line += f" · 오류: {run['error']}"
        lines.append(run_line)
    else:
        lines.append("- 최신 분석 run: 없음 (분석한 적 없음)")

    if versions is None:
        lines.append("- 분석 버전: 조회 불가 (토큰에 허용되지 않은 경로)")
    elif not versions:
        lines.append("- 분석 버전: 없음")
    else:
        summary = wire_field(versions[0], "one_line_summary")
        lines.append(f"- 최신 분석 버전 한 줄 요약: {summary}" if summary else "- 분석 버전: 있음")
    lines.append(render_recording_sessions(sessions))
    return "\n".join(lines)


def render_excerpts(candidates: list[dict]) -> str:
    if not candidates:
        return "(근거 후보 없음)"
    lines = []
    for candidate in candidates:
        lines.append(
            f"- [{mmss(wire_field(candidate, 'start_ms'))}–{mmss(wire_field(candidate, 'end_ms'))}] "
            f"{candidate.get('excerpt', '').strip()}"
        )
        if candidate.get("reason"):
            lines.append(f"  - 이유: {candidate['reason']}")
    return "\n".join(lines)


def render_document_tree(pages: list[dict]) -> str:
    if not pages:
        return "(위키 페이지 없음)"
    by_parent: dict[Any, list[dict]] = {}
    ids = {page["id"] for page in pages}
    for page in pages:
        parent = wire_field(page, "parent_id")
        by_parent.setdefault(parent if parent in ids else None, []).append(page)
    for children in by_parent.values():
        children.sort(key=lambda p: (wire_field(p, "sort_order", 0), p.get("title", "")))

    lines: list[str] = []

    def walk(parent: Any, depth: int) -> None:
        for page in by_parent.get(parent, []):
            lines.append(f"{'  ' * depth}- {page.get('title')} ({wire_field(page, 'page_type')}) `{page['id']}`")
            walk(page["id"], depth + 1)

    walk(None, 0)
    return "\n".join(lines)


def render_document(page: dict) -> str:
    header = f"# {page.get('title')}\n\n- id: {page.get('id')} · 유형: {wire_field(page, 'page_type')}"
    if wire_field(page, "meeting_id"):
        header += f" · 회의: {wire_field(page, 'meeting_id')}"
    header += f" · 수정: {wire_field(page, 'updated_at')}"
    body = (wire_field(page, "plain_text") or "").strip()
    return f"{header}\n\n{body if body else '(본문 없음)'}"


def matching_lines(text: str, query: str, context: int = 0) -> list[str]:
    needle = query.lower()
    lines = text.splitlines()
    hits: list[str] = []
    for index, line in enumerate(lines):
        if needle in line.lower():
            start, end = max(0, index - context), min(len(lines), index + context + 1)
            hits.append(f"L{index + 1}: " + " / ".join(s.strip() for s in lines[start:end]))
    return hits


def render_document_search(query: str, pages: list[dict]) -> str:
    """Pages whose title or plain text contains `query` (case-insensitive)."""
    blocks: list[str] = []
    for page in pages:
        title = page.get("title") or ""
        text = wire_field(page, "plain_text") or ""
        hits = matching_lines(text, query)
        if query.lower() not in title.lower() and not hits:
            continue
        block = [f"## {title} `{page['id']}`"]
        block.extend(f"- {hit}" for hit in hits[:10])
        if len(hits) > 10:
            block.append(f"- … 외 {len(hits) - 10}줄")
        blocks.append("\n".join(block))
    if not blocks:
        return f"'{query}' 일치 없음"
    return "\n\n".join(blocks)


def handoff_line(handoff: dict) -> str:
    """`- KEY · state · role · trigger — summary  (id …; 맡음 who, lease …)`."""
    role = handoff.get("requestedRole") or handoff.get("requested_role") or "-"
    trigger = handoff.get("trigger") or "agent"
    state = handoff.get("state") or "open"
    key = handoff.get("ticketKey") or handoff.get("ticket_key") or "?"
    summary = handoff.get("summary") or ""
    who = handoff.get("actorLabel") or handoff.get("actor_label") or "?"
    when = handoff.get("createdAt") or handoff.get("created_at") or ""
    tail = [f"id {handoff.get('id')}", f"{who} {when}".strip()]
    holder = handoff.get("claimedBy") or handoff.get("claimed_by")
    lease = handoff.get("leaseExpiresAt") or handoff.get("lease_expires_at")
    if holder:
        tail.append(f"맡음 {holder}" + (f" · lease {lease}" if lease else ""))
    reason = handoff.get("blockedReason") or handoff.get("blocked_reason")
    if reason:
        tail.append(f"막힘: {reason}")
    model = handoff.get("toModel") or handoff.get("to_model")
    if model:
        tail.append(f"모델 {model}")  # requested model, only when set
    blockers = wire_field(handoff, "blocked_by") or []
    if blockers:
        names = [
            f"수동 ({b.get('reason') or '-'})" if b.get("manual") else str(b.get("key"))
            for b in blockers
            if isinstance(b, dict)
        ]
        tail.append("기다리는 중 " + ", ".join(names))
    return f"- {key} · {state} · {role} · {trigger} — {summary}  ({'; '.join(tail)})"


def render_handoffs(rows: list[dict]) -> str:
    if not rows:
        return "(인계 없음)"
    return "\n".join(handoff_line(row) for row in rows)


def question_line(row: dict, *, with_key: bool) -> str:
    """`- [state · 항목] KEY 질문 (선택: …) — who when (id …) → /tickets?ticket=…`."""
    options = wire_field(row, "options") or []
    opts = f" (선택: {' / '.join(options)})" if options else ""
    head = f"- [{wire_field(row, 'state')} · {wire_field(row, 'rubric_label')}]"
    key = f" {wire_field(row, 'ticket_key')}" if with_key else ""
    return (
        f"{head}{key} {wire_field(row, 'question')}{opts} — "
        f"{wire_field(row, 'asked_by_label') or '?'} {wire_field(row, 'asked_at')} "
        f"(id {wire_field(row, 'id')}) → /tickets?ticket={wire_field(row, 'ticket_id')}"
    )


def render_questions(rows: list[dict]) -> str:
    if not rows:
        return "(질문 없음)"
    return "\n".join(question_line(row, with_key=True) for row in rows)


def render_handoff_brief(handoff: dict) -> str:
    key = handoff.get("ticketKey") or handoff.get("ticket_key") or "?"
    holder = handoff.get("claimedBy") or handoff.get("claimed_by")
    lease = handoff.get("leaseExpiresAt") or handoff.get("lease_expires_at")
    parts = [f"{key} 인계 {handoff.get('id')} → {handoff.get('state')}"]
    if holder:
        parts.append(f"맡음 {holder}")
    if lease and handoff.get("state") == "claimed":
        parts.append(f"lease {lease}")
    return " · ".join(parts)


# -- 회의 찾기 -------------------------------------------------------

MEETING_STATUS_LABELS = {
    "scheduled": "예정",
    "in_progress": "진행 중",
    "ended": "종료",
    "cancelled": "캘린더에서 취소됨",
}


def kst_minutes(value: str | None) -> str:
    """ISO 일시 → `YYYY-MM-DD HH:MM`(한국 시간). 못 읽으면 원문 그대로."""
    if not value:
        return "?"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("Asia/Seoul"))
    return parsed.strftime("%Y-%m-%d %H:%M")


def meeting_status(row: dict) -> str | None:
    """`status` from the list flags when present (upcoming rows), else the row's own `status`."""
    if wire_field(row, "calendar_cancelled_at"):
        return "cancelled"
    if wire_field(row, "has_ended"):
        return "ended"
    if wire_field(row, "has_started"):
        return "in_progress"
    if wire_field(row, "has_started") is False:
        return "scheduled"
    return wire_field(row, "status")


def meeting_row_line(row: dict) -> str:
    """`- 2026-10-13 10:00 · 데일리 스크럼 · 종료 · 참석 3 — 한 줄 요약 (id …)` (summary only when present)."""
    status = meeting_status(row)
    label = MEETING_STATUS_LABELS.get(status, status or "?")
    attendees = row.get("attendees") or []
    summary = wire_field(row, "one_line_summary")
    return (
        f"- {kst_minutes(wire_field(row, 'scheduled_at'))} · {row.get('title')} · {label}"
        f" · 참석 {len(attendees)}" + (f" — {summary}" if summary else "") + f" (id {row.get('id')})"
    )


def render_meeting_list(rows: list[dict]) -> str:
    if not rows:
        return "(회의 없음)"
    return "\n".join(meeting_row_line(row) for row in rows)


# -- outcomes ------------------------------------------------------------------

OUTCOME_STAGE_LABELS = {
    "draft": "초안",
    "executing": "실행 중",
    "observing": "결과 확인",
    "closed": "완료",
    "paused": "보류",
    "stopped": "중단",
}


def _ticket_refs(rows: list[dict] | None) -> list[str]:
    return [f"  - {t.get('key')} · {t.get('status')} — {t.get('title')}" for t in rows or []]


def outcome_line(row: dict) -> str:
    stage = wire_field(row, "stage")
    result = wire_field(row, "result_status")
    tail = f" · {result}" if result else ""
    return (
        f"- {row.get('key')} · {OUTCOME_STAGE_LABELS.get(stage, stage)}{tail} — {row.get('title')}"
        f" (티켓 {len(row.get('tickets') or [])}, id {row.get('id')})"
    )


def render_outcome_list(rows: list[dict]) -> str:
    if not rows:
        return "(아웃컴 없음)"
    return "\n".join(outcome_line(row) for row in rows)


def render_outcome(row: dict) -> str:
    stage = wire_field(row, "stage")
    lines = [
        f"# {row.get('key')} {row.get('title')}",
        "",
        f"- id: {row.get('id')} · revision {row.get('revision')}",
        f"- 상태: {OUTCOME_STAGE_LABELS.get(stage, stage)} · 결과: {wire_field(row, 'result_status') or '-'}",
        f"- 책임자: {wire_field(row, 'owner_name') or '없음'}",
        f"- 일정: {wire_field(row, 'start_date') or '?'} → {wire_field(row, 'target_date') or '?'}"
        f" · 검토 {wire_field(row, 'review_at') or '-'}",
    ]
    for label, name in (
        ("설명", "description"),
        ("범위", "scope"),
        ("검증 방법", "verification_method"),
        ("목표", "target"),
        ("투자 한도", "investment_limit"),
    ):
        value = wire_field(row, name)
        if value:
            lines += ["", f"## {label}", str(value)]
    lines += [
        "",
        f"## 소속 티켓 ({len(row.get('tickets') or [])})",
        *(_ticket_refs(row.get("tickets")) or ["  (없음)"]),
    ]
    evidence = row.get("evidence") or []
    if evidence:
        lines += ["", f"## 근거 ({len(evidence)})"]
        lines += [f"  - {e.get('body')}" + (f" <{e['url']}>" if e.get("url") else "") for e in evidence]
    judgements = row.get("judgements") or []
    if judgements:
        lines += ["", f"## 판정 ({len(judgements)})"]
        lines += [f"  - {j.get('result')}: {j.get('rationale')}" for j in judgements]
    return "\n".join(lines)


# -- ticket evidence -----------------------------------------------------------


def render_ticket_evidence(rows: list[dict]) -> str:
    if not rows:
        return "(근거 없음)"
    lines = []
    for e in rows:
        where = wire_field(e, "meeting_title") or wire_field(e, "meeting_id") or "-"
        at = (
            f" [{mmss(wire_field(e, 'start_ms'))}–{mmss(wire_field(e, 'end_ms'))}]"
            if wire_field(e, "start_ms") is not None
            else ""
        )
        speaker = f" {e['speaker']}:" if e.get("speaker") else ""
        lines.append(f"- {where}{at}{speaker} {e.get('excerpt')} ({e.get('source')}, id {e.get('id')})")
    return "\n".join(lines)


# -- search --------------------------------------------------------------------


def render_search(data: dict) -> str:
    results = data.get("results") or []
    if not results:
        return "(검색 결과 없음)" + (
            "" if wire_field(data, "content_ready", True) else " — 내용 색인 준비 전이라 제목만 찾았어요"
        )
    lines = []
    for r in results:
        key = wire_field(r, "ticket_key")
        label = f"{key} " if key else ""
        status = f" · {r['status']}" if r.get("status") else ""
        lines.append(
            f"- [{r.get('kind')}] {label}{r.get('title')}{status} · {wire_field(r, 'project_key') or '-'}"
            f" · {wire_field(r, 'date') or '-'} (id {r.get('id')})"
        )
        for hit in (r.get("hits") or [])[:3]:
            anchor = f" @{mmss(wire_field(hit, 'anchor_ms'))}" if wire_field(hit, "anchor_ms") is not None else ""
            lines.append(f"  - {wire_field(hit, 'item_kind')}{anchor}: {hit.get('snippet')}")
    return "\n".join(lines)


# -- doc updates ---------------------------------------------------------------


def node_text(nodes: Any) -> str:
    """Plain text of TipTap nodes (blocks joined by newlines)."""
    if isinstance(nodes, list):
        return "\n".join(t for t in (node_text(n) for n in nodes) if t)
    if not isinstance(nodes, dict):
        return ""
    if nodes.get("type") == "text":
        return str(nodes.get("text") or "")
    inner = nodes.get("content") or []
    sep = "\n" if nodes.get("type") in ("bulletList", "orderedList", "taskList", "doc") else ""
    return sep.join(t for t in (node_text(n) for n in inner) if t)


def doc_update_line(row: dict) -> str:
    text = node_text(wire_field(row, "new_content") or []).strip().replace("\n", " / ")
    if len(text) > 300:
        text = text[:300] + "…"
    meeting = wire_field(row, "meeting_title")
    return (
        f"- [{row.get('state')}] {row.get('op')} @ {wire_field(row, 'anchor_heading') or '-'}: {text or '(내용 없음)'}"
        f" (id {row.get('id')}, 페이지 {wire_field(row, 'page_id')}" + (f", 회의 {meeting}" if meeting else "") + ")"
    )


def render_doc_updates(rows: list[dict]) -> str:
    if not rows:
        return "(문서 업데이트 없음)"
    return "\n".join(doc_update_line(row) for row in rows)
