"""Keep common-cause failures and conservative false rejections visible."""
import pytest
from lxml import html

from research.selector_applicability.study import decisions, observed_dependencies
from research.selector_applicability.veto import (
    AdditionalCase, expanded_cases, full_evidence_conjunction_baseline,
    negative_veto_graph, negative_veto_status, run_veto_study,
)
from research.selector_applicability.fixtures import FixtureCase
from tests.research.test_selector_applicability import evidence


@pytest.mark.parametrize("case", expanded_cases(), ids=lambda case: case.name)
def test_full_strong_baseline_and_graph_are_equivalent(case):
    rows = evidence(case)
    assert full_evidence_conjunction_baseline(rows) == negative_veto_graph(rows)


def test_historical_counterexample_vetoes_stale_anchor_agreement():
    case = FixtureCase("anchor_reassigned", 10)
    rows = evidence(case)
    assert decisions(case.source_url, case.target_url, case.selector, rows)["target_dual_locator"]
    assert not full_evidence_conjunction_baseline(rows)
    assert negative_veto_status(rows).startswith("REANALYZE_HISTORICAL_COUNTEREXAMPLE")


def test_unseen_joint_failure_is_not_hidden_by_veto():
    case = AdditionalCase("unseen_anchor_cofailure", 10)
    rows = evidence(case)
    assert not observed_dependencies(rows)
    assert full_evidence_conjunction_baseline(rows)
    tree = html.fromstring(case.render(case.target_url, after=True))
    urls = {"https://binding.example.test" + node.get("href") for article in tree.xpath(case.selector) for node in article.xpath(".//a")}
    assert urls != case.expected_urls()


def test_reversible_effect_is_a_safe_transfer_conservatively_rejected():
    case = AdditionalCase("reversible_parameter_effect", 10)
    rows = evidence(case)
    assert decisions(case.source_url, case.target_url, case.selector, rows)["target_dual_locator"]
    assert not full_evidence_conjunction_baseline(rows)
    tree = html.fromstring(case.render(case.target_url, after=True))
    urls = {"https://binding.example.test" + node.get("href") for article in tree.xpath(case.selector) for node in article.xpath(".//a")}
    assert urls == case.expected_urls()


@pytest.mark.asyncio
async def test_expanded_protocol_executes_real_components(tmp_path):
    report = await run_veto_study(tmp_path / "new")
    assert report["scenario_count"] == 42 and report["mechanism_families"] == 14
    assert report["cost"] == {"mock_http_requests": 252, "external_http_requests": 0, "LLM_calls": 0}
    assert report["full_baseline_flat_graph_decisions_equal"]
    assert report["method_counts"]["negative_veto_flat"] == {
        "correct_accepts": 9, "wrong_accepts": 3, "safe_rejected": 6, "total_rejected": 30}
    assert report["source_hashes_unchanged"]


@pytest.mark.asyncio
async def test_negative_veto_cannot_overwrite_old_evidence(tmp_path):
    directory = tmp_path / "old"
    directory.mkdir()
    with pytest.raises(FileExistsError):
        await run_veto_study(directory)
