import asyncio
import socket

import pytest
import respx

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.drafts import build_crawl_draft
from packages.crawl_core.policies import clear_dns_cache
from services.easyspider_mcp.adapters.playwright import PlaywrightAdapter


START = "https://cnews.chinadaily.com.cn/news"


@pytest.mark.asyncio
@pytest.mark.parametrize("detail_host,private,expected", [
    ("jl.chinadaily.com.cn", False, "completed"),
    ("unrelated.example", False, "failed"),
    ("jl.chinadaily.com.cn", True, "failed"),
])
async def test_real_http_engine_follows_only_public_same_site_details(monkeypatch, tmp_path, detail_host, private, expected):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    clear_dns_cache()

    def addresses(host, *_):
        ip = "127.0.0.1" if private and host == detail_host else "93.184.216.34"
        return [(None, None, None, None, (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", addresses)
    detail = f"https://{detail_host}/article"
    spec, _, plan = build_crawl_draft(
        url=START, field_selectors={"title": ".//a", "url": ".//a", "content": "//main"},
        item_selector="//article", detail_fields=["content"], detail_link_selector=".//a", item_limit=1,
    )
    spec.policy.respect_robots = False
    spec.execution.request_delay_ms = 0
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, plan))
    execution_id = await adapter.execution_create(task_id, {})
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(START).respond(200, text=f'<article><a href="{detail}">Public article</a></article>')
            details = router.get(detail).respond(200, text="<main>Complete public article body.</main>")
            await adapter.execution_start(execution_id)
            async with asyncio.timeout(10):
                while (status := await adapter.execution_logs(execution_id))["status"] in {"starting", "running"}:
                    await asyncio.sleep(0.02)
            assert status["status"] == expected, status
            if expected == "completed":
                result = await adapter.execution_result(execution_id)
                assert result["records"] == [{"title": "Public article", "url": detail, "content": "Complete public article body."}]
                assert details.called
            else:
                assert status["error"].startswith("PolicyViolation:")
                assert not details.called
    finally:
        clear_dns_cache()
