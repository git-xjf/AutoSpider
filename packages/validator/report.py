from __future__ import annotations

from typing import Any

from packages.crawl_core.models import (
    CrawlSpec,
    RepairPatch,
    ValidationIssue,
    ValidationReport,
)

from .metrics import calculate_metrics, is_null
from .semantic import semantic_evaluation
from .coverage import analyze_stratum_coverage


def validate_records(crawl_spec: CrawlSpec | dict, records: list[dict[str, Any]]) -> ValidationReport:
    spec = crawl_spec if isinstance(crawl_spec, CrawlSpec) else CrawlSpec.model_validate(crawl_spec)
    semantic_score, issues = semantic_evaluation(spec, records)
    metrics = calculate_metrics(spec, records, semantic_score)
    required = [field.name for field in spec.fields if field.required]
    coverage_certificate, coverage_issues, evidence_requests = analyze_stratum_coverage(spec, records)
    issues.extend(coverage_issues)

    if not records:
        issues.append(
            ValidationIssue(
                type="NO_RECORDS",
                severity="high",
                evidence={"record_count": 0},
                suspected_root_cause="item container or page-ready selector did not match",
            )
        )
    for name in required:
        null_count = sum(1 for row in records if is_null(row.get(name)))
        if records and null_count / len(records) > 0.2:
            issues.append(
                ValidationIssue(
                    field=name,
                    type="HIGH_NULL_RATE",
                    severity="high",
                    evidence={"null_rate": null_count / len(records)},
                    suspected_root_cause="field selector is missing or has the wrong page scope",
                )
            )
    if metrics.duplicate_rate > 0.2:
        issues.append(
            ValidationIssue(
                type="HIGH_DUPLICATE_RATE",
                severity="high",
                evidence={"duplicate_rate": metrics.duplicate_rate},
                suspected_root_cause="pagination may be stuck on the same page",
            )
        )

    hard_fail = any(issue.severity == "high" for issue in issues)
    passed = (
        not hard_fail
        and metrics.record_count > 0
        and metrics.required_field_coverage >= 0.9
        and metrics.required_field_null_rate <= 0.2
        and metrics.duplicate_rate <= 0.2
        and metrics.semantic_score >= 0.7
    )
    score = (
        metrics.required_field_coverage * 0.3
        + (1 - metrics.required_field_null_rate) * 0.25
        + (1 - metrics.duplicate_rate) * 0.15
        + metrics.semantic_score * 0.3
    )
    patch = None
    if not passed:
        target_issue = next((issue for issue in issues if issue.severity == "high"), issues[0] if issues else None)
        patch = RepairPatch(
            operation="REQUEST_REANALYSIS",
            target=target_issue.field if target_issue and target_issue.field else "page",
            reason=target_issue.suspected_root_cause if target_issue else "validation thresholds were not met",
        )
    return ValidationReport(
        status="PASS" if passed else "FAIL",
        overall_score=max(0, min(1, score)),
        metrics=metrics,
        issues=issues,
        coverage_certificate=coverage_certificate,
        evidence_requests=evidence_requests,
        repair_patch=patch,
    )
