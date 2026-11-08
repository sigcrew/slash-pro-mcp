"""Project tools: create_project / update_project (`projects:write` scope)."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import SlashClient, path_segment
from ..common import DESTRUCTIVE_IDEMPOTENT, WRITE, tool_errors

PROJECT_KEY = re.compile(r"[A-Z][A-Z0-9]{1,19}")


def register(mcp: FastMCP, client: SlashClient) -> None:
    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def create_project(key: str, name: str, description: str | None = None) -> str:
        """Create a project (needs projects:write). key: `^[A-Z][A-Z0-9]{1,19}$`, the KEY in KEY-N, fixed
        after creation."""
        key = key.strip().upper()
        if not PROJECT_KEY.fullmatch(key):
            raise ValueError("key는 ^[A-Z][A-Z0-9]{1,19}$ 형식이어야 함")
        if not name.strip():
            raise ValueError("name이 비어 있음")
        body: dict[str, Any] = {"key": key, "name": name.strip()}
        if description and description.strip():
            body["description"] = description
        created = (await client.request("POST", "/projects", json=body)).data
        return render.untrusted(
            "project write", f"프로젝트 생성: {created['key']} — {created['name']} `{created['id']}`"
        )

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def update_project(key: str, name: str | None = None, description: str | None = None) -> str:
        """Change a project's name and/or description (needs projects:write)."""
        body: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("name이 비어 있음")
            body["name"] = name.strip()
        if description is not None:
            body["description"] = description
        if not body:
            raise ValueError("name이나 description이 필요함")
        project = await client.resolve_project(key)
        updated = (await client.request("PATCH", f"/projects/{path_segment(project['id'])}", json=body)).data
        return render.untrusted("project write", f"프로젝트 수정: {updated['key']} — {updated['name']}")
