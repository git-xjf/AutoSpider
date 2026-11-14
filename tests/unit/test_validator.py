import json
from pathlib import Path

from packages.validator import validate_records

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_records_pass():
    report = validate_records(load("crawl_spec.json"), load("expected_records.json"))
    assert report.status == "PASS"
    assert report.metrics.required_field_coverage == 1


def test_constant_wrong_author_and_missing_content_fail():
    records = load("expected_records.json")
    for row in records:
        row["author"] = "News Center"
        row["content"] = ""
    report = validate_records(load("crawl_spec.json"), records)
    assert report.status == "FAIL"
    assert any(issue.type == "HIGH_NULL_RATE" and issue.field == "content" for issue in report.issues)
    assert report.repair_patch.operation == "REQUEST_REANALYSIS"

