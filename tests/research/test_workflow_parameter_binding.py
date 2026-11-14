"""Guard equivalence and diagnosis scope; no live pages or model calls."""
from copy import deepcopy
import json

import pytest

from packages.crawl_core.models import Operation
from packages.crawl_core.reuse import build_reused_crawl
from research.parameter_binding.probe import (
    alias_checks, all_steps, exact_query_guard, graph_query_guard, run_probe, source_workflow,
)


def test_current_reuse_does_not_globally_replace_equal_numeric_values():
    results = alias_checks()
    assert len(results) == 8
    assert all(value for row in results for key, value in row.items() if isinstance(value, bool))


@pytest.mark.parametrize("query", ["channel=10&page_size=10", "channel=37&page_size=10", "tracking=only", "", "channel=10&channel=37"])
def test_graph_encoding_has_no_extra_information_over_query_table(query):
    spec, _, _ = source_workflow()
    source = spec.model_dump(mode="json")
    url = "https://binding.example.test/list?" + query
    assert exact_query_guard(source, url) == graph_query_guard(source, url)


def test_fixed_selector_value_is_retained_not_wrongly_substituted():
    spec, _, plan = source_workflow("//section[@data-channel='10']/article")
    original = deepcopy(plan.model_dump(mode="json"))
    _, result = build_reused_crawl(spec.model_dump(mode="json"), original,
        start_url="https://binding.example.test/list?channel=37&page_size=10", item_limit=37)
    loops = [step for step in all_steps(result.steps) if step.op == Operation.LOOP_ELEMENTS]
    assert loops[0].selector == "//section[@data-channel='10']/article"
    assert loops[0].max_iterations == 37
    assert original == plan.model_dump(mode="json")


@pytest.mark.asyncio
async def test_actual_memory_executor_validator_probe_retains_negative_results(tmp_path):
    result = await run_probe(tmp_path / "new-evidence")
    summary = result["summary"]
    assert summary["numeric_alias_checks_all_pass"]
    assert summary["current_wrong_transfers"] == summary["current_wrong_transfers_QA_PASS"] == 3
    assert summary["table_guard_wrong_accepted"] == 0
    assert summary["table_guard_safe_rejected"] == 2
    assert summary["table_guard_safe_accepted"] == 1
    assert summary["table_graph_all_decisions_equal"]
    assert summary["mock_http_requests"] == 12 and summary["external_http_requests"] == 0
    assert result["source_hash_verified_unchanged_during_run"]
    assert all(row["source_validation"] == "PASS" and row["stored_plan_round_trip_equal"] for row in result["transfers"])
    assert all(row["source_inputs_unchanged"] for row in result["transfers"])
    # No 'candidate superior' metric: the graph and table use the same predicate.
    assert "novelty_proven" not in json.dumps(result)


@pytest.mark.asyncio
async def test_probe_refuses_to_overwrite_evidence(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        await run_probe(destination)
