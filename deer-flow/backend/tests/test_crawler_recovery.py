import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from deerflow.agents.middlewares.crawler_recovery_middleware import CrawlerRecoveryMiddleware, compact_crawler_context
from deerflow.agents.middlewares.tool_result_meta import normalize_tool_message


def receipt(payload, name="autospider_crawl_draft_execute", call_id="failed"):
    return ToolMessage(content=json.dumps(payload), name=name, tool_call_id=call_id, id=call_id)


def runtime(run_id="run"):
    return SimpleNamespace(context={"thread_id": "thread", "run_id": run_id})


def messages(payload):
    return [HumanMessage(content="Collect titles, times and bodies"), receipt(payload), AIMessage(content="The crawl failed.", id="premature")]


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "needs_repair", "code": "selector_no_match", "needs_reanalysis": True},
        {"execution_status": "failed", "error": "AdapterError: selector matched no values: //time"},
        {"validation": {"status": "FAIL", "issues": [{"field": "content"}]}},
        {"status": "validation_error", "code": "invalid_arguments", "retryable": True},
    ],
)
def test_recoverable_result_gets_one_bounded_continuation(payload):
    middleware = CrawlerRecoveryMiddleware()
    state = {"messages": messages(payload)}
    assert middleware.after_model(state, runtime())["jump_to"] == "model"
    assert middleware.after_model(state, runtime()) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "blocked", "code": "policy_blocked"},
        {"status": "tool_error", "retryable": False},
        {"status": "network_error", "retryable": False},
        {"validation": {"status": "PASS"}, "memory_committed": True},
    ],
)
def test_nonrecoverable_or_successful_result_does_not_restart(payload):
    assert CrawlerRecoveryMiddleware().after_model({"messages": messages(payload)}, runtime()) is None


def test_intermediate_probe_does_not_hide_pending_execution_failure():
    current = messages({"status": "needs_repair", "code": "selector_no_match"})
    current.insert(-1, receipt({"status": "ready", "fields": {"time": {"count": 1}}}, "autospider_probe_http_select", "probe"))
    assert CrawlerRecoveryMiddleware().after_model({"messages": current}, runtime())["jump_to"] == "model"


def test_later_pass_clears_pending_failure_and_old_turn_is_ignored():
    current = messages({"status": "needs_repair", "code": "selector_no_match"})
    current.insert(-1, receipt({"validation": {"status": "PASS"}}, call_id="passed"))
    assert CrawlerRecoveryMiddleware().after_model({"messages": current}, runtime()) is None
    current = messages({"status": "needs_repair", "code": "selector_no_match"})
    current.insert(-1, HumanMessage(content="Stop this task"))
    assert CrawlerRecoveryMiddleware().after_model({"messages": current}, runtime()) is None


@pytest.mark.parametrize("content", ["[TOKEN BUDGET EXCEEDED] stopped", "[FORCED STOP] loop capped"])
def test_recovery_never_overrides_budget_or_loop_stop(content):
    current = messages({"status": "needs_repair", "code": "selector_no_match"})
    current[-1].content = content
    assert CrawlerRecoveryMiddleware().after_model({"messages": current}, runtime()) is None


@pytest.mark.parametrize("marker", ["context", "message"])
def test_recovery_does_not_override_provider_or_runtime_stops(marker):
    current = messages({"status": "needs_repair", "code": "selector_no_match"})
    rt = runtime()
    if marker == "context":
        rt.context["stop_reason"] = "model_length_capped"
    else:
        current[-1].additional_kwargs["safety_termination"] = {"reason_value": "refusal"}
    assert CrawlerRecoveryMiddleware().after_model({"messages": current}, rt) is None


@pytest.mark.parametrize("wrapper", ["dict_json", "mcp_blocks", "mcp_blocks_json"])
def test_mcp_block_receipt_is_error_and_not_model_recoverable(wrapper):
    msg = receipt({"status": "blocked", "code": "policy_blocked"})
    if wrapper != "dict_json":
        msg.content = [{"type": "text", "text": msg.content}]
        if wrapper == "mcp_blocks_json":
            msg.content = json.dumps(msg.content)
    meta = normalize_tool_message(msg).additional_kwargs["deerflow_tool_meta"]
    assert meta["status"] == "error"
    assert meta["recoverable_by_model"] is False
    assert meta["recommended_next_action"] == "stop"


def test_qa_fail_is_error_even_when_protocol_call_succeeded():
    meta = normalize_tool_message(receipt({"validation": {"status": "FAIL"}})).additional_kwargs["deerflow_tool_meta"]
    assert meta["status"] == "error"
    assert meta["recoverable_by_model"] is True


def test_unrelated_tools_and_malformed_crawler_status_are_not_reinterpreted():
    unrelated = receipt({"status": "blocked"}, name="other_service_tool")
    assert normalize_tool_message(unrelated).additional_kwargs["deerflow_tool_meta"]["status"] == "success"
    malformed = receipt({"status": []})
    assert normalize_tool_message(malformed).additional_kwargs["deerflow_tool_meta"]["status"] == "success"


def test_old_successful_probes_compact_request_only_keep_latest_and_failures():
    old = receipt({"status": "ready", "url": "https://example.test", "summary": {"page_title": "News", "dom": "long article " * 800}}, "autospider_probe_http", "old")
    failed = receipt({"status": "needs_repair", "error": "selector mismatch", "padding": "x" * 8000}, call_id="bad")
    latest = receipt({"status": "ready", "url": "https://example.test", "padding": "y" * 8000}, "autospider_probe_http", "latest")
    original = old.content
    compact = compact_crawler_context([old, failed, latest], retain_recent=1)
    assert len(compact[0].content) < 1400
    assert compact[0].id == old.id
    assert old.content == original
    assert compact[1].content == failed.content
    assert compact[2].content == latest.content
    assert "deerflow_tool_transforms" in compact[0].additional_kwargs


class RecoveryModel(BaseChatModel):
    rounds: int = 0

    @property
    def _llm_type(self):
        return "offline-crawler-recovery"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.rounds += 1
        if self.rounds == 1:
            output = AIMessage(content="", tool_calls=[{"name": "autospider_crawl_draft_execute", "args": {"draft_id": "bad"}, "id": "bad"}])
        elif self.rounds == 2:
            output = AIMessage(content="Could not collect the required field.")
        elif self.rounds == 3:
            assert any(getattr(m, "name", "") == "crawler_recovery" for m in messages)
            output = AIMessage(content="", tool_calls=[{"name": "autospider_crawl_draft_execute", "args": {"draft_id": "repaired"}, "id": "fixed"}])
        else:
            output = AIMessage(content="Collected all requested fields after repair.")
        return ChatResult(generations=[ChatGeneration(message=output)])


@pytest.mark.parametrize("async_mode", [False, True])
def test_real_graph_continues_after_premature_failure_and_finishes(async_mode):
    calls = []

    @tool
    def autospider_crawl_draft_execute(draft_id: str) -> str:
        """Run an isolated synthetic draft, without network or model APIs."""
        calls.append(draft_id)
        return json.dumps({"status": "needs_repair", "code": "selector_no_match"} if draft_id == "bad" else {"validation": {"status": "PASS"}, "record_count": 2})

    model = RecoveryModel()
    graph = create_agent(model=model, tools=[autospider_crawl_draft_execute], middleware=[CrawlerRecoveryMiddleware()])
    state = {"messages": [HumanMessage(content="Collect two articles")]}
    result = asyncio.run(graph.ainvoke(state, context={"thread_id": "t", "run_id": "r"})) if async_mode else graph.invoke(state, context={"thread_id": "t", "run_id": "r"})
    assert calls == ["bad", "repaired"]
    assert result["messages"][-1].content == "Collected all requested fields after repair."


def test_real_graph_budget_stop_prevents_recovery_model_call():
    from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
    from deerflow.config.token_budget_config import TokenBudgetConfig

    class BudgetModel(RecoveryModel):
        def _generate(self, *args, **kwargs):
            result = super()._generate(*args, **kwargs)
            used = 1 if self.rounds == 1 else 2000
            result.generations[0].message.usage_metadata = {"input_tokens": used, "output_tokens": 1, "total_tokens": used + 1}
            return result

    @tool
    def autospider_crawl_draft_execute(draft_id: str) -> str:
        """Return a synthetic required-selector failure."""
        return json.dumps({"status": "needs_repair", "code": "selector_no_match"})

    model = BudgetModel()
    graph = create_agent(
        model=model,
        tools=[autospider_crawl_draft_execute],
        middleware=[
            CrawlerRecoveryMiddleware(),
            TokenBudgetMiddleware(TokenBudgetConfig(enabled=True, max_tokens=1000)),
        ],
    )
    result = graph.invoke({"messages": [HumanMessage(content="Collect articles")]}, context={"thread_id": "t", "run_id": "budget"})
    assert model.rounds == 2
    assert "TOKEN BUDGET EXCEEDED" in result["messages"][-1].content
