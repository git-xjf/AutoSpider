"""Offline audit of the bounded resource attempt and old snapshot byte changes.

Fresh responses may be correlated with earlier original hashes; earlier files
are never overwritten. No browser, network, semantic scoring or invention claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from lxml import html

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import capture_public_memory_pages as common

BASE = ROOT / "runtime/experiments/patent-memory-live-20261021"


def digest(data):
    return sha256(data).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def checked_file(directory, filename, expected_hash):
    directory = Path(directory).resolve()
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("saved path escapes evidence directory")
    data = path.read_bytes()
    if digest(data) != expected_hash:
        raise ValueError("saved body hash mismatch")
    return path, data


def next_data(data):
    decoded = common.decode_document(data, "")
    if not decoded["verified"]:
        raise ValueError("strict document decoding failed")
    tree = html.fromstring(decoded["text"])
    scripts = tree.xpath('//script[@id="__NEXT_DATA__"]')
    if len(scripts) != 1:
        raise ValueError("unique __NEXT_DATA__ required")
    return json.loads(scripts[0].text or "")


def summary(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    result = {"type": type(value).__name__, "canonical_sha256": digest(canonical)}
    if isinstance(value, (str, list, dict)):
        result["length"] = len(value)
    return result


def differences(before, after, pointer=""):
    if type(before) is type(after) and before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(set(before) | set(after)):
            path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in before and key in after:
                changes.extend(differences(before[key], after[key], path))
            else:
                changes.append({"pointer": path, "before_present": key in before, "after_present": key in after})
        return changes
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [change for index, pair in enumerate(zip(before, after))
                for change in differences(*pair, pointer + "/" + str(index))]
    return [{"pointer": pointer, "before": summary(before), "after": summary(after)}]


def normalized_content(value):
    return " ".join(html.fragment_fromstring(value, create_parent="div").text_content().split())


def audit(capture_dir=BASE / "authorized-browser-r2", previous_dir=BASE / "authorized-public-batch-r1"):
    capture_dir, previous_dir = Path(capture_dir), Path(previous_dir)
    current_report_path = capture_dir / "report.json"
    current = load_json(current_report_path)
    guarded = {Path(__file__).resolve(): digest(Path(__file__).read_bytes()),
               current_report_path: digest(current_report_path.read_bytes()),
               Path(common.__file__): digest(Path(common.__file__).read_bytes())}
    records = []
    for resource in current["resources"]:
        if "saved_file" not in resource or resource.get("resource_type") != "document":
            continue
        raw_path, raw = checked_file(capture_dir, resource["saved_file"], resource["saved_sha256"])
        if resource.get("body_redacted") is not False or digest(raw) != resource["original_body_sha256"]:
            raise ValueError("this audit requires a newly preserved raw response")
        guarded[raw_path] = digest(raw)
        article_id = resource["url"].rsplit("/", 1)[-1]
        if not article_id.isdigit():
            raise ValueError("unexpected detail entry")
        old_dir = previous_dir / ("detail-" + article_id)
        old_report_path = old_dir / "report.json"
        old_report = load_json(old_report_path)
        guarded[old_report_path] = digest(old_report_path.read_bytes())
        old_pages = [page for page in old_report["pages"] if page.get("url") == resource["url"] and page.get("saved_file")]
        if len(old_pages) != 1:
            raise ValueError("unique previous entry required")
        old = old_pages[0]
        old_path, saved = checked_file(old_dir, old["saved_file"], old["saved_sha256"])
        guarded[old_path] = digest(saved)
        same_original = digest(raw) == old["original_body_sha256"]
        row = {"entry_url": resource["url"], "new_raw_sha256": digest(raw),
               "previous_saved_sha256": digest(saved), "previous_body_redacted": old.get("body_redacted"),
               "new_raw_hash_matches_previous_original_hash": same_original,
               "original_files_overwritten": False, "independent_semantic_reference_count": 0}
        if same_original:
            raw_json, saved_json = next_data(raw), next_data(saved)
            raw_detail = raw_json["props"]["pageProps"]["articleDetail"]
            saved_detail = saved_json["props"]["pageProps"]["articleDetail"]
            row.update(current_redact_text_reproduces_previous_saved_bytes=(common.redact_text(raw.decode("utf-8")).encode("utf-8") == saved),
                       next_data_changes_from_raw_to_old_saved=differences(raw_json, saved_json),
                       article_content_literal_unchanged=raw_detail["content"] == saved_detail["content"],
                       article_content_normalized_html_text_unchanged=(normalized_content(raw_detail["content"]) == normalized_content(saved_detail["content"])),
                       article_title_unchanged=raw_detail["title"] == saved_detail["title"],
                       article_id_unchanged=raw_detail["id"] == saved_detail["id"])
        records.append(row)
    if any(digest(path.read_bytes()) != expected for path, expected in guarded.items()):
        raise RuntimeError("input/source bytes changed during offline audit")
    return {"audited_at_utc": datetime.now(timezone.utc).isoformat(),
            "audit_source_sha256": digest(Path(__file__).read_bytes()),
            "capture_report_sha256": digest(current_report_path.read_bytes()),
            "input_and_source_hash_guard": "all_guarded_bytes_unchanged",
            "capture_status": current["status"], "halt_reason": current.get("halt_reason"),
            "network_requests": current["network_requests"], "network_body_bytes": current["network_body_bytes"],
            "browser_started": current["browser_started"], "replay_ready": current["replay_ready"],
            "records": records,
            "limitations": ["New responses match recorded old original hashes; old saved evidence is not overwritten or relabeled.",
                            "Differences summarize values by type/length/hash, not article text or secret values.",
                            "An unchanged field is not independent semantic truth or runtime source binding.",
                            "No attempt to bypass the CDN robots denial, fetch scripts, or infer an unobserved hydration path."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, default=BASE / "authorized-browser-r2")
    parser.add_argument("--previous-dir", type=Path, default=BASE / "authorized-public-batch-r1")
    parser.add_argument("--output", type=Path, default=BASE / "cls-browser-input-audit.json")
    args = parser.parse_args()
    result = audit(args.capture_dir, args.previous_dir)
    with args.output.open("xb") as handle:
        handle.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"records": len(result["records"]), "hash_guard": result["input_and_source_hash_guard"],
                      "capture_status": result["capture_status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
