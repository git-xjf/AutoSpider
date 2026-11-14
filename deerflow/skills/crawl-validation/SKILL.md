---
name: crawl-validation
description: Validate sample or final crawl records and diagnose precise CrawlPlan repairs. Use after every execution and before a full run or memory commit.
allowed-tools:
  - autospider_qa_validate
  - autospider_qa_validate_execution
  - autospider_execution_result
  - autospider_execution_result_summary
  - autospider_execution_logs
  - autospider_probe_http_select
  - autospider_selector_test
  - autospider_probe_snapshot
---

# Crawl Validation

An execution selector_no_match failure can precede QA. Inspect only the supplied failed page/field with a compact selector probe and return verified changes to the lead; keep the requested field contract. Execution failure is not a successful empty QA result. Policy/internal failures stop instead of triggering selector repairs.

Check required-field coverage and nulls both globally and in every route stratum, semantic type, repeated constants, duplicates, cardinality, URL validity, cross-record alignment, pagination, and detail-page origin. Prefer `qa_validate_execution` so full article bodies never enter model context. Process completion is not proof of quality. A failed `coverage_certificate` blocks PASS even when the global average passes. On failure, use `evidence_requests` as a hard evidence boundary: inspect only the listed representative URLs and fields, within each request's sample/character budget; do not fetch the full DOM or reanalyze successful strata. Emit the smallest RepairPatch against CrawlPlan; never edit the compiled task and never repeat a failed patch. A PASS needs no second model interpretation.
