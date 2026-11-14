"""Real MCP acceptance for recoverable probe errors, without paid model calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from verify_network_profile import ROOT, payload


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="https://ge.cri.cn/20260930/c48b39cc-2bb2-4508-b770-a22e46f37998.html",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Also verify one real read-only browser probe",
    )
    args = parser.parse_args()
    config = json.loads(
        (ROOT / "deer-flow/extensions_config.json").read_text(encoding="utf-8-sig")
    )["mcpServers"]["autospider"]
    target = ROOT / "runtime/probe-error-verification" / uuid.uuid4().hex
    target.mkdir(parents=True)
    env = {
        **os.environ,
        **config.get("env", {}),
        "AUTOSPIDER_MEMORY_DB": str(target / "site_memory.db"),
        "AUTOSPIDER_RUNTIME_ROOT": str(target / "runtime"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    parameters = StdioServerParameters(
        command=config["command"], args=config["args"], env=env, cwd=str(ROOT)
    )
    report = {
        "model_calls": 0,
        "url": args.url,
        "isolated_runtime": str(target),
        "checks": [],
    }
    started = time.monotonic()

    async with asyncio.timeout(180):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(label, name, arguments):
                    result = await session.call_tool(name, arguments)
                    data = payload(result)
                    receipt = {
                        "check": label,
                        "is_error": bool(result.isError),
                        **{
                            key: data[key]
                            for key in [
                                "status",
                                "code",
                                "parameter",
                                "valid",
                                "retry_limit",
                            ]
                            if key in data
                        },
                    }
                    if "fields" in data:
                        receipt["field_counts"] = {
                            key: value["count"] for key, value in data["fields"].items()
                        }
                    if "field_errors" in data:
                        receipt["field_errors"] = {
                            key: value["code"]
                            for key, value in data["field_errors"].items()
                        }
                    report["checks"].append(receipt)
                    print(json.dumps(receipt, ensure_ascii=False), flush=True)
                    assert not result.isError, (
                        f"Unexpected protocol ToolException in {label}"
                    )
                    return data

                bad = "//div[contains(@class,'secondPage-details-1')]//*[re:test(text(),'\\d{4}-\\d{2}-\\d{2}')]"
                data = await call(
                    "actual_re_test_preflight",
                    "probe_http_select",
                    {"url": args.url, "selectors": {"date": bad}},
                )
                assert data["status"] == "validation_error"
                assert (
                    data["field_errors"]["date"]["code"]
                    == "unsupported_xpath_namespace"
                )

                data = await call(
                    "mixed_fields_keep_valid_title",
                    "probe_http_select",
                    {"url": args.url, "selectors": {"title": "//title", "date": bad}},
                )
                assert (
                    data["status"] == "validation_error"
                    and data["fields"]["title"]["count"] == 1
                )
                assert "date" not in data["fields"]

                data = await call(
                    "xpath_1_valid_selector",
                    "probe_http_select",
                    {
                        "url": args.url,
                        "selectors": {
                            "title": "//title",
                            "text": "//div[contains(@class,'secondPage-details-1')]",
                        },
                        "max_chars": 100,
                    },
                )
                assert (
                    data["status"] == "ready" and data["fields"]["title"]["count"] == 1
                )

                data = await call(
                    "schema_argument_error",
                    "probe_http_select",
                    {
                        "url": args.url,
                        "selectors": {"title": "//title"},
                        "max_samples": 99,
                    },
                )
                assert (
                    data["status"] == "validation_error"
                    and data["parameter"] == "max_samples"
                )

                data = await call(
                    "stale_probe_session",
                    "probe_snapshot",
                    {"session_id": "expired-verification-probe"},
                )
                assert (
                    data["status"] == "resource_error"
                    and data["code"] == "session_not_found"
                )

                data = await call(
                    "private_network_still_blocked",
                    "probe_http",
                    {"url": "http://127.0.0.1/"},
                )
                assert data["status"] == "blocked" and data["retryable"] is False

                if args.browser:
                    data = await call(
                        "real_browser_open", "probe_open", {"url": args.url}
                    )
                    assert data["status"] == "ready"
                    session_id = data["session_id"]
                    try:
                        data = await call(
                            "browser_invalid_selector",
                            "selector_test",
                            {
                                "session_id": session_id,
                                "selector_type": "xpath",
                                "selector": "//div[",
                            },
                        )
                        assert (
                            data["valid"] is False
                            and data["code"] == "selector_evaluation_failed"
                        )
                        data = await call(
                            "browser_valid_title",
                            "selector_test",
                            {
                                "session_id": session_id,
                                "selector_type": "xpath",
                                "selector": "//title",
                            },
                        )
                        assert data["valid"] is True and data["match_count"] == 1
                    finally:
                        await session.call_tool(
                            "probe_close", {"session_id": session_id}
                        )
    report["status"] = "PASS"
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    report_path = target / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "report": str(report_path),
                "elapsed_seconds": report["elapsed_seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
