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

from playwright.async_api import BrowserContext, Error as PlaywrightError, Locator, Page, async_playwright

from packages.crawl_core.errors import AdapterError, PolicyViolation, SelectorMatchError
from packages.crawl_core.models import CrawlPlan, CrawlSpec, Operation, PlanStep
from packages.crawl_core.policies import PolicyEngine, assert_public_host, host_allowed
from packages.crawl_core.site_scope import default_allowed_domains
from packages.validator.coverage import select_sample_indices

from .repository import TaskRepository


class _StopPageLoop(Exception):
    pass


@dataclass
class _RunState:
    execution_id: int
    spec: CrawlSpec
    plan: CrawlPlan
    run_dir: Path
    context: BrowserContext
    page: Page
    parameters: dict[str, Any]
    sample_size: int | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)
    log_lines: list[str] = field(default_factory=list)
    pages_visited: int = 0
    stop_requested: bool = False
    record_hashes: set[str] = field(default_factory=set)
    page_stack: list[Page] = field(default_factory=list)
    navigation_policy_error: PolicyViolation | None = None


class PlaywrightExecutor:
    """Deterministic CrawlPlan interpreter backed by Playwright."""

    def __init__(self, repository: TaskRepository, policy: PolicyEngine | None = None):
        self.repository = repository
        self.policy = policy or PolicyEngine()
        self._jobs: dict[int, asyncio.Task[None]] = {}
        self._states: dict[int, _RunState] = {}

    async def start(self, execution_id: int, *, headless: bool = True) -> dict[str, Any]:
        execution = self.repository.get_execution(execution_id)
        running = self._jobs.get(execution_id)
        if running and not running.done():
            raise AdapterError(f"execution {execution_id} is already running")
        if execution["status"] not in {"created", "failed", "stopped", "interrupted"}:
            raise AdapterError(f"execution {execution_id} cannot start from {execution['status']}")
        self.repository.update_execution(execution_id, status="starting", error=None, failure=None)
        job = asyncio.create_task(self._run(execution_id, headless=headless), name=f"autospider-{execution_id}")
        self._jobs[execution_id] = job
        await asyncio.sleep(0)
        return {"started": True, "execution_id": execution_id, "engine": "playwright"}

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

    async def _run(self, execution_id: int, *, headless: bool) -> None:
        # Preflight belongs inside the lifecycle boundary too: DNS/policy/schema
        # errors must never leave a persisted execution permanently starting.
        try:
            async with asyncio.timeout(60):
                execution = self.repository.get_execution(execution_id)
                spec = CrawlSpec.model_validate(execution["task"]["crawl_spec"])
                await asyncio.to_thread(self.policy.check, spec)
            async with asyncio.timeout(spec.execution.max_run_seconds):
                await self._run_browser(execution_id, headless=headless)
        except asyncio.CancelledError:
            self.repository.update_execution(execution_id, status="stopped", finished_at=_now())
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.repository.update_execution(execution_id, status="failed", finished_at=_now(), error=error)
            path = self.repository.run_dir(execution_id) / "execution.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{_now()} ERROR {error}\n")
        finally:
            self._states.pop(execution_id, None)
            self._jobs.pop(execution_id, None)

    async def _run_browser(self, execution_id: int, *, headless: bool) -> None:
        execution = self.repository.get_execution(execution_id)
        task = execution["task"]
        spec = CrawlSpec.model_validate(task["crawl_spec"])
        plan = CrawlPlan.model_validate(task["crawl_plan"])
        parameters = execution.get("parameters", {})
        run_dir = self.repository.run_dir(execution_id)
        started_at = _now()
        self.repository.update_execution(execution_id, status="running", started_at=started_at)

        try:
            async with async_playwright() as playwright:
                browser_type = getattr(playwright, str(parameters.get("browser", "chromium")), None)
                if browser_type is None:
                    raise AdapterError("browser must be chromium, firefox or webkit")
                proxy = _proxy_options(parameters)
                browser = await browser_type.launch(headless=headless, proxy=proxy, timeout=45_000)
                context_options: dict[str, Any] = {
                    "accept_downloads": bool(parameters.get("accept_downloads", True)),
                    "ignore_https_errors": bool(parameters.get("ignore_https_errors", False)),
                }
                for name in ("user_agent", "locale", "timezone_id"):
                    if parameters.get(name):
                        context_options[name] = parameters[name]
                if parameters.get("storage_state"):
                    storage_state = parameters["storage_state"]
                    if isinstance(storage_state, str):
                        storage_path = Path(storage_state).resolve()
                        if not storage_path.is_relative_to(self.repository.root):
                            raise AdapterError("storage_state path must be inside AUTOSPIDER_RUNTIME_ROOT")
                        storage_state = str(storage_path)
                    context_options["storage_state"] = storage_state
                context = await browser.new_context(**context_options)
                try:
                    page = await context.new_page()
                    state = _RunState(
                        execution_id,
                        spec,
                        plan,
                        run_dir,
                        context,
                        page,
                        parameters,
                        sample_size=task.get("sample_size") if task.get("sample") else None,
                    )
                    self._states[execution_id] = state
                    await self._install_navigation_guard(state)
                    await self._log(state, "execution started")
                    timeout = min(spec.execution.max_run_seconds, int(parameters.get("max_run_seconds", spec.execution.max_run_seconds)))
                    async with asyncio.timeout(timeout):
                        for start_url in _start_urls(plan.start_urls, parameters):
                            if spec.policy.respect_robots and not await asyncio.to_thread(self.policy.robots_allowed, start_url):
                                raise PolicyViolation(f"robots.txt does not allow AutoSpider: {start_url}")
                            state.parameters["start_url"] = start_url
                            state.record = {}
                            await self._execute_steps(state, plan.steps, page)
                            if state.record:
                                self._append_record(state, state.record)
                                state.record = {}
                    await self._write_results(state)
                    await self._log(state, f"execution completed: {len(state.records)} records")
                finally:
                    await context.close()
                    await browser.close()
            self.repository.update_execution(
                execution_id,
                status="completed",
                finished_at=_now(),
                record_count=len(state.records),
                pages_visited=state.pages_visited,
            )
        except asyncio.CancelledError:
            self.repository.update_execution(execution_id, status="stopped", finished_at=_now())
            raise
        except Exception as exc:
            state = self._states.get(execution_id)
            if state is not None and state.navigation_policy_error is not None and isinstance(exc, PlaywrightError):
                exc = state.navigation_policy_error
            if state is not None:
                await self._log(state, f"ERROR {type(exc).__name__}: {exc}")
            self.repository.update_execution(
                execution_id,
                status="failed",
                finished_at=_now(),
                error=f"{type(exc).__name__}: {exc}",
                failure=exc.diagnostic if isinstance(exc, SelectorMatchError) else None,
            )
        finally:
            self._states.pop(execution_id, None)

    async def _install_navigation_guard(self, state: _RunState) -> None:
        allowed = state.spec.policy.allowed_domains or default_allowed_domains(state.spec.start_urls)

        async def guard(route) -> None:
            request = route.request
            parsed = urlparse(request.url)
            is_navigation = request.is_navigation_request()
            # Optional embedded frames can be blocked without poisoning the
            # article extraction; a top-level navigation/popup is required.
            try:
                frame = getattr(request, "frame", None) if is_navigation else None
            except PlaywrightError:
                # A popup navigation can arrive before its Frame is created.
                # Treat it as required, not an optional embedded resource.
                frame = None
            is_required_navigation = is_navigation and (frame is None or frame.parent_frame is None)
            if parsed.scheme in {"http", "https"}:
                if is_navigation and not state.spec.policy.allow_cross_domain_navigation and parsed.hostname and not host_allowed(parsed.hostname, allowed):
                    if is_required_navigation:
                        state.navigation_policy_error = PolicyViolation(f"domain {parsed.hostname!r} is not in allowed_domains")
                    await route.abort("blockedbyclient")
                    return
                if is_required_navigation:
                    state.navigation_policy_error = None
                if state.spec.policy.block_private_networks and parsed.hostname:
                    try:
                        await asyncio.to_thread(assert_public_host, parsed.hostname)
                    except PolicyViolation as exc:
                        if is_required_navigation:
                            state.navigation_policy_error = exc
                        await route.abort("blockedbyclient")
                        return
            await route.continue_()

        await state.context.route("**/*", guard)

    async def _execute_steps(self, state: _RunState, steps: list[PlanStep], scope: Page | Locator) -> bool:
        changed = False
        for step in steps:
            if state.stop_requested:
                raise asyncio.CancelledError
            changed = await self._execute_step(state, step, scope) or changed
            if state.navigation_policy_error is not None:
                raise state.navigation_policy_error
        return changed

    async def _execute_step(self, state: _RunState, step: PlanStep, scope: Page | Locator) -> bool:
        await self._log(state, f"{step.op.value} {step.id or step.field or step.effective_selector or ''}".rstrip())
        # A relative selector belongs to the current loop item. An ordinary
        # selector is page-scoped, which is especially important after a
        # list-item click navigates to a detail page.
        target_scope = scope if step.relative_selector is not None else state.page
        if step.frame_selector:
            frame_prefix = "xpath=" if step.frame_selector_type == "xpath" else "css="
            target_scope = state.page.frame_locator(frame_prefix + step.frame_selector)
        if step.op == Operation.OPEN_URL:
            url = _template(step.url or "", state.parameters, state.record)
            await state.page.goto(url, wait_until="domcontentloaded")
            state.pages_visited += 1
            return True
        if step.op == Operation.WAIT:
            if step.selector:
                async with asyncio.timeout(step.seconds or 10):
                    while True:
                        try:
                            await self._find(target_scope, step.selector_type, step.selector, step.fallbacks)
                            break
                        except AdapterError:
                            await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(step.seconds or 0)
            return False
        if step.op == Operation.EXTRACT:
            try:
                locator = await self._find(target_scope, step.selector_type, step.effective_selector or "", step.fallbacks)
            except AdapterError as exc:
                field_spec = next((item for item in state.spec.fields if item.name == step.field), None)
                if field_spec and not field_spec.required:
                    state.record[step.field or "value"] = None
                    await self._log(state, f"optional field absent: {step.field}")
                    return False
                raise SelectorMatchError(
                    field=step.field or "value", selector=step.effective_selector or "",
                    selector_type=step.selector_type, url=state.page.url,
                    page_scope=field_spec.page_scope if field_spec else "auto",
                ) from exc
            state.record[step.field or "value"] = await self._extract(state, locator.first, step)
            return False
        if step.op == Operation.INPUT:
            locator = await self._find(target_scope, step.selector_type, step.effective_selector or "", step.fallbacks)
            await locator.first.fill(_template(step.value or "", state.parameters, state.record))
            return True
        if step.op == Operation.SELECT:
            locator = await self._find(target_scope, step.selector_type, step.effective_selector or "", step.fallbacks)
            await locator.first.select_option(_template(step.value or "", state.parameters, state.record))
            return True
        if step.op == Operation.PRESS:
            locator = await self._find(target_scope, step.selector_type, step.effective_selector or "", step.fallbacks)
            await locator.first.press(step.value or "")
            return True
        if step.op == Operation.MOVE:
            locator = await self._find(target_scope, step.selector_type, step.effective_selector or "", step.fallbacks)
            await locator.first.hover()
            return True
        if step.op == Operation.CLICK:
            try:
                locator = await self._find(target_scope, step.selector_type, step.effective_selector or "", step.fallbacks)
            except AdapterError:
                if step.id == "next":
                    raise _StopPageLoop
                raise
            old_url = state.page.url
            old_pages = set(state.context.pages)
            target = (await locator.first.get_attribute("target") or "").lower()
            if target == "_blank":
                async with state.context.expect_page(timeout=10_000) as pending_page:
                    await locator.first.click()
                await pending_page.value
            else:
                await locator.first.click()
                await asyncio.sleep(0.1)
            new_pages = [page for page in state.context.pages if page not in old_pages]
            if new_pages:
                state.page_stack.append(state.page)
                state.page = new_pages[-1]
                await state.page.wait_for_load_state("domcontentloaded")
            try:
                await state.page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
            if state.page.url != old_url:
                state.pages_visited += 1
            await self._delay(state)
            return True
        if step.op == Operation.BACK:
            if state.page_stack:
                await state.page.close()
                state.page = state.page_stack.pop()
            else:
                await state.page.go_back(wait_until="domcontentloaded")
            state.pages_visited += 1
            if step.seconds:
                await asyncio.sleep(step.seconds)
            return True
        if step.op == Operation.LOOP_ELEMENTS:
            return await self._loop_elements(state, step, scope)
        if step.op == Operation.LOOP_PAGES:
            return await self._loop_pages(state, step, scope)
        if step.op == Operation.SCROLL:
            if step.effective_selector:
                locator = await self._find(target_scope, step.selector_type, step.effective_selector, step.fallbacks)
                await locator.first.scroll_into_view_if_needed()
            elif (step.value or "").lower() in {"end", "bottom"}:
                await state.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                await state.page.mouse.wheel(0, int(step.value or "800"))
            if step.seconds:
                await asyncio.sleep(step.seconds)
            return True
        if step.op == Operation.SCREENSHOT:
            filename = Path(step.value or f"screenshot-{len(list(state.run_dir.glob('screenshot-*.png'))) + 1}.png").name
            if not filename.lower().endswith(".png"):
                filename += ".png"
            if step.effective_selector:
                locator = await self._find(target_scope, step.selector_type, step.effective_selector, step.fallbacks)
                await locator.first.screenshot(path=state.run_dir / filename)
            else:
                await state.page.screenshot(path=state.run_dir / filename, full_page=True)
            return False
        if step.op == Operation.DOWNLOAD:
            locator = await self._find(target_scope, step.selector_type, step.effective_selector or "", step.fallbacks)
            async with state.page.expect_download() as pending:
                await locator.first.click()
            download = await pending.value
            download_dir = state.run_dir / "downloads"
            download_dir.mkdir(parents=True, exist_ok=True)
            filename = Path(step.value or download.suggested_filename).name
            await download.save_as(download_dir / filename)
            await self._log(state, f"download saved: {filename}")
            return True
        if step.op == Operation.CONDITION:
            if not step.effective_selector:
                raise AdapterError("CONDITION requires a selector")
            locator = _locator(target_scope, step.selector_type, step.effective_selector)
            if await locator.count():
                return await self._execute_steps(state, step.body, scope)
            return False
        if step.op == Operation.CUSTOM:
            if os.getenv("AUTOSPIDER_ALLOW_CUSTOM_CODE", "0") != "1":
                raise AdapterError("CUSTOM JavaScript is disabled; set AUTOSPIDER_ALLOW_CUSTOM_CODE=1")
            await state.page.evaluate(step.code or "")
            return True
        raise AdapterError(f"unsupported CrawlPlan operation: {step.op}")

    async def _loop_elements(self, state: _RunState, step: PlanStep, scope: Page | Locator) -> bool:
        locator = _locator(scope, step.selector_type, step.effective_selector or "")
        available = await locator.count()
        candidate_count = min(available, step.max_iterations or 10_000)
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
            items = _locator(scope, step.selector_type, step.effective_selector or "")
            if index >= await items.count():
                break
            state.record = {}
            await self._execute_steps(state, step.body, items.nth(index))
            self._append_record(state, state.record)
            state.record = {}
        return bool(indices)

    async def _loop_pages(self, state: _RunState, step: PlanStep, scope: Page | Locator) -> bool:
        seen: set[str] = set()
        changed = False
        url_driven = any(child.op == Operation.OPEN_URL for child in step.body)
        items_step = next((child for child in step.body if child.op == Operation.LOOP_ELEMENTS), None)
        for index in range(step.max_iterations or 1):
            state.parameters["page_number"] = index + 1
            state.parameters["next_page_number"] = index + 2
            signature = await self._page_signature(state.page, items_step)
            if signature in seen and not url_driven:
                break
            seen.add(signature)
            try:
                for child in step.body:
                    if len(state.records) >= (state.spec.traversal.item_limit or 10_000):
                        return changed
                    # Do not load an unused page after a sample/final page.
                    if index + 1 == (step.max_iterations or 1) and (
                        child.id == "next" or child.op == Operation.SCROLL
                    ):
                        break
                    if child.id == "next" or child.op == Operation.SCROLL:
                        # Initial WAIT may have populated an empty client-side
                        # list since the beginning of this iteration.
                        signature = await self._page_signature(state.page, items_step)
                    changed = await self._execute_step(state, child, state.page) or changed
            except _StopPageLoop:
                break
            if len(state.records) >= (state.spec.traversal.item_limit or 10_000):
                break
            if not url_driven and index + 1 < (step.max_iterations or 1):
                # Load-more often appends asynchronously and leaves the first
                # 2,000 characters unchanged. Compare all visible content.
                deadline = asyncio.get_running_loop().time() + 10
                while await self._page_signature(state.page, items_step) == signature:
                    if asyncio.get_running_loop().time() >= deadline:
                        await self._log(state, "pagination stopped: no page-content change within 10 seconds")
                        return changed
                    await asyncio.sleep(0.2)
        return changed

    async def _find(self, scope: Page | Locator, selector_type: str, selector: str, fallbacks: list[str]) -> Locator:
        errors: list[str] = []
        for candidate in (selector, *fallbacks):
            try:
                locator = _locator(scope, selector_type, candidate)
                if await locator.count():
                    return locator
            except Exception as exc:
                errors.append(str(exc))
        suffix = f" ({'; '.join(errors)})" if errors else ""
        raise AdapterError(f"selector matched no elements: {selector}{suffix}")

    async def _extract(self, state: _RunState, locator: Locator, step: PlanStep) -> Any:
        if step.source == "text":
            value = await locator.inner_text()
        elif step.source == "html":
            value = await locator.inner_html()
        elif step.source == "url":
            value = state.page.url
        else:
            value = await locator.get_attribute(step.attribute or "")
        if value is not None and step.source == "attribute" and step.attribute in {"href", "src"}:
            value = urljoin(state.page.url, value)
        field = next((item for item in state.spec.fields if item.name == step.field), None)
        if isinstance(value, str) and field:
            if field.normalize.trim:
                value = value.strip()
            if field.normalize.regex:
                match = re.search(field.normalize.regex, value)
                value = match.group(1) if match and match.lastindex else (match.group(0) if match else None)
        return value

    def _append_record(self, state: _RunState, record: dict[str, Any]) -> None:
        if not record:
            return
        if len(state.records) >= (state.spec.traversal.item_limit or 10_000):
            return
        digest = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        if digest in state.record_hashes:
            return
        state.record_hashes.add(digest)
        state.records.append(dict(record))
        self.repository.update_execution(state.execution_id, record_count=len(state.records), pages_visited=state.pages_visited)

    async def _delay(self, state: _RunState) -> None:
        delay = int(state.parameters.get("request_delay_ms", state.spec.execution.request_delay_ms))
        if delay:
            await asyncio.sleep(delay / 1000)

    @staticmethod
    async def _page_signature(page: Page, items_step: PlanStep | None = None) -> str:
        if items_step:
            # Ignore spinners, clocks, ads and sidebars; wait for actual list data.
            contents = await _locator(page, items_step.selector_type, items_step.effective_selector or "").evaluate_all(
                "els => els.map(e => [e.innerText, e.getAttribute('href'), [...e.querySelectorAll('a')].map(a => a.href)])"
            )
            body = json.dumps(contents, ensure_ascii=False)
        else:
            body = await page.locator("body").inner_text()
        return f"{page.url}|{hashlib.sha256(body.encode()).hexdigest()}"

    async def _log(self, state: _RunState, message: str) -> None:
        line = f"{_now()} {message}"
        state.log_lines.append(line)
        path = state.run_dir / "execution.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def _write_results(self, state: _RunState) -> None:
        json_path = state.run_dir / "result.json"
        payload = json.dumps(state.records, ensure_ascii=False, indent=2) + "\n"
        if len(payload.encode("utf-8")) > state.spec.execution.max_output_bytes:
            raise AdapterError(f"output exceeds {state.spec.execution.max_output_bytes} bytes")
        json_path.write_text(payload, encoding="utf-8")
        output = state.spec.output
        if output.format == "json" and output.filename and output.filename != "result.json":
            (state.run_dir / output.filename).write_text(payload, encoding="utf-8")
        elif output.format == "csv":
            path = state.run_dir / (output.filename or f"{state.plan.name}.csv")
            _write_csv(path, state.records)
        elif output.format == "xlsx":
            path = state.run_dir / (output.filename or f"{state.plan.name}.xlsx")
            _write_xlsx(path, state.records)


def _locator(scope: Any, selector_type: str, selector: str) -> Locator:
    prefix = "xpath=" if selector_type == "xpath" else "css="
    return scope.locator(prefix + selector)


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


def _proxy_options(parameters: dict[str, Any]) -> dict[str, str] | None:
    server = parameters.get("proxy_server")
    if not server:
        return None
    result = {"server": str(server)}
    if parameters.get("proxy_username"):
        result["username"] = str(parameters["proxy_username"])
    if parameters.get("proxy_password"):
        result["password"] = str(parameters["proxy_password"])
    return result


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
