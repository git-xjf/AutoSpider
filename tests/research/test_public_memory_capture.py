"""Read-only capture policy and redaction checks; no live network in tests."""
import json

import pytest

from scripts import capture_public_memory_pages as capture_module
from scripts.capture_public_memory_pages import (
    capture, decode_document, evaluate_robots, extract_detail_links, preflight_url,
    recognize_document, redact_headers, redact_text, redact_url, validate_capture_url,
)


def test_sensitive_query_values_are_not_preserved():
    result = redact_url("https://www.cls.cn/detail/1?id=11&token=secretTOKEN&sign=secretSIGN")
    assert "secretTOKEN" not in result
    assert "secretSIGN" not in result
    assert "id=11" in result


def test_sensitive_headers_are_not_preserved():
    result = redact_headers({"Cookie": "sid=secretSID", "Authorization": "Bearer secretTOKEN",
                             "Set-Cookie": "session=secretRESPONSE", "Content-Type": "text/html"})
    serialized = json.dumps(result)
    assert "secretSID" not in serialized
    assert "secretTOKEN" not in serialized
    assert "secretRESPONSE" not in serialized
    assert "text/html" in serialized


def test_sensitive_text_headers_are_not_preserved():
    result = redact_text("Authorization: Bearer secretTOKEN\nCookie: session=secretSID\nPublic title")
    assert "secretTOKEN" not in result
    assert "secretSID" not in result
    assert "Public title" in result


@pytest.mark.parametrize("url,method", [
    ("https://www.cls.cn.evil.invalid/depth", "GET"),
    ("https://user:secret@www.cls.cn/depth", "GET"),
    ("http://127.0.0.1/depth", "GET"),
    ("https://www.cls.cn:8443/depth", "GET"),
    ("https://www.cls.cn/login", "GET"),
    ("https://www.cls.cn/detail/1?sign=secret", "GET"),
    ("https://www.cls.cn/depth", "POST"),
])
def test_capture_scope_rejects_unsafe_or_credentialed_targets(url, method):
    assert not validate_capture_url(url, method=method)["allowed"]


def test_known_public_list_is_syntactically_in_scope():
    assert validate_capture_url("https://www.cls.cn/depth?id=1118")["allowed"]


@pytest.mark.parametrize("status", [None, 301, 401, 403, 404, 429, 500, 503])
def test_unverified_robots_never_grants_capture(status):
    assert not evaluate_robots(status, "", "https://www.cls.cn/depth")["allowed"]


def test_robots_checks_exact_public_path():
    body = "User-agent: *\nDisallow: /private\nAllow: /depth\n"
    assert evaluate_robots(200, body, "https://www.cls.cn/depth")["allowed"]
    assert not evaluate_robots(200, body, "https://www.cls.cn/private")["allowed"]
    assert not evaluate_robots(200, "<html>challenge</html>", "https://www.cls.cn/depth", content_type="text/html")["allowed"]


def test_no_detail_identifiers_are_invented():
    markup = '<a href="/detail/123">One</a><a href="https://outside.invalid/detail/456">Foreign</a>'
    assert extract_detail_links(markup, "https://www.cls.cn/depth", max_details=2) == ["https://www.cls.cn/detail/123"]
    assert extract_detail_links("<main>No anchors</main>", "https://www.cls.cn/depth") == []


def test_project_guard_failure_stops_before_any_http(tmp_path, monkeypatch):
    def blocked(_):
        raise RuntimeError("Fake-IP denied by existing project policy")

    def must_not_open_client(*args, **kwargs):
        raise AssertionError("HTTP must not begin after policy denial")

    monkeypatch.setattr(capture_module, "assert_public_host", blocked)
    monkeypatch.setattr(capture_module.httpx, "Client", must_not_open_client)
    assert preflight_url("https://www.cls.cn/depth")["status"] == "policy_blocked"
    output = tmp_path / "exclusive-evidence"
    report = capture("https://www.cls.cn/depth", output)
    assert report["status"] == "policy_blocked"
    assert report["network_requests"] == 0
    assert not report["replay_ready"]
    assert (output / "report.json").is_file()
    with pytest.raises(FileExistsError):
        capture("https://www.cls.cn/depth", output)


def test_explicit_utf8_meta_preserves_chinese_text():
    text = '<meta charset="utf-8"><p>公开新闻</p>'
    result = decode_document(text.encode("utf-8"), "text/html")
    assert result["verified"]
    assert result["text"] == text


def test_explicit_gbk_header_does_not_get_replacement_characters():
    text = "<p>公开新闻</p>"
    result = decode_document(text.encode("gbk"), "text/html; charset=gbk")
    assert result["verified"]
    assert result["text"] == text


def test_declared_encoding_failure_does_not_save_lossy_text():
    result = decode_document(b"<meta charset='utf-8'><p>\xff</p>", "text/html")
    assert not result["verified"]
    assert result["text"] is None


def test_unknown_encoding_is_not_guessed():
    result = decode_document(b"<p>Undeclared encoding</p>", "text/html")
    assert not result["verified"]


def test_missing_media_type_can_be_archived_without_forging_header():
    source = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>公开正文</body></html>'.encode("utf-8")
    result = recognize_document(source, "")
    assert result["recognized"]
    assert result["header_missing"]
    assert result["basis"] == "strict_decoding_and_html_signature"


@pytest.mark.parametrize("source,media_type", [
    (b'<html><head><meta charset="utf-8"></head></html>', "application/json"),
    (b'{"value":"<html>"}', ""),
    (b'<svg><title>Not HTML</title></svg>', ""),
    (b'%PDF-1.7', ""),
    (b'<html><p>Undeclared encoding</p></html>', ""),
    (b'<html><meta charset="utf-8"><p>\xff</p></html>', ""),
])
def test_missing_media_type_is_not_a_blanket_content_override(source, media_type):
    assert not recognize_document(source, media_type)["recognized"]


@pytest.mark.parametrize("markup", [
    '<html><head><!-- <meta charset="utf-8"> --></head><body>Text</body></html>',
    '<html><head><script>const sample=\'<meta charset="utf-8">\';</script></head><body>Text</body></html>',
    '<html><head></head><body><meta charset="utf-8">Text</body></html>',
    '<html><head><template><meta charset="utf-8"></template></head><body>Text</body></html>',
    '<html><head><title><meta charset="utf-8"></title></head><body>Text</body></html>',
])
def test_spelled_or_inert_meta_is_not_an_encoding_declaration(markup):
    assert not decode_document(markup.encode("utf-8"), "")["verified"]


def test_real_head_meta_http_equiv_can_declare_encoding():
    markup = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=gbk"></head><body>公开正文</body></html>'
    result = decode_document(markup.encode("gbk"), "")
    assert result["verified"]
    assert result["text"] == markup
