"""document_tree / get_document / search_documents (read) and write_document (write)."""

import pytest

from slash_pro_mcp.client import SlashClient
from tests.helpers import FakeSlash, call, call_error, call_untrusted, json_response, unfence, ws_path

pytestmark = pytest.mark.asyncio

PROJECTS = [{"id": "p-demo", "key": "ABC"}]
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


async def test_document_tree_resolves_project_and_indents_children(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/projects/p-demo/documents/tree"), json_response(200, PAGES))

    text = await call_untrusted(client, "document_tree", project_key="abc")

    assert text.splitlines() == [
        "- 회의록 (minutes) `root-b`",
        "- 결정 (decisions) `root-a`",
        "  - MCP 서버 (general) `00000000-0000-7000-8000-000000000031`",
    ]


async def test_document_tree_unknown_project(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))

    assert "프로젝트 없음: NOPE" in await call_error(client, "document_tree", project_key="NOPE")


async def test_get_document_renders_plain_text(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/documents/pages/root-b"), json_response(200, PAGES[2]))

    text = await call_untrusted(client, "get_document", page_id="root-b")

    assert text == (
        "# 회의록\n\n- id: root-b · 유형: minutes · 회의: m-1 · 수정: 2026-09-18T03:00:00Z\n\n회의에서 mcp 얘기"
    )


async def test_get_document_empty_body(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/documents/pages/00000000-0000-7000-8000-000000000031"), json_response(200, PAGES[1]))

    assert (await call_untrusted(client, "get_document", page_id="00000000-0000-7000-8000-000000000031")).endswith(
        "(본문 없음)"
    )


async def test_search_documents_across_project_matches_title_and_body(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    # the tree with `q` returns only the matching pages, without bodies
    fake.on("GET", ws_path("/projects/p-demo/documents/tree"), json_response(200, [{"id": p["id"]} for p in PAGES]))
    for page in PAGES:
        fake.on("GET", ws_path(f"/documents/pages/{page['id']}"), json_response(200, page))

    text = await call_untrusted(client, "search_documents", text="mcp", project_key="ABC")
    (tree,) = fake.calls_to("GET", ws_path("/projects/p-demo/documents/tree"))
    assert tree.query == {"q": "mcp"}
    assert len(fake.calls_to("GET", ws_path("/documents/pages/.*"))) == 3

    assert text == (
        "## 결정 `root-a`\n- L2: MCP는 stdio\n\n## MCP 서버 `00000000-0000-7000-8000-000000000031`\n\n## 회의록 `root-b`\n- L1: 회의에서 mcp 얘기"
    )


async def test_search_documents_reads_at_most_20_pages(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("GET", ws_path("/projects/p-demo/documents/tree"), json_response(200, [{"id": f"p{i}"} for i in range(23)]))
    fake.on("GET", ws_path("/documents/pages/p[0-9]+"), json_response(200, {**PAGES[2], "id": "p"}))
    text = await call_untrusted(client, "search_documents", text="mcp", project_key="ABC")
    assert len(fake.calls_to("GET", ws_path("/documents/pages/.*"))) == 20
    assert text.endswith("… 일치하는 페이지 3개 더 있음(표시 안 함): text를 좁히기")


async def test_search_documents_within_one_page_and_no_match(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/documents/pages/root-a"), json_response(200, PAGES[0]))

    assert (
        await call_untrusted(client, "search_documents", text="잠금", page_id="root-a")
        == "## 결정 `root-a`\n- L1: ETag 낙관적 잠금을 쓴다"
    )
    assert await call_untrusted(client, "search_documents", text="없는말", page_id="root-a") == "'없는말' 일치 없음"
    assert not fake.calls_to("GET", ws_path("/projects"))


async def test_search_documents_requires_a_scope(client: SlashClient, fake: FakeSlash):
    assert "project_key나 page_id가 필요함" in await call_error(client, "search_documents", text="x")
    assert "text가 비어 있음" in await call_error(client, "search_documents", text="  ", page_id="p")


async def test_write_document_appends_markdown_with_source(client: SlashClient, fake: FakeSlash):
    fake.on(
        "PUT",
        ws_path("/documents/pages/00000000-0000-7000-8000-000000000031/content"),
        json_response(200, {**PAGES[1], "plain_text": "x"}),
    )

    text = await call_untrusted(
        client,
        "write_document",
        page_id="00000000-0000-7000-8000-000000000031",
        markdown="## 결론\n- stdio",
        source="ABC-23 구현",
    )

    assert text == "문서 append: MCP 서버 `00000000-0000-7000-8000-000000000031`"
    (put,) = fake.calls_to("PUT", ".*")
    assert put.body == {
        "format": "markdown",
        "content": "## 결론\n- stdio",
        "mode": "append",
        "source": "ABC-23 구현",
    }
    assert "x-requested-by" not in put.headers
    assert "if-match" not in put.headers


async def test_write_document_replace_and_validation(client: SlashClient, fake: FakeSlash):
    fake.on("PUT", ws_path("/documents/pages/00000000-0000-7000-8000-000000000031/content"), json_response(200, PAGES[1]))

    assert await call_untrusted(
        client, "write_document", page_id="00000000-0000-7000-8000-000000000031", markdown="본문", mode="replace"
    ) == ("문서 replace: MCP 서버 `00000000-0000-7000-8000-000000000031`")
    assert fake.calls[-1].body == {"format": "markdown", "content": "본문", "mode": "replace"}
    assert "알 수 없는 mode: upsert" in await call_error(
        client, "write_document", page_id="00000000-0000-7000-8000-000000000031", markdown="x", mode="upsert"
    )
    assert "markdown이 비어 있음" in await call_error(
        client, "write_document", page_id="00000000-0000-7000-8000-000000000031", markdown=" "
    )


async def test_write_document_error_passes_through(client: SlashClient, fake: FakeSlash):
    fake.on(
        "PUT",
        ws_path("/documents/pages/00000000-0000-7000-8000-000000000031/content"),
        json_response(403, raw={"detail": "scope_required: document:write"}),
    )

    assert "HTTP 403: scope_required: document:write" in await call_error(
        client, "write_document", page_id="00000000-0000-7000-8000-000000000031", markdown="x"
    )


# -- create_document --------------------------------------------------------


async def test_get_document_markdown_flag(client: SlashClient, fake: FakeSlash):
    from tests.helpers import text_response

    fake.on("GET", ws_path("/documents/pages/p1"), text_response(200, "## 전략\n\n- [ ] 할 일"))
    assert await call_untrusted(client, "get_document", page_id="p1", markdown=True) == "## 전략\n\n- [ ] 할 일"
    assert fake.calls[-1].query == {"format": "markdown"}


async def test_create_document_under_section_root(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on("POST", ws_path("/projects/p-demo/documents/ensure-roots"), json_response(200, None))
    fake.on(
        "GET",
        ws_path("/projects/p-demo/documents/tree"),
        json_response(
            200,
            [
                {"id": "r-prod", "parentId": None, "pageType": "product", "title": "Product"},
                {"id": "r-spec", "parentId": None, "pageType": "feature_spec", "title": "Feature Specs"},
            ],
        ),
    )
    fake.on("POST", ws_path("/projects/p-demo/documents/pages"), json_response(201, {"id": "pg-1", "title": "스펙"}))

    md = "## 표\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] 할 일"
    text = await call(client, "create_document", project_key="abc", section="feature-specs", title="스펙", markdown=md)

    assert unfence(text, "document write") == "문서 생성: 스펙 `pg-1` (feature-specs)"
    post = fake.calls[-1]
    assert post.body == {"title": "스펙", "parentId": "r-spec", "format": "markdown", "content": md}
    assert "section" in await call_error(
        client, "create_document", project_key="abc", section="nope", title="x", markdown="y"
    )


async def test_create_document_product_uses_strategy_doc_and_appends(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "POST",
        ws_path("/projects/p-demo/documents/ensure-one-pager"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032", "title": "ABC 제품 전략"}),
    )
    fake.on(
        "PUT",
        ws_path("/documents/pages/00000000-0000-7000-8000-000000000032/content"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032"}),
    )

    text = await call(
        client, "create_document", project_key="ABC", section="product", title="무시됨", markdown="## 가설"
    )

    assert unfence(text, "document write") == (
        "제품 전략 페이지 작성: ABC 제품 전략 `00000000-0000-7000-8000-000000000032`"
    )
    assert fake.calls[-1].body == {"format": "markdown", "content": "## 가설", "mode": "replace"}


async def test_create_document_product_falls_back_to_append_when_human_wrote(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "POST",
        ws_path("/projects/p-demo/documents/ensure-one-pager"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032", "title": "ABC 제품 전략"}),
    )
    puts = []

    def handler(request):
        import json as _json

        body = _json.loads(request.content)
        puts.append(body["mode"])
        if body["mode"] == "replace":
            return json_response(409, raw={"message": "제품 전략 문서는 항목 추가로만", "code": "not_empty"})
        return json_response(200, {"id": "00000000-0000-7000-8000-000000000032"})

    fake.on("PUT", ws_path("/documents/pages/00000000-0000-7000-8000-000000000032/content"), handler)

    text = await call(client, "create_document", project_key="ABC", section="product", title="x", markdown="## 가설")

    assert puts == ["replace", "append"]
    assert unfence(text, "document write").startswith("제품 전략 페이지 작성")


async def test_create_document_product_does_not_append_on_other_conflicts(client: SlashClient, fake: FakeSlash):
    fake.on("GET", ws_path("/projects"), json_response(200, PROJECTS))
    fake.on(
        "POST",
        ws_path("/projects/p-demo/documents/ensure-one-pager"),
        json_response(200, {"id": "00000000-0000-7000-8000-000000000032", "title": "ABC 제품 전략"}),
    )
    fake.on(
        "PUT",
        ws_path("/documents/pages/00000000-0000-7000-8000-000000000032/content"),
        json_response(409, raw={"message": "busy", "code": "document_busy"}),
    )
    text = await call_error(client, "create_document", project_key="ABC", section="product", title="x", markdown="y")
    assert "code: document_busy" in text
    assert len(fake.calls_to("PUT", ".*")) == 1


async def test_old_wiki_tool_names_are_deprecated_aliases(client: SlashClient):
    from slash_pro_mcp.server import build_server
    from slash_pro_mcp.tools.documents import ALIASES

    tools = {t.name: t for t in await build_server(client).list_tools()}
    for old, new in ALIASES.items():
        assert old in tools and new in tools
        assert tools[old].inputSchema == tools[new].inputSchema
        assert new in tools[old].description
