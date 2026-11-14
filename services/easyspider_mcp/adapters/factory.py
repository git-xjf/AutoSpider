from __future__ import annotations

import os

from .base import EasySpiderAdapter
from .cli import CLIAdapter
from .http import HTTPAdapter
from .playwright import PlaywrightAdapter


def build_adapter() -> EasySpiderAdapter:
    mode = os.getenv("AUTOSPIDER_ENGINE", os.getenv("EASYSPIDER_MODE", "native")).lower()
    if mode in {"playwright", "native"}:
        return PlaywrightAdapter(os.getenv("AUTOSPIDER_RUNTIME_ROOT", "runtime/playwright"))
    home = os.getenv("EASYSPIDER_HOME", "./EasySpider/ElectronJS")
    data_root = os.getenv("EASYSPIDER_DATA_ROOT", os.path.join(home, "Data"))
    if mode == "cli":
        return CLIAdapter(home, os.getenv("EASYSPIDER_EXECUTABLE"))
    if mode != "http":
        raise ValueError("AUTOSPIDER_ENGINE must be 'native', 'playwright', 'http' or 'cli'")
    return HTTPAdapter(
        os.getenv("EASYSPIDER_BASE_URL", "http://127.0.0.1:8074"),
        data_root,
        float(os.getenv("EASYSPIDER_HTTP_TIMEOUT", "30")),
    )
