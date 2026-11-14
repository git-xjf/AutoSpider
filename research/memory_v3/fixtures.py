"""Controlled offline browser capsules for field-source differential replay.

These are authored engineering tests, not captured news sites or a semantic
truth benchmark. Scoring labels are separate from every Bundle/Binding input.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json


SCHEMA_VERSION = "memory-source-binding-v3-fixtures/1"
ORIGIN = "https://capsule.invalid"
ENTRY_URL = ORIGIN + "/article.html"
API_URL = ORIGIN + "/api.json"
SCRIPT_URL = ORIGIN + "/renderer.js"
TRUE_BODY = "The target report describes a reproducible experiment, its detailed observations, and supporting evidence."
FOREIGN_BODY = "Recommended material belongs to a different entity and must not become the requested report."


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def payload_hash(value):
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def _renderer(kind):
    code = {
        "normal": "body.textContent = target.body; recommended.textContent = foreign.body;",
        "wrong_record": "body.textContent = foreign.body; recommended.textContent = foreign.body;",
        "equal_text": "body.textContent = target.body; recommended.textContent = foreign.body;",
        "mixed": "mixed.textContent = (target.body + ' ' + foreign.body).trim();",
        "duplicate_dom": "for (const node of document.querySelectorAll('.body-slot')) node.textContent = target.body;",
        "canary_branch": "body.textContent = /\\s/.test(target.body) ? target.body : foreign.body;",
        "truncate": "body.textContent = target.body.slice(0, 32);",
        "wrong_field": "body.textContent = target.title;",
        "side_effect": "body.textContent = target.body; recommended.textContent = foreign.body; document.querySelector('#status').textContent = JSON.stringify(data).length;",
        "static_spoof": "body.textContent = 'The target report describes a reproducible experiment, its detailed observations, and supporting evidence.'; recommended.textContent = foreign.body;",
    }[kind]
    return """'use strict';
(async () => {
  const response = await fetch('/api.json');
  if (!response.ok) throw new Error('source response is not ready');
  const data = await response.json();
  const target = data.records.find(record => record.id === 'target');
  const foreign = data.records.find(record => record.id === 'foreign');
  const body = document.querySelector('#body');
  const recommended = document.querySelector('#recommended');
  const mixed = document.querySelector('#mixed');
  """ + code + """
  document.documentElement.setAttribute('data-ready', 'yes');
})().catch(error => { document.documentElement.setAttribute('data-error', error.message); });
"""


def _bundle(kind="normal", *, same_text=False, empty_foreign=False, missing_entity=False,
            duplicate_entity=False, source_status=200, missing_script=False,
            semantic_wrong=False, version="1"):
    target_body = "Upstream supplied incorrect body text even though this renderer faithfully reads the target body field." if semantic_wrong else TRUE_BODY
    records = [{"id": "target", "body": target_body, "title": TRUE_BODY}, {"id": "foreign", "body": FOREIGN_BODY, "title": "Recommended heading"}]
    if same_text:
        records[1]["body"] = target_body
    if empty_foreign:
        records[1]["body"] = ""
    if missing_entity:
        records[0]["id"] = "another-entity"
    if duplicate_entity:
        records.append({"id": "target", "body": "Second target entity; binding identity is ambiguous.", "title": "Another title"})
    slots = '<div class="body-slot"></div><div class="body-slot"></div>' if kind == "duplicate_dom" else ""
    markup = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Controlled report</title></head>'
        '<body><aside id="recommended"></aside><main><div id="body"></div><div id="mixed"></div>'
        + slots + '</main><footer id="status"></footer><script src="/renderer.js"></script></body></html>'
    )
    candidates = ["#recommended", "#body"]
    if kind == "mixed":
        candidates = ["#mixed"]
    if kind == "duplicate_dom":
        candidates = [".body-slot"]
    resources = [
        {"url": ENTRY_URL, "body": markup, "content_type": "text/html; charset=utf-8", "status": 200, "method": "GET", "request_body": None, "version": version},
        {"url": SCRIPT_URL, "body": _renderer(kind), "content_type": "application/javascript; charset=utf-8", "status": 200, "method": "GET", "request_body": None, "version": version},
        {"url": API_URL, "body": canonical({"records": records}), "content_type": "application/json; charset=utf-8", "status": source_status, "method": "GET", "request_body": None, "version": version},
    ]
    if missing_script:
        resources = [resource for resource in resources if resource["url"] != SCRIPT_URL]
    return {"entry_url": ENTRY_URL, "resources": resources, "ready_selector": 'html[data-ready="yes"]', "candidates": candidates, "version": version}


def _round(round_id, bundle, expected_selectors, *, semantic_reference=TRUE_BODY, interpretation="exclusive source field identity in the observed task"):
    return {
        "round_id": round_id,
        "bundle": deepcopy(bundle),
        "binding": {"resource_url": API_URL, "records_pointer": "/records", "entity_field": "id", "entity_value": "target", "value_field": "body", "version": "1"},
        "scoring": {"expected_binding_selectors": list(expected_selectors), "semantic_reference": semantic_reference, "interpretation": interpretation},
    }


def _case(name, description, bundle, expected, *, rounds=None, interpretation="exclusive source field identity in the observed task"):
    first = _round("initial", bundle, expected, interpretation=interpretation)
    return {"name": name, "description": description, "rounds": rounds if rounds is not None else [first, {**deepcopy(first), "round_id": "repeat_exact_capsule"}]}


def build_suite():
    cases = [
        _case("normal_no_schema", "A script renders a target JSON record without schema.org markup.", _bundle(), ["#body"]),
        _case("recommendation_wrong_record", "The candidate body reads a different entity; nonempty text does not establish target binding.", _bundle("wrong_record"), []),
        _case("equal_text_different_entity", "Target and foreign bodies initially have identical text; text equality selects the wrong recommendation first.", _bundle("equal_text", same_text=True), ["#body"]),
        _case("target_foreign_mixed_empty_control", "The foreign field starts empty; target-only intervention appears correct but a foreign intervention contaminates the candidate.", _bundle("mixed", empty_foreign=True), []),
        _case("duplicate_dom_body_role", "Two nodes match the body selector; no unique candidate can be certified.", _bundle("duplicate_dom"), []),
        _case("duplicate_source_entity", "Two records have the target ID, so the declared entity binding is ambiguous.", _bundle(duplicate_entity=True), []),
        _case("missing_source_entity", "The declared target ID is absent from the source response.", _bundle(missing_entity=True), []),
        _case("canary_sensitive_branch", "A whitespace-dependent application branch reacts differently to opaque canary values; abstention is a conservative limitation, not absence of actual dependence.", _bundle("canary_branch"), ["#body"], interpretation="baseline reads the target, but intervention changes branch; no broad certificate is assumed"),
        _case("truncated_field_transform", "The body reads the target field but truncates it; whole-field identity is not the same as arbitrary transformed dependence.", _bundle("truncate"), ["#body"], interpretation="target dependence exists but full-field copy contract is violated"),
        _case("source_503", "The captured source response is unavailable; no selector-invalidity inference should be stored.", _bundle(source_status=503), []),
        _case("missing_replay_script", "The capsule is incomplete because the external renderer resource is missing; unresolved requests must be reported.", _bundle(missing_script=True), []),
        _case("upstream_semantic_wrong_binding_right", "The source contains wrong article text while the page faithfully renders the declared target body field. Binding is not semantic truth.", _bundle(semantic_wrong=True), ["#body"]),
        _case("unrelated_dom_side_effect", "A source-length status badge changes, while the correctly selected target body remains independent of foreign text.", _bundle("side_effect"), ["#body"]),
        _case("same_entity_wrong_field", "Target title initially equals target body, but the candidate reads title rather than the declared body field.", _bundle("wrong_field"), []),
        _case("static_equal_text_spoof", "A constant string equals the target body but does not depend on that source field.", _bundle("static_spoof"), []),
    ]
    original = _bundle()
    changed = _bundle("wrong_record", version="2")
    branch_rounds = [
        _round("initial", original, ["#body"]),
        _round("repeat_exact_capsule", original, ["#body"]),
        _round("changed_renderer_snapshot_must_miss", changed, []),
        _round("repeat_changed_capsule", changed, []),
    ]
    cases.append(_case("renderer_branch_snapshot_changes", "A changed script/version must miss an old certificate even when the source JSON and entry URL are unchanged.", original, ["#body"], rounds=branch_rounds))
    return {
        "schema_version": SCHEMA_VERSION,
        "capsule_origin": "authored deterministic local replay inputs; no captured production website",
        "allowed_network": "only exact resources in each frozen Bundle; all other requests are aborted",
        "cases": cases,
    }


def freeze_payload():
    payload = build_suite()
    return {"payload_sha256": payload_hash(payload), "payload": payload}
