from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from packages.crawl_core.errors import InspectionError, PolicyViolation
from packages.crawl_core.jsonpath import select_json_path
from packages.crawl_core.policies import assert_public_host, host_allowed
from packages.crawl_core.site_scope import default_allowed_domains

from .dom_simplifier import simplify_dom, visible_text
from .fingerprint import fingerprint_dom


@dataclass
class ProbeSession:
    browser: Browser
    context: BrowserContext
    page: Page
    network_events: list[dict[str, Any]] = field(default_factory=list)
    blocked_optional_redirects: int = 0


class BrowserInspector:
    def __init__(
        self,
        *,
        navigation_timeout_ms: int = 30_000,
        max_sessions: int = 4,
        visible_text_max_chars: int = 6_000,
        simplified_dom_max_chars: int = 16_000,
    ):
        self.navigation_timeout_ms = navigation_timeout_ms
        self.max_sessions = max_sessions
        self.visible_text_max_chars = visible_text_max_chars
        self.simplified_dom_max_chars = simplified_dom_max_chars
        self._playwright = None
        self._sessions: dict[str, ProbeSession] = {}
        self._lock = asyncio.Lock()

    async def open(
        self,
        url: str,
        *,
        headless: bool = True,
        allowed_domains: list[str] | None = None,
        allow_cross_domain_navigation: bool = False,
        block_private_networks: bool = True,
    ) -> dict[str, Any]:
        async with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise InspectionError("maximum probe session count reached")
            domains = allowed_domains or default_allowed_domains([url])
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            browser = await self._playwright.chromium.launch(headless=headless)
            # Service-worker fetches can bypass browser routes. Read-only
            # inspection uses ordinary guarded network requests instead.
            try:
                context = await browser.new_context(service_workers="block")
            except Exception:
                await browser.close()
                raise
            navigation_policy_error: PolicyViolation | None = None
            navigation_fetch_error: InspectionError | None = None
            redirect_target: str | None = None
            blocked_optional_redirects = 0
            opening_navigation = True
            probe_session: ProbeSession | None = None

            async def check_target(target: str, *, navigation: bool) -> None:
                parsed = urlparse(target)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise PolicyViolation(
                        "probe redirect must target an absolute HTTP(S) URL"
                    )
                if parsed.username or parsed.password:
                    raise PolicyViolation(
                        "probe URLs containing credentials are forbidden"
                    )
                if block_private_networks:
                    await asyncio.to_thread(assert_public_host, parsed.hostname)
                if (
                    navigation
                    and not allow_cross_domain_navigation
                    and not host_allowed(parsed.hostname, domains)
                ):
                    raise PolicyViolation("cross-domain probe navigation is disabled")

            async def guard(route, request):
                nonlocal \
                    navigation_policy_error, \
                    navigation_fetch_error, \
                    redirect_target, \
                    blocked_optional_redirects
                parsed = urlparse(request.url)
                if parsed.scheme not in {"http", "https"}:
                    await route.continue_()
                    return
                is_navigation = request.is_navigation_request()
                try:
                    frame = getattr(request, "frame", None) if is_navigation else None
                except PlaywrightError:
                    # A pending popup can have no frame yet. Its navigation is
                    # still guarded, and cannot silently discard a policy stop.
                    frame = None
                is_required_navigation = is_navigation and (
                    frame is None or frame.parent_frame is None
                )
                try:
                    await check_target(request.url, navigation=is_navigation)
                except PolicyViolation as exc:
                    if is_required_navigation:
                        navigation_policy_error = exc
                    await route.abort("blockedbyclient")
                    return
                except Exception:
                    # Preserve the fail-closed behavior for unexpected guard
                    # failures. It is not evidence that a target is authorized.
                    if is_required_navigation:
                        navigation_policy_error = PolicyViolation(
                            "probe target safety verification failed"
                        )
                    await route.abort("blockedbyclient")
                    return
                response = None
                try:
                    # Playwright's route handler is only called for the FIRST
                    # URL of a native redirect chain. Never let API fetching or
                    # browser following contact an unchecked redirect target.
                    response = await route.fetch(
                        max_redirects=0, timeout=self.navigation_timeout_ms
                    )
                    if 300 <= response.status < 400 and response.status != 304:
                        location = response.headers.get("location")
                        if not location:
                            raise InspectionError(
                                "probe redirect has no Location header"
                            )
                        target = urljoin(request.url, location)
                        await check_target(target, navigation=is_navigation)
                        if (
                            opening_navigation
                            and frame is not None
                            and frame is page.main_frame
                        ):
                            redirect_target = target
                        else:
                            # Relaying even one checked 3xx would let the next
                            # hop bypass this route. Do not serve another origin's
                            # final iframe body at the original URL either.
                            blocked_optional_redirects += 1
                            if probe_session is not None:
                                probe_session.blocked_optional_redirects = (
                                    blocked_optional_redirects
                                )
                        await route.abort("blockedbyclient")
                        return
                    await route.fulfill(response=response)
                except PolicyViolation as exc:
                    if is_required_navigation:
                        navigation_policy_error = exc
                    await route.abort("blockedbyclient")
                except Exception as exc:
                    if is_required_navigation:
                        error = InspectionError("browser probe request failed")
                        error.code = (
                            "navigation_timeout"
                            if isinstance(exc, PlaywrightTimeoutError)
                            else "navigation_failed"
                        )
                        error.parameter = "url"
                        navigation_fetch_error = error
                    await route.abort("blockedbyclient")
                finally:
                    if response is not None:
                        try:
                            await response.dispose()
                        except PlaywrightError:
                            # Navigation cleanup can already have closed the
                            # context. Teardown errors are not selector evidence.
                            pass

            try:
                await context.route("**/*", guard)
                page = await context.new_page()
            except Exception:
                await asyncio.gather(
                    context.close(), browser.close(), return_exceptions=True
                )
                raise
            network_events: list[dict[str, Any]] = []

            async def capture_response(response) -> None:
                request = response.request
                if request.resource_type not in {"xhr", "fetch"}:
                    return
                content_type = response.headers.get("content-type", "")
                event: dict[str, Any] = {
                    "method": request.method,
                    "url": _redact_url(request.url),
                    "resource_type": request.resource_type,
                    "status": response.status,
                    "content_type": content_type,
                }
                if request.post_data:
                    event["request_preview"] = _redact_preview(
                        request.post_data, max_chars=1_500
                    )
                content_length = int(response.headers.get("content-length", "0") or 0)
                if "json" in content_type.lower() and content_length <= 200_000:
                    try:
                        response_text = await response.text()
                        event["response_preview"] = _redact_preview(
                            response_text, max_chars=2_000
                        )
                        event["_response_json"] = json.loads(response_text)
                    except Exception:
                        event["response_preview"] = "<unavailable>"
                network_events.append(event)
                if len(network_events) > 100:
                    del network_events[:-100]

            page.on("response", capture_response)
            page.set_default_timeout(self.navigation_timeout_ms)
            try:
                current_url = url
                seen_urls = set()
                deadline = (
                    asyncio.get_running_loop().time()
                    + self.navigation_timeout_ms / 1000
                )
                for _hop in range(11):
                    if current_url in seen_urls:
                        error = InspectionError("browser probe redirect loop detected")
                        error.code = "redirect_loop"
                        error.parameter = "url"
                        raise error
                    seen_urls.add(current_url)
                    redirect_target = None
                    navigation_fetch_error = None
                    remaining_ms = int(
                        (deadline - asyncio.get_running_loop().time()) * 1000
                    )
                    if remaining_ms <= 0:
                        raise PlaywrightTimeoutError(
                            "browser probe navigation timed out"
                        )
                    try:
                        await page.goto(
                            current_url,
                            wait_until="domcontentloaded",
                            timeout=remaining_ms,
                        )
                    except Exception:
                        if navigation_policy_error is not None:
                            raise navigation_policy_error
                        if navigation_fetch_error is not None:
                            raise navigation_fetch_error
                        if redirect_target is None:
                            raise
                    if navigation_policy_error is not None:
                        raise navigation_policy_error
                    if redirect_target is None:
                        break
                    # Fresh browser navigation preserves the target document's
                    # real URL, origin and relative-link resolution.
                    current_url = redirect_target
                else:
                    error = InspectionError("browser probe redirect limit exceeded")
                    error.code = "redirect_limit"
                    error.parameter = "url"
                    raise error
                title = await page.title()
                opening_navigation = False
            except Exception as exc:
                await asyncio.gather(
                    context.close(), browser.close(), return_exceptions=True
                )
                if navigation_policy_error is not None:
                    raise navigation_policy_error from exc
                if isinstance(exc, PolicyViolation):
                    raise
                if isinstance(exc, InspectionError):
                    raise
                error = InspectionError("browser probe navigation failed")
                error.code = (
                    "navigation_timeout"
                    if isinstance(exc, PlaywrightTimeoutError)
                    else "navigation_failed"
                )
                error.parameter = "url"
                raise error from exc
            session_id = f"probe_{uuid.uuid4().hex}"
            probe_session = ProbeSession(
                browser=browser,
                context=context,
                page=page,
                network_events=network_events,
                blocked_optional_redirects=blocked_optional_redirects,
            )
            self._sessions[session_id] = probe_session
            result = {
                "session_id": session_id,
                "url": page.url,
                "title": title,
                "status": "ready",
            }
            result.update(_optional_redirect_notice(blocked_optional_redirects))
            return result

    def _get(self, session_id: str) -> ProbeSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            error = InspectionError("unknown or expired probe session")
            error.code = "session_not_found"
            error.parameter = "session_id"
            raise error from exc

    async def snapshot(
        self, session_id: str, mode: str = "simplified_dom"
    ) -> dict[str, Any]:
        session = self._get(session_id)
        raw = await session.page.content()
        links = await session.page.locator("a[href]").evaluate_all(
            "els => els.slice(0, 80).map(e => ({text: (e.innerText || '').trim().slice(0, 160), href: e.href}))"
        )
        buttons = await session.page.locator("button, input[type=submit]").evaluate_all(
            "els => els.slice(0, 30).map(e => ({text: (e.innerText || e.value || '').trim().slice(0, 120), type: e.type || 'button'}))"
        )
        forms = await session.page.locator("form").evaluate_all(
            "els => els.slice(0, 20).map(e => ({action: e.action, method: (e.method || 'get').toLowerCase()}))"
        )
        return {
            "url": session.page.url,
            "visible_text": visible_text(raw, max_chars=self.visible_text_max_chars),
            "dom": simplify_dom(raw, max_chars=self.simplified_dom_max_chars)
            if mode == "simplified_dom"
            else "",
            "links": links,
            "forms": forms,
            "buttons": buttons,
            "fingerprint": fingerprint_dom(raw, session.page.url),
            "limits": {
                "visible_text_chars": self.visible_text_max_chars,
                "simplified_dom_chars": self.simplified_dom_max_chars,
                "links": 80,
                "buttons": 30,
                "forms": 20,
            },
            **_optional_redirect_notice(session.blocked_optional_redirects),
        }

    async def selector_test(
        self, session_id: str, selector_type: str, selector: str
    ) -> dict[str, Any]:
        session = self._get(session_id)
        if selector_type not in {"xpath", "css"}:
            return _selector_error(
                "invalid_selector_type",
                "selector_type",
                "selector_type must be xpath or css",
            )
        if (
            not isinstance(selector, str)
            or not selector.strip()
            or len(selector) > 2000
        ):
            return _selector_error(
                "invalid_selector",
                "selector",
                "selector must be a non-empty string of at most 2000 characters",
            )
        if session.page.is_closed():
            error = InspectionError("probe browser page is closed")
            error.code = "session_closed"
            error.parameter = "session_id"
            raise error
        try:
            locator = session.page.locator(
                f"xpath={selector}" if selector_type == "xpath" else selector
            )
            count = await locator.count()
            samples = await locator.evaluate_all(
                "els => els.slice(0, 5).map(e => ({text: (e.innerText || e.textContent || '').trim().slice(0, 500), tag: e.tagName.toLowerCase()}))"
            )
            return {"valid": True, "match_count": count, "samples": samples}
        except PlaywrightError:
            if session.page.is_closed():
                error = InspectionError("probe browser page is closed")
                error.code = "session_closed"
                error.parameter = "session_id"
                raise error
            # Playwright call logs contain selectors and URLs verbatim. Only
            # bounded diagnostic guidance enters the model-facing response.
            return _selector_error(
                "selector_evaluation_failed",
                "selector",
                "selector could not be evaluated by the browser",
            )

    async def network(self, session_id: str, *, limit: int = 10) -> dict[str, Any]:
        """Return bounded XHR/fetch evidence with common secrets redacted."""
        if not 1 <= limit <= 30:
            raise InspectionError("network event limit must be between 1 and 30")
        session = self._get(session_id)
        await asyncio.sleep(0)
        events = [
            {key: value for key, value in event.items() if not key.startswith("_")}
            for event in session.network_events[-limit:]
        ]
        return {
            "url": session.page.url,
            "requests": events,
            "captured_count": len(session.network_events),
            "returned_count": len(events),
            "truncated": len(session.network_events) > len(events),
        }

    async def network_jsonpath_test(
        self,
        session_id: str,
        path: str,
        *,
        request_index: int = -1,
    ) -> dict[str, Any]:
        """Test a safe JSONPath subset against one captured JSON response."""
        path_keys = {
            _normalized_key(token)
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", path)
        }
        if path_keys & _SENSITIVE_KEYS:
            return {
                "valid": False,
                "match_count": 0,
                "samples": [],
                "error": "JSONPath cannot select a sensitive field",
            }
        session = self._get(session_id)
        json_events = [
            event for event in session.network_events if "_response_json" in event
        ]
        if not json_events:
            return {
                "valid": False,
                "match_count": 0,
                "samples": [],
                "error": "no captured JSON response",
            }
        try:
            event = json_events[request_index]
        except IndexError:
            return {
                "valid": False,
                "match_count": 0,
                "samples": [],
                "error": "request_index is out of range",
            }
        try:
            values = select_json_path(event["_response_json"], path)
        except ValueError as exc:
            return {"valid": False, "match_count": 0, "samples": [], "error": str(exc)}
        samples = [_compact_json_value(value) for value in values[:5]]
        return {
            "valid": True,
            "match_count": len(values),
            "samples": samples,
            "request_url": event["url"],
        }

    async def close(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.context.close()
            await session.browser.close()
        return {"closed": bool(session), "session_id": session_id}

    async def shutdown(self) -> None:
        for session_id in list(self._sessions):
            await self.close(session_id)
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


def _optional_redirect_notice(count: int) -> dict[str, Any]:
    if not count:
        return {}
    return {
        "blocked_optional_redirects": count,
        "warning": "Optional iframe/asset redirects were blocked to keep every redirect target guarded. Main-page redirects were followed as fresh authorized navigations.",
    }


def _selector_error(code: str, parameter: str, message: str) -> dict[str, Any]:
    return {
        "valid": False,
        "match_count": 0,
        "samples": [],
        "code": code,
        "parameter": parameter,
        "error": message,
        "retry_limit": 1,
        "next_action": "Correct only the reported selector once from verified page evidence. Use XPath 1.0 element selectors or plain CSS; do not repeat unchanged inputs or guess another transport.",
    }


_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
    "x_api_key",
}


def _redact_url(value: str) -> str:
    parsed = urlparse(value)
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "***" if key.lower() in _SENSITIVE_KEYS else item))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _redact_preview(value: str, *, max_chars: int) -> str:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        redacted = value
        for key in _SENSITIVE_KEYS:
            redacted = re_sub_secret(redacted, key)
        return redacted[:max_chars]

    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: ("***" if key.lower() in _SENSITIVE_KEYS else scrub(child))
                for key, child in list(item.items())[:50]
            }
        if isinstance(item, list):
            return [scrub(child) for child in item[:20]]
        if isinstance(item, str):
            return item[:500]
        return item

    return json.dumps(scrub(payload), ensure_ascii=False, separators=(",", ":"))[
        :max_chars
    ]


def re_sub_secret(value: str, key: str) -> str:
    return re.sub(
        rf"(?i)({re.escape(key)}\s*[=:]\s*)([^&\s,;]+)",
        r"\1***",
        value,
    )


def _normalized_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _compact_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, dict):
        return {
            key: _compact_json_value(child) for key, child in list(value.items())[:20]
        }
    if isinstance(value, list):
        return [_compact_json_value(child) for child in value[:20]]
    return value
