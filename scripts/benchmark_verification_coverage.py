from __future__ import annotations

import json
from typing import Any

from packages.crawl_core.models import CrawlSpec
from packages.validator import select_sample_indices, validate_records
from packages.validator.metrics import calculate_metrics, is_null
from packages.validator.semantic import semantic_evaluation


def _spec() -> CrawlSpec:
    return CrawlSpec.model_validate(
        {
            "request_id": "patent-benchmark",
            "start_urls": ["https://example.com/news"],
            "objective": "measure minority-template false passes",
            "fields": [
                {"name": "url", "description": "URL", "type": "url", "required": True, "page_scope": "list"},
                {"name": "title", "description": "Title", "type": "string", "required": True, "page_scope": "detail"},
                {"name": "content", "description": "Body", "type": "string", "required": True, "page_scope": "detail"},
            ],
            "traversal": {
                "mode": "list_detail",
                "item_limit": 10,
                "follow_detail": True,
                "pagination": {"strategy": "none"},
            },
            "execution": {"request_delay_ms": 0},
            "policy": {"allowed_domains": ["example.com"]},
            "output": {"format": "json"},
        }
    )


def _records(failing: set[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(10):
        special = index in failing
        route = "special" if special else "news"
        rows.append(
            {
                "url": f"https://example.com/{route}/{1000 + index}",
                "title": f"Item {index}",
                "content": "" if special else f"Body {index}",
            }
        )
    return rows


def _legacy_global_pass(rows: list[dict[str, Any]]) -> bool:
    spec = _spec()
    score, issues = semantic_evaluation(spec, rows)
    metrics = calculate_metrics(spec, rows, score)
    return (
        bool(rows)
        and not any(issue.severity == "high" for issue in issues)
        and all(sum(is_null(row.get(field.name)) for row in rows) / len(rows) <= 0.2
                for field in spec.fields if field.required)
        and metrics.required_field_coverage >= 0.9
        and metrics.required_field_null_rate <= 0.2
        and metrics.duplicate_rate <= 0.2
        and metrics.semantic_score >= 0.7
    )


def _case(name: str, failing: set[int]) -> dict[str, Any]:
    rows = _records(failing)
    prefix_indices = select_sample_indices(len(rows), 5, "prefix")
    spread_indices = select_sample_indices(len(rows), 5, "boundary_spread")
    prefix = [rows[index] for index in prefix_indices]
    spread = [rows[index] for index in spread_indices]
    prefix_report = validate_records(_spec(), prefix)
    spread_report = validate_records(_spec(), spread)
    full_report = validate_records(_spec(), rows)
    return {
        "case": name,
        "failing_indices": sorted(failing),
        "prefix_indices": prefix_indices,
        "boundary_spread_indices": spread_indices,
        "legacy_global": {
            "prefix_status": "PASS" if _legacy_global_pass(prefix) else "FAIL",
            "boundary_spread_status": "PASS" if _legacy_global_pass(spread) else "FAIL",
            "full_status": "PASS" if _legacy_global_pass(rows) else "FAIL",
        },
        "coverage_certificate": {
            "prefix_status": prefix_report.status,
            "boundary_spread_status": spread_report.status,
            "full_status": full_report.status,
            "full_failed_pairs": full_report.coverage_certificate.failed_stratum_field_pairs,
            "evidence_requests": len(full_report.evidence_requests),
        },
    }


def main() -> None:
    windows = [set(range(start, start + 2)) for start in range(9)]
    prefix = set(select_sample_indices(10, 5, "prefix"))
    spread = set(select_sample_indices(10, 5, "boundary_spread"))
    result = {
        "experiment": "minority-template validation coverage",
        "records": 10,
        "sample_size": 5,
        "contiguous_two_record_windows": {
            "total": len(windows),
            "prefix_detected": sum(bool(window & prefix) for window in windows),
            "boundary_spread_detected": sum(bool(window & spread) for window in windows),
        },
        "cases": [
            _case("tail_template_transition", {8, 9}),
            _case("middle_template_island", {4, 5}),
            _case("single_unsampled_outlier", {8}),
        ],
        "scope_note": (
            "Synthetic deterministic regression for the concrete false-PASS mechanism; "
            "it is not a real-site accuracy or patentability claim."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
