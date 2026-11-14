from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from packages.crawl_core.drafts import build_crawl_draft
from packages.inspector.http_summary import summarize_http_html
from services.easyspider_mcp import server


URL = "https://example.com/news/"
FIELDS = {"title": ".//h3/a", "url": ".//h3/a", "content": "//div[@id='Content']"}


@pytest.mark.asyncio
async def test_optional_field_types_schema_lists_legal_types_and_safe_defaults():
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    description = tools["crawl_draft_create"].inputSchema["properties"]["field_types"]["description"]
    for value in ("string", "integer", "number", "boolean", "date", "datetime", "url", "html", "omit"):
        assert value in description


@pytest.mark.parametrize(
    "details,fields,code,part",
    [
        (["content", "content"], FIELDS, "duplicate_detail_fields", "duplicate"),
        (["body"], FIELDS, "unknown_detail_fields", "field_selectors"),
        (["//div[@id='Content']"], FIELDS, "selector_in_detail_fields", "names"),
    ],
)
def test_detail_contract_reports_the_specific_parameter_failure(details, fields, code, part):
    with pytest.raises(ValueError, match=part) as caught:
        build_crawl_draft(url=URL, field_selectors=fields, item_selector="//article", detail_fields=details, detail_link_selector=".//h3/a")
    assert caught.value.code == code
    assert caught.value.parameter == "detail_fields"


@pytest.mark.parametrize(
    "details,fields,code",
    [
        (["content", "content"], FIELDS, "duplicate_detail_fields"),
        (["body"], FIELDS, "unknown_detail_fields"),
        (["//div[@id='Content']"], FIELDS, "selector_in_detail_fields"),
    ],
)
def test_draft_tool_returns_compact_actionable_validation_without_storing(monkeypatch, details, fields, code):
    repository = Mock()
    monkeypatch.setattr(server, "drafts", repository)
    result = server.crawl_draft_create(url=URL, field_selectors=fields, item_selector="//article", detail_fields=details, detail_link_selector=".//h3/a")
    assert result["status"] == "validation_error"
    assert result["code"] == code
    assert result["parameter"] == "detail_fields"
    assert result["retry_limit"] == 1
    assert "field_selectors" in result["next_action"]
    assert "example" in result
    assert len(json.dumps(result, ensure_ascii=False)) < 1800
    repository.create.assert_not_called()


def test_draft_parameter_validation_does_not_silently_supply_missing_body_selector(monkeypatch):
    repository = Mock()
    monkeypatch.setattr(server, "drafts", repository)
    fields = {"title": ".//h3/a"}
    result = server.crawl_draft_create(url=URL, field_selectors=fields, item_selector="//article", detail_fields=["content"], detail_link_selector=".//h3/a")
    assert result["details"]["unknown_fields"] == ["content"]
    assert fields == {"title": ".//h3/a"}
    repository.create.assert_not_called()


def test_bad_draft_credential_url_is_rejected_before_any_store_write(monkeypatch):
    repository = Mock()
    monkeypatch.setattr(server, "drafts", repository)
    result = server.crawl_draft_create(url="https://user:secret@example.com/news/", field_selectors={"title": "//h1"})
    assert result["status"] == "validation_error"
    assert "credentials" in result["error"]
    assert "secret" not in json.dumps(result)
    repository.create.assert_not_called()


def test_draft_type_validation_does_not_echo_rejected_request_secrets(monkeypatch):
    repository = Mock()
    monkeypatch.setattr(server, "drafts", repository)
    result = server.crawl_draft_create(url=URL, mode="http_api", selector_type="jsonpath", field_selectors={"title": "$.title"}, http_request={"response_format": "json", "headers": {"Authorization": "PRIVATE_REJECTED_TOKEN"}})
    assert result["status"] == "validation_error"
    assert "PRIVATE_REJECTED_TOKEN" not in json.dumps(result)
    assert "input_value" not in result["error"]
    repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_protocol_returns_detail_validation_as_normal_structured_result(monkeypatch):
    repository = Mock()
    monkeypatch.setattr(server, "drafts", repository)
    content, structured = await server.mcp.call_tool("crawl_draft_create", {"url": URL, "field_selectors": {"title": ".//h3/a"}, "item_selector": "//article", "detail_fields": ["content"], "detail_link_selector": ".//h3/a"})
    assert structured["status"] == "validation_error"
    assert structured["code"] == "unknown_detail_fields"
    assert len(json.dumps(structured, ensure_ascii=False)) < 1800
    assert content
    repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_discovered_draft_schema_explains_relation_and_remains_small():
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    tool = tools["crawl_draft_create"]
    properties = tool.inputSchema["properties"]
    assert "names" in properties["detail_fields"]["description"]
    assert "field_selectors" in properties["detail_fields"]["description"]
    assert properties["detail_fields"]["examples"] == [["content"]]
    assert "detail" in properties["field_selectors"]["description"]
    assert "relative" in properties["detail_link_selector"]["description"]
    assert "detail_fields are absolute" not in tool.description
    assert len(json.dumps(tool.inputSchema)) < 6000


def test_default_overview_avoids_three_copies_of_news_text_and_exposes_item_structure():
    cards = "".join(f'<article class="news-card"><h3><a href="{i}.html">{"LONG_NEWS_SENTENCE " * 100}</a></h3><time datetime="2026-10-04">2026-10-04</time></article>' for i in range(40))
    result = summarize_http_html(f'<html><head><title>Latest news</title></head><body>{cards}<nav class="pagination"><a href="2.html" rel="next">Next</a></nav></body></html>', URL)
    assert "dom_excerpt" not in result and "visible_text" not in result and "dom" not in result
    assert result["page_title"] == "Latest news"
    assert result["pagination_links"][0]["url"] == URL + "2.html"
    assert len(result["links_preview"]) == 1
    item = next(block for block in result["repeated_blocks"] if block["selector"] == "article.news-card")
    assert item["count"] == 40
    assert "<h3>" in item["sample_html"] and "<time" in item["sample_html"]
    assert len(json.dumps(result, ensure_ascii=False)) < 3500


def test_detail_overview_exposes_heading_and_body_container_not_full_article():
    raw = '<html><head><title>News detail</title></head><body><h1 id="article-title">A real headline</h1><div id="Content">' + "".join(f"<p>{'DETAIL_NEWS_PARAGRAPH ' * 100}</p>" for _ in range(30)) + "</div></body></html>"
    result = summarize_http_html(raw, URL + "article.html")
    assert result["headings"][0]["text"] == "A real headline"
    candidate = next(item for item in result["body_candidates"] if item["selector"] == "div#Content")
    assert candidate["paragraph_count"] == 30
    assert candidate["text_chars"] > 50000
    assert len(candidate["preview"]) <= 100
    assert "<p>" not in json.dumps(result)
    assert "dom_excerpt" not in result and "visible_text" not in result
    assert len(json.dumps(result, ensure_ascii=False)) < 3500


def test_overview_prioritizes_next_link_and_stays_small_with_many_long_real_urls():
    long_path = "x" * 400
    links = "".join(f'<a href="{long_path}/{i}.html">{i}</a>' for i in range(1, 50))
    raw = '<html><body><nav class="pagination">' + links + f'<a rel="next" href="{long_path}/50.html">Next</a></nav></body></html>'
    result = summarize_http_html(raw, URL)
    assert any(item["rel"] == "next" and item["url"].endswith("/50.html") for item in result["pagination_links"])
    assert len(json.dumps(result, ensure_ascii=False)) < 3500


def test_overview_body_candidates_and_excerpt_omit_active_script_text():
    raw = '<html><body><h1>Headline</h1><div id="Content"><script>SECRET_ACTIVE_SCRIPT</script><p>' + "Actual body " * 40 + "</p></div></body></html>"
    result = summarize_http_html(raw, URL)
    assert "SECRET_ACTIVE_SCRIPT" not in json.dumps(result)
    assert result["body_candidates"][0]["preview"].startswith("Actual body")


def test_overview_body_with_several_inline_source_links_is_not_dropped():
    paragraphs = "".join(f'<p>{"Article discussion " * 30}<a href="/source/{i}">source</a></p>' for i in range(6))
    result = summarize_http_html(f'<html><body><h1>Headline</h1><div id="Content">{paragraphs}</div></body></html>', URL)
    assert any(candidate["selector"] == "div#Content" and candidate["paragraph_count"] == 6 for candidate in result["body_candidates"])
