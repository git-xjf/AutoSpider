"""Offline audit correctness and scope checks, using synthetic documents only."""
from hashlib import sha256
import json

import pytest

from scripts import audit_cls_source_documents as auditor


def fixture_capture(tmp_path, monkeypatch, *, content="Unique target source phrase.",
                    article_id=1, query_id="1", duplicate_script=False):
    monkeypatch.setattr(auditor, "ROOT", tmp_path)
    folder = tmp_path / "capture"
    folder.mkdir()
    payload = {"page": "/detail/[id]", "props": {"pageProps": {
        "query": {"id": query_id}, "articleDetail": {
            "id": article_id, "title": "Public test heading", "content": content,
        },
    }}}
    script = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
    markup = ('<html><head><meta charset="utf-8"></head><body>'
              '<h1 class="article-title">Public test heading</h1>'
              '<div class="detail-content"><div>' + content + "</div></div>"
              + script + (script if duplicate_script else "") + "</body></html>")
    data = markup.encode("utf-8")
    (folder / "source.html").write_bytes(data)
    report = {"pages": [{"url": "https://www.cls.cn/detail/1", "role": "detail",
                         "saved_file": "source.html", "saved_encoding": "utf-8",
                         "saved_sha256": sha256(data).hexdigest(),
                         "original_body_sha256": sha256(data).hexdigest(),
                         "body_redacted": False}], "javascript_executed": False,
              "rendered_html_captured": False, "replay_ready": False}
    (folder / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return folder


def test_same_response_agreement_is_not_independent_truth(tmp_path, monkeypatch):
    folder = fixture_capture(tmp_path, monkeypatch)
    result, _ = auditor.audit_document(folder)
    assert result["article_detail"]["identity"]["url_query_entity_agree_as_strings"]
    assert result["representation_provenance"]["independent_semantic_reference_count"] == 0
    assert result["representation_provenance"]["json_and_ssr_same_saved_document"]
    assert not result["unchanged_v3_compatibility"]["compatible_without_resource_or_contract_rewrite"]
    assert "Unique target source phrase." not in json.dumps(result)


def test_html_to_text_comparison_is_separate_from_literal_contract(tmp_path, monkeypatch):
    folder = fixture_capture(tmp_path, monkeypatch, content="<p>First paragraph.</p><p>Second paragraph.</p>")
    result, _ = auditor.audit_document(folder)
    comparison = next(row for row in result["normalized_direct_body_candidate_comparisons"] if row["selector"] == ".detail-content")
    assert comparison["match_count"] == 1
    match = comparison["matches"][0]
    assert match["equals_normalized_json_content_html_text"]
    assert not match["equals_normalized_json_content_literal"]
    assert result["article_detail"]["content_fragment"]["contains_parsed_element_tags"]


def test_duplicate_embedded_script_is_not_arbitrarily_selected(tmp_path, monkeypatch):
    folder = fixture_capture(tmp_path, monkeypatch, duplicate_script=True)
    result, _ = auditor.audit_document(folder)
    assert result["next_data"]["match_count"] == 2
    assert not result["next_data"]["json_parse_success"]
    assert result["next_data"]["json_parse_error_class"] == "non_unique_or_missing_next_data"


def test_task_entity_mismatch_is_retained(tmp_path, monkeypatch):
    folder = fixture_capture(tmp_path, monkeypatch, article_id=2)
    result, _ = auditor.audit_document(folder)
    assert not result["article_detail"]["identity"]["url_query_entity_agree_as_strings"]
    assert not result["article_detail"]["identity"]["independent_identity_evidence"]


def test_changed_saved_bytes_cannot_pass_manifest_hash(tmp_path, monkeypatch):
    folder = fixture_capture(tmp_path, monkeypatch)
    (folder / "source.html").write_bytes(b"Changed input")
    with pytest.raises(ValueError, match="hash mismatch"):
        auditor.audit_document(folder)


def test_report_cannot_redirect_audit_outside_capture_directory(tmp_path, monkeypatch):
    folder = fixture_capture(tmp_path, monkeypatch)
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    report["pages"][0]["saved_file"] = "../outside.html"
    (folder / "report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        auditor.audit_document(folder)
