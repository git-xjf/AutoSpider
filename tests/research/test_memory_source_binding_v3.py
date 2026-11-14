"""Independent source-binding tests; not semantic truth or novelty evidence."""
from dataclasses import replace
import json

import pytest

from research.memory_v3.replay import Binding, Bundle, ReplayEngine, ReplayStore, Resource


ORIGIN = "https://replay.invalid"


def capsule(kind="normal", *, same_text=False, body="Target paragraph."):
    records = [
        {"id": "target", "body": body},
        {"id": "foreign", "body": body if same_text else "Foreign paragraph."},
    ]
    expression = {
        "normal": "target.body",
        "mixed": "target.body + ' / ' + foreign.body",
        "branch": "target.body === 'Target paragraph.' ? foreign.body : target.body",
        "truncated": "target.body.substring(0, 6)",
        "control_influence": "foreign.body === 'Foreign paragraph.' ? target.body : 'Control changed target region'",
    }.get(kind, "target.body")
    code = f"""
      (async () => {{
        const data = await (await fetch('/api')).json();
        const target = data.records.find(x => x.id === 'target');
        const foreign = data.records.find(x => x.id === 'foreign');
        document.querySelector('#body').textContent = {expression};
        document.querySelector('#foreign').textContent = foreign.body;
        document.querySelector('#copy').textContent = target.body;
        document.documentElement.setAttribute('data-ready', '1');
      }})();
    """
    markup = f'<html><body><div id="body"></div><div id="foreign"></div><div id="copy"></div><script>{code}</script></body></html>'
    candidates = ["#body", "#copy"] if kind == "duplicate_role" else ["#body", "#foreign"]
    resources = [Resource(ORIGIN + "/page", markup, "text/html"),
                 Resource(ORIGIN + "/api", json.dumps({"records": records}), "application/json")]
    bundle = Bundle(ORIGIN + "/page", resources, '[data-ready="1"]', candidates)
    return bundle, Binding(ORIGIN + "/api")


@pytest.fixture(scope="module")
def engine():
    active = ReplayEngine()
    try:
        active.__enter__()
    except Exception as error:
        if "Executable doesn't exist" in str(error) or "No module named 'playwright'" in str(error):
            pytest.skip("Optional Chromium research tests require installed Playwright browser")
        raise
    try:
        yield active
    finally:
        active.__exit__(None, None, None)


def test_real_browser_proves_target_response_without_schema(engine):
    bundle, binding = capsule()
    result = engine.evaluate(bundle, binding, budget_loads=4)
    assert result["state"] == "VALID"
    assert result["selector"] == "#body"
    assert result["cost"]["page_loads"] == 4
    assert result["cost"]["aborted_requests"] == 0
    assert len(result["traces"]) == 4


def test_equal_text_from_different_entity_needs_intervention(engine):
    bundle, binding = capsule(same_text=True)
    result = engine.evaluate(bundle, binding, budget_loads=4)
    assert result["state"] == "VALID"
    assert result["selector"] == "#body"
    assert result["passing_candidates"] == ["#body"]


@pytest.mark.parametrize("kind", ["mixed", "branch", "truncated", "control_influence", "duplicate_role"])
def test_unsupported_or_ambiguous_output_remains_pending(engine, kind):
    bundle, binding = capsule(kind)
    result = engine.evaluate(bundle, binding, budget_loads=4)
    assert result["state"] == "PENDING"
    assert result["selector"] is None


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_budget_includes_stability_runs_and_all_negative_controls(engine, budget):
    bundle, binding = capsule()
    result = engine.evaluate(bundle, binding, budget_loads=budget)
    assert result["state"] == "PENDING"
    assert result["cost"]["page_loads"] <= budget
    assert result["required_loads"] == 4


def test_corrupt_upstream_value_can_still_have_correct_field_binding(engine):
    # A task may identify the upstream field, but that does not make its value
    # semantically true. No real-article truth label is passed to the engine.
    bundle, binding = capsule(body="Upstream supplied incorrect report.")
    result = engine.evaluate(bundle, binding, budget_loads=4)
    assert result["state"] == "VALID"
    assert result["selector"] == "#body"


@pytest.mark.parametrize("body", ["", "  \n\t "])
def test_empty_target_cannot_certify_a_nonempty_body_contract(engine, body):
    bundle, binding = capsule(body=body)
    result = engine.evaluate(bundle, binding, budget_loads=4)
    assert result["state"] == "PENDING"
    assert result["selector"] is None


def test_receipt_survives_reopen_only_for_exact_bundle(engine, tmp_path):
    bundle, binding = capsule()
    path = tmp_path / "research-receipts.sqlite"
    with ReplayStore(path) as store:
        first = engine.evaluate(bundle, binding, budget_loads=4, store=store)
        assert first["state"] == "VALID"
        assert not first["cache_hit"]
    with ReplayStore(path) as reopened:
        hit = engine.evaluate(bundle, binding, budget_loads=0, store=reopened)
        assert hit["state"] == "VALID"
        assert hit["cache_hit"]
        assert hit["cost"]["page_loads"] == 0
        revised, _ = capsule(body="Revised source field.")
        miss = engine.evaluate(revised, binding, budget_loads=0, store=reopened)
        assert miss["state"] == "PENDING"
        assert not miss["cache_hit"]


@pytest.mark.parametrize("component", ["binding", "script", "candidates", "version"])
def test_receipt_does_not_cross_public_input_changes(engine, tmp_path, component):
    bundle, binding = capsule()
    with ReplayStore(tmp_path / f"{component}.sqlite") as store:
        assert engine.evaluate(bundle, binding, budget_loads=4, store=store)["state"] == "VALID"
        if component == "binding":
            binding = replace(binding, version="new")
        elif component == "script":
            resources = list(bundle.resources)
            resources[0] = replace(resources[0], body=resources[0].body.replace("target.body;", "foreign.body;"))
            bundle = replace(bundle, resources=resources)
        elif component == "candidates":
            bundle = replace(bundle, candidates=["#foreign"])
        else:
            bundle = replace(bundle, version="new")
        result = engine.evaluate(bundle, binding, budget_loads=0, store=store)
        assert result["state"] == "PENDING"
        assert not result["cache_hit"]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "nonstr"])
def test_target_identity_and_type_cannot_be_guessed(engine, fault):
    bundle, binding = capsule()
    resources = list(bundle.resources)
    payload = json.loads(resources[1].body)
    if fault == "missing":
        payload["records"][0]["id"] = "not-target"
    elif fault == "duplicate":
        payload["records"][1]["id"] = "target"
    else:
        payload["records"][0]["body"] = 123
    resources[1] = replace(resources[1], body=json.dumps(payload))
    result = engine.evaluate(replace(bundle, resources=resources), binding, budget_loads=4)
    assert result["state"] == "PENDING"
    assert result["selector"] is None


def test_missing_response_is_not_filled_from_the_network(engine):
    bundle, binding = capsule()
    resources = list(bundle.resources)
    resources[0] = replace(resources[0], body=resources[0].body.replace("fetch('/api')", "fetch('/not-recorded')"))
    result = engine.evaluate(replace(bundle, resources=resources), binding, budget_loads=4, timeout_ms=300)
    assert result["state"] == "PENDING"
    assert result["cost"]["aborted_requests"] > 0


def test_foreign_universe_is_all_records_not_only_one_control(engine):
    bundle, binding = capsule()
    resources = list(bundle.resources)
    payload = json.loads(resources[1].body)
    payload["records"].append({"id": "third", "body": "Third record."})
    resources[1] = replace(resources[1], body=json.dumps(payload))
    result = engine.evaluate(replace(bundle, resources=resources), binding, budget_loads=4)
    assert result["required_loads"] == 5
    assert result["state"] == "PENDING"
    assert result["cost"]["page_loads"] <= 4


def test_target_only_ablation_does_not_read_foreign_path_stability():
    from dataclasses import asdict
    from scripts.benchmark_memory_source_binding_v3 import _legacy_comparisons

    bundle, binding = capsule()
    public_round = {"bundle": asdict(bundle), "binding": asdict(binding)}
    # This represents an unrelated foreign control changing a DOM path. The
    # target-only ablation may not look at that suffix, even if it is available.
    result = {
        "traces": [{"label": label, "complete": True, "candidates": {}}
                   for label in ("baseline_1", "baseline_2", "target", "foreign_0")],
        "candidate_results": [{"selector": "#body", "base_match": True,
                               "target_response": True, "baseline_stable": True,
                               "stable": False, "non_target_unchanged": False}],
        "state": "PENDING", "selector": None, "passing_candidates": [], "reason": "control_failed",
    }
    decision = _legacy_comparisons(public_round, result)["target_only_ablation"]
    assert decision["state"] == "VALID"
    assert decision["selector"] == "#body"
