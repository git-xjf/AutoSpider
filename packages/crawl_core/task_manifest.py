from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .models import CrawlPlan, CrawlSpec


def compute_task_hash(crawl_spec: CrawlSpec | dict[str, Any], crawl_plan: CrawlPlan | dict[str, Any]) -> str:
    """Return the canonical integrity hash used by compiled task manifests."""
    spec = crawl_spec if isinstance(crawl_spec, CrawlSpec) else CrawlSpec.model_validate(crawl_spec)
    plan = crawl_plan if isinstance(crawl_plan, CrawlPlan) else CrawlPlan.model_validate(crawl_plan)
    canonical = json.dumps(
        {
            "crawl_spec": spec.model_dump(mode="json", exclude_none=True),
            "crawl_plan": plan.model_dump(mode="json", exclude_none=True),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _legacy_task_hash(spec: CrawlSpec, plan: CrawlPlan) -> str:
    """Reproduce manifests compiled before execution-mode routing was added."""
    spec_payload = spec.model_dump(mode="json", exclude_none=True)
    spec_payload.get("execution", {}).pop("mode", None)
    plan_payload = plan.model_dump(mode="json", exclude_none=True)
    plan_payload.pop("execution_mode", None)
    canonical = json.dumps(
        {"crawl_spec": spec_payload, "crawl_plan": plan_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_task_manifest(task: dict[str, Any]) -> None:
    """Reject incomplete or model-mutated manifests before they reach the executor."""
    if task.get("schema") != "autospider.task/v1" or task.get("engine") not in {
        "http_html",
        "http_api",
        "playwright",
    }:
        raise ValueError("task is not a supported AutoSpider task")
    if task.get("version") != "1.0":
        raise ValueError("unsupported AutoSpider task version")
    try:
        spec = CrawlSpec.model_validate(task["crawl_spec"])
        plan = CrawlPlan.model_validate(task["crawl_plan"])
    except KeyError as exc:
        raise ValueError(f"compiled task is missing {exc.args[0]}") from exc
    if spec.start_urls != plan.start_urls:
        raise ValueError("compiled task start URLs do not match")
    if task.get("engine") != plan.execution_mode:
        raise ValueError("compiled task engine does not match CrawlPlan.execution_mode")
    supplied_hash = task.get("task_hash")
    expected_hash = compute_task_hash(spec, plan)
    if isinstance(supplied_hash, str) and hmac.compare_digest(supplied_hash, expected_hash):
        return
    is_legacy = (
        "execution_mode" not in task["crawl_plan"]
        and "mode" not in task["crawl_spec"].get("execution", {})
        and task.get("engine") == "playwright"
    )
    if is_legacy and isinstance(supplied_hash, str):
        if hmac.compare_digest(supplied_hash, _legacy_task_hash(spec, plan)):
            return
    raise ValueError("compiled task integrity check failed; compile it again instead of editing task JSON")
