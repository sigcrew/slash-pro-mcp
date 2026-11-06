"""Server configuration from environment variables (set them in the agent's MCP config).

| Setting      | Variable (deprecated fallback)                              |
|--------------|-------------------------------------------------------------|
| API root     | `SLASH_PRO_API_URL` (`ECHO_API`), default `DEFAULT_API_URL`  |
| Workspace id | `SLASH_PRO_WORKSPACE_ID` (`ECHO_WS`), required, a UUID       |
| Token        | `SLASH_PRO_TOKEN` (`ECHO_TOKEN`), else a token file:         |
|              | `SLASH_PRO_TOKEN_FILE` (`ECHO_TOKEN_FILE`), else             |
|              | `~/.config/slash-pro/token`, else `~/.claude/echo-token`     |
| Agent label  | `SLASH_PRO_AGENT` (`ECHO_AGENT`)                             |
| Agent model  | `SLASH_PRO_AGENT_MODEL` (`ECHO_AGENT_MODEL`)                 |
| Requested by | `SLASH_PRO_REQUESTED_BY` (`ECHO_REQUESTED_BY`)               |
| Rules        | `SLASH_PRO_WORKSPACE_RULES=0` skips the workspace rules page |
| Custom host  | `SLASH_PRO_ALLOW_CUSTOM_API=<host>` allows exactly that API  |
|              | host besides `echo-api.slash.pro`, loopback included         |

The `ECHO_*` names and the `KEY=VALUE` file at `ECHO_ENV_FILE` (default
`~/.claude/echo-env`; only `ECHO_*` keys are read, and only when the workspace id or
a token source is still missing; refused if group/world-writable or not owned by the
user) are deprecated and only fill settings the new variables leave unset. The token is never logged, printed or
included in `repr`. A custom API host (development / self-hosting only, loopback included) accepts
only a literal `SLASH_PRO_TOKEN`; no token file is ever read for it, so a config
change alone cannot send a saved token to another host.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

DEFAULT_API_URL = "https://echo-api.slash.pro"
DEFAULT_TOKEN_FILE = "~/.config/slash-pro/token"
LEGACY_TOKEN_FILE = "~/.claude/echo-token"
LEGACY_ENV_FILE = "~/.claude/echo-env"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
OFFICIAL_API_HOSTS = ("echo-api.slash.pro",)
MAX_TOKEN_FILE_BYTES = 8192
MAX_ENV_FILE_BYTES = 65536
# scheme://host[:port][/path] — lowercase ASCII host or bracketed IPv6, plain path, nothing else
# (no userinfo, query, fragment, backslash, `;`, spaces or non-ASCII/IDN).
_API_URL = re.compile(
    r"(?P<scheme>https?)://(?P<host>[a-z0-9.-]+|\[[0-9a-f:.]+\])(?::(?P<port>[0-9]{1,5}))?(?P<path>/[A-Za-z0-9._~/-]*)?"
)


class ConfigError(RuntimeError):
    """A setting is missing or unsafe. The message never contains the token."""


@dataclass(frozen=True)
class EchoConfig:
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


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` lines (optional `export`, quotes, `#` comments)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def _shown(url: str) -> str:
    """`scheme://host` only, for error messages: never echo paths, queries or credentials."""
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
                f"API host is not the Slash Pro API: {_shown(url)} — set SLASH_PRO_ALLOW_CUSTOM_API to exactly "
                "this host (and give the token as a literal SLASH_PRO_TOKEN) if you really mean it"
            )
    return url, custom


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


def read_legacy_env_file(path: Path) -> dict[str, str]:
    """The deprecated `KEY=VALUE` file. Refused when group/world-writable or not owned by the
    user (it can point the token at another host). Group/world-readable is only a warning while
    it holds no token (just the API URL / workspace id); with `ECHO_TOKEN` in it, it is refused."""
    st = path.stat()
    if os.name == "posix" and (st.st_mode & 0o022 or st.st_uid != os.getuid()):
        raise ConfigError(f"env file {path} is writable by others or not owned by you — fix or remove it")
    if st.st_size > MAX_ENV_FILE_BYTES:
        raise ConfigError(f"env file {path} is too large")
    try:
        values = parse_env_file(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"env file {path} is not UTF-8 text") from exc
    if os.name == "posix" and st.st_mode & 0o077:
        if "ECHO_TOKEN" in values:
            raise ConfigError(
                f"env file {path} holds a token and is accessible by group/others — run: chmod 600 {path}"
            )
        print(f"slash-pro-mcp: warning: {path} is readable by group/others (run: chmod 600 {path})", file=sys.stderr)
    return values


def read_token_file(path: Path) -> str:
    return check_token(read_private_file(path, "token file", MAX_TOKEN_FILE_BYTES), f"file {path}")


def load_config(environ: dict[str, str] | None = None) -> EchoConfig:
    env = dict(os.environ if environ is None else environ)
    legacy_used: list[str] = []

    def has(*names: str) -> bool:
        return any((env.get(n) or "").strip() for n in names)

    token_source = has("SLASH_PRO_TOKEN", "ECHO_TOKEN", "SLASH_PRO_TOKEN_FILE", "ECHO_TOKEN_FILE") or any(
        Path(f).expanduser().is_file() for f in (DEFAULT_TOKEN_FILE, LEGACY_TOKEN_FILE)
    )
    legacy_file = Path(env.get("ECHO_ENV_FILE") or LEGACY_ENV_FILE).expanduser()
    # Only consult the deprecated file when something required is still missing.
    if not (has("SLASH_PRO_WORKSPACE_ID", "ECHO_WS") and token_source) and legacy_file.is_file():
        for key, value in read_legacy_env_file(legacy_file).items():
            if key.startswith("ECHO_") and key not in env:
                env[key] = value

    def get(name: str, legacy: str) -> str:
        value = (env.get(name) or "").strip()
        if value:
            return value
        value = (env.get(legacy) or "").strip()
        if value:
            legacy_used.append(legacy)
        return value

    allow_custom = env.get("SLASH_PRO_ALLOW_CUSTOM_API") or ""
    api, custom = check_api_url(get("SLASH_PRO_API_URL", "ECHO_API") or DEFAULT_API_URL, allow_custom=allow_custom)
    workspace_raw = get("SLASH_PRO_WORKSPACE_ID", "ECHO_WS")
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
    elif raw := get("SLASH_PRO_TOKEN", "ECHO_TOKEN"):
        token = check_token(raw, "SLASH_PRO_TOKEN")
    elif named := get("SLASH_PRO_TOKEN_FILE", "ECHO_TOKEN_FILE"):
        path = Path(named).expanduser()
        if not path.is_file():
            raise ConfigError(f"token file not found: {path}")
        token = read_token_file(path)
    else:
        default = Path(DEFAULT_TOKEN_FILE).expanduser()
        legacy = Path(LEGACY_TOKEN_FILE).expanduser()
        if default.is_file():
            token = read_token_file(default)
        elif legacy.is_file():
            legacy_used.append(LEGACY_TOKEN_FILE)
            token = read_token_file(legacy)
        else:
            raise ConfigError(
                "a token is required: set SLASH_PRO_TOKEN or SLASH_PRO_TOKEN_FILE, "
                f"or save it to {DEFAULT_TOKEN_FILE} (chmod 600)"
            )

    config = EchoConfig(
        api=api,
        workspace_id=workspace_id,
        token=token,
        requested_by=get("SLASH_PRO_REQUESTED_BY", "ECHO_REQUESTED_BY") or None,
        model_id=get("SLASH_PRO_AGENT_MODEL", "ECHO_AGENT_MODEL") or None,
        agent=get("SLASH_PRO_AGENT", "ECHO_AGENT") or None,
        workspace_rules=(env.get("SLASH_PRO_WORKSPACE_RULES") or "").strip().lower() not in ("0", "false", "no", "off"),
    )
    if legacy_used:
        print(
            f"slash-pro-mcp: deprecated settings in use ({', '.join(sorted(legacy_used))}) "
            "— switch to the SLASH_PRO_* variables",
            file=sys.stderr,
        )
    return config
