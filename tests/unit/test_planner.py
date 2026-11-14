import json
from pathlib import Path

from packages.crawl_core.models import CrawlSpec, Operation, PageModel
from packages.crawl_core.planner import build_crawl_plan

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_list_detail_pagination_plan():
    spec = CrawlSpec.model_validate_json((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    page = PageModel.model_validate_json((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    plan = build_crawl_plan(spec, page)
    assert plan.steps[0].op == Operation.OPEN_URL
    page_loop = plan.steps[1]
    assert page_loop.op == Operation.LOOP_PAGES
    assert page_loop.max_iterations == 10
    assert page_loop.body[0].op == Operation.WAIT
    item_loop = page_loop.body[1]
    assert item_loop.op == Operation.LOOP_ELEMENTS
    assert [step.field for step in item_loop.body if step.op == Operation.EXTRACT] == [
        "title", "publish_time", "url", "author", "content"
    ]
    assert any(step.op == Operation.BACK for step in item_loop.body)
