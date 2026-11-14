"""Real Chromium regression for the policy/error contract."""
import asyncio
import http.server
import threading

import pytest

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.drafts import build_crawl_draft
from services.easyspider_mcp.adapters.playwright import PlaywrightAdapter


@pytest.mark.asyncio
async def test_real_browser_navigation_block_remains_typed_policy_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")

    class Website(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'<article><a href="https://unrelated.example/article">Public article</a></article>')

        def log_message(self, *_args):
            pass

    website = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Website)
    worker = threading.Thread(target=website.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{website.server_port}/news"
        spec, _, plan = build_crawl_draft(
            url=url, field_selectors={"title": ".//a", "content": "//main"},
            item_selector="//article", detail_fields=["content"], detail_link_selector=".//a",
            mode="playwright", item_limit=1,
        )
        # Explicit test-local exceptions, never used for production drafts.
        spec.policy.block_private_networks = False
        spec.policy.respect_robots = False
        spec.execution.request_delay_ms = 0
        adapter = PlaywrightAdapter(tmp_path / "runtime")
        task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, plan))
        execution_id = await adapter.execution_create(task_id, {})
        await adapter.execution_start(execution_id)
        async with asyncio.timeout(20):
            while (result := await adapter.execution_logs(execution_id))["status"] in {"starting", "running"}:
                await asyncio.sleep(0.05)
        assert result["status"] == "failed"
        assert result["error"] == "PolicyViolation: domain 'unrelated.example' is not in allowed_domains"
        assert "selector matched no elements" not in result["error"]
    finally:
        website.shutdown()
        worker.join(timeout=5)
