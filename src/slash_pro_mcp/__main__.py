"""`slash-pro-mcp` / `python -m slash_pro_mcp` — run the SLASH MCP server over stdio."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import replace
from uuid import UUID

import httpx

from . import __version__, login
from .client import SlashApiError, SlashClient
from .config import ConfigError, SlashConfig, load_config
from .instructions import INSTRUCTIONS, load_instructions, scope_note
from .server import build_server

USAGE = """\
usage: slash-pro-mcp [--help | --version]
       slash-pro-mcp login [--label TEXT] [--scopes a,b,...] [--days 30|90|365] [--force]

Runs the SLASH MCP server over stdio. `login` gets a token by browser approval and saves it
to SLASH_PRO_TOKEN_FILE (default ~/.config/slash-pro/token); it needs a server with /device-codes.
Configure it with environment variables:
  SLASH_PRO_WORKSPACE_ID  workspace UUID (optional; default: the token's workspace)
  SLASH_PRO_TOKEN         personal API token, or
  SLASH_PRO_TOKEN_FILE    path to a chmod 600 file holding it (default ~/.config/slash-pro/token)
  SLASH_PRO_API_URL       API root (default https://echo-api.slash.pro)
  SLASH_PRO_AGENT, SLASH_PRO_AGENT_MODEL, SLASH_PRO_REQUESTED_BY  optional labels
  SLASH_PRO_TOOLS=tickets,documents  register only these tool groups (default: all)
  SLASH_PRO_WORKSPACE_RULES=0    keep the workspace rules page out of the instructions
  SLASH_PRO_ALLOW_CUSTOM_API=<host>  dev/self-host: allow exactly this API host besides
                                     echo-api.slash.pro, loopback included (literal SLASH_PRO_TOKEN only)
See https://github.com/sigcrew/slash-pro-mcp#configuration
"""


# Clients give a server about 10 s to answer `initialize` (Codex's default). One deadline covers the
# token lookup and the optional rules page, so a slow API never holds up the handshake.
STARTUP_TIMEOUT_SECONDS = 5
TOKEN_INFO_PATH = "/api-tokens/me"


async def _token_info(client: SlashClient) -> dict | None:
    """`GET /api-tokens/me` (`{workspaceId, userId, name, scopes, …}`), or None when the workspace
    is already configured and the server cannot answer (older server, network, other errors)."""
    configured = client.config.workspace_id
    try:
        data = await client.get_data(TOKEN_INFO_PATH, workspace=False)
        if not isinstance(data, dict):
            raise ValueError("unexpected /api-tokens/me reply")
        return {**data, "workspaceId": str(UUID(str(data.get("workspaceId"))))}
    except SlashApiError as exc:
        if exc.status == 401:
            raise ConfigError(
                "the token is invalid, expired (90 days) or revoked: issue a new one in the web app"
            ) from exc
        if exc.status == 403:
            raise ConfigError("the token's workspace membership is inactive") from exc
        if not configured:
            hint = "this SLASH server has no /api-tokens/me" if exc.status == 404 else f"HTTP {exc.status}"
            raise ConfigError(f"could not read the token's workspace ({hint}): set SLASH_PRO_WORKSPACE_ID") from exc
        reason = f"HTTP {exc.status}"
    except (httpx.HTTPError, ValueError) as exc:
        if not configured:
            raise ConfigError(
                f"could not read the token's workspace ({type(exc).__name__}): set SLASH_PRO_WORKSPACE_ID"
            ) from exc
        reason = type(exc).__name__
    if reason != "HTTP 404":  # an older server without the route is expected; stay quiet
        print(f"slash-pro-mcp: token info not read ({reason}); using SLASH_PRO_WORKSPACE_ID", file=sys.stderr)
    return None


async def _startup(
    config: SlashConfig, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[SlashConfig, str, dict | None]:
    """Resolve the token's workspace, then read the workspace rules, with a throwaway client (the
    server's own client lives on FastMCP's event loop, not this one), all within
    `STARTUP_TIMEOUT_SECONDS`. Returns the config with its workspace id, the instructions and the
    token owner `{id, name}` (None if unknown). Raises `ConfigError` when the workspace is unknown
    or the token is refused."""
    client = SlashClient(config, transport=transport)
    owner = None
    instructions = INSTRUCTIONS
    try:
        async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
            info = await _token_info(client)
            if info is not None:
                workspace = info["workspaceId"]
                if config.workspace_id and config.workspace_id != workspace:
                    raise ConfigError(
                        f"the token belongs to workspace {workspace}, not SLASH_PRO_WORKSPACE_ID "
                        f"{config.workspace_id}: fix the variable or use that workspace's token"
                    )
                client.config = config = replace(config, workspace_id=workspace)
                if info.get("userId"):
                    owner = {"id": str(info["userId"]), "name": info.get("name")}
                instructions += scope_note(info.get("scopes"), config.tools)
            instructions = await load_instructions(client, instructions)
    except TimeoutError:
        if not config.workspace_id:
            raise ConfigError(
                f"the token's workspace was not read within {STARTUP_TIMEOUT_SECONDS}s: "
                "set SLASH_PRO_WORKSPACE_ID or retry"
            ) from None
        print(
            f"slash-pro-mcp: workspace rules not read within {STARTUP_TIMEOUT_SECONDS}s; starting without them",
            file=sys.stderr,
        )
    finally:
        await client.aclose()
    return config, instructions, owner


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0
    if args and args[0] in ("-V", "--version"):
        print(f"slash-pro-mcp {__version__}")
        return 0
    if args and args[0] == "login":
        return login.main(args[1:])
    if args:
        print(USAGE, end="", file=sys.stderr)
        return 2
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"slash-pro-mcp: {exc}", file=sys.stderr)
        return 2
    # httpx/httpcore log request lines at INFO/DEBUG; keep stdio's stderr quiet and never
    # let a debug logger near the request headers.
    for name in ("httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        config, instructions, owner = asyncio.run(_startup(config))
    except ConfigError as exc:
        print(f"slash-pro-mcp: {exc}", file=sys.stderr)
        return 2
    server = build_server(SlashClient(config, me=owner), instructions=instructions, tools=config.tools)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
