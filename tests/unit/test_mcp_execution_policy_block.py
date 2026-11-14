from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from services.easyspider_mcp import server
from test_mcp_atomic_execution import FIXTURES, CompletedAdapter, setup_reuse
from test_mcp_draft_execution import setup_draft


POLICY_ERROR = "PolicyViolation: domain 'jl.chinadaily.com.cn' is not in allowed_domains"


def failed_logs(error):
    return {"status": "failed", "record_count": 7, "pages_visited": 8, "error": error}


def assert_policy_blocked(result):
    assert result["execution_status"] == "failed"
    assert result["status"] == "blocked"
    assert result["code"] == "policy_blocked"
    assert result["retryable"] is False
    assert result["requires_user_confirmation"] is True
    assert result["needs_reanalysis"] is False
    assert "authority" in result["next_action"]
    assert "selector" in result["next_action"]
    assert "jl.chinadaily.com.cn" in result["error"]
    assert len(json.dumps(result, ensure_ascii=False)) < 2000


@pytest.mark.asyncio
async def test_atomic_policy_failure_is_an_authority_block_not_selector_repair(monkeypatch):
    spec = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    page = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    adapter = CompletedAdapter([])
    adapter.execution_logs = AsyncMock(return_value=failed_logs(POLICY_ERROR))
    adapter.execution_result = AsyncMock()
    monkeypatch.setattr(server, "adapter", adapter)
    result = await server.crawl_execute_validated(spec, server.crawl_plan_build(spec, page))
    assert_policy_blocked(result)
    adapter.execution_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_sample", [True, False])
async def test_draft_policy_failure_caches_block_without_memory_or_repeat_execution(monkeypatch, tmp_path, fail_sample):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path, item_limit=3)
    completed = await adapter.execution_logs(11)
    adapter.execution_logs = AsyncMock(side_effect=[failed_logs(POLICY_ERROR)] if fail_sample else [completed, failed_logs(POLICY_ERROR)])
    result = await server.crawl_draft_execute(draft_id, sample_size=2)
    assert_policy_blocked(result)
    assert result["phase"] == ("sample" if fail_sample else "full")
    assert len(adapter.registered) == (1 if fail_sample else 2)
    assert "memory_committed" not in result
    assert server.memory.lookup_workflows("example.com") == []
    cached = await server.crawl_draft_execute(draft_id)
    assert_policy_blocked(cached)
    assert cached["reused_result"] is True
    assert len(adapter.registered) == (1 if fail_sample else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_sample", [True, False])
async def test_reuse_policy_failure_does_not_instruct_new_selector_analysis(monkeypatch, fail_sample):
    adapter = setup_reuse(monkeypatch)
    completed = await adapter.execution_logs(11)
    adapter.execution_logs = AsyncMock(side_effect=[failed_logs(POLICY_ERROR)] if fail_sample else [completed, failed_logs(POLICY_ERROR)])
    result = await server.crawl_reuse_workflow(workflow_id=4, start_url=adapter.source_task["crawl_spec"]["start_urls"][0], item_limit=3, sample_size=2)
    assert_policy_blocked(result)
    assert len(adapter.registered) == (1 if fail_sample else 2)


@pytest.mark.asyncio
async def test_draft_timeout_resumption_classifies_later_policy_failure(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    server.drafts.save_result(draft_id, {"draft_id": draft_id, "task_id": 7, "execution_id": 11, "execution_status": "running", "phase": "full", "timed_out": True})
    monkeypatch.setattr(server, "execution_logs", AsyncMock(return_value=failed_logs(POLICY_ERROR)))
    result = await server.crawl_draft_execute(draft_id)
    assert_policy_blocked(result)
    assert adapter.registered == []
    assert "timed_out" not in result
    assert server.memory.lookup_workflows("example.com") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error,needs_reanalysis", [
    ("AdapterError: selector matched no elements: //main", True),
    ("AdapterError: page says PolicyViolation: not an execution policy failure", False),
])
async def test_execution_failure_uses_leading_type_not_page_text(monkeypatch, tmp_path, error, needs_reanalysis):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    adapter.execution_logs = AsyncMock(return_value=failed_logs(error))
    result = await server.crawl_draft_execute(draft_id)
    assert result["execution_status"] == "failed"
    assert result["needs_reanalysis"] is needs_reanalysis
    assert result.get("status") != "blocked"
    assert "requires_user_confirmation" not in result


@pytest.mark.asyncio
async def test_policy_error_is_bounded_and_redacts_url_credentials(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    error = "PolicyViolation: URL https://user:PRIVATE_PASSWORD@jl.chinadaily.com.cn/article is forbidden " + "x" * 10000
    adapter.execution_logs = AsyncMock(return_value=failed_logs(error))
    result = await server.crawl_draft_execute(draft_id)
    assert_policy_blocked(result)
    assert "PRIVATE_PASSWORD" not in result["error"]
    assert "user:" not in result["error"]
    assert len(result["error"]) < 600


def test_policy_block_does_not_turn_partial_pass_evidence_into_full_validation_or_memory(monkeypatch, tmp_path):
    draft_id, _ = setup_draft(monkeypatch, tmp_path)
    spec, page, plan = server.drafts.get(draft_id)
    report = {"status": "PASS", "overall_score": 1.0, "metrics": {"record_count": 3}}
    result = server._finish_draft_result(draft_id, spec, page, plan, {"task_id": 7, "execution_id": 11, "phase": "full", "execution_status": "failed", "record_count": 7, "error": POLICY_ERROR, "validation": report})
    assert_policy_blocked(result)
    assert result["validation_complete"] is False
    assert "needs_full_validation" not in result
    assert "memory_committed" not in result
    assert server.memory.lookup_workflows("example.com") == []


@pytest.mark.asyncio
async def test_old_cached_policy_failure_is_normalized_without_reexecution(monkeypatch, tmp_path):
    draft_id, adapter = setup_draft(monkeypatch, tmp_path)
    server.drafts.save_result(draft_id, {"task_id": 7, "execution_id": 11, "draft_id": draft_id, "phase": "full", "execution_status": "failed", "error": POLICY_ERROR, "needs_reanalysis": True, "record_count": 7})
    result = await server.crawl_draft_execute(draft_id)
    assert_policy_blocked(result)
    assert result["reused_result"] is True
    assert adapter.registered == []
