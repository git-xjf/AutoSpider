"""Bounded real-model verification of the production crawler-lead graph.

Uses the configured provider and real MCP tools; it never supplies selectors or
calls crawl tools itself. Full news records are not printed into this report.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "deer-flow/.env", override=False)
os.environ["DEER_FLOW_HOME"] = str(ROOT / "deer-flow/.deer-flow")
os.environ["DEER_FLOW_CONFIG_PATH"] = str(ROOT / "deer-flow/config.yaml")
os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(ROOT / "deer-flow/extensions_config.json")


class UsageAudit(BaseCallbackHandler):
    raise_error = True

    def __init__(self):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.seen = set()

    def on_chat_model_start(self, serialized, messages, **kwargs):
        if self.calls >= 12 or self.input_tokens + self.output_tokens >= 100000:
            raise RuntimeError("Live verification safety stop: 12 model calls or 100,000 cumulative tokens")
        self.calls += 1

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if message is None or message.id in self.seen:
                    continue
                self.seen.add(message.id)
                usage = message.usage_metadata or {}
                self.input_tokens += usage.get("input_tokens", 0)
                self.output_tokens += usage.get("output_tokens", 0)
                names = [call["name"] for call in message.tool_calls]
                print(json.dumps({"model_call": self.calls, "input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0), "tools": names, "cumulative": self.input_tokens + self.output_tokens}), flush=True)
                if not names:
                    print(json.dumps({"final_preview": text_content(message.content)[:500]}, ensure_ascii=False), flush=True)

    def on_tool_end(self, output, **kwargs):
        if not isinstance(output, ToolMessage):
            return
        try:
            parsed = json.loads(text_content(output.content))
        except (ValueError, TypeError):
            return
        if not isinstance(parsed, dict):
            return
        keys = ("status", "code", "parameter", "error", "phase", "scope_count", "record_count", "needs_reanalysis", "retryable", "validation")
        receipt = {key: parsed[key] for key in keys if key in parsed}
        if "fields" in parsed and isinstance(parsed["fields"], dict):
            receipt["field_counts"] = {key: value.get("count") for key, value in parsed["fields"].items() if isinstance(value, dict)}
        if receipt:
            print(json.dumps({"tool": output.name, "receipt": receipt}, ensure_ascii=False)[:1800], flush=True)


def text_content(value):
    if isinstance(value, str):
        return value
    parts = []
    for block in value or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://cnews.chinadaily.com.cn/5bd5693aa3101a87ca8ff676")
    parser.add_argument("--min-records", type=int, default=50)
    parser.add_argument("--isolated-memory", action="store_true")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--draft-id", help="Resume a real existing draft; never supplies selectors")
    parser.add_argument("--expect-blocked", action="store_true", help="Verify a policy block stops repair/search loops")
    args = parser.parse_args()

    if args.isolated_memory:
        # Generated verification artifacts, not a mutation of the user's store.
        verification_root = ROOT / "runtime/generic-verification" / uuid.uuid4().hex
        verification_root.mkdir(parents=True)
        extensions = json.loads((ROOT / "deer-flow/extensions_config.json").read_text(encoding="utf-8"))
        tool_env = extensions["mcpServers"]["autospider"]["env"]
        tool_env["AUTOSPIDER_MEMORY_DB"] = str(verification_root / "site_memory.db")
        tool_env["AUTOSPIDER_RUNTIME_ROOT"] = str(verification_root / "runs-root")
        extensions_path = verification_root / "extensions_config.json"
        extensions_path.write_text(json.dumps(extensions, ensure_ascii=False, indent=2), encoding="utf-8")
        os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(extensions_path)
        print(json.dumps({"verification_artifacts": str(verification_root), "memory": "empty isolated store"}), flush=True)

    from deerflow.agents.lead_agent.agent import assemble_lead_agent

    user_id = args.user_id
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    audit = UsageAudit()
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id, "agent_name": "crawler-lead", "thinking_enabled": False, "subagent_enabled": True, "is_plan_mode": False}, "recursion_limit": 350, "callbacks": [audit]}
    assembly = assemble_lead_agent(config)
    started = time.monotonic()
    print(json.dumps({"phase": "real_model_graph", "thread_id": thread_id, "run_id": run_id, "agent": "crawler-lead"}), flush=True)
    prompt = f"帮我爬取{args.url}的新闻内容，2页，包含标题、链接、发布时间和完整正文。"
    if args.draft_id:
        prompt += f"上次草稿 ID 为 {args.draft_id}，请继续同一个草稿，不要重复创建或执行。"
    async with asyncio.timeout(360):
        state = await assembly.graph.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            config=config,
            context={"thread_id": thread_id, "run_id": run_id, "user_id": user_id},
        )
    messages = state["messages"]
    executed = [message for message in messages if isinstance(message, ToolMessage) and message.name == "autospider_crawl_draft_execute"]
    assert executed, "The model did not execute a crawl draft"
    result = None
    for message in reversed(executed):
        raw = text_content(message.content)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            parsed = json.loads(text_content(parsed))
        if args.expect_blocked and isinstance(parsed, dict) and parsed.get("status") == "blocked":
            assert parsed.get("retryable") is False and parsed.get("requires_user_confirmation") is True
            assert not parsed.get("validation_complete") and not parsed.get("memory_committed")
            after_block = messages[messages.index(message) + 1 :]
            calls = [call["name"] for item in after_block if isinstance(item, AIMessage) for call in item.tool_calls]
            assert all(name == "ask_clarification" for name in calls), {"unexpected_calls_after_block": calls}
            assert audit.calls <= 3, {"unexpected_model_calls": audit.calls}
            print(
                json.dumps(
                    {
                        "status": "EXPECTED_POLICY_BLOCK",
                        "model_calls": audit.calls,
                        "total_tokens": audit.input_tokens + audit.output_tokens,
                        "record_count": parsed.get("record_count"),
                        "memory_committed": False,
                        "repair_calls_after_block": 0,
                    }
                ),
                flush=True,
            )
            return
        if isinstance(parsed, dict) and parsed.get("validation", {}).get("status") == "PASS":
            result = parsed
            break
    assert result is not None, "No completed draft with PASS was returned"
    assert result.get("validation_complete") and result.get("memory_committed"), result
    path = next(Path(value) for value in result["output_files"] if value.endswith(".json"))
    records = json.loads(path.read_text(encoding="utf-8"))
    assert len(records) >= args.min_records, {"record_count": len(records)}
    fields = ["title", "url", "publish_time", "content"]
    assert all(str(row.get(name, "")).strip() for row in records for name in fields)
    assert len({row["url"] for row in records}) == len(records)
    final = next(message for message in reversed(messages) if isinstance(message, AIMessage))
    assert "TOKEN BUDGET EXCEEDED" not in text_content(final.content)
    print(
        json.dumps(
            {
                "status": "PASS",
                "model_calls": audit.calls,
                "input_tokens": audit.input_tokens,
                "output_tokens": audit.output_tokens,
                "total_tokens": audit.input_tokens + audit.output_tokens,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "record_count": len(records),
                "execution_id": result["execution_id"],
                "workflow_id": result["workflow_id"],
                "qa": result["validation"]["status"],
                "output_files": result["output_files"],
                "final_preview": text_content(final.content)[:180],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
