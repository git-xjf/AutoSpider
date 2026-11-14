"""Standalone applicability-memory pilot; it does not modify the crawler runtime.

All policies see the same selector observations from already acquired HTML
snapshots. A budget unit is one *additional applicability verification* on one
snapshot, not an HTTP request, model call, or saved network operation. The
shared verifier checks an explicit public Article/headline provenance contract;
it does not receive the held-out expected article text used by the scorer.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html import escape
from itertools import combinations
import json
from pathlib import Path
from time import perf_counter
from typing import Callable

from lxml import html as lxml_html


SELECTORS = ("#content", "#article", '[itemprop="articleBody"]')
OLD_SELECTOR = SELECTORS[0]
POLICIES = (
    "always_reuse",
    "ttl_prefix_revalidation",
    "route_stratified_revalidation",
    "explicit_dependency_local_revalidation",
    "observation_difference_minimal_scope",
)


@dataclass(frozen=True)
class Snapshot:
    url: str
    list_title: str
    markup: str
    status: int = 200


@dataclass(frozen=True)
class Observation:
    url: str
    route: str
    available: bool
    features: tuple[tuple[str, str], ...]

    def values(self) -> dict[str, str]:
        return dict(self.features)


@dataclass(frozen=True)
class Verification:
    index: int
    transient: bool
    valid: tuple[bool, ...]
    reasons: tuple[str, ...]

    def valid_for(self, selector: str) -> bool:
        return self.valid[SELECTORS.index(selector)]


@dataclass
class Decision:
    selectors: list[str | None]
    notes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    snapshots: tuple[Snapshot, ...]
    expected_text: tuple[str, ...]
    budget: int = 2


def _text(node) -> str:
    return " ".join(part.strip() for part in node.itertext() if part.strip())


def _nodes(tree, selector):
    return tree.cssselect(selector) if tree is not None else []


def _observations(snapshot: Snapshot, tree) -> Observation:
    """Only non-text selector observations enter the online policy API.

    The existing source snapshots are public input, but policies do not receive
    their HTML or the scoring labels. Headline values and body text are omitted.
    Attribute observations include public itemtype metadata; no private test
    marker or change label is inserted in the HTML.
    """
    values = {}
    for position, selector in enumerate(SELECTORS):
        matched = _nodes(tree, selector)
        prefix = f"s{position}"
        values[prefix + ".count"] = str(len(matched))
        values[prefix + ".tag"] = matched[0].tag if matched else "__absent__"
        values[prefix + ".class"] = matched[0].get("class", "") if matched else "__absent__"
        ancestry = []
        if matched:
            for node in [matched[0], *matched[0].iterancestors()][:4]:
                ancestry.append(node.tag + ":" + node.get("itemtype", ""))
        values[prefix + ".ancestry"] = "/".join(ancestry)
    return Observation(
        snapshot.url,
        "/news/{id}.html",  # All fixtures intentionally collide on one route.
        snapshot.status < 500 and tree is not None,
        tuple(sorted(values.items())),
    )


def _contract(snapshot: Snapshot, tree, index: int) -> Verification:
    """Shared mechanical contract, independent of the expected-text scorer.

    An extraction is applicable iff its first nonempty match belongs to a
    schema.org/Article and that Article's headline matches the list-page title.
    This is a controlled-fixture contract, not a general semantic body oracle.
    """
    if snapshot.status >= 500 or tree is None:
        return Verification(index, True, (False,) * len(SELECTORS), ("transient_unavailable",) * len(SELECTORS))
    valid, reasons = [], []
    for selector in SELECTORS:
        matched = _nodes(tree, selector)
        reason = "no_match"
        if matched:
            selected = matched[0]
            article = next((
                ancestor for ancestor in [selected, *selected.iterancestors()]
                if ancestor.tag == "article"
                and ancestor.get("itemtype") == "https://schema.org/Article"
            ), None)
            if not _text(selected):
                reason = "empty_match"
            elif article is None:
                reason = "outside_declared_article"
            else:
                headlines = article.cssselect('[itemprop="headline"]')
                reason = "valid" if headlines and _text(headlines[0]) == snapshot.list_title else "headline_origin_mismatch"
        valid.append(reason == "valid")
        reasons.append(reason)
    return Verification(index, False, tuple(valid), tuple(reasons))


class ProbeBudget:
    """Policies cannot ask this object for text, HTML, or scoring labels."""

    def __init__(self, budget: int, verifier: Callable[[int], Verification]):
        self.limit = budget
        self._verifier = verifier
        self.results: dict[int, Verification] = {}
        self.elapsed_seconds = 0.0

    def probe(self, index: int) -> Verification:
        if index in self.results:
            return self.results[index]
        if len(self.results) >= self.limit:
            raise RuntimeError("additional applicability verification budget exhausted")
        started = perf_counter()
        result = self._verifier(index)
        self.elapsed_seconds += perf_counter() - started
        self.results[index] = result
        return result


def _best(result: Verification) -> str | None:
    return next((selector for selector in SELECTORS if result.valid_for(selector)), None)


def _global_scope_policy(observed, history, historical_proofs, budget, stratified=False):
    selected = list(range(len(observed)))
    if stratified:
        by_route = defaultdict(list)
        for index, item in enumerate(observed):
            by_route[item.route].append(index)
        heads = [indices[0] for indices in by_route.values()]
        selected = heads + [index for index in selected if index not in heads]
    for index in selected[:budget.limit]:
        budget.probe(index)
    results = [value for value in budget.results.values() if not value.transient]
    applicable = [selector for selector in SELECTORS if results and all(item.valid_for(selector) for item in results)]
    replacement = applicable[0] if applicable else None
    # Transient evidence never invalidates a selector; keep the old memory if
    # the entire verification set was temporarily unavailable.
    if not results:
        replacement = OLD_SELECTOR
    decisions = [replacement if item.available else None for item in observed]
    for index, verification in budget.results.items():
        decisions[index] = None if verification.transient else _best(verification)
    return Decision(decisions, {"scope": "route" if stratified else "domain", "transient_not_selector_invalidation": True})


def _dependency_policy(observed, history, historical_proofs, budget):
    """Strong baseline: all candidate dependencies, per-URL proof cache.

    It uses exactly the same full observation vector as the proposal, retains
    unchanged per-URL certificates, revalidates dirty entries, caches successes,
    and abstains for unverified dirty entries. No candidate dependency is hidden
    from it. It deliberately does not generalize proof across different URLs.
    """
    decisions = []
    dirty = []
    for index, current in enumerate(observed):
        if not current.available:
            decisions.append(None)
            if len(budget.results) < budget.limit:
                budget.probe(index)  # Classify the failure without deleting a rule.
        elif current.features == history[index].features:
            decisions.append(_best(historical_proofs[index]))
        else:
            decisions.append(None)
            dirty.append(index)
    for index in dirty:
        if len(budget.results) >= budget.limit:
            break
        result = budget.probe(index)
        decisions[index] = None if result.transient else _best(result)
    return Decision(decisions, {"dirty_indices": dirty, "unverified_dirty_action": "abstain", "dependency_set": "all_shared_candidates", "proof_scope": "exact_url_and_observations", "transient_not_selector_invalidation": True})


def _distance(left: Observation, right: Observation) -> int:
    return sum(value != right.values().get(key) for key, value in left.features)


def _minimal_rule(selector, examples, observed):
    """Smallest observed feature conjunction excluding every known counterexample.

    No change labels or unprobed contract outcomes enter this search. If positive
    and negative examples have identical observations, no separating rule exists.
    It may still generalize incorrectly when a counterexample is unobserved;
    the pilot includes and reports exactly that failure.
    """
    positives = [item for item, proof in examples if not proof.transient and proof.valid_for(selector)]
    negatives = [item for item, proof in examples if not proof.transient and not proof.valid_for(selector)]
    if not positives:
        return None
    keys = [key for key, _ in positives[0].features]
    for size in range(3):
        options = []
        for keys_subset in combinations(keys, size):
            for positive in positives:
                values = positive.values()
                condition = tuple((key, values[key]) for key in keys_subset)
                if any(_matches(negative, condition) for negative in negatives):
                    continue
                positive_coverage = sum(_matches(item, condition) for item in positives)
                current_coverage = sum(item.available and _matches(item, condition) for item in observed)
                options.append((positive_coverage, current_coverage, condition))
        if options:
            return sorted(options, key=lambda option: (-option[0], -option[1], option[2]))[0][2]
    return None


def _matches(observation: Observation, condition) -> bool:
    values = observation.values()
    return all(values.get(key) == value for key, value in condition)


def _candidate_policy(observed, history, historical_proofs, budget):
    examples = list(zip(history, historical_proofs))
    changed = [index for index, item in enumerate(observed) if item.features != history[index].features or not item.available]
    counts = Counter(observed[index].features for index in changed)
    while len(budget.results) < budget.limit:
        remaining = [index for index in changed if index not in budget.results]
        if not remaining:
            break
        visited_groups = {observed[index].features for index in budget.results}
        # A new observation family precedes another instance of an inspected
        # family. Within a family, spend spare budget on a real counterexample
        # check. Labels, expected text, and variant indices are unavailable.
        target = min(remaining, key=lambda index: (
            observed[index].features in visited_groups,
            -_distance(observed[index], history[index]),
            counts[observed[index].features],
            index,
        ))
        result = budget.probe(target)
        examples.append((observed[target], result))
    rules = {selector: _minimal_rule(selector, examples, observed) for selector in SELECTORS}
    decisions = []
    for index, item in enumerate(observed):
        if not item.available:
            decisions.append(None)
        elif index in budget.results:
            decisions.append(_best(budget.results[index]))
        elif item.features == history[index].features:
            decisions.append(_best(historical_proofs[index]))
        else:
            decisions.append(next((selector for selector in SELECTORS if rules[selector] is not None and _matches(item, rules[selector])), None))
    return Decision(decisions, {
        "changed_indices": changed,
        "rules": {selector: dict(condition) if condition is not None else None for selector, condition in rules.items()},
        "rule_search_max_features": 2,
        "counterexamples_used": "historical_certification_and_current_probes_only",
        "unseen_counterexample_risk": True,
        "transient_not_selector_invalidation": True,
    })


def _policy(name, observed, history, historical_proofs, budget):
    if name == "always_reuse":
        return Decision([OLD_SELECTOR if item.available else None for item in observed])
    if name == "ttl_prefix_revalidation":
        return _global_scope_policy(observed, history, historical_proofs, budget)
    if name == "route_stratified_revalidation":
        return _global_scope_policy(observed, history, historical_proofs, budget, stratified=True)
    if name == "explicit_dependency_local_revalidation":
        return _dependency_policy(observed, history, historical_proofs, budget)
    return _candidate_policy(observed, history, historical_proofs, budget)


def _body(index):
    return f"The complete article body for record {index}; evidence and detailed reporting belong to this article."


def _snapshot(index, layout="normal", unrelated_edit=False, wrong_headline=False):
    title = f"Research report {index}"
    headline = f"Another report {index}" if wrong_headline else title
    header = '<header><nav><a>Rearranged unrelated navigation</a></nav><div class="ad-slot"></div></header>' if unrelated_edit else "<header>News</header>"
    if layout == "normal":
        content = f'<article itemscope="" itemtype="https://schema.org/Article"><h1 itemprop="headline">{escape(headline)}</h1><div id="content" class="body">{escape(_body(index))}</div></article>'
    else:
        if layout == "article":
            body_node = f'<div id="article" class="body">{escape(_body(index))}</div>'
        elif layout == "itemprop":
            body_node = f'<div itemprop="articleBody" class="body">{escape(_body(index))}</div>'
        else:
            body_node = f'<div id="unseen-body" class="body">{escape(_body(index))}</div>'
        content = (
            '<article itemtype="https://schema.org/ItemList"><h2>Recommended reading</h2>'
            f'<div id="content" class="recommendations">Nonempty recommendation for topic {index}.</div></article>'
            f'<article itemscope="" itemtype="https://schema.org/Article"><h1 itemprop="headline">{escape(headline)}</h1>{body_node}</article>'
        )
    return Snapshot(f"https://fixture.invalid/news/{1000 + index}.html", title, f"<html><body>{header}<main>{content}</main></body></html>")


def fixtures():
    def build(name, description, layouts=None, budget=2, unrelated=False, unavailable=None, wrong_title=None):
        snapshots = []
        for index in range(10):
            snapshot = _snapshot(index, (layouts or {}).get(index, "normal"), unrelated, index in (wrong_title or set()))
            if index in (unavailable or set()):
                snapshot = Snapshot(snapshot.url, snapshot.list_title, "<html><body>Temporary service error</body></html>", 503)
            snapshots.append(snapshot)
        return Fixture(name, description, tuple(snapshots), tuple(_body(index) for index in range(10)), budget)

    return [
        build("unrelated_dom_edit", "Only header/navigation changed; selector dependencies and Article provenance remain valid.", unrelated=True),
        build("single_template_drift", "All ten pages changed from #content to #article; a fixed library already contains both.", {index: "article" for index in range(10)}),
        build("same_route_tail_variant_8_plus_2", "Eight normal and two tail variants share one URL route; old #content is nonempty recommendations on variants.", {8: "article", 9: "article"}),
        build("same_route_head_variant", "The first two pages are variants; prefix replacement can overgeneralize into eight normal pages.", {0: "article", 1: "article"}),
        build("temporary_transport_failure", "A 503 must cause temporary URL unavailability, not permanent selector invalidation.", unavailable={0}),
        build("budget_insufficient_two_new_families", "Budget one cannot certify both a #article family and a distinct articleBody family.", {8: "article", 9: "itemprop"}, budget=1),
        build("unseen_counterexample_same_observations", "Two pages share selector observations; the unprobed page has a headline-origin mismatch, invisible to non-text observations.", {8: "article", 9: "article"}, budget=1, wrong_title={9}),
        build("observed_counterexample_prevents_scope", "With budget two, identical-observation positive/negative examples prevent a generalized alternative rule.", {8: "article", 9: "article"}, budget=2, wrong_title={9}),
        build("candidate_library_missing_repair", "The real body uses an unseen selector; no method may invent an oracle repair.", {8: "unseen", 9: "unseen"}),
    ]


def _score(fixture, trees, decision):
    """Held-out text is first read here, after the policy has returned decisions."""
    correct = wrong_nonempty = empty = abstained = unavailable = 0
    for index, selector in enumerate(decision.selectors):
        if fixture.snapshots[index].status >= 500:
            unavailable += 1
        elif selector is None:
            abstained += 1
        else:
            matched = _nodes(trees[index], selector)
            value = _text(matched[0]) if matched else ""
            if value == fixture.expected_text[index]:
                # The scoring target is extraction text AND the shared source
                # contract; correct-looking text from the wrong Article is not
                # counted as a usable application of memory.
                proof = _contract(fixture.snapshots[index], trees[index], index)
                if proof.valid_for(selector):
                    correct += 1
                else:
                    wrong_nonempty += 1
            elif value:
                wrong_nonempty += 1
            else:
                empty += 1
    assert correct + wrong_nonempty + empty + abstained + unavailable == len(trees)
    return {
        "correct_usable_rules_retained": correct,
        "invalid_rule_reuses": wrong_nonempty + empty,
        "wrong_nonempty_extractions": wrong_nonempty,
        "empty_or_missing_extractions": empty,
        "abstained": abstained,
        "temporarily_unavailable": unavailable,
    }


def _pareto(candidate, baseline):
    candidate_pair = (candidate["invalid_rule_reuses"], candidate["correct_usable_rules_retained"])
    baseline_pair = (baseline["invalid_rule_reuses"], baseline["correct_usable_rules_retained"])
    if candidate_pair == baseline_pair:
        verdict = "tie"
    elif candidate_pair[0] <= baseline_pair[0] and candidate_pair[1] >= baseline_pair[1]:
        verdict = "candidate_dominates"
    elif baseline_pair[0] <= candidate_pair[0] and baseline_pair[1] >= candidate_pair[1]:
        verdict = "strong_baseline_dominates"
    else:
        verdict = "tradeoff_neither_dominates"
    return {"candidate": {"invalid_reuses": candidate_pair[0], "usable_rules": candidate_pair[1]}, "strong_baseline": {"invalid_reuses": baseline_pair[0], "usable_rules": baseline_pair[1]}, "verdict": verdict}


def run():
    started = perf_counter()
    historical_snapshots = tuple(_snapshot(index) for index in range(10))
    historical_trees = tuple(lxml_html.fromstring(item.markup) for item in historical_snapshots)
    history = tuple(_observations(item, tree) for item, tree in zip(historical_snapshots, historical_trees))
    historical_proofs = tuple(_contract(item, tree, index) for index, (item, tree) in enumerate(zip(historical_snapshots, historical_trees)))
    cases = []
    for fixture in fixtures():
        scan_started = perf_counter()
        trees = tuple(lxml_html.fromstring(item.markup) for item in fixture.snapshots)
        observed = tuple(_observations(item, tree) for item, tree in zip(fixture.snapshots, trees))
        scan_seconds = perf_counter() - scan_started
        policies = {}
        for name in POLICIES:
            verifier = ProbeBudget(fixture.budget, lambda index: _contract(fixture.snapshots[index], trees[index], index))
            policy_started = perf_counter()
            decision = _policy(name, observed, history, historical_proofs, verifier)
            elapsed = perf_counter() - policy_started
            metrics = _score(fixture, trees, decision)
            policies[name] = {
                **metrics,
                "additional_applicability_verifications": len(verifier.results),
                "verified_indices": list(verifier.results),
                "verification_seconds": round(verifier.elapsed_seconds, 6),
                "policy_seconds_including_verification": round(elapsed, 6),
                "decisions": decision.selectors,
                "verification_trace": [{"index": result.index, "transient": result.transient, "valid_candidates": [selector for selector in SELECTORS if result.valid_for(selector)], "reasons": dict(zip(SELECTORS, result.reasons))} for result in verifier.results.values()],
                "notes": decision.notes,
            }
            assert len(verifier.results) <= fixture.budget
        cases.append({
            "case": fixture.name,
            "description": fixture.description,
            "shared_extra_verification_budget": fixture.budget,
            "public_input_cost": {
                "snapshot_count": len(trees),
                "snapshot_bytes": sum(len(item.markup.encode("utf-8")) for item in fixture.snapshots),
                "html_parses": len(trees),
                "selector_observations": len(trees) * len(SELECTORS),
                "parse_and_observation_seconds": round(scan_seconds, 6),
                "network_requests_in_pilot": 0,
                "llm_calls_in_pilot": 0,
            },
            "policies": policies,
            "candidate_vs_strong_pareto": _pareto(policies[POLICIES[-1]], policies[POLICIES[-2]]),
        })
    by_name = {item["case"]: item for item in cases}
    assert by_name["same_route_tail_variant_8_plus_2"]["policies"]["always_reuse"]["wrong_nonempty_extractions"] == 2
    assert by_name["same_route_tail_variant_8_plus_2"]["candidate_vs_strong_pareto"]["verdict"] == "tie"
    assert by_name["unseen_counterexample_same_observations"]["candidate_vs_strong_pareto"]["verdict"] == "strong_baseline_dominates"
    assert by_name["observed_counterexample_prevents_scope"]["policies"][POLICIES[-1]]["invalid_rule_reuses"] == 0
    verdicts = Counter(item["candidate_vs_strong_pareto"]["verdict"] for item in cases)
    return {
        "experiment": "memory_applicability_pilot",
        "version": 1,
        "candidate_library_shared_by_all_policies": list(SELECTORS),
        "old_memory_selector": OLD_SELECTOR,
        "cost_model": {
            "budget_unit": "additional applicability verification on an already acquired HTML snapshot",
            "common_historical_preparation": {"html_parses": 10, "all_candidate_contract_checks": 10},
            "common_current_cost": "Every policy receives the same non-text candidate observations from all ten already acquired snapshots; per-case parsing/candidate-computation cost is reported separately.",
            "not_measured": ["saved HTTP requests", "saved model tokens", "end-to-end crawler integration", "public-site accuracy"],
        },
        "online_information_boundary": {
            "allowed": ["URL and route", "existing candidate selector library", "non-text selector observations", "historically certified candidate outcomes", "outcomes of budgeted shared applicability contract"],
            "not_exposed_to_policies": ["expected extraction text", "fixture case name", "change labels", "variant indices", "unprobed contract outcomes", "raw HTML/body text"],
            "contract": "first nonempty selected node belongs to a public schema.org/Article whose headline matches the list-page title",
            "contract_limit": "Controlled-source mechanical provenance, not a semantic oracle for arbitrary websites.",
        },
        "cases": cases,
        "candidate_vs_strong_pareto_counts": dict(verdicts),
        "total_runtime_seconds": round(perf_counter() - started, 6),
        "limitations": [
            "Deterministic synthetic snapshots; no independent random trials or confidence intervals.",
            "All candidate rules exist in the common historical library; no model-generated repair or hidden correct selector.",
            "Minimal scope is searched over at most two observed features; unseen same-observation counterexamples can cause unsafe reuse.",
            "The strong baseline conservatively certifies exact URLs; candidate generalization trades abstention against false applicability.",
            "A stronger baseline could share exact certificates or learn abstract dependency scopes; novelty over those mechanisms is not established.",
            "Transient failures are classified without selector invalidation, but repeated-task persistence and TTL expiry are not implemented in this pilot.",
            "Prototype results do not prove integration, general effectiveness, inventive step, or patentability.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save the complete pilot JSON; no file is written when omitted.")
    arguments = parser.parse_args()
    result = run()
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "experiment": result["experiment"],
        "output": str(arguments.output) if arguments.output else None,
        "pareto_counts": result["candidate_vs_strong_pareto_counts"],
        "cases": [{"case": item["case"], "pareto": item["candidate_vs_strong_pareto"], "policies": {name: {key: row[key] for key in ("correct_usable_rules_retained", "invalid_rule_reuses", "abstained", "additional_applicability_verifications")} for name, row in item["policies"].items()}} for item in result["cases"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
