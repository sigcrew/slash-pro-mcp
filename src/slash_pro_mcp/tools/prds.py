"""PRD tools: list_prds / get_prd / create_prd / assign_ticket_prd."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashClient, path_segment
from ..common import (
    DESTRUCTIVE_IDEMPOTENT,
    FOLLOW_UP_ERRORS,
    MAX_LINKED_KEYS,
    READ,
    WRITE,
    check_keys,
    follow_up_deadline,
    tool_errors,
)

# The server's current PRD skeleton headings, by argument.
PRD_HEADINGS = {
    "problem": "문제",
    "success": "성공 한 문장",
    "scope": "투입 범위 · 식욕",
    "approach": "접근과 핵심 흐름",
    "risks": "토끼굴 · 주의할 점",
    "out_of_scope": "범위 밖",
    "verification": "결과 확인",
    "open_questions": "미결 사항",
}


def build_sections(**values: str | list[str] | None) -> dict[str, str]:
    """Skeleton heading -> markdown body; only non-empty sections are sent. A list becomes bullets."""
    sections: dict[str, str] = {}
    for name, heading in PRD_HEADINGS.items():
        value = values.get(name)
        if isinstance(value, list):
            value = "\n".join(f"- {i.strip()}" for i in value if i.strip())
        if value and value.strip():
            sections[heading] = value.strip()
    return sections


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_prds(project_key: str) -> str:
        """A project's PRDs; the id is for get_prd, create_ticket and assign_ticket_prd."""
        project = await client.resolve_project(project_key)
        rows = await client.get_data(f"/projects/{path_segment(project['id'])}/prds")
        return render.untrusted("prd list", render.render_prd_list(list(rows or [])))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_prd(prd_page_id: str) -> str:
        """Full PRD as markdown plus its member tickets (get_ticket shows only a 4,000-char summary)."""
        text = await client.get_text(f"/prds/{path_segment(prd_page_id, 'prd_page_id')}", params={"format": "markdown"})
        return render.untrusted("prd", text)

    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def create_prd(
        project_key: str,
        title: str,
        problem: str | None = None,
        success: str | None = None,
        scope: list[str] | None = None,
        approach: str | None = None,
        risks: list[str] | None = None,
        out_of_scope: list[str] | None = None,
        verification: str | None = None,
        open_questions: list[str] | None = None,
        ticket_keys: list[str] | None = None,
        markdown: str | None = None,
    ) -> str:
        """Create a PRD under the product strategy page. Section arguments fill the skeleton: problem, success
        (one sentence), scope (appetite), approach, risks, out_of_scope, verification, open_questions; lists become bullets.
        markdown instead replaces the whole body (not with sections). ticket_keys (max 20) are attached after creation."""
        if not title.strip():
            raise ValueError("title이 비어 있음")
        started = asyncio.get_running_loop().time()
        keys = check_keys(ticket_keys, MAX_LINKED_KEYS)
        sections = build_sections(
            problem=problem,
            success=success,
            scope=scope,
            approach=approach,
            risks=risks,
            out_of_scope=out_of_scope,
            verification=verification,
            open_questions=open_questions,
        )
        if markdown is not None and not markdown.strip():
            raise ValueError("markdown이 비어 있음")
        if markdown and sections:
            raise ValueError("markdown은 섹션 인자와 함께 쓸 수 없음")
        project = await client.resolve_project(project_key)
        body: dict[str, Any] = {"title": title.strip()}
        if sections:
            body["sections"] = sections
        created = (await client.request("POST", f"/projects/{path_segment(project['id'])}/prds", json=body)).data
        page_id = render.wire_field(created, "pageId")
        lines = [f"PRD 생성: {render.wire_field(created, 'title')} (id {page_id})"]
        assigned: list[str] = []
        steps = (["markdown 본문 쓰기"] if markdown else []) + [f"{key} 연결" for key in keys]
        done = 0
        try:
            async with follow_up_deadline(started):
                if markdown:
                    await client.request(
                        "PUT",
                        f"/wiki/pages/{await client.writable_page(page_id)}/content",
                        json={"format": "markdown", "content": markdown, "mode": "replace"},
                    )
                    done += 1
                for key in keys:
                    await client.ticket_write(key, "PATCH", "", {"prd_page_id": page_id})
                    assigned.append(key)
                    done += 1
        except FOLLOW_UP_ERRORS as exc:
            skipped = f"\n건너뜀: {', '.join(steps[done + 1 :])}" if steps[done + 1 :] else ""
            lines.append(f"PRD {page_id} 생성됨, 그러나 {steps[done]} 실패:\n{str(exc) or type(exc).__name__}{skipped}")
        if assigned:
            lines.insert(1, f"연결: {', '.join(assigned)}")
        return render.untrusted("prd write", "\n".join(lines))

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def assign_ticket_prd(key: str, prd_page_id: str | None = None) -> str:
        """Attach a ticket to a PRD of the same project; omit prd_page_id to detach."""
        value = prd_page_id.strip() if prd_page_id and prd_page_id.strip() else None
        response = await client.ticket_write(key, "PATCH", "", {"prd_page_id": value})
        prd = (response.data or {}).get("prd")
        label = render.wire_field(prd, "title") if prd else "없음"
        return render.untrusted("prd write", f"{key.strip().upper()} → PRD {label}")
