from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree, html

from packages.crawl_core.errors import AdapterError, PolicyViolation, SelectorMatchError
from packages.crawl_core.jsonpath import select_json_path
from packages.crawl_core.models import CrawlPlan, CrawlSpec, HttpRequestModel, Operation, PlanStep
from packages.crawl_core.policies import PolicyEngine
from packages.crawl_core.site_scope import default_allowed_domains
from packages.validator.coverage import select_sample_indices

from .repository import TaskRepository


class _StopPageLoop(Exception):
    pass


@dataclass
class _HttpState:
    execution_id: int
    spec: CrawlSpec
    plan: CrawlPlan
    run_dir: Path
    client: httpx.AsyncClient
    parameters: dict[str, Any]
    sample_size: int | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)
    document: Any = None
    document_format: str = "html"
    current_url: str = ""
    pages_visited: int = 0
    stop_requested: bool = False
    record_hashes: set[str] = field(default_factory=set)
    document_stack: list[tuple[Any, str, str]] = field(default_factory=list)
    robots_checked: set[str] = field(default_factory=set)
    next_cursor: str | int | None = None
    pagination_done: bool = False


class HttpExecutor:
    """Deterministic direct-HTTP CrawlPlan interpreter for HTML and JSON APIs."""

    def __init__(self, repository: TaskRepository, policy: PolicyEngine | None = None):
        self.repository = repository
        self.policy = policy or PolicyEngine()
        self._jobs: dict[int, asyncio.Task[None]] = {}
        self._states: dict[int, _HttpState] = {}

    async def start(self, execution_id: int, *, headless: bool = True) -> dict[str, Any]:
        del headless
        execution = self.repository.get_execution(execution_id)
        engine = execution["task"].get("engine")
        if engine not in {"http_html", "http_api"}:
            raise AdapterError(f"HTTP executor cannot run engine {engine!r}")
        running = self._jobs.get(execution_id)
        if running and not running.done():
            raise AdapterError(f"execution {execution_id} is already running")
        if execution["status"] not in {"created", "failed", "stopped", "interrupted"}:
            raise AdapterError(f"execution {execution_id} cannot start from {execution['status']}")
        self.repository.update_execution(execution_id, status="starting", error=None, failure=None)
        job = asyncio.create_task(self._run(execution_id), name=f"autospider-http-{execution_id}")
        self._jobs[execution_id] = job
        await asyncio.sleep(0)
        return {"started": True, "execution_id": execution_id, "engine": engine}

    async def stop(self, execution_id: int) -> dict[str, Any]:
        job = self._jobs.get(execution_id)
        state = self._states.get(execution_id)
        if state:
            state.stop_requested = True
        if not job or job.done():
            return {"stopped": False, "execution_id": execution_id}
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass
        return {"stopped": True, "execution_id": execution_id}

    async def _run(self, execution_id: int) -> None:
        try:
            execution = self.repository.get_execution(execution_id)
            spec = CrawlSpec.model_validate(execution["task"]["crawl_spec"])
            await asyncio.to_thread(self.policy.check, spec)
            async with asyncio.timeout(spec.execution.max_run_seconds):
                await self._run_http(execution_id)
        except asyncio.CancelledError:
            self.repository.update_execution(execution_id, status="stopped", finished_at=_now())
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.repository.update_execution(
                execution_id, status="failed", finished_at=_now(), error=error,
                failure=exc.diagnostic if isinstance(exc, SelectorMatchError) else None,
            )
            path = self.repository.run_dir(execution_id) / "execution.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{_now()} ERROR {error}\n")
        finally:
            self._states.pop(execution_id, None)
            self._jobs.pop(execution_id, None)

    async def _run_http(self, execution_id: int) -> None:
        execution = self.repository.get_execution(execution_id)
        spec = CrawlSpec.model_validate(execution["task"]["crawl_spec"])
        plan = CrawlPlan.model_validate(execution["task"]["crawl_plan"])
        parameters = execution.get("parameters", {})
        for name in ("http_params", "http_json", "http_form"):
            sensitive = _sensitive_keys(parameters.get(name))
            if sensitive:
                raise AdapterError(
                    f"secret request parameters cannot be stored in {name}: "
                    + ", ".join(sorted(sensitive))
                )
        headers = {"User-Agent": str(parameters.get("user_agent", "AutoSpider/0.1"))}
        headers.update(plan.http_request.headers if plan.http_request else {})
        runtime_headers = parameters.get("http_headers")
        if runtime_headers is not None:
            if not isinstance(runtime_headers, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in runtime_headers.items()
            ):
                raise AdapterError("http_headers must be a string-to-string object")
            forbidden = {
                "authorization",
                "cookie",
                "proxy-authorization",
                "x-api-key",
                "api-key",
            }
            if any(key.lower() in forbidden for key in runtime_headers):
                raise AdapterError(
                    "secret HTTP headers cannot be stored in execution parameters; use playwright browser state"
                )
            headers.update(runtime_headers)

        timeout = plan.http_request.timeout_seconds if plan.http_request else 30
        self.repository.update_execution(execution_id, status="running", started_at=_now())
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as client:
            task = execution["task"]
            state = _HttpState(
                execution_id,
                spec,
                plan,
                self.repository.run_dir(execution_id),
                client,
                parameters,
                sample_size=task.get("sample_size") if task.get("sample") else None,
            )
            self._states[execution_id] = state
            if plan.http_request and plan.http_request.pagination.type == "cursor":
                state.next_cursor = plan.http_request.pagination.initial_cursor
            await self._log(state, f"execution started: {plan.execution_mode}")
            for start_url in _start_urls(plan.start_urls, parameters):
                state.parameters["start_url"] = start_url
                state.record = {}
                await self._execute_steps(state, plan.steps, state.document)
                if state.record:
                    self._append_record(state, state.record)
                    state.record = {}
            await self._write_results(state)
            await self._log(state, f"execution completed: {len(state.records)} records")
        self.repository.update_execution(
            execution_id,
            status="completed",
            finished_at=_now(),
            record_count=len(state.records),
            pages_visited=state.pages_visited,
        )

    async def _execute_steps(self, state: _HttpState, steps: list[PlanStep], scope: Any) -> bool:
        changed = False
        for step in steps:
            if state.stop_requested:
                raise asyncio.CancelledError
            changed = await self._execute_step(state, step, scope) or changed
        return changed

    async def _execute_step(self, state: _HttpState, step: PlanStep, scope: Any) -> bool:
        await self._log(state, f"{step.op.value} {step.id or step.field or step.effective_selector or ''}".rstrip())
        target_scope = scope if step.relative_selector is not None else state.document
        if step.frame_selector:
            raise AdapterError("frame selectors require playwright mode")
        if step.op == Operation.OPEN_URL:
            url = _template(step.url or "", state.parameters, state.record)
            await self._load(state, url, use_request_model=step.id in {"open", "api_page"})
            return True
        if step.op == Operation.WAIT:
            if step.selector and not self._select(state, state.document, step.selector_type, step.selector, step.fallbacks):
                raise AdapterError(f"selector matched no values: {step.selector}")
            return False
        if step.op == Operation.EXTRACT:
            values = self._select(
                state,
                target_scope,
                step.selector_type,
                step.effective_selector or "",
                step.fallbacks,
            )
            if not values:
                field_spec = next((item for item in state.spec.fields if item.name == step.field), None)
                if field_spec and not field_spec.required:
                    state.record[step.field or "value"] = None
                    return False
                raise SelectorMatchError(
                    field=step.field or "value", selector=step.effective_selector or "",
                    selector_type=step.selector_type, url=state.current_url,
                    page_scope=field_spec.page_scope if field_spec else "auto",
                )
            state.record[step.field or "value"] = self._extract(state, values[0], step)
            return False
        if step.op == Operation.CLICK:
            values = self._select(
                state,
                target_scope,
                step.selector_type,
                step.effective_selector or "",
                step.fallbacks,
            )
            if not values:
                if step.id == "next":
                    raise _StopPageLoop
                raise AdapterError(f"selector matched no values: {step.effective_selector}")
            href = _link_value(values[0], state.document_format)
            if not href:
                raise AdapterError("direct HTTP CLICK requires an href or URL value; use playwright for buttons")
            if step.id != "next":
                state.document_stack.append((state.document, state.document_format, state.current_url))
            await self._load(state, urljoin(state.current_url, str(href)), use_request_model=False)
            return True
        if step.op == Operation.BACK:
            if not state.document_stack:
                raise AdapterError("HTTP BACK has no saved document")
            state.document, state.document_format, state.current_url = state.document_stack.pop()
            if step.seconds:
                await asyncio.sleep(step.seconds)
            return True
        if step.op == Operation.LOOP_ELEMENTS:
            return await self._loop_elements(state, step, scope)
        if step.op == Operation.LOOP_PAGES:
            return await self._loop_pages(state, step)
        if step.op == Operation.CONDITION:
            if not step.effective_selector:
                raise AdapterError("CONDITION requires a selector")
            if self._select(state, target_scope, step.selector_type, step.effective_selector, step.fallbacks):
                return await self._execute_steps(state, step.body, target_scope)
            return False
        if step.op in {
            Operation.INPUT,
            Operation.SELECT,
            Operation.PRESS,
            Operation.MOVE,
            Operation.SCROLL,
            Operation.SCREENSHOT,
            Operation.DOWNLOAD,
            Operation.CUSTOM,
        }:
            raise AdapterError(f"{step.op.value} requires playwright mode")
        raise AdapterError(f"unsupported direct-HTTP operation: {step.op}")

    async def _loop_elements(self, state: _HttpState, step: PlanStep, scope: Any) -> bool:
        items = self._select(state, scope if scope is not None else state.document, step.selector_type, step.effective_selector or "", [])
        candidate_count = min(len(items), step.max_iterations or 10_000)
        indices = (
            select_sample_indices(
                candidate_count,
                state.sample_size,
                state.spec.execution.sample_strategy,
            )
            if state.sample_size is not None
            else list(range(min(candidate_count, state.spec.traversal.item_limit or 10_000)))
        )
        for index in indices:
            if len(state.records) >= (state.spec.traversal.item_limit or 10_000):
                break
            item = items[index]
            state.record = {}
            await self._execute_steps(state, step.body, item)
            self._append_record(state, state.record)
            state.record = {}
        return bool(items)

    async def _loop_pages(self, state: _HttpState, step: PlanStep) -> bool:
        seen: set[str] = set()
        changed = False
        pagination = state.plan.http_request.pagination if state.plan.http_request else None
        for index in range(step.max_iterations or 1):
            state.parameters["page_number"] = index + 1
            state.parameters["next_page_number"] = index + 2
            before = len(state.records)
            try:
                for child in step.body:
                    if len(state.records) >= (state.spec.traversal.item_limit or 10_000):
                        break
                    # The final iteration only needs its current page; do not
                    # fetch an unused next page after collecting its records.
                    if index + 1 == (step.max_iterations or 1) and child.id == "next" and child.op in {
                        Operation.CLICK, Operation.OPEN_URL
                    }:
                        continue
                    await self._execute_step(state, child, state.document)
            except _StopPageLoop:
                break
            signature = self._document_signature(state)
            if signature in seen:
                break
            seen.add(signature)
            changed = changed or len(state.records) > before
            if len(state.records) >= (state.spec.traversal.item_limit or 10_000):
                break
            if len(state.records) == before:
                break
            if pagination and pagination.type == "cursor" and state.pagination_done:
                break
        return changed

    async def _load(self, state: _HttpState, url: str, *, use_request_model: bool) -> None:
        if state.pages_visited:
            await self._delay(state)
        request_model = state.plan.http_request if use_request_model else None
        method = request_model.method if request_model else "GET"
        params = dict(request_model.params) if request_model else {}
        json_body = _copy_json(request_model.json_body) if request_model else None
        form_body = dict(request_model.form_body) if request_model and request_model.form_body else None
        if request_model:
            params.update(_runtime_object(state.parameters.get("http_params"), "http_params"))
            if state.parameters.get("http_json") is not None:
                json_body = _copy_json(state.parameters["http_json"])
            if state.parameters.get("http_form") is not None:
                form_body = _runtime_object(state.parameters["http_form"], "http_form")
            params, json_body, form_body = self._apply_pagination(
                state,
                request_model,
                params,
                json_body,
                form_body,
            )
        response = await self._request(
            state,
            method,
            url,
            params=params,
            json_body=json_body,
            form_body=form_body,
        )
        response_format = request_model.response_format if request_model else "auto"
        state.document, state.document_format = _parse_response(response, response_format)
        state.current_url = str(response.url)
        state.pages_visited += 1
        if request_model and request_model.pagination.type == "cursor":
            values = select_json_path(state.document, request_model.pagination.next_cursor_path or "")
            state.next_cursor = values[0] if values else None
            state.pagination_done = state.next_cursor in {None, ""}

    def _apply_pagination(
        self,
        state: _HttpState,
        request_model: HttpRequestModel,
        params: dict[str, Any],
        json_body: Any,
        form_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], Any, dict[str, Any] | None]:
        pagination = request_model.pagination
        if pagination.type == "none":
            return params, json_body, form_body
        if pagination.type == "page":
            value: Any = pagination.start_page + int(state.parameters.get("page_number", 1)) - 1
            name = pagination.page_param or "page"
        else:
            value = state.next_cursor
            name = pagination.cursor_param or "cursor"
        params, json_body, form_body = _inject_parameter(
            pagination.parameter_location,
            name,
            value,
            params,
            json_body,
            form_body,
        )
        if pagination.page_size_param and pagination.page_size is not None:
            params, json_body, form_body = _inject_parameter(
                pagination.parameter_location,
                pagination.page_size_param,
                pagination.page_size,
                params,
                json_body,
                form_body,
            )
        return params, json_body, form_body

    async def _request(
        self,
        state: _HttpState,
        method: str,
        url: str,
        *,
        params: dict[str, Any],
        json_body: Any,
        form_body: dict[str, Any] | None,
    ) -> httpx.Response:
        allowed = state.spec.policy.allowed_domains or default_allowed_domains(state.spec.start_urls)
        current_url = url
        current_method = method
        for redirect_count in range(11):
            # HTTPX's params argument replaces the URL query, even for {}.
            # Merge explicit overrides once, then validate and send the same URL.
            # A redirect's Location must not inherit the original overrides.
            request_url = str(
                httpx.URL(current_url).copy_merge_params(params)
                if redirect_count == 0 and params
                else httpx.URL(current_url)
            )
            await asyncio.to_thread(
                self.policy.check_url,
                request_url,
                allowed_domains=allowed,
                block_private_networks=state.spec.policy.block_private_networks,
            )
            robots_key = _robots_key(request_url)
            if state.spec.policy.respect_robots and robots_key not in state.robots_checked:
                if not await asyncio.to_thread(self.policy.robots_allowed, request_url):
                    raise PolicyViolation(f"robots.txt does not allow AutoSpider: {request_url}")
                state.robots_checked.add(robots_key)
            response = await state.client.request(
                current_method,
                request_url,
                json=json_body if redirect_count == 0 else None,
                data=form_body if redirect_count == 0 else None,
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise AdapterError("HTTP redirect has no Location header")
                current_url = urljoin(str(response.url), location)
                if response.status_code == 303 or (response.status_code in {301, 302} and current_method == "POST"):
                    current_method = "GET"
                continue
            response.raise_for_status()
            max_bytes = int(os.getenv("AUTOSPIDER_HTTP_MAX_RESPONSE_BYTES", "20000000"))
            if len(response.content) > max_bytes:
                raise AdapterError(f"HTTP response exceeds {max_bytes} bytes")
            return response
        raise AdapterError("too many HTTP redirects")

    def _select(
        self,
        state: _HttpState,
        scope: Any,
        selector_type: str,
        selector: str,
        fallbacks: list[str],
    ) -> list[Any]:
        for candidate in (selector, *fallbacks):
            try:
                if selector_type == "jsonpath":
                    values = select_json_path(scope, candidate)
                elif selector_type == "xpath":
                    values = list(scope.xpath(candidate)) if scope is not None else []
                elif selector_type == "css":
                    values = list(scope.cssselect(candidate)) if scope is not None else []
                else:
                    raise AdapterError(f"unsupported selector type: {selector_type}")
            except (etree.XPathError, ValueError) as exc:
                raise AdapterError(f"invalid {selector_type} selector {candidate!r}: {exc}") from exc
            if values:
                return values
        return []

    def _extract(self, state: _HttpState, value: Any, step: PlanStep) -> Any:
        if step.source == "url":
            result: Any = state.current_url
        elif state.document_format == "json" or step.selector_type == "jsonpath":
            if step.source == "html" and isinstance(value, (dict, list)):
                result = json.dumps(value, ensure_ascii=False)
            else:
                result = value
        elif step.source == "attribute":
            result = value.get(step.attribute or "")
        elif step.source == "html":
            result = "".join(html.tostring(child, encoding="unicode") for child in value)
        else:
            result = " ".join(part.strip() for part in value.itertext() if part.strip())
        if isinstance(result, str):
            field = next((item for item in state.spec.fields if item.name == step.field), None)
            if field and field.type == "url":
                result = urljoin(state.current_url, result)
            if field and field.normalize.trim:
                result = result.strip()
            if field and field.normalize.regex:
                match = re.search(field.normalize.regex, result)
                result = match.group(1) if match and match.lastindex else (match.group(0) if match else None)
        return result

    def _append_record(self, state: _HttpState, record: dict[str, Any]) -> None:
        if not record or len(state.records) >= (state.spec.traversal.item_limit or 10_000):
            return
        digest = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        if digest in state.record_hashes:
            return
        state.record_hashes.add(digest)
        state.records.append(dict(record))
        self.repository.update_execution(
            state.execution_id,
            record_count=len(state.records),
            pages_visited=state.pages_visited,
        )

    async def _delay(self, state: _HttpState) -> None:
        delay = int(state.parameters.get("request_delay_ms", state.spec.execution.request_delay_ms))
        if delay:
            await asyncio.sleep(delay / 1000)

    @staticmethod
    def _document_signature(state: _HttpState) -> str:
        if state.document_format == "json":
            body = json.dumps(state.document, ensure_ascii=False, sort_keys=True)
        else:
            body = html.tostring(state.document, encoding="unicode")
        return hashlib.sha256(body.encode()).hexdigest()

    async def _log(self, state: _HttpState, message: str) -> None:
        path = state.run_dir / "execution.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{_now()} {message}\n")

    async def _write_results(self, state: _HttpState) -> None:
        payload = json.dumps(state.records, ensure_ascii=False, indent=2) + "\n"
        if len(payload.encode("utf-8")) > state.spec.execution.max_output_bytes:
            raise AdapterError(f"output exceeds {state.spec.execution.max_output_bytes} bytes")
        (state.run_dir / "result.json").write_text(payload, encoding="utf-8")
        output = state.spec.output
        if output.format == "json" and output.filename and output.filename != "result.json":
            (state.run_dir / output.filename).write_text(payload, encoding="utf-8")
        elif output.format == "csv":
            _write_csv(state.run_dir / (output.filename or f"{state.plan.name}.csv"), state.records)
        elif output.format == "xlsx":
            _write_xlsx(state.run_dir / (output.filename or f"{state.plan.name}.xlsx"), state.records)


def _parse_response(response: httpx.Response, requested_format: str) -> tuple[Any, str]:
    content_type = response.headers.get("content-type", "").lower()
    response_format = requested_format
    if response_format == "auto":
        response_format = "json" if "json" in content_type else "html"
    if response_format == "json":
        try:
            return response.json(), "json"
        except json.JSONDecodeError as exc:
            raise AdapterError(f"response is not valid JSON: {response.url}") from exc
    try:
        return html.fromstring(response.text), "html"
    except (ValueError, etree.ParserError) as exc:
        raise AdapterError(f"response is not valid HTML: {response.url}") from exc


def _link_value(value: Any, document_format: str) -> str | None:
    if document_format == "html" and hasattr(value, "get"):
        return value.get("href") or value.get("src")
    if isinstance(value, (str, int)):
        return str(value)
    return None


def _inject_parameter(location, name, value, params, json_body, form_body):
    if location == "query":
        params[name] = value
    elif location == "json":
        if json_body is None:
            json_body = {}
        if not isinstance(json_body, dict):
            raise AdapterError("JSON pagination requires an object json_body")
        json_body[name] = value
    else:
        if form_body is None:
            form_body = {}
        form_body[name] = value
    return params, json_body, form_body


def _runtime_object(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AdapterError(f"{name} must be an object")
    return dict(value)


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False)) if value is not None else None


def _robots_key(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _start_urls(defaults: list[str], parameters: dict[str, Any]) -> list[str]:
    value = parameters.get("start_urls", parameters.get("urlList_0"))
    if value is None:
        return defaults
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise AdapterError("start_urls/urlList_0 must be a URL list or newline-delimited string")


def _template(value: str, parameters: dict[str, Any], record: dict[str, Any]) -> str:
    merged = {**parameters, **record}
    return re.sub(r"{{\s*([^{}]+?)\s*}}", lambda match: str(merged.get(match.group(1), match.group(0))), value)


def _sensitive_keys(value: Any) -> set[str]:
    forbidden = {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "key",
        "password",
        "proxy_authorization",
        "secret",
        "sig",
        "signature",
        "token",
        "x_api_key",
    }
    if isinstance(value, dict):
        found = {
            str(key)
            for key in value
            if str(key).strip().lower().replace("-", "_") in forbidden
        }
        for child in value.values():
            found.update(_sensitive_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_sensitive_keys(child))
        return found
    return set()


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for record in records for key in record))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def _write_xlsx(path: Path, records: list[dict[str, Any]]) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise AdapterError("install the 'xlsx' extra to write XLSX results") from exc
    fields = list(dict.fromkeys(key for record in records for key in record))
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(fields)
    for record in records:
        sheet.append([record.get(name) for name in fields])
    workbook.save(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
