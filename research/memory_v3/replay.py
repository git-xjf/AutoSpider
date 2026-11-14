"""Known differential-replay reference for a strict plain-text source contract.

This is a feasibility engine, not a new causal-testing algorithm. A task supplies
the response, entity and field; the engine does not discover semantic truth or a
complete dataflow graph. All HTTP resources are fulfilled locally. Successful
receipts can be reused only for the same explicit capsule and browser version;
that is ordinary exact caching, not proof that hidden inputs or future branches
are identical. Date, randomness and timer scheduling are not instrumented.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from importlib.metadata import version as package_version
import json
from pathlib import Path
import secrets
import sqlite3
from time import perf_counter
from typing import Any
from urllib.parse import urldefrag, urlsplit


ENGINE_VERSION = "known-paired-replay-reference/1"
CONTRACT_VERSION = "strict-plain-text-paired-source/1"
ENGINE_SOURCE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()
ENVIRONMENT = {
    "locale": "en-US", "timezone_id": "UTC",
    "viewport": {"width": 1280, "height": 720},
    "device_scale_factor": 1, "color_scheme": "light",
    "reduced_motion": "reduce", "service_workers": "block",
    "offline": True,
}
CACHE_LIMIT = (
    "Reuse of an existing replay receipt for identical explicit inputs; not a "
    "new execution proof. Date, Math.random, timers and hidden inputs are not fixed."
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _norm(value: str) -> str:
    return " ".join(value.split())


def _bytes(value: str | bytes) -> bytes:
    return value if isinstance(value, bytes) else value.encode("utf-8")


def _network_url(value: str) -> str:
    return urldefrag(value)[0]


@dataclass(frozen=True)
class Resource:
    url: str
    body: str | bytes
    content_type: str
    status: int = 200
    method: str = "GET"
    request_body: str | None = None
    version: str = "1"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Bundle:
    entry_url: str
    resources: list[Resource]
    ready_selector: str
    candidates: list[str]
    version: str = "1"
    settle_ms: int = 30


@dataclass(frozen=True)
class Binding:
    resource_url: str
    records_pointer: str = "/records"
    entity_field: str = "id"
    entity_value: Any = "target"
    value_field: str = "body"
    version: str = "1"
    resource_method: str = "GET"
    request_body: str | None = None


def _resource_key(resource: Resource) -> tuple[str, str, str | None]:
    return (_network_url(resource.url), resource.method.upper(), resource.request_body)


def _public_inputs(bundle: Bundle, binding: Binding) -> dict:
    return {
        "entry_url": bundle.entry_url, "ready_selector": bundle.ready_selector,
        "candidates": list(bundle.candidates), "bundle_version": bundle.version,
        "settle_ms": bundle.settle_ms, "binding": asdict(binding),
        "resources": [
            {"url": item.url, "method": item.method.upper(), "request_body": item.request_body,
             "body_sha256": sha256(_bytes(item.body)).hexdigest(), "body_bytes": len(_bytes(item.body)),
             "content_type": item.content_type, "status": item.status,
             "version": item.version, "headers": dict(item.headers)}
            for item in bundle.resources
        ],
    }


def _pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("records_pointer must be an RFC 6901 JSON pointer")
    current = document
    for escaped in pointer[1:].split("/"):
        token = escaped.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not token.isdigit():
                raise ValueError("JSON pointer array index is invalid")
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise ValueError("JSON pointer does not address a container")
    return current


def _prepare(bundle: Bundle, binding: Binding) -> dict:
    if urlsplit(bundle.entry_url).scheme not in {"http", "https"}:
        raise ValueError("entry URL must use HTTP(S)")
    if not bundle.candidates or len(set(bundle.candidates)) != len(bundle.candidates):
        raise ValueError("candidate CSS selectors must be nonempty and unique")
    if not bundle.ready_selector or bundle.settle_ms < 0:
        raise ValueError("a ready selector and nonnegative settle interval are required")
    resources = {}
    for item in bundle.resources:
        if urlsplit(item.url).scheme not in {"http", "https"}:
            raise ValueError("resource URLs must use HTTP(S)")
        key = _resource_key(item)
        if key in resources:
            raise ValueError("resource manifest contains an ambiguous request key")
        resources[key] = item
    source_key = (_network_url(binding.resource_url), binding.resource_method.upper(), binding.request_body)
    source = resources.get(source_key)
    if source is None:
        raise ValueError("task-bound response is absent from the manifest")
    if not 200 <= source.status < 300:
        raise ValueError("task-bound response is not a successful response")
    document = json.loads(_bytes(source.body))
    records = _pointer(document, binding.records_pointer)
    if not isinstance(records, list) or not records:
        raise ValueError("records_pointer must select a nonempty JSON array")
    target_indices = [
        index for index, record in enumerate(records)
        if isinstance(record, dict) and binding.entity_field in record
        and _canonical(record[binding.entity_field]) == _canonical(binding.entity_value)
    ]
    if len(target_indices) != 1:
        raise ValueError("task entity must occur exactly once")
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(binding.value_field), str):
            raise ValueError("every controlled record must contain the specified plain-text field")
    target_index = target_indices[0]
    if not _norm(records[target_index][binding.value_field]):
        raise ValueError("task field must contain nonempty plain text")
    return {"resources": resources, "source_key": source_key, "document": document,
            "target_index": target_index, "target_text": records[target_index][binding.value_field],
            "foreign_indices": [index for index in range(len(records)) if index != target_index]}


class ReplayStore:
    """Research-only exact receipt cache; hashes check consistency, not authenticity."""

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS replay_receipts (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL)"
        )
        self.connection.commit()
        self.integrity_failures = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.connection.close()

    def lookup(self, cache_key: str) -> dict | None:
        row = self.connection.execute(
            "SELECT payload,payload_hash FROM replay_receipts WHERE cache_key=?", (cache_key,)
        ).fetchone()
        if row is None:
            return None
        try:
            result = json.loads(row[0])
            if (_digest(result) != row[1] or result.get("cache_key") != cache_key
                    or result.get("state") != "VALID"
                    or result.get("trace_hash") != _digest(result.get("traces", []))):
                raise ValueError("receipt consistency check failed")
            return result
        except (ValueError, TypeError, AttributeError, KeyError):
            self.integrity_failures += 1
            return None

    def record(self, cache_key: str, result: dict):
        if result.get("state") != "VALID" or result.get("cache_key") != cache_key:
            raise ValueError("only complete successful exact-input receipts are cached")
        if result.get("trace_hash") != _digest(result.get("traces", [])):
            raise ValueError("receipt trace hash mismatch")
        self.connection.execute(
            "INSERT OR REPLACE INTO replay_receipts VALUES (?,?,?)",
            (cache_key, _canonical(result), _digest(result)),
        )
        self.connection.commit()


_EXTRACT = """selectors => {
  const path = el => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      let ordinal = 1;
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling)
        if (sibling.tagName === node.tagName) ordinal++;
      parts.unshift(node.tagName.toLowerCase() + ':nth-of-type(' + ordinal + ')');
    }
    return parts.join(' > ');
  };
  const results = {};
  for (const selector of selectors) {
    try {
      const nodes = document.querySelectorAll(selector);
      results[selector] = {count: nodes.length, text: nodes.length === 1 ? nodes[0].innerText : null,
                           path: nodes.length === 1 ? path(nodes[0]) : null, error: null};
    } catch (error) {
      results[selector] = {count: null, text: null, path: null, error: String(error)};
    }
  }
  return results;
}"""

_BLOCK_UNSUPPORTED = """(() => {
  globalThis.__offlineReplayUnsupported = [];
  for (const name of ['Worker', 'SharedWorker', 'WebTransport', 'RTCPeerConnection']) {
    if (typeof globalThis[name] !== 'undefined') {
      globalThis[name] = function() {
        globalThis.__offlineReplayUnsupported.push(name);
        throw new Error('Offline replay does not support ' + name);
      };
    }
  }
})();"""


class ReplayEngine:
    """Synchronous Chromium replay. Each load uses a fresh isolated context."""

    def __init__(self, *, headless: bool = True, timeout_ms: int = 5000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._playwright = None
        self.browser = None
        self.browser_version = None
        self._startup_seconds = 0.0
        self._launch_accounted = False

    def __enter__(self):
        if self.browser is not None:
            return self
        from playwright.sync_api import sync_playwright
        started = perf_counter()
        self._playwright = sync_playwright().start()
        try:
            self.browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"],
            )
            self.browser_version = self.browser.version
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        self._startup_seconds = perf_counter() - started
        self._launch_accounted = False
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.browser is not None:
            self.browser.close()
            self.browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _load(self, bundle: Bundle, prepared: dict, label: str, mutation: dict | None, timeout_ms: int) -> dict:
        started = perf_counter()
        trace = {"label": label, "complete": False, "mutation": mutation,
                 "requests": [], "errors": [], "candidates": {}, "elapsed_seconds": 0.0}
        mutated_body = None
        if mutation is not None:
            document = deepcopy(prepared["document"])
            records = _pointer(document, mutation["records_pointer"])
            records[mutation["record_index"]][mutation["value_field"]] = mutation["canary"]
            mutated_body = _canonical(document).encode("utf-8")
        context = self.browser.new_context(**deepcopy(ENVIRONMENT), accept_downloads=False)
        context.set_default_timeout(timeout_ms)
        context.add_init_script(_BLOCK_UNSUPPORTED)

        def route_request(route):
            request = route.request
            key = (_network_url(request.url), request.method.upper(), request.post_data)
            record = {"url": request.url, "method": request.method,
                      "request_body_sha256": sha256((request.post_data or "").encode()).hexdigest(),
                      "fulfilled": False, "aborted": False, "error": None}
            trace["requests"].append(record)
            resource = prepared["resources"].get(key)
            if resource is None:
                record.update(aborted=True, error="unrecorded_request")
                trace["errors"].append("unrecorded_request:" + request.method + ":" + request.url)
                route.abort("blockedbyclient")
                return
            body = mutated_body if mutation is not None and key == prepared["source_key"] else _bytes(resource.body)
            headers = {key: value for key, value in resource.headers.items() if key.lower() != "content-length"}
            try:
                route.fulfill(status=resource.status, body=body, content_type=resource.content_type, headers=headers)
                record.update(fulfilled=True, status=resource.status, response_body_sha256=sha256(body).hexdigest())
                if resource.status >= 400:
                    trace["errors"].append("recorded_http_error:" + str(resource.status) + ":" + resource.url)
            except Exception as error:
                record.update(aborted=True, error=type(error).__name__ + ":" + str(error))
                trace["errors"].append(record["error"])
                try:
                    route.abort("failed")
                except Exception:
                    pass

        def block_websocket(route):
            trace["errors"].append("unsupported_websocket:" + route.url)
            trace["requests"].append({"url": route.url, "method": "WEBSOCKET", "fulfilled": False,
                                      "aborted": True, "error": "unsupported_websocket"})
            route.close(code=1008, reason="offline replay has no websocket transcript")

        context.route("**/*", route_request)
        context.route_web_socket("**/*", block_websocket)
        page = context.new_page()
        page.on("pageerror", lambda error: trace["errors"].append("pageerror:" + str(error)))
        page.on("crash", lambda: trace["errors"].append("page_crashed"))
        context.on("page", lambda _: trace["errors"].append("unsupported_popup"))
        try:
            page.goto(bundle.entry_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(bundle.ready_selector, state="attached", timeout=timeout_ms)
            if bundle.settle_ms:
                page.wait_for_timeout(bundle.settle_ms)
            trace["candidates"] = page.evaluate(_EXTRACT, bundle.candidates)
            for value in trace["candidates"].values():
                if isinstance(value.get("text"), str):
                    value["text"] = _norm(value["text"])
            unsupported = page.evaluate("globalThis.__offlineReplayUnsupported || []")
            trace["errors"].extend("unsupported:" + name for name in unsupported)
            source_seen = any(
                record["fulfilled"] and _network_url(record["url"]) == prepared["source_key"][0]
                and record["method"].upper() == prepared["source_key"][1]
                for record in trace["requests"]
            )
            if not source_seen:
                trace["errors"].append("task_bound_response_was_not_requested")
        except Exception as error:
            trace["errors"].append(type(error).__name__ + ":" + str(error))
        finally:
            context.close()
        trace["complete"] = not trace["errors"] and bool(trace["candidates"])
        trace["elapsed_seconds"] = perf_counter() - started
        return trace

    def evaluate(self, bundle: Bundle, binding: Binding, *, budget_loads: int,
                 store: ReplayStore | None = None, timeout_ms: int | None = None) -> dict:
        if self.browser is None:
            raise RuntimeError("Use ReplayEngine as a context manager before evaluation")
        if type(budget_loads) is not int or budget_loads < 0:
            raise ValueError("budget_loads must be a nonnegative integer")
        timeout = self.timeout_ms if timeout_ms is None else timeout_ms
        if type(timeout) is not int or timeout <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        started = perf_counter()
        startup = self._startup_seconds if not self._launch_accounted else 0.0
        launches = int(not self._launch_accounted)
        self._launch_accounted = True
        cache_key = _digest({"engine": ENGINE_VERSION, "engine_source": ENGINE_SOURCE_SHA256,
                             "contract": CONTRACT_VERSION,
                             "browser": self.browser_version, "playwright": package_version("playwright"),
                             "environment": ENVIRONMENT, "headless": self.headless, "timeout_ms": timeout,
                             "inputs": _public_inputs(bundle, binding)})

        def cost(traces):
            requests = [request for trace in traces for request in trace["requests"]]
            return {"page_loads": len(traces), "requests": len(requests),
                    "fulfilled_requests": sum(bool(item["fulfilled"]) for item in requests),
                    "aborted_requests": sum(bool(item["aborted"]) for item in requests),
                    "browser_launches": launches, "browser_startup_seconds": startup,
                    "elapsed_seconds": perf_counter() - started + startup}

        if store is not None:
            cached = store.lookup(cache_key)
            if cached is not None:
                cached["original_replay_cost"] = cached["cost"]
                cached.update(cache_hit=True, cost=cost([]), cache_semantics=CACHE_LIMIT)
                return cached
        base_result = {
            "state": "PENDING", "selector": None, "passing_candidates": [], "reason": "",
            "cache_hit": False, "cache_key": cache_key, "cache_semantics": CACHE_LIMIT,
            "browser_version": self.browser_version, "engine_version": ENGINE_VERSION,
            "engine_source_sha256": ENGINE_SOURCE_SHA256,
            "contract_version": CONTRACT_VERSION, "environment": deepcopy(ENVIRONMENT),
            "uninstrumented_inputs": ["Date", "Math.random", "performance", "timer scheduling"],
            "required_loads": None, "cost": {}, "traces": [], "trace_hash": _digest([]),
            "candidate_results": [],
        }
        try:
            prepared = _prepare(bundle, binding)
        except (ValueError, TypeError, KeyError, IndexError) as error:
            base_result.update(reason="unproven_task_binding:" + str(error), cost=cost([]))
            return base_result
        required = 3 + len(prepared["foreign_indices"])
        base_result["required_loads"] = required
        base_result["foreign_controls_total"] = len(prepared["foreign_indices"])
        base_result["target_source_text"] = _norm(prepared["target_text"])
        plans = [("baseline_1", None), ("baseline_2", None)]
        record_indices = [("target", prepared["target_index"])] + [
            (f"foreign_{position}", index) for position, index in enumerate(prepared["foreign_indices"])
        ]
        for label, index in record_indices:
            canary = "replayprobe_" + secrets.token_hex(32)
            while any(canary.encode() in _bytes(resource.body) for resource in bundle.resources):
                canary = "replayprobe_" + secrets.token_hex(32)
            plans.append((label, {"record_index": index, "records_pointer": binding.records_pointer,
                                  "value_field": binding.value_field, "canary": canary}))
        traces = [self._load(bundle, prepared, label, mutation, timeout)
                  for label, mutation in plans[:budget_loads]]
        by_label = {trace["label"]: trace for trace in traces}
        all_runs = len(traces) == required
        all_complete = all_runs and all(trace["complete"] for trace in traces)

        def view(label, selector):
            trace = by_label.get(label)
            value = trace.get("candidates", {}).get(selector) if trace else None
            if not trace or not trace["complete"] or not value or value.get("error") or value.get("count") != 1:
                return None
            return value

        for selector in bundle.candidates:
            first, second, target = (view(label, selector) for label in ("baseline_1", "baseline_2", "target"))
            baseline_stable = bool(first and second and first["path"] == second["path"] and first["text"] == second["text"])
            base_match = bool(first and second and first["text"] == second["text"] == _norm(prepared["target_text"]))
            target_response = bool(first and target and first["path"] == target["path"]
                                   and target["text"] == by_label["target"]["mutation"]["canary"])
            control_labels = [label for label, _ in plans if label.startswith("foreign_")]
            all_controls_run = all(label in by_label for label in control_labels)
            controls_unchanged = bool(first and all_controls_run and all(
                (value := view(label, selector)) and value["path"] == first["path"] and value["text"] == first["text"]
                for label in control_labels
            ))
            stable_paths = bool(first and all(
                (value := view(trace["label"], selector)) and value["path"] == first["path"] for trace in traces
            ))
            stable = baseline_stable and stable_paths
            passed = all_complete and stable and base_match and target_response and controls_unchanged
            conditions = {"base_match": base_match, "target_response": target_response,
                          "non_target_unchanged": controls_unchanged, "stable": stable,
                          "baseline_stable": baseline_stable, "all_controls_run": all_controls_run,
                          "all_runs_complete": all_complete}
            failed = [name for name, passed_condition in conditions.items() if not passed_condition]
            base_result["candidate_results"].append({
                "selector": selector, "state": "VALID" if passed else "PENDING",
                "reason": "observed_plain_text_source_binding" if passed else ",".join(failed), **conditions,
            })
        passing = [item["selector"] for item in base_result["candidate_results"] if item["state"] == "VALID"]
        if len(passing) == 1:
            base_result.update(state="VALID", selector=passing[0], reason="unique_observed_plain_text_source_binding")
        elif len(passing) > 1:
            base_result["reason"] = "multiple_source_bound_candidates_role_is_unproven"
        elif not all_runs:
            base_result["reason"] = "insufficient_replay_budget"
        elif not all_complete:
            base_result["reason"] = "incomplete_replay_trace"
        else:
            base_result["reason"] = "no_candidate_satisfies_strict_paired_contract"
        base_result.update(passing_candidates=passing, traces=traces,
                           trace_hash=_digest(traces), cost=cost(traces))
        if store is not None and base_result["state"] == "VALID":
            store.record(cache_key, base_result)
        return base_result


def evaluate_bundle(bundle: Bundle, binding: Binding, *, budget_loads: int,
                    store: ReplayStore | None = None, headless: bool = True,
                    timeout_ms: int = 5000) -> dict:
    with ReplayEngine(headless=headless, timeout_ms=timeout_ms) as engine:
        return engine.evaluate(bundle, binding, budget_loads=budget_loads, store=store)
