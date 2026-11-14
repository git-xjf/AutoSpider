import json
from copy import deepcopy
from pathlib import Path

import pytest

from services.easyspider_mcp import server


FIXTURES = Path(__file__).parents[1] / "fixtures"


class CompletedAdapter:
    def __init__(self, records):
        self.records = records

    async def task_register(self, task):
        assert task["schema"] == "autospider.task/v1"
        return 7

    async def execution_create(self, task_id, parameters):
        assert task_id == 7
        return 11

    async def execution_start(self, execution_id, *, headless=True):
        return {"execution_id": execution_id, "status": "starting"}

    async def execution_logs(self, execution_id):
        return {
            "status": "completed",
            "record_count": len(self.records),
            "pages_visited": 4,
            "error": None,
        }

    async def execution_result(self, execution_id, *, limit=20):
        return {
            "status": "ready",
            "record_count": len(self.records),
            "output_files": ["runtime/runs/11/result.json"],
            "records": self.records[:limit],
        }


@pytest.mark.asyncio
async def test_atomic_execution_returns_qa_and_bounded_preview(monkeypatch):
    spec = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    page = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    records = json.loads((FIXTURES / "expected_records.json").read_text(encoding="utf-8"))
    records[0]["content"] = "x" * 5_000
    plan = server.crawl_plan_build(spec, page)
    monkeypatch.setattr(server, "adapter", CompletedAdapter(records))

    result = await server.crawl_execute_validated(
        spec,
        plan,
        sample_size=3,
        preview_records=1,
        max_field_chars=200,
    )

    assert result["task_id"] == 7
    assert result["execution_id"] == 11
    assert result["execution_status"] == "completed"
    assert result["validation"]["status"] == "PASS"
    assert result["record_count"] == 3
    assert len(result["records_preview"][0]["content"]) < 300
    assert result["records_omitted"] == 2


@pytest.mark.asyncio
async def test_atomic_failed_qa_does_not_echo_full_article_evidence(monkeypatch):
    spec = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    page = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    source = json.loads((FIXTURES / "expected_records.json").read_text(encoding="utf-8"))[0]
    records = [{**source, "title": f"Article {index}", "url": f"https://example.com/{index}",
                "content": "A" * 30000} for index in range(5)]
    monkeypatch.setattr(server, "adapter", CompletedAdapter(records))
    result = await server.crawl_execute_validated(spec, server.crawl_plan_build(spec, page), preview_records=0)
    assert result["validation"]["status"] == "FAIL"
    assert "A" * 1000 not in json.dumps(result)
    assert len(json.dumps(result).encode()) < 10000


class MemoryWorkflow:
    def __init__(self, plan):
        self.plan = plan

    def get_workflow(self, workflow_id):
        assert workflow_id == 4
        return {
            "id": 4,
            "domain": "example.com",
            "success_rate": 1,
            "last_success_at": "2026-10-01T00:00:00Z",
            "easyspider_task_id": 7,
            "crawl_plan": self.plan,
        }


class ReuseAdapter(CompletedAdapter):
    def __init__(self, records, source_task):
        super().__init__(records)
        self.source_task = source_task
        self.registered = []

    async def task_get(self, task_id):
        assert task_id == 7
        return self.source_task

    async def task_register(self, task):
        self.registered.append(deepcopy(task))
        self.current_task = task
        return 7

    async def execution_result(self, execution_id, *, limit=20):
        result = await super().execution_result(execution_id, limit=limit)
        count = self.current_task["crawl_spec"]["traversal"]["item_limit"]
        result["records"] = self.records[: min(limit, count)]
        result["record_count"] = len(result["records"])
        return result


def setup_reuse(monkeypatch):
    spec = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    page = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    records = json.loads((FIXTURES / "expected_records.json").read_text(encoding="utf-8"))
    plan = server.crawl_plan_build(spec, page)
    task = server.compiler.compile(spec, plan)
    adapter = ReuseAdapter(records, task)
    monkeypatch.setattr(server, "adapter", adapter)
    monkeypatch.setattr(server, "memory", MemoryWorkflow(plan))
    return adapter


@pytest.mark.asyncio
async def test_reuse_workflow_validates_sample_and_full_without_model_plan(monkeypatch):
    adapter = setup_reuse(monkeypatch)

    result = await server.crawl_reuse_workflow(
        workflow_id=4,
        start_url=adapter.source_task["crawl_spec"]["start_urls"][0] + "?channel=2",
        item_limit=3,
        sample_size=2,
        output_filename="requested.json",
        preview_records=0,
    )

    assert len(adapter.registered) == 2
    assert adapter.registered[0]["sample"] is True
    assert adapter.registered[1]["sample"] is False
    assert adapter.registered[1]["crawl_spec"]["traversal"]["item_limit"] == 3
    assert adapter.registered[1]["crawl_spec"]["output"]["filename"] == "requested.json"
    assert result["workflow_id"] == 4
    assert result["validation"]["status"] == "PASS"
    assert result["sample_validation"]["status"] == "PASS"
    assert result["records_preview"] == []
    assert "crawl_plan" not in result


@pytest.mark.asyncio
async def test_reuse_workflow_does_not_duplicate_a_sufficient_sample(monkeypatch):
    adapter = setup_reuse(monkeypatch)
    result = await server.crawl_reuse_workflow(
        workflow_id=4,
        start_url=adapter.source_task["crawl_spec"]["start_urls"][0],
        item_limit=2,
    )
    assert len(adapter.registered) == 1
    assert result["sample_is_final"] is True
    assert result["validation"]["status"] == "PASS"


@pytest.mark.asyncio
async def test_reuse_workflow_stops_on_failed_sample(monkeypatch):
    adapter = setup_reuse(monkeypatch)
    adapter.records = [{"title": "wrong"}]
    result = await server.crawl_reuse_workflow(
        workflow_id=4,
        start_url=adapter.source_task["crawl_spec"]["start_urls"][0],
        item_limit=3,
        sample_size=1,
    )
    assert len(adapter.registered) == 1
    assert result["validation"]["status"] == "FAIL"
    assert result["needs_reanalysis"] is True
