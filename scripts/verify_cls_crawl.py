"""Opt-in live regression: actual MCP -> sample QA -> 50 main-column articles.

Run with mytorch from the repository root. Reads the deployed MCP environment;
does not change network policy, submit an LLM chat, or spend model API credits.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.cls.cn/depth?id=1118"


async def main():
    config = json.loads((ROOT / "deer-flow/extensions_config.json").read_text(encoding="utf-8"))["mcpServers"]["autospider"]
    # Separate repository to avoid recovering/interfering with live DeerFlow runs.
    output = ROOT / "runtime/verification" / time.strftime("cls-1118-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    env = {**config["env"], "AUTOSPIDER_RUNTIME_ROOT": str(output / "engine"), "PYTHONUTF8": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=config["args"], env=env, cwd=str(ROOT))
    spec = {
        "request_id": output.name, "start_urls": [URL], "objective": "财联社券商栏目50条新闻",
        "fields": [
            {"name": "title", "description": "新闻标题", "type": "string", "required": True, "page_scope": "list"},
            {"name": "url", "description": "详情链接", "type": "url", "required": True, "page_scope": "list", "extract": {"source": "attribute", "attribute": "href"}},
            {"name": "publish_time", "description": "页面原始发布时间文本（可能为相对时间）", "type": "string", "required": True, "page_scope": "list"},
            {"name": "summary", "description": "页面可用的新闻摘要", "type": "string", "required": False, "page_scope": "list"},
        ],
        "traversal": {"mode": "list", "item_limit": 50, "max_pages": 6, "pagination": {"strategy": "click", "max_pages": 6}},
        "execution": {"request_delay_ms": 1000, "max_run_seconds": 180},
        "policy": {"allowed_domains": ["www.cls.cn"], "respect_robots": True, "block_private_networks": True},
        "output": {"format": "json"},
    }
    def mapping(selector, **kwargs):
        return {"page_scope": "list", "selector": selector, "selector_type": "css", "confidence": 1, **kwargs}
    page = {
        "url": URL, "page_type": "PAGINATED_LIST", "confidence": 1,
        "list": {"item_selector": {"type": "css", "value": ".w-894 > .b-t-w-1 > .c-b"}},
        "fields": {
            "title": mapping("a.f-w-b[href^='/detail/']"),
            "url": mapping("a.f-w-b[href^='/detail/']", extract="attribute", attribute="href"),
            "publish_time": mapping(".p-a.r-0.b-0 > span:first-child"),
            "summary": mapping(".line2"),
        },
        "pagination": {"type": "click", "selector": ".w-894 .w-162.c-p", "selector_type": "css"},
        "waits": {"timeout_seconds": 30},
        "evidence": ["Verified rendered main-column news cards and load-more button; sidebar and image-link duplicates excluded."],
    }
    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save("crawl_spec.json", spec)
    save("page_model.json", page)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            async def call(name, **kwargs):
                response = await session.call_tool(name, kwargs)
                if response.isError:
                    raise RuntimeError(f"{name}: {response.content}")
                return response.structuredContent
            # Verify the same guarded probe used by the analyst, not an unguarded browser.
            probe = await call("probe_open", url=URL)
            if probe.get("status") != "ready":
                raise RuntimeError(probe)
            try:
                for _ in range(30):
                    tested = await call("selector_test", session_id=probe["session_id"], selector_type="css", selector=page["list"]["item_selector"]["value"])
                    if tested.get("count", tested.get("match_count", 0)):
                        break
                    await asyncio.sleep(0.5)
                save("selector_evidence.json", tested)
                assert tested.get("count", tested.get("match_count", 0)) > 0, tested
                snapshot = await call("probe_snapshot", session_id=probe["session_id"])
                page["fingerprint"] = snapshot["fingerprint"]
                save("page_model.json", page)
            finally:
                await call("probe_close", session_id=probe["session_id"])
            plan = await call("crawl_plan_build", crawl_spec=spec, page_model=page)
            save("crawl_plan.json", plan)
            for label, sample_size, expected in [("sample", 5, 5), ("full", None, 50)]:
                started = time.monotonic()
                compiled = await call("task_compile", crawl_spec=spec, crawl_plan=plan, sample_size=sample_size)
                save(f"{label}.task.json", compiled["task"])
                registered = await call("task_register", task=compiled["task"])
                execution = await call("execution_create", task_id=registered["task_id"])
                execution_id = execution["execution_id"]
                await call("execution_start", execution_id=execution_id)
                async with asyncio.timeout(240):
                    while True:
                        status = await call("execution_logs", execution_id=execution_id, wait_seconds=30)
                        print(label, status["status"], status.get("record_count"), status.get("error"), flush=True)
                        if status["status"] not in {"starting", "running"}:
                            break
                save(f"{label}.status.json", status)
                if status["status"] != "completed":
                    raise RuntimeError(status)
                result = await call("execution_result", execution_id=execution_id, limit=expected)
                records = result["records"]
                assert len(records) == result["record_count"] == expected, result
                assert len({r["url"] for r in records}) == expected
                report = await call("qa_validate", crawl_spec=spec, records=records)
                save(f"{label}.validation.json", report)
                save(f"{label}.json", records)
                assert report["status"] == "PASS", report
                save(f"{label}.manifest.json", {**execution, **registered, "seconds": round(time.monotonic() - started, 2), "count": len(records)})
                print(label, "PASS", len(records), "elapsed", round(time.monotonic() - started, 2), flush=True)
            # Memory stores selectors only; do not link a task ID from isolated verification storage.
            committed = await call("memory_commit_validated", page_model=page, crawl_plan=plan, crawl_spec=spec, validation_report=report)
            save("memory_commit.json", committed)
    print("OUTPUT", output, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
