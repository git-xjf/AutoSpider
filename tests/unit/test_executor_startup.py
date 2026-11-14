import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.errors import PolicyViolation
from packages.crawl_core.models import CrawlSpec, PageModel
from packages.crawl_core.planner import build_crawl_plan
from services.easyspider_mcp.adapters.playwright import PlaywrightAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["policy", "browser"])
async def test_preflight_failure_becomes_terminal(tmp_path, monkeypatch, failure):
    fixtures = Path(__file__).parents[1] / "fixtures"
    spec = CrawlSpec.model_validate_json((fixtures / "crawl_spec.json").read_text(encoding="utf-8"))
    page = PageModel.model_validate_json((fixtures / "page_model.json").read_text(encoding="utf-8"))
    adapter = PlaywrightAdapter(tmp_path)
    task_id = await adapter.task_register(PlaywrightCompiler().compile(spec, build_crawl_plan(spec, page)))
    execution_id = await adapter.execution_create(task_id, {})

    def check(_spec):
        if failure == "policy":
            raise PolicyViolation("fake DNS rejected")

    monkeypatch.setattr(adapter.executor.policy, "check", check)
    monkeypatch.setattr(
        adapter.executor.playwright,
        "_run_browser",
        AsyncMock(side_effect=RuntimeError("browser unavailable")),
    )
    await adapter.execution_start(execution_id)
    async with asyncio.timeout(3):
        while (await adapter.execution_logs(execution_id))["status"] == "starting":
            await asyncio.sleep(0.01)
    status = await adapter.execution_logs(execution_id)
    assert status["status"] == "failed"
    assert ("fake DNS rejected" if failure == "policy" else "browser unavailable") in status["error"]
    assert "ERROR" in status["logs"]
    assert execution_id not in adapter.executor._jobs


@pytest.mark.asyncio
async def test_long_poll_returns_terminal_and_validates_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("AUTOSPIDER_MEMORY_DB", str(tmp_path / "memory.db"))
    from services.easyspider_mcp import server
    monkeypatch.setattr(server.adapter, "execution_logs", AsyncMock(side_effect=[{"status": "running"}, {"status": "failed", "error": "DNS"}]))
    assert (await server.execution_logs(1, wait_seconds=2))["error"] == "DNS"
    with pytest.raises(ValueError):
        await server.execution_logs(1, wait_seconds=31)
