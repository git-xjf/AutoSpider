from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from packages.crawl_core.errors import AdapterError
from packages.crawl_core.task_manifest import verify_task_manifest


def _windows_process_is_alive(pid: int) -> bool:
    """Query process state without os.kill, which can terminate on Windows.

    Access-denied and unexpected query errors conservatively count as alive;
    only a missing PID or a confirmed exited process permits recovery.
    """
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, TypeError, ValueError):
        return True


def _process_is_alive(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= 0xFFFFFFFF:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    except OSError:
        return True
    return True


class TaskRepository:
    """Small durable task/execution store used by the standalone MCP process."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.tasks = self.root / "tasks"
        self.executions = self.root / "executions"
        self.runs = self.root / "runs"
        for folder in (self.tasks, self.executions, self.runs):
            folder.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._recover_interrupted_executions()

    def _recover_interrupted_executions(self) -> None:
        """Recover orphaned jobs without interrupting another live MCP process.

        Legacy records without an owner retain restart recovery. PID reuse can
        conservatively leave an orphan marked running until that PID exits.
        """
        for path in self.executions.glob("*.json"):
            try:
                payload = self._read(path)
            except AdapterError:
                continue
            if payload.get("status") in {"starting", "running"}:
                if _process_is_alive(payload.get("owner_pid")):
                    continue
                payload["status"] = "interrupted"
                payload["error"] = "AutoSpider service restarted before execution completed"
                self._write(path, payload)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"cannot read AutoSpider state: {path}") from exc

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _allocate(self, folder: Path) -> int:
        with self._lock:
            values = [int(path.stem) for path in folder.glob("*.json") if path.stem.isdigit()]
            return max(values, default=0) + 1

    def register_task(self, task: dict[str, Any]) -> int:
        try:
            verify_task_manifest(task)
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        with self._lock:
            task_id = self._allocate(self.tasks)
            payload = deepcopy_json(task)
            payload["id"] = task_id
            self._write(self.tasks / f"{task_id}.json", payload)
        return task_id

    def get_task(self, task_id: int) -> dict[str, Any]:
        path = self.tasks / f"{task_id}.json"
        if task_id < 1 or not path.exists():
            raise AdapterError(f"AutoSpider task does not exist: {task_id}")
        return self._read(path)

    def create_execution(self, task_id: int, parameters: dict[str, Any]) -> int:
        with self._lock:
            task = self.get_task(task_id)
            execution_id = self._allocate(self.executions)
            payload = {
                "id": execution_id,
                "task_id": task_id,
                "status": "created",
                "parameters": deepcopy_json(parameters),
                "task": task,
            }
            self._write(self.executions / f"{execution_id}.json", payload)
            (self.runs / str(execution_id)).mkdir(parents=True, exist_ok=True)
        return execution_id

    def get_execution(self, execution_id: int) -> dict[str, Any]:
        path = self.executions / f"{execution_id}.json"
        if execution_id < 1 or not path.exists():
            raise AdapterError(f"AutoSpider execution does not exist: {execution_id}")
        return self._read(path)

    def update_execution(self, execution_id: int, **changes: Any) -> dict[str, Any]:
        with self._lock:
            path = self.executions / f"{execution_id}.json"
            payload = self.get_execution(execution_id)
            payload.update(deepcopy_json(changes))
            if changes.get("status") == "starting":
                payload["owner_pid"] = os.getpid()
            self._write(path, payload)
            return payload

    def run_dir(self, execution_id: int) -> Path:
        self.get_execution(execution_id)
        folder = (self.runs / str(execution_id)).resolve()
        if folder.parent != self.runs:
            raise AdapterError("invalid execution output path")
        folder.mkdir(parents=True, exist_ok=True)
        return folder


def deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
