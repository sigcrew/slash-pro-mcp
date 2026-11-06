"""FastMCP server: one tool per Slash Pro REST call.

`build_server(client)` registers the tools as closures over an `EchoClient`
so tests can inject an httpx `MockTransport`. Tools return markdown; text that
comes from the workspace is fenced (`render.untrusted`). API failures surface as
tool errors carrying the server's `message`, `code` and `errors[]`, also fenced.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .client import EchoClient
from .instructions import INSTRUCTIONS
from .tools import meeting_agenda, meetings, outcomes, prds, projects, questions, search, ticket_extras, tickets, wiki

SERVER_NAME = "slash-pro"


def build_server(client: EchoClient, instructions: str = INSTRUCTIONS) -> FastMCP:
    """Create the FastMCP server with every tool group registered.

    `instructions` is the fixed text by default; `__main__` passes the
    composed text with the workspace's rules (`instructions.load_instructions`).
    """
    mcp = FastMCP(SERVER_NAME, instructions=instructions, log_level="WARNING")
    # FastMCP 1.x has no public setting for serverInfo.version (it would report the `mcp`
    # package's version); set it on the low-level server.
    mcp._mcp_server.version = __version__
    projects.register(mcp, client)
    tickets.register(mcp, client)
    meetings.register(mcp, client)
    wiki.register(mcp, client)
    prds.register(mcp, client)
    questions.register(mcp, client)
    meeting_agenda.register(mcp, client)
    outcomes.register(mcp, client)
    ticket_extras.register(mcp, client)
    search.register(mcp, client)
    wiki.register_doc_updates(mcp, client)
    # Some clients (Gemini CLI) mishandle `$ref`; inline the nested models of each input schema.
    # FastMCP 1.x has no public hook for this, hence the private tool manager.
    for tool in mcp._tool_manager.list_tools():
        tool.parameters = inline_refs(tool.parameters)
    return mcp


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` with every `{"$ref": "#/$defs/X"}` replaced by X (our models are not recursive)."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)
