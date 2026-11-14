"""Independent checks of the isolated v2 applicability-memory prototype.

These tests establish correctness for the explicit mechanical contract. They
do not establish semantic extraction correctness or patent novelty. The forced
certificate conflict is a consistency-defense test, not a naturally occurring
advantage of a deterministic full-input cache.
"""

from __future__ import annotations

from dataclasses import replace
from html import escape

import pytest

from research.memory_v2 import core
from research.memory_v2.core import (
    FieldContract,
    MemoryStore,
    Rule,
    Snapshot,
    audit_evidence,
    evaluate_policy,
    observe_page,
)


POLICY = "counterexample_scoped"
RULE = Rule("body", "#content")
CONTRACT = FieldContract()


def page(
    index: int = 0,
    *,
    headline: str = "Shared headline",
    list_title: str = "Shared headline",
    body: str = "Real article body",
    source: str | None = None,
    origin: str = "https://example.test",
    mode: str = "http_html",
    body_role: bool = True,
    extra_headline: bool = False,
    extra_source: bool = False,
    extra_body: bool = False,
    article: bool = True,
) -> Snapshot:
    url = f"{origin}/news/{1000 + index}"
    source_url = source if source is not None else url
    role_attribute = ' itemprop="articleBody"' if body_role else ""
    content = (
        f'<a itemprop="url" href="{escape(source_url, quote=True)}"></a>'
        f'<h1 itemprop="headline">{escape(headline)}</h1>'
        + ('<h2 itemprop="headline">Second headline</h2>' if extra_headline else "")
        + ('<a itemprop="url" href="https://other.test/wrong"></a>' if extra_source else "")
        + f'<div id="content" class="body"{role_attribute}>{escape(body)}</div>'
        + ('<div id="content" class="body" itemprop="articleBody">Second body</div>' if extra_body else "")
    )
    markup = (
        f'<html><body><article itemscope itemtype="https://schema.org/Article">{content}</article></body></html>'
        if article
        else f"<html><body><main>{content}</main></body></html>"
    )
    return Snapshot(url, list_title, markup, mode=mode)


def run(store, pages, *, budget=0, rules=None, contract=None, policy=POLICY):
    return evaluate_policy(
        policy,
        pages,
        rules or [RULE],
        contract or CONTRACT,
        store,
        budget=budget,
    )


def only_decision(result):
    assert len(result["decisions"]) == 1
    return result["decisions"][0]


def evidence(snapshot, rule=RULE, contract=CONTRACT):
    return observe_page(snapshot, [rule], contract).evidence[rule.rule_id]


def test_cold_projection_cannot_activate_certificate_without_budget(tmp_path, monkeypatch):
    calls = []
    original = core.audit_evidence

    def counted(ev):
        calls.append(ev.scope_key)
        return original(ev)

    monkeypatch.setattr(core, "audit_evidence", counted)
    snapshots = [page()]
    observations = [observe_page(snapshots[0], [RULE], CONTRACT)]
    assert calls == [], "Projection must not invoke the audited verification API."
    with MemoryStore(tmp_path / "memory.db") as store:
        result = evaluate_policy(POLICY, snapshots, [RULE], CONTRACT, store, budget=0, observations=observations)
        assert only_decision(result)["state"] == "PENDING"
        assert result["audit_count"] == 0
        assert result["cache_hits"] == 0
        assert store.lookup(observations[0].evidence[RULE.rule_id]) is None
    assert calls == []


def test_budget_one_audits_largest_scope_and_rejects_unaudited_scope(tmp_path):
    snapshots = [page(index) for index in range(8)] + [
        page(8, headline="Wrong source headline"),
        page(9, headline="Wrong source headline"),
    ]
    observations = [observe_page(snapshot, [RULE], CONTRACT) for snapshot in snapshots]
    assert len({item.evidence[RULE.rule_id].scope_key for item in observations[:8]}) == 1
    assert observations[0].evidence[RULE.rule_id].scope_key != observations[8].evidence[RULE.rule_id].scope_key
    with MemoryStore(tmp_path / "memory.db") as store:
        result = run(store, snapshots, budget=1)
    assert result["audit_count"] == 1
    assert result["audited_indices"] == [0]
    assert [item["state"] for item in result["decisions"][:8]] == ["VALID"] * 8
    assert [item["state"] for item in result["decisions"][8:]] == ["PENDING"] * 2
    assert all(item["selector"] is None for item in result["decisions"][8:])


def test_hidden_headline_counterexample_does_not_reuse_old_scope(tmp_path):
    original = page()
    changed = page(headline="Related story")
    assert evidence(original).scope_key != evidence(changed).scope_key
    with MemoryStore(tmp_path / "memory.db") as store:
        assert only_decision(run(store, [original], budget=1))["state"] == "VALID"
        no_budget = run(store, [changed], budget=0)
        assert only_decision(no_budget)["state"] == "PENDING"
        assert no_budget["cache_hits"] == 0
        checked = run(store, [changed], budget=1)
        assert only_decision(checked)["state"] == "INVALID"
        assert checked["audit_count"] == 1
        preserved = run(store, [original], budget=0)
        assert only_decision(preserved)["state"] == "VALID"
        assert preserved["audit_count"] == 0


def test_certificate_survives_restart_for_exact_scope(tmp_path):
    location = tmp_path / "persistent.db"
    with MemoryStore(location) as store:
        first = run(store, [page()], budget=1)
        assert only_decision(first)["state"] == "VALID"
    with MemoryStore(location) as reopened:
        cached = run(reopened, [page()], budget=0)
    assert only_decision(cached)["state"] == "VALID"
    assert cached["audit_count"] == 0
    assert cached["cache_hits"] >= 1


@pytest.mark.parametrize(
    "changed_rule,changed_contract,changed_page",
    [
        (Rule("body", "#content", version="2"), CONTRACT, page()),
        (Rule("body", ".body"), CONTRACT, page()),
        (Rule("body", "#content", field_name="summary"), CONTRACT, page()),
        (RULE, FieldContract(version="2"), page()),
        (RULE, FieldContract(require_source_url=False), page()),
        (RULE, FieldContract(require_body_role=False), page()),
        (RULE, CONTRACT, page(mode="playwright")),
        (RULE, CONTRACT, page(origin="https://other.test")),
        (RULE, CONTRACT, page(origin="http://example.test")),
    ],
    ids=["rule-version", "selector-content", "field", "contract-version", "source-contract", "body-contract", "mode", "host", "scheme"],
)
def test_receipt_is_bound_to_all_versions_and_execution_context(
    tmp_path, changed_rule, changed_contract, changed_page
):
    location = tmp_path / "bindings.db"
    with MemoryStore(location) as store:
        assert only_decision(run(store, [page()], budget=1))["state"] == "VALID"
    with MemoryStore(location) as store:
        result = run(store, [changed_page], rules=[changed_rule], contract=changed_contract, budget=0)
    assert result["cache_hits"] == 0
    assert only_decision(result)["state"] == "PENDING"


def test_forced_same_scope_conflict_is_persistently_quarantined(tmp_path):
    # Artificial contradictory writes test storage defenses. Complete evidence
    # plus this deterministic contract should not naturally generate them.
    ev = evidence(page())
    valid = audit_evidence(ev)
    assert valid.state == "VALID"
    invalid = type(valid)(state="INVALID", reason="artificial contradictory receipt")
    location = tmp_path / "conflict.db"
    with MemoryStore(location) as store:
        store.record(ev, valid)
        store.record(ev, invalid)
        assert only_decision(run(store, [page()], budget=0))["state"] != "VALID"
    with MemoryStore(location) as store:
        # A new agreeing observation must not silently clear the conflict.
        store.record(ev, valid)
        result = run(store, [page()], budget=1)
        assert only_decision(result)["state"] != "VALID"


def test_temporary_transport_failure_does_not_pollute_rule_receipt(tmp_path):
    original = page()
    unavailable = replace(original, markup="", status=503)
    with MemoryStore(tmp_path / "memory.db") as store:
        assert only_decision(run(store, [original], budget=1))["state"] == "VALID"
        outage = run(store, [unavailable], budget=1)
        assert only_decision(outage)["state"] == "PENDING"
        assert outage["audit_count"] == 0
        assert store.summary()["states"].get("INVALID", 0) == 0
        assert store.summary()["transport"][original.url] == "temporary_unavailable"
        recovered = run(store, [original], budget=0)
    assert only_decision(recovered)["state"] == "VALID"
    assert recovered["audit_count"] == 0
    assert recovered["cache_hits"] >= 1


@pytest.mark.parametrize(
    "snapshot",
    [page(article=False), page(extra_headline=True), page(extra_body=True), page(extra_source=True)],
    ids=["no-article-witness", "ambiguous-headline", "ambiguous-match", "ambiguous-source"],
)
def test_unknown_or_ambiguous_evidence_cannot_invalidate_rule(tmp_path, snapshot):
    ev = evidence(snapshot)
    assert audit_evidence(ev).state == "PENDING"
    with MemoryStore(tmp_path / "memory.db") as store:
        checked = run(store, [snapshot], budget=1)
        assert only_decision(checked)["state"] == "PENDING"
        assert only_decision(checked)["selector"] is None
        retry = run(store, [snapshot], budget=0)
        assert only_decision(retry)["state"] == "PENDING"
        assert store.summary()["states"].get("INVALID", 0) == 0
        # Unknown evidence must not contaminate an independently supported scope.
        good = run(store, [page()], budget=1)
        assert only_decision(good)["state"] == "VALID"


@pytest.mark.parametrize(
    "changed",
    [
        page(headline="Other article"),
        page(list_title="Other list title"),
        page(source="https://other.test/news/1000"),
        page(body_role=False),
        page(body=""),
        page(extra_headline=True),
        page(extra_source=True),
        page(extra_body=True),
        page(article=False),
    ],
    ids=["headline", "list-title", "source-url", "body-role", "nonempty", "headline-count", "source-count", "match-count", "article-ancestor"],
)
def test_every_contract_read_dependency_changes_evidence_scope(changed):
    original = evidence(page())
    altered = evidence(changed)
    assert original.scope_key != altered.scope_key
    assert audit_evidence(original).state == "VALID"
    assert audit_evidence(altered).state != "VALID"


def test_equal_projected_scope_has_equal_fixed_contract_verdict():
    snapshots = [page(index, body=f"Different but nonempty text {index}") for index in range(4)]
    snapshots += [page(headline="Wrong"), page(source="https://other.test/"), page(body_role=False)]
    seen = {}
    for snapshot in snapshots:
        ev = evidence(snapshot)
        verdict = audit_evidence(ev)
        value = (verdict.state, verdict.reason)
        if ev.scope_key in seen:
            assert seen[ev.scope_key] == value
        seen[ev.scope_key] = value


def test_exact_url_receipt_does_not_survive_snapshot_change(tmp_path):
    original = page(body="Original nonempty body")
    changed = page(body="Updated nonempty body")
    before = observe_page(original, [RULE], CONTRACT)
    after = observe_page(changed, [RULE], CONTRACT)
    assert before.snapshot_hash != after.snapshot_hash
    with MemoryStore(tmp_path / "memory.db") as store:
        assert only_decision(run(store, [original], budget=1, policy="exact_url_receipts"))["state"] == "VALID"
        result = run(store, [changed], budget=0, policy="exact_url_receipts")
    assert only_decision(result)["state"] == "PENDING"
    assert result["cache_hits"] == 0


def test_candidate_and_complete_projection_baseline_share_information(tmp_path):
    # This strong baseline receives exactly the same full contract projection;
    # a deterministic exact-scope cache offers no expected information advantage.
    snapshots = [page(index) for index in range(8)] + [page(8, headline="Wrong"), page(9, headline="Wrong")]
    results = []
    for policy in (POLICY, "complete_projection_greedy"):
        with MemoryStore(tmp_path / f"{policy}.db") as store:
            results.append(run(store, snapshots, budget=1, policy=policy))
    assert results[0]["decisions"] == results[1]["decisions"]
    assert results[0]["audited_indices"] == results[1]["audited_indices"]
    assert results[0]["audit_count"] == results[1]["audit_count"]


@pytest.mark.parametrize("component", ["PROJECTOR_VERSION", "VERIFIER_VERSION"])
def test_projector_and_verifier_implementation_versions_invalidate_receipt(tmp_path, monkeypatch, component):
    with MemoryStore(tmp_path / "implementation-version.db") as store:
        assert only_decision(run(store, [page()], budget=1))["state"] == "VALID"
        monkeypatch.setattr(core, component, "independent-test/new-implementation")
        result = run(store, [page()], budget=0)
    assert only_decision(result)["state"] == "PENDING"
    assert result["cache_hits"] == 0


def test_contract_pass_is_not_a_semantic_body_truth_oracle():
    # A publisher can put incorrect text under otherwise consistent Article
    # metadata. This passes the declared mechanical contract; no test or report
    # should reinterpret that result as independent semantic ground truth.
    declared_but_wrong = page(body="Unrelated yet nonempty related-links content")
    verdict = audit_evidence(evidence(declared_but_wrong))
    assert verdict.state == "VALID"


def tampered_evidence(ev, component):
    if component == "facts":
        return replace(ev, facts={**ev.facts, "headline_relation": "mismatch"})
    if component == "scope":
        return replace(ev, scope_key="0" * 64)
    if component == "url":
        return replace(ev, url="https://other.test/news/1000")
    if component == "same_origin_url":
        return replace(ev, url="https://example.test/news/9999")
    if component == "snapshot_hash":
        return replace(ev, snapshot_hash="0" * 64)
    if component == "rule_id":
        return replace(ev, rule_id="some-other-rule")
    if component == "selector":
        return replace(ev, selector=".unrelated")
    raise AssertionError(component)


TAMPERED_COMPONENTS = ["facts", "scope", "url", "same_origin_url", "snapshot_hash", "rule_id", "selector"]


@pytest.mark.parametrize("component", TAMPERED_COMPONENTS)
def test_evaluate_rejects_tampered_precomputed_evidence(tmp_path, component):
    snapshot = page()
    observation = observe_page(snapshot, [RULE], CONTRACT)
    original = observation.evidence[RULE.rule_id]
    forged = replace(observation, evidence={RULE.rule_id: tampered_evidence(original, component)})
    with MemoryStore(tmp_path / "tampered-observations.db") as store:
        assert only_decision(run(store, [snapshot], budget=1))["state"] == "VALID"
        with pytest.raises(ValueError):
            evaluate_policy(POLICY, [snapshot], [RULE], CONTRACT, store, budget=0, observations=[forged])


@pytest.mark.parametrize("component", TAMPERED_COMPONENTS)
def test_store_boundary_rejects_inconsistent_evidence(tmp_path, component):
    original = evidence(page())
    verdict = audit_evidence(original)
    altered = tampered_evidence(original, component)
    with MemoryStore(tmp_path / "tampered-store.db") as store:
        store.record(original, verdict)
        with pytest.raises(ValueError):
            store.lookup(altered)
        with pytest.raises(ValueError):
            store.record(altered, verdict)


def test_mutating_nested_facts_after_projection_is_detected(tmp_path):
    ev = evidence(page())
    verdict = audit_evidence(ev)
    with MemoryStore(tmp_path / "nested-mutation.db") as store:
        store.record(ev, verdict)
        ev.facts["headline_relation"] = "mismatch"
        with pytest.raises(ValueError):
            store.lookup(ev)


def test_invalid_css_is_bounded_invalid_evidence_and_needs_audit(tmp_path):
    invalid = Rule("broken", "[")
    snapshot = page()
    observation = observe_page(snapshot, [invalid], CONTRACT)
    verdict = audit_evidence(observation.evidence[invalid.rule_id])
    assert verdict.state == "INVALID"
    assert verdict.reason == "invalid_selector"
    with MemoryStore(tmp_path / "invalid-css.db") as store:
        assert only_decision(run(store, [snapshot], rules=[invalid], budget=0))["state"] == "PENDING"
        checked = run(store, [snapshot], rules=[invalid], budget=1)
    assert checked["audit_count"] == 1
    assert only_decision(checked)["state"] == "INVALID"


def test_evidence_dictionary_insertion_order_cannot_change_audit_selection(tmp_path):
    # A cached INVALID first rule makes the second rule's uncovered scopes
    # decisive. Incorrectly zipping rule order with dict.values() instead chooses
    # the two-page prefix group when the evidence dictionary is reversed.
    absent = Rule("absent", "#never-present")
    rules = [absent, RULE]
    snapshots = [page(0, headline="Wrong"), page(1, headline="Wrong")] + [
        page(index) for index in range(2, 10)
    ]
    observations = [observe_page(snapshot, rules, CONTRACT) for snapshot in snapshots]
    reordered = [replace(item, evidence=dict(reversed(list(item.evidence.items())))) for item in observations]
    results = []
    for name, supplied in (("normal", observations), ("reversed", reordered)):
        with MemoryStore(tmp_path / f"order-{name}.db") as store:
            ev = observations[0].evidence[absent.rule_id]
            store.record(ev, audit_evidence(ev))
            results.append(evaluate_policy(POLICY, snapshots, rules, CONTRACT, store, budget=1, observations=supplied))
    assert results[0]["audited_indices"] == [2]
    assert results[1]["audited_indices"] == results[0]["audited_indices"]
    assert results[1]["decisions"] == results[0]["decisions"]


@pytest.mark.parametrize(
    "headline,list_title",
    [("", ""), ("   ", "\t\n"), ("", "Shared headline"), ("Shared headline", "")],
    ids=["both-empty", "both-whitespace", "headline-empty", "list-title-empty"],
)
def test_empty_title_evidence_is_unknown_not_a_positive_or_negative_witness(headline, list_title):
    ev = evidence(page(headline=headline, list_title=list_title))
    assert audit_evidence(ev).state == "PENDING"


@pytest.mark.parametrize("borrowed", ["headline", "source", "both"])
def test_nested_independent_itemscope_cannot_supply_outer_article_provenance(borrowed):
    url = "https://example.test/news/1000"
    title = "Shared headline"
    outer_headline = "" if borrowed in {"headline", "both"} else f'<h1 itemprop="headline">{title}</h1>'
    outer_source = "" if borrowed in {"source", "both"} else f'<a itemprop="url" href="{url}"></a>'
    nested_headline = f'<span itemprop="headline">{title}</span>' if borrowed in {"headline", "both"} else ""
    nested_source = f'<a itemprop="url" href="{url}"></a>' if borrowed in {"source", "both"} else ""
    markup = (
        '<html><body><article itemscope itemtype="https://schema.org/Article">'
        + outer_headline + outer_source
        + '<div itemscope itemtype="https://schema.org/Person">'
        + nested_headline + nested_source + '</div>'
        + '<div id="content" itemprop="articleBody">Real body</div>'
        + '</article></body></html>'
    )
    verdict = audit_evidence(evidence(Snapshot(url, title, markup)))
    assert verdict.state == "PENDING", "A nested entity's properties are not the outer Article's provenance."


@pytest.mark.parametrize("nested_type", ["https://schema.org/Person", "https://schema.org/Organization", ""])
def test_body_nearest_independent_scope_must_own_article_properties(nested_type):
    url = "https://example.test/news/1000"
    title = "Shared headline"
    type_attribute = f' itemtype="{nested_type}"' if nested_type else ""
    markup = (
        '<html><body><article itemscope itemtype="https://schema.org/Article">'
        f'<h1 itemprop="headline">{title}</h1><a itemprop="url" href="{url}"></a>'
        f'<div itemscope{type_attribute}>'
        '<div id="content" itemprop="articleBody">Independent entity content</div>'
        '</div></article></body></html>'
    )
    verdict = audit_evidence(evidence(Snapshot(url, title, markup)))
    assert verdict.state == "PENDING", "A distant Article cannot take ownership across an independent scope."


def test_nested_entity_metadata_does_not_ambiguitize_valid_outer_article():
    url = "https://example.test/news/1000"
    title = "Shared headline"
    markup = (
        '<html><body><article itemscope itemtype="https://schema.org/Article">'
        f'<h1 itemprop="headline">{title}</h1><a itemprop="url" href="{url}"></a>'
        '<div itemscope itemtype="https://schema.org/Person">'
        '<span itemprop="headline">Unrelated person label</span>'
        '<a itemprop="url" href="https://example.test/person/1"></a></div>'
        '<div id="content" itemprop="articleBody">Actual outer article body</div>'
        '</article></body></html>'
    )
    assert audit_evidence(evidence(Snapshot(url, title, markup))).state == "VALID"
