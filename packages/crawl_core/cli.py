from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from packages.compiler.playwright import PlaywrightCompiler
from packages.crawl_core.models import CrawlSpec, PageModel
from packages.crawl_core.planner import build_crawl_plan
from packages.validator import validate_records

app = typer.Typer(help="AutoSpider V0.1 deterministic control-plane utilities")
console = Console()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@app.command("compile")
def compile_workflow(
    spec_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    page_model_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("runtime/outputs/compile"),
    sample_size: Annotated[int | None, typer.Option("--sample-size")] = None,
) -> None:
    """Build CrawlPlan and compile a native HTTP or Playwright task."""
    spec = CrawlSpec.model_validate(_read_json(spec_path))
    page = PageModel.model_validate(_read_json(page_model_path))
    plan = build_crawl_plan(spec, page)
    task = PlaywrightCompiler().compile(spec, plan, sample_size=sample_size)
    _write_json(output_dir / "crawl_plan.json", plan.model_dump(mode="json", exclude_none=True))
    _write_json(output_dir / "autospider_task.json", task)
    console.print(f"[green]Compiled[/green] native {task['engine']} task into {output_dir}")


@app.command("validate")
def validate_output(
    spec_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    records_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("validation_report.json"),
) -> None:
    """Validate extracted JSON records against CrawlSpec."""
    report = validate_records(_read_json(spec_path), _read_json(records_path))
    _write_json(output, report.model_dump(mode="json", exclude_none=True))
    console.print(f"[{('green' if report.status == 'PASS' else 'red')}]{report.status}[/] score={report.overall_score:.3f}")
    if report.status != "PASS":
        raise typer.Exit(2)


@app.command()
def doctor() -> None:
    """Check the active interpreter and important integration settings."""
    env_name = os.getenv("CONDA_DEFAULT_ENV", "")
    in_mytorch = (
        env_name == "mytorch"
        or Path(env_name).name.lower() == "mytorch"
        or Path(sys.prefix).name.lower() == "mytorch"
    )
    runtime_root = Path(os.getenv("AUTOSPIDER_RUNTIME_ROOT", "runtime/playwright")).resolve()
    rows = [
        ("Python", sys.executable, "OK"),
        ("Conda env", "mytorch" if in_mytorch else (env_name or "not reported"), "OK" if in_mytorch else "WARN"),
        ("Crawler service", os.getenv("AUTOSPIDER_ENGINE", "native"), "OK"),
        ("Runtime root", str(runtime_root), "INFO"),
    ]
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError as exc:
        rows.append(("Dependencies", str(exc), "FAIL"))
    else:
        rows.append(("Dependencies", "MCP + Playwright", "OK"))
    runtime_root.mkdir(parents=True, exist_ok=True)
    rows.append(("Native executors", "http_html + http_api + playwright", "OK"))
    for name, value, status in rows:
        typer.echo(f"{status:4} | {name:16} | {value}")


if __name__ == "__main__":
    app()
