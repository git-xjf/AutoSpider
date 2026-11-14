from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Literal
from urllib.parse import parse_qsl, urlparse

from packages.crawl_core.models import (
    CoverageCertificate,
    CrawlSpec,
    EvidenceRequest,
    StratumFieldCoverage,
    ValidationIssue,
    ValidationStratum,
)

from .metrics import is_null


_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
_INTEGER = re.compile(r"^\d+$")
_LONG_IDENTIFIER = re.compile(r"^(?=.{8,}$)(?=.*\d)[0-9a-z_-]+$", re.I)
_NUMBER_IN_FILENAME = re.compile(r"^\d+(\.[a-z0-9]{1,8})$", re.I)


def _normalize_path_segment(segment: str) -> str:
    if _UUID.fullmatch(segment):
        return "{uuid}"
    filename = _NUMBER_IN_FILENAME.fullmatch(segment)
    if filename:
        return "{n}" + filename.group(1).lower()
    if _INTEGER.fullmatch(segment):
        return "{n}"
    if _LONG_IDENTIFIER.fullmatch(segment):
        return "{id}"
    return segment.lower()


def route_signature(value: Any) -> str:
    """Return a content-free route shape for validation stratification.

    Concrete identifiers and query values are deliberately discarded.  The
    The signature is stable across records likely to share a page template.
    Fetch-target URLs in evidence requests retain their original query values.
    """

    if not isinstance(value, str):
        return "__missing_url__"
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "__missing_url__"
    segments = [_normalize_path_segment(item) for item in parsed.path.split("/") if item]
    path = "/" + "/".join(segments) if segments else "/"
    query_keys = sorted({key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    query_shape = "?" + ",".join(query_keys) if query_keys else ""
    return f"{parsed.hostname.lower().rstrip('.')}{path}{query_shape}"


def select_sample_indices(
    total: int,
    sample_size: int,
    strategy: Literal["prefix", "boundary_spread"] = "boundary_spread",
) -> list[int]:
    """Select deterministic zero-based item positions without reading values."""

    if total <= 0 or sample_size <= 0:
        return []
    size = min(total, sample_size)
    if strategy == "prefix" or size == total:
        return list(range(size))
    if strategy != "boundary_spread":
        raise ValueError(f"unsupported sample strategy: {strategy}")
    if size == 1:
        return [0]
    # Integer rounding keeps the selection reproducible and includes both
    # boundaries, which catches the common late-template transition missed by
    # a fixed prefix sample.
    return [((index * (total - 1)) + (size - 1) // 2) // (size - 1) for index in range(size)]


def _url_field(spec: CrawlSpec) -> str | None:
    typed = [field.name for field in spec.fields if field.type == "url" or field.extract.source == "url"]
    if typed:
        return typed[0]
    names = {field.name.lower(): field.name for field in spec.fields}
    for candidate in ("url", "link", "href", "detail_url"):
        if candidate in names:
            return names[candidate]
    return None


def _page_scope(spec: CrawlSpec, field_names: list[str]) -> Literal["list", "detail", "single", "mixed"]:
    scopes = {
        field.page_scope
        for field in spec.fields
        if field.name in field_names and field.page_scope != "auto"
    }
    if not scopes:
        return "detail" if spec.traversal.follow_detail else "single"
    if len(scopes) == 1:
        return next(iter(scopes))
    return "mixed"


def _failed_representative_urls(
    rows: list[dict[str, Any]], url_field: str | None, failed_fields: list[str]
) -> list[str]:
    """Greedily cover failing fields using actual failure rows, at most two URLs."""

    if not url_field:
        return []
    candidates: dict[str, set[str]] = {}
    for row in rows:
        value = row.get(url_field)
        if not isinstance(value, str) or route_signature(value) == "__missing_url__":
            continue
        failures = {name for name in failed_fields if is_null(row.get(name))}
        if failures:
            candidates.setdefault(value, set()).update(failures)
    uncovered = set(failed_fields)
    selected: list[str] = []
    while candidates and uncovered and len(selected) < 2:
        # Stable insertion order resolves ties deterministically.
        url = max(candidates, key=lambda item: len(candidates[item] & uncovered))
        covered = candidates.pop(url) & uncovered
        if not covered:
            break
        selected.append(url)
        uncovered.difference_update(covered)
    return selected


def analyze_stratum_coverage(
    spec: CrawlSpec,
    records: list[dict[str, Any]],
    *,
    required_null_rate_ceiling: float = 0.2,
) -> tuple[CoverageCertificate, list[ValidationIssue], list[EvidenceRequest]]:
    """Audit required fields per route shape and emit bounded repair evidence.

    Global averages can hide a small page-template family.  This audit makes
    every observed route-shape/required-field pair independently accountable.
    It never includes extracted article bodies. Route signatures discard query
    values; representative fetch-target URLs preserve them to remain executable.
    Only supplied records are audited, not unobserved candidates or pages.
    """

    required = [field.name for field in spec.fields if field.required]
    url_field = _url_field(spec)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = route_signature(record.get(url_field)) if url_field else "__all_records__"
        grouped[key].append(record)

    strata: list[ValidationStratum] = []
    issues: list[ValidationIssue] = []
    requests: list[EvidenceRequest] = []
    failed_pairs = 0

    for key in sorted(grouped):
        rows = grouped[key]
        urls: list[str] = []
        if url_field:
            for row in rows:
                value = row.get(url_field)
                if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
                    urls.append(value)
                if len(urls) == 2:
                    break
        field_stats: list[StratumFieldCoverage] = []
        failed_fields: list[str] = []
        for name in required:
            null_count = sum(1 for row in rows if is_null(row.get(name)))
            null_rate = null_count / len(rows)
            field_stats.append(
                StratumFieldCoverage(
                    field=name,
                    observed_count=len(rows),
                    null_count=null_count,
                    null_rate=null_rate,
                )
            )
            if null_rate > required_null_rate_ceiling:
                failed_pairs += 1
                failed_fields.append(name)
                issues.append(
                    ValidationIssue(
                        field=name,
                        type="STRATUM_HIGH_NULL_RATE",
                        severity="high",
                        evidence={
                            "stratum": key,
                            "record_count": len(rows),
                            "null_rate": null_rate,
                            "representative_urls": _failed_representative_urls(rows, url_field, [name]),
                        },
                        suspected_root_cause=(
                            "a minority route/template family is not covered by the field selector"
                        ),
                    )
                )
        strata.append(
            ValidationStratum(
                key=key,
                record_count=len(rows),
                representative_urls=urls,
                fields=field_stats,
            )
        )
        if failed_fields and len(requests) < 8:
            requests.append(
                EvidenceRequest(
                    stratum=key,
                    fields=failed_fields,
                    representative_urls=_failed_representative_urls(rows, url_field, failed_fields),
                    page_scope=_page_scope(spec, failed_fields),
                    max_samples=min(3, max(1, len(failed_fields))),
                    max_chars=min(4000, 800 + 400 * len(failed_fields)),
                    reason=(
                        "Fetch structural excerpts only for these failed required fields and this route shape; "
                        "do not return the full DOM or successful-field content."
                    ),
                )
            )

    pair_count = len(strata) * len(required)
    certificate = CoverageCertificate(
        url_field=url_field,
        required_null_rate_ceiling=required_null_rate_ceiling,
        strata=strata,
        required_stratum_field_pairs=pair_count,
        failed_stratum_field_pairs=failed_pairs,
        complete=bool(records) and failed_pairs == 0,
    )
    return certificate, issues, requests
