from __future__ import annotations

import json

import pytest

from packages.inspector.http_summary import (
    SelectorValidationError,
    select_http_html,
    validate_http_select_inputs,
)


URL = "https://example.com/news/"
HTML = '<html><body><article class="item"><h1>Title</h1><a href="1.html">Link</a><span>2026-09-29</span></article></body></html>'


@pytest.mark.parametrize(
    ("selector", "code"),
    [
        (
            "//article[re:test(text(),'\\d{4}-\\d{2}-\\d{2}')]",
            "unsupported_xpath_namespace",
        ),
        ("//missing[re:test(text(),'date')]", "unsupported_xpath_namespace"),
        ("//missing[fn:matches(text(),'date')]", "unsupported_xpath_namespace"),
        ("//foo:missing", "unsupported_xpath_namespace"),
        ("//child::foo:missing", "unsupported_xpath_namespace"),
        ("//missing[matches(text(),'date')]", "unsupported_xpath_function"),
        ("//missing[ends-with(@id,'suffix')]", "unsupported_xpath_function"),
        ("//missing[lower-case(text())='date']", "unsupported_xpath_function"),
        ("//missing[$variable]", "unsupported_xpath_variable"),
        ("count(//article)", "invalid_selector_result"),
        ("boolean(//article)", "invalid_selector_result"),
        ("1 + 2", "invalid_selector_result"),
        ("//article/namespace::*", "invalid_selector_result"),
        ("//*[", "invalid_selector_syntax"),
        ("", "invalid_selector"),
    ],
)
def test_preflight_rejects_invalid_fields_without_a_network_or_real_document(
    selector, code
):
    with pytest.raises(SelectorValidationError) as caught:
        validate_http_select_inputs(
            {"publish_time": selector}, scope_selector="//missing"
        )
    error = caught.value
    assert isinstance(error, ValueError)
    assert error.code == code
    assert error.parameter == "selectors.publish_time"
    assert error.hint
    if selector:
        assert selector not in str(error)


@pytest.mark.parametrize(
    "scope",
    [
        "//article/@id",
        "//article/text()",
        "string(//missing)",
        "count(//missing)",
        "true()",
        "//comment() | string('x')",
    ],
)
def test_scope_selector_cannot_return_attributes_text_or_scalar_values(scope):
    # text() with an otherwise empty synthetic article must be demonstrated on
    # a scope that actually has text; the direct document adds that evidence.
    if scope == "//article/text()":
        with pytest.raises(SelectorValidationError, match="HTML elements"):
            select_http_html(
                "<html><body><article>Text</article></body></html>",
                URL,
                {"title": "."},
                scope_selector=scope,
            )
    else:
        with pytest.raises(SelectorValidationError) as caught:
            validate_http_select_inputs(
                {"title": "."}, scope_selector=scope, allow_partial=True
            )
        assert caught.value.parameter == "scope_selector"


@pytest.mark.parametrize(
    "selector",
    [
        "//article[contains(@class,'item')]",
        "//article[starts-with(@class,'it')]",
        "//article[position()=1]",
        "//article[contains(@class,'item') and (@id)]",
        "//article[@id or (@class)]",
        "//article[position() mod (2) = 0]",
        "//article[position() div (2) = 1]",
        "//child::article/descendant-or-self::node()",
        "//missing",
        "string(//missing)",
        "normalize-space(//h1)",
        "//article[@data-value='re:test($secret, matches(x))']",
        '//article[@title="fn:matches($secret)"]',
    ],
)
def test_preflight_accepts_standard_xpath1_and_does_not_inspect_quoted_literals(
    selector,
):
    report = validate_http_select_inputs({"title": selector})
    assert report == {"valid_fields": ["title"], "field_errors": {}}


def test_partial_mode_preserves_valid_fields_in_actual_failing_ge_cri_batch():
    selectors = {
        "e": "//article//*[re:test(text(),'\\d{4}-\\d{2}-\\d{2}')]",
        "f": "//article/*[2]",
        "g": "//span[contains(text(),'2026')]",
    }
    preflight = validate_http_select_inputs(selectors, allow_partial=True)
    assert preflight["valid_fields"] == ["f", "g"]
    assert preflight["field_errors"]["e"]["code"] == "unsupported_xpath_namespace"
    result = select_http_html(HTML, URL, selectors, allow_partial=True)
    assert set(result["fields"]) == {"f", "g"}
    assert result["fields"]["f"]["count"] == 1
    assert result["fields"]["g"]["samples"] == ["2026-09-29"]
    assert result["field_errors"]["e"]["parameter"] == "selectors.e"
    assert "e" not in result["fields"]


def test_invalid_field_does_not_become_zero_matches_when_scope_is_empty():
    result = select_http_html(
        HTML,
        URL,
        {"valid": ".//h1", "bad": ".//missing[matches(text(),'x')]"},
        scope_selector="//missing",
        allow_partial=True,
    )
    assert result["scope_count"] == 0
    assert result["fields"] == {"valid": {"count": 0, "samples": []}}
    assert result["field_errors"]["bad"]["code"] == "unsupported_xpath_function"


def test_strict_mode_does_not_hide_field_errors():
    with pytest.raises(SelectorValidationError, match="namespace"):
        select_http_html(HTML, URL, {"valid": "//h1", "bad": "//re:h1"})


@pytest.mark.parametrize(
    ("selector_type", "selector"),
    [
        ("xpath", ".item > h1"),
        ("css", "//h1"),
        ("css", "h1:has-text('Title')"),
        ("css", "a >> text=Link"),
        ("css", "["),
    ],
)
def test_css_xpath_mismatch_and_browser_only_syntax_get_static_diagnostics(
    selector_type, selector
):
    with pytest.raises(SelectorValidationError) as caught:
        validate_http_select_inputs({"title": selector}, selector_type=selector_type)
    assert caught.value.code == "invalid_selector_syntax"
    assert "selector_type" in caught.value.hint


def test_valid_success_shape_and_zero_matches_remain_unchanged():
    result = select_http_html(
        HTML,
        URL,
        {
            "title": "//h1",
            "missing": "//missing",
            "empty": "string(//missing)",
            "text": "normalize-space(//h1)",
        },
        allow_partial=True,
    )
    assert set(result) == {
        "url",
        "selector_type",
        "scope_count",
        "scopes_evaluated",
        "scope_count_truncated",
        "fields",
    }
    assert (
        result["fields"]["missing"]
        == result["fields"]["empty"]
        == {"count": 0, "samples": []}
    )
    assert result["fields"]["text"] == {"count": 1, "samples": ["Title"]}


def test_normal_css_and_relative_xpath_are_supported():
    result = select_http_html(
        HTML,
        URL,
        {"title": "h1", "url": "a"},
        selector_type="css",
        scope_selector="article.item",
        attributes={"url": "href"},
    )
    assert result["fields"]["url"]["samples"] == [URL + "1.html"]
    result = select_http_html(HTML, URL, {"title": ".//h1"}, scope_selector="//article")
    assert result["fields"]["title"]["samples"] == ["Title"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"selector_type": "browser"},
        {"selectors": {}},
        {"selectors": {" ": "//h1"}},
        {"selectors": {"bad": "x" * 2001}},
        {"attributes": ["href"]},
        {"attributes": {"other": "href"}},
        {"attributes": {"title": ""}},
        {"max_samples": True},
        {"max_chars": "100"},
        {"allow_partial": "yes"},
    ],
)
def test_invalid_argument_fields_return_typed_value_errors(kwargs):
    kwargs = {"selectors": {"title": "//h1"}, **kwargs}
    with pytest.raises(SelectorValidationError):
        validate_http_select_inputs(**kwargs)


def test_error_receipt_never_contains_selector_literal_secret_or_provider_cause():
    selector = "//h1[matches(text(),'TOP_SECRET_TOKEN')]"
    with pytest.raises(SelectorValidationError) as caught:
        validate_http_select_inputs({"title": selector})
    serialized = json.dumps(caught.value.as_dict())
    assert "TOP_SECRET_TOKEN" not in serialized
    assert selector not in serialized
    assert caught.value.__cause__ is None
    assert set(caught.value.as_dict()) == {"code", "parameter", "error", "hint"}


def test_non_html_response_is_typed_without_raw_body():
    with pytest.raises(SelectorValidationError) as caught:
        select_http_html("", URL, {"title": "//h1"})
    assert caught.value.code == "html_parse_error"
    assert caught.value.parameter == "response_format"
