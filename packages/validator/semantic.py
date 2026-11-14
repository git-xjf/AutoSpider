from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from packages.crawl_core.models import CrawlSpec, ValidationIssue

from .metrics import is_null


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value.strip()) < 6:
        return False
    normalized = value.strip().replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
        return True
    except ValueError:
        return any(char.isdigit() for char in normalized) and any(mark in normalized for mark in ("-", "/", "年"))


def semantic_evaluation(spec: CrawlSpec, records: list[dict[str, Any]]) -> tuple[float, list[ValidationIssue]]:
    checks = 0
    passed = 0
    issues: list[ValidationIssue] = []
    for field in spec.fields:
        values = [row.get(field.name) for row in records if not is_null(row.get(field.name))]
        if not values:
            continue
        validator = None
        if field.type == "url" or field.extract.source == "url":
            validator = _valid_url
        elif field.type in {"date", "datetime"}:
            validator = _valid_date
        if validator:
            checks += len(values)
            valid = sum(1 for value in values if validator(value))
            passed += valid
            if valid / len(values) < 0.8:
                issues.append(
                    ValidationIssue(
                        field=field.name,
                        type="SEMANTIC_MISMATCH",
                        severity="high",
                        evidence={"valid_ratio": valid / len(values), "sample_values": values[:5]},
                        suspected_root_cause="selector or extraction mode does not match the requested field type",
                    )
                )
        elif len(values) >= 3:
            checks += 1
            unique_ratio = len({str(value).strip() for value in values}) / len(values)
            if unique_ratio <= 0.2:
                issues.append(
                    ValidationIssue(
                        field=field.name,
                        type="SUSPICIOUS_CONSTANT",
                        severity="high" if field.required else "medium",
                        evidence={"unique_ratio": unique_ratio, "sample_values": values[:5]},
                        suspected_root_cause="selector likely matches a shared header, label or navigation element",
                    )
                )
            else:
                passed += 1
    return (passed / checks if checks else 1.0), issues

