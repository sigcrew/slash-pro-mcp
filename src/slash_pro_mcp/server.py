"""FastMCP server: one tool per SLASH REST call.

`build_server(client)` registers the tools as closures over an `SlashClient`
so tests can inject an httpx `MockTransport`. Tools return markdown; text that
comes from the workspace is fenced (`render.untrusted`). API failures surface as
tool errors carrying the server's `message`, `code` and `errors[]`, also fenced.
"""

from __future__ import annotations

import inspect
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .client import SlashClient
from .instructions import INSTRUCTIONS
from .tools import meeting_agenda, meetings, outcomes, prds, projects, questions, search, ticket_extras, tickets, wiki

SERVER_NAME = "slash-pro"

# Keys are `config.TOOL_GROUPS`, in registration order.
GROUPS = {
    "projects": projects.register,
    "tickets": tickets.register,
    "meetings": meetings.register,
    "wiki": wiki.register,
    "prds": prds.register,
    "questions": questions.register,
    "meeting_agenda": meeting_agenda.register,
    "outcomes": outcomes.register,
    "ticket_extras": ticket_extras.register,
    "search": search.register,
    "doc_updates": wiki.register_doc_updates,
}


def build_server(client: SlashClient, instructions: str = INSTRUCTIONS, tools: frozenset[str] | None = None) -> FastMCP:
    """Create the FastMCP server with the tool groups in `tools` registered (None = all).

    `instructions` is the fixed text by default; `__main__` passes the
    composed text with the workspace's rules (`instructions.load_instructions`).
    """
    mcp = FastMCP(SERVER_NAME, instructions=instructions, log_level="WARNING")
    # FastMCP 1.x has no public setting for serverInfo.version (it would report the `mcp`
    # package's version); set it on the low-level server.
    mcp._mcp_server.version = __version__
    for name, register in GROUPS.items():
        if tools is None or name in tools:
            register(mcp, client)
    # Some clients (Gemini CLI) mishandle `$ref`; inline the nested models of each input schema.
    # Every session pays for `tools/list` in context, so docstring indentation and pydantic's
    # generated titles ("Project Key" for project_key) are dropped too.
    # FastMCP 1.x has no public hook for this, hence the private tool manager.
    for tool in mcp._tool_manager.list_tools():
        tool.parameters = inline_refs(tool.parameters)
        tool.description = inspect.cleandoc(tool.description or "")
    return mcp


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` with every `{"$ref": "#/$defs/X"}` replaced by X (our models are not recursive) and
    the generated `title` of every node dropped (a property named `title` stays)."""
    defs = schema.get("$defs", {})

    def walk(node: Any, properties: bool = False) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            if properties:  # keys are parameter names
                return {k: walk(v) for k, v in node.items()}
            return {k: walk(v, k == "properties") for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)
