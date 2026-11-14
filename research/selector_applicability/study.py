"""Compare observational applicability checks with a target-page strong baseline.

The generated structural anchor is fallible. A graph stores the same observed
facts as a flat table; neither representation supplies semantic ground truth.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from lxml import etree, html

from packages.crawl_core.reuse import build_reused_crawl
from packages.site_memory.repository import SiteMemoryRepository
from packages.validator.report import validate_records
from research.parameter_binding.probe import (
    GUARDED_SOURCES as PREVIOUS_SOURCES, canonical, execute_fixture, source_workflow,
)
from .fixtures import cases

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = "docs/patent/selector-applicability-2026-10-26/PROTOCOL.md"
SOURCES = (*PREVIOUS_SOURCES, "research/selector_applicability/fixtures.py",
           "research/selector_applicability/study.py", "scripts/probe_selector_applicability.py", PROTOCOL)


def source_hashes(names=SOURCES):
    return {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def write_new(path, value):
    path = Path(path)
    with path.open("xb") as handle:
        handle.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def xpath_literal(value):
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    return "concat(" + ',"\'",'.join("'" + part + "'" for part in value.split("'")) + ")"


def elements(tree, selector):
    try:
        result = tree.xpath(selector)
    except (etree.XPathError, ValueError, TypeError):
        return []
    if not isinstance(result, list) or any(not isinstance(node, etree._Element) for node in result):
        return []
    return result


@dataclass(frozen=True)
class Anchor:
    parent_xpath: str
    children_xpath: str


def derive_anchor(body, selector):
    tree = html.fromstring(body)
    selected = elements(tree, selector)
    if not selected:
        return None
    parent = selected[0].getparent()
    if parent is None or any(node.getparent() is not parent for node in selected):
        return None
    identifier = parent.get("id")
    tag = selected[0].tag
    if not identifier or not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", tag):
        return None
    parent_xpath = "//*[@id=" + xpath_literal(identifier) + "]"
    if len(elements(tree, parent_xpath)) != 1 or any(node.tag != tag for node in selected):
        return None
    anchor = Anchor(parent_xpath, parent_xpath + "/" + tag)
    # A filtered subset must not be enlarged merely because siblings have the
    # same tag. Both locators must designate exactly the source node set.
    if set(selected) != set(elements(tree, anchor.children_xpath)):
        return None
    return anchor


def observe(body, selector, anchor, *, name, url, intervention=None):
    tree = html.fromstring(body)
    selected = elements(tree, selector)
    parents = elements(tree, anchor.parent_xpath) if anchor else []
    reference = elements(tree, anchor.children_xpath) if anchor and len(parents) == 1 else []
    valid = len(parents) == 1 and bool(reference)
    paths = lambda nodes: sorted(tree.getroottree().getpath(node) for node in nodes)
    return {"name": name, "url": url, "intervention": intervention,
            "body_sha256": sha256(body).hexdigest(),
            "selector_nodes": paths(selected), "anchor_nodes": paths(reference),
            "anchor_valid": valid,
            "nodes_agree": bool(selected) and valid and set(selected) == set(reference)}


def changed_keys(source_url, target_url):
    old = parse_qs(urlsplit(source_url).query, keep_blank_values=True)
    new = parse_qs(urlsplit(target_url).query, keep_blank_values=True)
    return {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}


def valid_controls(observations):
    source = next(row for row in observations if row["name"] == "source")
    probes = [row for row in observations if row["intervention"] is not None]
    source_keys = set(parse_qs(urlsplit(source["url"]).query, keep_blank_values=True))
    return (len(probes) == len(source_keys)
            and {row["intervention"] for row in probes} == source_keys
            and all(changed_keys(source["url"], row["url"]) == {row["intervention"]} for row in probes))


def observed_dependencies(observations):
    return {row["intervention"] for row in observations
            if row["intervention"] is not None and row["anchor_valid"] and not row["nodes_agree"]}


def applicability_status(observations):
    rows = {row["name"]: row for row in observations}
    source, repeat, target = (rows[name] for name in ("source", "source_repeat", "target"))
    probed = {row["intervention"] for row in observations if row["intervention"] is not None}
    if not valid_controls(observations) or not changed_keys(source["url"], target["url"]) <= probed:
        return "UNCERTAIN_MISSING_SINGLE_PARAMETER_CONTROLS"
    if (source["url"] != repeat["url"] or not source["nodes_agree"] or not repeat["nodes_agree"]
            or source["selector_nodes"] != repeat["selector_nodes"]):
        return "UNCERTAIN_SOURCE_WITNESS"
    if not all(row["anchor_valid"] for row in observations):
        return "UNCERTAIN_ANCHOR"
    if not target["nodes_agree"]:
        return "REANALYZE_TARGET_NODE_DISAGREEMENT"
    return "OBSERVATIONALLY_COMPATIBLE_NOT_SEMANTICALLY_CERTIFIED"


def observational_guard(observations):
    return applicability_status(observations) == "OBSERVATIONALLY_COMPATIBLE_NOT_SEMANTICALLY_CERTIFIED"


def graph_observational_guard(observations):
    """Encode facts, not inferred causes; graph answer must match the flat one."""
    nodes = {row["name"]: {"valid": row["anchor_valid"], "agree": row["nodes_agree"],
                          "url": row["url"], "paths": row["selector_nodes"]} for row in observations}
    edges = [(row["intervention"], row["name"], "observed_counterexample")
             for row in observations if row["intervention"] is not None and not row["nodes_agree"]]
    edges += [(name, "target", "anchor_observed") for name in nodes]
    # Counterexample edges describe observations, not a global dependency proof.
    probed = {row["intervention"] for row in observations if row["intervention"] is not None}
    return (valid_controls(observations)
            and changed_keys(nodes["source"]["url"], nodes["target"]["url"]) <= probed
            and nodes["source"]["url"] == nodes["source_repeat"]["url"]
            and all(nodes[src]["valid"] for src, _, kind in edges if kind == "anchor_observed")
            and nodes["source"]["agree"] and nodes["source_repeat"]["agree"]
            and nodes["source"]["paths"] == nodes["source_repeat"]["paths"]
            and nodes["target"]["agree"])


def decisions(source_url, target_url, selector, observations):
    keys = changed_keys(source_url, target_url)
    query_equal = urlsplit(source_url).query == urlsplit(target_url).query
    params = parse_qs(urlsplit(source_url).query, keep_blank_values=True)
    overlaps = any(re.search(r"(?<!\d)" + re.escape(value) + r"(?!\d)", selector)
                   for key in keys for value in params.get(key, []) if value)
    controls_ready = valid_controls(observations) and all(row["anchor_valid"] for row in observations)
    target = next(row for row in observations if row["name"] == "target")
    return {"current_reuse": True, "exact_query": query_equal,
            "numeric_overlap": not overlaps,
            "observed_dependency_cache": controls_ready and not bool(keys & observed_dependencies(observations)),
            "all_query_dependency_cache": query_equal,
            "target_dual_locator": target["nodes_agree"],
            "observational_guard_flat": observational_guard(observations),
            "observational_guard_graph": graph_observational_guard(observations)}


def counts(rows, method):
    accepted = [row for row in rows if row["decisions"][method]]
    return {"correct_accepts": sum(row["scoring"]["current_records_correct"] for row in accepted),
            "wrong_accepts": sum(not row["scoring"]["current_records_correct"] for row in accepted),
            "safe_rejected": sum(not row["decisions"][method] and row["scoring"]["current_records_correct"] for row in rows),
            "total_rejected": len(rows) - len(accepted)}


async def run_study(destination, *, fixture_cases=None, extra_sources=()):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    guarded_sources = (*SOURCES, *extra_sources)
    before = source_hashes(guarded_sources)
    # Byte archives are part of the new run, never an overwrite of earlier runs.
    for name in guarded_sources:
        archive = destination / "source" / name
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("xb") as handle:
            handle.write((ROOT / name).read_bytes())
    rows, requests = [], 0
    for case in (cases() if fixture_cases is None else fixture_cases):
        directory = destination / case.name
        directory.mkdir()
        memory = SiteMemoryRepository(directory / "isolated-memory.db")
        samples = list(case.observations())
        spec, page, plan = source_workflow(case.selector, query=urlsplit(case.source_url).query)
        source_spec, source_plan = spec.model_dump(mode="json"), plan.model_dump(mode="json")
        write_new(directory / "source-inputs.json", {"spec": source_spec, "plan": source_plan,
                                                      "page_model": page.model_dump(mode="json", by_alias=True)})
        anchor = derive_anchor(samples[0][2], case.selector)
        observations = []
        reused_input_unchanged = True
        target_records = None
        for name, url, body, intervention in samples:
            with (directory / (name + ".html")).open("xb") as handle:
                handle.write(body)
            if name == "source":
                run_spec, run_plan = spec, plan
            else:
                loaded = memory.get_workflow(workflow_id)
                original = deepcopy((source_spec, loaded["crawl_plan"]))
                run_spec, run_plan = build_reused_crawl(source_spec, loaded["crawl_plan"], start_url=url, item_limit=37)
                reused_input_unchanged &= original == (source_spec, loaded["crawl_plan"])
            records, cost = await execute_fixture(run_spec, run_plan, directory / name, body)
            requests += cost["mock_http_requests"]
            qa = validate_records(run_spec, records)
            write_new(directory / (name + "-qa.json"), qa.model_dump(mode="json"))
            fact = observe(body, case.selector, anchor, name=name, url=url, intervention=intervention)
            fact.update({"qa_status": qa.status, "record_count": len(records), "cost": cost,
                         "mock_request_url": url})
            observations.append(fact)
            if name == "source":
                if qa.status != "PASS":
                    raise RuntimeError("source fixture failed real QA: " + case.name)
                memory.commit_validated(page_model=page.model_dump(mode="json", by_alias=True),
                    crawl_plan=source_plan, crawl_spec_hash=sha256(canonical(source_spec).encode()).hexdigest(),
                    workflow_name=case.name, validation_score=qa.overall_score, easyspider_task_id=1)
                saved = next(row for row in memory.lookup_workflows("binding.example.test", limit=20) if row["name"] == case.name)
                workflow_id = saved["id"]
                if saved["crawl_plan"] != source_plan:
                    raise RuntimeError("memory round trip changed source plan")
            elif name == "target":
                target_records = records
        # Decision functions have no FixtureCase or target labels as input.
        result = decisions(case.source_url, case.target_url, case.selector, observations)
        if result["observational_guard_flat"] != result["observational_guard_graph"]:
            raise RuntimeError("graph/table implementation disagreement")
        actual = {record.get("url") for record in target_records}
        expected = case.expected_urls()  # scoring starts only after decisions
        row = {"case": case.name, "family": case.family, "numeric_variant": case.old,
               "source_url": case.source_url, "target_url": case.target_url, "selector": case.selector,
               "anchor": asdict(anchor) if anchor else None,
               "applicability_status": applicability_status(observations),
               "observed_dependency_keys": sorted(observed_dependencies(observations)),
               "source_inputs_unchanged": reused_input_unchanged,
               "observations": observations, "decisions": result,
               "scoring": {"expected_urls": sorted(expected), "actual_urls": sorted(actual),
                           "current_records_correct": actual == expected}}
        write_new(directory / "case.json", row)
        rows.append(row)
    if before != source_hashes(guarded_sources):
        raise RuntimeError("source or fixed protocol changed during run")
    methods = list(rows[0]["decisions"])
    report = {"assessment_date": "2026-10-26", "source_sha256": before,
        "source_hashes_unchanged": True, "fixture_kind": "authored_not_live_sites",
        "scenario_count": len(rows), "mechanism_families": len({row["family"] for row in rows}),
        "numeric_variants_per_family": 3,
        "cost": {"mock_http_requests": requests, "external_http_requests": 0, "LLM_calls": 0},
        "current_wrong_transfers_QA_PASS": sum(not row["scoring"]["current_records_correct"]
            and row["observations"][-1]["qa_status"] == "PASS" for row in rows),
        "method_counts": {method: counts(rows, method) for method in methods},
        "strong_baseline_decisions_equal": all(row["decisions"]["target_dual_locator"] == row["decisions"]["observational_guard_flat"] for row in rows),
        "graph_table_decisions_equal": all(row["decisions"]["observational_guard_flat"] == row["decisions"]["observational_guard_graph"] for row in rows),
        "all_source_inputs_unchanged": all(row["source_inputs_unchanged"] for row in rows),
        "minimum_observation_pages_per_case": {"current_reuse": 2, "exact_query": 2,
            "numeric_overlap": 2, "observed_dependency_cache": 6, "all_query_dependency_cache": 2,
            "target_dual_locator": 2, "observational_guard_flat": 6, "observational_guard_graph": 6},
        "limitations": [
            f"{len({row['family'] for row in rows})} authored mechanism families, not {len(rows)} independent sites or natural failures.",
            "Six shared actual executor runs per case; admission decisions are evaluated over common evidence, not independent timing trials.",
            "The structural anchor is a hypothesis derived from source nodes, not independent task-semantic truth.",
            "Observed dependency absence is not proof of independence; single-parameter probes miss interactions.",
            "Node comparison is within each parsed document; text equality does not count as node identity.",
            "Graph stores exactly the flat facts and does not establish causal dependencies.",
            "The dual-locator baseline is a limited reference, not a reproduction of published weighted multi-locator algorithms.",
            "No production memory, reuse policy, security configuration or earlier evidence is changed.",
        ], "cases": rows}
    write_new(destination / "report.json", report)
    return report
