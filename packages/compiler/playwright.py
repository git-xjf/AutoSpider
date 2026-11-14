from __future__ import annotations

from copy import deepcopy
from typing import Any

from packages.crawl_core.errors import CompileError
from packages.crawl_core.models import CrawlPlan, CrawlSpec, Operation
from packages.crawl_core.task_manifest import compute_task_hash


class PlaywrightCompiler:
    """Compile the public CrawlPlan IR into an AutoSpider execution manifest.

    The manifest deliberately contains no EasySpider graph/node representation.
    It is stable JSON that can be persisted, inspected and executed without an LLM.
    """

    version = "1.0"

    def compile(
        self,
        crawl_spec: CrawlSpec | dict[str, Any],
        crawl_plan: CrawlPlan | dict[str, Any],
        sample_size: int | None = None,
    ) -> dict[str, Any]:
        spec = crawl_spec if isinstance(crawl_spec, CrawlSpec) else CrawlSpec.model_validate(crawl_spec)
        plan = crawl_plan if isinstance(crawl_plan, CrawlPlan) else CrawlPlan.model_validate(crawl_plan)
        if plan.start_urls != spec.start_urls:
            raise CompileError("CrawlPlan.start_urls must equal CrawlSpec.start_urls")
        if sample_size is not None and not 1 <= sample_size <= 50:
            raise CompileError("sample_size must be between 1 and 50")

        full_item_limit = spec.traversal.item_limit
        spec = deepcopy(spec)
        if sample_size is not None:
            spec.traversal.item_limit = min(spec.traversal.item_limit or sample_size, sample_size)
        effective_plan = deepcopy(plan)

        def constrain(steps) -> None:
            for step in steps:
                if step.op == Operation.LOOP_PAGES and sample_size is not None:
                    step.max_iterations = 1
                if step.op == Operation.LOOP_ELEMENTS:
                    if sample_size is not None and spec.execution.sample_strategy == "boundary_spread":
                        # Preserve the size of the first-page candidate set so
                        # the executor can choose representatives across it.
                        # The copied CrawlSpec.item_limit still caps emitted
                        # sample records to sample_size.
                        configured = full_item_limit
                    else:
                        configured = spec.traversal.item_limit
                    if sample_size is not None and spec.execution.sample_strategy == "prefix":
                        configured = min(configured or sample_size, sample_size)
                    if step.max_iterations is not None:
                        configured = min(configured or step.max_iterations, step.max_iterations)
                    step.max_iterations = configured
                constrain(step.body)

        constrain(effective_plan.steps)
        spec_data = spec.model_dump(mode="json", exclude_none=True)
        plan_data = effective_plan.model_dump(mode="json", exclude_none=True)
        return {
            "schema": "autospider.task/v1",
            "version": self.version,
            "engine": plan.execution_mode,
            "name": plan.name,
            "description": spec.objective,
            "sample": sample_size is not None,
            "sample_size": sample_size,
            "crawl_spec": spec_data,
            "crawl_plan": plan_data,
            "task_hash": compute_task_hash(spec_data, plan_data),
        }
