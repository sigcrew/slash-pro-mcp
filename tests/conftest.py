"""Fixtures for the MCP server tests.

The REST API is faked with an httpx `MockTransport`; nothing here touches the
network. Run with `uv run pytest -q`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio

from slash_pro_mcp.client import SlashClient
from slash_pro_mcp.config import SlashConfig
from tests.helpers import AGENT, API, TOKEN, WS, FakeSlash


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Point `~` at an empty directory so no test can read a real token file."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    for name in list(os.environ):
        if name.startswith("SLASH_PRO_"):
            monkeypatch.delenv(name)
    return home


@pytest.fixture
def fake() -> Iterator[FakeSlash]:
    api = FakeSlash()
    yield api
    # Contract with the server: every request a tool made exists for API tokens, with a scope.
    assert not api.unscoped, f"requests to routes without an API-token scope: {api.unscoped}"


@pytest.fixture
def config() -> SlashConfig:
    return SlashConfig(api=API, workspace_id=WS, token=TOKEN, agent=AGENT)


@pytest_asyncio.fixture
async def client(fake: FakeSlash, config: SlashConfig) -> AsyncIterator[SlashClient]:
    api = SlashClient(config, transport=httpx.MockTransport(fake.handle))
    try:
        yield api
    finally:
        await api.aclose()
