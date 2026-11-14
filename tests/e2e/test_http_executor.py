from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.drafts import CrawlDraftRepository, build_crawl_draft
from packages.crawl_core.models import CrawlSpec, PageModel
from packages.crawl_core.planner import build_crawl_plan
from packages.inspector import BrowserInspector
from packages.site_memory import SiteMemoryRepository
from services.easyspider_mcp import server as mcp_server
from services.easyspider_mcp.adapters.playwright import PlaywrightAdapter


FIXTURES = Path(__file__).parents[1] / "fixtures"
WEBSITE = Path(__file__).parents[1] / "websites"


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


async def _run(adapter, spec, page):
    task = PlaywrightCompiler().compile(spec, build_crawl_plan(spec, page))
    task_id = await adapter.task_register(task)
    execution_id = await adapter.execution_create(task_id, {"request_delay_ms": 0})
    started = await adapter.execution_start(execution_id)
    async with asyncio.timeout(10):
        while True:
            status = await adapter.execution_logs(execution_id)
            if status["status"] not in {"starting", "running"}:
                break
            await asyncio.sleep(0.02)
    assert status["status"] == "completed", status
    return started, await adapter.execution_result(execution_id, limit=20)


@pytest.mark.asyncio
async def test_static_html_mode_fetches_list_and_details_without_browser(tmp_path, website_url):
    spec_data = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    spec_data["start_urls"] = [website_url]
    spec_data["execution"]["mode"] = "http_html"
    spec_data["execution"]["request_delay_ms"] = 0
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
    page_data["recommended_mode"] = "http_html"
    page_data["pagination"] = {"type": "none"}
    adapter = PlaywrightAdapter(tmp_path / "runtime")

    started, result = await _run(
        adapter,
        CrawlSpec.model_validate(spec_data),
        PageModel.model_validate(page_data),
    )

    assert started["engine"] == "http_html"
    assert result["record_count"] == 2
    assert result["records"][0]["title"] == "Alpha launch"
    assert result["records"][0]["content"] == "Alpha article body."


@pytest.mark.asyncio
async def test_failed_required_field_returns_exact_failure_context_and_new_draft_can_pass(monkeypatch, tmp_path, website_url):
    spec_data = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    spec_data["start_urls"] = [website_url]
    spec_data["execution"].update(mode="http_html", request_delay_ms=0)
    spec_data["traversal"].update(max_pages=1, pagination={"strategy": "none", "max_pages": 1})
    spec_data["policy"] = {"allowed_domains": ["127.0.0.1"], "respect_robots": False, "block_private_networks": False}
    spec = CrawlSpec.model_validate(spec_data)
    page_data = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    page_data.update(url=website_url, recommended_mode="http_html", pagination={"type": "none"})
    page_data["fields"]["content"].update(selector="//missing-body", fallbacks=[])
    page = PageModel.model_validate(page_data)
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    monkeypatch.setattr(mcp_server, "adapter", adapter)
    monkeypatch.setattr(mcp_server, "drafts", CrawlDraftRepository(tmp_path / "drafts"))
    monkeypatch.setattr(mcp_server, "memory", SiteMemoryRepository(tmp_path / "memory.db"))
    draft_id = mcp_server.drafts.create(spec, page, build_crawl_plan(spec, page))
    failed = await mcp_server.crawl_draft_execute(draft_id)
    assert failed["status"] == "needs_repair"
    assert failed["failure"]["field"] == "content"
    assert failed["failure"]["url"].endswith("/detail/alpha.html")
    assert failed["failure"]["page_scope"] == "detail"
    assert failed["needs_reanalysis"] is True
    assert "memory_committed" not in failed
    cached = await mcp_server.crawl_draft_execute(draft_id)
    assert cached["execution_id"] == failed["execution_id"]
    assert cached["reused_result"] is True
    page_data["fields"]["content"]["selector"] = "//article[contains(@class,'content')]"
    repaired_page = PageModel.model_validate(page_data)
    repaired_id = mcp_server.drafts.create(spec, repaired_page, build_crawl_plan(spec, repaired_page))
    repaired = await mcp_server.crawl_draft_execute(repaired_id)
    assert repaired["validation"]["status"] == "PASS"
    assert repaired["memory_committed"] is True


@pytest.mark.asyncio
async def test_memory_reuse_runs_sample_then_full_with_real_http_engine(monkeypatch, tmp_path, website_url):
    spec_data = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    spec_data["start_urls"] = [website_url]
    spec_data["execution"].update(mode="http_html", request_delay_ms=0)
    spec_data["traversal"].update(max_pages=1, pagination={"strategy": "none", "max_pages": 1})
    spec_data["policy"] = {
        "allowed_domains": ["127.0.0.1"],
        "respect_robots": False,
        "block_private_networks": False,
    }
    spec_data["output"] = {"format": "json"}
    page_data = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    page_data.update(url=website_url, recommended_mode="http_html", pagination={"type": "none"})
    spec = CrawlSpec.model_validate(spec_data)
    plan = build_crawl_plan(spec, PageModel.model_validate(page_data))
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    source_task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, plan))
    memory = SiteMemoryRepository(tmp_path / "memory.db")
    memory.commit_validated(
        page_model=page_data,
        crawl_plan=plan.model_dump(mode="json", exclude_none=True),
        crawl_spec_hash="fixture",
        workflow_name=plan.name,
        validation_score=1,
        easyspider_task_id=source_task_id,
    )
    monkeypatch.setattr(mcp_server, "adapter", adapter)
    monkeypatch.setattr(mcp_server, "memory", memory)

    result = await mcp_server.crawl_reuse_workflow(
        workflow_id=1,
        start_url=website_url + "?channel=2",
        item_limit=2,
        sample_size=1,
        output_filename="reused.json",
        max_wait_seconds=10,
    )
    assert result["engine"] == "http_html"
    assert result["sample_validation"]["status"] == "PASS"
    assert result["validation"]["status"] == "PASS"
    assert result["record_count"] == 2
    assert result["limit_reached"] is True
    assert result["records_preview"] == []
    records = (await adapter.execution_result(result["execution_id"]))["records"]
    assert records[0]["content"] == "Alpha article body."


@pytest.mark.asyncio
async def test_compact_draft_runs_sample_full_qa_memory_and_reuses_completed_result(monkeypatch, tmp_path, website_url):
    fixture_page = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    requested_fields = ["title", "url", "publish_time", "content"]
    spec, page, plan = build_crawl_draft(
        url=website_url,
        field_selectors={name: fixture_page["fields"][name]["selector"] for name in requested_fields},
        item_selector=fixture_page["list"]["item_selector"]["value"],
        mode="http_html",
        detail_fields=["content"],
        detail_link_selector=fixture_page["detail"]["link_selector"],
        page_limit=1,
        item_limit=2,
        output_filename="draft-news.json",
    )
    # The fixture is loopback-only. Production drafts retain robots/private
    # network protection; these exceptions apply only to this test's draft.
    spec.policy.respect_robots = False
    spec.policy.block_private_networks = False
    spec.execution.request_delay_ms = 0
    drafts = CrawlDraftRepository(tmp_path / "runtime")
    draft_id = drafts.create(spec, page, plan)
    memory = SiteMemoryRepository(tmp_path / "memory.db")
    adapter = PlaywrightAdapter(tmp_path / "runtime")
    register = AsyncMock(wraps=adapter.task_register)
    monkeypatch.setattr(adapter, "task_register", register)
    monkeypatch.setattr(mcp_server, "adapter", adapter)
    monkeypatch.setattr(mcp_server, "memory", memory)
    monkeypatch.setattr(mcp_server, "drafts", drafts)

    result = await mcp_server.crawl_draft_execute(
        draft_id=draft_id, sample_size=1, max_wait_seconds=10, preview_records=0,
    )

    assert result["engine"] == "http_html"
    assert result["phase"] == "full"
    assert result["sample_is_final"] is False
    assert result["sample_validation"]["status"] == "PASS"
    assert result["validation"]["status"] == "PASS"
    assert result["record_count"] == 2
    assert result["records_preview"] == []
    assert result["memory_committed"] is True
    assert result["sample_execution_id"] != result["execution_id"]
    assert register.await_count == 2
    records = (await adapter.execution_result(result["execution_id"]))["records"]
    assert [record["title"] for record in records] == ["Alpha launch", "Beta release"]
    assert [record["publish_time"] for record in records] == ["2026-09-01", "2026-09-02"]
    assert [record["content"] for record in records] == ["Alpha article body.", "Beta article body."]
    assert all(record["url"].startswith(website_url.rsplit("/", 1)[0] + "/detail/") for record in records)
    stored_records = json.loads(Path(result["output_files"][0]).read_text(encoding="utf-8"))
    assert stored_records == records
    workflow = memory.get_workflow(result["workflow_id"])
    assert workflow["success_rate"] == 1
    assert workflow["easyspider_task_id"] == result["task_id"]
    assert workflow["crawl_plan"]["execution_mode"] == "http_html"
    assert drafts.get_result(draft_id)["execution_id"] == result["execution_id"]

    repeated = await mcp_server.crawl_draft_execute(draft_id=draft_id, sample_size=1, max_wait_seconds=10)

    assert repeated["reused_result"] is True
    assert repeated["execution_id"] == result["execution_id"]
    assert repeated["sample_execution_id"] == result["sample_execution_id"]
    assert repeated["workflow_id"] == result["workflow_id"]
    assert register.await_count == 2


class ApiHandler(http.server.BaseHTTPRequestHandler):
    def _send(self, page: int):
        records = {
            1: [
                {"title": "One", "url": "/detail/1", "score": 1},
                {"title": "Two", "url": "/detail/2", "score": 2},
            ],
            2: [{"title": "Three", "url": "/detail/3", "score": 3}],
            3: [],
        }[page]
        body = json.dumps({"data": {"items": records}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/page":
            body = b"<html><body><script>fetch('/api/news?page=1').then(r => r.json())</script></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        query = parse_qs(urlparse(self.path).query)
        self._send(int(query.get("page", ["1"])[0]))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length) or b"{}")
        self._send(int(data.get("page", 1)))

    def log_message(self, *args):
        return


@pytest.fixture
def api_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/news"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_browser_probe_captures_bounded_json_api_evidence(api_url):
    inspector = BrowserInspector(max_sessions=1)
    page_url = api_url.replace("/api/news", "/page")
    try:
        opened = await inspector.open(
            page_url,
            allowed_domains=["127.0.0.1"],
            block_private_networks=False,
        )
        await asyncio.sleep(0.2)
        captured = await inspector.network(opened["session_id"], limit=5)
        tested = await inspector.network_jsonpath_test(
            opened["session_id"],
            "$.data.items[*].title",
            request_index=-1,
        )
    finally:
        await inspector.shutdown()

    request = next(item for item in captured["requests"] if "/api/news" in item["url"])
    assert request["method"] == "GET"
    assert request["status"] == 200
    assert '"items"' in request["response_preview"]
    assert tested["valid"] is True
    assert tested["match_count"] == 2
    assert tested["samples"] == ["One", "Two"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,location", [("GET", "query"), ("POST", "json")])
async def test_json_api_mode_supports_get_and_post_pagination(tmp_path, api_url, method, location):
    spec = CrawlSpec.model_validate(
        {
            "request_id": f"api-{method.lower()}",
            "start_urls": [api_url],
            "objective": "collect three API records",
            "fields": [
                {"name": "title", "description": "Title", "type": "string", "required": True},
                {"name": "url", "description": "URL", "type": "url", "required": True},
                {"name": "score", "description": "Score", "type": "integer", "required": True},
            ],
            "traversal": {"mode": "list", "item_limit": 3, "max_pages": 3},
            "execution": {"mode": "http_api", "request_delay_ms": 0},
            "policy": {
                "allowed_domains": ["127.0.0.1"],
                "respect_robots": False,
                "block_private_networks": False,
            },
            "output": {"format": "json"},
        }
    )
    page = PageModel.model_validate(
        {
            "url": api_url,
            "page_type": "PAGINATED_LIST",
            "confidence": 1,
            "recommended_mode": "http_api",
            "http_request": {
                "method": method,
                "url": api_url,
                "response_format": "json",
                "pagination": {
                    "type": "page",
                    "page_param": "page",
                    "parameter_location": location,
                    "start_page": 1,
                },
            },
            "list": {"item_selector": {"type": "jsonpath", "value": "$.data.items[*]"}},
            "fields": {
                "title": {"page_scope": "list", "selector": "title", "selector_type": "jsonpath", "confidence": 1},
                "url": {"page_scope": "list", "selector": "url", "selector_type": "jsonpath", "confidence": 1},
                "score": {"page_scope": "list", "selector": "score", "selector_type": "jsonpath", "confidence": 1},
            },
        }
    )

    started, result = await _run(PlaywrightAdapter(tmp_path / method), spec, page)

    assert started["engine"] == "http_api"
    assert [row["title"] for row in result["records"]] == ["One", "Two", "Three"]
    assert [row["score"] for row in result["records"]] == [1, 2, 3]
