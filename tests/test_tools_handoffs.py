"""list_handoffs / update_handoff (claim / heartbeat / complete / block), and the delivery arguments of add_handoff (role / trigger / anchor).

Handoff state calls carry no `If-Match` (the server's conditional UPDATE is the
lock); a 409 surfaces the server's message with `claimed_by`.
"""

from dataclasses import replace

import httpx
import pytest

from slash_pro_mcp.client import SlashClient
from slash_pro_mcp.config import SlashConfig
from tests.helpers import AGENT, FakeSlash, call, call_error, call_untrusted, json_response, unfence, ws_path

pytestmark = pytest.mark.asyncio


def handoff(**extra) -> dict:
    return {
        "id": "h-1",
        "ticketId": "t-23",
        "ticketKey": "ABC-23",
        "ticketTitle": "검증 대상",
        "toAgent": "Codex",
        "requestedRole": "verifier",
        "trigger": "human",
        "anchor": {"pr_urls": ["https://example.com/pull/1"], "head_shas": []},
        "state": "open",
        "claimedBy": None,
        "leaseExpiresAt": None,
        "createdAt": "2026-09-19T01:00:00+00:00",
        "actorLabel": "Claude Code",
        "summary": "검증 요청: PR #1",
        **extra,
    }


async def test_list_handoffs_defaults_to_own_label(client: SlashClient, fake: FakeSlash):
    fake.on(
        "GET",
        ws_path("/handoffs"),
        json_response(
            200,
            [
                handoff(
                    id="h-2",
                    state="claimed",
                    claimedBy="Codex",
                    leaseExpiresAt="2026-09-19T03:00:00+00:00",
                    trigger="agent",
                    requestedRole=None,
                ),
                handoff(),
            ],
        ),
    )
    text = await call_untrusted(client, "list_handoffs")
    (get,) = fake.calls
    assert get.query == {"to": AGENT, "limit": "50"}
    assert text == (
        "- ABC-23 · claimed · - · agent — 검증 요청: PR #1  "
        "(id h-2; Claude Code 2026-09-19T01:00:00+00:00; 맡음 Codex · lease 2026-09-19T03:00:00+00:00)\n"
        "- ABC-23 · open · verifier · human — 검증 요청: PR #1  "
        "(id h-1; Claude Code 2026-09-19T01:00:00+00:00)"
    )


async def test_list_handoffs_forwards_to_and_state(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/handoffs"), json_response(200, []))
    assert await call_untrusted(client, "list_handoffs", to="Codex", state="completed, blocked") == "(인계 없음)"
    assert fake.calls[-1].query == {"to": "Codex", "limit": "50", "state": "completed,blocked"}
    await call_untrusted(client, "list_handoffs", to="Codex", state="all")
    assert fake.calls[-1].query["state"] == "all"
    assert "알 수 없는 state: bogus" in await call_error(client, "list_handoffs", state="bogus")


async def test_list_handoffs_without_agent_label_omits_to(fake: FakeSlash, config: SlashConfig):
    fake.on("GET", ws_path("/handoffs"), json_response(200, [handoff(blockedBy=[{"key": "ABC-9"}, {"manual": True}])]))
    api = SlashClient(replace(config, agent=None), transport=httpx.MockTransport(fake.handle))
    try:
        text = await call_untrusted(api, "list_handoffs")
    finally:
        await api.aclose()
    assert fake.calls[-1].query == {"limit": "50"}  # the server then lists every recipient
    assert text.endswith("; 기다리는 중 ABC-9, 수동 (-))")


async def test_claim_heartbeat_complete_block(client: SlashClient, fake: FakeSlash):
    claimed = handoff(state="claimed", claimedBy="Codex", leaseExpiresAt="2026-09-19T03:00:00+00:00")
    fake.on("POST", ws_path("/handoffs/h-1/claim"), json_response(200, claimed))
    fake.on("POST", ws_path("/handoffs/h-1/heartbeat"), json_response(200, claimed))
    fake.on(
        "POST", ws_path("/handoffs/h-1/complete"), json_response(200, handoff(state="completed", claimedBy="Codex"))
    )
    fake.on(
        "POST",
        ws_path("/handoffs/h-1/block"),
        json_response(200, handoff(state="blocked", claimedBy="Codex", blockedReason="토큰 없음")),
    )

    text = unfence(await call(client, "update_handoff", handoff_id=" h-1 ", action="claim"), "handoff")
    assert text == "claim: ABC-23 인계 h-1 → claimed · 맡음 Codex · lease 2026-09-19T03:00:00+00:00"
    post = fake.calls[-1]
    assert post.body == {"agent": None}  # the server records the token's label
    assert "if-match" not in post.headers
    assert "x-requested-by" not in post.headers

    await call(client, "update_handoff", handoff_id="h-1", action="heartbeat")
    assert fake.calls[-1].body == {"agent": None}

    text = unfence(
        await call(client, "update_handoff", handoff_id="h-1", action="complete", note_id="note-9"), "handoff"
    )
    assert text == "complete: ABC-23 인계 h-1 → completed · 맡음 Codex"
    assert fake.calls[-1].body == {"agent": None, "note_id": "note-9"}
    await call(client, "update_handoff", handoff_id="h-1", action="complete")
    assert fake.calls[-1].body == {"agent": None, "note_id": None}
    # clients that send every parameter: empty strings for unused ones are not errors
    await call(client, "update_handoff", handoff_id="h-1", action="heartbeat", note_id="", reason="")
    assert fake.calls[-1].body == {"agent": None}

    text = unfence(
        await call(client, "update_handoff", handoff_id="h-1", action="block", reason="토큰 없음"), "handoff"
    )
    assert text == "block: ABC-23 인계 h-1 → blocked · 맡음 Codex"
    assert fake.calls[-1].body == {"agent": None, "reason": "토큰 없음"}
    calls = len(fake.calls)
    for args, err in (
        ({"action": "block", "reason": " "}, "reason이 비어 있음"),
        ({"action": "block"}, "reason이 비어 있음"),
        ({"action": "claim", "note_id": "n"}, "note_id는 action=complete에서만"),
        ({"action": "complete", "reason": "r"}, "reason은 action=block에서만"),
    ):
        assert err in await call_error(client, "update_handoff", handoff_id="h-1", **args)
    assert len(fake.calls) == calls


async def test_claim_taken_surfaces_holder(client: SlashClient, fake: FakeSlash):
    fake.on(
        "POST",
        ws_path("/handoffs/h-1/claim"),
        json_response(409, raw={"message": "이미 맡은 인계입니다 (Codex)", "code": 409, "claimed_by": "Codex"}),
    )
    text = await call_error(client, "update_handoff", handoff_id="h-1", action="claim")
    assert "HTTP 409: 이미 맡은 인계입니다 (Codex)" in text
    assert len(fake.calls) == 1  # no retry: a claim conflict is final


async def test_add_handoff_sends_role_trigger_and_anchor(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/tickets/by-key/ABC-23"), json_response(200, {"id": "t-23"}, etag='"h1"'))
    fake.on("POST", ws_path("/tickets/t-23/notes"), json_response(201, {"id": "note-h"}, etag='"h2"'))
    await call(
        client,
        "add_handoff",
        key="ABC-23",
        goal="검증 요청",
        to="Codex",
        role="verifier",
        trigger="human",
        pr_urls=["https://example.com/pull/1", " "],
        head_shas=["abc1234"],
    )
    (post,) = fake.calls_to("POST", ".*")
    assert post.body["handoff"]["requested_role"] == "verifier"
    assert post.body["handoff"]["trigger"] == "human"
    assert post.body["handoff"]["anchor"] == {"pr_urls": ["https://example.com/pull/1"], "head_shas": ["abc1234"]}
    assert "알 수 없는 role: boss" in await call_error(client, "add_handoff", key="ABC-23", goal="g", role="boss")
    assert "알 수 없는 trigger: cron" in await call_error(client, "add_handoff", key="ABC-23", goal="g", trigger="cron")


async def test_add_handoff_sends_model_and_list_shows_it(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/tickets/by-key/ABC-23"), json_response(200, {"id": "t-23"}, etag='"h1"'))
    fake.on("POST", ws_path("/tickets/t-23/notes"), json_response(201, {"id": "note-h"}, etag='"h2"'))
    await call(client, "add_handoff", key="ABC-23", goal="검증 요청", to="Codex", model=" gpt-5 ")
    (post,) = fake.calls_to("POST", ".*")
    assert post.body["handoff"]["to_model"] == "gpt-5"
    await call(client, "add_handoff", key="ABC-23", goal="g", to="Codex")
    assert "to_model" not in fake.calls_to("POST", ".*")[-1].body["handoff"]
    await call(client, "add_handoff", key="ABC-23", goal="g", to=" ")
    assert fake.calls_to("POST", ".*")[-1].body["handoff"]["to_agent"] is None

    fake.on(
        "GET",
        ws_path("/handoffs"),
        json_response(
            200,
            [
                {
                    "id": "h-9",
                    "ticketKey": "ABC-23",
                    "state": "open",
                    "requestedRole": "verifier",
                    "trigger": "human",
                    "summary": "검증 요청: PR #1",
                    "toAgent": "Codex",
                    "toModel": "gpt-5",
                    "actorLabel": "Claude Code",
                    "createdAt": "2026-09-20T01:00:00+00:00",
                }
            ],
        ),
    )
    text = await call_untrusted(client, "list_handoffs", to="Codex")
    assert text.startswith("- ABC-23 · open · verifier · human — 검증 요청: PR #1  (id h-9; ")
    assert text.rstrip(")").endswith("; 모델 gpt-5")
