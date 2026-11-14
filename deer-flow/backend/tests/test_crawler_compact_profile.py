"""Opt-in crawler context profiles must not alter ordinary agents or policies."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import ValidationError

from deerflow.agents.lead_agent import agent as lead
from deerflow.agents.lead_agent import prompt
from deerflow.agents.lead_agent.profiles import compact_tool_descriptions
from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from deerflow.agents.middlewares.skill_tool_policy_middleware import SkillToolPolicyMiddleware
from deerflow.config.agents_config import AgentConfig, preserve_non_managed_fields
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.tool_search_config import ToolSearchConfig
from deerflow.tools.builtins.tool_search import assemble_deferred_tools
from deerflow.tools.mcp_metadata import tag_mcp_tool


@tool
def probe_http(url: str) -> str:
    """Inspect synthetic HTML without using a network."""
    return "verified selectors"


@tool
def advanced_repair(selector: str) -> str:
    """Repair an unusual selector."""
    return selector


@tool
def forbidden_write(content: str) -> str:
    """An irrelevant local mutation surface."""
    raise AssertionError("must not execute")


def test_agent_profile_defaults_preserve_existing_behavior():
    cfg = AgentConfig(name="ordinary")
    assert cfg.prompt_profile == "standard"
    assert cfg.tool_names is None
    assert cfg.preload_tools == []


def test_profile_fields_are_preserved_by_generic_agent_editors():
    cfg = AgentConfig(name="crawler", prompt_profile="crawler_compact", tool_names=["read_file"], preload_tools=["probe_http"])
    kept = preserve_non_managed_fields(cfg)
    assert kept["prompt_profile"] == "crawler_compact"
    assert kept["tool_names"] == ["read_file"]
    assert kept["preload_tools"] == ["probe_http"]


def test_unknown_profile_fails_closed():
    with pytest.raises(ValidationError):
        AgentConfig(name="crawler", prompt_profile="untrusted-custom-prompt")


def test_preloaded_mcp_is_callable_without_search_but_advanced_remains_deferred():
    loaded = tag_mcp_tool(probe_http.model_copy())
    advanced = tag_mcp_tool(advanced_repair.model_copy())
    tools, setup = assemble_deferred_tools([loaded, advanced], enabled=True, preloaded_names=frozenset({"probe_http"}), return_schema=False)
    assert setup.deferred_names == frozenset({"advanced_repair"})
    assert loaded in tools
    middleware = DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash)
    assert middleware._hidden({}) == {"advanced_repair"}
    request = ToolCallRequest(tool_call={"name": "probe_http", "args": {"url": "https://example.test"}, "id": "call"}, tool=loaded, state={}, runtime=SimpleNamespace())
    assert middleware.wrap_tool_call(request, lambda req: "called") == "called"


def test_all_preloaded_is_valid_and_does_not_hide_local_tools():
    loaded = tag_mcp_tool(probe_http.model_copy())
    tools, setup = assemble_deferred_tools([loaded, forbidden_write], enabled=True, preloaded_names=frozenset({"probe_http"}))
    assert setup.tool_search_tool is None
    assert setup.deferred_names == frozenset()
    assert tools == [loaded, forbidden_write]


def test_receipt_search_still_writes_hash_scoped_promotions():
    _, setup = assemble_deferred_tools([tag_mcp_tool(advanced_repair.model_copy())], enabled=True, return_schema=False)
    result = setup.tool_search_tool.invoke({"type": "tool_call", "name": "tool_search", "args": {"query": "select:advanced_repair"}, "id": "search"})
    assert result.update["promoted"] == {"catalog_hash": setup.catalog_hash, "names": ["advanced_repair"]}
    receipt = json.loads(result.update["messages"][0].content)
    assert receipt == [{"name": "advanced_repair", "status": "promoted"}]
    assert "parameters" not in result.update["messages"][0].content


def test_default_search_still_returns_full_schema():
    _, setup = assemble_deferred_tools([tag_mcp_tool(advanced_repair.model_copy())], enabled=True)
    result = setup.tool_search_tool.invoke({"type": "tool_call", "name": "tool_search", "args": {"query": "select:advanced_repair"}, "id": "search"})
    assert "parameters" in result.update["messages"][0].content


def test_preloaded_tool_remains_blocked_by_active_skill_policy(monkeypatch):
    loaded = tag_mcp_tool(probe_http.model_copy())
    middleware = SkillToolPolicyMiddleware(slash_source_owner_token="test-owned")
    monkeypatch.setattr(middleware, "_active_policy", lambda request: ("skill_context", ("/mnt/skills/custom/example/SKILL.md",)))
    monkeypatch.setattr(middleware, "_allowed_names", lambda *args, **kwargs: {"advanced_repair"})
    request = ToolCallRequest(tool_call={"name": "probe_http", "args": {"url": "https://example.test"}, "id": "call"}, tool=loaded, state={}, runtime=SimpleNamespace())
    result = middleware.wrap_tool_call(request, lambda req: pytest.fail("preload must not bypass active skill policy"))
    assert result.status == "error"


def test_receipt_search_obeys_active_skill_policy(monkeypatch):
    loaded = tag_mcp_tool(probe_http.model_copy())
    advanced = tag_mcp_tool(advanced_repair.model_copy())
    _, setup = assemble_deferred_tools([loaded, advanced], enabled=True, return_schema=False)
    search = setup.tool_search_tool
    request = ToolCallRequest(tool_call={"name": "tool_search", "args": {"query": "select:probe_http,advanced_repair"}, "id": "search"}, tool=search, state={}, runtime=SimpleNamespace())
    result = search.invoke({"type": "tool_call", **request.tool_call})
    policy = SkillToolPolicyMiddleware(slash_source_owner_token="test-owned")
    filtered = policy._filter_tool_search_result(request, result, allowed={"probe_http"})
    assert filtered.update["promoted"]["names"] == ["probe_http"]
    assert json.loads(filtered.update["messages"][0].content) == [{"name": "probe_http", "status": "promoted"}]


def test_compact_tool_descriptions_clone_without_changing_schema_or_callable():
    from deerflow.tools.builtins import task_tool

    original_description = task_tool.description
    compact = compact_tool_descriptions([task_tool])[0]
    assert compact is not task_tool
    assert len(compact.description) < len(original_description)
    assert compact.args_schema is task_tool.args_schema
    assert compact.coroutine is task_tool.coroutine
    assert task_tool.description == original_description


def test_compact_prompt_has_bounded_framework_context_and_safety_boundaries(monkeypatch):
    monkeypatch.setattr(prompt, "get_agent_soul", lambda *args, **kwargs: "<soul>selected operator workflow</soul>")
    compact = prompt.apply_crawler_prompt_template(agent_name="crawler", deferred_names=frozenset({"advanced_repair"}), skill_names=frozenset({"intelligent-crawling"}), subagent_enabled=True, allowed_subagents=["web-analyst", "qa-repair"])
    assert len(compact) < 6500
    for required in ["untrusted", "robots", "CAPTCHA", "token", "advanced_repair", "intelligent-crawling", "web-analyst", "qa-repair", "selected operator workflow", "receipt", "ask_clarification"]:
        assert required in compact
    assert "MANDATORY Clarification Scenarios" not in compact
    assert "FULL replacement text" not in compact


class _ProbeModel(BaseChatModel):
    rounds: int = 0
    bound_names: list[list[str]] = []

    @property
    def _llm_type(self):
        return "offline-profile-probe"

    def bind_tools(self, tools, **kwargs):
        self.bound_names.append([t.name for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.rounds += 1
        output = AIMessage(content="", tool_calls=[{"name": "probe_http", "args": {"url": "https://example.test"}, "id": "probe"}]) if self.rounds == 1 else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=output)])


def _app():
    return AppConfig(
        models=[ModelConfig(name="safe", display_name="safe", use="langchain_openai:ChatOpenAI", model="safe")], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), tool_search=ToolSearchConfig(enabled=True)
    )


def _patch_assembly(monkeypatch, cfg, *, authorize=None, compiled=False):
    import deerflow.tools as tools_module

    model = _ProbeModel()
    captured = {}
    monkeypatch.setattr(lead, "load_agent_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(lead, "_load_enabled_available_skills", lambda *args, **kwargs: [])
    monkeypatch.setattr(lead, "build_tracing_callbacks", lambda: [])
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(lead, "apply_prompt_template", lambda **kwargs: "ordinary prompt")
    monkeypatch.setattr(prompt, "get_agent_soul", lambda *args, **kwargs: "<soul>compact workflow</soul>")
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [tag_mcp_tool(probe_http.model_copy()), tag_mcp_tool(advanced_repair.model_copy()), forbidden_write])
    if authorize:
        monkeypatch.setattr(lead, "apply_tool_authorization", authorize)

    def middleware(*args, **kwargs):
        setup = kwargs["deferred_setup"]
        captured["setup"] = setup
        captured["prompt_profile"] = kwargs.get("prompt_profile", "standard")
        return [DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash)]

    monkeypatch.setattr(lead, "build_middlewares", middleware)
    if not compiled:
        monkeypatch.setattr(lead, "create_agent", lambda **kwargs: kwargs)
    return model, captured


def test_factory_filters_raw_and_late_tools_keeps_advanced_discovery(monkeypatch):
    cfg = AgentConfig(name="crawler", prompt_profile="crawler_compact", tool_names=[], preload_tools=["probe_http"])
    _patch_assembly(monkeypatch, cfg)
    result = lead._make_lead_agent({"configurable": {"agent_name": "crawler"}}, app_config=_app())
    names = {t.name for t in result["tools"]}
    assert names == {"probe_http", "advanced_repair", "tool_search"}
    assert "untrusted" in result["system_prompt"]
    assert "ordinary prompt" != result["system_prompt"]


def test_compact_profile_without_explicit_local_allowlist_still_excludes_mutations(monkeypatch):
    cfg = AgentConfig(name="crawler", prompt_profile="crawler_compact", preload_tools=["probe_http"])
    _patch_assembly(monkeypatch, cfg)
    result = lead._make_lead_agent({"configurable": {"agent_name": "crawler"}}, app_config=_app())
    assert "forbidden_write" not in {tool.name for tool in result["tools"]}
    assert "update_agent" not in {tool.name for tool in result["tools"]}


def test_standard_agent_keeps_full_toolset_and_general_prompt(monkeypatch):
    cfg = AgentConfig(name="ordinary")
    _patch_assembly(monkeypatch, cfg)
    result = lead._make_lead_agent({"configurable": {"agent_name": "ordinary"}}, app_config=_app())
    assert {"forbidden_write", "update_agent"} <= {tool.name for tool in result["tools"]}
    assert result["system_prompt"] == "ordinary prompt"


@pytest.mark.parametrize("profile", ["standard", "crawler_compact"])
def test_factory_passes_explicit_profile_to_middleware_builder(monkeypatch, profile):
    cfg = AgentConfig(name="test", prompt_profile=profile)
    _, captured = _patch_assembly(monkeypatch, cfg)
    lead._make_lead_agent({"configurable": {"agent_name": "test"}}, app_config=_app())
    assert captured["prompt_profile"] == profile


def test_preloading_never_reintroduces_layer_one_denied_tool(monkeypatch):
    cfg = AgentConfig(name="crawler", prompt_profile="crawler_compact", tool_names=[], preload_tools=["probe_http"])
    _patch_assembly(monkeypatch, cfg, authorize=lambda tools, **kwargs: ([t for t in tools if t.name != "probe_http"], None))
    result = lead._make_lead_agent({"configurable": {"agent_name": "crawler"}}, app_config=_app())
    assert "probe_http" not in {t.name for t in result["tools"]}


def test_production_factory_compiled_graph_newsite_calls_preloaded_probe_without_search(monkeypatch):
    cfg = AgentConfig(name="crawler", prompt_profile="crawler_compact", tool_names=[], preload_tools=["probe_http"])
    model, captured = _patch_assembly(monkeypatch, cfg, compiled=True)
    graph = lead._make_lead_agent({"configurable": {"agent_name": "crawler"}}, app_config=_app())
    result = graph.invoke({"messages": [HumanMessage(content="crawl https://example.test")]})
    assert result["messages"][-1].content == "done"
    assert model.rounds == 2
    assert all("probe_http" in names and "advanced_repair" not in names for names in model.bound_names)
    assert "advanced_repair" in captured["setup"].deferred_names


def test_embedded_client_honors_opt_in_profile_and_preload(monkeypatch):
    from deerflow import client as client_module

    cfg = AgentConfig(name="crawler", prompt_profile="crawler_compact", tool_names=[], preload_tools=["probe_http"])
    client = object.__new__(client_module.DeerFlowClient)
    client._app_config = _app()
    client._agent_name = "crawler"
    client._available_skills = None
    client._checkpoint_channel_mode = "full"
    client._checkpoint_snapshot_frequency = 10
    client._agent = None
    client._agent_config_key = None
    client._middlewares = []
    client._checkpointer = object()
    monkeypatch.setattr("deerflow.config.agents_config.load_agent_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(client_module, "get_enabled_skills_for_config", lambda *args, **kwargs: [])
    monkeypatch.setattr(client_module, "build_middlewares", lambda *args, **kwargs: [])
    monkeypatch.setattr(client_module, "create_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(client_module, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(client_module, "apply_prompt_template", lambda **kwargs: pytest.fail("compact client must render only its selected prompt"))
    monkeypatch.setattr(client, "_get_tools", lambda **kwargs: [tag_mcp_tool(probe_http.model_copy()), tag_mcp_tool(advanced_repair.model_copy()), forbidden_write])
    monkeypatch.setattr(prompt, "get_agent_soul", lambda *args, **kwargs: "<soul>embedded compact workflow</soul>")
    client._ensure_agent({"configurable": {"thinking_enabled": False}}, context={"user_id": "test-user"})
    assert {t.name for t in client._agent["tools"]} == {"probe_http", "advanced_repair", "tool_search"}
    assert "embedded compact workflow" in client._agent["system_prompt"]
    assert "untrusted" in client._agent["system_prompt"]
