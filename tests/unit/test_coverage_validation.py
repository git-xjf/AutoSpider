from packages.crawl_core.models import CrawlSpec
from packages.validator import route_signature, select_sample_indices, validate_records


def _spec() -> CrawlSpec:
    return CrawlSpec.model_validate(
        {
            "request_id": "coverage",
            "start_urls": ["https://example.com/news"],
            "objective": "collect heterogeneous details",
            "fields": [
                {
                    "name": "url",
                    "description": "Detail URL",
                    "type": "url",
                    "required": True,
                    "page_scope": "list",
                },
                {
                    "name": "title",
                    "description": "Title",
                    "type": "string",
                    "required": True,
                    "page_scope": "detail",
                },
                {
                    "name": "content",
                    "description": "Body",
                    "type": "string",
                    "required": True,
                    "page_scope": "detail",
                },
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


def test_route_signature_removes_ids_and_query_values():
    assert (
        route_signature("https://EXAMPLE.com/archive/2026/12345.html?lang=zh&id=secret")
        == "example.com/archive/{n}/{n}.html?id,lang"
    )
    assert route_signature("https://example.com/policy/987") == "example.com/policy/{n}"


def test_boundary_spread_is_deterministic_and_includes_last_candidate():
    assert select_sample_indices(10, 5) == [0, 2, 5, 7, 9]
    assert select_sample_indices(10, 5, "prefix") == [0, 1, 2, 3, 4]
    assert select_sample_indices(2, 5) == [0, 1]


def test_minority_route_failure_cannot_hide_at_global_threshold():
    records = [
        {
            "url": f"https://example.com/news/{index}.html",
            "title": f"News {index}",
            "content": f"Body {index}",
        }
        for index in range(8)
    ]
    records.extend(
        [
            {"url": "https://example.com/special/9001", "title": "Special A", "content": ""},
            {"url": "https://example.com/special/9002", "title": "Special B", "content": None},
        ]
    )

    report = validate_records(_spec(), records)

    # The old global check admitted exactly 20% nulls.  The route certificate
    # isolates the minority template and blocks the false PASS.
    assert report.metrics.required_field_null_rate < 0.21
    assert report.status == "FAIL"
    issue = next(item for item in report.issues if item.type == "STRATUM_HIGH_NULL_RATE")
    assert issue.field == "content"
    assert issue.evidence["stratum"] == "example.com/special/{n}"
    assert report.coverage_certificate.complete is False
    assert report.coverage_certificate.failed_stratum_field_pairs == 1


def test_evidence_request_is_field_scoped_and_content_free():
    records = [
        {"url": "https://example.com/special/1", "title": "A", "content": "SECRET_BODY"},
        {"url": "https://example.com/special/2", "title": "B", "content": ""},
        {"url": "https://example.com/special/3", "title": "C", "content": ""},
    ]

    report = validate_records(_spec(), records)
    request = report.evidence_requests[0]
    serialized = request.model_dump_json()

    assert request.fields == ["content"]
    assert request.page_scope == "detail"
    assert request.max_samples <= 3
    assert request.max_chars <= 4000
    assert "SECRET_BODY" not in serialized
    assert request.representative_urls == ["https://example.com/special/2"]


def test_evidence_urls_greedily_cover_actual_field_failures_and_preserve_query():
    records = [
        {"url": "https://example.com/special/1?id=first", "title": "A", "content": "Body"},
        {"url": "https://example.com/special/2?id=title", "title": "", "content": "Body"},
        {"url": "https://example.com/special/3?id=both", "title": "", "content": ""},
        {"url": "https://example.com/special/4?id=body", "title": "D", "content": ""},
    ]

    report = validate_records(_spec(), records)

    assert report.evidence_requests[0].fields == ["title", "content"]
    assert report.evidence_requests[0].representative_urls == ["https://example.com/special/3?id=both"]
    issues = {item.field: item for item in report.issues if item.type == "STRATUM_HIGH_NULL_RATE"}
    assert issues["title"].evidence["representative_urls"] == ["https://example.com/special/2?id=title"]
    assert issues["content"].evidence["representative_urls"] == ["https://example.com/special/3?id=both"]
    assert report.coverage_certificate.strata[0].key == "example.com/special/{n}?id"


def test_evidence_urls_select_two_failure_rows_when_fields_fail_separately():
    records = [
        {"url": "https://example.com/special/1", "title": "A", "content": "Body"},
        {"url": "https://example.com/special/2", "title": "", "content": "Body"},
        {"url": "https://example.com/special/3", "title": "C", "content": ""},
    ]

    request = validate_records(_spec(), records).evidence_requests[0]

    assert request.representative_urls == ["https://example.com/special/2", "https://example.com/special/3"]


def test_coverage_certificate_explicitly_certifies_observed_records_only():
    report = validate_records(
        _spec(),
        [{"url": "https://example.com/special/1", "title": "A", "content": "Body"}],
    )

    assert report.coverage_certificate.scope == "observed_records"
    assert report.coverage_certificate.complete is True
    assert report.metrics.record_count == 1
