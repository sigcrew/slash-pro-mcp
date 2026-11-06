"""wiki_tree / get_wiki_page / search_wiki (read) and write_wiki_page (write)."""

import pytest

from slash_pro_mcp.client import EchoClient
from tests.helpers import FakeEcho, call, call_error, call_untrusted, json_response, unfence, ws_path

pytestmark = pytest.mark.asyncio

PROJECTS = [{"id": "p-echo", "key": "ECHO"}]
PAGES = [
    {
        "id": "root-a",
        "parent_id": None,
        "title": "결정",
        "page_type": "decisions",
        "sort_order": 1,
        "plain_text": "ETag 낙관적 잠금을 쓴다\nMCP는 stdio",
        "updated_at": "2026-09-18T01:00:00Z",
        "meeting_id": None,
    },
    {
        "id": "00000000-0000-7000-8000-000000000031",
        "parent_id": "root-a",
        "title": "MCP 서버",
        "page_type": "general",
        "sort_order": 0,
        "plain_text": "",
        "updated_at": "2026-09-18T02:00:00Z",
        "meeting_id": None,
    },
    {
        "id": "root-b",
        "parent_id": None,
        "title": "회의록",
        "page_type": "minutes",
        "sort_order": 0,
        "plain_text": "회의에서 mcp 얘기",
        "updated_at": "2026-09-18T03:00:00Z",
        "meeting_id": "m-1",
    },
]


async def test_wiki_tree_resolves_project_and_indents_children(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/projects/p-echo/wiki/tree"), json_response(200, PAGES))

    text = await call_untrusted(client, "wiki_tree", project_key="echo")

    assert text.splitlines() == [
        "- 회의록 (minutes) `root-b`",
        "- 결정 (decisions) `root-a`",
        "  - MCP 서버 (general) `00000000-0000-7000-8000-000000000031`",
    ]


async def test_wiki_tree_unknown_project(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))

    assert "Project not found: NOPE" in await call_error(client, "wiki_tree", project_key="NOPE")


async def test_get_wiki_page_renders_plain_text(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/wiki/pages/root-b"), json_response(200, PAGES[2]))

    text = await call_untrusted(client, "get_wiki_page", page_id="root-b")

    assert text == (
        "# 회의록\n\n- id: root-b · type: minutes · meeting: m-1 · updated: 2026-09-18T03:00:00Z\n\n회의에서 mcp 얘기"
    )


async def test_get_wiki_page_empty_body(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/wiki/pages/00000000-0000-7000-8000-000000000031"), json_response(200, PAGES[1]))

    assert (await call_untrusted(client, "get_wiki_page", page_id="00000000-0000-7000-8000-000000000031")).endswith(
        "(본문 없음)"
    )


async def test_search_wiki_across_project_matches_title_and_body(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    # the tree with `q` returns only the matching pages, without bodies
    fake.on("GET", ws_path("/projects/p-echo/wiki/tree"), json_response(200, [{"id": p["id"]} for p in PAGES]))
    for page in PAGES:
        fake.on("GET", ws_path(f"/wiki/pages/{page['id']}"), json_response(200, page))

    text = await call_untrusted(client, "search_wiki", text="mcp", project_key="ECHO")
    (tree,) = fake.calls_to("GET", ws_path("/projects/p-echo/wiki/tree"))
    assert tree.query == {"q": "mcp"}
    assert len(fake.calls_to("GET", ws_path("/wiki/pages/.*"))) == 3

    assert text == (
        "## 결정 `root-a`\n- L2: MCP는 stdio\n\n## MCP 서버 `00000000-0000-7000-8000-000000000031`\n\n## 회의록 `root-b`\n- L1: 회의에서 mcp 얘기"
    )


async def test_search_wiki_reads_at_most_20_pages(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/projects/p-echo/wiki/tree"), json_response(200, [{"id": f"p{i}"} for i in range(23)]))
    fake.on("GET", ws_path("/wiki/pages/p[0-9]+"), json_response(200, {**PAGES[2], "id": "p"}))
    text = await call_untrusted(client, "search_wiki", text="mcp", project_key="ECHO")
    assert len(fake.calls_to("GET", ws_path("/wiki/pages/.*"))) == 20
    assert text.endswith("… 3 more matching pages not shown; narrow the text")


async def test_search_wiki_within_one_page_and_no_match(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/wiki/pages/root-a"), json_response(200, PAGES[0]))

    assert (
        await call_untrusted(client, "search_wiki", text="잠금", page_id="root-a")
        == "## 결정 `root-a`\n- L1: ETag 낙관적 잠금을 쓴다"
    )
    assert await call_untrusted(client, "search_wiki", text="없는말", page_id="root-a") == "'없는말' 일치 없음"
    assert not fake.calls_to("GET", ws_path("/projects"))


async def test_search_wiki_requires_a_scope(client: EchoClient, fake: FakeEcho):
    assert "project_key or page_id is required" in await call_error(client, "search_wiki", text="x")
    assert "text must not be empty" in await call_error(client, "search_wiki", text="  ", page_id="p")


async def test_write_wiki_page_appends_markdown_with_source(client: EchoClient, fake: FakeEcho):
    fake.on(
        "PUT",
        ws_path("/wiki/pages/00000000-0000-7000-8000-000000000031/content"),
        json_response(200, {**PAGES[1], "plain_text": "x"}),
    )

    text = await call_untrusted(
        client,
        "write_wiki_page",
        page_id="00000000-0000-7000-8000-000000000031",
        markdown="## 결론\n- stdio",
        source="ECHO-23 구현",
    )

    assert text == "Wiki append: MCP 서버 `00000000-0000-7000-8000-000000000031`"
    (put,) = fake.calls_to("PUT", ".*")
    assert put.body == {
        "format": "markdown",
        "content": "## 결론\n- stdio",
        "mode": "append",
        "source": "ECHO-23 구현",
    }
    assert "x-requested-by" not in put.headers
    assert "if-match" not in put.headers


async def test_write_wiki_page_replace_and_validation(client: EchoClient, fake: FakeEcho):
    fake.on("PUT", ws_path("/wiki/pages/00000000-0000-7000-8000-000000000031/content"), json_response(200, PAGES[1]))

    assert await call_untrusted(
        client, "write_wiki_page", page_id="00000000-0000-7000-8000-000000000031", markdown="본문", mode="replace"
    ) == ("Wiki replace: MCP 서버 `00000000-0000-7000-8000-000000000031`")
    assert fake.calls[-1].body == {"format": "markdown", "content": "본문", "mode": "replace"}
    assert "unknown mode: upsert" in await call_error(
        client, "write_wiki_page", page_id="00000000-0000-7000-8000-000000000031", markdown="x", mode="upsert"
    )
    assert "markdown must not be empty" in await call_error(
        client, "write_wiki_page", page_id="00000000-0000-7000-8000-000000000031", markdown=" "
    )


async def test_write_wiki_page_error_passes_through(client: EchoClient, fake: FakeEcho):
    fake.on(
        "PUT",
        ws_path("/wiki/pages/00000000-0000-7000-8000-000000000031/content"),
        json_response(403, raw={"detail": "scope_required: wiki:write"}),
    )

    assert "HTTP 403: scope_required: wiki:write" in await call_error(
        client, "write_wiki_page", page_id="00000000-0000-7000-8000-000000000031", markdown="x"
    )


# -- create_wiki_page --------------------------------------------------------


async def test_get_wiki_page_markdown_flag(client: EchoClient, fake: FakeEcho):
    from tests.helpers import text_response

    fake.on("GET", ws_path("/wiki/pages/p1"), text_response(200, "## 전략\n\n- [ ] 할 일"))
    assert await call_untrusted(client, "get_wiki_page", page_id="p1", markdown=True) == "## 전략\n\n- [ ] 할 일"
    assert fake.calls[-1].query == {"format": "markdown"}


async def test_create_wiki_page_under_section_root(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-echo/wiki/ensure-roots"), json_response(200, None))
    fake.on(
        "GET",
        ws_path("/projects/p-echo/wiki/tree"),
        json_response(
            200,
            [
                {"id": "r-prod", "parentId": None, "pageType": "product", "title": "Product"},
                {"id": "r-spec", "parentId": None, "pageType": "feature_spec", "title": "Feature Specs"},
            ],
        ),
    )
    fake.on("POST", ws_path("/projects/p-echo/wiki/pages"), json_response(201, {"id": "pg-1", "title": "스펙"}))

    md = "## 표\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] 할 일"
    text = await call(
        client, "create_wiki_page", project_key="echo", section="feature-specs", title="스펙", markdown=md
    )

    assert unfence(text, "wiki write") == "Wiki page created: 스펙 `pg-1` (feature-specs)"
    post = fake.calls[-1]
    assert post.body == {"title": "스펙", "parentId": "r-spec", "format": "markdown", "content": md}
    assert "section" in await call_error(
        client, "create_wiki_page", project_key="echo", section="nope", title="x", markdown="y"
    )


async def test_create_wiki_page_product_uses_strategy_doc_and_appends(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "POST",
        ws_path("/projects/p-echo/wiki/ensure-one-pager"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032", "title": "ECHO 제품 전략"}),
    )
    fake.on(
        "PUT",
        ws_path("/wiki/pages/00000000-0000-7000-8000-000000000032/content"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032"}),
    )

    text = await call(
        client, "create_wiki_page", project_key="ECHO", section="product", title="무시됨", markdown="## 가설"
    )

    assert unfence(text, "wiki write") == (
        "Product strategy page written: ECHO 제품 전략 `00000000-0000-7000-8000-000000000032`"
    )
    assert fake.calls[-1].body == {"format": "markdown", "content": "## 가설", "mode": "replace"}


async def test_create_wiki_page_product_falls_back_to_append_when_human_wrote(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "POST",
        ws_path("/projects/p-echo/wiki/ensure-one-pager"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032", "title": "ECHO 제품 전략"}),
    )
    puts = []

    def handler(request):
        import json as _json

        body = _json.loads(request.content)
        puts.append(body["mode"])
        if body["mode"] == "replace":
            return json_response(409, raw={"message": "제품 전략 문서는 항목 추가로만", "code": "not_empty"})
        return json_response(200, {"id": "00000000-0000-7000-8000-000000000032"})

    fake.on("PUT", ws_path("/wiki/pages/00000000-0000-7000-8000-000000000032/content"), handler)

    text = await call(client, "create_wiki_page", project_key="ECHO", section="product", title="x", markdown="## 가설")

    assert puts == ["replace", "append"]
    assert unfence(text, "wiki write").startswith("Product strategy page written")


async def test_create_wiki_page_product_does_not_append_on_other_conflicts(client: EchoClient, fake: FakeEcho):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "POST",
        ws_path("/projects/p-echo/wiki/ensure-one-pager"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032", "title": "ECHO 제품 전략"}),
    )
    fake.on(
        "PUT",
        ws_path("/wiki/pages/00000000-0000-7000-8000-000000000032/content"),
        json_response(409, raw={"message": "busy", "code": "document_busy"}),
    )
    text = await call_error(client, "create_wiki_page", project_key="ECHO", section="product", title="x", markdown="y")
    assert "code: document_busy" in text
    assert len(fake.calls_to("PUT", ".*")) == 1
