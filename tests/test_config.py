"""Configuration: env vars, legacy fallbacks, token file safety, URL and id validation."""

from pathlib import Path

import pytest

from slash_pro_mcp.config import (
    DEFAULT_API_URL,
    ConfigError,
    EchoConfig,
    load_config,
    parse_env_file,
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
    config = EchoConfig(api=DEFAULT_API_URL, workspace_id=WS, token=TOKEN)
    assert TOKEN not in repr(config)
    assert TOKEN not in str(config)


def test_legacy_names_and_env_file_still_work(tmp_path: Path, capsys):
    env_file = tmp_path / "echo-env"
    env_file.write_text(f"ECHO_API=https://echo-api.slash.pro/legacy\nECHO_WS={WS}\nOTHER=ignored\n")
    env_file.chmod(0o600)
    config = load_config(
        {"ECHO_ENV_FILE": str(env_file), "ECHO_TOKEN_FILE": str(token_file(tmp_path)), "ECHO_AGENT": "Codex"}
    )
    assert config.api == "https://echo-api.slash.pro/legacy"
    assert config.workspace_id == WS
    assert config.token == TOKEN
    assert config.agent == "Codex"
    err = capsys.readouterr().err
    assert "deprecated" in err and "ECHO_WS" in err
    assert TOKEN not in err


def test_new_variables_win_over_legacy(tmp_path: Path):
    config = load_config(
        {
            "SLASH_PRO_WORKSPACE_ID": WS,
            "ECHO_WS": "00000000-0000-7000-8000-000000000011",
            "SLASH_PRO_TOKEN": TOKEN,
            "ECHO_TOKEN": "et_old",
        }
    )
    assert config.workspace_id == WS
    assert config.token == TOKEN


def test_default_token_file_then_legacy_file(isolated_home: Path):
    legacy = isolated_home / ".claude"
    legacy.mkdir()
    token_file(legacy, "et_legacy\n").rename(legacy / "echo-token")
    assert load_config({"SLASH_PRO_WORKSPACE_ID": WS}).token == "et_legacy"

    new = isolated_home / ".config" / "slash-pro"
    new.mkdir(parents=True)
    token_file(new)
    assert load_config({"SLASH_PRO_WORKSPACE_ID": WS}).token == TOKEN


def test_parse_env_file_handles_export_quotes_and_comments():
    text = "# comment\nexport ECHO_API='https://a/api'\nECHO_WS=\"ws-1\"\nBROKEN\n\n"
    assert parse_env_file(text) == {"ECHO_API": "https://a/api", "ECHO_WS": "ws-1"}
    assert parse_env_file("ECHO_WS=fixture # inline comment\n") == {"ECHO_WS": "fixture"}
    assert parse_env_file("ECHO_WS='a # not a comment'\n") == {"ECHO_WS": "a # not a comment"}


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
    assert workspace not in str(excinfo.value)  # never echo the raw value (it may be a pasted secret)


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
        with pytest.raises(ConfigError, match="not the Slash Pro API"):
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
    legacy = isolated_home / ".claude"
    legacy.mkdir()
    token_file(legacy).rename(legacy / "echo-token")
    env = {
        "SLASH_PRO_WORKSPACE_ID": WS,
        "SLASH_PRO_API_URL": "https://api.example.com",
        "SLASH_PRO_ALLOW_CUSTOM_API": "api.example.com",
    }
    explicit = token_file(tmp_path, "et_explicit\n")
    for extra in (
        {},
        {"ECHO_TOKEN": TOKEN},
        {"SLASH_PRO_TOKEN_FILE": str(explicit)},
        {"ECHO_TOKEN_FILE": str(explicit)},
    ):
        with pytest.raises(ConfigError, match="literal SLASH_PRO_TOKEN"):
            load_config({**env, **extra})
    assert load_config({**env, "SLASH_PRO_TOKEN": "et_literal"}).token == "et_literal"


def test_workspace_rules_opt_out():
    assert load_config(base()).workspace_rules is True
    assert load_config(base(SLASH_PRO_WORKSPACE_RULES="0")).workspace_rules is False


def legacy_env(directory: Path, content: str, mode: int) -> Path:
    path = directory / "echo-env"
    path.write_text(content)
    path.chmod(mode)
    return path


def test_legacy_env_file_is_ignored_when_nothing_is_missing(tmp_path: Path):
    # Unreadable-by-policy (0644, holds a token) — but never opened because nothing is missing.
    path = legacy_env(tmp_path, f"ECHO_TOKEN=et_old\nECHO_API=https://echo-api.slash.pro/old\nECHO_WS={WS}\n", 0o644)
    config = load_config({**base(), "ECHO_ENV_FILE": str(path)})
    assert config.api == DEFAULT_API_URL and config.token == TOKEN


def test_world_readable_legacy_env_without_token_only_warns(tmp_path: Path, capsys):
    path = legacy_env(tmp_path, f"ECHO_API=https://echo-api.slash.pro/legacy\nECHO_WS={WS}\n", 0o644)
    config = load_config({"ECHO_ENV_FILE": str(path), "SLASH_PRO_TOKEN": TOKEN})
    assert config.workspace_id == WS and config.api == "https://echo-api.slash.pro/legacy"
    assert "readable by group/others" in capsys.readouterr().err


def test_world_readable_legacy_env_with_token_is_refused(tmp_path: Path):
    path = legacy_env(tmp_path, f"ECHO_WS={WS}\nECHO_TOKEN=et_old\n", 0o644)
    with pytest.raises(ConfigError, match="holds a token") as excinfo:
        load_config({"ECHO_ENV_FILE": str(path)})
    assert "et_old" not in str(excinfo.value)
    path.chmod(0o600)
    assert load_config({"ECHO_ENV_FILE": str(path)}).token == "et_old"


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_writable_legacy_env_is_refused(tmp_path: Path, mode: int):
    path = legacy_env(tmp_path, f"ECHO_API=https://echo-api.slash.pro/legacy\nECHO_WS={WS}\n", mode)
    with pytest.raises(ConfigError, match="writable by others or not owned"):
        load_config({"ECHO_ENV_FILE": str(path), "SLASH_PRO_TOKEN": TOKEN})


def test_legacy_env_owned_by_someone_else_is_refused(tmp_path: Path, monkeypatch):
    path = legacy_env(tmp_path, f"ECHO_WS={WS}\n", 0o600)
    monkeypatch.setattr("os.getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(ConfigError, match="writable by others or not owned"):
        load_config({"ECHO_ENV_FILE": str(path), "SLASH_PRO_TOKEN": TOKEN})


def test_legacy_env_file_must_be_utf8(tmp_path: Path):
    path = legacy_env(tmp_path, "", 0o600)
    path.write_bytes(b"ECHO_WS=\xff\xfe\n")
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config({"ECHO_ENV_FILE": str(path), "SLASH_PRO_TOKEN": TOKEN})
