from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .models import CrawlPlan, CrawlSpec, PageModel, ValidationReport


class CrawlPhase(StrEnum):
    RECEIVED = "RECEIVED"
    SPEC_NORMALIZING = "SPEC_NORMALIZING"
    SPEC_READY = "SPEC_READY"
    POLICY_CHECK = "POLICY_CHECK"
    MEMORY_LOOKUP = "MEMORY_LOOKUP"
    PAGE_PROBING = "PAGE_PROBING"
    PAGE_MODEL_READY = "PAGE_MODEL_READY"
    PLAN_BUILDING = "PLAN_BUILDING"
    PLAN_READY = "PLAN_READY"
    COMPILING = "COMPILING"
    TASK_READY = "TASK_READY"
    SAMPLE_RUNNING = "SAMPLE_RUNNING"
    SAMPLE_VALIDATING = "SAMPLE_VALIDATING"
    REPAIRING = "REPAIRING"
    RECOMPILING = "RECOMPILING"
    FULL_RUNNING = "FULL_RUNNING"
    FINAL_VALIDATING = "FINAL_VALIDATING"
    MEMORY_COMMIT = "MEMORY_COMMIT"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED = "FAILED"


class CrawlState(BaseModel):
    request_id: str
    phase: CrawlPhase = CrawlPhase.RECEIVED
    crawl_spec: CrawlSpec | None = None
    page_model: PageModel | None = None
    crawl_plan: CrawlPlan | None = None
    task_id: int | None = None
    execution_id: int | None = None
    repair_attempt: int = Field(default=0, ge=0)
    validation_report: ValidationReport | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


ALLOWED_TRANSITIONS: dict[CrawlPhase, set[CrawlPhase]] = {
    CrawlPhase.RECEIVED: {CrawlPhase.SPEC_NORMALIZING, CrawlPhase.BLOCKED, CrawlPhase.FAILED},
    CrawlPhase.SPEC_NORMALIZING: {CrawlPhase.SPEC_READY, CrawlPhase.FAILED},
    CrawlPhase.SPEC_READY: {CrawlPhase.POLICY_CHECK, CrawlPhase.FAILED},
    CrawlPhase.POLICY_CHECK: {CrawlPhase.MEMORY_LOOKUP, CrawlPhase.BLOCKED, CrawlPhase.FAILED},
    CrawlPhase.MEMORY_LOOKUP: {CrawlPhase.PAGE_PROBING, CrawlPhase.FAILED},
    CrawlPhase.PAGE_PROBING: {CrawlPhase.PAGE_MODEL_READY, CrawlPhase.BLOCKED, CrawlPhase.FAILED},
    CrawlPhase.PAGE_MODEL_READY: {CrawlPhase.PLAN_BUILDING, CrawlPhase.FAILED},
    CrawlPhase.PLAN_BUILDING: {CrawlPhase.PLAN_READY, CrawlPhase.FAILED},
    CrawlPhase.PLAN_READY: {CrawlPhase.COMPILING, CrawlPhase.FAILED},
    CrawlPhase.COMPILING: {CrawlPhase.TASK_READY, CrawlPhase.FAILED},
    CrawlPhase.TASK_READY: {CrawlPhase.SAMPLE_RUNNING, CrawlPhase.FAILED},
    CrawlPhase.SAMPLE_RUNNING: {CrawlPhase.SAMPLE_VALIDATING, CrawlPhase.FAILED},
    CrawlPhase.SAMPLE_VALIDATING: {CrawlPhase.REPAIRING, CrawlPhase.FULL_RUNNING, CrawlPhase.NEEDS_HUMAN, CrawlPhase.FAILED},
    CrawlPhase.REPAIRING: {CrawlPhase.RECOMPILING, CrawlPhase.NEEDS_HUMAN, CrawlPhase.FAILED},
    CrawlPhase.RECOMPILING: {CrawlPhase.TASK_READY, CrawlPhase.FAILED},
    CrawlPhase.FULL_RUNNING: {CrawlPhase.FINAL_VALIDATING, CrawlPhase.FAILED},
    CrawlPhase.FINAL_VALIDATING: {CrawlPhase.MEMORY_COMMIT, CrawlPhase.REPAIRING, CrawlPhase.NEEDS_HUMAN, CrawlPhase.FAILED},
    CrawlPhase.MEMORY_COMMIT: {CrawlPhase.COMPLETED, CrawlPhase.FAILED},
}


class CrawlStateMachine:
    """Enforce the sample-first state transitions independently of an LLM."""

    def __init__(self, state: CrawlState):
        self.state = state

    def transition(self, target: CrawlPhase, *, max_repairs: int = 3) -> CrawlState:
        allowed = ALLOWED_TRANSITIONS.get(self.state.phase, set())
        if target not in allowed:
            raise ValueError(f"illegal crawl transition: {self.state.phase} -> {target}")
        if target == CrawlPhase.FULL_RUNNING:
            report = self.state.validation_report
            if report is None or report.status != "PASS":
                raise ValueError("full execution requires a PASS sample ValidationReport")
        if target == CrawlPhase.REPAIRING:
            if self.state.repair_attempt >= max_repairs:
                raise ValueError("maximum repair attempts reached; transition to NEEDS_HUMAN")
            self.state.repair_attempt += 1
        if target == CrawlPhase.MEMORY_COMMIT:
            report = self.state.validation_report
            if report is None or report.status != "PASS":
                raise ValueError("memory commit requires PASS validation evidence")
        self.state.phase = target
        return self.state
