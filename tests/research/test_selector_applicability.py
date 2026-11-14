"""Mechanism checks and necessary failures, not a patent novelty test."""
from copy import deepcopy
import json

import pytest
from lxml import html

from research.selector_applicability.fixtures import FAMILIES, FixtureCase, cases
from research.selector_applicability.study import (
    applicability_status, decisions, derive_anchor, elements, graph_observational_guard, observe,
    observed_dependencies, observational_guard, run_study, valid_controls, xpath_literal,
)


def evidence(case):
    samples = list(case.observations())
    anchor = derive_anchor(samples[0][2], case.selector)
    return [observe(body, case.selector, anchor, name=name, url=url, intervention=key)
            for name, url, body, key in samples]


@pytest.mark.parametrize("value", ["plain", "single'quote", 'double"quote', 'both\'"quotes'])
def test_xpath_attribute_literal_is_not_injected(value):
    tree = html.fromstring("<html><body><section/></body></html>")
    tree.xpath("//section")[0].set("id", value)
    assert len(tree.xpath("//*[@id=" + xpath_literal(value) + "]")) == 1


@pytest.mark.parametrize("body,selector", [
    (b'<div><article/></div>', '//article'),
    (b'<div id="same"><article/></div><div id="same"><article/></div>', '//div[1]/article'),
    (b'<div id="one"><article class="picked"/><article/></div>', '//article[@class="picked"]'),
    (b'<div id="one"><article/><p/></div>', '//div/*'),
])
def test_no_anchor_is_invented_from_missing_duplicate_or_filtered_parent(body, selector):
    assert derive_anchor(body, selector) is None


def test_invalid_and_scalar_xpaths_do_not_become_node_evidence():
    tree = html.fromstring("<p>fixture</p>")
    assert elements(tree, "[") == []
    assert elements(tree, "string(//p)") == []


@pytest.mark.parametrize("family", FAMILIES)
def test_flat_graph_agree_on_same_observed_facts(family):
    rows = evidence(FixtureCase(family, 42))
    assert observational_guard(rows) == graph_observational_guard(rows)


def test_equal_parameter_values_do_not_make_layout_a_dependency():
    case = FixtureCase("unrelated_layout_constant", 10)
    rows = evidence(case)
    result = decisions(case.source_url, case.target_url, case.selector, rows)
    assert not result["numeric_overlap"]
    assert observed_dependencies(rows) == set()
    assert result["observational_guard_flat"]


def test_encoded_value_misses_numeric_overlap_but_not_node_disagreement():
    case = FixtureCase("encoded_constant", 10)
    result = decisions(case.source_url, case.target_url, case.selector, evidence(case))
    assert result["numeric_overlap"]
    assert not result["observational_guard_flat"]


def test_single_parameter_observations_miss_interaction():
    case = FixtureCase("joint_parameter_interaction", 10)
    rows = evidence(case)
    result = decisions(case.source_url, case.target_url, case.selector, rows)
    assert not observed_dependencies(rows)
    assert result["observed_dependency_cache"]
    assert not result["target_dual_locator"]
    assert not result["observational_guard_flat"]


def test_anchor_reassignment_is_preserved_as_a_false_accept_counterexample():
    case = FixtureCase("anchor_reassigned", 10)
    rows = evidence(case)
    result = decisions(case.source_url, case.target_url, case.selector, rows)
    assert "channel" in observed_dependencies(rows)
    assert result["observational_guard_flat"]
    assert result["target_dual_locator"]
    # The stale selector and anchor agree, but agree on the wrong region.
    tree = html.fromstring(case.render(case.target_url, after=True))
    actual = {"https://binding.example.test" + node.get("href")
              for article in tree.xpath(case.selector) for node in article.xpath(".//a")}
    assert actual != case.expected_urls()


def test_controls_must_cover_single_parameters_not_confounded_changes():
    case = FixtureCase("generic_channel_change", 10)
    rows = evidence(case)
    assert valid_controls(rows)
    missing = [row for row in rows if row["name"] != "probe_tracking"]
    assert not valid_controls(missing)
    assert not observational_guard(missing)
    altered = deepcopy(rows)
    next(row for row in altered if row["name"] == "probe_channel")["url"] += "&tracking=other"
    assert not valid_controls(altered)


def test_text_equality_is_not_node_identity():
    rows = evidence(FixtureCase("equal_text_regions", 10))
    target = next(row for row in rows if row["name"] == "target")
    assert target["selector_nodes"] != target["anchor_nodes"]
    assert not target["nodes_agree"]


def test_unobserved_parameter_cannot_be_treated_as_independent():
    rows = evidence(FixtureCase("generic_channel_change", 10))
    next(row for row in rows if row["name"] == "target")["url"] += "&unobserved=new"
    assert applicability_status(rows) == "UNCERTAIN_MISSING_SINGLE_PARAMETER_CONTROLS"
    assert not observational_guard(rows) and not graph_observational_guard(rows)


def test_uncertainty_is_not_positive_evidence_of_failure():
    missing = evidence(FixtureCase("missing_unique_anchor", 10))
    assert applicability_status(missing).startswith("UNCERTAIN_")
    bad = evidence(FixtureCase("literal_attribute", 10))
    assert applicability_status(bad) == "REANALYZE_TARGET_NODE_DISAGREEMENT"


def test_query_key_renaming_does_not_supply_free_semantics():
    rows = evidence(FixtureCase("generic_channel_change", 10))
    renamed = deepcopy(rows)
    mapping = {"channel": "q", "page_size": "p", "tracking": "z"}
    for row in renamed:
        for old, new in mapping.items():
            row["url"] = row["url"].replace(old + "=", new + "=")
        if row["intervention"] is not None:
            row["intervention"] = mapping[row["intervention"]]
    assert observational_guard(rows) == observational_guard(renamed)
    assert graph_observational_guard(rows) == graph_observational_guard(renamed)


@pytest.mark.asyncio
async def test_fixed_protocol_runs_real_components_and_retains_failures(tmp_path):
    result = await run_study(tmp_path / "new-run")
    assert result["scenario_count"] == len(cases()) == 36
    assert result["mechanism_families"] == 12
    assert result["cost"] == {"mock_http_requests": 216, "external_http_requests": 0, "LLM_calls": 0}
    assert result["source_hashes_unchanged"] and result["all_source_inputs_unchanged"]
    assert result["graph_table_decisions_equal"]
    assert result["strong_baseline_decisions_equal"]
    assert result["method_counts"]["observational_guard_flat"]["wrong_accepts"] == 3
    assert all(row["observations"][0]["qa_status"] == "PASS" for row in result["cases"])
    assert all(row["observations"][-1]["qa_status"] == "PASS" for row in result["cases"])
    assert "novelty_proven" not in json.dumps(result)


@pytest.mark.asyncio
async def test_old_evidence_is_not_overwritten(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        await run_study(destination)
