from __future__ import annotations

from typing import Any

from packages.crawl_core.errors import AdapterError
from packages.crawl_core.policies import PolicyEngine

from .http import HttpExecutor
from .playwright import PlaywrightExecutor
from .repository import TaskRepository


class HybridExecutor:
    """Dispatch a signed task manifest to its deterministic execution engine."""

    def __init__(self, repository: TaskRepository, policy: PolicyEngine | None = None):
        shared_policy = policy or PolicyEngine()
        self.repository = repository
        self.policy = shared_policy
        self.playwright = PlaywrightExecutor(repository, shared_policy)
        self.http = HttpExecutor(repository, shared_policy)

    @property
    def _jobs(self):
        return {**self.playwright._jobs, **self.http._jobs}

    def _executor_for(self, execution_id: int):
        execution = self.repository.get_execution(execution_id)
        engine = execution["task"].get("engine")
        if engine == "playwright":
            return self.playwright
        if engine in {"http_html", "http_api"}:
            return self.http
        raise AdapterError(f"unsupported AutoSpider engine: {engine!r}")

    async def start(self, execution_id: int, *, headless: bool = True) -> dict[str, Any]:
        return await self._executor_for(execution_id).start(execution_id, headless=headless)

    async def stop(self, execution_id: int) -> dict[str, Any]:
        return await self._executor_for(execution_id).stop(execution_id)
