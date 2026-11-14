from __future__ import annotations

from typing import Any

from packages.crawl_core.models import Operation, PlanStep

from .versions.base import EasySpiderVersionProfile


class GraphBuilder:
    def __init__(self, profile: EasySpiderVersionProfile, start_url: str):
        self.profile = profile
        self.start_url = start_url
        self.graph: list[dict[str, Any]] = [
            {
                "index": 0,
                "id": 0,
                "parentId": 0,
                "type": -1,
                "option": 0,
                "title": "root",
                "sequence": [],
                "parameters": {"history": 1, "tabIndex": 0, "useLoop": False, "xpath": "", "wait": 0},
                "isInLoop": False,
            }
        ]

    def build(self, steps: list[PlanStep]) -> list[dict[str, Any]]:
        for position, step in enumerate(steps):
            self._append(step, parent_id=0, position=position, in_loop=False)
        return self.graph

    def _append(self, step: PlanStep, *, parent_id: int, position: int, in_loop: bool) -> int:
        node_id = len(self.graph)
        option, title, parameters = self.profile.lower(step, in_loop=in_loop, start_url=self.start_url)
        is_loop = step.op in {Operation.LOOP_ELEMENTS, Operation.LOOP_PAGES}
        node = {
            "id": node_id,
            "index": node_id,
            "parentId": parent_id,
            "type": 1 if is_loop else 0,
            "option": option,
            "title": title,
            "sequence": [],
            "isInLoop": in_loop,
            "position": position,
            "parameters": parameters,
        }
        self.graph.append(node)
        self.graph[parent_id]["sequence"].append(node_id)
        for child_position, child in enumerate(step.body):
            self._append(child, parent_id=node_id, position=child_position, in_loop=in_loop or is_loop)
        return node_id

