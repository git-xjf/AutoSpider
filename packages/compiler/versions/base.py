from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from packages.crawl_core.models import PlanStep


class EasySpiderVersionProfile(ABC):
    version: str

    @abstractmethod
    def lower(self, step: PlanStep, *, in_loop: bool, start_url: str) -> tuple[int, str, dict[str, Any]]:
        raise NotImplementedError

