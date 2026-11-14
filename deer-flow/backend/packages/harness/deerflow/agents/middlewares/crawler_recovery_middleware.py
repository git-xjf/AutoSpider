"""Crawler-only bounded continuation and request-local probe history compaction."""

from __future__ import annotations

import json
import threading

from deerflow_extension_api import ContentKind, canonical_hash, provenance_kwargs
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from deerflow.agents.middlewares._bounded_dict import BoundedDict
from deerflow.agents.middlewares.tool_result_meta import crawler_result_kind, read_crawler_receipt
from deerflow.agents.middlewares.tool_transform_meta import append_tool_transform

_RECOVERY_PROMPT = (
    "An AutoSpider operation in this turn still has a recoverable failure. Continue one bounded diagnosis/repair using the existing tool evidence, "
    "rather than ending solely because execution failed before QA. Inspect the failed field/page, verify changed selectors, and execute a NEW draft when needed. "
    "Keep the user's fields and limits. Never repeat unchanged terminal executions, bypass policy, restart blocked work, or invent a successful result. "
    "Use existing compact tools; do not reload schemas, full DOM, article bodies or saved plans. Stop with the specific unresolved cause if safe repair remains unavailable."
)
_EXECUTION_TOOLS = frozenset({"autospider_crawl_draft_execute", "autospider_crawl_reuse_workflow", "autospider_crawl_execute_validated"})


def compact_crawler_context(messages: list, *, retain_recent: int = 4, max_probe_chars: int = 1200) -> list:
    """Compact old successful overview displays only; preserve state and failure evidence."""
    positions = [i for i, msg in enumerate(messages) if isinstance(msg, ToolMessage)]
    protected = set(positions[-retain_recent:]) if retain_recent > 0 else set()
    output = list(messages)
    for index, msg in enumerate(messages):
        if index in protected or not isinstance(msg, ToolMessage) or msg.name != "autospider_probe_http":
            continue
        payload = read_crawler_receipt(msg.content)
        if payload is None or payload.get("status") != "ready":
            continue
        original = json.dumps(payload, ensure_ascii=False)
        if len(original) <= max_probe_chars:
            continue
        source = payload.get("summary") or payload.get("overview") or payload
        if not isinstance(source, dict):
            continue
        compact = {
            "status": "ready",
            "context_compacted": True,
            "url": payload.get("url", source.get("url")),
            "note": "Older successful overview display shortened; newer tool results and failure evidence are unchanged. Counts/selectors must use verified probes.",
        }
        for key in ("page_title", "pagination_links", "pagination_hints", "repeated_blocks", "body_candidates", "links_preview"):
            if key in source:
                value = source[key]
                compact[key] = value[:2] if isinstance(value, list) and key != "pagination_links" else value
        for key in ("body_candidates", "repeated_blocks", "links_preview", "pagination_hints"):
            if len(json.dumps(compact, ensure_ascii=False)) <= max_probe_chars:
                break
            compact.pop(key, None)
        content = json.dumps(compact, ensure_ascii=False)
        # Exact navigation URLs are never truncated to meet a display budget.
        if len(content) > max_probe_chars or len(content) >= len(original):
            continue
        kwargs = dict(msg.additional_kwargs)
        append_tool_transform(kwargs, "crawler_context_compaction", by="crawler_recovery")
        output[index] = msg.model_copy(update={"content": content, "additional_kwargs": kwargs})
    return output


class CrawlerRecoveryMiddleware(AgentMiddleware[AgentState]):
    """Prevent one premature terminal response after recoverable crawler failures."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._used = BoundedDict(1000)
        self._pending = BoundedDict(1000)

    def release_policy_parameters(self):
        return {"recovery_nudges_per_run": 1, "retain_recent_tool_results": 4, "older_probe_display_chars": 1200, "recovery_prompt_hash": canonical_hash(_RECOVERY_PROMPT)}

    @staticmethod
    def _key(runtime):
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            return str(context.get("thread_id", "unknown")), str(context.get("run_id") or id(runtime))
        return "unknown", str(id(runtime))

    def _clear(self, runtime):
        key = self._key(runtime)
        with self._lock:
            self._used.pop(key, None)
            self._pending.pop(key, None)

    def before_agent(self, state, runtime):
        self._clear(runtime)

    async def abefore_agent(self, state, runtime):
        self._clear(runtime)

    def after_agent(self, state, runtime):
        self._clear(runtime)

    async def aafter_agent(self, state, runtime):
        self._clear(runtime)

    @staticmethod
    def _unresolved(messages):
        start = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage) and not messages[i].additional_kwargs.get("hide_from_ui")), None)
        if start is None:
            return False
        pending = None
        for msg in messages[start + 1 :]:
            if not isinstance(msg, ToolMessage):
                continue
            meta = msg.additional_kwargs.get("deerflow_tool_meta", {})
            if not isinstance(meta, dict):
                meta = {}
            if meta.get("recommended_next_action") == "stop":
                return False
            if not (msg.name or "").startswith("autospider_"):
                continue
            payload = read_crawler_receipt(msg.content)
            if payload is None:
                continue
            kind = crawler_result_kind(payload)
            if kind == "stop":
                return False
            if kind == "repair":
                pending = msg.name
            elif pending and (msg.name == pending or msg.name in _EXECUTION_TOOLS):
                report = payload.get("validation", {})
                if payload.get("status") in {"ready", "PASS"} or isinstance(report, dict) and report.get("status") == "PASS":
                    pending = None
        return pending is not None

    def _apply(self, state, runtime):
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        last = messages[-1]
        if last.tool_calls or last.invalid_tool_calls or not last.content:
            return None
        context = getattr(runtime, "context", None)
        if isinstance(context, dict) and context.get("stop_reason"):
            return None
        if last.additional_kwargs.get("deerflow_error_fallback") or last.additional_kwargs.get("safety_termination"):
            return None
        if last.response_metadata.get("stop_reason") in {"refusal", "max_tokens"}:
            return None
        if last.response_metadata.get("finish_reason") in {"length", "MAX_TOKENS", "content_filter", "SAFETY"}:
            return None
        if isinstance(last.content, str) and any(marker in last.content for marker in ("[TOKEN BUDGET EXCEEDED]", "[FORCED STOP]", "[LOOP DETECTED]")):
            return None
        if not self._unresolved(messages):
            return None
        key = self._key(runtime)
        with self._lock:
            if self._used.get(key):
                return None
            self._used[key] = True
            self._pending[key] = True
        return {"messages": [RemoveMessage(id=last.id)] if last.id else [], "jump_to": "model"}

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        return self._apply(state, runtime)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self._apply(state, runtime)

    def _request(self, request):
        messages = compact_crawler_context(list(request.messages))
        with self._lock:
            pending = self._pending.pop(self._key(request.runtime), False)
        if pending:
            messages.append(HumanMessage(content=_RECOVERY_PROMPT, name="crawler_recovery", additional_kwargs={"hide_from_ui": True, **provenance_kwargs(ContentKind.MIDDLEWARE_INJECTION, "crawler_recovery")}))
        return request.override(messages=messages)

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))
