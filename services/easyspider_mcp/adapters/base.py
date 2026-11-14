from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EasySpiderAdapter(ABC):
    @abstractmethod
    async def task_register(self, task: dict[str, Any]) -> int: ...

    @abstractmethod
    async def task_get(self, task_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def execution_create(self, task_id: int, parameters: dict[str, Any]) -> int: ...

    @abstractmethod
    async def execution_start(self, execution_id: int, *, headless: bool = True) -> dict[str, Any]: ...

    @abstractmethod
    async def execution_logs(self, execution_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def execution_stop(self, execution_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def execution_result(self, execution_id: int, *, limit: int = 20) -> dict[str, Any]: ...

