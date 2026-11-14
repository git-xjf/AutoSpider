"""Read local run metadata and measure request-text compaction without model calls.

Use the DeerFlow backend virtualenv. Provider credentials, complete messages and
article data are deliberately excluded from the exported diagnostic report.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def text_size(messages):
    return sum(len(m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)) for m in messages)


def audit(database: Path) -> dict:
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from deerflow.agents.middlewares.crawler_recovery_middleware import compact_crawler_context

    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT run_id,thread_id,model_name,status,stop_reason,created_at,total_input_tokens,total_output_tokens,total_tokens,llm_call_count,"
            "lead_agent_tokens,subagent_tokens,middleware_tokens,first_human_message FROM runs ORDER BY created_at DESC"
        ).fetchall()
        runs = []
        threads = {}
        for row in rows:
            run = dict(row)
            prompt = run.pop("first_human_message") or ""
            urls = []
            for match in re.findall(r'https?://[^\s"<>\u4e00-\u9fff，。；]+', prompt):
                parsed = urlsplit(match)
                if parsed.hostname:
                    urls.append(urlunsplit((parsed.scheme, parsed.hostname, parsed.path, "", "")))
            run["target_urls_without_query"] = urls[:4]
            runs.append(run)
            threads.setdefault(run["thread_id"], []).append(run)
        snapshots = []
        serde = JsonPlusSerializer()
        for thread_id, thread_runs in list(threads.items())[:12]:
            row = connection.execute(
                "SELECT type,checkpoint FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1", (thread_id,)
            ).fetchone()
            if row is None:
                continue
            checkpoint = serde.loads_typed((row["type"], row["checkpoint"]))
            messages = checkpoint.get("channel_values", {}).get("messages", [])
            before = after = calls = 0
            cached = retained_input = 0
            for i, msg in enumerate(messages):
                if isinstance(msg, AIMessage) and msg.usage_metadata:
                    calls += 1
                    usage = msg.usage_metadata
                    retained_input += usage.get("input_tokens", 0)
                    cached += usage.get("input_token_details", {}).get("cache_read", 0)
                    before += text_size(messages[:i])
                    after += text_size(compact_crawler_context(messages[:i]))
            recorded_input = sum(run["total_input_tokens"] or 0 for run in thread_runs)
            snapshots.append({
                "thread_id": thread_id,
                "retained_model_calls": calls,
                "retained_input_tokens": retained_input,
                "retained_cache_read_tokens": cached,
                "all_run_input_tokens": recorded_input,
                "cache_scope_is_complete": retained_input == recorded_input,
                "unmatched_or_compacted_old_usage": retained_input != recorded_input,
                "replayed_message_characters_before": before,
                "replayed_message_characters_after": after,
                "replayed_message_characters_saved": before - after,
                "saved_percent_of_message_characters": round((before - after) / before * 100, 2) if before else 0,
                "tool_result_characters": sum(text_size([m]) for m in messages if isinstance(m, ToolMessage)),
                "visible_user_turns": sum(isinstance(m, HumanMessage) and getattr(m, "name", None) == "user-input" for m in messages),
            })
        return {
            "assessment_date": "2026-11-01", "database_open_mode": "read_only", "provider_calls": 0,
            "note": "Historical provider usage is reported as recorded, including cached input. Character replay excludes tool schemas/system prompts not retained in checkpoints and is not a token/currency savings measurement. Incomplete snapshots are explicitly marked.",
            "runs": runs, "checkpoint_replay": snapshots,
        }
    finally:
        connection.close()


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=root / "deer-flow/backend/.deer-flow/data/deerflow.db")
    parser.add_argument("--output", type=Path, default=root / "runtime/agent-diagnostics/2026-11-01/history-audit.json")
    args = parser.parse_args()
    result = audit(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "runs": len(result["runs"]), "provider_calls": 0,
                      "latest": result["runs"][:2], "checkpoint_replay": result["checkpoint_replay"][:3]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
