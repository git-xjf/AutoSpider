from __future__ import annotations

import hashlib
import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from urllib.parse import urljoin

import httpx
from pydantic import Field, ValidationError

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.models import CrawlPlan, CrawlSpec, PageModel, ValidationReport
from packages.crawl_core.drafts import CrawlDraftRepository, DraftValidationError, build_crawl_draft
from packages.crawl_core.planner import build_crawl_plan
from packages.crawl_core.policies import PolicyEngine, host_allowed
from packages.crawl_core.reuse import build_reused_crawl
from packages.crawl_core.site_scope import default_allowed_domains
from packages.crawl_core.task_manifest import verify_task_manifest
from packages.inspector import BrowserInspector
from packages.inspector.dom_simplifier import simplify_dom, visible_text
from packages.inspector.http_summary import summarize_http_html, select_http_html, validate_http_select_inputs
from packages.crawl_core.errors import PolicyViolation
from packages.site_memory import SiteMemoryRepository
from packages.validator import validate_records

from .adapters import build_adapter
from .tool_errors import AutoSpiderMCP, ProbeToolError, failure_receipt


@asynccontextmanager
async def server_lifespan(_server):
    try:
        yield {}
    finally:
        executor = getattr(adapter, "executor", None)
        if executor is not None:
            for execution_id in list(executor._jobs):
                await executor.stop(execution_id)
        await inspector.shutdown()


mcp = AutoSpiderMCP(
    "autospider",
    instructions="Deterministic, policy-constrained HTTP and Playwright crawling tools for AutoSpider",
    host=os.getenv("AUTOSPIDER_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("AUTOSPIDER_MCP_PORT", "8000")),
    lifespan=server_lifespan,
)
compiler = PlaywrightCompiler()
inspector = BrowserInspector(
    navigation_timeout_ms=int(os.getenv("AUTOSPIDER_PROBE_TIMEOUT_MS", "30000")),
    max_sessions=int(os.getenv("AUTOSPIDER_MAX_PROBE_SESSIONS", "4")),
    visible_text_max_chars=int(os.getenv("AUTOSPIDER_SNAPSHOT_TEXT_MAX_CHARS", "6000")),
    simplified_dom_max_chars=int(os.getenv("AUTOSPIDER_SNAPSHOT_DOM_MAX_CHARS", "16000")),
)
policy = PolicyEngine()
memory = SiteMemoryRepository(os.getenv("AUTOSPIDER_MEMORY_DB", "data/site_memory.db"))
adapter = build_adapter()
drafts = CrawlDraftRepository(os.getenv("AUTOSPIDER_RUNTIME_ROOT", "runtime/playwright"))


def _canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _compact_value(value: Any, *, max_string_chars: int = 500) -> Any:
    """Bound model-facing previews while preserving full records on disk."""
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        return value[:max_string_chars] + f"… <{len(value) - max_string_chars} chars omitted>"
    if isinstance(value, list):
        return [_compact_value(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, dict):
        return {key: _compact_value(item, max_string_chars=max_string_chars) for key, item in value.items()}
    return value


def _validation_receipt(
    report: dict[str, Any], *, max_string_chars: int = 500, maximum_bytes: int = 16000
) -> dict[str, Any]:
    """Bound the entire QA receipt, retaining decisions and aggregate counts.

    Detail omission changes only the display, never the validator's decision.
    Evidence-request URLs remain exact fetch targets rather than truncated URLs.
    """
    receipt = _compact_value(report, max_string_chars=max_string_chars)
    receipt["evidence_requests"] = [dict(item) for item in report.get("evidence_requests", [])]
    certificate = receipt.get("coverage_certificate") or {}
    strata = certificate.get("strata", [])
    issues = receipt.get("issues", [])
    requests = receipt["evidence_requests"]
    omitted = {"strata": 0, "issues": 0, "requests": 0}
    warning = "QA detail display truncated; decisions and coverage counts include all evaluated records."

    def size() -> int:
        return len(json.dumps(receipt, ensure_ascii=False).encode("utf-8"))

    while size() > maximum_bytes:
        if warning not in receipt.setdefault("warnings", []):
            receipt["warnings"].append(warning)
        if strata:
            ceiling = certificate.get("required_null_rate_ceiling", 0.2)
            successful = next(
                (index for index, item in enumerate(strata)
                 if all(field.get("null_rate", 0) <= ceiling for field in item.get("fields", []))),
                len(strata) - 1,
            )
            strata.pop(successful)
            omitted["strata"] += 1
        elif issues:
            issues.pop()
            omitted["issues"] += 1
        elif requests:
            requests.pop()
            omitted["requests"] += 1
        else:
            # Fixed metrics/decision fields are small; unusually long caller
            # warnings are the remaining variable-size input.
            receipt["warnings"] = [warning]
            break
    if any(omitted.values()):
        receipt["warnings"][-1] = (
            f"{warning} Omitted strata={omitted['strata']}, issues={omitted['issues']}, "
            f"evidence requests={omitted['requests']}."
        )
        # Reserve room for the final omission counts in the byte budget.
        if size() > maximum_bytes:
            return _validation_receipt(report, max_string_chars=max_string_chars, maximum_bytes=maximum_bytes - 128)
    return receipt


def _classify_execution_failure(result: dict[str, Any]) -> dict[str, Any]:
    """Separate policy/authority stops from selector failures on every pipeline.

    Executors persist exception type prefixes. Match the leading type, not page
    text embedded inside an AdapterError, and never expand domain/network policy.
    """
    raw_error = result.get("error")
    if not isinstance(raw_error, str):
        return result
    is_policy_error = raw_error.lstrip().startswith("PolicyViolation:")
    safe_error = re.sub(r"(https?://)[^\s/@]+@", r"\1[credentials-redacted]@", raw_error, flags=re.I)
    result["error"] = _compact_value(safe_error, max_string_chars=500)
    if result.get("execution_status") == "failed" and is_policy_error:
        # A transport authority stop supersedes selector/coverage diagnostics.
        # Keep the blocked receipt below the model-facing size bound even when
        # a preceding sample had a detailed coverage certificate.
        for key in ("validation", "sample_validation"):
            report = result.get(key)
            if isinstance(report, dict):
                report.pop("coverage_certificate", None)
                report.pop("evidence_requests", None)
        result.update(
            status="blocked",
            code="policy_blocked",
            retryable=False,
            requires_user_confirmation=True,
            needs_reanalysis=False,
            next_action="Stop and ask the user or administrator for authority/configuration guidance. This is a policy block, not a selector failure: do not repair selectors, change transport, add domains/private exceptions, skip records, or repeat execution. After verified authorization changes, use a new authorized plan/draft; terminal failed IDs remain cached.",
        )
    elif result.get("execution_status") == "failed":
        selector_failure = raw_error.lstrip().startswith((
            "SelectorMatchError:", "AdapterError: selector matched no values:",
            "AdapterError: selector matched no elements:",
        ))
        if selector_failure:
            result.update(
                status="needs_repair", code="selector_no_match", retryable=False,
                retry_limit=0, needs_reanalysis=True,
                next_action="Inspect the failed page/field in failure using a bounded selector probe. Preserve the requested fields and task limits. Verify a changed selector, create a new draft, and execute it; do not restart this terminal execution or ask to drop a required field before diagnosis. This execution failed before QA; a missing QA report does not mean repair is unnecessary.",
            )
        else:
            result.update(
                status="tool_error", code="execution_failed", retryable=False,
                retry_limit=0, needs_reanalysis=False,
                next_action="Report the execution failure and inspect its logs. Do not guess selector changes or restart the failed execution for an unclassified internal/transport error.",
            )
    return result


def _result_summary(result: dict[str, Any], *, preview_records: int, max_field_chars: int) -> dict[str, Any]:
    records = result.get("records") or []
    return {
        "status": result.get("status"),
        "record_count": result.get("record_count", 0),
        "output_files": result.get("output_files", []),
        "error": result.get("error"),
        "records_preview": _compact_value(records[:preview_records], max_string_chars=max_field_chars),
        "records_omitted": max(0, int(result.get("record_count", 0)) - min(preview_records, len(records))),
    }


def _bound_selector_receipt(result: dict[str, Any], *, maximum: int = 6000) -> dict[str, Any]:
    """Keep counts/errors for every field, but bound redundant model previews."""
    seen_codes = set()
    for diagnostic in result.get("field_errors", {}).values():
        code = diagnostic["code"]
        if code in seen_codes:
            diagnostic.pop("error", None)
            diagnostic.pop("hint", None)
        seen_codes.add(code)
    if len(str(result.get("url", ""))) > 2048:
        result.pop("url", None)
        result["url_omitted"] = True
    omitted = 0
    while len(json.dumps(result, ensure_ascii=False)) > maximum:
        samples = [field["samples"] for field in result.get("fields", {}).values() if field.get("samples")]
        if not samples:
            # Keep field names, parameters and codes even when hints dominate.
            hints = [diagnostic for diagnostic in result.get("field_errors", {}).values() if "hint" in diagnostic or "error" in diagnostic]
            if not hints:
                break
            diagnostic = max(hints, key=lambda value: len(value.get("hint", "")) + len(value.get("error", "")))
            diagnostic.pop("hint", None)
            diagnostic.pop("error", None)
            result["diagnostics_compacted"] = True
            continue
        longest = max(samples, key=lambda values: max(len(value) for value in values))
        longest.remove(max(longest, key=len))
        omitted += 1
        result["samples_omitted"] = omitted
        result["preview_truncated"] = True
    return result


@mcp.tool()
async def probe_open(
    url: str,
    headless: bool = True,
    allowed_domains: list[str] | None = None,
    respect_robots: bool = True,
    allow_cross_domain_navigation: bool = False,
) -> dict[str, Any]:
    """Open one public HTTP(S) page in a read-only analysis browser."""
    allowed = allowed_domains or default_allowed_domains([url])
    await asyncio.to_thread(policy.check_url, url, allowed_domains=allowed, block_private_networks=True)
    if respect_robots and not await asyncio.to_thread(policy.robots_allowed, url):
        return failure_receipt("blocked", "policy_blocked", "robots.txt does not allow AutoSpider.", requires_user_confirmation=True, needs_reanalysis=False, next_action="Stop and respect the site's robots policy. Do not change transport, disable policy checks or repeat this request.")
    return await inspector.open(
        url,
        headless=headless,
        allowed_domains=allowed,
        allow_cross_domain_navigation=allow_cross_domain_navigation,
    )


async def _probe_http_response(
    url: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    form_body: dict[str, Any] | None = None,
    allowed_domains: list[str] | None = None,
    respect_robots: bool = True,
    allow_cross_domain_navigation: bool = False,
) -> httpx.Response | dict[str, Any]:
    normalized_method = method.upper()
    if normalized_method not in {"GET", "POST"}:
        raise ValueError("method must be GET or POST")
    if normalized_method == "GET" and (json_body is not None or form_body is not None):
        raise ValueError("GET probe cannot contain a request body")
    if json_body is not None and form_body is not None:
        raise ValueError("provide json_body or form_body, not both")
    allowed = allowed_domains or default_allowed_domains([url])
    current_url = url
    current_method = normalized_method
    async with httpx.AsyncClient(
        headers={"User-Agent": "AutoSpider/0.1"},
        timeout=30,
        follow_redirects=False,
    ) as client:
        for redirect_count in range(11):
            await asyncio.to_thread(
                policy.check_url,
                current_url,
                allowed_domains=allowed,
                block_private_networks=True,
            )
            if respect_robots and not await asyncio.to_thread(policy.robots_allowed, current_url):
                return failure_receipt("blocked", "policy_blocked", "robots.txt does not allow AutoSpider.", requires_user_confirmation=True, needs_reanalysis=False, next_action="Stop and respect the site's robots policy. Do not change transport, disable policy checks or repeat this request.")
            response = await client.request(
                current_method,
                current_url,
                params=params if redirect_count == 0 else None,
                json=json_body if redirect_count == 0 else None,
                data=form_body if redirect_count == 0 else None,
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ProbeToolError("invalid_redirect", "The HTTP redirect has no Location header.", next_action="Stop and verify the target using existing evidence; do not guess redirect URLs or repeat this request.")
                next_url = urljoin(str(response.url), location)
                next_host = urlparse(next_url).hostname or ""
                if not allow_cross_domain_navigation and not host_allowed(next_host, allowed):
                    raise PolicyViolation("cross-domain HTTP redirect is disabled")
                current_url = next_url
                if response.status_code == 303 or (response.status_code in {301, 302} and current_method == "POST"):
                    current_method = "GET"
                continue
            max_bytes = int(os.getenv("AUTOSPIDER_HTTP_PROBE_MAX_BYTES", "1000000"))
            if len(response.content) > max_bytes:
                raise ProbeToolError("response_too_large", "The HTTP response exceeds the probe's configured size limit.", next_action="Use a narrower verified public endpoint or bounded existing evidence. Do not repeat the same oversized probe.")
            if response.status_code >= 400:
                retry = normalized_method == "GET" and (response.status_code in {408, 429} or response.status_code >= 500)
                return failure_receipt("http_error", "http_status_error", f"HTTP {response.status_code}", status_code=response.status_code, retryable=retry, retry_limit=int(retry), next_action="For 404, read actual pagination_links from the original page; do not guess more URL patterns. For 401/403, stop and respect access controls. Retry an eligible GET at most once, respecting Retry-After; never automatically repeat POST or state-changing requests.")
            return response
        raise ProbeToolError("redirect_limit", "The HTTP probe exceeded its redirect limit.", next_action="Stop and verify the actual target URL; do not guess alternate paths or repeat the redirect loop.")


@mcp.tool()
async def probe_http(
    url: str,
    method: Literal["GET", "POST"] = "GET",
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    form_body: dict[str, Any] | None = None,
    allowed_domains: list[str] | None = None,
    respect_robots: bool = True,
    allow_cross_domain_navigation: bool = False,
    mode: Literal["overview", "dom"] = "overview",
) -> dict[str, Any]:
    """Inspect HTTP HTML/JSON without a browser. Default overview returns compact
    pagination links, item structure, headings and body-container candidates,
    never a full DOM or article. Use probe_http_select to verify those candidates.
    Explicit mode=dom is for advanced repair only, not repeated routine requests.
    404 returns a small recoverable status; never infer pagination from conventions.
    """
    response = await _probe_http_response(
        url,
        method,
        params,
        json_body,
        form_body,
        allowed_domains,
        respect_robots,
        allow_cross_domain_navigation,
    )
    if isinstance(response, dict):
        return response
    content_type = response.headers.get("content-type", "")
    base = {
        "status": "ready",
        "url": str(response.url),
        "method": response.request.method,
        "status_code": response.status_code,
        "content_type": content_type,
        "response_bytes": len(response.content),
    }
    if "json" in content_type.lower():
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProbeToolError("invalid_json", "The endpoint declared JSON but returned malformed JSON.", next_action="Verify the response format and endpoint using existing evidence; do not parse it as HTML or repeat unchanged calls.") from exc
        preview = json.dumps(_compact_value(payload, max_string_chars=500), ensure_ascii=False)
        return {**base, "response_format": "json", "json_preview": preview[:4_000]}
    if mode == "dom":
        return {**base, "response_format": "html", "dom": simplify_dom(response.text), "visible_text": visible_text(response.text, max_chars=1200)}
    try:
        summary = summarize_http_html(response.text, str(response.url))
    except ValueError as exc:
        raise ProbeToolError("invalid_html", "The HTTP response contains no parseable HTML document.", next_action="Verify the response format and endpoint. Do not guess selectors against an empty or non-HTML response.") from exc
    return {**base, "response_format": "html", **summary}


@mcp.tool()
async def probe_http_select(
    url: str,
    selectors: Annotated[dict[str, str], Field(min_length=1, max_length=20)],
    selector_type: Literal["xpath", "css"] = "xpath",
    scope_selector: str | None = None,
    attributes: dict[str, str] | None = None,
    max_samples: Annotated[int, Field(ge=1, le=3)] = 2,
    max_chars: Annotated[int, Field(ge=100, le=1000)] = 500,
) -> dict[str, Any]:
    """Test XPath 1.0 / standard CSS on complete HTML with a plain GET.
    For list fields set scope_selector to the item selector; field selectors
    are then relative. Set attributes={"url":"href"} for links. No browser,
    No re:test/fn: prefixes, XPath 2.0/3.0 functions, variables or browser-only
    selectors. field_errors identifies invalid fields and preserves other field
    counts. Correct only failed parameters once; no mode change for syntax errors.
    No full DOM/article bodies. Robots/private checks remain enabled.
    """
    preflight = validate_http_select_inputs(selectors, selector_type=selector_type, scope_selector=scope_selector, attributes=attributes, max_samples=max_samples, max_chars=max_chars, allow_partial=True)
    if not preflight["valid_fields"]:
        return _bound_selector_receipt(failure_receipt("validation_error", "invalid_selector", "No supplied HTML field selector passed validation.", field_errors=preflight["field_errors"], retryable=True, retry_limit=1, next_action="Correct only the named field parameters once using their hints and verified HTML evidence. Do not retry unchanged selectors or switch modes for a syntax error."))
    response = await _probe_http_response(url)
    if isinstance(response, dict):
        return response
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        return failure_receipt("response_error", "response_format_mismatch", "This response is JSON, not an HTML document.", response_format="json", next_action="Use http_api with verified JSONPath selectors for this public JSON response; do not apply XPath/CSS to it.")
    result = {
        "status": "ready",
        "status_code": response.status_code,
        **select_http_html(response.text, str(response.url), selectors, selector_type=selector_type, scope_selector=scope_selector, attributes=attributes, max_samples=max_samples, max_chars=max_chars, allow_partial=True),
    }
    if result.get("field_errors"):
        result.update(failure_receipt("validation_error", "invalid_selector", "Some HTML field selectors are invalid; valid field counts are retained as partial evidence, not complete validation.", retryable=True, retry_limit=1, next_action="Keep the valid field evidence. Correct only field_errors parameters once using XPath 1.0/standard CSS and the supplied hints; do not retry unchanged selectors or switch modes for syntax errors."))
    return _bound_selector_receipt(result)


@mcp.tool()
async def probe_snapshot(session_id: str, mode: str = "simplified_dom") -> dict[str, Any]:
    """Return bounded visible text, simplified DOM, links, forms and buttons."""
    return await inspector.snapshot(session_id, mode)


@mcp.tool()
async def probe_network(
    session_id: str,
    limit: Annotated[int, Field(ge=1, le=30)] = 10,
) -> dict[str, Any]:
    """Return bounded, redacted XHR/fetch evidence for choosing http_api mode."""
    return await inspector.network(session_id, limit=limit)


@mcp.tool()
async def network_jsonpath_test(
    session_id: str,
    path: str,
    request_index: int = -1,
) -> dict[str, Any]:
    """Test deterministic JSONPath against a captured XHR/fetch JSON response."""
    return await inspector.network_jsonpath_test(
        session_id,
        path,
        request_index=request_index,
    )


@mcp.tool()
async def probe_close(session_id: str) -> dict[str, Any]:
    """Close a probe browser session and release its resources."""
    return await inspector.close(session_id)


@mcp.tool()
async def selector_test(session_id: str, selector_type: str, selector: str) -> dict[str, Any]:
    """Validate an XPath or CSS selector and return match count and examples."""
    if selector_type not in {"xpath", "css"}:
        raise ValueError("selector_type must be xpath or css")
    return await inspector.selector_test(session_id, selector_type, selector)


@mcp.tool()
def crawl_plan_build(crawl_spec: CrawlSpec, page_model: PageModel) -> dict[str, Any]:
    """Build hybrid IR from the full typed schemas. Execution mode resolves from
    CrawlSpec.execution.mode or PageModel.recommended_mode. Set traversal.item_limit for a record cap;
    load-more uses pagination.type='click' plus selector. Extract href from an
    element using extract='attribute', attribute='href', never XPath ./@href.
    """
    spec = CrawlSpec.model_validate(crawl_spec)
    page = PageModel.model_validate(page_model)
    return build_crawl_plan(spec, page).model_dump(mode="json", exclude_none=True)


@mcp.tool()
def task_compile(
    crawl_spec: CrawlSpec,
    crawl_plan: CrawlPlan,
    sample_size: int | None = None,
) -> dict[str, Any]:
    """Compile IR to a signed http_html, http_api or playwright task; never uses an LLM."""
    task = compiler.compile(crawl_spec, crawl_plan, sample_size=sample_size)
    return {"compiled": True, "engine": task["engine"], "task": task, "warnings": []}


@mcp.tool()
async def task_compile_register(
    crawl_spec: CrawlSpec,
    crawl_plan: CrawlPlan,
    sample_size: int | None = None,
) -> dict[str, Any]:
    """Compile and register atomically so task JSON cannot be corrupted between calls."""
    task = compiler.compile(crawl_spec, crawl_plan, sample_size=sample_size)
    task_id = await adapter.task_register(task)
    return {
        "compiled": True,
        "engine": task["engine"],
        "task_id": task_id,
        "task_hash": task["task_hash"],
        "sample": task["sample"],
        "sample_size": task["sample_size"],
        "warnings": [],
    }


@mcp.tool()
async def crawl_execute_validated(
    crawl_spec: CrawlSpec,
    crawl_plan: CrawlPlan,
    sample_size: Annotated[int | None, Field(default=None, ge=1, le=50)] = None,
    headless: bool | None = None,
    max_wait_seconds: Annotated[float, Field(ge=1, le=600)] = 300,
    preview_records: Annotated[int, Field(ge=0, le=5)] = 1,
    max_field_chars: Annotated[int, Field(ge=100, le=2000)] = 500,
) -> dict[str, Any]:
    """Compile, register, execute, wait and validate in one deterministic call.

    Advanced typed execution/repair primitive. For routine new sites use
    crawl_draft_create/execute; for compatible memory use crawl_reuse_workflow.
    Full records remain in output_files; previews and QA evidence are bounded.
    """
    spec = CrawlSpec.model_validate(crawl_spec)
    plan = CrawlPlan.model_validate(crawl_plan)
    task = compiler.compile(spec, plan, sample_size=sample_size)
    task_id = await adapter.task_register(task)
    execution_id = await adapter.execution_create(task_id, {})
    await adapter.execution_start(
        execution_id,
        headless=spec.execution.headless if headless is None else headless,
    )

    deadline = asyncio.get_running_loop().time() + max_wait_seconds
    status = await adapter.execution_logs(execution_id)
    while status["status"] in {"created", "starting", "running"} and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(min(0.5, max(0, deadline - asyncio.get_running_loop().time())))
        status = await adapter.execution_logs(execution_id)

    response: dict[str, Any] = {
        "engine": task["engine"],
        "task_id": task_id,
        "execution_id": execution_id,
        "task_hash": task["task_hash"],
        "sample": task["sample"],
        "sample_size": task["sample_size"],
        "execution_status": status["status"],
        "record_count": status.get("record_count", 0),
        "pages_visited": status.get("pages_visited", 0),
        "error": status.get("error"),
    }
    if isinstance(status.get("failure"), dict):
        response["failure"] = status["failure"]
    if status["status"] in {"created", "starting", "running"}:
        response["timed_out"] = True
        response["next_action"] = "Call execution_logs once with wait_seconds=30; do not restart this execution."
        return response
    if status["status"] != "completed":
        return _classify_execution_failure(response)

    result = await adapter.execution_result(execution_id, limit=1000)
    response.update(
        _result_summary(
            result,
            preview_records=preview_records,
            max_field_chars=max_field_chars,
        )
    )
    response["execution_status"] = status["status"]
    response["validation"] = _validation_receipt(
        validate_records(spec, result.get("records") or []).model_dump(mode="json", exclude_none=True),
        max_string_chars=max_field_chars,
    )
    response["validation_complete"] = response["validation"]["metrics"]["record_count"] >= response["record_count"]
    if not response["validation_complete"]:
        response["needs_full_validation"] = True
        response["next_action"] = "QA covers only the first 1000 records; validate the full output before claiming complete QA or committing memory."
    return response


@mcp.tool()
async def crawl_reuse_workflow(
    workflow_id: Annotated[int, Field(ge=1)],
    start_url: str,
    item_limit: Annotated[int, Field(ge=1, le=1000)],
    output_filename: str | None = None,
    field_names: list[str] | None = None,
    max_pages: Annotated[int | None, Field(ge=1, le=1000)] = None,
    sample_size: Annotated[int, Field(ge=1, le=50)] = 5,
    headless: bool | None = None,
    max_wait_seconds: Annotated[float, Field(ge=1, le=600)] = 300,
    preview_records: Annotated[int, Field(ge=0, le=5)] = 0,
) -> dict[str, Any]:
    """Reuse validated site memory by ID; no model-written spec or plan needed.

    Only the same URL route and already covered fields are accepted. QA a small
    sample, then execute full automatically on PASS; small requests run once.
    Returns bounded QA/files, never the stored plan or complete article data.
    """
    try:
        workflow = memory.get_workflow(workflow_id)
        source_task_id = workflow.get("easyspider_task_id")
        if not source_task_id or not workflow.get("last_success_at") or workflow.get("success_rate", 0) < 0.7:
            raise ValueError("workflow has no validated executable task; reanalysis is required")
        source_task = await adapter.task_get(source_task_id)
        verify_task_manifest(source_task)
        spec, plan = build_reused_crawl(
            source_task["crawl_spec"],
            workflow["crawl_plan"],
            start_url=start_url,
            item_limit=item_limit,
            output_filename=output_filename,
            field_names=field_names,
            max_pages=max_pages,
        )
    except (KeyError, ValueError) as exc:
        return {
            "workflow_id": workflow_id,
            "needs_reanalysis": True,
            "error": str(exc),
            "next_action": "Discover inspection/planning tools with tool_search and analyze this request; do not retry the same reuse arguments.",
        }

    # A small request is itself the validated final result. Do not apply sample
    # page limits, or crawl the same records twice, in that case.
    sample_is_final = item_limit <= sample_size
    sample = await crawl_execute_validated(
        spec,
        plan,
        sample_size=None if sample_is_final else sample_size,
        headless=headless,
        max_wait_seconds=max_wait_seconds,
        preview_records=preview_records if sample_is_final else 0,
    )
    sample.update(workflow_id=workflow_id, sample_is_final=sample_is_final, allowed_domains=spec.policy.allowed_domains)
    sample["requested_record_count"] = item_limit
    sample["limit_reached"] = sample_is_final and sample.get("record_count", 0) >= item_limit
    if sample.get("validation", {}).get("status") != "PASS":
        sample["needs_reanalysis"] = not sample.get("timed_out", False)
        return _classify_execution_failure(sample)
    if sample_is_final:
        return sample

    result = await crawl_execute_validated(
        spec,
        plan,
        headless=headless,
        max_wait_seconds=max_wait_seconds,
        preview_records=preview_records,
    )
    result.update(
        workflow_id=workflow_id,
        sample_is_final=False,
        sample_execution_id=sample["execution_id"],
        sample_validation=sample["validation"],
        requested_record_count=item_limit,
        limit_reached=result.get("record_count", 0) >= item_limit,
        needs_reanalysis=not result.get("timed_out", False) and result.get("validation", {}).get("status") != "PASS",
        allowed_domains=spec.policy.allowed_domains,
    )
    return _classify_execution_failure(result)


@mcp.tool()
def crawl_draft_create(
    url: str,
    field_selectors: Annotated[
        dict[str, str],
        Field(
            min_length=1,
            max_length=20,
            description="All requested fields mapped to selectors, including detail fields. List selectors are relative to item_selector; detail selectors are evaluated on the detail document.",
            examples=[{"title": ".//h3/a", "url": ".//h3/a", "content": "//div[@id='Content']"}],
        ),
    ],
    item_selector: str | None = None,
    selector_type: Literal["xpath", "css", "jsonpath"] = "xpath",
    mode: Literal["http_html", "http_api", "playwright"] = "http_html",
    detail_fields: Annotated[
        list[str] | None, Field(description="Unique field names only, e.g. ['content'], never selectors or a mapping. Every name must already be a key in field_selectors, where its detail-page selector belongs.", examples=[["content"]])
    ] = None,
    detail_link_selector: Annotated[str | None, Field(description="Link element selector relative to each item_selector, e.g. .//h3/a. Required with detail_fields; do not select an @href attribute node.", examples=[".//h3/a"])] = None,
    pagination_selector: str | None = None,
    pagination_url_template: str | None = None,
    pagination_after_extract: bool = False,
    page_limit: Annotated[int, Field(ge=1, le=1000)] = 1,
    item_limit: Annotated[int | None, Field(ge=1, le=1000)] = None,
    field_attributes: dict[str, str] | None = None,
    field_types: Annotated[
        dict[str, str] | None,
        Field(description="Optional requested-field types: string, integer, number, boolean, date, datetime, url or html. Usually omit: names url/link/href default to url, all other fields to string. Not text, str or attribute."),
    ] = None,
    http_request: dict[str, Any] | None = None,
    output_filename: str | None = None,
    objective: str | None = None,
) -> dict[str, Any]:
    """Prepare a new-site draft from verified selectors, never from model-written
    CrawlSpec/Plan JSON. field_selectors maps field names to XPath/CSS/JSONPath.
    detail_fields is only a list of names: ["content"]. Include the corresponding
    detail selector in field_selectors too, e.g. {"content":"//main"}. List
    selectors are relative to item_selector; detail selectors target the detail
    document opened by an item-relative detail_link_selector. HTML links default
    to href extraction; override with field_attributes. page_limit counts LIST
    pages, not details. {{page_number}} is current page (1-based). When page 1
    uses the start URL, set pagination_after_extract=True and use the observed
    next-page pattern with {{next_page_number}} (2, 3, ...). Next call
    crawl_draft_execute with the returned draft_id.
    """
    try:
        spec, page, plan = build_crawl_draft(
            url=url,
            field_selectors=field_selectors,
            item_selector=item_selector,
            selector_type=selector_type,
            mode=mode,
            detail_fields=detail_fields,
            detail_link_selector=detail_link_selector,
            pagination_selector=pagination_selector,
            pagination_url_template=pagination_url_template,
            pagination_after_extract=pagination_after_extract,
            page_limit=page_limit,
            item_limit=item_limit,
            field_attributes=field_attributes,
            field_types=field_types,
            http_request=http_request,
            output_filename=output_filename,
            objective=objective,
        )
    except ValueError as exc:
        message = str(exc)[:500]
        parameter = getattr(exc, "parameter", None)
        if isinstance(exc, ValidationError):
            # Pydantic's normal str() includes input_value, which may contain
            # credentials rejected by the durable HTTP request model.
            errors = exc.errors(include_input=False, include_context=False, include_url=False)[:3]
            parameter = ".".join(str(part) for part in errors[0]["loc"])[:100] if errors else None
            message = "; ".join(f"{'.'.join(str(part) for part in error['loc'])[:100]}: {error['msg'][:160]}" for error in errors)[:500]
        result = {
            "status": "validation_error",
            "code": getattr(exc, "code", "invalid_draft"),
            "parameter": parameter,
            "error": message,
            "retry_limit": 1,
            "next_action": "Correct only the reported parameter once using verified evidence; do not guess schemas or retry unchanged inputs. detail_fields contains names whose selectors must be in field_selectors. Stop if the corrected call still fails.",
        }
        if isinstance(exc, DraftValidationError):
            result["details"] = exc.details
            result["example"] = {"illustrative_only": True, "field_selectors": {"title": ".//h3/a", "content": "//main"}, "detail_fields": ["content"], "detail_link_selector": ".//h3/a"}
        return result
    draft_id = drafts.create(spec, page, plan)
    return {
        "status": "ready",
        "draft_id": draft_id,
        "url": url,
        "engine": plan.execution_mode,
        "fields": [field.name for field in spec.fields],
        "allowed_domains": spec.policy.allowed_domains,
        "page_limit": page_limit,
        "item_limit": item_limit,
        "next_action": "Call crawl_draft_execute with this draft_id; do not fetch typed compiler/QA/memory schemas.",
    }


async def _execute_draft_full(
    draft_id: str,
    spec: CrawlSpec,
    page: PageModel,
    plan: CrawlPlan,
    *,
    sample: dict[str, Any] | None,
    headless: bool | None,
    max_wait_seconds: float,
    preview_records: int,
) -> dict[str, Any]:
    result = await crawl_execute_validated(
        spec,
        plan,
        headless=headless,
        max_wait_seconds=max_wait_seconds,
        preview_records=preview_records,
    )
    result.update(draft_id=draft_id, phase="full", sample_is_final=sample is None)
    if sample is not None:
        result.update(sample_execution_id=sample["execution_id"], sample_validation=sample["validation"])
    return _finish_draft_result(draft_id, spec, page, plan, result)


def _finish_draft_result(
    draft_id: str,
    spec: CrawlSpec,
    page: PageModel,
    plan: CrawlPlan,
    result: dict[str, Any],
) -> dict[str, Any]:
    result["requested_record_count"] = spec.traversal.item_limit
    result["requested_list_pages"] = spec.traversal.pagination.max_pages or spec.traversal.max_pages
    _classify_execution_failure(result)
    report = result.get("validation")
    if report is not None:
        evaluated = report.get("metrics", {}).get("record_count", 0)
        result["validation_complete"] = result.get("code") != "policy_blocked" and evaluated >= result.get("record_count", 0)
        if not result["validation_complete"] and result.get("code") != "policy_blocked":
            result["needs_full_validation"] = True
            result["next_action"] = "Only the bounded record prefix was validated; validate the full output deterministically before claiming complete QA or committing memory."
        elif report.get("status") == "FAIL" and report.get("evidence_requests"):
            result["next_action"] = (
                "Use validation.evidence_requests exactly as the bounded reanalysis scope: inspect only the listed "
                "representative URLs and failed fields within each route stratum. Do not fetch a full DOM or reanalyze "
                "successful strata; create a new minimally changed draft after selector evidence is verified."
            )
    if report is not None and report.get("status") == "PASS" and result.get("phase") == "full" and result["validation_complete"] and result.get("code") != "policy_blocked":
        committed = memory_commit_validated(page, plan, spec, result["validation"], task_id=result["task_id"])
        candidates = memory.lookup_workflows(urlparse(spec.start_urls[0]).hostname or "", limit=20)
        workflow_id = next((item["id"] for item in candidates if item["name"] == plan.name and item["easyspider_task_id"] == result["task_id"]), None)
        result.update(memory_committed=True, page_id=committed["page_id"], workflow_id=workflow_id)
    result["needs_reanalysis"] = (
        not result.get("timed_out", False)
        and result.get("code") not in {"policy_blocked", "execution_failed"}
        and (result.get("code") == "selector_no_match" or result.get("validation", {}).get("status") == "FAIL")
    )
    # QA evidence can contain long article values even with preview_records=0.
    # Preserve scores/status/counts, but never echo entire failed bodies into
    # model context or exceed the bounded result cache.
    for key in ("validation", "sample_validation"):
        if key in result:
            result[key] = _validation_receipt(result[key], max_string_chars=500)
    drafts.save_result(draft_id, result)
    return result


@mcp.tool()
async def crawl_draft_execute(
    draft_id: str,
    sample_size: Annotated[int, Field(ge=1, le=50)] = 5,
    headless: bool | None = None,
    max_wait_seconds: Annotated[float, Field(ge=1, le=600)] = 300,
    preview_records: Annotated[int, Field(ge=0, le=3)] = 0,
) -> dict[str, Any]:
    """Execute a prepared draft: sample QA -> full QA -> validated site memory,
    all inside the service. Full data stays in output_files. Repeating a completed
    draft returns its saved result, not another crawl. A timeout resumes the same
    execution on the next call; never construct compiled task JSON yourself.
    """
    spec, page, plan = drafts.get(draft_id)
    previous = drafts.get_result(draft_id)
    if previous is not None:
        if not previous.get("timed_out"):
            return _classify_execution_failure({**previous, "reused_result": True})
        status = await execution_logs(previous["execution_id"], wait_seconds=min(max_wait_seconds, 30))
        if status["status"] in {"created", "starting", "running"}:
            return {**previous, "reused_execution": True}
        previous.pop("timed_out", None)
        previous.pop("next_action", None)
        previous.update(execution_status=status["status"], error=status.get("error"), pages_visited=status.get("pages_visited", previous.get("pages_visited", 0)))
        if isinstance(status.get("failure"), dict):
            previous["failure"] = status["failure"]
        if status["status"] != "completed":
            return _finish_draft_result(draft_id, spec, page, plan, previous)
        result = await adapter.execution_result(previous["execution_id"], limit=1000)
        previous.update(_result_summary(result, preview_records=preview_records, max_field_chars=500))
        validation_spec = spec.model_copy(deep=True)
        if previous.get("phase") == "sample":
            original_sample_size = previous.get("sample_size") or sample_size
            validation_spec.traversal.item_limit = min(spec.traversal.item_limit or original_sample_size, original_sample_size)
        previous["validation"] = validate_records(validation_spec, result.get("records") or []).model_dump(mode="json", exclude_none=True)
        if previous.get("phase") == "full" or previous["validation"]["status"] != "PASS":
            return _finish_draft_result(draft_id, spec, page, plan, previous)
        return await _execute_draft_full(draft_id, spec, page, plan, sample=previous, headless=headless, max_wait_seconds=max_wait_seconds, preview_records=preview_records)

    if spec.traversal.item_limit is not None and spec.traversal.item_limit <= sample_size:
        return await _execute_draft_full(draft_id, spec, page, plan, sample=None, headless=headless, max_wait_seconds=max_wait_seconds, preview_records=preview_records)
    sample = await crawl_execute_validated(spec, plan, sample_size=sample_size, headless=headless, max_wait_seconds=max_wait_seconds, preview_records=0)
    sample.update(draft_id=draft_id, phase="sample", sample_is_final=False)
    if sample.get("validation", {}).get("status") != "PASS":
        return _finish_draft_result(draft_id, spec, page, plan, sample)
    return await _execute_draft_full(draft_id, spec, page, plan, sample=sample, headless=headless, max_wait_seconds=max_wait_seconds, preview_records=preview_records)


@mcp.tool()
async def task_register(task: dict[str, Any]) -> dict[str, Any]:
    """Register one compiled AutoSpider task and return its task ID."""
    task_id = await adapter.task_register(task)
    return {"task_id": task_id}


@mcp.tool()
async def task_get(task_id: int) -> dict[str, Any]:
    """Read an AutoSpider task by task ID."""
    return {"task": await adapter.task_get(task_id)}


@mcp.tool()
async def execution_create(task_id: int, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create an immutable execution instance; task ID and execution ID differ."""
    execution_id = await adapter.execution_create(task_id, parameters or {})
    return {"execution_id": execution_id}


@mcp.tool()
async def execution_start(execution_id: int, headless: bool = True) -> dict[str, Any]:
    """Start an already-created execution instance."""
    return await adapter.execution_start(execution_id, headless=headless)


@mcp.tool()
async def execution_logs(
    execution_id: int,
    wait_seconds: Annotated[float, Field(ge=0, le=30)] = 0,
) -> dict[str, Any]:
    """Read status/error/progress. Use wait_seconds=30 to wait for completion, not tight polling."""
    if not 0 <= wait_seconds <= 30:
        raise ValueError("wait_seconds must be between 0 and 30")
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        status = await adapter.execution_logs(execution_id)
        if status["status"] not in {"starting", "running"} or asyncio.get_running_loop().time() >= deadline:
            return status
        await asyncio.sleep(min(0.5, max(0, deadline - asyncio.get_running_loop().time())))


@mcp.tool()
async def execution_stop(execution_id: int) -> dict[str, Any]:
    """Stop a running execution instance."""
    return await adapter.execution_stop(execution_id)


@mcp.tool()
async def execution_result(execution_id: int, limit: int = 20) -> dict[str, Any]:
    """Resolve an execution ID to parsed output records without filesystem access."""
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    return await adapter.execution_result(execution_id, limit=limit)


@mcp.tool()
async def execution_result_summary(
    execution_id: int,
    preview_records: Annotated[int, Field(ge=0, le=5)] = 1,
    max_field_chars: Annotated[int, Field(ge=100, le=2000)] = 500,
) -> dict[str, Any]:
    """Return status, files and a bounded preview; full records stay on disk."""
    result = await adapter.execution_result(execution_id, limit=max(1, preview_records))
    return _result_summary(
        result,
        preview_records=preview_records,
        max_field_chars=max_field_chars,
    )


@mcp.tool()
def qa_validate(crawl_spec: CrawlSpec, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate sample or final records for completeness, semantics and duplicates."""
    return validate_records(crawl_spec, records).model_dump(mode="json", exclude_none=True)


@mcp.tool()
async def qa_validate_execution(
    execution_id: int,
    crawl_spec: CrawlSpec,
    preview_records: Annotated[int, Field(ge=0, le=5)] = 1,
    max_field_chars: Annotated[int, Field(ge=100, le=2000)] = 500,
) -> dict[str, Any]:
    """Validate an execution server-side without sending its full records to the model."""
    spec = CrawlSpec.model_validate(crawl_spec)
    result = await adapter.execution_result(execution_id, limit=1000)
    records = result.get("records") or []
    report = _validation_receipt(validate_records(spec, records).model_dump(mode="json", exclude_none=True))
    return {
        **_result_summary(
            result,
            preview_records=preview_records,
            max_field_chars=max_field_chars,
        ),
        "validation": report,
        "validation_complete": len(records) >= result.get("record_count", 0),
    }


@mcp.tool()
def memory_lookup_site(
    url: str,
    page_type: str | None = None,
    fingerprint: str | None = None,
    include_page_model: bool = False,
) -> dict[str, Any]:
    """Return small validated-workflow metadata for crawl_reuse_workflow.

    Set include_page_model only when inspecting or repairing selectors; the
    ordinary memory-reuse path does not need full page models or plans.
    """
    domain = urlparse(url).hostname
    if not domain:
        raise ValueError("url must contain a host")
    matches = memory.lookup(domain, page_type=page_type, fingerprint=fingerprint)
    compact_matches = []
    # The newest validated page model is enough for the fast path. Older
    # revisions stay queryable in SQLite but need not consume every model turn.
    for item in matches[:1]:
        compact_matches.append(
            {
                "id": item["id"],
                "url_pattern": item["url_pattern"],
                "page_type": item["page_type"],
                "fingerprint": item["fingerprint"],
                "fingerprint_similarity": item["fingerprint_similarity"],
                "match_kind": item["match_kind"],
                "last_seen": item["last_seen"],
                "field_names": list(item["page_model"].get("fields", {})),
                "recommended_mode": item["page_model"].get("recommended_mode", "playwright"),
                **({"page_model": item["page_model"]} if include_page_model else {}),
            }
        )
    workflows = memory.lookup_workflows(domain, limit=3)
    compact_workflows = []
    for workflow in workflows:
        plan = workflow["crawl_plan"]
        compact_workflows.append(
            {
                "id": workflow["id"],
                "name": workflow["name"],
                "success_rate": workflow["success_rate"],
                "last_success_at": workflow["last_success_at"],
                "version": workflow["version"],
                "task_id": workflow["easyspider_task_id"],
                "plan_summary": {
                    "name": plan.get("name"),
                    "start_urls": plan.get("start_urls", []),
                    "step_count": len(plan.get("steps", [])),
                    "execution_mode": plan.get("execution_mode", "playwright"),
                    "field_names": sorted(_plan_field_names(plan.get("steps", []))),
                },
            }
        )
    return {
        "domain": domain,
        "requested_page_type": page_type,
        "has_memory": bool(compact_matches or compact_workflows),
        "matches": compact_matches,
        "workflows": compact_workflows,
        "recommended_workflow_id": compact_workflows[0]["id"] if compact_workflows else None,
    }


def _plan_field_names(steps: list[dict[str, Any]]) -> set[str]:
    fields = set()
    for step in steps:
        if step.get("op") == "EXTRACT" and step.get("field"):
            fields.add(step["field"])
        fields.update(_plan_field_names(step.get("body", [])))
    return fields


@mcp.tool()
def memory_get_workflow(workflow_id: int) -> dict[str, Any]:
    """Fetch one full validated plan selected from compact site-memory results."""
    try:
        return {"workflow": memory.get_workflow(workflow_id)}
    except KeyError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def memory_commit_validated(
    page_model: PageModel,
    crawl_plan: CrawlPlan,
    crawl_spec: CrawlSpec,
    validation_report: ValidationReport,
    task_id: int | None = None,
    easyspider_task_id: int | None = None,
) -> dict[str, Any]:
    """Persist selectors only after a PASS report; guesses are rejected."""
    report = ValidationReport.model_validate(validation_report)
    if report.status != "PASS":
        raise ValueError("only PASS validation evidence may be committed")
    certificate = report.coverage_certificate
    if certificate is not None and (not certificate.complete or certificate.failed_stratum_field_pairs):
        raise ValueError("failed observed-record coverage evidence may not be committed")
    page = PageModel.model_validate(page_model)
    plan = CrawlPlan.model_validate(crawl_plan)
    spec = CrawlSpec.model_validate(crawl_spec)
    page_id = memory.commit_validated(
        page_model=page.model_dump(mode="json", exclude_none=True),
        crawl_plan=plan.model_dump(mode="json", exclude_none=True),
        crawl_spec_hash=_canonical_hash(spec.model_dump(mode="json", exclude_none=True)),
        workflow_name=plan.name,
        validation_score=report.overall_score,
        easyspider_task_id=task_id if task_id is not None else easyspider_task_id,
    )
    return {"committed": True, "page_id": page_id}


def main() -> None:
    transport = os.getenv("AUTOSPIDER_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    elif transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        raise ValueError("AUTOSPIDER_MCP_TRANSPORT must be stdio or streamable-http")


if __name__ == "__main__":
    main()
