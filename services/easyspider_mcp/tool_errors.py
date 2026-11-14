"""Compact, redacted failure receipts at the actual MCP protocol boundary.

Underlying functions still raise typed failures for deterministic callers. MCP
clients receive actionable data, including argument validation before function
dispatch. Failed operations are never labelled ready, and stateful operations
are never recommended for blind retries.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from packages.crawl_core.errors import CompileError, InspectionError, PolicyViolation
from packages.inspector.http_summary import SelectorValidationError


logger = logging.getLogger(__name__)
_SAFE_PARAMETER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,99}$")
_PROBE_TOOLS = {
    "probe_http",
    "probe_http_select",
    "probe_open",
    "probe_snapshot",
    "probe_network",
    "network_jsonpath_test",
    "selector_test",
    "probe_close",
}


class ProbeToolError(ValueError):
    """An expected probe failure with a developer-authored, safe receipt."""

    def __init__(
        self,
        code: str,
        error: str,
        *,
        status: str = "response_error",
        parameter: str | None = None,
        next_action: str,
        **metadata: Any,
    ):
        super().__init__(error)
        self.receipt = failure_receipt(
            status,
            code,
            error,
            parameter=parameter,
            next_action=next_action,
            **metadata,
        )


def failure_receipt(
    status: str,
    code: str,
    error: str,
    *,
    parameter: str | None = None,
    retryable: bool = False,
    retry_limit: int = 0,
    next_action: str,
    **metadata: Any,
) -> dict[str, Any]:
    result = {
        "status": status,
        "code": code,
        "error": error,
        "retryable": retryable,
        "retry_limit": retry_limit,
        "next_action": next_action,
        **metadata,
    }
    if parameter is not None:
        result["parameter"] = (
            parameter if _SAFE_PARAMETER.fullmatch(parameter) else "arguments"
        )
    return result


def _causes(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen and len(seen) < 12:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__


def _safe_to_repeat(tool: str, arguments: dict[str, Any]) -> bool:
    return tool in _PROBE_TOOLS and (
        tool != "probe_http" or str(arguments.get("method", "GET")).upper() == "GET"
    )


def _validation_receipt(exc: ValidationError) -> dict[str, Any]:
    # Never stringify Pydantic's exception: its default includes input_value.
    errors = exc.errors(include_input=False, include_context=False, include_url=False)
    fields = []
    for error in errors[:3]:
        location = ".".join(str(item) for item in error.get("loc", ()))
        fields.append(
            {
                "parameter": location
                if _SAFE_PARAMETER.fullmatch(location)
                else "arguments",
                "code": error["type"],
            }
        )
    return failure_receipt(
        "validation_error",
        "invalid_arguments",
        "Tool arguments do not match the documented input contract.",
        parameter=fields[0]["parameter"] if fields else "arguments",
        retryable=True,
        retry_limit=1,
        details=fields,
        next_action="Correct only the reported argument once using the tool's existing input contract. Do not retry unchanged inputs, guess schemas, or load all schemas. Stop if the corrected call still fails.",
    )


def _failure_for(
    tool: str, arguments: dict[str, Any], exc: BaseException
) -> dict[str, Any]:
    chain = list(_causes(exc))
    # A policy failure may be wrapped by a provider error. It always takes priority.
    if any(isinstance(item, PolicyViolation) for item in chain):
        return failure_receipt(
            "blocked",
            "policy_blocked",
            "The request is blocked by domain, network or robots policy.",
            parameter="url",
            requires_user_confirmation=True,
            needs_reanalysis=False,
            next_action="Stop all transport and selector retries. Report the policy block and request administrator guidance; never change hosts, disable guards, skip blocked records or switch modes to bypass it.",
        )
    for item in chain:
        if isinstance(item, ProbeToolError):
            return item.receipt
        if isinstance(item, SelectorValidationError):
            if item.code == "html_parse_error":
                return failure_receipt(
                    "response_error",
                    "invalid_html",
                    str(item),
                    parameter="response_format",
                    next_action=item.hint,
                )
            return failure_receipt(
                "validation_error",
                item.code,
                str(item),
                parameter=item.parameter,
                retryable=True,
                retry_limit=1,
                next_action=item.hint
                + " Correct only this parameter once; never retry unchanged input or switch transports for a syntax error.",
            )
    timed_out = next(
        (item for item in chain if isinstance(item, httpx.TimeoutException)), None
    )
    transport_error = next(
        (item for item in chain if isinstance(item, httpx.TransportError)), None
    )
    if timed_out is not None or transport_error is not None:
        repeat = _safe_to_repeat(tool, arguments)
        return failure_receipt(
            "network_error",
            "request_timeout" if timed_out is not None else "network_unavailable",
            "The HTTP probe timed out."
            if timed_out is not None
            else "The HTTP connection failed.",
            parameter="url",
            retryable=repeat,
            retry_limit=int(repeat),
            next_action="A read-only GET probe may be retried once after checking connectivity; stop if it fails again. Do not restart running tasks or repeat POST/state-changing requests automatically.",
        )
    inspection = next(
        (item for item in chain if isinstance(item, InspectionError)), None
    )
    if inspection is not None:
        code = getattr(inspection, "code", "inspection_failed")
        if code in {"session_not_found", "session_closed"}:
            return failure_receipt(
                "resource_error",
                code,
                "The probe session is unknown, expired or closed.",
                parameter="session_id",
                retryable=True,
                retry_limit=1,
                next_action="If browser evidence is still required, reopen the original verified URL once and use the returned new session_id; close owned unused probes. Do not repeat calls with the stale ID or restart crawl executions.",
            )
        if code == "navigation_timeout":
            return failure_receipt(
                "network_error",
                code,
                "Browser navigation timed out.",
                parameter="url",
                retryable=True,
                retry_limit=1,
                next_action="A read-only browser probe may be reopened once after checking connectivity; stop on repeated failure. Never retry policy blocks or restart crawl executions.",
            )
        return failure_receipt(
            "resource_error",
            code,
            "The browser probe could not complete.",
            next_action="Use already verified compact evidence, close owned unused probes, or ask the administrator to check the browser service. Do not repeat the same failed probe.",
        )
    if isinstance(chain[-1], ValueError) and tool in _PROBE_TOOLS:
        return failure_receipt(
            "validation_error",
            "invalid_probe_input",
            "The probe input is invalid for this operation.",
            retryable=True,
            retry_limit=1,
            next_action="Correct the reported input once using the documented tool contract; do not retry unchanged calls or guess alternate schemas.",
        )
    if any(isinstance(item, CompileError) for item in chain):
        return failure_receipt(
            "validation_error",
            "invalid_crawl_plan",
            "The crawl plan cannot be compiled with the requested execution settings.",
            next_action="Use a new draft built from verified evidence. Do not modify signed tasks or repeat registration/execution blindly.",
        )
    incident_id = uuid4().hex[:12]
    logger.error(
        "AutoSpider tool failure: tool=%s type=%s incident=%s",
        tool,
        type(chain[-1]).__name__,
        incident_id,
    )
    return failure_receipt(
        "tool_error",
        "internal_error",
        "An unexpected tool failure requires administrator investigation.",
        incident_id=incident_id,
        next_action="Stop and report the incident_id. Do not repeat the call, restart executions, switch modes or label partial results complete.",
    )


class AutoSpiderMCP(FastMCP):
    """Keep SDK input validation and business errors out of ToolException loops."""

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            return failure_receipt(
                "validation_error",
                "unknown_tool",
                "The requested tool is not registered on this server.",
                retryable=True,
                retry_limit=1,
                next_action="Discover the exact needed registered tool once. Do not invent names or retry the unknown name.",
            )
        # Validate before dispatch so no partially executed mutation is labelled
        # an input error. Preserve FastMCP's existing JSON-string coercion.
        try:
            parsed = tool.fn_metadata.pre_parse_json(arguments)
            tool.fn_metadata.arg_model.model_validate(parsed)
        except ValidationError as exc:
            return _validation_receipt(exc)
        except Exception as exc:
            return _failure_for(name, arguments, exc)
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            return _failure_for(name, arguments, exc)
