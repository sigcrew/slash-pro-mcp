"""Wiki tools: tree/page/search (read) and write_wiki_page (write)."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashApiError, SlashClient, path_segment
from ..common import (
    DESTRUCTIVE,
    DESTRUCTIVE_IDEMPOTENT,
    READ,
    WIKI_SECTIONS,
    WRITE_IDEMPOTENT,
    check_choice,
    tool_errors,
)

MAX_SEARCH_PAGES = 20


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def wiki_tree(project_key: str) -> str:
        """A project's wiki page tree (title, type, page id)."""
        project = await client.resolve_project(project_key)
        pages = await client.get_data(f"/projects/{path_segment(project['id'])}/wiki/tree") or []
        return render.untrusted("wiki tree", render.render_wiki_tree(list(pages)))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def get_wiki_page(page_id: str, markdown: bool = False) -> str:
        """A wiki page with its plain-text body. markdown=True: only the body as markdown (the form
        write_wiki_page accepts)."""
        page_id = path_segment(page_id, "page_id")
        if markdown:
            text = await client.get_text(f"/wiki/pages/{page_id}", params={"format": "markdown"})
            return render.untrusted("wiki page", text)
        page = await client.get_data(f"/wiki/pages/{page_id}")
        return render.untrusted("wiki page", render.render_wiki_page(page))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def search_wiki(text: str, project_key: str | None = None, page_id: str | None = None) -> str:
        """Case-insensitive text search showing matching lines: in one page (page_id) or a project's pages
        (project_key; the first 20 matching pages are read)."""
        text = text.strip()[:200]
        if not text:
            raise ValueError("text가 비어 있음")
        if page_id:
            page = await client.get_data(f"/wiki/pages/{path_segment(page_id, 'page_id')}")
            return render.untrusted("wiki search", render.render_wiki_search(text, [page]))
        if not project_key:
            raise ValueError("project_key나 page_id가 필요함")
        project = await client.resolve_project(project_key)
        # `q` filters the tree on the server (ids and titles only); bodies are read page by page.
        found = await client.get_data(f"/projects/{path_segment(project['id'])}/wiki/tree", params={"q": text}) or []
        pages = [await client.get_data(f"/wiki/pages/{path_segment(p['id'])}") for p in found[:MAX_SEARCH_PAGES]]
        out = render.render_wiki_search(text, pages)
        if len(found) > MAX_SEARCH_PAGES:
            out += f"\n\n… 일치하는 페이지 {len(found) - MAX_SEARCH_PAGES}개 더 있음(표시 안 함): text를 좁히기"
        return render.untrusted("wiki search", out)

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE)
    @tool_errors
    async def write_wiki_page(page_id: str, markdown: str, mode: str = "append", source: str | None = None) -> str:
        """Write markdown to a page body. mode: append|replace (replace overwrites the whole body). source prepends a `> 출처:` block.
        The workspace rules page is refused."""
        mode = check_choice("mode", mode, ("append", "replace"))
        if not markdown.strip():
            raise ValueError("markdown이 비어 있음")
        body: dict[str, Any] = {"format": "markdown", "content": markdown, "mode": mode}
        if source:
            body["source"] = source
        response = await client.request("PUT", f"/wiki/pages/{await client.writable_page(page_id)}/content", json=body)
        page = response.data or {}
        return render.untrusted("wiki write", f"위키 {mode}: {page.get('title', page_id)} `{page.get('id', page_id)}`")

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE)
    @tool_errors
    async def create_wiki_page(
        project_key: str,
        section: str,
        title: str,
        markdown: str,
        source: str | None = None,
    ) -> str:
        """Create a markdown page under a section root: product|feature-specs|technical-docs|decisions|
        notes|perspectives. product is the project's single strategy page (title ignored): the body replaces a skeleton-only
        page, else is appended. PRDs: create_prd."""
        section = check_choice("section", section, tuple(WIKI_SECTIONS))
        if not title.strip():
            raise ValueError("title이 비어 있음")
        if not markdown.strip():
            raise ValueError("markdown이 비어 있음")
        project = await client.resolve_project(project_key)
        pid = path_segment(project["id"])
        body_extra: dict[str, Any] = {"source": source} if source else {}
        if section == "product":
            page = (await client.request("POST", f"/projects/{pid}/wiki/ensure-one-pager", json={})).data
            page_id = await client.writable_page(page["id"])
            # The server allows replacing a skeleton-only page; once people wrote in it, it answers
            # 409 `not_empty` and we append instead.
            payload = {"format": "markdown", "content": markdown, **body_extra}
            try:
                await client.request("PUT", f"/wiki/pages/{page_id}/content", json={**payload, "mode": "replace"})
            except SlashApiError as exc:
                if exc.status != 409 or exc.code != "not_empty":
                    raise
                await client.request("PUT", f"/wiki/pages/{page_id}/content", json={**payload, "mode": "append"})
            return render.untrusted(
                "wiki write", f"제품 전략 페이지 작성: {page.get('title', title.strip())} `{page_id}`"
            )
        await client.request("POST", f"/projects/{pid}/wiki/ensure-roots")
        tree = await client.get_data(f"/projects/{pid}/wiki/tree") or []
        want = WIKI_SECTIONS[section]
        root = next(
            (p for p in tree if render.wire_field(p, "parentId") is None and render.wire_field(p, "pageType") == want),
            None,
        )
        if root is None:
            raise ValueError(f"섹션 루트 없음: {section}")
        created = (
            await client.request(
                "POST",
                f"/projects/{pid}/wiki/pages",
                json={
                    "title": title.strip(),
                    "parentId": root["id"],
                    "format": "markdown",
                    "content": markdown,
                    **body_extra,
                },
            )
        ).data
        return render.untrusted(
            "wiki write", f"위키 페이지 생성: {created.get('title', title)} `{created['id']}` ({section})"
        )


def register_doc_updates(mcp: FastMCP, client: SlashClient) -> None:
    """AI doc-update proposals from meetings. Accept and revert change the document, so the
    target page goes through `writable_page` (never the workspace rules page)."""

    async def page_of(update_id: str, page_id: str) -> tuple[str, str]:
        """Check the update belongs to `page_id` (the only way to learn its page before acting)."""
        page = await client.writable_page(page_id)
        update = path_segment(update_id, "update_id")
        rows = await client.get_data(f"/wiki/pages/{page}/doc-updates") or []
        if not any(str(r.get("id")).lower() == update.lower() for r in rows):
            raise ValueError(
                f"업데이트 {update}: 페이지 {page}의 최근 200개 업데이트에 없음 "
                "(list_doc_updates로 page_id 확인, 더 오래된 것은 웹 앱에서)"
            )
        return update, page

    async def act(update: str, action: str) -> str:
        out = (await client.request("POST", f"/wiki/doc-updates/{update}/{action}")).data or {}
        return render.untrusted("doc update", f"{action}: " + render.doc_update_line(out))

    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def list_doc_updates(page_id: str | None = None, meeting_id: str | None = None) -> str:
        """AI doc updates from meetings, for one page_id or one meeting_id (exactly one; meeting_id needs
        wiki:read and meetings:read)."""
        if bool(page_id) == bool(meeting_id):
            raise ValueError("page_id와 meeting_id 중 하나만 지정")
        if page_id:
            path = f"/wiki/pages/{path_segment(page_id, 'page_id')}/doc-updates"
        else:
            path = f"/meetings/{path_segment(meeting_id, 'meeting_id')}/doc-updates"
        rows = await client.get_data(path) or []
        return render.untrusted("doc updates", render.render_doc_updates(list(rows)))

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE)
    @tool_errors
    async def resolve_doc_update(update_id: str, page_id: str, action: Literal["accept", "revert", "reject"]) -> str:
        """accept: insert a proposed update or unmark an applied one. revert: remove an applied one.
        reject: drop a proposed one. page_id: the update's page (list_doc_updates)."""
        update, _ = await page_of(update_id, page_id)
        return await act(update, action)

    @mcp.tool(structured_output=False, annotations=WRITE_IDEMPOTENT)
    @tool_errors
    async def refresh_doc_update(update_id: str) -> str:
        """Re-check a proposal against the latest page: content refreshed, or 409 duplicate|revoked."""
        return await act(path_segment(update_id, "update_id"), "refresh")

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def accept_all_doc_updates(page_id: str) -> str:
        """Accept every AI doc update of a page. The workspace rules page is refused."""
        page = await client.writable_page(page_id)
        response = await client.request("POST", f"/wiki/pages/{page}/doc-updates/accept-all")
        try:
            skipped = json.loads(response.text).get("skipped") or []
        except (ValueError, AttributeError):
            skipped = []
        lines = [f"수락 {len(response.data or [])}개, 건너뜀 {len(skipped)}개"]
        lines += [render.doc_update_line(r) for r in response.data or []]
        lines += [f"- 건너뜀 {s.get('id')}: {s.get('code')}" for s in skipped if isinstance(s, dict)]
        return render.untrusted("doc updates", "\n".join(lines))
