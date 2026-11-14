from __future__ import annotations

import asyncio
import csv
import json

import pytest

from services.easyspider_mcp.adapters.cli import CLIAdapter


class _Process:
    pid = 4321
    returncode = None

    async def communicate(self):
        return b"", b""


@pytest.mark.asyncio
async def test_cli_execution_is_local_and_headless(tmp_path, monkeypatch):
    executable = tmp_path / "easyspider_executestage.exe"
    executable.touch()
    instance_dir = tmp_path / "execution_instances"
    instance_dir.mkdir()
    (instance_dir / "7.json").write_text(json.dumps({"id": 7}), encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    adapter = CLIAdapter(str(tmp_path), str(executable))

    result = await adapter.execution_start(7, headless=True)

    assert result == {"started": True, "execution_id": 7, "pid": 4321}
    assert captured["args"] == (
        str(executable),
        "--id",
        "[7]",
        "--headless",
        "1",
        "--read_type",
        "local",
    )
    assert captured["kwargs"]["cwd"] == tmp_path.resolve()


@pytest.mark.asyncio
async def test_cli_logs_recognize_completed_files_after_restart(tmp_path):
    output_dir = tmp_path / "Data" / "Task_7"
    output_dir.mkdir(parents=True)
    (output_dir / "sample_log.txt").write_text("openPage\nDone!\n", encoding="utf-8")
    with (output_dir / "sample.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title"])
        writer.writeheader()
        writer.writerow({"title": "One article"})

    adapter = CLIAdapter(str(tmp_path), str(tmp_path / "unused.exe"))

    result = await adapter.execution_logs(7)

    assert result["status"] == "completed"
    assert "Done!" in result["logs"]
