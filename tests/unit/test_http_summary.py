from __future__ import annotations

import json

import pytest

from packages.inspector.http_summary import select_http_html, summarize_http_html


URL = "https://example.com/news/"


def test_summary_preserves_real_footer_pagination_before_dom_truncation():
    cards = "".join(f'<article class="news-card"><a href="2026/{index}.html">Title {index}</a><p>{"body " * 150}</p></article>' for index in range(30))
    raw = f'<html><body>{cards}<nav class="pagination"><a href="2.html">2</a><a rel="next" href="2.html">Next</a></nav></body></html>'
    result = summarize_http_html(raw, URL)
    assert result["pagination_links"][0]["url"] == URL + "2.html"
    assert any(link["rel"] == "next" for link in result["pagination_links"])
    assert "visible_text" not in result and "dom_excerpt" not in result
    assert len(json.dumps(result, ensure_ascii=False)) < 3500
    assert any(block["class"] == "news-card" and block["count"] == 30 for block in result["repeated_blocks"])


def test_summary_excludes_unsafe_and_cross_host_links_and_prioritizes_articles():
    raw = """<html><body><a href="/about">About</a><a href="/2026/article.html">Article</a>
        <nav id="pager"><a href="javascript:go()">2</a><a href="data:text/html,x">3</a>
        <a href="https://user:secret@example.com/news/4.html">4</a>
        <a href="https://other.example/news/5.html">5</a><a href="2.html">2</a></nav>
        <script>createPageHTML(10, 1, 'index', 'html');</script></body></html>"""
    result = summarize_http_html(raw, URL)
    assert [link["url"] for link in result["pagination_links"]] == [URL + "2.html"]
    assert result["links_preview"][0]["url"] == "https://example.com/2026/article.html"
    assert result["pagination_hints"][0]["verified"] is False
    assert "createPageHTML" in result["pagination_hints"][0]["snippet"]
    assert "user:secret" not in json.dumps(result)


def test_summary_does_not_mistake_breadcrumb_home_link_for_pagination():
    raw = '<html><body><nav class="breadcrumbs"><a href="/">首页</a></nav><div class="pagination"><a href="2.html">2</a></div></body></html>'
    result = summarize_http_html(raw, URL)
    assert [entry["url"] for entry in result["pagination_links"]] == [URL + "2.html"]


def test_summary_bounds_all_metadata_for_many_classes_links_and_scripts():
    body = "".join(f'<div class="block-{index} {"x" * 200}"><a href="{index}.html">{"text" * 100}</a></div>' for index in range(100) for _ in range(3))
    raw = f'<html><body>{body}<nav class="pager">' + "".join(f'<a href="{index}.html">{index}</a>' for index in range(2, 60)) + "</nav>" + "".join("<script>createPageHTML(" + "x" * 500 + ");</script>" for _ in range(10)) + "</body></html>"
    result = summarize_http_html(raw, URL)
    assert len(result["pagination_links"]) <= 12
    assert len(result["links_preview"]) <= 6
    assert len(result["repeated_blocks"]) <= 6
    assert len(result["pagination_hints"]) <= 2
    assert all(len(block["sample_html"]) <= 400 for block in result["repeated_blocks"])
    assert len(json.dumps(result, ensure_ascii=False)) < 3500


def test_summary_prioritizes_main_dated_cards_and_deduplicates_nested_sidebar_blocks():
    menu = '<div class="nav_menu"><a href="/reader.html">Reader</a><ul>' + "".join(f'<li><a href="/channel/{index}.html">Menu {index}</a></li>' for index in range(14)) + "</ul></div>"
    cards = "".join(
        f'<div class="tile-shell"><div class="tile-image"><a href="/news/2026/0930/{index}.html"><img src="/image.jpg"></a></div>'
        f'<div class="tile-text"><div class="tile-title"><a href="/news/2026/0930/{index}.html">Main article {index}</a></div>'
        '<div class="tile-meta"><span>News source</span><span>2026-09-30 14:50:46</span></div></div></div>'
        for index in range(20)
    )
    sidebar = '<aside class="sidebar">' + "".join(f'<div class="popular-item"><div class="item-label"><a href="/archive/{index}.html">Old recommended article {index}</a></div></div>' for index in range(10)) + "</aside>"
    result = summarize_http_html(f"<html><body>{menu}<div>{cards}</div>{sidebar}</body></html>", URL)
    assert result["repeated_blocks"][0]["selector"] == "div.tile-shell"
    assert result["repeated_blocks"][0]["count"] == 20
    assert not any(block["class"] in {"tile-text", "tile-title", "item-label"} for block in result["repeated_blocks"])
    assert not any(block["selector"] == "li" for block in result["repeated_blocks"])
    assert result["links_preview"][0]["url"] == "https://example.com/news/2026/0930/0.html"
    assert result["links_preview"][0]["text"] == "Main article 0"
    hints = result["repeated_blocks"][0]["field_hints"]
    checked = select_http_html(
        f"<html><body>{menu}<div>{cards}</div>{sidebar}</body></html>",
        URL,
        {name: hint["selector"] for name, hint in hints.items()},
        scope_selector="//div[@class='tile-shell']",
    )
    assert checked["fields"]["title"]["count"] == 20
    assert checked["fields"]["publish_time"]["count"] == 20
    assert checked["fields"]["publish_time"]["samples"] == ["2026-09-30 14:50:46"] * 2
    assert len(json.dumps(result, ensure_ascii=False)) < 3300


def test_summary_prefers_requested_category_cards_over_more_popular_sidebar_items():
    main = "".join(f'<div class="tile"><a href="/news/{index}.html">Main title {index}</a><time>2026-09-30</time></div>' for index in range(8))
    sidebar = "".join(f'<article class="news-card"><h2><a href="/archive/{index}.html">Popular title {index}</a></h2></article>' for index in range(30))
    result = summarize_http_html(
        f"<html><body><section>{main}</section><aside>{sidebar}</aside></body></html>",
        URL,
    )
    assert result["repeated_blocks"][0]["selector"] == "div.tile"
    assert result["links_preview"][0]["url"] == "https://example.com/news/0.html"


def test_large_page_named_wrapper_does_not_turn_main_articles_into_pagination():
    cards = "".join(f'<article class="tile"><h2><a href="{index}.html">Article {index}</a></h2><time>2026-09-30</time></article>' for index in range(30))
    raw = f'<html><body><div class="pages-layout">{cards}<div class="pagination"><a href="2.html">下一页</a></div></div></body></html>'
    result = summarize_http_html(raw, URL)
    assert [entry["url"] for entry in result["pagination_links"]] == [URL + "2.html"]
    assert result["repeated_blocks"][0]["count"] == 30
    assert result["links_preview"][0]["url"] == URL + "0.html"


def test_summary_bounds_deep_field_hints_without_discarding_main_card_evidence():
    url = "https://example.com/" + "x" * 450 + "/"
    body = "<div>" * 200 + '<h3><a href="1.html">Title</a></h3><time>2026-09-30</time>' + "</div>" * 200
    raw = "<html><body>" + f'<article class="tile">{body}</article>' * 3 + "</body></html>"
    result = summarize_http_html(raw, url)
    assert len(json.dumps(result, ensure_ascii=False)) <= 3300
    assert result["repeated_blocks"][0]["selector"] == "article.tile"
    assert result["repeated_blocks"][0]["count"] == 3
    assert result["links_preview"][0]["text"] == "Title"


def test_select_xpath_scoped_fields_counts_all_items_and_bounds_preview():
    raw = "<html><body>" + "".join(f'<article class="item"><h2>Title {index}</h2><a href="{index}.html">Link</a><p>{"body" * 200}</p></article>' for index in range(20)) + "</body></html>"
    result = select_http_html(
        raw,
        URL,
        {"title": ".//h2", "url": ".//a/@href", "content": ".//p"},
        scope_selector="//article[@class='item']",
        max_samples=2,
        max_chars=50,
    )
    assert result["scope_count"] == 20
    assert result["fields"]["title"] == {"count": 20, "samples": ["Title 0", "Title 1"]}
    assert result["fields"]["url"]["samples"] == [URL + "0.html", URL + "1.html"]
    assert result["fields"]["content"]["count"] == 20
    assert all(len(sample) <= 50 for sample in result["fields"]["content"]["samples"])


def test_select_css_with_attributes_and_without_scopes():
    raw = '<html><body><h1>Full Title</h1><a class="article" href="/detail/9">Link</a></body></html>'
    result = select_http_html(
        raw,
        URL,
        {"title": "h1", "link": "a.article"},
        selector_type="css",
        attributes={"link": "href"},
    )
    assert result["fields"]["title"] == {"count": 1, "samples": ["Full Title"]}
    assert result["fields"]["link"] == {
        "count": 1,
        "samples": ["https://example.com/detail/9"],
    }


def test_select_css_scopes_and_missing_fields():
    raw = "<html><body><article><h2>One</h2></article><article><h2>Two</h2></article></body></html>"
    result = select_http_html(
        raw,
        URL,
        {"title": "h2", "missing": ".missing"},
        selector_type="css",
        scope_selector="article",
    )
    assert result["fields"]["title"]["count"] == 2
    assert result["fields"]["missing"] == {"count": 0, "samples": []}


def test_select_missing_href_does_not_fabricate_the_base_url():
    result = select_http_html(
        "<html><body><a>Link</a></body></html>",
        URL,
        {"link": "//a"},
        attributes={"link": "href"},
    )
    assert result["fields"]["link"] == {"count": 1, "samples": []}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"selectors": {"bad": "//*["}},
        {"selectors": {"title": "h2"}, "selector_type": "css", "scope_selector": "["},
        {"selectors": {"title": "h2"}, "selector_type": "unknown"},
        {"selectors": {str(index): "//h2" for index in range(21)}},
    ],
)
def test_select_rejects_invalid_or_excessive_selectors(kwargs):
    with pytest.raises(ValueError):
        select_http_html("<html><body><h2>Title</h2></body></html>", URL, **kwargs)


def test_select_caps_scope_evaluation_but_reports_full_scope_count():
    raw = "<html><body>" + "".join("<article><h2>Title</h2></article>" for _ in range(70)) + "</body></html>"
    result = select_http_html(raw, URL, {"title": ".//h2"}, scope_selector="//article")
    assert result["scope_count"] == 70
    assert result["scopes_evaluated"] == 50
    assert result["scope_count_truncated"] is True
    assert result["fields"]["title"]["count"] == 50


def test_select_global_count_uses_full_dom_not_summary_excerpt():
    raw = "<html><body>" + "".join(f"<p>{'x' * 200} {index}</p>" for index in range(90)) + "<h1>Footer Title</h1></body></html>"
    result = select_http_html(raw, URL, {"paragraphs": "//p", "title": "//h1"})
    assert result["fields"]["paragraphs"]["count"] == 90
    assert result["fields"]["title"]["samples"] == ["Footer Title"]
