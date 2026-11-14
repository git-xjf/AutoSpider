from __future__ import annotations

import http.server
import json
import threading
from urllib.parse import urlsplit

import pytest

from packages.crawl_core.drafts import CrawlDraftRepository, build_crawl_draft
from packages.site_memory import SiteMemoryRepository
from packages.validator.metrics import is_null
from services.easyspider_mcp import server as mcp_server
from services.easyspider_mcp.adapters.playwright import PlaywrightAdapter


class RecordingAdapter(PlaywrightAdapter):
    """Record lifecycle calls while delegating every fetch to the real executor."""

    def __init__(self, runtime_root):
        super().__init__(runtime_root)
        self.registered = []

    async def task_register(self, task):
        self.registered.append(task)
        return await super().task_register(task)


@pytest.fixture
def minority_template_site():
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append(path)
            if path == "/list.html":
                items = []
                for index in range(10):
                    route = "news" if index < 8 else "special"
                    items.append(
                        f'<article class="item"><a class="title" '
                        f'href="/{route}/{1000 + index}.html">Article {index}</a></article>'
                    )
                body = "<html><body>" + "".join(items) + "</body></html>"
            elif path.startswith(("/news/", "/special/")):
                index = int(path.rsplit("/", 1)[1].split(".", 1)[0]) - 1000
                if not 0 <= index < 10:
                    self.send_error(404)
                    return
                class_name = "content" if index < 8 else "alternate"
                body = (
                    f'<html><body><main class="{class_name}">'
                    f"Article body for entry {index}."
                    "</main>"
                    # A required selector with no match already aborts execution.
                    # This regression exercises a matched-but-empty legacy slot.
                    + ('<div class="content"></div>' if index >= 8 else "")
                    + "</body></html>"
                )
            else:
                self.send_error(404)
                return
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            pass

    website = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=website.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{website.server_port}/list.html", requests
    finally:
        website.shutdown()
        website.server_close()
        thread.join(timeout=5)


def create_local_draft(url, selector, strategy):
    spec, page, plan = build_crawl_draft(
        url=url,
        field_selectors={"title": "a.title", "url": "a.title", "content": selector},
        item_selector="article.item",
        selector_type="css",
        mode="http_html",
        detail_fields=["content"],
        detail_link_selector="a.title",
        item_limit=10,
        objective="Collect all ten articles including the minority detail template",
    )
    spec.policy.respect_robots = False
    spec.policy.block_private_networks = False
    spec.execution.request_delay_ms = 0
    spec.execution.sample_strategy = strategy
    pending = list(plan.steps)
    while pending:
        step = pending.pop()
        if step.op.value == "BACK":
            step.seconds = 0
        pending.extend(step.body)
    return mcp_server.drafts.create(spec, page, plan), spec


def legacy_global_gate_passes(spec, records, report):
    """Reproduce the pre-stratification gate on records from actual HTTP runs."""
    metrics = report["metrics"]
    non_stratum_hard_issues = [
        issue for issue in report["issues"]
        if issue["severity"] == "high" and issue["type"] != "STRATUM_HIGH_NULL_RATE"
    ]
    required = [field.name for field in spec.fields if field.required]
    return (
        bool(records)
        and not non_stratum_hard_issues
        and all(sum(is_null(row.get(name)) for row in records) / len(records) <= 0.2 for name in required)
        and metrics["required_field_coverage"] >= 0.9
        and metrics["required_field_null_rate"] <= 0.2
        and metrics["duplicate_rate"] <= 0.2
        and metrics["semantic_score"] >= 0.7
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy,failed_phase,wrong_task_count", [
    ("prefix", "full", 2),
    ("boundary_spread", "sample", 1),
])
async def test_real_http_coverage_gate_blocks_minority_failure_then_accepts_repair(
    monkeypatch, tmp_path, minority_template_site, strategy, failed_phase, wrong_task_count,
):
    url, requests = minority_template_site
    adapter = RecordingAdapter(tmp_path / "executions")
    monkeypatch.setattr(mcp_server, "adapter", adapter)
    monkeypatch.setattr(mcp_server, "drafts", CrawlDraftRepository(tmp_path / "drafts"))
    monkeypatch.setattr(mcp_server, "memory", SiteMemoryRepository(tmp_path / "memory.db"))

    wrong_id, spec = create_local_draft(url, ".content", strategy)
    wrong = await mcp_server.crawl_draft_execute(wrong_id, sample_size=5, max_wait_seconds=15)
    assert wrong["execution_status"] == "completed", wrong
    assert wrong["phase"] == failed_phase
    assert len(adapter.registered) == wrong_task_count
    wrong_records = (await adapter.execution_result(wrong["execution_id"], limit=20))["records"]
    report = wrong["validation"]
    assert legacy_global_gate_passes(spec, wrong_records, report)
    assert report["status"] == "FAIL"
    assert report["coverage_certificate"]["complete"] is False
    assert report["coverage_certificate"]["failed_stratum_field_pairs"] == 1
    assert wrong["validation_complete"] is True
    assert wrong["needs_reanalysis"] is True
    assert "memory_committed" not in wrong
    assert mcp_server.memory.lookup_workflows("127.0.0.1") == []
    assert len(report["evidence_requests"]) == 1
    request = report["evidence_requests"][0]
    assert request["fields"] == ["content"]
    assert request["page_scope"] == "detail"
    assert all("/special/" in value for value in request["representative_urls"])
    assert len(request["representative_urls"]) <= 2
    assert request["max_chars"] <= 4000
    assert "Article body" not in json.dumps(request)
    if strategy == "prefix":
        assert wrong["sample_validation"]["status"] == "PASS"
        assert len(wrong_records) == 10
        assert sum(is_null(row["content"]) for row in wrong_records) == 2
        sample_records = (await adapter.execution_result(wrong["sample_execution_id"], limit=20))["records"]
        assert all("/news/" in row["url"] for row in sample_records)
    else:
        assert len(wrong_records) == 5
        assert [row["title"] for row in wrong_records] == ["Article 0", "Article 2", "Article 5", "Article 7", "Article 9"]
        assert "sample_validation" not in wrong
    wrong_requests = list(requests)

    repaired_id, _ = create_local_draft(url, ".content, .alternate", strategy)
    repaired = await mcp_server.crawl_draft_execute(repaired_id, sample_size=5, max_wait_seconds=15)
    assert repaired["execution_status"] == "completed", repaired
    assert repaired["phase"] == "full"
    assert repaired["sample_validation"]["status"] == "PASS"
    assert repaired["validation"]["status"] == "PASS"
    assert repaired["validation"]["coverage_certificate"]["complete"] is True
    assert repaired["validation"]["evidence_requests"] == []
    assert repaired["validation_complete"] is True
    assert repaired["memory_committed"] is True
    repaired_records = (await adapter.execution_result(repaired["execution_id"], limit=20))["records"]
    assert len(repaired_records) == 10
    assert all(row["content"] == f"Article body for entry {index}." for index, row in enumerate(repaired_records))
    assert len(mcp_server.memory.lookup_workflows("127.0.0.1")) == 1
    assert len(adapter.registered) == wrong_task_count + 2
    assert sum(path == "/list.html" for path in wrong_requests) == wrong_task_count
    assert sum(path != "/list.html" for path in wrong_requests) == (15 if strategy == "prefix" else 5)
    repaired_requests = requests[len(wrong_requests):]
    assert sum(path == "/list.html" for path in repaired_requests) == 2
    assert sum(path != "/list.html" for path in repaired_requests) == 15

    print(json.dumps({
        "experiment": "actual_local_http_pipeline",
        "sample_strategy": strategy,
        "wrong_selector": {
            "phase": failed_phase,
            "evaluated_records": len(wrong_records),
            "legacy_global_gate": "PASS",
            "stratum_gate": report["status"],
            "global_required_null_rate": report["metrics"]["required_field_null_rate"],
            "failed_stratum_field_pairs": 1,
            "memory_committed": False,
            "http_list_requests": wrong_task_count,
            "http_detail_requests": len(wrong_requests) - wrong_task_count,
        },
        "repaired_selector": {
            "full_records": len(repaired_records),
            "sample_gate": repaired["sample_validation"]["status"],
            "full_gate": repaired["validation"]["status"],
            "memory_committed": repaired["memory_committed"],
            "http_list_requests": 2,
            "http_detail_requests": 15,
        },
        "scope": "Controlled local HTTP fixture with an empty legacy selector slot; no model or public-site accuracy claim.",
    }, ensure_ascii=False))
