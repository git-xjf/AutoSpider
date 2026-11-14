import json
from unittest.mock import AsyncMock

import pytest

from packages.crawl_core.drafts import CrawlDraftRepository
from packages.site_memory import SiteMemoryRepository
from services.easyspider_mcp import server
from test_mcp_atomic_execution import FIXTURES, ReuseAdapter


def setup_draft(monkeypatch, tmp_path, item_limit=3):
    records = json.loads((FIXTURES / "expected_records.json").read_text(encoding="utf-8"))
    adapter = ReuseAdapter(records, {})
    monkeypatch.setattr(server, "adapter", adapter)
    monkeypatch.setattr(server, "drafts", CrawlDraftRepository(tmp_path / "runtime"))
    monkeypatch.setattr(server, "memory", SiteMemoryRepository(tmp_path / "memory.db"))
    draft = server.crawl_draft_create(
        url="https://example.com/news", item_limit=item_limit,
        field_selectors={"title": ".//h2/a", "url": ".//h2/a", "publish_time": ".//time", "content": "//article[@class='content']"},
        item_selector="//article[@class='news-item']", detail_fields=["content"],
        detail_link_selector=".//h2/a", output_filename="draft-result.json",
    )
    return draft["draft_id"], adapter


@pytest.mark.asyncio
async def test_new_site_draft_executes_sample_full_commits_once_and_caches(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    result = await server.crawl_draft_execute(draft_id, sample_size=2)
    assert len(adapter.registered) == 2
    assert adapter.registered[0]["sample"] is True
    assert adapter.registered[1]["sample"] is False
    assert result["validation"]["status"] == "PASS"
    assert result["sample_validation"]["status"] == "PASS"
    assert result["memory_committed"] is True
    assert result["workflow_id"] == 1
    assert result["records_preview"] == []
    assert "crawl_plan" not in result and "crawl_spec" not in result
    cached = await server.crawl_draft_execute(draft_id)
    assert cached["reused_result"] is True
    assert cached["execution_id"] == result["execution_id"]
    assert len(adapter.registered) == 2
    assert len(server.memory.lookup_workflows("example.com")) == 1


@pytest.mark.asyncio
async def test_small_draft_runs_once_with_full_page_bounds(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path, item_limit=2)
    result = await server.crawl_draft_execute(draft_id)
    assert len(adapter.registered) == 1
    assert adapter.registered[0]["sample"] is False
    assert result["sample_is_final"] is True
    assert result["validation"]["status"] == "PASS"


@pytest.mark.asyncio
async def test_failed_sample_never_executes_full_or_commits_memory(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    adapter.records = [{"title": "wrong"}]
    result = await server.crawl_draft_execute(draft_id, sample_size=1)
    assert len(adapter.registered) == 1
    assert result["validation"]["status"] == "FAIL"
    assert result["needs_reanalysis"] is True
    assert "memory_committed" not in result
    assert server.memory.lookup_workflows("example.com") == []


@pytest.mark.asyncio
async def test_compact_schemas_do_not_embed_typed_models():
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    for name in ("crawl_draft_create", "crawl_draft_execute", "probe_http_select"):
        schema = json.dumps(tools[name].inputSchema)
        assert all(model not in schema for model in ("CrawlSpec", "CrawlPlan", "PageModel", "PlanStep"))
        assert len(schema) < 6000


@pytest.mark.asyncio
async def test_timed_out_sample_resumes_original_size_then_runs_full(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    original = server.crawl_execute_validated
    original_qa = server.validate_records
    validated_limits = []

    def record_validation(spec, records):
        validated_limits.append(spec.traversal.item_limit)
        return original_qa(spec, records)

    monkeypatch.setattr(server, "validate_records", record_validation)

    async def timed_sample(spec, plan, **kwargs):
        if kwargs.get("sample_size") is not None:
            task = server.compiler.compile(spec, plan, sample_size=kwargs["sample_size"])
            await adapter.task_register(task)
            return {"task_id": 7, "execution_id": 11, "execution_status": "running",
                    "sample_size": 2, "timed_out": True}
        return await original(spec, plan, **kwargs)

    monkeypatch.setattr(server, "crawl_execute_validated", timed_sample)
    first = await server.crawl_draft_execute(draft_id, sample_size=2)
    assert first["timed_out"] and first["phase"] == "sample"
    monkeypatch.setattr(server, "execution_logs", AsyncMock(return_value={"status": "completed"}))
    # Default sample_size=5 must not overwrite the original two-record sample.
    resumed = await server.crawl_draft_execute(draft_id)
    assert resumed["validation"]["status"] == "PASS"
    assert resumed["sample_validation"]["status"] == "PASS"
    assert resumed["memory_committed"]
    assert len(adapter.registered) == 2
    assert "timed_out" not in resumed
    assert validated_limits == [2, 3]


@pytest.mark.asyncio
async def test_pending_timeout_reuses_execution_without_registering_again(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    server.drafts.save_result(draft_id, {
        "draft_id": draft_id, "task_id": 7, "execution_id": 11,
        "execution_status": "running", "phase": "full", "timed_out": True,
    })
    monkeypatch.setattr(server, "execution_logs", AsyncMock(return_value={"status": "running"}))
    result = await server.crawl_draft_execute(draft_id)
    assert result["reused_execution"]
    assert result["execution_id"] == 11
    assert adapter.registered == []


def test_partial_record_validation_never_commits_full_workflow(monkeypatch, tmp_path):
    draft_id, _ = setup_draft(monkeypatch, tmp_path)
    spec, page, plan = server.drafts.get(draft_id)
    report = {
        "status": "PASS", "overall_score": 1.0, "confidence": 1.0,
        "metrics": {"required_field_coverage": 1.0, "required_field_null_rate": 0.0,
                    "duplicate_rate": 0.0, "semantic_score": 1.0, "record_count": 1000},
        "issues": [], "warnings": [],
    }
    result = server._finish_draft_result(draft_id, spec, page, plan, {
        "phase": "full", "task_id": 7, "record_count": 1500, "validation": report,
    })
    assert result["validation_complete"] is False
    assert result["needs_full_validation"] is True
    assert server.memory.lookup_workflows("example.com") == []


@pytest.mark.asyncio
async def test_failed_qa_evidence_is_bounded_and_can_be_cached(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path, item_limit=10)
    adapter.records = [
        {**adapter.records[0], "title": f"Unique article {index}",
         "url": f"https://example.com/detail/{index}", "content": "A" * 30000}
        for index in range(5)
    ]
    result = await server.crawl_draft_execute(draft_id)
    assert result["validation"]["status"] == "FAIL"
    assert len(json.dumps(result).encode()) < 10000
    assert "A" * 1000 not in json.dumps(result)
    cached = await server.crawl_draft_execute(draft_id)
    assert cached["reused_result"]
    assert len(adapter.registered) == 1
