from __future__ import annotations

import argparse
import copy
import shutil
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROVIDERS = {
    "glm": {
        "default_model": "glm-5.3",
        "display": "智谱 BigModel",
        "env": "ZHIPUAI_API_KEY",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
        "supports_thinking": True,
        "when_thinking_disabled": {
            "extra_body": {"thinking": {"type": "disabled"}}
        },
    },
    "deepseek": {
        "default_model": "deepseek-chat",
        "display": "DeepSeek",
        "env": "DEEPSEEK_API_KEY",
        "api_base": "https://api.deepseek.com",
        "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
        "supports_thinking": False,
    },
    "qwen": {
        "default_model": "qwen-plus",
        "display": "通义千问 DashScope",
        "env": "DASHSCOPE_API_KEY",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "use": "langchain_openai:ChatOpenAI",
        "supports_thinking": False,
    },
}


def dotenv_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    prefix = f"{name}="
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            value = line[len(prefix) :].strip().strip('"').strip("'")
            return value or None
    return None


def build_model_config(
    provider_name: str,
    *,
    model_id: str | None = None,
    api_base: str | None = None,
    thinking: bool | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """Build model-specific request profiles without persisting credentials."""
    provider = PROVIDERS[provider_name]
    model_id = model_id or provider["default_model"]
    is_glm53 = provider_name == "glm" and (
        model_id.lower() == "glm-5.3" or model_id.lower().startswith("glm-5.3-")
    )
    if reasoning_effort is not None and (
        not is_glm53 or reasoning_effort not in {"low", "high", "max"}
    ):
        raise ValueError("--reasoning-effort requires a GLM-5.3 model and low/high/max")
    model_config = {
        "name": model_id,
        "display_name": f"{model_id} ({provider['display']})",
        "use": provider["use"],
        "model": model_id,
        "api_base": api_base or provider["api_base"],
        "api_key": f"${provider['env']}",
        "timeout": 600.0,
        "max_retries": 2,
        "supports_thinking": provider["supports_thinking"] if thinking is None else thinking,
        # DeerFlow's effort selector offers medium, which GLM-5.3 rejects.
        # Keep the provider effort in extra_body instead of that generic control.
        "supports_reasoning_effort": False,
        "supports_vision": False,
        "stream_usage": False,
    }
    if is_glm53:
        # GLM-5.3 always thinks. The crawler and system tasks request the
        # disabled branch, so map that branch to the lowest supported effort.
        # Define both branches to prevent defaulting to the expensive max mode.
        model_config["when_thinking_enabled"] = {
            "extra_body": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": reasoning_effort or "low",
            }
        }
        model_config["when_thinking_disabled"] = {
            "extra_body": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "low",
            }
        }
    elif provider.get("when_thinking_disabled"):
        model_config["when_thinking_disabled"] = copy.deepcopy(provider["when_thinking_disabled"])
    return model_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Switch the active DeerFlow model without putting secrets in YAML."
    )
    parser.add_argument("provider", choices=sorted(PROVIDERS))
    parser.add_argument("--model", help="provider model ID; defaults to a stable profile")
    parser.add_argument(
        "--deerflow-repo",
        type=Path,
        default=PROJECT_ROOT / "deer-flow",
    )
    parser.add_argument("--api-base", help="override the provider endpoint")
    parser.add_argument(
        "--thinking",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override whether DeerFlow exposes thinking controls",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "high", "max"),
        help="GLM-5.3 thinking-enabled profile (default low); disabled profile always uses low",
    )
    args = parser.parse_args()

    repo = args.deerflow_repo.resolve()
    config_path = repo / "config.yaml"
    env_path = repo / ".env"
    if not config_path.exists():
        raise SystemExit(f"DeerFlow config not found: {config_path}")

    provider = PROVIDERS[args.provider]
    env_name = provider["env"]
    if dotenv_value(env_path, env_name) is None:
        raise SystemExit(
            f"Set {env_name} in {env_path} before selecting provider {args.provider}."
        )

    model_id = args.model or provider["default_model"]
    try:
        model_config = build_model_config(
            args.provider,
            model_id=model_id,
            api_base=args.api_base,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
        )
    except ValueError as exc:
        parser.error(str(exc))

    config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    backup_path = config_path.with_suffix(".yaml.model.bak")
    shutil.copy2(config_path, backup_path)
    config["models"] = [model_config]
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Active DeerFlow model: {model_id}")
    print(f"Provider key variable: {env_name}")
    print(f"Config: {config_path}")


if __name__ == "__main__":
    main()
