from services.easyspider_mcp import server


def test_required_selector_failure_has_actionable_receipt_without_fake_qa():
    result = server._classify_execution_failure({
        "execution_status": "failed",
        "error": "SelectorMatchError: selector matched no values: //time",
        "failure": {"code": "selector_no_match", "field": "publish_time",
                    "selector": "//time", "selector_type": "xpath",
                    "url": "https://example.com/article/7", "page_scope": "detail"},
    })
    assert result["status"] == "needs_repair"
    assert result["code"] == "selector_no_match"
    assert result["needs_reanalysis"] is True
    assert result["retryable"] is False  # The terminal execution must not restart.
    assert result["failure"]["field"] == "publish_time"
    assert "new draft" in result["next_action"]
    assert "validation" not in result  # No records were validated.


def test_policy_failure_wins_over_selector_diagnostic():
    result = server._classify_execution_failure({
        "execution_status": "failed", "error": "PolicyViolation: blocked",
        "failure": {"code": "selector_no_match"},
    })
    assert result["code"] == "policy_blocked"
    assert result["needs_reanalysis"] is False


def test_unknown_execution_error_is_not_a_selector_repair():
    result = server._classify_execution_failure({"execution_status": "failed", "error": "RuntimeError: broken service"})
    assert result["status"] == "tool_error"
    assert result["needs_reanalysis"] is False
    assert result["retryable"] is False


def test_finish_does_not_mark_internal_failure_for_reanalysis(monkeypatch, tmp_path):
    from test_mcp_draft_execution import setup_draft

    draft_id, _ = setup_draft(monkeypatch, tmp_path)
    spec, page, plan = server.drafts.get(draft_id)
    result = server._finish_draft_result(draft_id, spec, page, plan, {
        "phase": "sample", "execution_status": "failed", "error": "RuntimeError: broken service",
    })
    assert result["needs_reanalysis"] is False
    assert server.memory.lookup_workflows("example.com") == []


def test_failure_context_preserves_public_queries_but_omits_secret_urls():
    from packages.crawl_core.errors import SelectorMatchError

    def failure(url):
        return SelectorMatchError(field="title", selector="//h1", selector_type="xpath", url=url, page_scope="detail").diagnostic

    assert failure("https://example.com/article?id=7")["url"].endswith("?id=7")
    assert "url" not in failure("https://example.com/article?access_token=PRIVATE")
    assert "url" not in failure("https://user:PRIVATE@example.com/article")
