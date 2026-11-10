"""Workspace search (the web app's ⌘K): tickets, outcomes, docs and meetings by title and content."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashClient, path_segment
from ..common import READ, tool_errors

SEARCH_KINDS = ("ticket", "outcome", "doc", "meeting")


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=READ)
    @tool_errors
    async def search(
        query: str,
        kinds: list[str] | None = None,
        project_key: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        sort: str = "relevance",
        limit: int = 8,
    ) -> str:
        """Workspace search over titles, bodies, transcripts and decisions; every word must match. Needs document:read
        plus each kind's read scope. kinds: ticket|outcome|doc|meeting (default all). date_from / date_to: YYYY-MM-DD (KST)
        or ISO datetime. sort: relevance|recent. limit: max 30 per kind."""
        if not query.strip():
            raise ValueError("query가 비어 있음")
        params: dict[str, Any] = {"q": query.strip()[:200], "limit": max(1, min(int(limit), 30))}
        sort = sort.strip().lower()
        if sort not in ("relevance", "recent"):
            raise ValueError("sort는 relevance나 recent")
        params["sort"] = sort
        wanted = [k.strip().lower() for k in kinds or [] if k.strip()]
        unknown = [k for k in wanted if k not in SEARCH_KINDS]
        if unknown:
            raise ValueError(f"알 수 없는 kind: {', '.join(unknown)} (허용: {', '.join(SEARCH_KINDS)})")
        if wanted:
            params["kind"] = wanted
        if project_key:
            params["project"] = path_segment((await client.resolve_project(project_key))["id"])
        for name, value in (("from", date_from), ("to", date_to)):
            if value and value.strip():
                params[name] = value.strip()
        data = await client.get_data("/search", params=params) or {}
        return render.untrusted("search", render.render_search(data))
