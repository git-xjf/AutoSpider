from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from packages.crawl_core.errors import AdapterError

from .base import EasySpiderAdapter
from .results import ResultReader


class CLIAdapter(EasySpiderAdapter):
    def __init__(self, easyspider_home: str, executable: str | None = None):
        self.home = Path(easyspider_home).resolve()
        self.tasks = self.home / "tasks"
        self.instances = self.home / "execution_instances"
        self.data_root = self.home / "Data"
        self.executable = Path(executable).resolve() if executable else self.home / "easyspider_executestage.exe"
        self.results = ResultReader(self.data_root)
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        self._logs: dict[int, bytearray] = {}
        self._statuses: dict[int, str] = {}
        for directory in (self.tasks, self.instances, self.data_root):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _next_id(folder: Path) -> int:
        ids = [int(path.stem) for path in folder.glob("*.json") if path.stem.isdigit()]
        return max(ids, default=-1) + 1

    async def task_register(self, task: dict[str, Any]) -> int:
        task_id = self._next_id(self.tasks)
        payload = dict(task)
        payload["id"] = task_id
        (self.tasks / f"{task_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return task_id

    async def task_get(self, task_id: int) -> dict[str, Any]:
        path = self.tasks / f"{task_id}.json"
        if not path.exists():
            raise AdapterError(f"EasySpider task does not exist: {task_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    async def execution_create(self, task_id: int, parameters: dict[str, Any]) -> int:
        task = await self.task_get(task_id)
        execution_id = self._next_id(self.instances)
        task["id"] = execution_id
        if parameters.get("urlList_0"):
            task["links"] = parameters["urlList_0"]
        for item in task.get("inputParameters", []):
            if item["name"] not in parameters:
                continue
            node = task["graph"][int(item["nodeId"])]
            value = parameters[item["name"]]
            if node["option"] == 1:
                node["parameters"]["links"] = value
            elif node["option"] == 4:
                node["parameters"]["value"] = value
            elif node["option"] == 8 and node["parameters"].get("loopType") == 0:
                node["parameters"]["exitCount"] = int(value)
        (self.instances / f"{execution_id}.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        return execution_id

    async def _capture(self, execution_id: int, process: asyncio.subprocess.Process) -> None:
        stdout, stderr = await process.communicate()
        self._logs[execution_id].extend(stdout or b"")
        self._logs[execution_id].extend(stderr or b"")

    async def execution_start(self, execution_id: int, *, headless: bool = True) -> dict[str, Any]:
        if not (self.instances / f"{execution_id}.json").exists():
            raise AdapterError(f"execution instance does not exist: {execution_id}")
        if not self.executable.exists():
            raise AdapterError(f"EasySpider executable not found: {self.executable}")
        if execution_id in self._processes and self._processes[execution_id].returncode is None:
            raise AdapterError(f"execution {execution_id} is already running")
        process = await asyncio.create_subprocess_exec(
            str(self.executable),
            # The pinned 0.3.5 Windows executable exposes the singular ``id``
            # option even though newer source releases use ``ids``.
            "--id",
            f"[{execution_id}]",
            "--headless",
            "1" if headless else "0",
            "--read_type",
            "local",
            cwd=self.home,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
        )
        self._processes[execution_id] = process
        self._logs[execution_id] = bytearray()
        self._statuses[execution_id] = "running"
        asyncio.create_task(self._capture(execution_id, process))
        return {"started": True, "execution_id": execution_id, "pid": process.pid}

    def _file_logs(self, execution_id: int) -> str:
        folder = self.data_root / f"Task_{execution_id}"
        if not folder.exists():
            return ""
        paths = sorted(
            (*folder.glob("*_log.txt"), *folder.glob("*.log")),
            key=lambda path: path.stat().st_mtime,
        )
        chunks: list[str] = []
        for path in paths:
            try:
                chunks.append(path.read_text(encoding="utf-8-sig", errors="replace"))
            except OSError:
                continue
        return "\n".join(chunks)[-1_000_000:]

    async def execution_logs(self, execution_id: int) -> dict[str, Any]:
        process = self._processes.get(execution_id)
        file_logs = self._file_logs(execution_id)
        captured = self._logs.get(execution_id, b"").decode("utf-8", "replace")
        logs = file_logs or captured
        status = self._statuses.get(execution_id, "unknown")
        if process and process.returncode is not None:
            status = "completed" if process.returncode == 0 else "failed"
        # 0.3.5 can keep its process alive after the worker has finished. The
        # result file plus its explicit Done marker are the reliable terminal
        # signal; clean up the private browser tree once both are present.
        result = self.results.read(execution_id, limit=1)
        if "Done!" in logs and result["status"] == "ready":
            if process and process.returncode is None:
                await self._terminate_process(process)
            status = "completed"
        self._statuses[execution_id] = status
        return {"status": status, "logs": logs}

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def execution_stop(self, execution_id: int) -> dict[str, Any]:
        process = self._processes.get(execution_id)
        if not process or process.returncode is not None:
            return {"stopped": False, "execution_id": execution_id}
        await self._terminate_process(process)
        self._statuses[execution_id] = "stopped"
        return {"stopped": True, "execution_id": execution_id}

    async def execution_result(self, execution_id: int, *, limit: int = 20) -> dict[str, Any]:
        return self.results.read(execution_id, limit)
