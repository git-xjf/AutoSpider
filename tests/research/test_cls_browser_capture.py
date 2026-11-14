"""No live network: the new capture boundary is tested with mock responses."""
from hashlib import sha256
import json
import os

import pytest

from scripts import capture_cls_browser_resources as capture

HTML = b'<!doctype html><html><head><meta charset="utf-8"><title>Public article</title></head><body>Public text</body></html>'
ROBOTS = b"User-agent: *\nAllow: /\n"


@pytest.mark.parametrize("url,method,allowed", [
    (capture.ENTRIES[0], "GET", True),
    ("https://wwwjs.cls.cn/_next/static/a.js", "GET", True),
    ("https://cls.cn/data", "GET", True),
    ("https://other.cls.cn/a.js", "GET", False),
    ("https://www.cls.cn.evil.example/a", "GET", False),
    ("http://www.cls.cn/a", "GET", False),
    ("https://www.cls.cn/a", "HEAD", False),
    ("https://www.cls.cn/a", "POST", False),
    ("https://www.cls.cn/a?token=secret", "GET", False),
    ("https://www.cls.cn/%6cogin", "GET", False),
    ("https://user:password@www.cls.cn/a", "GET", False),
    ("https://127.0.0.1/a", "GET", False),
])
def test_exact_authorized_scope(url, method, allowed):
    assert capture.scope(url, method)["allowed"] is allowed


@pytest.mark.parametrize("body,reason", [
    (b'const publicUrl="https://www.cls.cn/a?q=a%20b#fragment";', None),
    (b'{"token":"do-not-save-this"}', "possible_sensitive_literal_in_body"),
    (b'{"token":""}', None),
    (b'Authorization: Bearer ABCDEFGHIJKLMNOP', "possible_bearer_credential"),
    (b'-----BEGIN PRIVATE KEY-----', "possible_private_key"),
    (b'https://www.cls.cn/a?access_token=private', "sensitive_query_value_in_body"),
    (b'var invalid = "\xff";', "text_encoding_unverified"),
])
def test_quarantine_not_lossy_rewriting(body, reason):
    assert capture.sensitive_body_reason(body) == reason


def mock_collector(tmp_path, monkeypatch, responses):
    calls, configurations, guards = [], [], []

    class Response:
        def __init__(self, value):
            self.status_code, self.headers, self.body = value
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def iter_raw(self, chunk_size):
            for offset in range(0, len(self.body), chunk_size):
                yield self.body[offset:offset + chunk_size]

    class Client:
        def __init__(self, **config):
            configurations.append(config)
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def stream(self, method, url):
            assert method == "GET"
            calls.append(url)
            assert url in responses, "Unexpected network/redirect must not occur."
            return Response(responses[url])

    monkeypatch.setattr(capture.httpx, "Client", Client)
    monkeypatch.setattr(capture.common, "assert_public_host", guards.append)
    collector = capture.Capture(tmp_path / "evidence")
    return collector, calls, configurations, guards


def test_missing_robots_no_target_request(tmp_path, monkeypatch):
    collector, calls, _, guards = mock_collector(tmp_path, monkeypatch, {
        "https://www.cls.cn/robots.txt": (404, {"content-type": "text/html"}, HTML)})
    assert collector.get_resource(capture.ENTRIES[0], "document") is None
    assert collector.get_resource(capture.ENTRIES[1], "document") is None
    assert calls == ["https://www.cls.cn/robots.txt"]
    assert guards == ["www.cls.cn"]
    assert collector.finish()["replay_ready"] is False


def test_save_public_bytes_and_exact_request_key_without_cookies(tmp_path, monkeypatch):
    url = "https://www.cls.cn/data?q=a%20b"
    body = b'const publicUrl="https://www.cls.cn/a?q=a%20b#fragment";'
    collector, calls, configurations, _ = mock_collector(tmp_path, monkeypatch, {
        "https://www.cls.cn/robots.txt": (200, {"content-type": "text/plain"}, ROBOTS),
        url: (200, {"content-type": "application/javascript", "set-cookie": "anonymous=do-not-save"}, body)})
    meta, received = collector.get_resource(url)
    assert received == body
    assert meta["url"] == url
    assert meta["saved_sha256"] == sha256(body).hexdigest()
    assert (collector.output / meta["saved_file"]).read_bytes() == body
    assert meta["headers"]["set-cookie"] == "[REDACTED]"
    assert "set-cookie" not in meta["browser_headers"]
    assert collector.get_resource(url)[1] == body
    assert len(calls) == 2
    assert all(not item["trust_env"] and not item["follow_redirects"] for item in configurations)
    assert all(set(item["headers"]) == {"User-Agent", "Accept-Encoding"} for item in configurations)
    assert "do-not-save" not in json.dumps(collector.finish())


def test_sensitive_response_never_saved_or_returned(tmp_path, monkeypatch):
    url = capture.ENTRIES[0]
    body = HTML.replace(b"Public text", b'{"token":"private-data-value"}')
    collector, _, _, _ = mock_collector(tmp_path, monkeypatch, {
        "https://www.cls.cn/robots.txt": (200, {"content-type": "text/plain"}, ROBOTS),
        url: (200, {}, body)})
    assert collector.get_resource(url, "document") is None
    assert not list(collector.output.glob("resource-*.bin"))
    assert "private-data-value" not in json.dumps(collector.finish())


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_or_rate_limit_stops_all_further_gets(tmp_path, monkeypatch, status):
    collector, calls, _, _ = mock_collector(tmp_path, monkeypatch, {
        "https://www.cls.cn/robots.txt": (200, {"content-type": "text/plain"}, ROBOTS),
        capture.ENTRIES[0]: (status, {}, b"")})
    assert collector.get_resource(capture.ENTRIES[0], "document") is None
    assert collector.get_resource(capture.ENTRIES[1], "document") is None
    assert calls == ["https://www.cls.cn/robots.txt", capture.ENTRIES[0]]
    assert collector.halt_reason == "authentication_denial_or_rate_limit"


def test_missing_cdn_robots_stops_before_browser(tmp_path, monkeypatch):
    markup = HTML.replace(b"</head>", b'<script src="https://wwwjs.cls.cn/a.js"></script></head>')
    collector, calls, _, _ = mock_collector(tmp_path, monkeypatch, {
        "https://www.cls.cn/robots.txt": (200, {"content-type": "text/plain"}, ROBOTS),
        capture.ENTRIES[0]: (200, {}, markup), capture.ENTRIES[1]: (200, {}, markup),
        "https://wwwjs.cls.cn/robots.txt": (404, {"content-type": "text/html"}, HTML)})
    monkeypatch.setattr(collector, "browser_capture", lambda: pytest.fail("No browser without required robots"))
    collector.run()
    report = collector.finish()
    assert len(calls) == 4
    assert report["saved_resources"] == 2
    assert not report["browser_started"]
    assert not report["observed_execution_complete"]
    assert report["status"] == "PENDING_INCOMPLETE"


def test_capture_directory_is_never_overwritten(tmp_path):
    destination = tmp_path / "evidence"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        capture.Capture(destination)


def test_cli_restores_temporary_mode_on_failure(tmp_path, monkeypatch, capsys):
    name = "AUTOSPIDER_FAKE_IP_MODE"
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(capture.sys, "argv", ["capture", "--output-dir", str(tmp_path / "evidence"), "--verified-public-once"])
    def fail(_):
        assert os.environ[name] == "verified_public"
        raise RuntimeError("do not copy sensitive transport details")
    monkeypatch.setattr(capture.Capture, "run", fail)
    capture.main()
    assert name not in os.environ
    result = json.loads((tmp_path / "evidence/report.json").read_text(encoding="utf-8"))
    assert result["mode_restored"]
    assert result["error_class"] == "RuntimeError"
    assert result["status"] == "PENDING_INCOMPLETE"
    assert "sensitive transport" not in json.dumps(result)


def test_request_cap_prevents_even_robots(tmp_path, monkeypatch):
    collector, calls, _, _ = mock_collector(tmp_path, monkeypatch, {})
    collector.report["network_requests"] = capture.LIMITS["get_requests"]
    assert collector.get_resource(capture.ENTRIES[0], "document") is None
    assert not calls


def test_auth_endpoint_is_not_requested_and_halts(tmp_path, monkeypatch):
    collector, calls, _, _ = mock_collector(tmp_path, monkeypatch, {})
    assert collector.get_resource("https://www.cls.cn/%6cogin", "document") is None
    assert collector.halt_reason == "login_or_verification_endpoint"
    assert not calls


def test_real_offline_browser_only_executes_mocked_public_responses(tmp_path, monkeypatch):
    script_url = "https://wwwjs.cls.cn/synthetic-test-only.js"
    script = b"document.getElementById('target').textContent='SYNTHETIC_BROWSER_EXECUTED';"
    markup = HTML.replace(b"</head>", ('<script defer src="' + script_url + '"></script></head>').encode())
    markup = markup.replace(b"Public text", b'<div id="target" class="detail-content">Initial synthetic value</div>')
    collector, calls, _, _ = mock_collector(tmp_path, monkeypatch, {
        "https://www.cls.cn/robots.txt": (200, {"content-type": "text/plain"}, ROBOTS),
        "https://wwwjs.cls.cn/robots.txt": (200, {"content-type": "text/plain"}, ROBOTS),
        capture.ENTRIES[0]: (200, {}, markup), capture.ENTRIES[1]: (200, {}, markup),
        script_url: (200, {"content-type": "application/javascript"}, script)})
    collector.run()
    result = collector.finish()
    assert result["browser_started"] and result["javascript_executed"]
    assert not result["browser_direct_network"]
    assert not result["replay_ready"]  # capture is never a separate offline recheck
    assert len(result["pages"]) == 2
    expected = sha256(b"SYNTHETIC_BROWSER_EXECUTED").hexdigest()
    for page in result["pages"]:
        body = next(group for group in page["candidates"] if group["selector"] == ".detail-content")
        assert body["count"] == 1 and body["texts"][0]["sha256_utf8"] == expected
        assert not page["errors"]
    assert calls.count(script_url) == 1
    assert result["network_requests"] == len(calls) == 5
    assert "SYNTHETIC_BROWSER_EXECUTED" not in json.dumps(result)
