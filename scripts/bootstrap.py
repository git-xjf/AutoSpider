from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

DEERFLOW_PIN = "9ce6fdcb2252ea005b4fa98ca50bd3857e1f681a"


def merge_dict(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".autospider.bak"))


def git_head(repo: Path) -> str | None:
    try:
        top_level = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        # Vendored source belongs to AutoSpider's repository. Its parent HEAD
        # is not the upstream DeerFlow revision recorded in UPSTREAM.lock.
        if Path(top_level).resolve() != repo.resolve():
            return None
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Install AutoSpider into a DeerFlow 2.0 checkout")
    parser.add_argument("--deerflow-repo", required=True, type=Path)
    parser.add_argument("--deerflow-home", type=Path, help="defaults to <repo>/.deer-flow")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--easyspider-home", type=Path, help="deprecated; retained for CLI compatibility")
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[1]
    repo = args.deerflow_repo.resolve()
    if not (repo / "config.example.yaml").exists():
        raise SystemExit(f"not a DeerFlow 2.0 checkout: {repo}")
    current = git_head(repo)
    if current and current != DEERFLOW_PIN:
        print(f"WARNING: DeerFlow HEAD is {current}; V0.1 was verified at {DEERFLOW_PIN}")

    deerflow_home = (args.deerflow_home or repo / ".deer-flow").resolve()
    config_path = repo / "config.yaml"
    if not config_path.exists():
        shutil.copy2(repo / "config.example.yaml", config_path)
    base_config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    patch_config = yaml.safe_load((project / "deerflow/config.autospider.patch.yaml").read_text(encoding="utf-8"))
    patch_config["subagents"]["custom_agents"]["web-analyst"]["system_prompt"] = (
        project / "deerflow/agents/web-analyst/SYSTEM.md"
    ).read_text(encoding="utf-8")
    patch_config["subagents"]["custom_agents"]["qa-repair"]["system_prompt"] = (
        project / "deerflow/agents/qa-repair/SYSTEM.md"
    ).read_text(encoding="utf-8")
    merged_config = merge_dict(base_config, patch_config)
    backup(config_path)
    config_path.write_text(yaml.safe_dump(merged_config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    extensions_path = repo / "extensions_config.json"
    base_extensions = json.loads(extensions_path.read_text(encoding="utf-8-sig")) if extensions_path.exists() else {}
    extension_patch = json.loads((project / "deerflow/extensions_config.json").read_text(encoding="utf-8"))
    base_extensions.get("mcpServers", {}).pop("easyspider", None)
    base_extensions.get("skills", {}).pop("easyspider-workflow", None)
    server = extension_patch["mcpServers"]["autospider"]
    server["command"] = sys.executable
    server["env"]["PYTHONPATH"] = str(project)
    server["env"]["AUTOSPIDER_MEMORY_DB"] = str(project / "data/site_memory.db")
    server["env"]["AUTOSPIDER_RUNTIME_ROOT"] = str(project / "runtime/playwright")
    merged_extensions = merge_dict(base_extensions, extension_patch)
    backup(extensions_path)
    extensions_path.write_text(json.dumps(merged_extensions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    user_root = deerflow_home / "users" / args.user_id
    agent_target = user_root / "agents" / "crawler-lead"
    skill_target = user_root / "skills" / "custom"
    agent_target.mkdir(parents=True, exist_ok=True)
    skill_target.mkdir(parents=True, exist_ok=True)
    legacy_skill = skill_target / "easyspider-workflow"
    if legacy_skill.exists():
        shutil.rmtree(legacy_skill)
    shutil.copy2(project / "deerflow/agents/crawler-lead/config.yaml", agent_target / "config.yaml")
    shutil.copy2(project / "deerflow/agents/crawler-lead/SOUL.md", agent_target / "SOUL.md")
    for skill in (project / "deerflow/skills").iterdir():
        if skill.is_dir():
            shutil.copytree(skill, skill_target / skill.name, dirs_exist_ok=True)

    print("AutoSpider DeerFlow integration installed")
    print(f"  config:      {config_path}")
    print(f"  extensions:  {extensions_path}")
    print(f"  agent:       {agent_target}")
    print(f"  skills:      {skill_target}")
    print(f"  DEER_FLOW_HOME={deerflow_home}")


if __name__ == "__main__":
    main()
