from __future__ import annotations

import json
from pathlib import Path

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.models import CrawlSpec, PageModel
from packages.crawl_core.planner import build_crawl_plan
from packages.validator import validate_records


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = CrawlSpec.model_validate(load(root / "tests/fixtures/crawl_spec.json"))
    page = PageModel.model_validate(load(root / "tests/fixtures/page_model.json"))
    plan = build_crawl_plan(spec, page)
    task = PlaywrightCompiler().compile(spec, plan, sample_size=3)
    report = validate_records(spec, load(root / "tests/fixtures/expected_records.json"))
    assert task["version"] == "1.0"
    assert task["engine"] == "playwright"
    assert report.status == "PASS"
    print(f"smoke test passed: native hybrid task; QA score={report.overall_score:.3f}")


if __name__ == "__main__":
    main()
