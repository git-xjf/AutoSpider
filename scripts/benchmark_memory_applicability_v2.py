"""Run isolated v2 memory pilots against a previously frozen fixture manifest.

Freeze and evaluation are separate commands. Extra audit budgets count fixed
local contract checks on already acquired snapshots, not HTTP/model savings.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from urllib.parse import urljoin, urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research.memory_v2 import fixtures


BUDGETS = (0, 1, 2, 4, 10)


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Experimental evidence is append-only by filename; do not overwrite a
    # frozen manifest or an earlier run when repeating the command.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=_json_default)
        handle.write("\n")


def freeze_manifest(path, seed):
    manifest = fixtures.freeze_payload(seed)
    manifest["fixture_source_sha256"] = sha256(Path(fixtures.__file__).read_bytes()).hexdigest()
    manifest["freeze_protocol"] = "Written before policy evaluation; engineering holdout, not third-party blind testing."
    _write_new(path, manifest)
    return {"manifest": str(path), "payload_sha256": manifest["payload_sha256"], "fixture_source_sha256": manifest["fixture_source_sha256"], "scenarios": len(manifest["payload"]["scenarios"])}


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if fixtures.payload_hash(manifest["payload"]) != manifest["payload_sha256"]:
        raise ValueError("Frozen fixture payload hash mismatch")
    source_hash = sha256(Path(fixtures.__file__).read_bytes()).hexdigest()
    if source_hash != manifest["fixture_source_sha256"]:
        raise ValueError("Fixture generator changed after the manifest was frozen")
    if fixtures.payload_hash(fixtures.build_suite(manifest["payload"]["seed"])) != manifest["payload_sha256"]:
        raise ValueError("Current deterministic generator differs from frozen fixtures")
    return manifest


def _sum_costs(observations):
    total = defaultdict(float)
    rows = []
    for observation in observations:
        cost = observation.cost
        cost = asdict(cost) if is_dataclass(cost) else dict(cost)
        rows.append(cost)
        for key, value in cost.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total[key] += value
    return {"sum": dict(total), "per_page": rows}


def _prepare(round_data, api):
    # Only this stripped public view is passed to the policy. Engineering case
    # labels and scoring_expected_text remain in the runner, not PageObservation.
    pages = [api["Snapshot"](**page) for page in round_data["pages"]]
    rules = [api["Rule"](**rule) for rule in round_data["rules"]]
    contract = api["FieldContract"](**round_data["contract"])
    started = perf_counter()
    observations = [api["observe_page"](page, rules, contract) for page in pages]
    elapsed = perf_counter() - started
    common_cost = _sum_costs(observations)
    common_cost.update({
        "common_stage_seconds": elapsed,
        "snapshot_count": len(pages),
        "input_utf8_bytes": sum(len(page.markup.encode("utf-8")) for page in pages),
        "network_requests": 0,
        "llm_calls": 0,
        "charged_to_every_policy": True,
    })
    return pages, rules, contract, observations, common_cost


def _normal_text(node):
    return " ".join(" ".join(node.itertext()).split())


def _normalized_url(value):
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _belongs_to_owner(node, owner):
    for item in [node, *node.iterancestors()]:
        if item is owner:
            return True
        if "itemscope" in item.attrib:
            return False
    return False


def _direct_properties(owner, name):
    return [item for item in owner.cssselect(f'[itemprop~="{name}"]') if item is not owner and _belongs_to_owner(item, owner)]


def _score_contract(snapshot, tree, selector, contract):
    """Independent scorer-side public contract, never available to the policy.

    Its work is excluded from policy-time measurements and separately counted.
    Expected text is checked only by _score after every policy has finished.
    """
    if snapshot.status >= 500 or not snapshot.complete:
        return "PENDING", "", 0
    selected = tree.cssselect(selector)
    if len(selected) != 1:
        return "PENDING" if len(selected) > 1 else "INVALID", "", len(selected)
    node = selected[0]
    value = _normal_text(node)
    if not value:
        return "INVALID", value, 1
    article_types = {"https://schema.org/Article", "http://schema.org/Article", "https://schema.org/NewsArticle", "http://schema.org/NewsArticle"}
    articles = [ancestor for ancestor in [node, *node.iterancestors()] if article_types.intersection(ancestor.get("itemtype", "").split())]
    if len(articles) != 1:
        return "PENDING", value, 1
    article = articles[0]
    if not _belongs_to_owner(node, article):
        return "PENDING", value, 1
    if contract.require_body_role and "articleBody" not in node.get("itemprop", "").split():
        return "PENDING", value, 1
    headlines = _direct_properties(article, "headline")
    if len(headlines) == 0:
        return "PENDING", value, 1
    if len(headlines) != 1:
        return "PENDING", value, 1
    if not _normal_text(headlines[0]) or not " ".join(snapshot.list_title.split()):
        return "PENDING", value, 1
    if _normal_text(headlines[0]) != " ".join(snapshot.list_title.split()):
        return "INVALID", value, 1
    if contract.require_source_url:
        sources = _direct_properties(article, "url")
        if not sources:
            return "PENDING", value, 1
        if len(sources) != 1:
            return "PENDING", value, 1
        source = sources[0].get("href") or sources[0].get("content")
        if not source:
            return "PENDING", value, 1
        if _normalized_url(urljoin(snapshot.url, source)) != _normalized_url(snapshot.url):
            return "INVALID", value, 1
    return "VALID", value, 1


def _score(round_data, prepared, result):
    from lxml import html as lxml_html

    pages, rules, contract, observations, _ = prepared
    expected = round_data["scoring_expected_text"]  # Only scoring accesses this.
    decisions = result["decisions"]
    if len(decisions) != len(pages):
        raise AssertionError("Policy decisions must retain every requested page")
    metrics = Counter({
        "correct_usable_rules_retained": 0,
        "invalid_rule_reuses": 0,
        "contract_valid_decisions": 0,
        "contract_noncompliant_reuses": 0,
        "body_text_correct_decisions": 0,
        "semantic_false_acceptances_despite_contract": 0,
        "pending_as_valid_reuses": 0,
        "wrong_nonempty_extractions": 0,
        "empty_or_ambiguous_extractions": 0,
        "abstained": 0,
        "temporarily_unavailable": 0,
        "scorer_html_parses": 0,
        "scorer_contract_checks": 0,
    })
    states = Counter()
    for index, (page, decision) in enumerate(zip(pages, decisions)):
        if decision["url"] != page.url:
            raise AssertionError("Policy decision URL order changed")
        states[decision["state"]] += 1
        if page.status >= 500 or not page.complete:
            metrics["temporarily_unavailable"] += 1
            continue
        selector = decision.get("selector")
        if decision["state"] != "VALID" or selector is None:
            metrics["abstained"] += 1
            continue
        tree = lxml_html.fromstring(page.markup)
        metrics["scorer_html_parses"] += 1
        state, value, count = _score_contract(page, tree, selector, contract)
        metrics["scorer_contract_checks"] += 1
        metrics["contract_valid_decisions"] += int(state == "VALID")
        metrics["body_text_correct_decisions"] += int(count == 1 and value == expected[index])
        metrics["contract_noncompliant_reuses"] += int(state != "VALID")
        metrics["semantic_false_acceptances_despite_contract"] += int(state == "VALID" and value != expected[index])
        if state == "VALID" and value == expected[index]:
            metrics["correct_usable_rules_retained"] += 1
        else:
            metrics["invalid_rule_reuses"] += 1
            if state == "PENDING":
                metrics["pending_as_valid_reuses"] += 1
            if count != 1 or not value:
                metrics["empty_or_ambiguous_extractions"] += 1
            elif value != expected[index]:
                metrics["wrong_nonempty_extractions"] += 1
    assert metrics["correct_usable_rules_retained"] + metrics["invalid_rule_reuses"] + metrics["abstained"] + metrics["temporarily_unavailable"] == len(pages)
    return {**dict(metrics), "decision_states": dict(states)}


def _pareto(left, right):
    a = left["invalid_rule_reuses"], left["correct_usable_rules_retained"]
    b = right["invalid_rule_reuses"], right["correct_usable_rules_retained"]
    if a == b:
        verdict = "tie"
    elif a[0] <= b[0] and a[1] >= b[1]:
        verdict = "candidate_dominates"
    elif b[0] <= a[0] and b[1] >= a[1]:
        verdict = "baseline_dominates"
    else:
        verdict = "tradeoff"
    return {"candidate": {"invalid_reuses": a[0], "usable_rules": a[1]}, "baseline": {"invalid_reuses": b[0], "usable_rules": b[1]}, "verdict": verdict}


def evaluate(manifest, budgets, store_mode="disk"):
    core_path = PROJECT_ROOT / "research/memory_v2/core.py"
    core_hash_at_start = sha256(core_path.read_bytes()).hexdigest()
    runner_hash_at_start = sha256(Path(__file__).read_bytes()).hexdigest()
    from research.memory_v2.core import (
        Rule, FieldContract, Snapshot, MemoryStore, observe_page, evaluate_policy, POLICIES,
    )
    api = {"Rule": Rule, "FieldContract": FieldContract, "Snapshot": Snapshot, "observe_page": observe_page}
    started = perf_counter()
    scenarios = []
    totals = defaultdict(Counter)
    pareto_counts = defaultdict(Counter)
    with TemporaryDirectory(prefix="autospider-memory-v2-") as temporary:
        for scenario_index, scenario in enumerate(manifest["payload"]["scenarios"]):
            history = None if scenario.get("cold_start", False) else _prepare(scenario["history"], api)
            rounds = [(data, _prepare(data, api)) for data in scenario["rounds"]]
            rows = []
            for budget in budgets:
                stores = {}
                warmups = {}
                try:
                    for policy in POLICIES:
                        path = Path(temporary) / f"{scenario_index}-{budget}-{policy}.sqlite"
                        stores[policy] = MemoryStore(":memory:" if store_mode == "memory" else path)
                        if history is None:
                            warmups[policy] = {"skipped_cold_start": True, "audit_count": 0, "cache_hits": 0, "policy_seconds": 0, "projection_cost": {"html_parses": 0, "candidate_observations": 0}}
                        else:
                            hp, hr, hc, ho, _ = history
                            warmups[policy] = evaluate_policy(policy, hp, hr, hc, stores[policy], 10, observations=ho)
                    for round_data, prepared in rounds:
                        pages, rules, contract, observations, common_cost = prepared
                        policy_results = {}
                        # All policies complete before any private expected-text
                        # labels are handed to the independent scoring function.
                        raw_results = {policy: evaluate_policy(policy, pages, rules, contract, stores[policy], budget, observations=observations) for policy in POLICIES}
                        for policy, result in raw_results.items():
                            if result["audit_count"] > budget:
                                raise AssertionError(f"{policy} exceeded B={budget}")
                            metrics = _score(round_data, prepared, result)
                            policy_results[policy] = {"metrics": metrics, **result}
                            aggregate = totals[(scenario["split"], budget, policy)]
                            for key, value in metrics.items():
                                if isinstance(value, int):
                                    aggregate[key] += value
                            aggregate["additional_audits"] += result["audit_count"]
                            aggregate["cache_hits"] += result.get("cache_hits", 0)
                            aggregate["page_decisions"] += len(pages)
                            aggregate["rounds"] += 1
                        comparisons = {}
                        for baseline in ("exact_url_receipts", "complete_projection_fifo", "complete_projection_greedy"):
                            comparison = _pareto(policy_results["counterexample_scoped"]["metrics"], policy_results[baseline]["metrics"])
                            comparisons[baseline] = comparison
                            pareto_counts[(scenario["split"], budget, baseline)][comparison["verdict"]] += 1
                        rows.append({
                            "round_id": round_data["round_id"], "budget": budget,
                            "public_input_and_projection_cost": common_cost,
                            "policies": policy_results, "candidate_vs_baselines": comparisons,
                        })
                finally:
                    for store in stores.values():
                        store.close()
                # Preserve actual warmup expense, separately from current-task B.
                for row in rows:
                    if row["budget"] == budget:
                        row["historical_warmup_summary"] = {policy: {key: value for key, value in result.items() if key != "decisions"} for policy, result in warmups.items()}
            scenarios.append({
                "name": scenario["name"], "split": scenario["split"], "description": scenario["description"],
                "cold_start": scenario.get("cold_start", False),
                "history_public_cost": history[-1] if history else {"html_parses": 0, "candidate_observations": 0, "skipped_cold_start": True},
                "evaluation_rows": rows,
            })
    aggregate_rows = [{"split": split, "budget": budget, "policy": policy, "metrics": dict(metrics)} for (split, budget, policy), metrics in totals.items()]
    comparison_rows = [{"split": split, "budget": budget, "baseline": baseline, "counts": dict(counts)} for (split, budget, baseline), counts in pareto_counts.items()]
    if sha256(core_path.read_bytes()).hexdigest() != core_hash_at_start:
        raise RuntimeError("core.py changed during evaluation; refusing to save mixed-version evidence")
    if sha256(Path(__file__).read_bytes()).hexdigest() != runner_hash_at_start:
        raise RuntimeError("Runner changed during evaluation; refusing to save mixed-version evidence")
    return {
        "experiment": "memory_applicability_v2_isolated_research",
        "fixture_manifest_sha256": manifest["payload_sha256"],
        "fixture_source_sha256": manifest["fixture_source_sha256"],
        "core_source_sha256": core_hash_at_start,
        "runner_source_sha256": runner_hash_at_start,
        "source_hash_verified_unchanged_during_run": True,
        "store_mode": store_mode,
        "budgets": list(budgets), "policies": list(POLICIES),
        "cost_model": {
            "budget_unit": "additional fixed deterministic applicability audit of all candidates on one already acquired HTML snapshot",
            "historical_certification": "Each policy gets the same historical pages and B=10 warmup; its actual cost and cache state are reported separately.",
            "shared_cost": "Every policy is charged the full current HTML parse and complete candidate-evidence projection cost, including all relational facts.",
            "store_mode": store_mode,
            "store_mode_limit": "Memory mode preserves each policy's SQLite connection across repeated tasks, but does not measure disk persistence/restart performance. Disk diagnostic timings cannot be compared with memory-mode timings as a policy gain.",
            "audit_savings_limit": "Fewer duplicate contract decisions do not establish CPU savings: evidence extraction can already perform most contract work. Policy timings and common projection timings must be interpreted together.",
            "network_requests": 0, "model_calls": 0,
        },
        "information_boundary": {
            "policy_inputs": ["public Snapshot", "identical versioned candidate library", "identical fixed contract", "complete shared observations", "its persistent certificate store", "audit budget"],
            "private_scoring_only": ["expected_text", "development/holdout labels", "case/change descriptions"],
            "engineering_holdout": "Separately generated fixed-seed holdout, frozen before evaluation; not third-party blind testing.",
        },
        "scenarios": scenarios, "aggregate_by_split_budget_policy": aggregate_rows,
        "candidate_pareto_by_split_budget_baseline": comparison_rows,
        "runtime_seconds": perf_counter() - started,
        "limitations": [
            "Fixed deterministic Article/headline/source/body-role contract; no semantic diagnosis or arbitrary-site generalization claim.",
            "Public role/source markup may be absent; absence remains unproven rather than automatically treated as rule failure.",
            "The safe candidate and complete-projection-greedy baseline share the full-evidence safety gate; ties must be retained.",
            "No HTTP/token savings, integrated production behavior, statistical superiority, novelty, inventive step, or patentability is established.",
            "Raw-input fixtures and generated labels are accessible to the researchers; this is reproducible engineering holdout only.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze-manifest", type=Path)
    mode.add_argument("--manifest", type=Path)
    parser.add_argument("--seed", type=int, default=fixtures.SEED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--budgets", nargs="+", type=int, default=list(BUDGETS))
    parser.add_argument("--store-mode", choices=("memory", "disk"), default="disk")
    args = parser.parse_args()
    if args.freeze_manifest:
        print(json.dumps(freeze_manifest(args.freeze_manifest, args.seed), ensure_ascii=False, indent=2))
        return
    if any(value < 0 for value in args.budgets):
        parser.error("Budgets cannot be negative")
    if args.output is None:
        parser.error("Evaluation requires --output so the complete evidence is saved")
    manifest = load_manifest(args.manifest)
    result = evaluate(manifest, args.budgets, args.store_mode)
    _write_new(args.output, result)
    print(json.dumps({
        "experiment": result["experiment"], "output": str(args.output),
        "manifest_sha256": result["fixture_manifest_sha256"],
        "scenarios": len(result["scenarios"]),
        "evaluation_rows": sum(len(item["evaluation_rows"]) for item in result["scenarios"]),
        "candidate_pareto": result["candidate_pareto_by_split_budget_baseline"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
