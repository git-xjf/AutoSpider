"""Keep the opt-in crawler profile lean without changing generic DeerFlow."""

from pathlib import Path
import json

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_crawler_profile_preloads_only_the_compact_normal_path():
    config = yaml.safe_load((ROOT / "deerflow/agents/crawler-lead/config.yaml").read_text(encoding="utf-8"))
    assert config["prompt_profile"] == "crawler_compact"
    assert set(config["preload_tools"]) == {
        "autospider_memory_lookup_site",
        "autospider_crawl_reuse_workflow",
        "autospider_probe_http",
        "autospider_probe_http_select",
        "autospider_crawl_draft_create",
        "autospider_crawl_draft_execute",
    }
    assert set(config["tool_names"]) == {"task", "ask_clarification", "present_files", "read_file", "describe_skill"}
    assert config["token_budget"]["max_tokens"] == 200000
    assert config["allowed_subagents"] == ["web-analyst", "qa-repair"]
    assert config["force_subagent_enabled"] is True


def test_compact_profile_does_not_change_global_discovery_or_transport_defaults():
    patch = yaml.safe_load((ROOT / "deerflow/config.autospider.patch.yaml").read_text(encoding="utf-8"))
    assert patch["tool_search"]["enabled"] is True
    assert patch["tool_search"]["auto_promote_top_k"] == 2
    assert "prompt_profile" not in patch
    for path in (ROOT / "deerflow/agents").glob("*/config.yaml"):
        if path.parent.name != "crawler-lead":
            assert yaml.safe_load(path.read_text(encoding="utf-8")).get("prompt_profile", "standard") == "standard"


def test_crawler_soul_stays_bounded_and_keeps_execution_boundaries():
    soul = (ROOT / "deerflow/agents/crawler-lead/SOUL.md").read_text(encoding="utf-8")
    assert len(soul) <= 3500
    for required in ("detail_fields", "FIELD NAMES", "http_api", "http_html", "playwright", "200,000", "untrusted", "Private/reserved-address", "SAME draft", "qa-repair", "FAIL"):
        assert required in soul


def test_qa_role_and_active_skill_allow_http_failure_diagnosis():
    patch = yaml.safe_load((ROOT / "deerflow/config.autospider.patch.yaml").read_text(encoding="utf-8"))
    assert "autospider_probe_http_select" in patch["subagents"]["custom_agents"]["qa-repair"]["tools"]
    skill = (ROOT / "deerflow/skills/crawl-validation/SKILL.md").read_text(encoding="utf-8")
    header = yaml.safe_load(skill.split("---", 2)[1])
    assert "autospider_probe_http_select" in header["allowed-tools"]
    assert "selector_no_match" in (ROOT / "deerflow/agents/qa-repair/SYSTEM.md").read_text(encoding="utf-8")


def test_integrated_proxy_profile_enables_verified_fake_ip_without_disabling_safety():
    config = json.loads((ROOT / "deerflow/extensions_config.json").read_text(encoding="utf-8"))
    environment = config["mcpServers"]["autospider"]["env"]
    assert environment["AUTOSPIDER_DOMAIN_SCOPE"] == "site"
    assert environment["AUTOSPIDER_FAKE_IP_MODE"] == "verified_public"
    assert "AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS" not in environment
    assert not any("PRIVATE" in name and value in {"0", "false"} for name, value in environment.items())
    soul = (ROOT / "deerflow/agents/crawler-lead/SOUL.md").read_text(encoding="utf-8")
    assert "No approval needed for these" in soul
    assert "failed public-DNS verification" in soul


def test_crawler_and_workers_explain_probe_error_recovery_without_transport_loops():
    soul = (ROOT / "deerflow/agents/crawler-lead/SOUL.md").read_text(encoding="utf-8")
    for required in ("XPath 1.0", "re:test", "field_errors", "once", "never POST", "Stop on internal errors"):
        assert required in soul
    for name in ["web-analyst", "qa-repair"]:
        prompt = (ROOT / f"deerflow/agents/{name}/SYSTEM.md").read_text(encoding="utf-8")
        assert "XPath 1.0" in prompt and "once" in prompt
        assert "policy/internal errors" in prompt
    selector_skill = (ROOT / "deerflow/skills/selector-engineering/SKILL.md").read_text(encoding="utf-8")
    assert "autospider_probe_http_select" in selector_skill
    assert "scope_selector must select item elements" in selector_skill
