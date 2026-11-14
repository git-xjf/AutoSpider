from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from packages.crawl_core.errors import PolicyViolation
from services.easyspider_mcp import server


URL = "https://example.com/news/"


def html_response(raw: str, *, status_code: int = 200, url: str = URL) -> httpx.Response:
    return httpx.Response(
        status_code,
        text=raw,
        headers={"content-type": "text/html; charset=utf-8"},
        request=httpx.Request("GET", url),
    )


def install_http_client(monkeypatch, response: httpx.Response):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.request.return_value = response
    factory = Mock(return_value=client)
    monkeypatch.setattr(server.httpx, "AsyncClient", factory)
    checks = Mock()
    robots = Mock(return_value=True)
    monkeypatch.setattr(server.policy, "check_url", checks)
    monkeypatch.setattr(server.policy, "robots_allowed", robots)
    return client, checks, robots


@pytest.mark.asyncio
async def test_probe_overview_is_compact_and_preserves_real_footer_pagination(monkeypatch):
    raw = (
        "<html><body>" + "".join(f'<article class="item"><a href="{index}.html">Title {index}</a><p>{"text " * 200}</p></article>' for index in range(40)) + '<nav class="pagination"><a href="2.html" rel="next">Next</a></nav></body></html>'
    )
    fetch = AsyncMock(return_value=html_response(raw))
    monkeypatch.setattr(server, "_probe_http_response", fetch)
    result = await server.probe_http(URL)
    assert result["status"] == "ready"
    assert result["response_format"] == "html"
    assert result["pagination_links"][0]["url"] == URL + "2.html"
    assert result["pagination_links"][0]["rel"] == "next"
    assert "dom" not in result and "visible_text" not in result
    assert len(json.dumps(result, ensure_ascii=False)) < 4000
    assert "dom_excerpt" not in result
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_dom_mode_remains_explicitly_available(monkeypatch):
    raw = "<html><body>" + "".join(f"<p>{'body ' * 100} {index}</p>" for index in range(50)) + "</body></html>"
    monkeypatch.setattr(server, "_probe_http_response", AsyncMock(return_value=html_response(raw)))
    result = await server.probe_http(URL, mode="dom")
    assert 3000 < len(result["dom"]) <= 16000
    assert len(result["visible_text"]) <= 1200


@pytest.mark.asyncio
async def test_probe_select_counts_full_document_and_bounds_samples(monkeypatch):
    raw = "<html><body>" + "".join(f"<p>{'content ' * 300} {index}</p>" for index in range(90)) + "<h1>Footer Title</h1></body></html>"
    monkeypatch.setattr(server, "_probe_http_response", AsyncMock(return_value=html_response(raw)))
    result = await server.probe_http_select(URL, {"paragraphs": "//p", "title": "//h1"}, max_samples=2, max_chars=100)
    assert result["status"] == "ready"
    assert result["fields"]["paragraphs"]["count"] == 90
    assert len(result["fields"]["paragraphs"]["samples"]) == 2
    assert all(len(sample) <= 100 for sample in result["fields"]["paragraphs"]["samples"])
    assert result["fields"]["title"]["samples"] == ["Footer Title"]
    assert "dom" not in result and "visible_text" not in result


@pytest.mark.asyncio
async def test_probe_select_supports_relative_css_fields_and_href_attributes(monkeypatch):
    raw = "<html><body>" + "".join(f'<article class="item"><h2>Title {index}</h2><a href="{index}.html">Link</a></article>' for index in range(20)) + "</body></html>"
    monkeypatch.setattr(server, "_probe_http_response", AsyncMock(return_value=html_response(raw)))
    result = await server.probe_http_select(
        URL,
        {"title": "h2", "url": "a"},
        selector_type="css",
        scope_selector="article.item",
        attributes={"url": "href"},
    )
    assert result["scope_count"] == 20
    assert result["fields"]["title"]["count"] == 20
    assert result["fields"]["url"]["count"] == 20
    assert result["fields"]["url"]["samples"] == [URL + "0.html", URL + "1.html"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["probe_http", "probe_http_select"])
async def test_http_404_is_small_recoverable_result_not_a_tool_error(monkeypatch, tool_name):
    bad_url = URL + "index_2.html"
    client, checks, robots = install_http_client(monkeypatch, html_response("<html>Not Found</html>" * 1000, status_code=404, url=bad_url))
    if tool_name == "probe_http":
        result = await server.probe_http(bad_url)
    else:
        result = await server.probe_http_select(bad_url, {"title": "//h1"})
    assert result["status"] == "http_error"
    assert result["status_code"] == 404
    assert result["retryable"] is False
    assert "actual pagination_links" in result["next_action"]
    assert "dom" not in result and "visible_text" not in result
    assert len(json.dumps(result)) < 1000
    client.request.assert_awaited_once()
    checks.assert_called_once_with(bad_url, allowed_domains=["example.com"], block_private_networks=True)
    robots.assert_called_once_with(bad_url)


@pytest.mark.asyncio
async def test_http_private_network_violation_is_not_converted_to_recoverable_404(monkeypatch):
    client, checks, robots = install_http_client(monkeypatch, html_response("irrelevant"))
    checks.side_effect = PolicyViolation("private target is forbidden")
    with pytest.raises(PolicyViolation, match="private target"):
        await server.probe_http(URL)
    client.request.assert_not_awaited()
    robots.assert_not_called()


@pytest.mark.asyncio
async def test_http_robots_denial_still_blocks_before_request(monkeypatch):
    client, checks, robots = install_http_client(monkeypatch, html_response("irrelevant"))
    robots.return_value = False
    result = await server.probe_http(URL)
    assert result["status"] == "blocked" and result["code"] == "policy_blocked"
    assert result["retryable"] is False and result["retry_limit"] == 0
    assert result["requires_user_confirmation"] is True
    checks.assert_called_once()
    client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_redirect_to_another_domain_stays_blocked(monkeypatch):
    response = httpx.Response(302, headers={"location": "https://other.example/news/"}, request=httpx.Request("GET", URL))
    client, _, _ = install_http_client(monkeypatch, response)
    with pytest.raises(PolicyViolation, match="cross-domain"):
        await server.probe_http(URL)
    client.request.assert_awaited_once()
