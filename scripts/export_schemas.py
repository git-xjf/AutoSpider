from __future__ import annotations

import json
from pathlib import Path

from packages.crawl_core.models import CrawlPlan, CrawlSpec, PageModel, RepairPatch, ValidationReport


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "schemas"
    root.mkdir(parents=True, exist_ok=True)
    models = {
        "crawl_spec.schema.json": CrawlSpec,
        "page_model.schema.json": PageModel,
        "crawl_plan.schema.json": CrawlPlan,
        "repair_patch.schema.json": RepairPatch,
        "validation_report.schema.json": ValidationReport,
    }
    for filename, model in models.items():
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://autospider.local/schema/{filename}"
        (root / filename).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

