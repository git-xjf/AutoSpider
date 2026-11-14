"""Verify the installed MCP network profile without invoking a paid model.

Uses an isolated runtime and site-memory database. Probes keep robots and
private-network checks enabled; only compact receipts are written to the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


def payload(result) -> dict:
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    blocks = [item.text for item in result.content if item.type == "text"]
    raw = "\n".join(blocks)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": raw[:600]}
    return value if isinstance(value, dict) else {"value": value}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extensions", type=Path, default=ROOT / "deer-flow/extensions_config.json"
    )
    args = parser.parse_args()
    server = json.loads(args.extensions.read_text(encoding="utf-8-sig"))["mcpServers"][
        "autospider"
    ]
    verification_root = ROOT / "runtime/network-verification" / uuid.uuid4().hex
    verification_root.mkdir(parents=True)
    env = {**os.environ, **server.get("env", {})}
    env.update(
        AUTOSPIDER_MEMORY_DB=str(verification_root / "site_memory.db"),
        AUTOSPIDER_RUNTIME_ROOT=str(verification_root / "runtime"),
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    report = {
        "model_calls": 0,
        "isolated_runtime": str(verification_root),
        "domain_scope": env.get("AUTOSPIDER_DOMAIN_SCOPE"),
        "fake_ip_mode": env.get("AUTOSPIDER_FAKE_IP_MODE"),
        "probes": [],
        "boundaries": [],
    }
    assert report["domain_scope"] == "site"
    assert report["fake_ip_mode"] == "verified_public"
    parameters = StdioServerParameters(
        command=server["command"], args=server["args"], env=env, cwd=str(ROOT)
    )
    started = time.monotonic()
    async with asyncio.timeout(240):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                draft_result = await session.call_tool(
                    "crawl_draft_create",
                    {
                        "url": "https://cnews.chinadaily.com.cn/5bd5693aa3101a87ca8ff676",
                        "field_selectors": {"title": "//title"},
                        "mode": "http_html",
                        "page_limit": 1,
                        "item_limit": 1,
                    },
                )
                draft = payload(draft_result)
                assert not draft_result.isError and draft.get("status") == "ready", (
                    draft
                )
                assert draft["allowed_domains"] == ["chinadaily.com.cn"], draft
                report["draft_allowed_domains"] = draft["allowed_domains"]
                targets = [
                    (
                        "https://cnews.chinadaily.com.cn/5bd5693aa3101a87ca8ff676",
                        draft["allowed_domains"],
                    ),
                    ("https://jl.chinadaily.com.cn/", draft["allowed_domains"]),
                    ("https://sx.chinadaily.com.cn/", draft["allowed_domains"]),
                    ("https://cn.chinadaily.com.cn/", draft["allowed_domains"]),
                    ("https://ge.cri.cn/policy", None),
                ]
                for url, allowed in targets:
                    arguments = {"url": url, "respect_robots": True}
                    if allowed is not None:
                        arguments["allowed_domains"] = allowed
                    probe_started = time.monotonic()
                    result = await session.call_tool("probe_http", arguments)
                    data = payload(result)
                    receipt = {
                        "url": url,
                        "status": data.get("status"),
                        "status_code": data.get("status_code"),
                        "elapsed_seconds": round(time.monotonic() - probe_started, 2),
                        "is_error": bool(result.isError),
                    }
                    if result.isError or data.get("status") != "ready":
                        receipt["error"] = data.get("error", data.get("reason"))
                    report["probes"].append(receipt)
                    print(json.dumps(receipt, ensure_ascii=False), flush=True)
                boundaries = [
                    ("private_loopback", "http://127.0.0.1/", None),
                    (
                        "unrelated_site",
                        "https://ge.cri.cn/policy",
                        draft["allowed_domains"],
                    ),
                    (
                        "explicit_narrow_list",
                        "https://jl.chinadaily.com.cn/",
                        ["cnews.chinadaily.com.cn"],
                    ),
                ]
                for name, url, allowed in boundaries:
                    arguments = {"url": url, "respect_robots": True}
                    if allowed is not None:
                        arguments["allowed_domains"] = allowed
                    result = await session.call_tool("probe_http", arguments)
                    data = payload(result)
                    error = data.get("error", "")
                    expected = (
                        "private, loopback, link-local or reserved target"
                        if name == "private_loopback"
                        else "is not in allowed_domains"
                    )
                    receipt = {
                        "check": name,
                        "blocked": (
                            data.get("status") == "blocked"
                            and data.get("code") == "policy_blocked"
                            and data.get("retryable") is False
                        )
                        or (bool(result.isError) and expected in error),
                    }
                    if not receipt["blocked"]:
                        receipt["error"] = error
                    report["boundaries"].append(receipt)
                    print(json.dumps(receipt), flush=True)
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    report["status"] = (
        "PASS"
        if all(
            not row["is_error"] and row["status"] == "ready" for row in report["probes"]
        )
        and all(row["blocked"] for row in report["boundaries"])
        else "FAIL"
    )
    report_path = verification_root / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({"status": report["status"], "report": str(report_path)}), flush=True
    )
    assert report["status"] == "PASS", "See the compact report for failed checks"


if __name__ == "__main__":
    asyncio.run(main())
