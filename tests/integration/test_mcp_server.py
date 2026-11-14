import sys
import json
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_mcp_discovers_expected_capabilities(tmp_path):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "services.autospider_mcp.server"],
        env={"AUTOSPIDER_RUNTIME_ROOT": str(tmp_path / "runtime"), "AUTOSPIDER_MEMORY_DB": str(tmp_path / "memory.db")},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            schema = next(tool.inputSchema for tool in tools.tools if tool.name == "crawl_plan_build")
            logs_schema = next(tool.inputSchema for tool in tools.tools if tool.name == "execution_logs")
            reuse_schema = next(tool.inputSchema for tool in tools.tools if tool.name == "crawl_reuse_workflow")
            select_schema = next(tool.inputSchema for tool in tools.tools if tool.name == "probe_http_select")
            draft_schema = next(tool.inputSchema for tool in tools.tools if tool.name == "crawl_draft_create")
            draft_execute_schema = next(tool.inputSchema for tool in tools.tools if tool.name == "crawl_draft_execute")
            assert "crawl_spec" not in reuse_schema["properties"]
            assert "crawl_plan" not in reuse_schema["properties"]
            assert reuse_schema["properties"]["item_limit"]["maximum"] == 1000
            assert select_schema["properties"]["selectors"]["maxProperties"] == 20
            assert select_schema["properties"]["max_samples"]["maximum"] == 3
            assert "crawl_spec" not in draft_schema["properties"]
            assert "crawl_plan" not in draft_schema["properties"]
            assert "draft_id" in draft_execute_schema["required"]
            assert "item_limit" in schema["$defs"]["TraversalSpec"]["properties"]
            assert "type" in schema["$defs"]["PagePagination"]["properties"]
            assert {
                "probe_open",
                "probe_http",
                "probe_http_select",
                "probe_network",
                "network_jsonpath_test",
                "selector_test",
                "crawl_plan_build",
                "task_compile",
                "task_compile_register",
                "crawl_execute_validated",
                "crawl_reuse_workflow",
                "crawl_draft_create",
                "crawl_draft_execute",
                "execution_start",
                "execution_result_summary",
                "qa_validate_execution",
                "qa_validate",
                "memory_get_workflow",
                "memory_commit_validated",
            } <= names
            assert logs_schema["properties"]["wait_seconds"]["maximum"] == 30
            fixtures = Path(__file__).parents[1] / "fixtures"
            spec = json.loads((fixtures / "crawl_spec.json").read_text(encoding="utf-8"))
            page = json.loads((fixtures / "page_model.json").read_text(encoding="utf-8"))
            planned = await session.call_tool("crawl_plan_build", {"crawl_spec": spec, "page_model": page})
            assert planned.isError is False
            plan = planned.structuredContent
            compiled = await session.call_tool(
                "task_compile",
                {"crawl_spec": spec, "crawl_plan": plan, "sample_size": 3},
            )
            assert compiled.isError is False
            assert compiled.structuredContent["task"]["version"] == "1.0"
            assert compiled.structuredContent["task"]["engine"] == "playwright"
            assert compiled.structuredContent["task"]["schema"] == "autospider.task/v1"
            registered = await session.call_tool(
                "task_compile_register",
                {"crawl_spec": spec, "crawl_plan": plan, "sample_size": 3},
            )
            assert registered.isError is False
            assert registered.structuredContent["task_id"] == 1
            assert len(registered.structuredContent["task_hash"]) == 64
