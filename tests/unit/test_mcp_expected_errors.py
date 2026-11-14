"""Expected probe failures are structured data, not repeated ToolExceptions."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from packages.crawl_core.errors import PolicyViolation
from services.easyspider_mcp import server


URL = "https://example.com/news/"


def payload(result):
    if isinstance(result, tuple):
        return result[1]
    return result


def response(raw="<html><h1>Title</h1><p>Body</p></html>", content_type="text/html"):
    return httpx.Response(
        200,
        text=raw,
        headers={"content-type": content_type},
        request=httpx.Request("GET", URL),
    )


@pytest.mark.asyncio
async def test_one_bad_xpath_returns_field_diagnostic_and_keeps_good_fields(
    monkeypatch,
):
    fetch = AsyncMock(return_value=response())
    monkeypatch.setattr(server, "_probe_http_response", fetch)
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select",
            {
                "url": URL,
                "selectors": {"title": "//h1", "date": "//*[re:test(text(),'\\d{4}')]"},
            },
        )
    )
    assert result["status"] == "validation_error"
    assert result["code"] == "invalid_selector"
    assert result["fields"]["title"] == {"count": 1, "samples": ["Title"]}
    assert "date" not in result["fields"]
    assert result["field_errors"]["date"]["parameter"] == "selectors.date"
    assert result["field_errors"]["date"]["code"] == "unsupported_xpath_namespace"
    assert result["retry_limit"] == 1
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_all_invalid_selectors_rejected_before_network(monkeypatch):
    fetch = AsyncMock(return_value=response())
    monkeypatch.setattr(server, "_probe_http_response", fetch)
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select",
            {
                "url": URL,
                "selectors": {"title": "//h1["},
            },
        )
    )
    assert result["status"] == "validation_error"
    assert result["field_errors"]["title"]["parameter"] == "selectors.title"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_schema_failure_is_bounded_and_does_not_echo_input(monkeypatch):
    fetch = AsyncMock(return_value=response())
    monkeypatch.setattr(server, "_probe_http_response", fetch)
    secret = "DO_NOT_ECHO_THIS_INPUT_TOKEN"
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select",
            {
                "url": URL,
                "selectors": {"title": "//h1"},
                "max_samples": secret,
            },
        )
    )
    assert result["status"] == "validation_error"
    assert result["parameter"] == "max_samples"
    assert result["retry_limit"] == 1
    assert secret not in json.dumps(result)
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_zero_matches_still_ready(monkeypatch):
    monkeypatch.setattr(
        server, "_probe_http_response", AsyncMock(return_value=response())
    )
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select",
            {
                "url": URL,
                "selectors": {"title": "//h2"},
            },
        )
    )
    assert result["status"] == "ready"
    assert result["fields"]["title"] == {"count": 0, "samples": []}
    assert "field_errors" not in result


@pytest.mark.asyncio
async def test_json_cannot_be_silently_tested_as_html(monkeypatch):
    monkeypatch.setattr(
        server,
        "_probe_http_response",
        AsyncMock(return_value=response('{"title":"News"}', "application/json")),
    )
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select",
            {
                "url": URL,
                "selectors": {"title": "//h1"},
            },
        )
    )
    assert result["status"] == "response_error"
    assert result["code"] == "response_format_mismatch"
    assert result["response_format"] == "json"
    assert "http_api" in result["next_action"]
    assert "fields" not in result


@pytest.mark.asyncio
async def test_timeout_is_limited_and_post_not_marked_safe_to_repeat(monkeypatch):
    secret = "sensitive-request-details"
    monkeypatch.setattr(
        server, "_probe_http_response", AsyncMock(side_effect=httpx.ReadTimeout(secret))
    )
    get = payload(await server.mcp.call_tool("probe_http", {"url": URL}))
    post = payload(
        await server.mcp.call_tool("probe_http", {"url": URL, "method": "POST"})
    )
    assert get["status"] == post["status"] == "network_error"
    assert get["code"] == post["code"] == "request_timeout"
    assert get["retryable"] is True and get["retry_limit"] == 1
    assert post["retryable"] is False and post["retry_limit"] == 0
    assert secret not in json.dumps([get, post])


@pytest.mark.asyncio
async def test_policy_failure_is_nonrepairable_not_transport_retry(monkeypatch):
    monkeypatch.setattr(
        server,
        "_probe_http_response",
        AsyncMock(side_effect=PolicyViolation("private target forbidden")),
    )
    result = payload(await server.mcp.call_tool("probe_http", {"url": URL}))
    assert result["status"] == "blocked"
    assert result["code"] == "policy_blocked"
    assert result["retryable"] is False and result["retry_limit"] == 0
    assert result["requires_user_confirmation"] is True
    assert result["needs_reanalysis"] is False


@pytest.mark.asyncio
async def test_unexpected_failure_not_hidden_as_success_or_retried(monkeypatch):
    secret = "SECRET_IN_INTERNAL_EXCEPTION"
    monkeypatch.setattr(
        server, "_probe_http_response", AsyncMock(side_effect=RuntimeError(secret))
    )
    result = payload(await server.mcp.call_tool("probe_http", {"url": URL}))
    assert result["status"] == "tool_error"
    assert result["code"] == "internal_error"
    assert result["retryable"] is False
    assert result["incident_id"]
    assert secret not in json.dumps(result)


@pytest.mark.asyncio
async def test_empty_html_is_parse_error_not_selector_failure(monkeypatch):
    monkeypatch.setattr(
        server, "_probe_http_response", AsyncMock(return_value=response(""))
    )
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select",
            {
                "url": URL,
                "selectors": {"title": "//h1"},
            },
        )
    )
    assert result["status"] == "response_error"
    assert result["code"] == "invalid_html"
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_unknown_session_receipt_has_bounded_recovery():
    result = payload(
        await server.mcp.call_tool("probe_snapshot", {"session_id": "unknown-probe"})
    )
    assert result["status"] == "resource_error"
    assert result["code"] == "session_not_found"
    assert result["parameter"] == "session_id"
    assert result["retry_limit"] == 1


@pytest.mark.asyncio
async def test_http_503_post_has_no_blind_retry_or_query_secret_echo(monkeypatch):
    secret = "SECRET_IN_HTTP_ERROR_URL"
    url = URL + "?token=" + secret
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.request.return_value = httpx.Response(
        503, request=httpx.Request("POST", url)
    )
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(server.policy, "check_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.policy, "robots_allowed", lambda *args, **kwargs: True)
    result = payload(
        await server.mcp.call_tool("probe_http", {"url": url, "method": "POST"})
    )
    assert result["status"] == "http_error" and result["status_code"] == 503
    assert result["retryable"] is False and result["retry_limit"] == 0
    assert secret not in json.dumps(result)
    client.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_robots_receipt_is_nonretryable_and_no_query_secret_echo(monkeypatch):
    secret = "SECRET_IN_ROBOTS_ERROR_URL"
    url = URL + "?token=" + secret
    monkeypatch.setattr(server.policy, "check_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.policy, "robots_allowed", lambda *args, **kwargs: False)
    result = payload(await server.mcp.call_tool("probe_http", {"url": url}))
    assert result["status"] == "blocked" and result["code"] == "policy_blocked"
    assert result["retryable"] is False
    assert secret not in json.dumps(result)


@pytest.mark.asyncio
async def test_output_validation_failure_not_mislabelled_retryable_input_error():
    from services.easyspider_mcp.tool_errors import AutoSpiderMCP

    mcp = AutoSpiderMCP("test-errors")
    calls = []

    @mcp.tool()
    async def mutate_once() -> dict[str, str]:
        calls.append(True)
        return None

    result = payload(await mcp.call_tool("mutate_once", {}))
    assert result["status"] == "tool_error" and result["code"] == "internal_error"
    assert result["retryable"] is False and result["retry_limit"] == 0
    assert calls == [True]


@pytest.mark.asyncio
async def test_many_selector_errors_stay_bounded_and_keep_each_field_name(monkeypatch):
    fetch = AsyncMock(return_value=response())
    monkeypatch.setattr(server, "_probe_http_response", fetch)
    selectors = {
        f"field_{index}_" + "x" * 60: "//*[re:test(text(),'x')]" for index in range(20)
    }
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select", {"url": URL, "selectors": selectors}
        )
    )
    assert result["status"] == "validation_error"
    assert set(result["field_errors"]) == set(selectors)
    assert len(json.dumps(result, ensure_ascii=False)) <= 6000
    assert all(
        receipt["code"] == "unsupported_xpath_namespace"
        for receipt in result["field_errors"].values()
    )
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_large_select_samples_bounded_without_losing_counts(monkeypatch):
    raw = (
        "<html>" + "".join("<p>" + "正文" * 500 + "</p>" for _ in range(3)) + "</html>"
    )
    monkeypatch.setattr(
        server, "_probe_http_response", AsyncMock(return_value=response(raw))
    )
    selectors = {f"field_{index}": "//p" for index in range(20)}
    result = payload(
        await server.mcp.call_tool(
            "probe_http_select",
            {"url": URL, "selectors": selectors, "max_samples": 3, "max_chars": 1000},
        )
    )
    assert result["status"] == "ready"
    assert set(result["fields"]) == set(selectors)
    assert all(field["count"] == 3 for field in result["fields"].values())
    assert result["preview_truncated"] is True and result["samples_omitted"] > 0
    assert len(json.dumps(result, ensure_ascii=False)) <= 6000


@pytest.mark.asyncio
async def test_empty_overview_returns_parse_failure_not_input_error(monkeypatch):
    monkeypatch.setattr(
        server, "_probe_http_response", AsyncMock(return_value=response(""))
    )
    result = payload(await server.mcp.call_tool("probe_http", {"url": URL}))
    assert result["status"] == "response_error" and result["code"] == "invalid_html"
    assert result["retryable"] is False
