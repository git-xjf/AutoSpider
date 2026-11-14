"""Reproduce current memory/validation gaps without opening a user database.

This is a diagnostic probe, not a test that considers the current gaps correct.
Successful probe execution exits zero even when quality truth is FAIL and the
current validator returns PASS. Every SQLite operation uses a temporary file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.crawl_core.models import CrawlSpec
from packages.site_memory import SiteMemoryRepository
from packages.validator import validate_records


def build_spec() -> CrawlSpec:
    return CrawlSpec.model_validate(
        {
            "request_id": "memory-patent-gap-probe",
            "start_urls": ["https://example.test/list"],
            "objective": "Extract each article's real body, not its related links",
            "fields": [
                {"name": "url", "description": "Detail URL", "type": "url", "required": True, "page_scope": "list"},
                {"name": "title", "description": "Article title", "type": "string", "required": True, "page_scope": "list"},
                {"name": "content", "description": "Article body", "type": "string", "required": True, "page_scope": "detail"},
            ],
            "traversal": {
                "mode": "list_detail",
                "item_limit": 10,
                "follow_detail": True,
                "pagination": {"strategy": "none"},
            },
            "execution": {"request_delay_ms": 0},
            "policy": {"allowed_domains": ["example.test"]},
            "output": {"format": "json"},
        }
    )


def correct_records() -> list[dict[str, Any]]:
    return [
        {
            "url": f"https://example.test/news/{1000 + index}",
            "title": f"Article {index}",
            "content": f"Ground-truth article body {index}",
        }
        for index in range(10)
    ]


def validation_case(name: str, records: list[dict[str, Any]], wrong_indices: list[int]) -> dict[str, Any]:
    report = validate_records(build_spec(), records)
    certificate = report.coverage_certificate
    assert certificate is not None
    return {
        "case": name,
        "fixture_truth": {
            "expected_quality_decision": "FAIL",
            "wrong_extraction_indices": wrong_indices,
            "correct_body_exists_for_every_record": True,
            "note": "Fixture truth is supplied by the probe, not inferred by the current validator.",
        },
        "actual_validation": {
            "status": report.status,
            "metrics": report.metrics.model_dump(mode="json"),
            "coverage_scope": certificate.scope,
            "strata_count": len(certificate.strata),
            "failed_stratum_field_pairs": certificate.failed_stratum_field_pairs,
            "content_null_rates": [
                {"stratum": stratum.key, "null_rate": field.null_rate}
                for stratum in certificate.strata
                for field in stratum.fields
                if field.field == "content"
            ],
            "issue_types": [issue.type for issue in report.issues],
        },
        "quality_gap_observed": report.status == "PASS",
    }


def memory_failure_probe() -> dict[str, Any]:
    # Deliberately use only repository APIs, not the MCP server: importing that
    # server initializes its configured repositories and could touch user state.
    with tempfile.TemporaryDirectory(prefix="autospider-memory-gap-") as temporary:
        repository = SiteMemoryRepository(Path(temporary) / "memory.db")
        repository.commit_validated(
            page_model={
                "url": "https://example.test/list",
                "page_type": "LIST_DETAIL",
                "fingerprint": "controlled-fixture-v1",
                "fields": {
                    "content": {"selector": "#Content", "selector_type": "css", "page_scope": "detail"}
                },
            },
            crawl_plan={"name": "legacy-body", "start_urls": ["https://example.test/list"], "steps": []},
            crawl_spec_hash="controlled-fixture-spec-v1",
            workflow_name="legacy-body",
            validation_score=0.95,
            easyspider_task_id=1,
        )
        before = repository.lookup_workflows("example.test")
        repository.record_failure(
            "example.test",
            "STRATUM_HIGH_NULL_RATE",
            "Controlled failure: body selector is empty for a known subset; manually recorded by this probe.",
            old_selector="#Content",
        )
        after = repository.lookup_workflows("example.test")
        with repository.connect() as connection:
            failure_count = connection.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
            bound_failure_count = connection.execute(
                "SELECT COUNT(*) FROM failures WHERE workflow_id IS NOT NULL"
            ).fetchone()[0]
        return {
            "case": "manually_recorded_failure_does_not_change_workflow_ranking",
            "database_scope": "isolated temporary SQLite; removed after the probe",
            "record_failure_invoked_manually": True,
            "production_automatic_failure_writeback_exercised": False,
            "before": [{"id": row["id"], "success_rate": row["success_rate"]} for row in before],
            "after": [{"id": row["id"], "success_rate": row["success_rate"]} for row in after],
            "persisted_failure_count": failure_count,
            "failures_bound_to_workflow": bound_failure_count,
            "same_top_workflow_after_failure": bool(before and after and before[0]["id"] == after[0]["id"]),
            "interpretation": (
                "A failure log alone does not affect lookup ordering or scope eligibility. "
                "This does not prove production failure writeback occurred, nor execute a real workflow."
            ),
        }


def run_probe() -> dict[str, Any]:
    same_route = correct_records()
    for row in same_route[8:]:
        row["content"] = ""
    wrong_region = correct_records()
    for index, row in enumerate(wrong_region):
        row["content"] = f"Distinct related-links text {index}"
    sources = [
        "packages/validator/coverage.py",
        "packages/validator/report.py",
        "packages/validator/semantic.py",
        "packages/site_memory/repository.py",
        "scripts/probe_memory_patent_gaps.py",
    ]
    return {
        "assessment_date": "2026-10-19",
        "probe_version": 1,
        "execution_scope": "Pure in-memory validation and isolated temporary SQLite; no network, MCP server, or user database.",
        "exit_policy": "Exit 0 means the probe completed; observed quality gaps are diagnostic results, not execution errors.",
        "source_sha256": {
            source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest() for source in sources
        },
        "validation_cases": [
            validation_case("same_route_two_of_ten_empty_bodies", same_route, [8, 9]),
            validation_case("unique_nonempty_wrong_body_region", wrong_region, list(range(10))),
        ],
        "memory_case": memory_failure_probe(),
        "limitations": [
            "Desired quality FAIL is fixture truth, while the current validator intentionally permits up to 20% per-field nulls.",
            "The wrong-region case supplies incorrect strings; it does not execute HTML extraction or estimate real-site error rates.",
            "The repository ranking probe manually records a failure; production automatic writeback is not exercised.",
            "No proposed memory control mechanism is implemented or evaluated by this probe.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON artifact path; full results are also printed.")
    arguments = parser.parse_args()
    payload = json.dumps(run_probe(), ensure_ascii=False, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
