from __future__ import annotations

import json
from typing import Any

import httpx

from packages.crawl_core.errors import AdapterError

from .base import EasySpiderAdapter
from .results import ResultReader


class HTTPAdapter(EasySpiderAdapter):
    def __init__(self, base_url: str, data_root: str, timeout_seconds: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.results = ResultReader(data_root)

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response
        except httpx.HTTPError as exc:
            raise AdapterError(f"EasySpider HTTP {method} {path} failed: {exc}") from exc

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {"message": response.text.strip()}
        return value if isinstance(value, dict) else {"data": value}

    async def task_register(self, task: dict[str, Any]) -> int:
        # EasySpider 0.3.5 names this form field ``paras`` (not ``params``).
        # Sending the latter makes its Electron main process call
        # JSON.parse(undefined), which terminates the request handler.
        response = await self._request("POST", "/manageTask", data={"paras": json.dumps(task, ensure_ascii=False)})
        try:
            return int(response.text.strip())
        except ValueError as exc:
            raise AdapterError(f"invalid task ID returned by EasySpider: {response.text[:200]}") from exc

    async def task_get(self, task_id: int) -> dict[str, Any]:
        response = await self._request("GET", "/queryTask", params={"id": task_id})
        return response.json()

    async def execution_create(self, task_id: int, parameters: dict[str, Any]) -> int:
        response = await self._request(
            "POST",
            "/invokeTask",
            data={"id": task_id, "paras": json.dumps(parameters, ensure_ascii=False)},
        )
        try:
            return int(response.text.strip())
        except ValueError as exc:
            raise AdapterError(f"invalid execution ID returned by EasySpider: {response.text[:200]}") from exc

    async def execution_start(self, execution_id: int, *, headless: bool = True) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/executeTask",
            data={"id": json.dumps(execution_id), "use_user_data": "0", "timeout": int(self.timeout_seconds)},
        )
        payload = self._payload(response)
        return {"started": True, "execution_id": execution_id, "response": payload}

    async def execution_logs(self, execution_id: int) -> dict[str, Any]:
        response = await self._request("GET", "/getTaskLog", params={"id": execution_id})
        payload = self._payload(response)
        raw = payload.get("data", payload)
        log = raw.get("log", "") if isinstance(raw, dict) else str(raw)
        lowered = log.lower()
        status = "failed" if any(token in lowered for token in ("traceback", "fatal error")) else "running"
        if any(token in lowered for token in ("execution completed", "任务执行完成", "finished")):
            status = "completed"
        return {"status": status, "logs": log, "raw": payload}

    async def execution_stop(self, execution_id: int) -> dict[str, Any]:
        response = await self._request("POST", "/stopTask", data={"id": execution_id})
        return {"stopped": True, "execution_id": execution_id, "response": self._payload(response)}

    async def execution_result(self, execution_id: int, *, limit: int = 20) -> dict[str, Any]:
        return self.results.read(execution_id, limit)
