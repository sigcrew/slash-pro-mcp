"""`slash-pro-mcp login`: device-flow token issuance (`POST /device-codes`, then poll `/device-codes/token`).

Needs no token or workspace. Exit codes: 0 saved, 1 denied, 2 usage/config/network error,
3 expired or timed out, 130 Ctrl-C. The token is only ever written to the token file, never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

import httpx

from . import __version__
from .client import SlashApiError, SlashClient
from .config import DEFAULT_TOKEN_FILE, ConfigError, SlashConfig, check_token, load_api_url, read_token_file

DEFAULT_SCOPES = "tickets:write,document:write,meetings:read"
SLOW_DOWN_SECONDS = 5
_SCOPE = re.compile(r"[a-z_]+:[a-z_]+")


def _sleep(seconds: float):  # tests replace this
    return asyncio.sleep(seconds)


def _show(value: object, limit: int = 200) -> str:
    """A server string for the terminal: printable characters only, so a reply cannot move the cursor."""
    return "".join(c for c in str(value) if c.isprintable())[:limit]


def _save(path: Path, token: str, force: bool) -> None:
    """Write `token` to `path` (0600, in a 0700 directory) via a temp file, then read it back."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".token-")  # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
        if force:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)  # fails if the file appeared meanwhile
            os.unlink(tmp)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    if read_token_file(path) != token:
        raise ConfigError(f"token file {path} did not read back correctly")


async def _run(
    args: argparse.Namespace, config: SlashConfig, path: Path, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    client = SlashClient(config, transport=transport)
    try:
        start = await client.request(
            "POST",
            "/device-codes",
            workspace=False,
            json={
                "label": args.label,
                "scopes": args.scopes,
                "expiresInDays": args.days,
                "clientName": f"slash-pro-mcp {__version__} on {socket.gethostname()}"[:100],
            },
        )
        grant = start.data if isinstance(start.data, dict) else {}
        device_code = grant.get("deviceCode")
        if not (isinstance(device_code, str) and device_code and grant.get("userCode")):
            raise ConfigError("unexpected reply from the server (is /device-codes deployed?)")
        print(f"Open: {_show(grant.get('verificationUriComplete') or grant.get('verificationUri'))}")
        print(f"Code: {_show(grant['userCode'])}")
        limit = int(grant.get("expiresIn") or 600)
        interval = max(1, int(grant.get("interval") or 5))
        print(f"waiting up to {limit // 60} min (Ctrl-C to cancel)", flush=True)
        waited = 0
        while waited < limit:
            await _sleep(interval)
            waited += interval
            reply = await client.request(
                "POST", "/device-codes/token", workspace=False, json={"deviceCode": device_code}
            )
            data = reply.data if isinstance(reply.data, dict) else {}
            status = data.get("status")
            if status == "pending":
                continue
            if status == "slow_down":
                interval += SLOW_DOWN_SECONDS
            elif status == "denied":
                print("denied in the browser", file=sys.stderr)
                return 1
            elif status == "expired":
                break
            elif status == "approved":
                token = data.get("token")
                if not isinstance(token, str):
                    raise ConfigError("approved reply has no token")
                _save(path, check_token(token, "the server reply"), args.force)
                scopes = ",".join(_show(s, 50) for s in data.get("scopes") or [])
                print(
                    f"saved {path} — workspace {_show(data.get('workspaceId'), 40)}, "
                    f"label {_show(data.get('label'), 50)}, scopes {scopes}"
                )
                return 0
            else:
                raise ConfigError("unexpected reply from the server")
        print("the code expired; run login again", file=sys.stderr)
        return 3
    finally:
        await client.aclose()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slash-pro-mcp login", description="Get a token by browser approval and save it.")
    p.add_argument("--label", default=(os.environ.get("SLASH_PRO_AGENT") or "MCP").strip()[:50], help="token label")
    p.add_argument("--scopes", default=DEFAULT_SCOPES, help=f"comma-separated (default {DEFAULT_SCOPES})")
    p.add_argument("--days", type=int, choices=(30, 90, 365), default=90, help="token lifetime")
    p.add_argument("--force", action="store_true", help="overwrite an existing token file")
    return p


def main(argv: list[str], transport: httpx.AsyncBaseTransport | None = None) -> int:
    args = _parser().parse_args(argv)  # exits 2 on bad usage
    args.scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    if not args.scopes or not all(_SCOPE.fullmatch(s) for s in args.scopes) or not 1 <= len(args.label) <= 50:
        print("slash-pro-mcp login: invalid --scopes or --label", file=sys.stderr)
        return 2
    try:
        api, custom = load_api_url(os.environ)
        named = (os.environ.get("SLASH_PRO_TOKEN_FILE") or "").strip()
        path = Path(named or DEFAULT_TOKEN_FILE).expanduser()
        if custom:
            raise ConfigError("a custom API host never reads a token file: give its token as SLASH_PRO_TOKEN instead")
        if os.path.lexists(path) and not args.force:
            raise ConfigError(f"{path} already exists: use --force to replace it")
        return asyncio.run(_run(args, SlashConfig(api=api, workspace_id=None, token=""), path, transport))
    except ConfigError as exc:
        print(f"slash-pro-mcp login: {exc}", file=sys.stderr)
    except SlashApiError as exc:
        print(f"slash-pro-mcp login: {exc}", file=sys.stderr)
    except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
        print(f"slash-pro-mcp login: {type(exc).__name__}", file=sys.stderr)
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    return 2
