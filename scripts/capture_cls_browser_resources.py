"""Bounded, credential-free CLS capture through an offline browser.

Only the two explicitly approved entries and three exact hostnames are in scope.
Every real GET goes through DNS policy, robots and cumulative limits. Browser
requests are never continued onto the network. No claim of patent novelty or
semantic truth follows from a successful capture.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import capture_public_memory_pages as common

HOSTS = ("www.cls.cn", "cls.cn", "wwwjs.cls.cn")
ENTRIES = ("https://www.cls.cn/detail/2501657", "https://www.cls.cn/detail/2470844")
LIMITS = {"get_requests": 80, "body_bytes": 20 * 1024 * 1024, "seconds": 120,
          "resource_bytes": 4 * 1024 * 1024, "robots_bytes": 256 * 1024}
EXECUTION_TYPES = {"document", "script", "stylesheet", "xhr", "fetch"}
DROP_HEADERS = common.SECRET_HEADERS | {"content-length", "transfer-encoding", "connection"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return sha256(data).hexdigest()


def write_new(path, data):
    with Path(path).open("xb") as handle:
        handle.write(data)


def scope(url, method="GET"):
    verdict = common.validate_capture_url(url, HOSTS, method)
    if method != "GET":
        verdict.update(allowed=False, reason="only_GET_authorized")
    if urlsplit(url).scheme != "https":
        verdict.update(allowed=False, reason="only_HTTPS_authorized")
    # Also check encoded path delimiters, rather than disguising an auth URL.
    from urllib.parse import unquote
    if common._GATE_PATH.search(unquote(urlsplit(url).path)):
        verdict.update(allowed=False, reason="login_or_verification_endpoint")
    return verdict


def sensitive_body_reason(data, content_type="", *, is_document=False):
    """Quarantine rather than rewrite possible secrets. False positives are OK.

    This is a conservative public-response screen, not a general secret scanner.
    Fresh HTTP clients never send a Cookie/Authorization header in the first place.
    """
    if is_document or "html" in content_type.lower():
        decoded = common.decode_document(data, content_type)
        if not decoded["verified"]:
            return "document_encoding_unverified"
        text = decoded["text"]
    else:
        try:
            text = data.decode("utf-8-sig", errors="strict")
        except UnicodeError:
            return "text_encoding_unverified"
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", text):
        return "possible_private_key"
    if re.search(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}", text):
        return "possible_bearer_credential"
    if re.search(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", text):
        return "possible_JWT"
    if common._SECRET_QUERY.search(text):
        return "sensitive_query_value_in_body"
    for match in common._SECRET_VALUE.finditer(text):
        if match.group(0).split(":", 1)[-1].strip(" \t\r\n\"'"):
            return "possible_sensitive_literal_in_body"
    # A title/password form check is meaningful for HTML, not arbitrary JS source.
    if is_document or "html" in content_type.lower() or text.lstrip().lower().startswith(("<!doctype", "<html")):
        decoded = common.decode_document(data, content_type)
        if not decoded["verified"]:
            return "document_encoding_unverified"
        return common._gate(decoded["text"])
    return None


BLOCK_UNSUPPORTED = """(() => {
  globalThis.__captureUnsupported = [];
  for (const name of ['Worker', 'SharedWorker', 'WebTransport', 'RTCPeerConnection']) {
    if (typeof globalThis[name] !== 'undefined') globalThis[name] = function() {
      globalThis.__captureUnsupported.push(name);
      throw new Error('Bounded capture does not support ' + name);
    };
  }
})();"""
OBSERVE = """() => {
  const selectors = ['.article-title', '.detail-content', '.detail-content > div'];
  return {url: location.href, readyState: document.readyState,
    unsupported: globalThis.__captureUnsupported || [],
    candidates: selectors.map(selector => ({selector,
      texts: [...document.querySelectorAll(selector)].map(n => n.innerText)}))};
}"""


class Capture:
    def __init__(self, output_dir):
        self.output = Path(output_dir)
        self.output.mkdir(parents=True, exist_ok=False)
        self.started = monotonic()
        self.halt_reason = None
        self.robots = {}
        self.resources = {}
        self.denied = set()
        source = Path(__file__).read_bytes()
        write_new(self.output / "collector-source.py", source)
        self.report = {
            "started_at_utc": now(), "status": "PENDING", "entries": list(ENTRIES),
            "allowed_hosts": list(HOSTS), "limits": dict(LIMITS),
            "collector_source_sha256": digest(source),
            "common_source_sha256": digest(Path(common.__file__).read_bytes()),
            "network_requests": 0, "network_body_bytes": 0,
            "network_count_scope": "target robots/resources GET only; public DNS verification counted separately by project policy, not here",
            "browser_direct_network": False, "browser_started": False,
            "javascript_executed": False, "replay_ready": False,
            "preflights": [], "robots": [], "resources": [], "blocked": [], "pages": [],
            "limitations": [
                "Fresh offline browser; all network GETs mediated; no browser profile or login state.",
                "Only document/script/stylesheet/xhr/fetch resources considered; other types blocked and recorded.",
                "Credential-like body literals are quarantined, never rewritten or executed; false positives possible.",
                "Set-Cookie and secret response headers omitted from browser fulfillment; original response metadata redacted.",
                "Observed closure is only for this finite execution, not all future paths or semantic truth.",
                "No source-field intervention or path equivalence proof is performed by this collector.",
            ],
        }

    def remaining(self):
        return max(0.0, LIMITS["seconds"] - (monotonic() - self.started))

    def block(self, url, reason, *, method="GET", kind=None):
        record = {"url": common.redact_url(url), "method": method, "reason": reason}
        if kind:
            record["resource_type"] = kind
        self.report["blocked"].append(record)
        return None

    def raw_get(self, url, maximum):
        """GET without redirects, cookies, env proxies, or caller-supplied headers."""
        if self.halt_reason:
            return {"error": "capture_halted:" + self.halt_reason}, b""
        if (not self.remaining() or self.report["network_requests"] >= LIMITS["get_requests"]
                or self.report["network_body_bytes"] >= LIMITS["body_bytes"]):
            self.halt_reason = "cumulative_budget_exhausted"
            return {"error": self.halt_reason}, b""
        decision = scope(url)
        if decision["allowed"]:
            decision = common.preflight_url(url, HOSTS)
        self.report["preflights"].append(decision)
        if not decision["allowed"]:
            return {"error": "policy_blocked", "policy_reason": decision["reason"]}, b""
        if not self.remaining():
            self.halt_reason = "cumulative_budget_exhausted"
            return {"error": self.halt_reason}, b""
        # Already scope-checked; retain public query encoding exactly for replay keys.
        meta = {"url": url, "method": "GET", "requested_at_utc": now()}
        body = bytearray()
        self.report["network_requests"] += 1
        try:
            with httpx.Client(trust_env=False, follow_redirects=False,
                              headers={"User-Agent": common.USER_AGENT, "Accept-Encoding": "identity"},
                              timeout=max(0.05, min(10.0, self.remaining()))) as client:
                with client.stream("GET", url) as response:
                    raw_headers = dict(response.headers)
                    meta.update(http_status=response.status_code,
                                headers=common.redact_headers(raw_headers),
                                content_type=response.headers.get("content-type", ""),
                                fulfill_headers={k: v for k, v in raw_headers.items() if k.lower() not in DROP_HEADERS})
                    if response.status_code in {401, 403, 429}:
                        self.halt_reason = "authentication_denial_or_rate_limit"
                        meta["error"] = self.halt_reason
                    elif 300 <= response.status_code < 400:
                        meta["error"] = "redirect_not_followed"
                        if not scope(urljoin(url, response.headers.get("location", "")))["allowed"]:
                            self.halt_reason = "out_of_scope_or_auth_redirect"
                    elif response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        meta["error"] = "unsupported_compressed_body"
                    if meta.get("error"):
                        meta.pop("fulfill_headers", None)
                        return meta, b""
                    # Leave a conservative <1 KiB margin rather than reading a
                    # chunk which could exceed the cumulative application-body cap.
                    iterator = response.iter_raw(chunk_size=1024)
                    while True:
                        if (LIMITS["body_bytes"] - self.report["network_body_bytes"] < 1024
                                or maximum - len(body) < 1024 or not self.remaining()):
                            self.halt_reason = "body_or_time_limit_reached"
                            meta["error"] = self.halt_reason
                            break
                        try:
                            chunk = next(iterator)
                        except StopIteration:
                            break
                        self.report["network_body_bytes"] += len(chunk)
                        if (not self.remaining() or len(body) + len(chunk) > maximum
                                or self.report["network_body_bytes"] > LIMITS["body_bytes"]):
                            self.halt_reason = "body_or_time_limit_reached"
                            meta["error"] = self.halt_reason
                            break
                        body.extend(chunk)
        except Exception as error:
            meta["error"] = type(error).__name__  # never log a transport's raw URL/header payload
        meta.update(original_body_sha256=digest(body), original_body_bytes=len(body),
                    body_complete=not bool(meta.get("error")))
        if meta.get("error"):
            meta.pop("fulfill_headers", None)
        return meta, bytes(body)

    def robots_allowed(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            meta, body = self.raw_get(origin + "/robots.txt", LIMITS["robots_bytes"])
            meta.pop("fulfill_headers", None)
            try:
                text = body.decode("utf-8-sig", errors="strict")
            except UnicodeError:
                text = ""
                meta["error"] = "robots_encoding_unverified"
            # Only store verified plain-text robots content, not error HTML.
            decision = common.evaluate_robots(meta.get("http_status") if not meta.get("error") else None,
                                             text, url, content_type=meta.get("content_type", ""))
            if decision["verified"] and meta.get("http_status") == 200:
                safe = common.redact_text(text).encode("utf-8")
                filename = f"robots-{len(self.report['robots'])}.txt"
                write_new(self.output / filename, safe)
                meta.update(saved_file=filename, saved_sha256=digest(safe), body_redacted=safe != body)
            meta["path_decisions"] = []
            self.robots[origin] = (meta, text)
            self.report["robots"].append(meta)
        meta, text = self.robots[origin]
        decision = common.evaluate_robots(meta.get("http_status") if not meta.get("error") else None,
                                         text, url, content_type=meta.get("content_type", ""))
        meta["path_decisions"].append({"url": common.redact_url(url), **decision})
        return decision["allowed"]

    def get_resource(self, url, kind="script"):
        url = urldefrag(url)[0]
        verdict = scope(url)
        if not verdict["allowed"]:
            if "login" in verdict["reason"] or "verification" in verdict["reason"]:
                self.halt_reason = "login_or_verification_endpoint"
            return self.block(url, verdict["reason"], kind=kind)
        if url in self.resources:
            return self.resources[url]
        if url in self.denied:
            return self.block(url, "previously_denied_resource", kind=kind)
        self.denied.add(url)  # no repeated retries for failed resources
        if not self.robots_allowed(url):
            return self.block(url, "robots_not_verified_or_disallowed", kind=kind)
        meta, data = self.raw_get(url, LIMITS["resource_bytes"])
        meta["resource_type"] = kind
        self.report["resources"].append(meta)
        if meta.get("error") or meta.get("http_status") != 200:
            meta.pop("fulfill_headers", None)
            return self.block(url, meta.get("error", "http_not_200"), kind=kind)
        if kind == "document":
            recognized = common.recognize_document(data, meta.get("content_type", ""))
            meta["document_recognition"] = recognized
            if not recognized["recognized"]:
                meta.pop("fulfill_headers", None)
                return self.block(url, "document_not_verified", kind=kind)
        reason = sensitive_body_reason(data, meta.get("content_type", ""), is_document=(kind == "document"))
        if reason:
            meta.pop("fulfill_headers", None)
            meta["quarantine_reason"] = reason
            if reason in {"login_or_verification_page", "password_form_present"}:
                self.halt_reason = reason
            return self.block(url, reason, kind=kind)
        # Metadata and stored browser headers are screened; raw Set-Cookie never persists.
        headers = meta.pop("fulfill_headers")
        if any(common.redact_text(v) != v for v in headers.values()):
            return self.block(url, "response_header_requires_redaction", kind=kind)
        filename = f"resource-{len(self.resources):03d}.bin"
        write_new(self.output / filename, data)
        meta.update(saved_file=filename, saved_sha256=digest(data), body_redacted=False,
                    browser_headers=headers, omitted_header_names=sorted(DROP_HEADERS))
        resource = (meta, data)
        self.resources[url] = resource
        return resource

    def browser_capture(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=[
                "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run",
                "--host-resolver-rules=MAP * ~NOTFOUND",
            ])
            self.report.update(browser_started=True, browser_version=browser.version, playwright_version=version("playwright"))
            try:
                for entry in ENTRIES:
                    if self.halt_reason or not self.remaining():
                        break
                    page_trace = {"entry_url": entry, "errors": [], "requests": [], "candidates": []}
                    self.report["pages"].append(page_trace)
                    context = browser.new_context(offline=True, service_workers="block", accept_downloads=False,
                                                  locale="zh-CN", timezone_id="Asia/Shanghai",
                                                  viewport={"width": 1280, "height": 720}, user_agent=common.USER_AGENT)
                    context.add_init_script(BLOCK_UNSUPPORTED)

                    def route_request(route):
                        request = route.request
                        record = {"url": common.redact_url(request.url), "method": request.method,
                                  "resource_type": request.resource_type, "fulfilled": False}
                        page_trace["requests"].append(record)
                        if request.method != "GET" or request.post_data:
                            self.block(request.url, "only_bodyless_GET_authorized", method=request.method)
                            route.abort("blockedbyclient")
                            return
                        if request.resource_type not in EXECUTION_TYPES:
                            self.block(request.url, "excluded_resource_type", kind=request.resource_type)
                            route.abort("blockedbyclient")
                            return
                        resource = self.get_resource(request.url, request.resource_type)
                        if resource is None:
                            route.abort("blockedbyclient")
                            return
                        meta, body = resource
                        route.fulfill(status=meta["http_status"], headers=meta["browser_headers"], body=body)
                        record.update(fulfilled=True, response_body_sha256=digest(body))

                    def websocket(route):
                        self.block(route.url, "unsupported_websocket", method="WEBSOCKET")
                        route.close(code=1008, reason="bounded capture has no websocket permission")

                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", websocket)
                    page = context.new_page()
                    page.on("pageerror", lambda error: page_trace["errors"].append(type(error).__name__))
                    page.on("console", lambda message: page_trace["errors"].append("console_error") if message.type == "error" else None)
                    context.on("page", lambda popup: popup.close() if popup is not page else None)
                    try:
                        page.goto(entry, wait_until="load", timeout=max(1, min(30000, int(self.remaining() * 1000))))
                        page.wait_for_timeout(min(1000, int(self.remaining() * 1000)))
                        observation = page.evaluate(OBSERVE)
                        page_trace["final_url"] = common.redact_url(observation["url"])
                        page_trace["ready_state"] = observation["readyState"]
                        page_trace["unsupported"] = observation["unsupported"]
                        # No rendered full HTML, browser cookies, or article text in the report.
                        for group in observation["candidates"]:
                            page_trace["candidates"].append({"selector": group["selector"], "count": len(group["texts"]),
                                "texts": [{"characters": len(text), "sha256_utf8": digest(text.encode("utf-8"))}
                                          for text in group["texts"]]})
                        self.report["javascript_executed"] = True
                    except Exception as error:
                        page_trace["errors"].append(type(error).__name__)
                    finally:
                        context.close()
            finally:
                browser.close()

    def run(self):
        # Preserve entry responses first, then verify hosts actually referenced by them.
        entries = [self.get_resource(entry, "document") for entry in ENTRIES]
        if not all(entries) or self.halt_reason:
            return
        for entry, resource in zip(ENTRIES, entries):
            meta, data = resource
            decoded = common.decode_document(data, meta.get("content_type", ""))
            if not decoded["verified"]:
                self.block(entry, "document_encoding_unverified")
                return
            document = html.fromstring(decoded["text"])
            for source in document.xpath("//script[@src]/@src"):
                script_url = urljoin(entry, source)
                if scope(script_url)["allowed"] and not self.robots_allowed(script_url):
                    self.block(script_url, "referenced_script_robots_not_verified_or_disallowed", kind="script")
                    return
        self.browser_capture()

    def finish(self):
        self.report.update(finished_at_utc=now(), elapsed_seconds=monotonic() - self.started,
                           halt_reason=self.halt_reason, saved_resources=len(self.resources))
        all_pages = len(self.report["pages"]) == len(ENTRIES)
        clean = all(not page["errors"] and not page.get("unsupported") and page.get("ready_state") == "complete"
                    for page in self.report["pages"])
        self.report["observed_execution_complete"] = bool(all_pages and clean and not self.report["blocked"] and not self.halt_reason)
        # A separate fully offline recheck is required even for a clean capture.
        self.report["status"] = "CAPTURED_RECHECK_REQUIRED" if self.report["observed_execution_complete"] else "PENDING_INCOMPLETE"
        write_new(self.output / "report.json", (json.dumps(self.report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verified-public-once", action="store_true", help="Only with explicit user approval; restored in finally.")
    args = parser.parse_args()
    collector = Capture(args.output_dir)
    name = "AUTOSPIDER_FAKE_IP_MODE"
    previous = os.environ.get(name)
    collector.report["mode_before"] = previous or "strict(default)"
    try:
        if args.verified_public_once:
            os.environ[name] = "verified_public"
        collector.report["mode_during"] = os.environ.get(name, "strict(default)")
        collector.run()
    except Exception as error:
        collector.report["error_class"] = type(error).__name__
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
        collector.report.update(mode_restored=os.environ.get(name) == previous,
                                mode_after=os.environ.get(name, "strict(default)"))
        report = collector.finish()
    print(json.dumps({key: report[key] for key in ("status", "network_requests", "network_body_bytes", "saved_resources", "browser_started", "replay_ready", "mode_restored", "halt_reason")}, indent=2))


if __name__ == "__main__":
    main()
