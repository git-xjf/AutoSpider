import hashlib
import json

import pytest
from pydantic import ValidationError

from packages.crawl_core.models import CrawlPlan, CrawlSpec, HttpRequestModel
from packages.crawl_core.task_manifest import verify_task_manifest


def test_http_request_rejects_persisted_secrets():
    with pytest.raises(ValidationError, match="secret headers"):
        HttpRequestModel(headers={"Authorization": "Bearer secret"})
    with pytest.raises(ValidationError, match="secret request parameters"):
        HttpRequestModel(method="POST", json_body={"credentials": {"api_key": "secret"}})


def test_transport_rejects_incompatible_selectors_and_operations():
    base = {
        "name": "unsafe",
        "start_urls": ["https://example.com"],
        "steps": [
            {
                "op": "EXTRACT",
                "field": "title",
                "selector": "$.title",
                "selector_type": "jsonpath",
                "source": "text",
            }
        ],
    }
    with pytest.raises(ValidationError, match="jsonpath selectors require http_api"):
        CrawlPlan.model_validate({**base, "execution_mode": "http_html"})
    with pytest.raises(ValidationError, match="browser-only operations"):
        CrawlPlan.model_validate(
            {
                "name": "unsafe",
                "start_urls": ["https://example.com"],
                "execution_mode": "http_html",
                "steps": [{"op": "SCROLL"}],
            }
        )


def test_old_playwright_task_hash_remains_valid():
    spec = {
        "request_id": "legacy",
        "start_urls": ["https://example.com"],
        "objective": "legacy task",
        "fields": [
            {"name": "title", "description": "Title", "type": "string", "required": True}
        ],
        "traversal": {"mode": "single"},
        "execution": {},
        "output": {"format": "json"},
    }
    plan = {
        "name": "legacy",
        "start_urls": ["https://example.com"],
        "steps": [
            {"op": "OPEN_URL", "url": "https://example.com"},
            {"op": "EXTRACT", "field": "title", "selector": "//h1", "source": "text"},
        ],
    }
    spec_payload = CrawlSpec.model_validate(spec).model_dump(mode="json", exclude_none=True)
    spec_payload["execution"].pop("mode", None)
    plan_payload = CrawlPlan.model_validate(plan).model_dump(mode="json", exclude_none=True)
    plan_payload.pop("execution_mode", None)
    canonical = json.dumps(
        {"crawl_spec": spec_payload, "crawl_plan": plan_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    task = {
        "schema": "autospider.task/v1",
        "version": "1.0",
        "engine": "playwright",
        "crawl_spec": spec_payload,
        "crawl_plan": plan_payload,
        "task_hash": hashlib.sha256(canonical).hexdigest(),
    }

    verify_task_manifest(task)
