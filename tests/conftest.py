"""Keep module-level MCP stores away from the user's live crawler state."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


_runtime = None
_previous_environment = {}


def pytest_configure(config):
    global _runtime
    _runtime = TemporaryDirectory(prefix="autospider-tests-")
    root = Path(_runtime.name)
    for name, value in {
        "AUTOSPIDER_RUNTIME_ROOT": str(root / "runtime"),
        "AUTOSPIDER_MEMORY_DB": str(root / "memory.db"),
    }.items():
        _previous_environment[name] = os.environ.get(name)
        os.environ[name] = value


def pytest_unconfigure(config):
    for name, previous in _previous_environment.items():
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
    if _runtime is not None:
        _runtime.cleanup()
