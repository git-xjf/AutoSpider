"""Offline structure audit of saved public CLS document responses.

This does not execute a browser, fetch dependencies, reconstruct redacted bytes,
or supply semantic ground truth. Article text is summarized by length and hash;
it is deliberately not copied into the audit output.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import html


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "runtime/experiments/patent-memory-live-20261021/authorized-public-batch-r1"
DEFAULT_OUTPUT = ROOT / "runtime/experiments/patent-memory-live-20261021/cls-source-audit.json"
DOCUMENT_NAMES = ("list", "detail-2501657", "detail-2470844")
ARTICLE_POINTER = "/props/pageProps/articleDetail"
LIMITATIONS = [
    "Two detail inputs are redacted public response snapshots, not original-byte reproductions.",
    "Original response hashes cannot reconstruct absent original bytes or establish which fields changed.",
    "Inline JSON and SSR DOM belong to the same response; agreement is not independent semantic ground truth.",
    "No browser, JavaScript, API response, CSS dependency, or runtime network trace is audited or replayed.",
    "Observed script URLs are document references, not verified accessible or captured dependencies.",
    "HTML fragment-to-text normalization is an audit operation, not an installed v3 source contract.",
    "No depth-list membership, causal field binding, semantic correctness, or patent novelty is inferred.",
]


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def normalized(value: str) -> str:
    return " ".join(value.split())


def text_summary(value: str) -> dict[str, Any]:
    norm = normalized(value)
    encoded = value.encode("utf-8")
    return {
        "characters": len(value),
        "utf8_bytes": len(encoded),
        "sha256_utf8": digest(encoded),
        "normalized_characters": len(norm),
        "normalized_sha256_utf8": digest(norm.encode("utf-8")),
    }


def pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def property_summaries(value: Any, pointer: str) -> list[dict[str, Any]]:
    """Describe JSON shape without copying arbitrary scalar values or article text."""
    row: dict[str, Any] = {"json_pointer": pointer, "type": type(value).__name__}
    if isinstance(value, str):
        row.update(text_summary(value))
    elif isinstance(value, (dict, list)):
        row["length"] = len(value)
    rows = [row]
    if isinstance(value, dict):
        for key, child in value.items():
            rows.extend(property_summaries(child, pointer + "/" + pointer_token(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(property_summaries(child, pointer + "/" + str(index)))
    return rows


def fragment_summary(value: str) -> tuple[dict[str, Any], str]:
    fragment = html.fragment_fromstring(value, create_parent="div")
    tags = Counter(node.tag for node in fragment.iterdescendants() if isinstance(node.tag, str))
    plain = fragment.text_content()
    return {
        "normalizer": "lxml.html.fragment_fromstring(create_parent='div').text_content + whitespace collapse",
        "contains_parsed_element_tags": bool(tags),
        "element_tag_counts": dict(sorted(tags.items())),
        "text": text_summary(plain),
    }, normalized(plain)


def _visible_element(node: Any) -> bool:
    return isinstance(node.tag, str) and not any(
        ancestor.tag in {"script", "style", "noscript", "head"}
        for ancestor in (node, *node.iterancestors())
    )


def compare_candidate(doc: Any, selector: str, nodes: list[Any], raw_content: str,
                      body_text: str, title: str) -> dict[str, Any]:
    matches = []
    for node in nodes:
        value = normalized(node.text_content())
        matches.append({
            "dom_path": doc.getroottree().getpath(node),
            "tag": node.tag,
            "text": text_summary(node.text_content()),
            "equals_normalized_json_content_literal": value == normalized(raw_content),
            "equals_normalized_json_content_html_text": value == body_text,
            "equals_normalized_json_title": value == normalized(title),
        })
    return {"selector": selector, "match_count": len(nodes), "matches": matches}


def source_reference_summary(src: str, entry_url: str) -> dict[str, Any]:
    """Keep public location structure, not query values that might contain identifiers."""
    value = urljoin(entry_url, src)
    parsed = urlsplit(value)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "path": parsed.path,
        "has_query": bool(parsed.query),
        "url_sha256_utf8": digest(value.encode("utf-8")),
        "captured_response": False,
    }


def audit_document(capture_dir: Path) -> tuple[dict[str, Any], dict[Path, str]]:
    report_path = capture_dir / "report.json"
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes.decode("utf-8-sig"))
    pages = report.get("pages", [])
    if len(pages) != 1 or not pages[0].get("saved_file"):
        raise ValueError(f"Expected exactly one saved document in {report_path}")
    page = pages[0]
    document_path = (capture_dir / page["saved_file"]).resolve()
    if not document_path.is_relative_to(capture_dir.resolve()):
        raise ValueError("Saved document path escapes its capture directory")
    data = document_path.read_bytes()
    actual_hash = digest(data)
    if actual_hash != page["saved_sha256"]:
        raise ValueError(f"Saved-document hash mismatch: {document_path}")
    text = data.decode(page.get("saved_encoding", page.get("encoding", "utf-8")), errors="strict")
    doc = html.fromstring(text)
    scripts = doc.xpath('//script[@id="__NEXT_DATA__"]')
    next_data = None
    next_error = None
    if len(scripts) == 1:
        try:
            next_data = json.loads(scripts[0].text or "")
        except (TypeError, ValueError) as exc:
            next_error = type(exc).__name__
    else:
        next_error = "non_unique_or_missing_next_data"
    props = next_data.get("props", {}).get("pageProps", {}) if isinstance(next_data, dict) else {}
    article = props.get("articleDetail") if isinstance(props, dict) else None
    public_url = page["url"]
    source_name = capture_dir.name
    source_report = {
        "capture_directory": str(capture_dir.relative_to(ROOT)).replace("\\", "/"),
        "report_sha256": digest(report_bytes),
        "collector_source_sha256": report.get("collector_source_sha256"),
        "requested_at_utc": page.get("requested_at_utc"),
        "http_status": page.get("http_status"),
        "content_type": page.get("content_type"),
        "capture_status": page.get("status"),
        "document_recognition": page.get("document_recognition"),
        "encoding_check": page.get("encoding_check"),
        "saved_file": page["saved_file"],
        "saved_encoding": page.get("saved_encoding"),
        "original_body_bytes": page.get("original_body_bytes"),
        "original_body_sha256": page.get("original_body_sha256"),
        "saved_bytes": len(data),
        "saved_sha256": actual_hash,
        "saved_hash_matches_report": True,
        "body_redacted": page.get("body_redacted"),
        "saved_bytes_equal_original_hash": actual_hash == page.get("original_body_sha256"),
        "literal_redacted_marker_count": text.count("[REDACTED]"),
        "original_body_not_available_for_field_diff": bool(page.get("body_redacted")),
        "rendered_html_captured": report.get("rendered_html_captured"),
        "javascript_executed": report.get("javascript_executed"),
        "replay_ready": report.get("replay_ready"),
    }
    entry: dict[str, Any] = {
        "name": source_name,
        "public_input": {"url": public_url, "role": page.get("role")},
        "source_report": source_report,
        "representation_provenance": {
            "unique_saved_http_document_count": 1,
            "json_and_ssr_same_saved_document": True,
            "independent_semantic_reference_count": 0,
            "browser_source_binding_evidence_available": False,
        },
        "next_data": {
            "selector": "script#__NEXT_DATA__",
            "match_count": len(scripts),
            "script_type": scripts[0].get("type") if len(scripts) == 1 else None,
            "json_parse_success": next_error is None,
            "json_parse_error_class": next_error,
            "script_text": text_summary(scripts[0].text or "") if len(scripts) == 1 else None,
            "root_keys": list(next_data) if isinstance(next_data, dict) else None,
            "page": next_data.get("page") if isinstance(next_data, dict) else None,
            "page_props_keys": list(props) if isinstance(props, dict) else None,
        },
        "external_script_references": [source_reference_summary(src, public_url)
                                       for src in doc.xpath('//script[@src]/@src')],
        "detail_hyperlinks_in_saved_dom": [urljoin(public_url, value) for value in doc.xpath('//a/@href')
                                           if re.fullmatch(r"/detail/\d+", urlsplit(urljoin(public_url, value)).path)],
        "list_membership_of_supplied_details_established": False,
    }
    try:
        json.loads(data)
        whole_document_json = True
    except (ValueError, UnicodeDecodeError):
        whole_document_json = False
    entry["unchanged_v3_compatibility"] = {
        "whole_resource_body_json_parse_success": whole_document_json,
        "article_detail_is_nonempty_record_array": isinstance(article, list) and bool(article),
        "complete_browser_dependency_capsule_available": False,
        "compatible_without_resource_or_contract_rewrite": False,
        "reasons": [
            "v3 parses the whole bound HTTP response as JSON; this saved response is HTML.",
            "v3 requires a nonempty records array; articleDetail is a singleton object or absent.",
            "Only the entry document is present; required browser resources were not captured.",
        ],
    }
    if isinstance(article, dict):
        query = props.get("query", {})
        public_id = article.get("id")
        url_id = urlsplit(public_url).path.rstrip("/").split("/")[-1]
        query_id = query.get("id") if isinstance(query, dict) else None
        entry["article_detail"] = {
            "json_pointer": ARTICLE_POINTER,
            "type": "dict",
            "top_level_keys": list(article),
            "property_summaries": property_summaries(article, ARTICLE_POINTER),
            "identity": {
                "entity_json_pointer": ARTICLE_POINTER + "/id",
                "entity_id": public_id,
                "query_json_pointer": "/props/pageProps/query/id",
                "query_id": query_id,
                "url_detail_id": url_id,
                "url_query_entity_agree_as_strings": str(public_id) == str(query_id) == url_id,
                "independent_identity_evidence": False,
            },
            "title_json_pointer": ARTICLE_POINTER + "/title",
            "content_json_pointer": ARTICLE_POINTER + "/content",
            "public_is_free_field": article.get("isFree"),
        }
        raw_content, title = article.get("content"), article.get("title")
        if isinstance(raw_content, str) and isinstance(title, str):
            fragment, body_text = fragment_summary(raw_content)
            entry["article_detail"]["content_fragment"] = fragment
            candidate_nodes = {
                ".article-title": doc.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " article-title ")]'),
                ".detail-content": doc.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " detail-content ")]'),
                ".detail-content > div": doc.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " detail-content ")]/div'),
            }
            entry["normalized_direct_body_candidate_comparisons"] = [
                compare_candidate(doc, selector, nodes, raw_content, body_text, title)
                for selector, nodes in candidate_nodes.items()
            ]
            body_matches = []
            title_matches = []
            for node in doc.iter():
                if not _visible_element(node) or node.tag in {"html", "body"}:
                    continue
                value = normalized(node.text_content())
                if body_text and value == body_text:
                    body_matches.append(doc.getroottree().getpath(node))
                if title and value == normalized(title):
                    title_matches.append(doc.getroottree().getpath(node))
            entry["exact_text_dom_discovery"] = {
                "scope": "Saved static DOM outside script/style/noscript/head; not browser visibility.",
                "body_html_text_exact_paths": body_matches,
                "title_text_exact_paths": title_matches,
                "same_response_only": True,
            }
            entry["unchanged_v3_compatibility"]["literal_content_contains_html_elements"] = fragment["contains_parsed_element_tags"]
            if fragment["contains_parsed_element_tags"]:
                entry["unchanged_v3_compatibility"]["reasons"].append(
                    "v3 plain-text equality does not implement the HTML-content-to-DOM-text transform."
                )
    else:
        entry["article_detail"] = {"json_pointer": ARTICLE_POINTER, "type": type(article).__name__, "present": "articleDetail" in props}
    return entry, {report_path: digest(report_bytes), document_path: actual_hash}


def audit(input_root: Path) -> dict[str, Any]:
    source_path = Path(__file__).resolve()
    guarded = {source_path: digest(source_path.read_bytes())}
    documents = []
    for name in DOCUMENT_NAMES:
        document, hashes = audit_document(input_root / name)
        documents.append(document)
        guarded.update(hashes)
    replay_path = ROOT / "research/memory_v3/replay.py"
    replay_hash = digest(replay_path.read_bytes())
    guarded[replay_path] = replay_hash
    result = {
        "schema_version": "cls-saved-document-structure-audit/1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Pure offline JSON/lxml structure and normalized-text comparison; no browser execution or network.",
        "auditor_source_sha256": guarded[source_path],
        "current_v3_replay_source_sha256": replay_hash,
        "documents": documents,
        "summary": {
            "saved_documents": len(documents),
            "redacted_documents": sum(bool(item["source_report"]["body_redacted"]) for item in documents),
            "parseable_unique_next_data_scripts": sum(bool(item["next_data"]["json_parse_success"]) for item in documents),
            "singleton_article_details": sum(item["article_detail"]["type"] == "dict" for item in documents),
            "independent_semantic_reference_count": 0,
            "complete_browser_capsules": 0,
            "unchanged_v3_compatible_documents": 0,
        },
        "limitations": LIMITATIONS,
    }
    if any(digest(path.read_bytes()) != expected for path, expected in guarded.items()):
        raise RuntimeError("Source or captured input changed during audit; output not saved")
    result["input_and_source_hash_guard"] = "all_guarded_bytes_unchanged"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = audit(args.input_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "summary": result["summary"],
                      "source_sha256": result["auditor_source_sha256"],
                      "hash_guard": result["input_and_source_hash_guard"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
