"""Server configuration from environment variables (set them in the agent's MCP config).

| Setting      | Variable                                                     |
|--------------|--------------------------------------------------------------|
| API root     | `SLASH_PRO_API_URL`, default `DEFAULT_API_URL`               |
| Workspace id | `SLASH_PRO_WORKSPACE_ID`, required, a UUID                   |
| Token        | `SLASH_PRO_TOKEN`, else a token file: `SLASH_PRO_TOKEN_FILE`, |
|              | else `~/.config/slash-pro/token`                             |
| Agent label  | `SLASH_PRO_AGENT`                                            |
| Agent model  | `SLASH_PRO_AGENT_MODEL`                                      |
| Requested by | `SLASH_PRO_REQUESTED_BY`                                     |
| Rules        | `SLASH_PRO_WORKSPACE_RULES=0` skips the workspace rules page |
| Custom host  | `SLASH_PRO_ALLOW_CUSTOM_API=<host>` allows exactly that API  |
|              | host besides `echo-api.slash.pro`, loopback included         |
| Tool groups  | `SLASH_PRO_TOOLS=tickets,wiki` registers only those groups   |

The token is never logged, printed or included in `repr`. A custom API host (development /
self-hosting only, loopback included) accepts only a literal `SLASH_PRO_TOKEN`; no token file is
ever read for it, so a config change alone cannot send a saved token to another host.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

DEFAULT_API_URL = "https://echo-api.slash.pro"
DEFAULT_TOKEN_FILE = "~/.config/slash-pro/token"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
OFFICIAL_API_HOSTS = ("echo-api.slash.pro",)
MAX_TOKEN_FILE_BYTES = 8192
# `SLASH_PRO_TOOLS` names; server.GROUPS maps each to its register function.
TOOL_GROUPS = (
    "projects",
    "tickets",
    "ticket_extras",
    "questions",
    "meetings",
    "meeting_agenda",
    "wiki",
    "doc_updates",
    "prds",
    "outcomes",
    "search",
)
# scheme://host[:port][/path] — lowercase ASCII host or bracketed IPv6, plain path, nothing else
# (no userinfo, query, fragment, backslash, `;`, spaces or non-ASCII/IDN).
_API_URL = re.compile(
    r"(?P<scheme>https?)://(?P<host>[a-z0-9.-]+|\[[0-9a-f:.]+\])(?::(?P<port>[0-9]{1,5}))?(?P<path>/[A-Za-z0-9._~/-]*)?"
)


class ConfigError(RuntimeError):
    """A setting is missing or unsafe. The message never contains the token."""


@dataclass(frozen=True)
class SlashConfig:
    api: str
    workspace_id: str
    token: str = field(repr=False)
    # Explicit override only; by default the server derives the requester from the token owner.
    requested_by: str | None = None
    # The model actually serving this process, sent as `X-Agent-Model` on every request.
    model_id: str | None = None
    # Explicit inbox label for `list_handoffs` (SLASH_PRO_AGENT); None = no default recipient.
    agent: str | None = None
    # Load the workspace's agent-instructions page into the server instructions.
    workspace_rules: bool = True
    # Tool groups to register (`TOOL_GROUPS` names); None = all.
    tools: frozenset[str] | None = None


def _shown(url: str) -> str:
    """`scheme://host` only, for error messages: never include paths, queries or credentials."""
    try:
        parts = urlsplit(url)
        shown = f"{parts.scheme}://{parts.hostname or ''}"
    except ValueError:
        return "(unparseable)"
    return re.sub(r"[^A-Za-z0-9.:/\[\]-]", "?", shown)


def check_api_url(raw: str, *, allow_custom: str = "") -> tuple[str, bool]:
    """Validate the API root; returns `(url, is_custom_host)`.

    https only (http only for loopback), strict host charset, host limited to `OFFICIAL_API_HOSTS`
    unless `allow_custom` names exactly this host (loopback counts as custom: development only)."""
    url = raw.strip().rstrip("/")
    match = _API_URL.fullmatch(url)
    if not match:
        raise ConfigError(
            f"API URL must look like https://host[/path] with a lowercase ASCII host and no "
            f"credentials, query or fragment: {_shown(url)}"
        )
    host = match["host"].strip("[]")
    if match["port"] and not 0 < int(match["port"]) < 65536:
        raise ConfigError(f"API URL has an invalid port: {_shown(url)}")
    local = host in LOCAL_HOSTS
    if match["scheme"] == "http" and not local:
        raise ConfigError(f"API URL must use https (http is allowed only for localhost): {_shown(url)}")
    custom = host not in OFFICIAL_API_HOSTS
    if custom:
        opt_in = allow_custom.strip().lower()
        if opt_in == "1":
            raise ConfigError(
                "SLASH_PRO_ALLOW_CUSTOM_API must name the host it allows (e.g. SLASH_PRO_ALLOW_CUSTOM_API=localhost), not 1"
            )
        if opt_in.strip("[]") != host:
            raise ConfigError(
                f"API host is not the SLASH API: {_shown(url)} — set SLASH_PRO_ALLOW_CUSTOM_API to exactly "
                "this host (and give the token as a literal SLASH_PRO_TOKEN) if you really mean it"
            )
    return url, custom


def check_tool_groups(raw: str) -> frozenset[str] | None:
    """`"tickets, wiki"` → those groups; empty or `all` → None (every group)."""
    names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    if not names or "all" in names:
        return None
    unknown = sorted(names - set(TOOL_GROUPS))
    if unknown:
        raise ConfigError(
            f"unknown SLASH_PRO_TOOLS group: {', '.join(unknown)[:200]} (allowed: {', '.join(TOOL_GROUPS)})"
        )
    return frozenset(names)


def check_workspace_id(raw: str) -> str:
    try:
        return str(UUID(raw.strip()))
    except ValueError as exc:
        raise ConfigError(f"workspace id must be a UUID (got {len(raw.strip())} characters)") from exc


def check_token(token: str, source: str) -> str:
    token = token.strip()
    if not token:
        raise ConfigError(f"token from {source} is empty")
    # Printable ASCII without spaces: anything else is a paste error or header injection.
    if not (token.isascii() and token.isprintable()) or " " in token:
        raise ConfigError(f"token from {source} contains whitespace or control characters")
    return token


def read_private_file(path: Path, what: str, max_bytes: int) -> str:
    """Read a secrets file only if it is private to the user (POSIX) and small."""
    st = path.stat()
    if os.name == "posix" and st.st_mode & 0o077:
        raise ConfigError(f"{what} {path} is accessible by group/others — run: chmod 600 {path}")
    if st.st_size > max_bytes:
        raise ConfigError(f"{what} {path} is too large")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{what} {path} is not UTF-8 text") from exc


def read_token_file(path: Path) -> str:
    return check_token(read_private_file(path, "token file", MAX_TOKEN_FILE_BYTES), f"file {path}")


def load_config(environ: dict[str, str] | None = None) -> SlashConfig:
    env = os.environ if environ is None else environ

    def get(name: str) -> str:
        return (env.get(name) or "").strip()

    allow_custom = env.get("SLASH_PRO_ALLOW_CUSTOM_API") or ""
    api, custom = check_api_url(get("SLASH_PRO_API_URL") or DEFAULT_API_URL, allow_custom=allow_custom)
    workspace_raw = get("SLASH_PRO_WORKSPACE_ID")
    if not workspace_raw:
        raise ConfigError("SLASH_PRO_WORKSPACE_ID is required (the workspace UUID from the web app URL)")
    workspace_id = check_workspace_id(workspace_raw)

    if custom:
        # Development / self-hosting only: a literal token, never any token file.
        raw = (env.get("SLASH_PRO_TOKEN") or "").strip()
        if not raw:
            raise ConfigError(
                "a custom API host needs the token as a literal SLASH_PRO_TOKEN "
                "(token files are never sent to a custom host)"
            )
        token = check_token(raw, "SLASH_PRO_TOKEN")
    elif raw := get("SLASH_PRO_TOKEN"):
        token = check_token(raw, "SLASH_PRO_TOKEN")
    elif named := get("SLASH_PRO_TOKEN_FILE"):
        path = Path(named).expanduser()
        if not path.is_file():
            raise ConfigError(f"token file not found: {path}")
        token = read_token_file(path)
    else:
        default = Path(DEFAULT_TOKEN_FILE).expanduser()
        if default.is_file():
            token = read_token_file(default)
        else:
            raise ConfigError(
                "a token is required: set SLASH_PRO_TOKEN or SLASH_PRO_TOKEN_FILE, "
                f"or save it to {DEFAULT_TOKEN_FILE} (chmod 600)"
            )

    return SlashConfig(
        api=api,
        workspace_id=workspace_id,
        token=token,
        requested_by=get("SLASH_PRO_REQUESTED_BY") or None,
        model_id=get("SLASH_PRO_AGENT_MODEL") or None,
        agent=get("SLASH_PRO_AGENT") or None,
        tools=check_tool_groups(get("SLASH_PRO_TOOLS")),
        workspace_rules=(env.get("SLASH_PRO_WORKSPACE_RULES") or "").strip().lower() not in ("0", "false", "no", "off"),
    )
