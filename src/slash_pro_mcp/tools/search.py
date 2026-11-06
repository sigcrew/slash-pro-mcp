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
        """Workspace search over titles, bodies, transcripts and decisions; results contain every word.
        Needs wiki:read (plus the read scope of each kind to see its results).
        kinds: any of ticket|outcome|doc|meeting (default all; kinds the token cannot read are dropped).
        date_from / date_to: YYYY-MM-DD (Korea time) or ISO datetime. sort: relevance|recent.
        limit: 1-30 per kind (default 8)."""
        if not query.strip():
            raise ValueError("query must not be empty")
        params: dict[str, Any] = {"q": query.strip()[:200], "limit": max(1, min(int(limit), 30))}
        sort = sort.strip().lower()
        if sort not in ("relevance", "recent"):
            raise ValueError("sort must be relevance or recent")
        params["sort"] = sort
        wanted = [k.strip().lower() for k in kinds or [] if k.strip()]
        unknown = [k for k in wanted if k not in SEARCH_KINDS]
        if unknown:
            raise ValueError(f"unknown kind: {', '.join(unknown)} (allowed: {', '.join(SEARCH_KINDS)})")
        if wanted:
            params["kind"] = wanted
        if project_key:
            params["project"] = path_segment((await client.resolve_project(project_key))["id"])
        for name, value in (("from", date_from), ("to", date_to)):
            if value and value.strip():
                params[name] = value.strip()
        data = await client.get_data("/search", params=params) or {}
        return render.untrusted("search", render.render_search(data))
