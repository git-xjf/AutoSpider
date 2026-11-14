from __future__ import annotations

from copy import deepcopy

from packages.crawl_core.models import CrawlPlan, Operation, RepairPatch


def apply_repair_patch(plan: CrawlPlan | dict, patch: RepairPatch | dict) -> CrawlPlan:
    current = plan if isinstance(plan, CrawlPlan) else CrawlPlan.model_validate(plan)
    change = patch if isinstance(patch, RepairPatch) else RepairPatch.model_validate(patch)
    updated = deepcopy(current)

    if change.operation == "REQUEST_REANALYSIS":
        raise ValueError("REQUEST_REANALYSIS requires a new PageModel; it cannot mutate CrawlPlan")

    matched = False

    def visit(steps) -> None:
        nonlocal matched
        for step in steps:
            target_field = change.target.removeprefix("fields.").removesuffix(".selector")
            if change.operation in {"REPLACE_SELECTOR", "ADD_FALLBACK_SELECTOR"} and step.op == Operation.EXTRACT and step.field == target_field:
                if change.operation == "REPLACE_SELECTOR":
                    if change.old_value is not None and step.effective_selector != change.old_value:
                        continue
                    if step.relative_selector is not None:
                        step.relative_selector = str(change.new_value)
                    else:
                        step.selector = str(change.new_value)
                elif str(change.new_value) not in step.fallbacks:
                    step.fallbacks = [*step.fallbacks, str(change.new_value)][:2]
                matched = True
            elif change.operation == "CHANGE_ITEM_CONTAINER" and step.op == Operation.LOOP_ELEMENTS:
                step.selector = str(change.new_value)
                matched = True
            elif change.operation == "CHANGE_DETAIL_NAVIGATION" and step.id == "open_detail":
                step.relative_selector = str(change.new_value)
                matched = True
            elif change.operation == "CHANGE_PAGINATION" and step.id == "next":
                step.selector = str(change.new_value)
                matched = True
            elif change.operation == "ADD_WAIT" and step.op == Operation.WAIT:
                step.seconds = float(change.new_value)
                matched = True
            visit(step.body)

    visit(updated.steps)
    if not matched:
        raise ValueError(f"repair target was not found: {change.target}")
    return CrawlPlan.model_validate(updated.model_dump(mode="json"))

