"""Controlled Chromium replay feasibility, not a production-news benchmark."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.memory_v3 import fixtures


def _default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=_default)
        handle.write("\n")


def freeze_manifest(path):
    manifest = fixtures.freeze_payload()
    manifest["fixture_source_sha256"] = sha256(Path(fixtures.__file__).read_bytes()).hexdigest()
    manifest["freeze_protocol"] = "Controlled fixture manifest written before evaluation; not production website capture or third-party blind testing."
    _write_new(path, manifest)
    return {"path": str(path), "payload_sha256": manifest["payload_sha256"], "fixture_source_sha256": manifest["fixture_source_sha256"], "case_count": len(manifest["payload"]["cases"])}


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if fixtures.payload_hash(manifest["payload"]) != manifest["payload_sha256"]:
        raise ValueError("Frozen payload hash mismatch")
    if sha256(Path(fixtures.__file__).read_bytes()).hexdigest() != manifest["fixture_source_sha256"]:
        raise ValueError("Fixture source changed after freeze")
    if fixtures.payload_hash(fixtures.build_suite()) != manifest["payload_sha256"]:
        raise ValueError("Generator differs from the frozen capsule manifest")
    return manifest


def _text(value):
    return " ".join(str(value).split())


def _source_value(round_data):
    binding = round_data["binding"]
    resource = next((item for item in round_data["bundle"]["resources"] if item["url"] == binding["resource_url"]), None)
    if resource is None or resource["status"] != 200:
        return None
    try:
        value = json.loads(resource["body"])
        for part in binding["records_pointer"].strip("/").split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        records = [record for record in value if record.get(binding["entity_field"]) == binding["entity_value"]]
        if len(records) != 1:
            return None
        result = records[0][binding["value_field"]]
        return result if isinstance(result, str) else None
    except (KeyError, TypeError, ValueError):
        return None


def _baseline_trace(result):
    return next((trace for trace in result.get("traces", []) if trace["label"] == "baseline_1"), None)


def _legacy_comparisons(round_data, result):
    """Ablations use only public source bytes and allowed transcript prefixes.

    Their load counts are not benchmarked independently: the complete protocol
    owns the shared browser cost. Expected-selector/semantic labels are not read.
    """
    baseline = _baseline_trace(result)
    source = _source_value(round_data)
    equality = []
    if baseline and baseline.get("complete") and source is not None:
        for selector in round_data["bundle"]["candidates"]:
            candidate = baseline.get("candidates", {}).get(selector, {})
            if candidate.get("count") == 1 and _text(candidate.get("text", "")) == _text(source):
                equality.append(selector)
    target_only = []
    prefix_labels = {trace["label"] for trace in result.get("traces", []) if trace.get("complete")}
    if {"baseline_1", "baseline_2", "target"} <= prefix_labels:
        target_only = [row["selector"] for row in result.get("candidate_results", []) if row.get("base_match") and row.get("target_response") and row.get("baseline_stable")]
    return {
        "offline_text_equality": {"state": "VALID" if equality else "PENDING", "selector": equality[0] if equality else None, "passing_candidates": equality, "reason": "first_baseline_text_equals_declared_source_value"},
        "target_only_ablation": {"state": "VALID" if target_only else "PENDING", "selector": target_only[0] if target_only else None, "passing_candidates": target_only, "reason": "stable_baseline_and_target_response_without_foreign_control"},
        "full_paired_exact_memoization": {key: result.get(key) for key in ("state", "selector", "passing_candidates", "reason")},
    }


def _score(round_data, result, decision):
    # Private engineering labels first enter here, after the engine and public
    # transcript ablations have already returned their decisions.
    scoring = round_data["scoring"]
    expected = scoring["expected_binding_selectors"]
    selector = decision.get("selector")
    accepted = decision.get("state") == "VALID" and selector is not None
    binding_correct = accepted and selector in expected
    baseline = _baseline_trace(result)
    observed = baseline.get("candidates", {}).get(selector, {}) if baseline and selector else {}
    semantic_correct = accepted and observed.get("count") == 1 and _text(observed.get("text", "")) == _text(scoring["semantic_reference"])
    return {
        "accepted": int(accepted),
        "correct_source_binding": int(binding_correct),
        "wrong_source_binding_acceptance": int(accepted and not binding_correct),
        "abstained": int(not accepted),
        "missed_observed_target_dependency": int(not accepted and bool(expected)),
        "correct_no_binding_abstention": int(not accepted and not expected),
        "semantic_reference_match_on_acceptance": int(semantic_correct),
        "semantic_error_despite_source_binding": int(binding_correct and not semantic_correct),
        "scoring_scope": scoring["interpretation"],
    }


def evaluate(manifest, budgets, timeout_ms, store_mode):
    core_path = ROOT / "research/memory_v3/replay.py"
    guards = {"core_source_sha256": sha256(core_path.read_bytes()).hexdigest(), "runner_source_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    from research.memory_v3.replay import Resource, Bundle, Binding, ReplayEngine, ReplayStore

    def inputs(data):
        bundle = {**data["bundle"], "resources": [Resource(**item) for item in data["bundle"]["resources"]]}
        return Bundle(**bundle), Binding(**data["binding"])

    rows = []
    aggregate = defaultdict(Counter)
    total_cost = defaultdict(float)
    with TemporaryDirectory(prefix="autospider-memory-v3-") as directory:
        with ReplayEngine(headless=True, timeout_ms=timeout_ms) as engine:
            for case_index, case in enumerate(manifest["payload"]["cases"]):
                for budget in budgets:
                    store_path = Path(directory) / f"case-{case_index}-budget-{budget}.sqlite"
                    store = ReplayStore(":memory:" if store_mode == "memory" else store_path)
                    try:
                        for data in case["rounds"]:
                            if store_mode == "disk":
                                # Reopen between tasks to test actual receipt
                                # persistence rather than a Python object cache.
                                store.close()
                                store = ReplayStore(store_path)
                            bundle, binding = inputs(data)
                            result = engine.evaluate(bundle, binding, budget_loads=budget, store=store)
                            if result.get("cost", {}).get("page_loads", 0) > budget:
                                raise AssertionError("Browser-load budget exceeded")
                            for key, value in result.get("cost", {}).items():
                                if isinstance(value, (int, float)) and not isinstance(value, bool):
                                    total_cost[key] += value
                            decisions = _legacy_comparisons(data, result)
                            metrics = {name: _score(data, result, decision) for name, decision in decisions.items()}
                            for policy, values in metrics.items():
                                count = aggregate[(budget, policy)]
                                for key, value in values.items():
                                    if isinstance(value, int):
                                        count[key] += value
                                count["task_evaluations"] += 1
                            rows.append({"case": case["name"], "round_id": data["round_id"], "budget_loads": budget, "decisions": decisions, "metrics": metrics, "replay": result})
                    finally:
                        store.close()

            # This independent store is reopened to test disk persistence even
            # when the main matrix uses in-memory SQLite. It reuses an existing
            # frozen capsule, and adds no new fixture chosen from results.
            normal = manifest["payload"]["cases"][0]["rounds"][0]
            bundle, binding = inputs(normal)
            persistent_path = Path(directory) / "explicit-persistent-receipt.sqlite"
            persistent = ReplayStore(persistent_path)
            initial = engine.evaluate(bundle, binding, budget_loads=max(budgets), store=persistent)
            persistent.close()
            persistent = ReplayStore(persistent_path)
            repeated = engine.evaluate(bundle, binding, budget_loads=0, store=persistent)
            persistent.close()
            for result in (initial, repeated):
                for key, value in result.get("cost", {}).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        total_cost[key] += value
            persistence = {"initial": initial, "after_store_reopen_budget_zero": repeated}
            if initial["state"] == "VALID":
                assert repeated.get("cache_hit") and repeated["state"] == "VALID" and repeated["cost"]["page_loads"] == 0
    if sha256(core_path.read_bytes()).hexdigest() != guards["core_source_sha256"] or sha256(Path(__file__).read_bytes()).hexdigest() != guards["runner_source_sha256"]:
        raise RuntimeError("Source changed during evaluation; refusing to save mixed-version evidence")
    return {
        "experiment": "memory_v3_controlled_field_source_replay",
        "fixture_manifest_sha256": manifest["payload_sha256"],
        "fixture_source_sha256": manifest["fixture_source_sha256"],
        **guards, "source_hash_verified_unchanged_during_run": True,
        "budgets_loads": budgets, "timeout_ms": timeout_ms, "store_mode": store_mode,
        "cost_model": {
            "unit": "one offline fresh-browser-context page load, shared across all candidates",
            "shared_transcript_ablations": "Text-equality reads only baseline_1; target-only reads baseline_1/baseline_2/target. All observed browser and request costs are charged to the combined replay protocol, not represented as independent baseline runtimes.",
            "memory_cache": "Exact complete capsule/binding/protocol identity; no cross-snapshot semantic generalization.",
            "network": "Only frozen offline Resource objects are fulfilled; undeclared requests are aborted and reported.",
        },
        "rows": rows,
        "aggregate_by_budget_policy": [{"budget_loads": budget, "policy": policy, "metrics": dict(values)} for (budget, policy), values in aggregate.items()],
        "total_observed_cost_including_persistence_check": dict(total_cost),
        "persistent_store_reopen_check": persistence,
        "limitations": [
            "Authored controlled capsules executed in real Chromium; not observed production-news website performance or natural website drift.",
            "Interventions test a declared JSON entity/field-to-selected-DOM relation, not article semantic truth.",
            "Canary-sensitive branches and text transformations can prevent certification despite actual target dependence.",
            "Exact receipt reuse and differential browser replay have direct prior art; this pilot establishes neither novelty nor inventive step.",
            "Legacy ablations are recomputed from the same protocol transcript; timing superiority between separately implemented algorithms is not measured.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze-manifest", type=Path)
    mode.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--budgets", nargs="+", type=int, default=[0, 3, 6])
    parser.add_argument("--timeout-ms", type=int, default=1500)
    parser.add_argument("--store-mode", choices=("memory", "disk"), default="memory")
    args = parser.parse_args()
    if args.freeze_manifest:
        print(json.dumps(freeze_manifest(args.freeze_manifest), ensure_ascii=False, indent=2))
        return
    if args.output is None or any(budget < 0 for budget in args.budgets):
        parser.error("Specify a new --output file and nonnegative budgets")
    result = evaluate(load_manifest(args.manifest), args.budgets, args.timeout_ms, args.store_mode)
    _write_new(args.output, result)
    print(json.dumps({"experiment": result["experiment"], "output": str(args.output), "manifest_sha256": result["fixture_manifest_sha256"], "task_rows": len(result["rows"]), "cost": result["total_observed_cost_including_persistence_check"], "aggregate": result["aggregate_by_budget_policy"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
