"""Real Chromium diagnostics, with only loopback test fixtures and no remote HTTP."""

from __future__ import annotations

import http.server
import threading

import pytest

from packages.crawl_core.errors import PolicyViolation
from packages.inspector import browser as module
from packages.inspector.browser import BrowserInspector


@pytest.fixture
def policy_website():
    requests = []

    class Website(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.headers.get("Host"), self.path))
            target = f"http://localhost:{self.server.server_port}/forbidden"
            locations = {
                "/redirect": target,
                "/chain": "/safe-redirect",
                "/safe-redirect": "/final/final.html",
                "/loop": "/loop",
                "/optional-to-private": target,
                "/optional-safe": "/unused.js",
            }
            if self.path in locations:
                self.send_response(302)
                self.send_header("Location", locations[self.path])
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            body = (
                f'<html><head><title>News</title></head><body><h1>Public article</h1><iframe src="{target}"></iframe></body></html>'
                if self.path == "/iframe"
                else "<html><h1>Forbidden</h1></html>"
            )
            if self.path == "/final/final.html":
                body = '<html><head><title>Final</title></head><body><h1>Final article</h1><a href="next.html">Next</a></body></html>'
            if self.path == "/optional-redirect":
                body = '<html><head><title>News</title></head><body><h1>Public article</h1><iframe src="/optional-to-private"></iframe><img src="/optional-safe"></body></html>'
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *_args):
            pass

    website = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Website)
    worker = threading.Thread(target=website.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{website.server_port}", requests
    finally:
        website.shutdown()
        website.server_close()
        worker.join(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_real_redirect_policy_denial_stays_typed(
    monkeypatch, policy_website, private
):
    url, requests = policy_website

    def public_test_fixture(host):
        if private and host == "localhost":
            raise PolicyViolation("private redirected target forbidden")

    monkeypatch.setattr(module, "assert_public_host", public_test_fixture)
    inspector = BrowserInspector(navigation_timeout_ms=5_000)
    try:
        with pytest.raises(PolicyViolation):
            await inspector.open(url + "/redirect", allowed_domains=["127.0.0.1"])
        assert not inspector._sessions
        assert not any(path == "/forbidden" for _host, path in requests)
    finally:
        await inspector.shutdown()


@pytest.mark.asyncio
async def test_real_optional_private_iframe_does_not_fail_top_level_probe(
    monkeypatch, policy_website
):
    url, requests = policy_website

    def public_test_fixture(host):
        if host == "localhost":
            raise PolicyViolation("private optional iframe forbidden")

    monkeypatch.setattr(module, "assert_public_host", public_test_fixture)
    inspector = BrowserInspector(navigation_timeout_ms=5_000)
    try:
        opened = await inspector.open(url + "/iframe", allowed_domains=["127.0.0.1"])
        assert opened["status"] == "ready" and opened["title"] == "News"
        selected = await inspector.selector_test(opened["session_id"], "xpath", "//h1")
        assert selected["valid"] is True and selected["match_count"] == 1
        assert not any(path == "/forbidden" for _host, path in requests)
    finally:
        await inspector.shutdown()


@pytest.mark.asyncio
async def test_real_allowed_redirect_chain_preserves_final_url_origin_and_links(
    monkeypatch, policy_website
):
    url, requests = policy_website
    monkeypatch.setattr(module, "assert_public_host", lambda _host: None)
    inspector = BrowserInspector(navigation_timeout_ms=5_000)
    try:
        opened = await inspector.open(url + "/chain", allowed_domains=["127.0.0.1"])
        assert opened["url"] == url + "/final/final.html"
        assert opened["title"] == "Final"
        page = inspector._get(opened["session_id"]).page
        assert await page.evaluate("location.origin") == url
        assert (
            await page.locator("a").evaluate("element => element.href")
            == url + "/final/next.html"
        )
        assert [path for _host, path in requests] == [
            "/chain",
            "/safe-redirect",
            "/final/final.html",
        ]
    finally:
        await inspector.shutdown()


@pytest.mark.asyncio
async def test_real_redirect_loop_stops_without_repeating_same_request(
    monkeypatch, policy_website
):
    from packages.crawl_core.errors import InspectionError

    url, requests = policy_website
    monkeypatch.setattr(module, "assert_public_host", lambda _host: None)
    inspector = BrowserInspector(navigation_timeout_ms=5_000)
    try:
        with pytest.raises(InspectionError) as caught:
            await inspector.open(url + "/loop", allowed_domains=["127.0.0.1"])
        assert caught.value.code == "redirect_loop"
        assert [path for _host, path in requests] == ["/loop"]
    finally:
        await inspector.shutdown()


@pytest.mark.asyncio
async def test_real_optional_redirects_never_reach_unchecked_later_hops(
    monkeypatch, policy_website
):
    url, requests = policy_website

    def public_test_fixture(host):
        if host == "localhost":
            raise PolicyViolation("private optional redirect forbidden")

    monkeypatch.setattr(module, "assert_public_host", public_test_fixture)
    inspector = BrowserInspector(navigation_timeout_ms=5_000)
    try:
        opened = await inspector.open(
            url + "/optional-redirect", allowed_domains=["127.0.0.1"]
        )
        assert opened["status"] == "ready" and opened["title"] == "News"
        # DOMContentLoaded need not wait for optional asset responses. The
        # session snapshot carries diagnostics that arrive after open returns.
        await inspector._get(opened["session_id"]).page.wait_for_load_state(
            "load", timeout=2_000
        )
        snapshot = await inspector.snapshot(opened["session_id"])
        assert snapshot["blocked_optional_redirects"] == 1
        assert not any(path in {"/forbidden", "/unused.js"} for _host, path in requests)
    finally:
        await inspector.shutdown()
