import pytest

from packages.crawl_core.models import ValidationMetrics, ValidationReport
from packages.crawl_core.states import CrawlPhase, CrawlState, CrawlStateMachine


def test_full_run_is_hard_gated_by_sample_pass():
    state = CrawlState(request_id="x", phase=CrawlPhase.SAMPLE_VALIDATING)
    machine = CrawlStateMachine(state)
    with pytest.raises(ValueError, match="PASS sample"):
        machine.transition(CrawlPhase.FULL_RUNNING)
    state.validation_report = ValidationReport(
        status="PASS",
        overall_score=1,
        metrics=ValidationMetrics(
            required_field_coverage=1,
            required_field_null_rate=0,
            duplicate_rate=0,
            semantic_score=1,
            record_count=3,
        ),
    )
    assert machine.transition(CrawlPhase.FULL_RUNNING).phase == CrawlPhase.FULL_RUNNING


def test_illegal_transition_and_repair_limit_are_rejected():
    state = CrawlState(request_id="x")
    with pytest.raises(ValueError, match="illegal"):
        CrawlStateMachine(state).transition(CrawlPhase.FULL_RUNNING)
    state.phase = CrawlPhase.SAMPLE_VALIDATING
    state.repair_attempt = 3
    with pytest.raises(ValueError, match="maximum repair"):
        CrawlStateMachine(state).transition(CrawlPhase.REPAIRING, max_repairs=3)

