"""Second, explicitly post-r1 iteration: preserve historical counterexamples.

This is a conventional conjunction reference, not a claim of a new algorithm.
Two additional authored failure families prevent a zero-error-only showcase.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from lxml import html

from .fixtures import FixtureCase, cases
from .study import (
    SOURCES, changed_keys, counts, graph_observational_guard,
    observational_guard, observed_dependencies, run_study, source_hashes, write_new,
)

EXTRA_SOURCES = (
    "research/selector_applicability/veto.py",
    "scripts/probe_selector_negative_veto.py",
    "docs/patent/selector-applicability-2026-10-26/PROTOCOL_NEGATIVE_VETO.md",
)


@dataclass(frozen=True)
class AdditionalCase(FixtureCase):
    @property
    def selector(self):
        return "//section[@class='picked']/article"

    def render(self, url, *, after=False):
        body = FixtureCase("generic_channel_change", self.old).render(url)
        tree = html.fromstring(body)
        primary = tree.xpath("//section[@data-current='yes']")[0]
        secondary = tree.xpath("//section[@data-current='no']")[0]
        if self.family == "unseen_anchor_cofailure":
            primary.set("class", "other" if after else "picked")
            secondary.set("class", "picked" if after else "other")
            if after:
                primary.set("id", "new-slot")
                secondary.set("id", "slot")
        elif self.family == "reversible_parameter_effect":
            calibration_channel = str(self.old + 17)
            picked_primary = primary.get("data-channel") != calibration_channel
            primary.set("class", "picked" if picked_primary else "other")
            secondary.set("class", "other" if picked_primary else "picked")
        else:
            raise ValueError("unknown additional diagnostic family")
        return html.tostring(tree, encoding="utf-8", doctype="<!doctype html>")


def expanded_cases():
    return cases() + [AdditionalCase(family, old)
        for family in ("unseen_anchor_cofailure", "reversible_parameter_effect") for old in (10, 42, 73)]


def changed_observed_counterexamples(observations):
    rows = {row["name"]: row for row in observations}
    return changed_keys(rows["source"]["url"], rows["target"]["url"]) & observed_dependencies(observations)


def full_evidence_conjunction_baseline(observations):
    """Strong, non-graph baseline using exactly the same facts and preconditions."""
    return observational_guard(observations) and not changed_observed_counterexamples(observations)


def negative_veto_graph(observations):
    rows = {row["name"]: row for row in observations}
    changed = changed_keys(rows["source"]["url"], rows["target"]["url"])
    edges = [(row["intervention"], "stored-selector", "observed_inapplicability")
             for row in observations if row["intervention"] is not None
             and row["anchor_valid"] and not row["nodes_agree"]]
    veto = any(key in changed for key, _, kind in edges if kind == "observed_inapplicability")
    return graph_observational_guard(observations) and not veto


def negative_veto_status(observations):
    if changed_observed_counterexamples(observations):
        return "REANALYZE_HISTORICAL_COUNTEREXAMPLE_NOT_GLOBAL_CAUSAL_PROOF"
    if full_evidence_conjunction_baseline(observations):
        return "OBSERVATIONALLY_COMPATIBLE_NOT_SEMANTICALLY_CERTIFIED"
    return "REANALYZE_OR_UNCERTAIN_ORIGINAL_GUARD"


async def run_veto_study(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    guarded_sources = (*SOURCES, *EXTRA_SOURCES)
    before = source_hashes(guarded_sources)
    base = await run_study(destination / "observations", fixture_cases=expanded_cases(), extra_sources=EXTRA_SOURCES)
    rows = []
    for row in base["cases"]:
        # Neither function receives the source case, expected URLs or score.
        flat = full_evidence_conjunction_baseline(row["observations"])
        graph = negative_veto_graph(row["observations"])
        if flat != graph:
            raise RuntimeError("negative-veto graph/table disagreement")
        rows.append({**row, "decisions": {**row["decisions"],
            "negative_veto_flat": flat, "negative_veto_graph": graph,
            "full_evidence_conjunction_baseline": flat},
            "negative_veto_status": negative_veto_status(row["observations"]),
            "changed_observed_counterexample_keys": sorted(changed_observed_counterexamples(row["observations"]))})
    if before != source_hashes(guarded_sources):
        raise RuntimeError("source or protocol changed during negative-veto study")
    methods = list(rows[0]["decisions"])
    report = {"assessment_date": "2026-10-26", "iteration": "post-r1-negative-veto",
        "source_sha256": before, "source_hashes_unchanged": True,
        "observation_report_sha256": sha256((destination / "observations/report.json").read_bytes()).hexdigest(),
        "scenario_count": len(rows), "mechanism_families": len({row["family"] for row in rows}),
        "numeric_variants_per_family": 3, "cost": base["cost"],
        "current_wrong_transfers_QA_PASS": base["current_wrong_transfers_QA_PASS"],
        "method_counts": {method: counts(rows, method) for method in methods},
        "full_baseline_flat_graph_decisions_equal": all(
            row["decisions"]["negative_veto_flat"] == row["decisions"]["negative_veto_graph"]
            == row["decisions"]["full_evidence_conjunction_baseline"] for row in rows),
        "minimum_observation_pages_per_case": {**base["minimum_observation_pages_per_case"],
            "negative_veto_flat": 6, "negative_veto_graph": 6, "full_evidence_conjunction_baseline": 6},
        "limitations": base["limitations"] + [
            "This iteration was designed after reading r1 results, not a blinded hold-out evaluation.",
            "The conjunction baseline is explicitly identical to the flat reference; gains over incomplete baselines do not establish novelty.",
            "A past counterexample is a conservative veto and may reject a currently correct transfer.",
            "Unobserved joint anchor/selector failure remains possible; observations are not semantic certificates.",
        ], "cases": rows}
    write_new(destination / "report-veto.json", report)
    return report
