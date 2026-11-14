import json
from pathlib import Path

from packages.crawl_core.models import CrawlSpec, PageModel
from packages.crawl_core.planner import build_crawl_plan
from packages.validator import apply_repair_patch

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_repair_replaces_only_target_selector():
    spec = CrawlSpec.model_validate_json((FIXTURES / "crawl_spec.json").read_text(encoding="utf-8"))
    page = PageModel.model_validate_json((FIXTURES / "page_model.json").read_text(encoding="utf-8"))
    plan = build_crawl_plan(spec, page)
    updated = apply_repair_patch(plan, {
        "operation": "REPLACE_SELECTOR",
        "target": "fields.author.selector",
        "old_value": "//span[contains(@class,'article-author')]",
        "new_value": "//a[@rel='author']"
    })
    before = plan.model_dump(mode="json")
    after = updated.model_dump(mode="json")
    assert before != after
    assert "//a[@rel='author']" in json.dumps(after)
    assert "//article[contains(@class,'content')]" in json.dumps(after)

