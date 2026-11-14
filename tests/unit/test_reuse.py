from copy import deepcopy

import pytest
from pydantic import ValidationError

from packages.crawl_core.models import CrawlPlan, CrawlSpec, Operation
from packages.crawl_core.reuse import build_reused_crawl


OLD_URL = "https://www.cls.cn/depth?id=1118"
NEW_URL = "https://www.cls.cn/depth?id=1111"


@pytest.fixture
def workflow():
    spec = {
        "request_id": "old-request",
        "start_urls": [OLD_URL],
        "objective": "Collect source articles",
        "fields": [
            {
                "name": "title",
                "description": "Title",
                "type": "string",
                "required": True,
            },
            {"name": "url", "description": "URL", "type": "url", "required": True},
            {
                "name": "content",
                "description": "Body",
                "type": "string",
                "required": True,
            },
        ],
        "traversal": {
            "mode": "list_detail",
            "item_limit": 5,
            "max_pages": 3,
            "follow_detail": True,
            "pagination": {"strategy": "click", "max_pages": 3},
        },
        "execution": {"mode": "playwright"},
        "output": {"format": "json", "filename": "source.json"},
    }
    plan = {
        "name": "source-plan",
        "start_urls": [OLD_URL],
        "steps": [
            {"op": "OPEN_URL", "url": OLD_URL},
            {
                "op": "LOOP_PAGES",
                "max_iterations": 3,
                "body": [
                    {"op": "WAIT", "selector": "//article", "seconds": 10},
                    {
                        "op": "LOOP_ELEMENTS",
                        "selector": "//article",
                        "max_iterations": 5,
                        "body": [
                            {
                                "op": "EXTRACT",
                                "field": "title",
                                "selector": ".//h2",
                                "source": "text",
                            },
                            {
                                "op": "EXTRACT",
                                "field": "url",
                                "selector": ".//a",
                                "source": "attribute",
                                "attribute": "href",
                            },
                            {"op": "CLICK", "selector": ".//a"},
                            {"op": "WAIT", "selector": "//main", "seconds": 10},
                            {
                                "op": "EXTRACT",
                                "field": "content",
                                "selector": "//main",
                                "source": "text",
                            },
                            {"op": "BACK"},
                        ],
                    },
                    {"op": "CLICK", "selector": "//button[@class='next']"},
                ],
            },
        ],
    }
    return spec, plan


def all_steps(steps):
    for step in steps:
        yield step
        yield from all_steps(step.body)


def test_reuse_changes_only_requested_parameters_and_is_independent(workflow):
    source_spec, source_plan = workflow
    original = deepcopy(workflow)

    spec, plan = build_reused_crawl(
        source_spec,
        source_plan,
        start_url=NEW_URL,
        item_limit=10,
        max_pages=2,
        output_filename="channel-1111.json",
        field_names=["title", "url"],
    )

    assert isinstance(spec, CrawlSpec)
    assert isinstance(plan, CrawlPlan)
    assert spec.start_urls == plan.start_urls == [NEW_URL]
    assert spec.request_id != source_spec["request_id"]
    assert plan.name != source_plan["name"]
    assert NEW_URL in spec.objective and "10" in spec.objective
    assert spec.traversal.item_limit == 10
    assert spec.traversal.max_pages == spec.traversal.pagination.max_pages == 2
    assert spec.output.filename == "channel-1111.json"
    assert [field.name for field in spec.fields] == ["title", "url"]
    assert {
        step.field for step in all_steps(plan.steps) if step.op == Operation.EXTRACT
    } == {"title", "url"}
    assert [
        step.max_iterations
        for step in all_steps(plan.steps)
        if step.op == Operation.LOOP_ELEMENTS
    ] == [10]
    assert [
        step.max_iterations
        for step in all_steps(plan.steps)
        if step.op == Operation.LOOP_PAGES
    ] == [2]
    assert plan.steps[0].url == NEW_URL
    assert [step.op for step in plan.steps[1].body[1].body] == [
        Operation.EXTRACT,
        Operation.EXTRACT,
        Operation.CLICK,
        Operation.WAIT,
        Operation.BACK,
    ]
    spec.fields[0].description = "Changed result"
    plan.steps[1].body[1].body[0].selector = "//changed"
    assert workflow == original


def test_reuse_preserves_placeholder_and_existing_page_limit(workflow):
    source_spec, source_plan = workflow
    source_plan["steps"][0]["url"] = "{{start_url}}"
    source_spec["output"]["format"] = "csv"

    spec, plan = build_reused_crawl(
        source_spec, source_plan, start_url=NEW_URL, item_limit=1
    )

    assert plan.steps[0].url == "{{start_url}}"
    assert plan.steps[1].max_iterations == 3
    assert spec.output.filename == f"{spec.request_id}.csv"
    second_spec, second_plan = build_reused_crawl(
        source_spec, source_plan, start_url=NEW_URL, item_limit=1000
    )
    assert second_spec.request_id != spec.request_id
    assert second_plan.name != plan.name


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/depth?id=1111",
        "http://www.cls.cn/depth?id=1111",
        "https://www.cls.cn/other?id=1111",
        "https://www.cls.cn:8443/depth?id=1111",
        "https://user:password@www.cls.cn/depth?id=1111",
        "javascript:alert(1)",
    ],
)
def test_reuse_rejects_changed_route_or_credentials(workflow, url):
    with pytest.raises(ValueError):
        build_reused_crawl(*workflow, start_url=url, item_limit=10)


@pytest.mark.parametrize("item_limit", [0, -1, 1001, True, 1.5, "10"])
def test_reuse_rejects_invalid_item_limits(workflow, item_limit):
    with pytest.raises(ValueError, match="item_limit"):
        build_reused_crawl(*workflow, start_url=NEW_URL, item_limit=item_limit)


@pytest.mark.parametrize("max_pages", [0, -1, True, 1.5, "2"])
def test_reuse_rejects_invalid_page_limits(workflow, max_pages):
    with pytest.raises(ValueError, match="max_pages"):
        build_reused_crawl(
            *workflow, start_url=NEW_URL, item_limit=10, max_pages=max_pages
        )


@pytest.mark.parametrize("field_names", [[], ["missing"], ["title", "title"]])
def test_reuse_rejects_unknown_empty_or_duplicate_fields(workflow, field_names):
    with pytest.raises(ValueError, match="field"):
        build_reused_crawl(
            *workflow, start_url=NEW_URL, item_limit=10, field_names=field_names
        )


def test_reuse_rejects_field_without_existing_extraction(workflow):
    spec, plan = workflow
    plan["steps"][1]["body"][1]["body"].pop(4)
    with pytest.raises(ValueError, match="content"):
        build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)


@pytest.mark.parametrize("source", ["spec", "plan"])
def test_reuse_rejects_multiple_or_mismatched_start_urls(workflow, source):
    spec, plan = workflow
    (spec if source == "spec" else plan)["start_urls"].append(NEW_URL)
    with pytest.raises(ValueError, match="start"):
        build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)


def test_reuse_rejects_mismatched_spec_plan(workflow):
    spec, plan = workflow
    plan["start_urls"] = [NEW_URL]
    with pytest.raises(ValueError, match="start"):
        build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)


def test_source_models_are_strictly_validated(workflow):
    spec, plan = workflow
    spec.pop("objective")
    with pytest.raises(ValidationError, match="objective"):
        build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)


@pytest.mark.parametrize(
    "http_request",
    [
        {"url": "https://www.cls.cn/api/depth?id=1118"},
        {"params": {"id": 1118}},
    ],
)
def test_query_change_rejects_fixed_http_request_in_any_mode(workflow, http_request):
    spec, plan = workflow
    plan["http_request"] = http_request
    with pytest.raises(ValueError, match="reanalysis"):
        build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)
    unchanged, _ = build_reused_crawl(spec, plan, start_url=OLD_URL, item_limit=10)
    assert unchanged.start_urls == [OLD_URL]


def test_api_reuse_requires_identical_start_url(workflow):
    spec, plan = workflow
    spec["execution"]["mode"] = plan["execution_mode"] = "http_api"
    plan["http_request"] = {"response_format": "json"}
    plan["steps"] = [
        {"op": "OPEN_URL", "url": "{{start_url}}"},
        {
            "op": "LOOP_ELEMENTS",
            "selector": "$.items",
            "selector_type": "jsonpath",
            "body": [
                {
                    "op": "EXTRACT",
                    "field": field["name"],
                    "selector": f"$.{field['name']}",
                    "selector_type": "jsonpath",
                    "source": "text",
                }
                for field in spec["fields"]
            ],
        },
    ]
    with pytest.raises(ValueError, match="reanalysis"):
        build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)
    _, unchanged = build_reused_crawl(spec, plan, start_url=OLD_URL, item_limit=10)
    assert unchanged.execution_mode == "http_api"


def test_page_limit_falls_back_to_existing_plan(workflow):
    spec, plan = workflow
    spec["traversal"].pop("max_pages")
    spec["traversal"]["pagination"].pop("max_pages")
    result, reused = build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)
    assert reused.steps[1].max_iterations == 3
    assert result.traversal.max_pages == result.traversal.pagination.max_pages == 3


def test_query_change_rejects_fixed_pagination_url(workflow):
    spec, plan = workflow
    plan["steps"][1]["body"].insert(
        0,
        {
            "op": "OPEN_URL",
            "url": f"{OLD_URL}&page={{{{page}}}}",
        },
    )
    with pytest.raises(ValueError, match="fixed navigation URL.*reanalysis"):
        build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)
    _, unchanged = build_reused_crawl(spec, plan, start_url=OLD_URL, item_limit=10)
    assert unchanged.steps[1].body[0].url == f"{OLD_URL}&page={{{{page}}}}"


def test_unbounded_spec_preserves_individual_page_loop_limits(workflow):
    spec, plan = workflow
    spec["traversal"].pop("max_pages")
    spec["traversal"]["pagination"].pop("max_pages")
    second_loop = deepcopy(plan["steps"][1])
    second_loop["max_iterations"] = 5
    plan["steps"].append(second_loop)
    _, reused = build_reused_crawl(spec, plan, start_url=NEW_URL, item_limit=10)
    assert [
        step.max_iterations for step in reused.steps if step.op == Operation.LOOP_PAGES
    ] == [3, 5]


def test_unsafe_output_filename_is_rejected(workflow):
    with pytest.raises(ValueError, match="filename"):
        build_reused_crawl(
            *workflow,
            start_url=NEW_URL,
            item_limit=10,
            output_filename="../result.json",
        )
