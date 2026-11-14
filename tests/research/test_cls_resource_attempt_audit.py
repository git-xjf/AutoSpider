"""Synthetic-only tests of the saved resource attempt audit; never network."""
from hashlib import sha256
import json

import pytest

from scripts import audit_cls_resource_attempt as auditor


def document(*, duplicate=False):
    data = {"props": {"pageProps": {"articleDetail": {
        "id": 1, "title": "Synthetic heading", "content": "Synthetic private-to-test article text"}}}}
    script = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script>"
    return ('<!doctype html><html><head><meta charset="utf-8"></head><body>' + script +
            (script if duplicate else "") + "</body></html>").encode()


def test_nested_diff_never_copies_values():
    changes = auditor.differences({"a/b": ["secret-before"]}, {"a/b": ["secret-after"]})
    assert changes[0]["pointer"] == "/a~1b/0"
    assert "secret" not in json.dumps(changes)
    assert auditor.differences({"same": 1}, {"same": 1}) == []


def test_duplicate_inline_source_is_rejected():
    with pytest.raises(ValueError, match="unique"):
        auditor.next_data(document(duplicate=True))


def test_saved_path_escape_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        auditor.checked_file(tmp_path, "../outside.bin", "irrelevant")


def test_manifest_hash_mismatch_is_rejected(tmp_path):
    (tmp_path / "body.bin").write_bytes(b"changed input")
    with pytest.raises(ValueError, match="hash mismatch"):
        auditor.checked_file(tmp_path, "body.bin", "wrong hash")


@pytest.mark.parametrize("same_original", [True, False])
def test_fresh_original_correlation_is_required_for_field_comparison(tmp_path, same_original):
    new_dir, old_dir = tmp_path / "new", tmp_path / "old"
    new_dir.mkdir()
    old_entry = old_dir / "detail-1"
    old_entry.mkdir(parents=True)
    body = document()
    value_hash = sha256(body).hexdigest()
    url = "https://www.cls.cn/detail/1"
    new_resource = {"url": url, "resource_type": "document", "saved_file": "body.bin",
                    "saved_sha256": value_hash, "original_body_sha256": value_hash, "body_redacted": False}
    current = {"resources": [new_resource], "status": "PENDING_INCOMPLETE", "network_requests": 4,
               "network_body_bytes": len(body), "browser_started": False, "replay_ready": False}
    old_page = {"url": url, "saved_file": "body.bin", "saved_sha256": value_hash,
                "original_body_sha256": value_hash if same_original else "different-original", "body_redacted": True}
    (new_dir / "body.bin").write_bytes(body)
    (new_dir / "report.json").write_text(json.dumps(current), encoding="utf-8")
    (old_entry / "body.bin").write_bytes(body)
    (old_entry / "report.json").write_text(json.dumps({"pages": [old_page]}), encoding="utf-8")
    result = auditor.audit(new_dir, old_dir)
    row = result["records"][0]
    assert row["new_raw_hash_matches_previous_original_hash"] is same_original
    assert ("article_content_literal_unchanged" in row) is same_original
    assert row["independent_semantic_reference_count"] == 0
    assert "private-to-test" not in json.dumps(result)
    assert result["input_and_source_hash_guard"] == "all_guarded_bytes_unchanged"
