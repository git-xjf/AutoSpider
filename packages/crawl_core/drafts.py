from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from cssselect import GenericTranslator, SelectorError
from lxml import etree

from .models import CrawlPlan, CrawlSpec, Operation, PageModel
from .planner import build_crawl_plan
from .policies import host_allowed
from .site_scope import default_allowed_domains


_DRAFT_VERSION = "autospider.draft/v1"
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_JSONPATH_PATTERN = re.compile(r"\$(?:\.[A-Za-z_][A-Za-z0-9_-]*|\['[^']+'\]|\[\"[^\"]+\"\]|\[(?:\d+|\*)\])*")
_MAX_DRAFT_BYTES = 2_000_000
_MAX_RESULT_BYTES = 100_000


class DraftValidationError(ValueError):
    """A compact parameter error that callers can correct without schema guessing."""

    def __init__(self, message: str, *, code: str, parameter: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.parameter = parameter
        self.details = details or {}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validate_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("url must be an absolute HTTP(S) URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are forbidden")
    _ = parsed.port
    return parsed.hostname.lower().rstrip(".")


def _validate_selector(value: str, selector_type: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("selectors must be non-empty strings")
    if selector_type == "xpath":
        if re.search(r"/@[A-Za-z_][\w:.-]*\s*$", value):
            raise ValueError("XPath selectors must select an element, not an attribute node")
        try:
            etree.XPath(value)
        except etree.XPathSyntaxError as exc:
            raise ValueError("invalid XPath selector") from exc
    elif selector_type == "css":
        try:
            GenericTranslator().css_to_xpath(value)
        except SelectorError as exc:
            raise ValueError("invalid CSS selector") from exc
    elif selector_type == "jsonpath":
        if _JSONPATH_PATTERN.fullmatch(value.strip()) is None:
            raise ValueError("invalid or unsupported JSONPath selector")
    else:
        raise ValueError("selector_type must be xpath, css or jsonpath")


def build_crawl_draft(
    *,
    url: str,
    field_selectors: dict[str, str],
    item_selector: str | None = None,
    selector_type: str = "xpath",
    mode: str = "http_html",
    detail_fields: list[str] | None = None,
    detail_link_selector: str | None = None,
    pagination_selector: str | None = None,
    pagination_url_template: str | None = None,
    pagination_after_extract: bool = False,
    page_limit: int = 1,
    item_limit: int | None = None,
    field_attributes: dict[str, str] | None = None,
    field_types: dict[str, str] | None = None,
    http_request: dict | None = None,
    output_filename: str | None = None,
    objective: str | None = None,
) -> tuple[CrawlSpec, PageModel, CrawlPlan]:
    """Build typed crawl artifacts from compact, observed extraction evidence.

    The layout fingerprint is not a validation certificate. The caller must run
    deterministic sample and final QA before committing the page to Site Memory.
    Page-based requests leave item_limit unset instead of guessing a page size.
    """
    _validate_url(url)
    allowed_domains = default_allowed_domains([url])
    if mode not in {"http_html", "http_api", "playwright"}:
        raise ValueError("mode must be http_html, http_api or playwright")
    if selector_type not in {"xpath", "css", "jsonpath"}:
        raise ValueError("selector_type must be xpath, css or jsonpath")
    if (mode == "http_api") != (selector_type == "jsonpath"):
        raise ValueError("http_api requires jsonpath; HTML modes require xpath or css")
    if type(page_limit) is not int or not 1 <= page_limit <= 1000:
        raise ValueError("page_limit must be an integer between 1 and 1000")
    if item_limit is not None and (type(item_limit) is not int or not 1 <= item_limit <= 1000):
        raise ValueError("item_limit must be an integer between 1 and 1000 or None")
    if type(pagination_after_extract) is not bool:
        raise ValueError("pagination_after_extract must be boolean")
    if not isinstance(field_selectors, dict) or not 1 <= len(field_selectors) <= 50:
        raise ValueError("field_selectors must contain between 1 and 50 fields")
    selectors = dict(field_selectors)
    for name, selector in selectors.items():
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ValueError("invalid field name")
        _validate_selector(selector, selector_type)
    attributes = {} if field_attributes is None else dict(field_attributes)
    types = {} if field_types is None else dict(field_types)
    if set(attributes) - set(selectors) or set(types) - set(selectors):
        raise ValueError("field_attributes and field_types must reference requested fields")
    if any(not isinstance(attribute, str) or not attribute.strip() for attribute in attributes.values()):
        raise ValueError("field attributes must be non-empty strings")
    if mode == "http_api" and attributes:
        raise ValueError("JSON API fields extract JSON values, not HTML attributes")
    if detail_fields is not None and (not isinstance(detail_fields, list) or any(not isinstance(name, str) for name in detail_fields)):
        raise DraftValidationError("detail_fields must be a list of field names, not a mapping or selectors", code="invalid_detail_fields_type", parameter="detail_fields")
    details = [] if detail_fields is None else list(detail_fields)
    if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None for name in details):
        raise DraftValidationError("detail_fields contains selectors or invalid names; put selectors in field_selectors and only names in detail_fields", code="selector_in_detail_fields", parameter="detail_fields")
    duplicates = sorted({name for name in details if details.count(name) > 1})
    if duplicates:
        raise DraftValidationError("detail_fields contains duplicate field names; supply each name once", code="duplicate_detail_fields", parameter="detail_fields", details={"duplicate_fields": [name[:80] for name in duplicates[:20]]})
    unknown = sorted(set(details) - set(selectors))
    if unknown:
        raise DraftValidationError(
            "detail_fields contains names without selectors in field_selectors; add verified detail selectors under the same names",
            code="unknown_detail_fields",
            parameter="detail_fields",
            details={"unknown_fields": [name[:80] for name in unknown[:20]], "available_fields": [name[:80] for name in sorted(selectors)[:20]]},
        )
    if item_selector is not None:
        _validate_selector(item_selector, selector_type)
    if details and (item_selector is None or not detail_link_selector):
        raise ValueError("detail fields require an item selector and detail_link_selector")
    if detail_link_selector is not None:
        _validate_selector(detail_link_selector, selector_type)
        if not details:
            raise ValueError("detail_link_selector requires detail_fields")
    if pagination_selector is not None and pagination_url_template is not None:
        raise ValueError("provide only one pagination selector or URL template")
    if pagination_selector is not None:
        if selector_type == "jsonpath":
            raise ValueError("API pagination must use http_request.pagination or a URL template")
        _validate_selector(pagination_selector, selector_type)
    if pagination_url_template is not None:
        placeholders = {"{{page_number}}": "1", "{{next_page_number}}": "2"}
        if not isinstance(pagination_url_template, str) or not any(token in pagination_url_template for token in placeholders):
            raise ValueError("pagination_url_template must contain {{page_number}} or {{next_page_number}}")
        concrete = pagination_url_template
        for token, value in placeholders.items():
            concrete = concrete.replace(token, value)
        if "{{" in concrete or "}}" in concrete or not host_allowed(_validate_url(concrete), allowed_domains):
            raise ValueError("pagination URL template must keep the allowed domain and supported placeholders")
    if pagination_after_extract and pagination_url_template is None:
        raise ValueError("pagination_after_extract requires a pagination URL template")
    if item_selector is None and (page_limit != 1 or pagination_selector is not None or pagination_url_template is not None):
        raise ValueError("pagination requires an item selector")
    if output_filename is not None and (not isinstance(output_filename, str) or not output_filename.strip()):
        raise ValueError("output_filename must be a non-empty plain filename")
    output_format = "json"
    if output_filename is not None:
        suffix = Path(output_filename).suffix.lower().lstrip(".")
        if suffix and suffix not in {"json", "csv", "xlsx"}:
            raise ValueError("output_filename extension must be json, csv or xlsx")
        output_format = suffix or "json"
    request_id = uuid4().hex
    field_specs = []
    mappings = {}
    for name, selector in selectors.items():
        scope = "detail" if name in details else "list" if item_selector is not None else "single"
        attribute = attributes.get(name)
        if attribute is None and mode != "http_api" and name.lower() in {"url", "link", "href"}:
            attribute = "href"
        extraction = "attribute" if attribute is not None else "text"
        field_specs.append(
            {
                "name": name,
                "description": name.replace("_", " "),
                "type": types.get(name, "url" if name.lower() in {"url", "link", "href"} else "string"),
                "required": True,
                "page_scope": scope,
                "extract": {"source": extraction, "attribute": attribute},
            }
        )
        mappings[name] = {
            "page_scope": scope,
            "selector": selector.strip(),
            "selector_type": selector_type,
            "extract": extraction,
            "attribute": attribute,
            "confidence": 0.5,
        }
    pagination = {"type": "none", "selector_type": selector_type if selector_type != "jsonpath" else "xpath"}
    if pagination_selector is not None:
        pagination.update(type="click", selector=pagination_selector)
    elif pagination_url_template is not None:
        pagination.update(type="url", url_template=pagination_url_template)
    spec = CrawlSpec.model_validate(
        {
            "request_id": request_id,
            "start_urls": [url],
            "objective": objective if objective is not None else f"Collect {page_limit} page(s) from {url} with fields: {', '.join(selectors)}.",
            "fields": field_specs,
            "traversal": {
                "mode": "list_detail" if details else "list" if item_selector is not None else "single",
                "item_limit": item_limit,
                "max_pages": page_limit,
                "follow_detail": bool(details),
                "pagination": {"strategy": pagination["type"], "max_pages": page_limit},
            },
            "execution": {"mode": mode},
            "policy": {"allowed_domains": allowed_domains, "respect_robots": True, "block_private_networks": True, "allow_cross_domain_navigation": False},
            "output": {"format": output_format, "filename": output_filename or f"{request_id}.{output_format}"},
        }
    )
    page = PageModel.model_validate(
        {
            "url": url,
            "page_type": "LIST_DETAIL" if details else "LIST" if item_selector is not None else "SINGLE",
            "confidence": 0.5,
            "recommended_mode": mode,
            "http_request": http_request,
            "list": {"item_selector": {"type": selector_type, "value": item_selector.strip()}} if item_selector is not None else None,
            "fields": mappings,
            "detail": {"enabled": bool(details), "link_selector": detail_link_selector, "selector_type": selector_type},
            "pagination": pagination,
            "uncertainty": ["Selectors are an unverified draft; deterministic sample and final QA are required before Site Memory commit."],
            "evidence": ["Compact draft created from supplied selector and pagination evidence."],
        }
    )
    if page.http_request and page.http_request.url and not host_allowed(_validate_url(page.http_request.url), allowed_domains):
        raise ValueError("http_request URL must keep the allowed domain")
    native_pagination = page.http_request.pagination.type if mode == "http_api" and page.http_request else "none"
    if mode == "http_api" and pagination_url_template is not None and page.http_request is not None:
        request = page.http_request
        if request.method != "GET" or request.params or request.json_body or request.form_body:
            raise ValueError("use native API pagination to preserve the request method and parameters")
    if native_pagination != "none" and (pagination_selector is not None or pagination_url_template is not None):
        raise ValueError("native API pagination cannot be combined with another pagination mechanism")
    if page_limit > 1 and pagination["type"] == "none" and native_pagination == "none":
        raise ValueError("page_limit greater than one requires observed pagination")
    if native_pagination != "none":
        spec.traversal.pagination.strategy = "auto"
    layout = {
        "mode": mode,
        "selector_type": selector_type,
        "item_selector": item_selector.strip() if item_selector is not None else None,
        "fields": mappings,
        "detail_link_selector": detail_link_selector,
        "pagination": pagination,
        "pagination_after_extract": pagination_after_extract,
        "http_pagination": page.http_request.pagination.model_dump(mode="json") if page.http_request else None,
    }
    page.fingerprint = _hash(layout)
    plan = build_crawl_plan(spec, page)
    if pagination_after_extract:
        loops = [step for step in plan.steps if step.op == Operation.LOOP_PAGES]
        if len(loops) != 1 or loops[0].body[0].op != Operation.OPEN_URL:
            raise ValueError("pagination_after_extract requires one URL-driven page loop")
        navigation = loops[0].body.pop(0)
        navigation.id = "next"
        loops[0].body.append(navigation)
    return (
        CrawlSpec.model_validate(spec.model_dump(mode="json")),
        PageModel.model_validate(page.model_dump(mode="json")),
        CrawlPlan.model_validate(plan.model_dump(mode="json")),
    )


class CrawlDraftRepository:
    """Opaque, integrity-checked draft storage below a trusted runtime root.

    Results support sequential repeat idempotency only. Concurrent execution of
    one draft must retain a single execution owner at the service boundary.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.directory = self.root / "drafts"

    def _path(self, draft_id: str, *, result: bool = False) -> Path:
        if not isinstance(draft_id, str) or _ID_PATTERN.fullmatch(draft_id) is None:
            raise ValueError("draft_id must be an opaque 32-character lowercase hex identifier")
        if self.directory.is_symlink() or self.directory.resolve().parent != self.root:
            raise ValueError("draft directory must remain within the runtime root")
        suffix = ".result.json" if result else ".json"
        path = self.directory / f"{draft_id}{suffix}"
        if path.is_symlink():
            raise ValueError("draft files must not be symlinks")
        return path

    @staticmethod
    def _validated_payload(spec: CrawlSpec, page: PageModel, plan: CrawlPlan) -> dict:
        cloned_spec = CrawlSpec.model_validate(spec.model_dump(mode="json"))
        cloned_page = PageModel.model_validate(page.model_dump(mode="json"))
        cloned_plan = CrawlPlan.model_validate(plan.model_dump(mode="json"))
        if len(cloned_spec.start_urls) != 1 or cloned_spec.start_urls != cloned_plan.start_urls or cloned_page.url != cloned_spec.start_urls[0]:
            raise ValueError("draft spec, page and plan start URLs must agree on one URL")
        if cloned_spec.execution.mode != cloned_page.recommended_mode or cloned_plan.execution_mode != cloned_page.recommended_mode:
            raise ValueError("draft execution modes must agree")
        if set(cloned_page.fields) != {field.name for field in cloned_spec.fields}:
            raise ValueError("draft page fields must exactly cover the requested fields")
        return {
            "crawl_spec": cloned_spec.model_dump(mode="json"),
            "page_model": cloned_page.model_dump(mode="json"),
            "crawl_plan": cloned_plan.model_dump(mode="json"),
        }

    @staticmethod
    def _atomic_write(path: Path, value: dict, max_bytes: int) -> None:
        content = _canonical(value).encode("utf-8")
        if len(content) > max_bytes:
            raise ValueError(f"draft payload exceeds {max_bytes} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        staged: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".draft-", suffix=".tmp", delete=False) as stream:
                staged = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, path)
        finally:
            if staged is not None and staged.exists():
                staged.unlink()

    def create(self, spec: CrawlSpec, page: PageModel, plan: CrawlPlan) -> str:
        payload = self._validated_payload(spec, page, plan)
        identifier = uuid4().hex
        path = self._path(identifier)
        self._atomic_write(
            path,
            {
                "version": _DRAFT_VERSION,
                "draft_id": identifier,
                "payload": payload,
                "sha256": _hash(payload),
            },
            _MAX_DRAFT_BYTES,
        )
        return identifier

    def get(self, draft_id: str) -> tuple[CrawlSpec, PageModel, CrawlPlan]:
        path = self._path(draft_id)
        if path.stat().st_size > _MAX_DRAFT_BYTES:
            raise ValueError("draft exceeds its storage size limit")
        stored = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict) or stored.get("version") != _DRAFT_VERSION:
            raise ValueError("unsupported draft storage version")
        payload = stored.get("payload")
        if stored.get("draft_id") != draft_id or not isinstance(payload, dict) or stored.get("sha256") != _hash(payload):
            raise ValueError("draft integrity verification failed")
        if set(payload) != {"crawl_spec", "page_model", "crawl_plan"}:
            raise ValueError("draft payload must contain exactly three typed models")
        spec = CrawlSpec.model_validate(payload["crawl_spec"])
        page = PageModel.model_validate(payload["page_model"])
        plan = CrawlPlan.model_validate(payload["crawl_plan"])
        self._validated_payload(spec, page, plan)
        return spec, page, plan

    def save_result(self, draft_id: str, result: dict) -> None:
        self.get(draft_id)
        if not isinstance(result, dict):
            raise ValueError("draft result must be a JSON object")
        self._atomic_write(self._path(draft_id, result=True), result, _MAX_RESULT_BYTES)

    def get_result(self, draft_id: str) -> dict | None:
        self.get(draft_id)
        path = self._path(draft_id, result=True)
        if not path.exists():
            return None
        if path.stat().st_size > _MAX_RESULT_BYTES:
            raise ValueError("draft result exceeds its storage size limit")
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("draft result must be a JSON object")
        return result
