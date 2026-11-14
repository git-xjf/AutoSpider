"""Offline integration checks for headerless archival, never live HTTP."""

from hashlib import sha256
import json

import pytest

from scripts import capture_public_memory_pages as collector


LIST_URL = "https://www.cls.cn/depth?id=1118"
ROBOTS_URL = "https://www.cls.cn/robots.txt"
HTML = '<!doctype html><html><head><meta charset="utf-8"><title>公开新闻</title></head><body>公开正文</body></html>'.encode("utf-8")


def mocked_capture(tmp_path, monkeypatch, body=HTML, *, headers=None, status=200,
                   robots_headers=None, robots_body=b"User-agent: *\nAllow: /\n"):
    requests = []
    guards = []
    client_configurations = []
    responses = {
        ROBOTS_URL: (200, {"content-type": "text/plain"} if robots_headers is None else robots_headers, robots_body),
        LIST_URL: (status, {} if headers is None else headers, body),
    }

    class Response:
        def __init__(self, entry):
            self.status_code, self.headers, self.body = entry

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def iter_raw(self, chunk_size):
            for offset in range(0, len(self.body), chunk_size):
                yield self.body[offset:offset + chunk_size]

    class Client:
        def __init__(self, **configuration):
            client_configurations.append(configuration)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def stream(self, method, url):
            assert method == "GET"
            requests.append((method, url))
            assert url in responses, "No unrecorded mock request or redirect may be followed."
            return Response(responses[url])

    def verified_guard(host):
        guards.append(host)
        assert host == "www.cls.cn"

    monkeypatch.setattr(collector, "assert_public_host", verified_guard)
    monkeypatch.setattr(collector.httpx, "Client", Client)
    directory = tmp_path / "new-exclusive-evidence"
    result = collector.capture(LIST_URL, directory, max_details=0)
    assert guards, "The project guard remains mandatory despite MIME recognition."
    assert all(item["trust_env"] is False and item["follow_redirects"] is False for item in client_configurations)
    return result, directory, requests


def test_headerless_html_is_saved_without_inventing_server_headers(tmp_path, monkeypatch):
    result, directory, requests = mocked_capture(tmp_path, monkeypatch)
    assert requests == [("GET", ROBOTS_URL), ("GET", LIST_URL)]
    assert result["network_requests"] == 2
    assert result["captured_documents"] == 1
    assert result["status"] == "document_only_capture_finished"
    assert result["replay_ready"] is False
    page = result["pages"][0]
    assert page["content_type"] == ""
    assert "content-type" not in page["headers"]
    assert page["document_recognition"] == {
        "recognized": True, "basis": "strict_decoding_and_html_signature", "header_missing": True,
    }
    assert page["original_body_sha256"] == sha256(HTML).hexdigest()
    assert page["saved_sha256"] == sha256(HTML).hexdigest()
    assert (directory / page["saved_file"]).read_bytes() == HTML
    assert not page["body_redacted"]
    assert page["encoding"] == "utf-8"
    saved_report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert saved_report["pages"][0]["content_type"] == ""


@pytest.mark.parametrize("media_type", ["application/json", "text/plain", "application/octet-stream"])
def test_explicit_wrong_mime_is_never_overridden_by_html_signature(tmp_path, monkeypatch, media_type):
    result, directory, _ = mocked_capture(tmp_path, monkeypatch, headers={"content-type": media_type})
    assert result["captured_documents"] == 0
    assert result["status"] == "no_documents_captured"
    assert result["pages"][0]["content_type"] == media_type
    assert result["pages"][0]["error"] == "explicit_non_html_media_type"
    assert not list(directory.glob("document-*"))


@pytest.mark.parametrize(
    "body,headers",
    [
        (b'<html><head><meta charset="utf-8"></head><body>\xff</body></html>', {}),
        (b'<html><body>No declared encoding</body></html>', {}),
        (HTML.replace(b"utf-8", b"definitely-unknown-codec"), {}),
        ('<html><head><meta charset="gbk"></head><body>公开正文</body></html>'.encode("gbk"), {"content-type": "text/html; charset=utf-8"}),
    ],
    ids=["invalid-byte", "no-encoding", "unknown-codec", "conflicting-explicit-header"],
)
def test_unknown_or_invalid_encoding_does_not_write_lossy_document(tmp_path, monkeypatch, body, headers):
    result, directory, _ = mocked_capture(tmp_path, monkeypatch, body, headers=headers)
    assert result["captured_documents"] == 0
    assert result["status"] == "no_documents_captured"
    assert not list(directory.glob("document-*"))
    assert "saved_file" not in result["pages"][0]


@pytest.mark.parametrize("gate", [
    '<title>验证码</title>', '<title>Just a moment</title>',
    '<title>Public title</title><input type="password">',
])
def test_html_recognition_does_not_authorize_a_verification_or_login_gate(tmp_path, monkeypatch, gate):
    body = ('<html><head><meta charset="utf-8">' + gate + '</head><body>Gate</body></html>').encode("utf-8")
    result, directory, _ = mocked_capture(tmp_path, monkeypatch, body)
    assert result["captured_documents"] == 0
    assert result["status"] == "no_documents_captured"
    assert result["pages"][0]["status"] == "verification_or_login_gate"
    assert not list(directory.glob("document-*"))


def test_missing_robots_mime_remains_fail_closed(tmp_path, monkeypatch):
    result, directory, requests = mocked_capture(tmp_path, monkeypatch, robots_headers={})
    assert requests == [("GET", ROBOTS_URL)]
    assert result["status"] == "robots_not_authorized"
    assert result["captured_documents"] == 0
    assert result["pages"] == []
    assert not list(directory.glob("document-*"))


@pytest.mark.parametrize("status", [302, 403, 503])
def test_html_body_does_not_override_redirect_or_http_failure(tmp_path, monkeypatch, status):
    result, directory, requests = mocked_capture(tmp_path, monkeypatch, status=status,
                                               headers={"location": "https://www.cls.cn/detail/123"})
    assert requests == [("GET", ROBOTS_URL), ("GET", LIST_URL)]
    assert result["captured_documents"] == 0
    assert result["status"] == "no_documents_captured"
    assert not list(directory.glob("document-*"))


def test_redacted_saved_body_has_separate_original_and_saved_hashes(tmp_path, monkeypatch):
    body = HTML.replace(b"</body>", b'<a href="/detail/1?token=secretTOKEN">Link</a></body>')
    result, directory, _ = mocked_capture(tmp_path, monkeypatch, body,
                                        headers={"set-cookie": "sid=secretCOOKIE", "authorization": "Bearer secretAUTH"})
    page = result["pages"][0]
    assert page["status"] == "captured_document_redacted"
    assert page["body_redacted"]
    assert page["original_body_sha256"] == sha256(body).hexdigest()
    saved = (directory / page["saved_file"]).read_bytes()
    assert page["saved_sha256"] == sha256(saved).hexdigest()
    assert page["saved_sha256"] != page["original_body_sha256"]
    all_saved_text = "\n".join(item.read_text(encoding="utf-8") for item in directory.iterdir())
    assert "secretTOKEN" not in all_saved_text
    assert "secretCOOKIE" not in all_saved_text
    assert "secretAUTH" not in all_saved_text
