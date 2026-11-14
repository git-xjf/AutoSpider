import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from packages.compiler import PlaywrightCompiler
from packages.crawl_core.errors import AdapterError
from packages.executor import TaskRepository
from packages.executor import repository as repository_module


FIXTURES = Path(__file__).parents[1] / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_repository_recovers_execution_orphaned_by_service_restart(tmp_path):
    root = tmp_path / "runtime"
    executions = root / "executions"
    executions.mkdir(parents=True)
    (root / "tasks").mkdir()
    (root / "runs").mkdir()
    path = executions / "7.json"
    path.write_text(json.dumps({"id": 7, "status": "running"}), encoding="utf-8")

    repository = TaskRepository(root)

    assert repository.get_execution(7)["status"] == "interrupted"


def seed_execution(root: Path, *, owner_pid=None, include_owner=True, status="running"):
    executions = root / "executions"
    executions.mkdir(parents=True)
    payload = {"id": 7, "status": status}
    if include_owner:
        payload["owner_pid"] = owner_pid
    (executions / "7.json").write_text(json.dumps(payload), encoding="utf-8")


def test_repository_start_records_current_process_as_execution_owner(tmp_path):
    root = tmp_path / "runtime"
    seed_execution(root, include_owner=False, status="created")
    repository = TaskRepository(root)
    payload = repository.update_execution(7, status="starting", owner_pid=123456)
    assert payload["owner_pid"] == os.getpid()


def test_repository_open_does_not_interrupt_live_execution_owned_by_same_process(tmp_path):
    root = tmp_path / "runtime"
    seed_execution(root, owner_pid=os.getpid())
    first = TaskRepository(root)
    second = TaskRepository(root)
    assert first.get_execution(7)["status"] == "running"
    assert second.get_execution(7)["status"] == "running"
    assert "error" not in second.get_execution(7)


def test_repository_skips_other_live_process_but_recovers_dead_owner(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    seed_execution(root, owner_pid=123456, status="starting")
    monkeypatch.setattr(repository_module, "_process_is_alive", lambda pid: pid == 123456, raising=False)
    assert TaskRepository(root).get_execution(7)["status"] == "starting"
    monkeypatch.setattr(repository_module, "_process_is_alive", lambda pid: False)
    assert TaskRepository(root).get_execution(7)["status"] == "interrupted"


@pytest.mark.parametrize("owner_pid", [None, 0, -1, True, "123456", 2**63])
def test_repository_recovers_invalid_owner_pid_without_a_process_signal(tmp_path, monkeypatch, owner_pid):
    root = tmp_path / "runtime"
    seed_execution(root, owner_pid=owner_pid)
    def forbidden_signal(*args, **kwargs):
        raise AssertionError("invalid PID must not receive a signal")
    monkeypatch.setattr(os, "kill", forbidden_signal)
    assert TaskRepository(root).get_execution(7)["status"] == "interrupted"


def test_process_alive_current_pid_is_true_without_signalling(monkeypatch):
    def forbidden_signal(*args, **kwargs):
        raise AssertionError("current PID must not receive a signal")
    monkeypatch.setattr(os, "kill", forbidden_signal)
    assert repository_module._process_is_alive(os.getpid()) is True


@pytest.mark.parametrize("exit_code,expected", [(259, True), (0, False), (1, False)])
def test_windows_owner_liveness_uses_query_api_and_closes_handle(monkeypatch, exit_code, expected):
    import ctypes

    kernel = Mock()
    kernel.OpenProcess.return_value = 123
    def get_exit_code(handle, pointer):
        pointer._obj.value = exit_code
        return True
    kernel.GetExitCodeProcess.side_effect = get_exit_code
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(os, "kill", Mock(side_effect=AssertionError("Windows must not signal a process")))

    assert repository_module._windows_process_is_alive(123456) is expected
    kernel.OpenProcess.assert_called_once_with(0x1000, False, 123456)
    kernel.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("last_error,expected", [(87, False), (5, True), (123, True)])
def test_windows_unknown_query_failures_conservatively_preserve_owner(monkeypatch, last_error, expected):
    import ctypes

    kernel = Mock()
    kernel.OpenProcess.return_value = None
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", Mock(return_value=last_error), raising=False)

    assert repository_module._windows_process_is_alive(123456) is expected
    kernel.CloseHandle.assert_not_called()


def test_windows_exit_code_query_failure_is_conservative_and_closes_handle(monkeypatch):
    import ctypes

    kernel = Mock()
    kernel.OpenProcess.return_value = 123
    kernel.GetExitCodeProcess.return_value = False
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)

    assert repository_module._windows_process_is_alive(123456) is True
    kernel.CloseHandle.assert_called_once_with(123)


def test_repository_rejects_mutated_or_incomplete_compiled_tasks(tmp_path):
    repository = TaskRepository(tmp_path / "runtime")
    compiler = PlaywrightCompiler()
    spec = load("crawl_spec.json")
    page = load("page_model.json")
    from packages.crawl_core.planner import build_crawl_plan
    from packages.crawl_core.models import CrawlSpec, PageModel

    plan = build_crawl_plan(CrawlSpec.model_validate(spec), PageModel.model_validate(page))
    task = compiler.compile(spec, plan, sample_size=3)
    assert repository.register_task(task) == 1

    task["crawl_plan"]["steps"].append("version")
    with pytest.raises(AdapterError, match="integrity|validation"):
        repository.register_task(task)

    task = compiler.compile(spec, plan, sample_size=3)
    del task["task_hash"]
    with pytest.raises(AdapterError, match="integrity"):
        repository.register_task(task)
