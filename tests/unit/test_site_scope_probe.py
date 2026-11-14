from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from packages.crawl_core.errors import PolicyViolation
from services.easyspider_mcp import server


START = "https://cnews.chinadaily.com.cn/news"
SIBLING = "https://jl.chinadaily.com.cn/article"


def _client(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.request.side_effect = [
        httpx.Response(302, headers={"location": SIBLING}, request=httpx.Request("GET", START)),
        httpx.Response(200, text="<h1>Story</h1>", request=httpx.Request("GET", SIBLING)),
    ]
    monkeypatch.setattr(server.httpx, "AsyncClient", Mock(return_value=client))
    monkeypatch.setattr(server.policy, "robots_allowed", Mock(return_value=True))
    return client


@pytest.mark.asyncio
async def test_site_scope_http_probe_allows_sibling_redirect_but_checks_every_hop(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    client = _client(monkeypatch)
    check = Mock()
    monkeypatch.setattr(server.policy, "check_url", check)
    result = await server.probe_http(START)
    assert result["status"] == "ready" and result["url"] == SIBLING
    assert client.request.await_count == 2
    assert [call.kwargs["allowed_domains"] for call in check.call_args_list] == [["chinadaily.com.cn"], ["chinadaily.com.cn"]]


@pytest.mark.asyncio
async def test_explicit_host_scope_still_rejects_sibling_redirect(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    client = _client(monkeypatch)
    monkeypatch.setattr(server.policy, "check_url", Mock())
    with pytest.raises(PolicyViolation, match="cross-domain"):
        await server.probe_http(START, allowed_domains=["cnews.chinadaily.com.cn"])
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_site_scope_does_not_bypass_private_guard_on_sibling_redirect(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    client = _client(monkeypatch)
    monkeypatch.setattr(server.policy, "check_url", Mock(side_effect=[None, PolicyViolation("private target forbidden")]))
    with pytest.raises(PolicyViolation, match="private target"):
        await server.probe_http(START)
    assert client.request.await_count == 1
