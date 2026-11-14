"""Test parameter-binding hypotheses against actual AutoSpider components.

All pages are authored diagnostic fixtures and HTTP is MockTransport-only.
Original numeric aliasing and stale task-bound selectors are separate findings.
No expected selector binding or scoring labels enter either reuse guard.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import socket
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.drafts import build_crawl_draft
from packages.crawl_core.models import CrawlPlan, CrawlSpec, HttpRequestModel, Operation, PlanStep
from packages.crawl_core.reuse import build_reused_crawl
from packages.executor.http import HttpExecutor
from packages.executor.repository import TaskRepository
from packages.site_memory.repository import SiteMemoryRepository
from packages.validator.report import validate_records

ROOT = Path(__file__).resolve().parents[2]
GUARDED_SOURCES = (
    "packages/crawl_core/reuse.py", "packages/crawl_core/models.py",
    "packages/crawl_core/drafts.py", "packages/crawl_core/planner.py",
    "packages/compiler/playwright.py", "packages/executor/http.py",
    "packages/site_memory/repository.py", "packages/validator/report.py",
    "packages/validator/semantic.py", "services/easyspider_mcp/server.py",
    "research/parameter_binding/probe.py",
)


def digest(data):
    return sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_hashes():
    return {name: digest((ROOT / name).read_bytes()) for name in GUARDED_SOURCES}


def all_steps(steps):
    for step in steps:
        yield step
        yield from all_steps(step.body)


def fixture_markup(channel):
    """Main and recommendation exchange roles, not the article text values."""
    other = "37" if channel == "10" else "10"
    def section(value, role):
        articles = "".join(
            f'<article><h2>Diagnostic article {value}-{index}</h2>'
            f'<a href="/article/{value}-{index}">Read</a>'
            f'<p>Unique diagnostic body for channel {value}, record {index}.</p></article>'
            for index in range(1, 4))
        return f'<section id="{role}" data-channel="{value}" class="channel_{value}">{articles}</section>'
    return ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
            + section(channel, "main") + section(other, "recommendations") + "</body></html>").encode()


def source_workflow(selector="//section[@id='main']/article", *, query="channel=10&page_size=10"):
    url = "https://binding.example.test/list?" + query
    spec, page, plan = build_crawl_draft(
        url=url, mode="http_html", item_limit=10, page_limit=1,
        item_selector=selector,
        field_selectors={"title": ".//h2", "content": ".//p", "url": ".//a"},
        output_filename="source-10.json")
    spec.execution.request_delay_ms = 0
    plan.steps.insert(1, PlanStep(op=Operation.WAIT, selector="//body", seconds=10))
    plan = CrawlPlan.model_validate(plan.model_dump(mode="json"))
    return spec, page, plan


def alias_checks():
    checks = []
    for mode in ("http_html", "playwright"):
        for old_value in (1, 10, 37):
            spec, _, plan = source_workflow(query=f"channel={old_value}&page_size={old_value}")
            spec.execution.mode = plan.execution_mode = mode
            for step in all_steps(plan.steps):
                if step.op == Operation.WAIT:
                    step.seconds = old_value
            original = (spec.model_dump(mode="json"), plan.model_dump(mode="json"))
            reused_spec, reused_plan = build_reused_crawl(*original,
                start_url=f"https://binding.example.test/list?channel=91&page_size={old_value}", item_limit=91)
            waiting = [step.seconds for step in all_steps(reused_plan.steps) if step.op == Operation.WAIT]
            checks.append({"case": f"same_value_{mode}_{old_value}",
                "requested_item_limit": 91,
                "unrelated_wait_values_preserved": waiting == [old_value, old_value],
                "query_page_size_preserved": urlsplit(reused_spec.start_urls[0]).query.endswith(f"page_size={old_value}"),
                "source_inputs_unchanged": original == (spec.model_dump(mode="json"), plan.model_dump(mode="json")),
                "item_limit_updated": reused_spec.traversal.item_limit == 91})
    # Fixed HTTP parameters are retained, not generalized from matching values.
    spec, _, plan = source_workflow()
    plan.http_request = HttpRequestModel(params={"channel": 10, "page_size": 10}, timeout_seconds=10)
    plan = CrawlPlan.model_validate(plan.model_dump(mode="json"))
    _, reused = build_reused_crawl(spec.model_dump(mode="json"), plan.model_dump(mode="json"),
                                   start_url=spec.start_urls[0], item_limit=37)
    checks.append({"case": "fixed_request_same_url", "fixed_params_preserved": reused.http_request.params == {"channel": 10, "page_size": 10},
                   "timeout_preserved": reused.http_request.timeout_seconds == 10})
    try:
        build_reused_crawl(spec.model_dump(mode="json"), plan.model_dump(mode="json"),
                           start_url="https://binding.example.test/list?channel=37&page_size=10", item_limit=37)
    except ValueError as error:
        checks.append({"case": "fixed_request_changed_query", "reanalysis_required": "reanalysis" in str(error)})
    else:
        checks.append({"case": "fixed_request_changed_query", "reanalysis_required": False})
    return checks


@dataclass(frozen=True)
class ReuseDecision:
    allowed: bool
    reason: str


def exact_query_guard(source_spec, new_url):
    """Conservative known baseline; not an invention or a complete safety proof."""
    old_url = source_spec["start_urls"][0]
    same = urlsplit(old_url).query == urlsplit(new_url).query
    return ReuseDecision(same, "same_query" if same else "query_change_requires_reanalysis")


def graph_query_guard(source_spec, new_url):
    """Equivalent graph representation, deliberately no extra inferred knowledge.

    The unresolved edge means applicability after a query change is not proven.
    It does NOT mean an actual dependency has been causally established.
    """
    nodes = {"query": urlsplit(source_spec["start_urls"][0]).query,
             "new_query": urlsplit(new_url).query, "workflow": "stored-plan"}
    edges = [("query", "workflow", "unresolved_applicability")]
    unresolved_change = any(kind == "unresolved_applicability" and nodes[source] != nodes["new_query"]
                            for source, _, kind in edges)
    return ReuseDecision(not unresolved_change, "query_change_requires_reanalysis" if unresolved_change else "same_query")


class OfflineFixturePolicy:
    """Only the MockTransport fixture URL is permitted, never real DNS/HTTP."""
    def check(self, spec):
        for url in spec.start_urls:
            self.check_url(url)
    def check_url(self, url, **_):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "binding.example.test" or parts.path != "/list":
            raise AssertionError("fixture-only transport scope violation")
    def robots_allowed(self, url, **_):
        self.check_url(url)
        return True  # simulated permission; no assertion about a real site's robots


async def execute_fixture(spec, plan, directory, body):
    repository = TaskRepository(directory)
    task_id = repository.register_task(PlaywrightCompiler().compile(spec, plan))
    execution_id = repository.create_execution(task_id, {})
    calls = []
    def response(request):
        if request.method != "GET" or str(request.url) != spec.start_urls[0]:
            raise AssertionError("unrecorded fixture request")
        calls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)
    client_type = httpx.AsyncClient
    def isolated_client(**kwargs):
        return client_type(**kwargs, transport=httpx.MockTransport(response))
    executor = HttpExecutor(repository, OfflineFixturePolicy())
    with patch("packages.executor.http.httpx.AsyncClient", isolated_client), \
            patch.object(socket, "getaddrinfo", side_effect=AssertionError("live DNS prohibited")), \
            patch.object(socket, "create_connection", side_effect=AssertionError("live TCP prohibited")):
        await executor._run(execution_id)
    execution = repository.get_execution(execution_id)
    if execution["status"] != "completed":
        raise RuntimeError("diagnostic execution failed: " + str(execution.get("error")))
    result_path = repository.run_dir(execution_id) / "result.json"
    records = json.loads(result_path.read_text(encoding="utf-8"))
    return records, {"mock_http_requests": len(calls), "external_http_requests": 0,
                     "result_sha256": digest(result_path.read_bytes()), "execution_status": execution["status"]}


def cases():
    return [
        ("same_query_generic", "//section[@id='main']/article", "channel=10&page_size=10", "10"),
        ("changed_channel_generic", "//section[@id='main']/article", "channel=37&page_size=10", "37"),
        ("changed_channel_literal_attribute", "//section[@data-channel='10']/article", "channel=37&page_size=10", "37"),
        ("changed_channel_literal_class", "//section[@class='channel_10']/article", "channel=37&page_size=10", "37"),
        ("changed_channel_literal_union", "//section[@id='missing']/article | //section[@data-channel='10']/article", "channel=37&page_size=10", "37"),
        ("unrelated_query_change", "//section[@id='main']/article", "channel=10&page_size=10&tracking=changed", "10"),
    ]


async def run_probe(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    memory = SiteMemoryRepository(destination / "isolated-memory.db")
    rows = []
    mock_requests = 0
    for name, selector, query, target in cases():
        spec, page, plan = source_workflow(selector)
        source_body = fixture_markup("10")
        records, cost = await execute_fixture(spec, plan, destination / name / "source", source_body)
        source_validation = validate_records(spec, records)
        mock_requests += cost["mock_http_requests"]
        if source_validation.status != "PASS":
            raise RuntimeError("source fixture did not meet actual production validator: " + name)
        source_spec, source_plan = spec.model_dump(mode="json"), plan.model_dump(mode="json")
        memory.commit_validated(page_model=page.model_dump(mode="json", by_alias=True), crawl_plan=source_plan,
            crawl_spec_hash=digest(canonical(source_spec).encode()), workflow_name=name,
            validation_score=source_validation.overall_score, easyspider_task_id=1)
        workflow = next(row for row in memory.lookup_workflows("binding.example.test", limit=20) if row["name"] == name)
        round_trip_equal = workflow["crawl_plan"] == source_plan
        new_url = "https://binding.example.test/list?" + query
        original = deepcopy((source_spec, workflow["crawl_plan"]))
        reused_spec, reused_plan = build_reused_crawl(source_spec, workflow["crawl_plan"], start_url=new_url, item_limit=37)
        new_records, new_cost = await execute_fixture(reused_spec, reused_plan, destination / name / "reused", fixture_markup(target))
        mock_requests += new_cost["mock_http_requests"]
        validation = validate_records(reused_spec, new_records)
        expected_urls = {f"https://binding.example.test/article/{target}-{index}" for index in range(1, 4)}
        correct = {record.get("url") for record in new_records} == expected_urls
        table = exact_query_guard(source_spec, new_url)
        graph = graph_query_guard(source_spec, new_url)
        rows.append({"case": name, "fixture_kind": "authored_diagnostic_not_live_site",
                     "stored_plan_round_trip_equal": round_trip_equal,
                     "source_validation": source_validation.status, "reused_validation": validation.status,
                     "current_reuse_allowed": True, "records_match_independent_fixture_target": correct,
                     "record_count": len(new_records), "source_inputs_unchanged": original == (source_spec, workflow["crawl_plan"]),
                     "source_fixture_sha256": digest(source_body), "reused_fixture_sha256": digest(fixture_markup(target)),
                     "table_guard_allowed": table.allowed, "graph_guard_allowed": graph.allowed,
                     "table_graph_decisions_equal": table == graph, "source_cost": cost, "reused_cost": new_cost})
    if before != source_hashes():
        raise RuntimeError("production/research source changed during probe")
    aliases = alias_checks()
    alias_flags = [value for row in aliases for key, value in row.items() if isinstance(value, bool)]
    summary = {"numeric_alias_cases": len(aliases), "numeric_alias_checks_all_pass": all(alias_flags),
               "diagnostic_transfer_cases": len(rows), "current_wrong_transfers": sum(not row["records_match_independent_fixture_target"] for row in rows),
               "current_wrong_transfers_QA_PASS": sum(not row["records_match_independent_fixture_target"] and row["reused_validation"] == "PASS" for row in rows),
               "table_graph_all_decisions_equal": all(row["table_graph_decisions_equal"] for row in rows),
               "table_guard_safe_accepted": sum(row["table_guard_allowed"] and row["records_match_independent_fixture_target"] for row in rows),
               "table_guard_wrong_accepted": sum(row["table_guard_allowed"] and not row["records_match_independent_fixture_target"] for row in rows),
               "table_guard_safe_rejected": sum(not row["table_guard_allowed"] and row["records_match_independent_fixture_target"] for row in rows),
               "table_guard_reanalysis": sum(not row["table_guard_allowed"] for row in rows),
               "mock_http_requests": mock_requests, "external_http_requests": 0, "LLM_calls": 0}
    return {"assessment_date": "2026-10-26", "source_sha256": before,
            "source_hash_verified_unchanged_during_run": True, "summary": summary,
            "numeric_alias_checks": aliases, "transfers": rows,
            "limitations": ["These are authored fixtures executed with actual production components, not observed live-site incidents.",
                "The originally proposed value-equality generalizer is not implemented by current production reuse.",
                "A stale selector applicability failure is different from wrong replacement of equal numeric values.",
                "Ground-truth URLs are read only by the scorer, never supplied to either guard.",
                "The exact-query guard is a conservative reference, not a proven safe general-purpose reuse policy.",
                "The graph guard encodes the same predicate and has no independent information or novelty.",
                "Guard results are counterfactual admission decisions over the same executed current-reuse results, not independent speed benchmarks.",
                "The user's production database, live runtime and production source are not modified."]}
