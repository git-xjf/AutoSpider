from __future__ import annotations

import json
from collections import Counter
from typing import Any

from packages.crawl_core.models import CrawlSpec, ValidationMetrics


def is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def calculate_metrics(spec: CrawlSpec, records: list[dict[str, Any]], semantic_score: float) -> ValidationMetrics:
    required = [field.name for field in spec.fields if field.required]
    if not records:
        return ValidationMetrics(
            required_field_coverage=0,
            required_field_null_rate=1,
            duplicate_rate=0,
            semantic_score=0,
            record_count=0,
        )
    total_required_cells = len(records) * len(required)
    present = sum(1 for row in records for name in required if name in row)
    nulls = sum(1 for row in records for name in required if is_null(row.get(name)))
    canonical = [json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) for row in records]
    duplicate_count = sum(count - 1 for count in Counter(canonical).values() if count > 1)
    return ValidationMetrics(
        required_field_coverage=present / total_required_cells if total_required_cells else 1,
        required_field_null_rate=nulls / total_required_cells if total_required_cells else 0,
        duplicate_rate=duplicate_count / len(records),
        semantic_score=semantic_score,
        record_count=len(records),
    )

