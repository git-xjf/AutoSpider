from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.models import CrawlPlan, CrawlSpec, Operation, PageModel, PlanStep
from packages.crawl_core.planner import build_crawl_plan
from services.easyspider_mcp.adapters.playwright import PlaywrightAdapter


FIXTURES = Path(__file__).parents[1] / "fixtures"
WEBSITE = Path(__file__).parents[1] / "websites"


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_size,expected", [(None, 5), (2, 2)])
async def test_delayed_load_more_and_global_limit(tmp_path, website_url, sample_size, expected):
    url = website_url.replace("list.html", "load-more.html")
    spec = CrawlSpec.model_validate({
        "request_id": "load-more", "start_urls": [url], "objective": "five titles",
        "fields": [
            {"name": "title", "description": "Title", "type": "string", "required": True},
            {"name": "summary", "description": "Optional", "type": "string", "required": False},
        ],
        "traversal": {"mode": "list", "item_limit": 5, "max_pages": 3},
        "execution": {"request_delay_ms": 0},
        "policy": {"respect_robots": False, "block_private_networks": False},
        "output": {"format": "json"},
    })
    page = PageModel.model_validate({
        "url": url, "page_type": "PAGINATED_LIST", "confidence": 1,
        "list": {"item_selector": {"type": "css", "value": "article"}},
        "fields": {
            "title": {"page_scope": "list", "selector": "a", "selector_type": "css", "confidence": 1},
            "summary": {"page_scope": "list", "selector": ".missing", "selector_type": "css", "confidence": 1},
        },
        "pagination": {"type": "click", "selector": "#more", "selector_type": "css"},
    })
    adapter = PlaywrightAdapter(tmp_path)
    task = PlaywrightCompiler().compile(spec, build_crawl_plan(spec, page), sample_size)
    task_id = await adapter.task_register(task)
    execution_id = await adapter.execution_create(task_id, {})
    await adapter.execution_start(execution_id)
    async with asyncio.timeout(15):
        while True:
            status = await adapter.execution_logs(execution_id)
            if status["status"] not in {"starting", "running"}:
                break
            await asyncio.sleep(0.05)
    assert status["status"] == "completed", status
    records = (await adapter.execution_result(execution_id, limit=10))["records"]
    expected_titles = ["Title 1", "Title 3"] if sample_size == 2 else [f"Title {i}" for i in range(1, expected + 1)]
    assert [r["title"] for r in records] == expected_titles
    assert all(r["summary"] is None for r in records)
    assert status["logs"].count("CLICK next") == (1 if sample_size is None else 0)


@pytest.fixture
def website_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEBSITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/list.html"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_native_list_detail_execution(tmp_path, website_url):
    spec_data = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    spec_data["start_urls"] = [website_url]
    spec_data["traversal"]["max_pages"] = 1
    spec_data["traversal"]["pagination"] = {"strategy": "none", "max_pages": 1}
    spec_data["policy"] = {
        "allowed_domains": ["127.0.0.1"],
        "respect_robots": False,
        "allow_cross_domain_navigation": False,
        "block_private_networks": False,
    }
    page_data = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    page_data["url"] = website_url
    page_data["pagination"] = {"type": "none"}
    spec = CrawlSpec.model_validate(spec_data)
    page = PageModel.model_validate(page_data)
    plan = build_crawl_plan(spec, page)
    task = PlaywrightCompiler().compile(spec, plan)
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    task_id = await adapter.task_register(task)
    execution_id = await adapter.execution_create(task_id, {"request_delay_ms": 0})

    await adapter.execution_start(execution_id, headless=True)
    for _ in range(100):
        status = await adapter.execution_logs(execution_id)
        if status["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.05)

    assert status["status"] == "completed", status
    result = await adapter.execution_result(execution_id, limit=10)
    assert result["record_count"] == 2
    assert result["records"] == [
        {
            "title": "Alpha launch",
            "publish_time": "2026-09-01",
            "url": website_url.replace("list.html", "detail/alpha.html"),
            "author": "Alice",
            "content": "Alpha article body.",
        },
        {
            "title": "Beta release",
            "publish_time": "2026-09-02",
            "url": website_url.replace("list.html", "detail/beta.html"),
            "author": "Bob",
            "content": "Beta article body.",
        },
    ]


@pytest.mark.asyncio
async def test_native_click_pagination_stops_when_next_is_absent(tmp_path, website_url):
    spec_data = {
        "version": "0.1",
        "request_id": "pagination",
        "start_urls": [website_url],
        "objective": "Collect paginated titles",
        "fields": [
            {"name": "title", "description": "Title", "type": "string", "required": True, "page_scope": "list"},
            {
                "name": "url",
                "description": "URL",
                "type": "url",
                "required": True,
                "page_scope": "list",
                "extract": {"source": "attribute", "attribute": "href"},
            },
        ],
        "traversal": {
            "mode": "list",
            "item_limit": 10,
            "max_pages": 3,
            "pagination": {"strategy": "click", "max_pages": 3},
        },
        "execution": {"request_delay_ms": 0},
        "policy": {
            "allowed_domains": ["127.0.0.1"],
            "respect_robots": False,
            "allow_cross_domain_navigation": False,
            "block_private_networks": False,
        },
        "output": {"format": "json"},
    }
    page_data = {
        "version": "0.1",
        "url": website_url,
        "page_type": "PAGINATED_LIST",
        "confidence": 1,
        "list": {"item_selector": {"type": "css", "value": "article.news-item"}},
        "fields": {
            "title": {"page_scope": "list", "selector": "a.title", "selector_type": "css", "extract": "text", "confidence": 1},
            "url": {
                "page_scope": "list",
                "selector": "a.title",
                "selector_type": "css",
                "extract": "attribute",
                "attribute": "href",
                "confidence": 1,
            },
        },
        "pagination": {"type": "click", "selector": "a.next", "selector_type": "css"},
    }
    spec = CrawlSpec.model_validate(spec_data)
    page = PageModel.model_validate(page_data)
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, build_crawl_plan(spec, page)))
    execution_id = await adapter.execution_create(task_id, {})
    await adapter.execution_start(execution_id)
    for _ in range(100):
        status = await adapter.execution_logs(execution_id)
        if status["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert status["status"] == "completed", status
    result = await adapter.execution_result(execution_id, limit=10)
    assert [record["title"] for record in result["records"]] == ["Alpha launch", "Beta release", "Gamma update"]


@pytest.mark.asyncio
async def test_native_iframe_extract_screenshot_and_download(tmp_path, website_url):
    iframe_url = website_url.replace("list.html", "iframe.html")
    spec = CrawlSpec.model_validate(
        {
            "request_id": "iframe",
            "start_urls": [iframe_url],
            "objective": "iframe fixture",
            "fields": [{"name": "title", "description": "Title", "type": "string", "required": True}],
            "traversal": {"mode": "single", "pagination": {"strategy": "none"}},
            "execution": {"request_delay_ms": 0},
            "policy": {
                "allowed_domains": ["127.0.0.1"],
                "respect_robots": False,
                "block_private_networks": False,
            },
            "output": {"format": "json"},
        }
    )
    plan = CrawlPlan(
        name="iframe",
        start_urls=[iframe_url],
        steps=[
            PlanStep(op=Operation.OPEN_URL, url="{{start_url}}"),
            PlanStep(
                op=Operation.EXTRACT,
                field="title",
                source="text",
                selector="h1",
                selector_type="css",
                frame_selector="iframe#article-frame",
            ),
            PlanStep(
                op=Operation.SCREENSHOT,
                selector="h1",
                selector_type="css",
                frame_selector="iframe#article-frame",
                value="heading.png",
            ),
            PlanStep(
                op=Operation.DOWNLOAD,
                selector="#download",
                selector_type="css",
                frame_selector="iframe#article-frame",
                value="fixture.txt",
            ),
        ],
    )
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, plan))
    execution_id = await adapter.execution_create(task_id, {})
    await adapter.execution_start(execution_id)
    for _ in range(100):
        status = await adapter.execution_logs(execution_id)
        if status["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert status["status"] == "completed", status
    result = await adapter.execution_result(execution_id)
    assert result["records"] == [{"title": "Inside frame"}]
    run_dir = adapter.repository.run_dir(execution_id)
    assert (run_dir / "heading.png").stat().st_size > 0
    assert (run_dir / "downloads" / "fixture.txt").read_text(encoding="utf-8").strip() == "AutoSpider download fixture."


@pytest.mark.asyncio
async def test_native_form_keyboard_hover_and_select_actions(tmp_path, website_url):
    actions_url = website_url.replace("list.html", "actions.html")
    spec = CrawlSpec.model_validate(
        {
            "request_id": "actions",
            "start_urls": [actions_url],
            "objective": "action fixture",
            "fields": [{"name": "result", "description": "Result", "type": "string", "required": True}],
            "traversal": {"mode": "single", "pagination": {"strategy": "none"}},
            "execution": {"request_delay_ms": 0},
            "policy": {"allowed_domains": ["127.0.0.1"], "respect_robots": False, "block_private_networks": False},
            "output": {"format": "json"},
        }
    )
    plan = CrawlPlan(
        name="actions",
        start_urls=[actions_url],
        steps=[
            PlanStep(op=Operation.OPEN_URL, url="{{start_url}}"),
            PlanStep(op=Operation.INPUT, selector="#query", selector_type="css", value="Hello"),
            PlanStep(op=Operation.PRESS, selector="#query", selector_type="css", value="End"),
            PlanStep(op=Operation.SELECT, selector="#kind", selector_type="css", value="news"),
            PlanStep(op=Operation.MOVE, selector="#hover", selector_type="css"),
            PlanStep(op=Operation.CLICK, selector="#apply", selector_type="css"),
            PlanStep(op=Operation.EXTRACT, field="result", source="text", selector="#result", selector_type="css"),
        ],
    )
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, plan))
    execution_id = await adapter.execution_create(task_id, {})
    await adapter.execution_start(execution_id)
    for _ in range(100):
        status = await adapter.execution_logs(execution_id)
        if status["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert status["status"] == "completed", status
    result = await adapter.execution_result(execution_id)
    assert result["records"] == [{"result": "Hello|news|hovered"}]


@pytest.mark.asyncio
async def test_native_list_detail_supports_new_tab(tmp_path, website_url):
    new_tab_url = website_url.replace("list.html", "new_tab.html")
    spec = CrawlSpec.model_validate(
        {
            "request_id": "new-tab",
            "start_urls": [new_tab_url],
            "objective": "new tab fixture",
            "fields": [
                {"name": "title", "description": "Title", "type": "string", "required": True, "page_scope": "list"},
                {"name": "author", "description": "Author", "type": "string", "required": True, "page_scope": "detail"},
            ],
            "traversal": {"mode": "list_detail", "item_limit": 1, "follow_detail": True, "pagination": {"strategy": "none"}},
            "execution": {"request_delay_ms": 0},
            "policy": {"allowed_domains": ["127.0.0.1"], "respect_robots": False, "block_private_networks": False},
            "output": {"format": "json"},
        }
    )
    page = PageModel.model_validate(
        {
            "url": new_tab_url,
            "page_type": "LIST_DETAIL",
            "confidence": 1,
            "list": {"item_selector": {"type": "css", "value": "article.news-item"}},
            "fields": {
                "title": {"page_scope": "list", "selector": "a.title", "selector_type": "css", "extract": "text", "confidence": 1},
                "author": {"page_scope": "detail", "selector": ".article-author", "selector_type": "css", "extract": "text", "confidence": 1},
            },
            "detail": {"enabled": True, "link_selector": "a.title", "selector_type": "css", "open_mode": "new_tab"},
            "waits": {"page_ready_selector": ".article-author", "selector_type": "css", "timeout_seconds": 5},
        }
    )
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, build_crawl_plan(spec, page)))
    execution_id = await adapter.execution_create(task_id, {})
    await adapter.execution_start(execution_id)
    for _ in range(100):
        status = await adapter.execution_logs(execution_id)
        if status["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert status["status"] == "completed", status
    result = await adapter.execution_result(execution_id)
    assert result["records"] == [{"title": "Alpha launch", "author": "Alice"}]
