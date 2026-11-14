import json
from pathlib import Path

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.models import CrawlSpec, Operation, PageModel
from packages.crawl_core.planner import build_crawl_plan


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_native_compiler_is_deterministic_and_bounds_sample():
    spec = CrawlSpec.model_validate_json((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    page = PageModel.model_validate_json((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    plan = build_crawl_plan(spec, page)
    compiler = PlaywrightCompiler()

    first = compiler.compile(spec, plan, sample_size=2)
    second = compiler.compile(spec, plan, sample_size=2)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["engine"] == "playwright"
    page_loop = first["crawl_plan"]["steps"][1]
    assert page_loop["op"] == Operation.LOOP_PAGES
    assert page_loop["max_iterations"] == 1
    # A boundary-spread sample emits two records but preserves the size of the
    # first-page candidate set so the executor can include the last item.
    assert page_loop["body"][1]["max_iterations"] == 100
    assert first["crawl_spec"]["traversal"]["item_limit"] == 2
    assert spec.traversal.item_limit != 2  # compilation does not mutate inputs


def test_prefix_sample_strategy_retains_legacy_first_n_plan_bound():
    spec_data = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    spec_data["execution"]["sample_strategy"] = "prefix"
    spec = CrawlSpec.model_validate(spec_data)
    page = PageModel.model_validate_json((FIXTURES / "page_model.json").read_text(encoding="utf-8"))

    task = PlaywrightCompiler().compile(spec, build_crawl_plan(spec, page), sample_size=2)

    assert task["crawl_plan"]["steps"][1]["body"][1]["max_iterations"] == 2


def test_compiler_selects_static_http_engine_from_page_model():
    spec_data = json.loads((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    spec_data["execution"]["mode"] = "auto"
    spec = CrawlSpec.model_validate(spec_data)
    page_data = json.loads((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    page_data["recommended_mode"] = "http_html"
    page_data["pagination"] = {"type": "none"}
    page = PageModel.model_validate(page_data)

    plan = build_crawl_plan(spec, page)
    task = PlaywrightCompiler().compile(spec, plan)

    assert plan.execution_mode == "http_html"
    assert task["engine"] == "http_html"


def test_compiler_builds_paginated_json_api_plan():
    spec = CrawlSpec.model_validate(
        {
            "request_id": "api-news",
            "start_urls": ["https://example.com/news"],
            "objective": "collect API news",
            "fields": [
                {"name": "title", "description": "Title", "type": "string", "required": True},
                {"name": "url", "description": "URL", "type": "url", "required": True},
            ],
            "traversal": {"mode": "list", "item_limit": 20, "max_pages": 3},
            "execution": {"mode": "http_api"},
            "policy": {"allowed_domains": ["example.com"]},
            "output": {"format": "json"},
        }
    )
    page = PageModel.model_validate(
        {
            "url": "https://example.com/news",
            "page_type": "PAGINATED_LIST",
            "confidence": 1,
            "recommended_mode": "http_api",
            "http_request": {
                "method": "GET",
                "url": "https://example.com/api/news",
                "response_format": "json",
                "pagination": {"type": "page", "page_param": "page", "start_page": 1},
            },
            "list": {"item_selector": {"type": "jsonpath", "value": "$.data.items[*]"}},
            "fields": {
                "title": {"page_scope": "list", "selector": "title", "selector_type": "jsonpath", "confidence": 1},
                "url": {"page_scope": "list", "selector": "url", "selector_type": "jsonpath", "confidence": 1},
            },
        }
    )

    plan = build_crawl_plan(spec, page)
    task = PlaywrightCompiler().compile(spec, plan, sample_size=5)

    assert task["engine"] == "http_api"
    assert task["crawl_plan"]["http_request"]["pagination"]["page_param"] == "page"
    assert task["crawl_plan"]["steps"][0]["op"] == Operation.LOOP_PAGES
    assert task["crawl_plan"]["steps"][0]["max_iterations"] == 1
