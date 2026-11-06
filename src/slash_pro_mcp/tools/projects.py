"""Project tools: create_project / update_project (`projects:write` scope)."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render
from ..client import EchoClient, path_segment
from ..common import DESTRUCTIVE_IDEMPOTENT, WRITE, tool_errors

PROJECT_KEY = re.compile(r"[A-Z][A-Z0-9]{1,19}")


def register(mcp: FastMCP, client: EchoClient) -> None:
    @mcp.tool(structured_output=False, annotations=WRITE)
    @tool_errors
    async def create_project(key: str, name: str, description: str | None = None) -> str:
        """Create a project. key: the KEY in ticket keys KEY-N, `^[A-Z][A-Z0-9]{1,19}$`, fixed after
        creation. Needs a token with `projects:write` (403 otherwise). Archiving and deleting are for people."""
        key = key.strip().upper()
        if not PROJECT_KEY.fullmatch(key):
            raise ValueError("key must match ^[A-Z][A-Z0-9]{1,19}$")
        if not name.strip():
            raise ValueError("name must not be empty")
        body: dict[str, Any] = {"key": key, "name": name.strip()}
        if description and description.strip():
            body["description"] = description
        created = (await client.request("POST", "/projects", json=body)).data
        return render.untrusted(
            "project write", f"Project created: {created['key']} — {created['name']} `{created['id']}`"
        )

    @mcp.tool(structured_output=False, annotations=DESTRUCTIVE_IDEMPOTENT)
    @tool_errors
    async def update_project(key: str, name: str | None = None, description: str | None = None) -> str:
        """Change a project's name and/or description (`projects:write`). Archiving and aliases are for
        people (403 for tokens)."""
        body: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be empty")
            body["name"] = name.strip()
        if description is not None:
            body["description"] = description
        if not body:
            raise ValueError("name or description is required")
        project = await client.resolve_project(key)
        updated = (await client.request("PATCH", f"/projects/{path_segment(project['id'])}", json=body)).data
        return render.untrusted("project write", f"Project updated: {updated['key']} — {updated['name']}")
