"""Opt-in context controls; never replace authorization or skill policies."""

from langchain_core.tools import BaseTool

from deerflow.tools.mcp_metadata import is_mcp_tool

_COMPACT_DESCRIPTIONS = {
    "task": (
        "Delegate one bounded specialist analysis using subagent_type and prompt. Do not duplicate probes or delegate execution ownership. "
        "Include verifiable acceptance criteria; specialist reports are self-reports, not proof of execution. Observe the runtime concurrency/total caps."
    ),
    "ask_clarification": "Ask the user only for missing requirements, ambiguity, or a necessary authorization. This interrupts the run; do not emit sibling tool calls. Use question with optional options or fields.",
    "read_file": "Read a bounded local file or selected skill instructions. Never load entire crawl results, raw DOM, or schema artifacts for routine crawling.",
}
DEFAULT_CRAWLER_TOOL_NAMES = frozenset({"task", "ask_clarification", "present_files", "read_file", "describe_skill"})


def select_profile_tools(tools: list[BaseTool], names: list[str] | None, *, profile: str = "standard") -> list[BaseTool]:
    """Filter local tools without making MCP authorization less restrictive."""
    if names is None and profile != "crawler_compact":
        return tools
    wanted = DEFAULT_CRAWLER_TOOL_NAMES if names is None else set(names)
    return [tool for tool in tools if is_mcp_tool(tool) or tool.name in wanted]


def compact_tool_descriptions(tools: list[BaseTool]) -> list[BaseTool]:
    """Clone selected tools: preserve schemas/callables and the shared originals."""
    return [tool.model_copy(update={"description": _COMPACT_DESCRIPTIONS[tool.name]}) if not is_mcp_tool(tool) and tool.name in _COMPACT_DESCRIPTIONS else tool for tool in tools]
