"""Compare the archived original HTTP dispatch with the fixed implementation.

Only synthetic URLs and MockTransport are used. The archived code is accepted
only if its hash matches the exact pre-fix project source recorded here.
"""
import argparse
import asyncio
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from packages.executor.http import HttpExecutor

OLD = ROOT / "runtime/experiments/patent-parameter-binding-20261026/http-before-query-fix.py"
OLD_SHA256 = "1d36eb9247082b2c97f13af799497a3a6dde21d55b558d8ecdfd61fa64979c73"


class Policy:
    def __init__(self):
        self.checked = []
    def check_url(self, url, **_):
        assert url.startswith("https://binding.example.test/")
        self.checked.append(url)


async def compare():
    if sha256(OLD.read_bytes()).hexdigest() != OLD_SHA256:
        raise ValueError("archived pre-fix source hash mismatch")
    name = "packages.executor._archived_parameter_query_probe"
    spec = importlib.util.spec_from_file_location(name, OLD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    rows = []
    url = "https://binding.example.test/list?channel=10&page_size=10"
    for label, cls in (("archived_original", module.HttpExecutor), ("fixed_current", HttpExecutor)):
        for extra in ({}, {"page": 2}, {"channel": 37}):
            received = []
            def handler(request):
                received.append(str(request.url))
                return httpx.Response(200, text="synthetic fixture")
            policy = Policy()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                state = SimpleNamespace(client=client, robots_checked=set(), spec=SimpleNamespace(
                    start_urls=[url], policy=SimpleNamespace(allowed_domains=["binding.example.test"],
                    block_private_networks=True, respect_robots=False)))
                await cls(None, policy)._request(state, "GET", url, params=extra, json_body=None, form_body=None)
            rows.append({"implementation": label, "explicit_params": extra, "actual_request_url": received[0],
                         "policy_checked_same_url_as_sent": policy.checked == received,
                         "mock_requests": len(received), "external_requests": 0})
    return {"assessment_date": "2026-10-26", "archived_source_sha256": OLD_SHA256,
            "fixed_source_sha256": sha256((ROOT / "packages/executor/http.py").read_bytes()).hexdigest(),
            "installed_httpx_version": httpx.__version__, "cases": rows,
            "scope": "Actual archived/current _request methods; no live network or production database.",
            "conclusion": "URL query loss is transport behavior, not numeric-alias memory generalization or a new invention."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(compare())
    result["runner_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
    with args.output.open("xb") as handle:
        handle.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
