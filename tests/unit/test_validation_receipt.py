import json
from pathlib import Path

import pytest

from packages.crawl_core.models import CrawlSpec, ValidationReport
from packages.validator import validate_records
from services.easyspider_mcp import server


FIXTURES = Path(__file__).parents[1] / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_many_route_failures_cannot_overflow_compact_qa_receipt():
    spec = CrawlSpec.model_validate(_fixture("crawl_spec.json"))
    base = _fixture("expected_records.json")[0]
    rows = [{**base, "url": f"https://example.com/group{chr(97 + index // 26)}{chr(97 + index % 26)}/item", "content": ""}
            for index in range(300)]
    report = validate_records(spec, rows).model_dump(mode="json", exclude_none=True)

    receipt = server._validation_receipt(report)

    assert len(json.dumps(receipt, ensure_ascii=False).encode("utf-8")) <= 16000
    assert receipt["status"] == "FAIL"
    assert receipt["coverage_certificate"]["failed_stratum_field_pairs"] == 300
    assert any("truncated" in warning for warning in receipt["warnings"])
    assert len(report["coverage_certificate"]["strata"]) == 300
    ValidationReport.model_validate(receipt)


def test_evidence_target_query_is_not_truncated():
    spec = CrawlSpec.model_validate(_fixture("crawl_spec.json"))
    target = "https://example.com/special/1?target=" + "x" * 700
    base = _fixture("expected_records.json")[0]
    report = validate_records(spec, [{**base, "url": target, "content": ""}]).model_dump(mode="json")

    receipt = server._validation_receipt(report, max_string_chars=100)

    assert receipt["evidence_requests"][0]["representative_urls"] == [target]


def test_memory_rejects_pass_label_with_failed_coverage():
    spec = CrawlSpec.model_validate(_fixture("crawl_spec.json"))
    base = _fixture("expected_records.json")[0]
    report = validate_records(spec, [{**base, "content": ""}]).model_dump(mode="json")
    report["status"] = "PASS"

    with pytest.raises(ValueError, match="coverage evidence"):
        server.memory_commit_validated(_fixture("page_model.json"), {}, spec, report)
