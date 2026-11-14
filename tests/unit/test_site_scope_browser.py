from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.crawl_core.errors import PolicyViolation
from packages.crawl_core.models import CrawlSpec
from packages.executor import playwright as executor_module
from packages.executor.playwright import PlaywrightExecutor


def _spec(domains):
    return CrawlSpec.model_validate({
        "request_id": "scope", "start_urls": ["https://cnews.chinadaily.com.cn/news"],
        "objective": "Read public same-site articles",
        "fields": [{"name": "title", "description": "Title", "type": "string", "required": True}],
        "traversal": {"mode": "single"},
        "policy": {"allowed_domains": domains}, "output": {"format": "json"},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("host,domains,private,blocked", [
    ("jl.chinadaily.com.cn", [], False, False),
    ("jl.chinadaily.com.cn", ["cnews.chinadaily.com.cn"], False, True),
    ("other.com.cn", [], False, True),
    ("chinadaily.com.cn.evil.example", [], False, True),
    ("jl.chinadaily.com.cn", [], True, True),
])
async def test_playwright_navigation_guard_preserves_site_and_private_boundaries(monkeypatch, host, domains, private, blocked):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    checks = []

    def check_public(value):
        checks.append(value)
        if private:
            raise PolicyViolation("private target forbidden")

    monkeypatch.setattr(executor_module, "assert_public_host", check_public)
    context = SimpleNamespace(route=AsyncMock())
    state = SimpleNamespace(spec=_spec(domains), context=context)
    await PlaywrightExecutor(None)._install_navigation_guard(state)
    guard = context.route.await_args.args[1]
    request = SimpleNamespace(url=f"https://{host}/article", is_navigation_request=lambda: True)
    route = SimpleNamespace(request=request, abort=AsyncMock(), continue_=AsyncMock())
    await guard(route)
    assert route.abort.await_count == int(blocked)
    assert route.continue_.await_count == int(not blocked)
    if not blocked or private:
        assert checks == [host]
    if blocked:
        assert isinstance(state.navigation_policy_error, PolicyViolation)


@pytest.mark.asyncio
async def test_playwright_does_not_fetch_private_non_navigation_resources(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")

    def blocked(_host):
        raise PolicyViolation("private resource forbidden")

    monkeypatch.setattr(executor_module, "assert_public_host", blocked)
    context = SimpleNamespace(route=AsyncMock())
    await PlaywrightExecutor(None)._install_navigation_guard(SimpleNamespace(spec=_spec([]), context=context))
    route = SimpleNamespace(
        request=SimpleNamespace(url="http://127.0.0.1/admin", is_navigation_request=lambda: False),
        abort=AsyncMock(), continue_=AsyncMock(),
    )
    await context.route.await_args.args[1](route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.continue_.assert_not_awaited()


@pytest.mark.asyncio
async def test_playwright_still_allows_public_third_party_cdn_resources(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    checked = []
    monkeypatch.setattr(executor_module, "assert_public_host", lambda host: checked.append(host))
    context = SimpleNamespace(route=AsyncMock())
    await PlaywrightExecutor(None)._install_navigation_guard(SimpleNamespace(spec=_spec([]), context=context))
    route = SimpleNamespace(
        request=SimpleNamespace(url="https://public.cdn.example/app.js", is_navigation_request=lambda: False),
        abort=AsyncMock(), continue_=AsyncMock(),
    )
    await context.route.await_args.args[1](route)
    route.continue_.assert_awaited_once()
    route.abort.assert_not_awaited()
    assert checked == ["public.cdn.example"]


@pytest.mark.asyncio
async def test_popup_request_without_a_created_frame_is_still_guarded(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")

    class PendingPopup:
        url = "https://unrelated.example/article"

        def is_navigation_request(self):
            return True

        @property
        def frame(self):
            raise executor_module.PlaywrightError("Frame for this navigation request is not available")

    context = SimpleNamespace(route=AsyncMock())
    state = SimpleNamespace(spec=_spec([]), context=context)
    await PlaywrightExecutor(None)._install_navigation_guard(state)
    route = SimpleNamespace(request=PendingPopup(), abort=AsyncMock(), continue_=AsyncMock())
    await context.route.await_args.args[1](route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    assert isinstance(state.navigation_policy_error, PolicyViolation)


@pytest.mark.asyncio
async def test_blocked_optional_iframe_does_not_poison_article_execution(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    context = SimpleNamespace(route=AsyncMock())
    state = SimpleNamespace(spec=_spec([]), context=context, navigation_policy_error=None)
    await PlaywrightExecutor(None)._install_navigation_guard(state)
    route = SimpleNamespace(
        request=SimpleNamespace(
            url="https://unrelated.example/ad", is_navigation_request=lambda: True,
            frame=SimpleNamespace(parent_frame=object()),
        ), abort=AsyncMock(), continue_=AsyncMock(),
    )
    await context.route.await_args.args[1](route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    assert state.navigation_policy_error is None
