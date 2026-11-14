import copy

import pytest

from scripts.configure_llm import PROVIDERS, build_model_config


@pytest.mark.parametrize("model_id", ["glm-5.3", "GLM-5.3", "glm-5.3-flash", "glm-5.3-flashx"])
def test_glm53_disabled_runtime_uses_low_instead_of_disabled(model_id):
    profile = build_model_config("glm", model_id=model_id)

    for mode in ("when_thinking_enabled", "when_thinking_disabled"):
        assert profile[mode]["extra_body"] == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
        }
    assert profile["supports_reasoning_effort"] is False
    assert profile["api_key"] == "$ZHIPUAI_API_KEY"


def test_glm53_high_is_only_used_by_enabled_runtime():
    profile = build_model_config("glm", model_id="glm-5.3", reasoning_effort="high")

    assert profile["when_thinking_enabled"]["extra_body"]["reasoning_effort"] == "high"
    assert profile["when_thinking_disabled"]["extra_body"]["reasoning_effort"] == "low"


@pytest.mark.parametrize("provider,model_id", [("glm", "glm-5.1"), ("deepseek", "deepseek-chat"), ("qwen", "qwen-plus")])
def test_other_model_profiles_do_not_inherit_glm53_parameters(provider, model_id):
    profile = build_model_config(provider, model_id=model_id)

    assert "when_thinking_enabled" not in profile
    if provider == "glm":
        assert profile["when_thinking_disabled"]["extra_body"]["thinking"]["type"] == "disabled"
    else:
        assert "when_thinking_disabled" not in profile
    with pytest.raises(ValueError, match="GLM-5.3"):
        build_model_config(provider, model_id=model_id, reasoning_effort="high")


def test_model_profiles_are_independent():
    providers_before = copy.deepcopy(PROVIDERS)
    first = build_model_config("glm", model_id="glm-5.3")
    first["when_thinking_disabled"]["extra_body"]["thinking"]["type"] = "disabled"
    first["when_thinking_enabled"]["extra_body"]["reasoning_effort"] = "max"

    assert PROVIDERS == providers_before
    second = build_model_config("glm", model_id="glm-5.3")
    assert second["when_thinking_disabled"]["extra_body"]["thinking"]["type"] == "enabled"
    assert second["when_thinking_enabled"]["extra_body"]["reasoning_effort"] == "low"


def test_no_thinking_ui_flag_cannot_disable_glm53_provider_thinking():
    profile = build_model_config("glm", model_id="glm-5.3", thinking=False)
    assert profile["supports_thinking"] is False
    assert profile["when_thinking_disabled"]["extra_body"]["thinking"]["type"] == "enabled"
