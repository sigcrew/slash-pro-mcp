"""`slash-pro-mcp login`: device flow against the fake API; the sleep is replaced so nothing waits."""

import argparse
import asyncio
import stat

import httpx
import pytest

from slash_pro_mcp import login
from slash_pro_mcp.client import SlashApiError
from slash_pro_mcp.config import ConfigError, SlashConfig
from tests.helpers import FakeSlash, json_response

NEW_TOKEN = "et_brand_new_token_value"
START = {
    "deviceCode": "dev-secret",
    "userCode": "BCDF-GHJK",
    "verificationUri": "https://app.test/connect",
    "verificationUriComplete": "https://app.test/connect?code=BCDF-GHJK",
    "expiresIn": 600,
    "interval": 5,
}
APPROVED = {
    "status": "approved",
    "token": NEW_TOKEN,
    "workspaceId": "ws-1",
    "label": "MCP",
    "scopes": ["tickets:write"],
}


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    seen: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(login, "_sleep", fake_sleep)
    return seen


def run(fake: FakeSlash, path, *, force: bool = False) -> int:
    args = argparse.Namespace(label="MCP", scopes=["tickets:write"], days=90, force=force)
    config = SlashConfig(api="https://api.test/api", workspace_id=None, token="")
    return asyncio.run(login._run(args, config, path, httpx.MockTransport(fake.handle)))


def serve(fake: FakeSlash, *polls: dict) -> None:
    fake.on("POST", "/api/device-codes", json_response(200, START))
    replies = iter(polls)
    fake.on("POST", "/api/device-codes/token", lambda _r: json_response(200, next(replies)))


def test_approved_writes_private_file_and_hides_token(fake, tmp_path, sleeps, capsys):
    serve(fake, APPROVED)
    path = tmp_path / "cfg" / "token"
    assert run(fake, path) == 0
    assert path.read_text() == NEW_TOKEN
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    out = capsys.readouterr()
    assert NEW_TOKEN not in out.out + out.err
    assert "BCDF-GHJK" in out.out and "saved" in out.out
    assert all("authorization" not in c.headers for c in fake.calls)
    assert fake.calls[0].body["scopes"] == ["tickets:write"] and fake.calls[0].body["expiresInDays"] == 90
    assert list(path.parent.iterdir()) == [path]  # no temp file left


def test_pending_then_slow_down_then_approved(fake, tmp_path, sleeps):
    serve(fake, {"status": "pending"}, {"status": "slow_down"}, APPROVED)
    assert run(fake, tmp_path / "token") == 0
    assert sleeps == [5, 5, 10]


def test_denied(fake, tmp_path, sleeps):
    serve(fake, {"status": "denied"})
    assert run(fake, tmp_path / "token") == 1
    assert not (tmp_path / "token").exists()


def test_expired_reply_and_timeout(fake, tmp_path, sleeps):
    serve(fake, {"status": "expired"})
    assert run(fake, tmp_path / "token") == 3
    fake.on("POST", "/api/device-codes/token", json_response(200, {"status": "pending"}))
    assert run(fake, tmp_path / "token") == 3  # gave up after expiresIn
    assert not (tmp_path / "token").exists()


def test_existing_file_refused_without_force(tmp_path, monkeypatch, capsys):
    path = tmp_path / "token"
    path.write_text("old")
    monkeypatch.setenv("SLASH_PRO_TOKEN_FILE", str(path))
    assert login.main([]) == 2
    assert path.read_text() == "old"
    assert "--force" in capsys.readouterr().err


def test_force_overwrites(fake, tmp_path, sleeps):
    path = tmp_path / "token"
    path.write_text("old")
    path.chmod(0o600)
    serve(fake, APPROVED)
    assert run(fake, path, force=True) == 0
    assert path.read_text() == NEW_TOKEN


def test_invalid_token_is_not_saved(fake, tmp_path, sleeps):
    serve(fake, {**APPROVED, "token": "bad token"})
    with pytest.raises(ConfigError):
        run(fake, tmp_path / "token")
    assert not (tmp_path / "token").exists()


def test_custom_host_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("SLASH_PRO_API_URL", "https://api.test/api")
    monkeypatch.setenv("SLASH_PRO_ALLOW_CUSTOM_API", "api.test")
    monkeypatch.setenv("SLASH_PRO_TOKEN_FILE", str(tmp_path / "token"))
    assert login.main([]) == 2


def test_transient_poll_errors_keep_waiting(fake, tmp_path, sleeps):
    fake.on("POST", "/api/device-codes", json_response(200, START))
    replies = iter(
        [json_response(503, {"detail": "busy"}), json_response(429, {"detail": "slow"}), json_response(200, APPROVED)]
    )
    fake.on("POST", "/api/device-codes/token", lambda _r: next(replies))
    assert run(fake, tmp_path / "token") == 0
    assert (tmp_path / "token").read_text() == NEW_TOKEN


def test_client_error_while_polling_stops(fake, tmp_path, sleeps):
    fake.on("POST", "/api/device-codes", json_response(200, START))
    fake.on("POST", "/api/device-codes/token", json_response(400, {"detail": "bad"}))
    with pytest.raises(SlashApiError):
        run(fake, tmp_path / "token")


def test_default_scopes_cover_the_core_tools():
    # The server never lets a write scope stand in for a read scope (document:write aside).
    from slash_pro_mcp.instructions import scope_note
    from slash_pro_mcp.login import DEFAULT_SCOPES

    assert scope_note(DEFAULT_SCOPES.split(","), frozenset({"tickets", "documents", "meetings"})) == ""
