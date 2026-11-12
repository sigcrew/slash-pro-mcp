"""Configuration: env vars, token file safety, URL and id validation."""

from pathlib import Path

import pytest

from slash_pro_mcp.config import (
    DEFAULT_API_URL,
    ConfigError,
    SlashConfig,
    load_config,
)

WS = "00000000-0000-7000-8000-000000000010"
TOKEN = "et_test_token_value"


def token_file(directory: Path, content: str = f"{TOKEN}\n", mode: int = 0o600) -> Path:
    path = directory / "token"
    path.write_text(content)
    path.chmod(mode)
    return path


def test_defaults_with_new_variables():
    config = load_config({"SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN": f" {TOKEN} "})
    assert config.api == DEFAULT_API_URL
    assert config.workspace_id == WS
    assert config.token == TOKEN
    assert config.requested_by is None
    assert config.model_id is None
    assert config.agent is None


def test_optional_labels_and_trailing_slash():
    config = load_config(
        {
            "SLASH_PRO_API_URL": "https://echo-api.slash.pro/api/",
            "SLASH_PRO_WORKSPACE_ID": WS.upper(),
            "SLASH_PRO_TOKEN": TOKEN,
            "SLASH_PRO_REQUESTED_BY": "봇",
            "SLASH_PRO_AGENT": "Codex",
            "SLASH_PRO_AGENT_MODEL": "gpt-6-astra",
        }
    )
    assert config.api == "https://echo-api.slash.pro/api"
    assert config.workspace_id == WS  # normalised
    assert config.requested_by == "봇"
    assert config.model_id == "gpt-6-astra"
    assert config.agent == "Codex"


def test_token_never_in_repr():
    config = SlashConfig(api=DEFAULT_API_URL, workspace_id=WS, token=TOKEN)
    assert TOKEN not in repr(config)
    assert TOKEN not in str(config)


def test_default_token_file(isolated_home: Path):
    new = isolated_home / ".config" / "slash-pro"
    new.mkdir(parents=True)
    token_file(new)
    assert load_config({"SLASH_PRO_WORKSPACE_ID": WS}).token == TOKEN


def test_missing_workspace_or_token_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="SLASH_PRO_WORKSPACE_ID is required"):
        load_config({"SLASH_PRO_TOKEN": TOKEN})
    with pytest.raises(ConfigError, match="token is required"):
        load_config({"SLASH_PRO_WORKSPACE_ID": WS})
    with pytest.raises(ConfigError, match="token file not found"):
        load_config({"SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN_FILE": str(tmp_path / "missing")})


@pytest.mark.parametrize("workspace", ["ws", "../x", "00000000-0000-7000-8000-00000000001g"])
def test_workspace_must_be_uuid(workspace: str):
    with pytest.raises(ConfigError, match="UUID") as excinfo:
        load_config({"SLASH_PRO_WORKSPACE_ID": workspace, "SLASH_PRO_TOKEN": TOKEN})
    assert workspace not in str(excinfo.value)  # never repeat the raw value (it may be a pasted secret)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "ftp://api.example.com",
        "api.example.com",
        "https://",
        "https://user:pass@api.example.com",
        "https://api.example.com/?x=1",
        "https://api.example.com/#frag",
        "https://api.example.com:notaport",
    ],
)
def test_unsafe_api_url_is_refused(url: str):
    with pytest.raises(ConfigError, match="API"):
        load_config({"SLASH_PRO_API_URL": url, "SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN": TOKEN})


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1:8000/api", "http://[::1]:8000"])
def test_loopback_is_a_custom_host_and_allows_plain_http(url: str, isolated_home: Path):
    env = {"SLASH_PRO_API_URL": url, "SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN": TOKEN}
    with pytest.raises(ConfigError, match="SLASH_PRO_ALLOW_CUSTOM_API"):
        load_config(env)
    host = url.split("//")[1].rsplit(":", 1)[0]
    assert load_config({**env, "SLASH_PRO_ALLOW_CUSTOM_API": host}).api == url
    # Like any custom host, loopback never gets a token file.
    new = isolated_home / ".config" / "slash-pro"
    new.mkdir(parents=True)
    token_file(new)
    del env["SLASH_PRO_TOKEN"]
    with pytest.raises(ConfigError, match="literal SLASH_PRO_TOKEN"):
        load_config({**env, "SLASH_PRO_ALLOW_CUSTOM_API": host})


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
def test_group_or_world_accessible_token_file_is_refused(tmp_path: Path, mode: int):
    path = token_file(tmp_path, mode=mode)
    with pytest.raises(ConfigError, match="chmod 600") as excinfo:
        load_config({"SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN_FILE": str(path)})
    assert TOKEN not in str(excinfo.value)


@pytest.mark.parametrize("bad", ["et_a\net_b", "et_a b", "et_\x00x", "et_\x1bx", "토큰"])
def test_token_with_whitespace_or_control_chars_is_refused(tmp_path: Path, bad: str):
    with pytest.raises(ConfigError, match="whitespace or control") as excinfo:
        load_config({"SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN": bad})
    assert bad not in str(excinfo.value)
    path = token_file(tmp_path, content=bad)
    with pytest.raises(ConfigError, match="whitespace or control"):
        load_config({"SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN_FILE": str(path)})


def base(**extra: str) -> dict[str, str]:
    return {"SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN": TOKEN, **extra}


@pytest.mark.parametrize(
    "url",
    [
        "https://slash.pro.evil.example.com",  # evil suffix
        "https://evilslash.pro",
        "https://echo-api.slash.pro\\@evil.example.com",  # backslash
        "https://echo-api.slash.pro;evil.example.com",
        "https://echo-api.slаsh.pro",  # Cyrillic а — IDN homograph
        "https://xn--echo-api-slsh-xyz.pro",
        "https://echo-api.slash.pro @evil.example.com",
        "https://EVIL.example.com",
    ],
)
def test_hosts_outside_slash_pro_are_refused(url: str):
    with pytest.raises(ConfigError) as excinfo:
        load_config(base(SLASH_PRO_API_URL=url))
    assert "@" not in str(excinfo.value) and ";" not in str(excinfo.value)


def test_error_shows_only_scheme_and_host():
    with pytest.raises(ConfigError) as excinfo:
        load_config(base(SLASH_PRO_API_URL="https://api.example.com/x?token=et_leak#frag"))
    assert "et_leak" not in str(excinfo.value)
    assert "https://api.example.com" in str(excinfo.value)


def test_only_the_official_api_host_is_allowed_without_opt_in():
    assert load_config(base(SLASH_PRO_API_URL="https://echo-api.slash.pro/v1")).api == "https://echo-api.slash.pro/v1"
    for url in ("https://slash.pro", "https://app.slash.pro", "https://a.b.slash.pro/api"):
        with pytest.raises(ConfigError, match="not the SLASH API"):
            load_config(base(SLASH_PRO_API_URL=url))


def test_custom_host_needs_an_opt_in_naming_it():
    with pytest.raises(ConfigError, match="SLASH_PRO_ALLOW_CUSTOM_API"):
        load_config(base(SLASH_PRO_API_URL="https://api.example.com"))
    with pytest.raises(ConfigError, match="must name the host"):
        load_config(base(SLASH_PRO_API_URL="https://api.example.com", SLASH_PRO_ALLOW_CUSTOM_API="1"))
    with pytest.raises(ConfigError, match="exactly"):
        load_config(base(SLASH_PRO_API_URL="https://api.example.com", SLASH_PRO_ALLOW_CUSTOM_API="other.example.com"))
    config = load_config(
        base(SLASH_PRO_API_URL="https://api.example.com", SLASH_PRO_ALLOW_CUSTOM_API="api.example.com")
    )
    assert config.api == "https://api.example.com"


def test_custom_host_accepts_only_a_literal_token(isolated_home: Path, tmp_path: Path):
    new = isolated_home / ".config" / "slash-pro"
    new.mkdir(parents=True)
    token_file(new)
    env = {
        "SLASH_PRO_WORKSPACE_ID": WS,
        "SLASH_PRO_API_URL": "https://api.example.com",
        "SLASH_PRO_ALLOW_CUSTOM_API": "api.example.com",
    }
    explicit = token_file(tmp_path, "et_explicit\n")
    for extra in (
        {},
        {"SLASH_PRO_TOKEN_FILE": str(explicit)},
    ):
        with pytest.raises(ConfigError, match="literal SLASH_PRO_TOKEN"):
            load_config({**env, **extra})
    assert load_config({**env, "SLASH_PRO_TOKEN": "et_literal"}).token == "et_literal"


def test_workspace_rules_opt_out():
    assert load_config(base()).workspace_rules is True
    assert load_config(base(SLASH_PRO_WORKSPACE_RULES="0")).workspace_rules is False


def test_tool_groups():
    assert load_config(base()).tools is None
    assert load_config(base(SLASH_PRO_TOOLS="all")).tools is None
    assert load_config(base(SLASH_PRO_TOOLS="all,tickets")).tools is None
    assert load_config(base(SLASH_PRO_TOOLS=" Tickets, documents ,")).tools == {"tickets", "documents"}
    with pytest.raises(ConfigError, match="unknown SLASH_PRO_TOOLS group: nope"):
        load_config(base(SLASH_PRO_TOOLS="tickets,nope"))


def test_oversized_token_file_is_refused(tmp_path: Path):
    path = token_file(tmp_path, "et_" + "a" * 9000)
    with pytest.raises(ConfigError, match="too large"):
        load_config({"SLASH_PRO_WORKSPACE_ID": WS, "SLASH_PRO_TOKEN_FILE": str(path)})
