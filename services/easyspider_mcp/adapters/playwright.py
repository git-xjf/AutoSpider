from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.crawl_core.errors import AdapterError
from packages.executor import HybridExecutor, TaskRepository

from .base import EasySpiderAdapter


class PlaywrightAdapter(EasySpiderAdapter):
    """Native adapter retaining the stable MCP lifecycle used by DeerFlow."""

    def __init__(self, runtime_root: str | Path):
        self.repository = TaskRepository(runtime_root)
        self.executor = HybridExecutor(self.repository)

    async def task_register(self, task: dict[str, Any]) -> int:
        return self.repository.register_task(task)

    async def task_get(self, task_id: int) -> dict[str, Any]:
        return self.repository.get_task(task_id)

    async def execution_create(self, task_id: int, parameters: dict[str, Any]) -> int:
        return self.repository.create_execution(task_id, parameters)

    async def execution_start(self, execution_id: int, *, headless: bool = True) -> dict[str, Any]:
        return await self.executor.start(execution_id, headless=headless)

    async def execution_logs(self, execution_id: int) -> dict[str, Any]:
        execution = self.repository.get_execution(execution_id)
        path = self.repository.run_dir(execution_id) / "execution.log"
        logs = path.read_text(encoding="utf-8", errors="replace")[-1_000_000:] if path.exists() else ""
        return {
            "status": execution["status"],
            "logs": logs,
            "error": execution.get("error"),
            "failure": execution.get("failure"),
            "record_count": execution.get("record_count", 0),
            "pages_visited": execution.get("pages_visited", 0),
            "engine": execution["task"].get("engine", "playwright"),
        }

    async def execution_stop(self, execution_id: int) -> dict[str, Any]:
        return await self.executor.stop(execution_id)

    async def execution_result(self, execution_id: int, *, limit: int = 20) -> dict[str, Any]:
        execution = self.repository.get_execution(execution_id)
        folder = self.repository.run_dir(execution_id)
        path = folder / "result.json"
        if not path.exists():
            return {
                "records": [],
                "record_count": 0,
                "output_files": [],
                "error": execution.get("error"),
                "status": "not_ready" if execution["status"] in {"created", "starting", "running"} else execution["status"],
            }
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"cannot read execution result: {path}") from exc
        output_files = [str(item) for item in folder.iterdir() if item.is_file() and item.suffix.lower() in {".json", ".csv", ".xlsx"}]
        return {
            "records": records[:limit],
            "record_count": len(records),
            "output_files": output_files,
            "status": "ready",
        }
