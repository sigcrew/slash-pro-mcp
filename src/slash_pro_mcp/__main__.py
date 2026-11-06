"""`slash-pro-mcp` / `python -m slash_pro_mcp` — run the Slash Pro MCP server over stdio."""

from __future__ import annotations

import asyncio
import logging
import sys

from . import __version__
from .client import EchoClient
from .config import ConfigError, EchoConfig, load_config
from .instructions import INSTRUCTIONS, load_instructions
from .server import build_server

USAGE = """\
usage: slash-pro-mcp [--help | --version]

Runs the Slash Pro MCP server over stdio. Configure it with environment variables:
  SLASH_PRO_WORKSPACE_ID  workspace UUID (required)
  SLASH_PRO_TOKEN         personal API token, or
  SLASH_PRO_TOKEN_FILE    path to a chmod 600 file holding it (default ~/.config/slash-pro/token)
  SLASH_PRO_API_URL       API root (default https://echo-api.slash.pro)
  SLASH_PRO_AGENT, SLASH_PRO_AGENT_MODEL, SLASH_PRO_REQUESTED_BY  optional labels
  SLASH_PRO_WORKSPACE_RULES=0    keep the workspace rules page out of the instructions
  SLASH_PRO_ALLOW_CUSTOM_API=<host>  dev/self-host: allow exactly this API host besides
                                     echo-api.slash.pro, loopback included (literal SLASH_PRO_TOKEN only)
See https://github.com/sigcrew/slash-pro-mcp#configuration
"""


# Clients give a server about 10 s to answer `initialize` (Codex's default); the rules page is
# optional, so a slow API must never hold up the handshake.
STARTUP_RULES_TIMEOUT_SECONDS = 3


async def _startup_instructions(config: EchoConfig) -> str:
    """Read the workspace rules with a throwaway client (the server's own client lives on
    FastMCP's event loop, not this one), within `STARTUP_RULES_TIMEOUT_SECONDS`."""
    client = EchoClient(config)
    try:
        async with asyncio.timeout(STARTUP_RULES_TIMEOUT_SECONDS):
            return await load_instructions(client)
    except TimeoutError:
        print(
            f"slash-pro-mcp: workspace rules not read within {STARTUP_RULES_TIMEOUT_SECONDS}s; starting without them",
            file=sys.stderr,
        )
        return INSTRUCTIONS
    finally:
        await client.aclose()


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0
    if args and args[0] in ("-V", "--version"):
        print(f"slash-pro-mcp {__version__}")
        return 0
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
    instructions = asyncio.run(_startup_instructions(config))
    server = build_server(EchoClient(config), instructions=instructions)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
