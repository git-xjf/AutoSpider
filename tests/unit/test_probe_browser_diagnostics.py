from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from packages.crawl_core.errors import InspectionError, PolicyViolation
from packages.inspector import browser as module
from packages.inspector.browser import BrowserInspector


URL = "https://example.com/news/"


def install_browser(monkeypatch, *, goto=None):
    page = SimpleNamespace(
        goto=AsyncMock(side_effect=goto),
        title=AsyncMock(return_value="News"),
        on=Mock(),
        set_default_timeout=Mock(),
        url=URL,
        is_closed=Mock(return_value=False),
        locator=Mock(),
    )
    context = SimpleNamespace(
        route=AsyncMock(), new_page=AsyncMock(return_value=page), close=AsyncMock()
    )
    browser = SimpleNamespace(
        new_context=AsyncMock(return_value=context), close=AsyncMock()
    )
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)),
        stop=AsyncMock(),
    )
    monkeypatch.setattr(
        module,
        "async_playwright",
        lambda: SimpleNamespace(start=AsyncMock(return_value=playwright)),
    )
    monkeypatch.setattr(module, "assert_public_host", lambda _host: None)
    return page, context, browser


def request(url, *, navigation=True, parent=None):
    return SimpleNamespace(
        url=url,
        is_navigation_request=lambda: navigation,
        frame=SimpleNamespace(parent_frame=parent),
    )


async def run_guard(context, target):
    response = SimpleNamespace(status=200, headers={}, dispose=AsyncMock())
    route = SimpleNamespace(
        abort=AsyncMock(),
        continue_=AsyncMock(),
        fetch=AsyncMock(return_value=response),
        fulfill=AsyncMock(),
    )
    await context.route.await_args.args[1](route, target)
    return route


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_required_redirect_keeps_typed_policy_violation(monkeypatch, private):
    _page, context, browser = install_browser(monkeypatch)

    def check_public(host):
        if private and host == "other.example":
            raise PolicyViolation("private target forbidden")

    monkeypatch.setattr(module, "assert_public_host", check_public)

    async def redirect(*_args, **_kwargs):
        route = await run_guard(
            context, request("https://other.example/news/?token=PRIVATE_REJECTED_TOKEN")
        )
        route.abort.assert_awaited_once_with("blockedbyclient")
        raise PlaywrightError("net::ERR_FAILED Call log: PRIVATE_REJECTED_TOKEN")

    _page.goto.side_effect = redirect
    inspector = BrowserInspector()
    with pytest.raises(PolicyViolation) as caught:
        await inspector.open(URL, allowed_domains=["example.com"])
    assert "PRIVATE_REJECTED_TOKEN" not in str(caught.value)
    assert not inspector._sessions
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("navigation", [False, True])
async def test_optional_private_asset_or_iframe_does_not_poison_open(
    monkeypatch, navigation
):
    page, context, _browser = install_browser(monkeypatch)

    def check_public(host):
        if host == "127.0.0.1":
            raise PolicyViolation("private target forbidden")

    monkeypatch.setattr(module, "assert_public_host", check_public)

    async def load(*_args, **_kwargs):
        route = await run_guard(
            context,
            request("http://127.0.0.1/admin", navigation=navigation, parent=object()),
        )
        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_awaited()

    page.goto.side_effect = load
    inspector = BrowserInspector()
    try:
        result = await inspector.open(URL, allowed_domains=["example.com"])
        assert result["status"] == "ready"
        assert result["title"] == "News"
    finally:
        await inspector.shutdown()


@pytest.mark.asyncio
async def test_pending_popup_without_frame_remains_guarded(monkeypatch):
    page, context, _browser = install_browser(monkeypatch)

    class PendingPopup:
        url = "https://unrelated.example/news/"

        def is_navigation_request(self):
            return True

        @property
        def frame(self):
            raise PlaywrightError("Frame for this navigation request is not available")

    async def load(*_args, **_kwargs):
        route = await run_guard(context, PendingPopup())
        route.abort.assert_awaited_once_with("blockedbyclient")
        raise PlaywrightError("net::ERR_FAILED")

    page.goto.side_effect = load
    with pytest.raises(PolicyViolation, match="cross-domain"):
        await BrowserInspector().open(URL, allowed_domains=["example.com"])


@pytest.mark.asyncio
async def test_unexpected_target_verification_failure_still_fails_closed(monkeypatch):
    page, context, _browser = install_browser(monkeypatch)

    def failed(_host):
        raise RuntimeError("PRIVATE_REJECTED_TOKEN")

    monkeypatch.setattr(module, "assert_public_host", failed)

    async def load(*_args, **_kwargs):
        route = await run_guard(context, request(URL))
        route.abort.assert_awaited_once_with("blockedbyclient")
        # A policy denial must be visible even if goto itself does not throw.

    page.goto.side_effect = load
    with pytest.raises(PolicyViolation, match="safety verification failed") as caught:
        await BrowserInspector().open(URL, allowed_domains=["example.com"])
    assert "PRIVATE_REJECTED_TOKEN" not in str(caught.value)


@pytest.mark.asyncio
async def test_navigation_timeout_has_static_bounded_diagnostic(monkeypatch):
    _page, context, browser = install_browser(
        monkeypatch, goto=PlaywrightTimeoutError("PRIVATE_REJECTED_TOKEN " * 200)
    )
    with pytest.raises(InspectionError) as caught:
        await BrowserInspector().open(URL, allowed_domains=["example.com"])
    assert caught.value.code == "navigation_timeout"
    assert caught.value.parameter == "url"
    assert str(caught.value) == "browser probe navigation failed"
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool", ["selector_test", "snapshot", "network", "network_jsonpath_test"]
)
async def test_unknown_session_errors_do_not_echo_input(tool):
    inspector = BrowserInspector()
    arguments = {
        "selector_test": ("xpath", "//h1"),
        "snapshot": (),
        "network": (),
        "network_jsonpath_test": ("$.title",),
    }
    with pytest.raises(InspectionError) as caught:
        await getattr(inspector, tool)("PRIVATE_REJECTED_TOKEN", *arguments[tool])
    assert caught.value.code == "session_not_found"
    assert caught.value.parameter == "session_id"
    assert "PRIVATE_REJECTED_TOKEN" not in str(caught.value)


def session_inspector(*, selector_error=None, closed=False):
    inspector = BrowserInspector()
    locator = SimpleNamespace(
        count=AsyncMock(return_value=2, side_effect=selector_error),
        evaluate_all=AsyncMock(return_value=[{"text": "Title", "tag": "h1"}]),
    )
    page = SimpleNamespace(
        locator=Mock(return_value=locator), is_closed=Mock(return_value=closed)
    )
    inspector._sessions["existing"] = SimpleNamespace(page=page)
    return inspector, page


@pytest.mark.asyncio
async def test_selector_failure_is_bounded_and_does_not_echo_call_logs():
    inspector, _page = session_inspector(
        selector_error=PlaywrightError("PRIVATE_REJECTED_TOKEN " * 1000)
    )
    result = await inspector.selector_test(
        "existing", "xpath", "//*[contains(@id,'PRIVATE_REJECTED_TOKEN')]"
    )
    assert result["valid"] is False
    assert result["match_count"] == 0 and result["samples"] == []
    assert result["code"] == "selector_evaluation_failed"
    assert result["parameter"] == "selector" and result["retry_limit"] == 1
    assert "XPath 1.0" in result["next_action"]
    serialized = json.dumps(result)
    assert "PRIVATE_REJECTED_TOKEN" not in serialized
    assert len(serialized) < 700


@pytest.mark.asyncio
async def test_browser_selector_success_preserves_existing_contract():
    inspector, _page = session_inspector()
    assert await inspector.selector_test("existing", "xpath", "//h1") == {
        "valid": True,
        "match_count": 2,
        "samples": [{"text": "Title", "tag": "h1"}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector_type,selector,code",
    [
        ("jsonpath", "$.title", "invalid_selector_type"),
        ("xpath", "", "invalid_selector"),
        ("xpath", "x" * 2001, "invalid_selector"),
    ],
)
async def test_browser_selector_invalid_input_does_not_attempt_evaluation(
    selector_type, selector, code
):
    inspector, page = session_inspector()
    result = await inspector.selector_test("existing", selector_type, selector)
    assert result["valid"] is False and result["code"] == code
    page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_closed_page_is_session_error_not_selector_error():
    inspector, page = session_inspector(closed=True)
    with pytest.raises(InspectionError) as caught:
        await inspector.selector_test("existing", "xpath", "//h1")
    assert caught.value.code == "session_closed"
    page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_page_closing_during_selector_evaluation_is_not_invalid_selector():
    inspector, page = session_inspector(
        selector_error=PlaywrightError("Target page closed PRIVATE_REJECTED_TOKEN")
    )
    page.is_closed.side_effect = [False, True]
    with pytest.raises(InspectionError) as caught:
        await inspector.selector_test("existing", "xpath", "//h1")
    assert caught.value.code == "session_closed"
    assert "PRIVATE_REJECTED_TOKEN" not in str(caught.value)


@pytest.mark.asyncio
async def test_browser_context_creation_failure_closes_launched_browser(monkeypatch):
    _page, _context, browser = install_browser(monkeypatch)
    browser.new_context.side_effect = PlaywrightError("context unavailable")
    with pytest.raises(PlaywrightError):
        await BrowserInspector().open(URL, allowed_domains=["example.com"])
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_page_creation_failure_closes_context_and_browser(monkeypatch):
    _page, context, browser = install_browser(monkeypatch)
    context.new_page.side_effect = PlaywrightError("page unavailable")
    with pytest.raises(PlaywrightError):
        await BrowserInspector().open(URL, allowed_domains=["example.com"])
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_context_blocks_service_worker_route_bypass(monkeypatch):
    _page, _context, browser = install_browser(monkeypatch)
    inspector = BrowserInspector()
    try:
        await inspector.open(URL, allowed_domains=["example.com"])
        browser.new_context.assert_awaited_once_with(service_workers="block")
    finally:
        await inspector.shutdown()
