from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExecutionMode = Literal["auto", "http_html", "http_api", "playwright"]
ResolvedExecutionMode = Literal["http_html", "http_api", "playwright"]
SelectorType = Literal["xpath", "css", "jsonpath"]

_SENSITIVE_REQUEST_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "key",
    "password",
    "proxy_authorization",
    "secret",
    "sig",
    "signature",
    "token",
    "x_api_key",
}


def _normalized_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _sensitive_request_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {
            str(key)
            for key in value
            if _normalized_key(str(key)) in _SENSITIVE_REQUEST_KEYS
        }
        for child in value.values():
            found.update(_sensitive_request_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_sensitive_request_keys(child))
        return found
    return set()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExtractSpec(StrictModel):
    source: Literal["text", "attribute", "html", "url"] = "text"
    attribute: str | None = None

    @model_validator(mode="after")
    def attribute_is_named(self) -> "ExtractSpec":
        if self.source == "attribute" and not self.attribute:
            raise ValueError("attribute is required when extract.source is 'attribute'")
        return self


class NormalizeSpec(StrictModel):
    trim: bool = True
    regex: str | None = None
    date_format: str | None = None


class FieldSpec(StrictModel):
    name: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    description: str = Field(min_length=1)
    type: Literal["string", "integer", "number", "boolean", "date", "datetime", "url", "html"]
    required: bool
    page_scope: Literal["auto", "list", "detail", "single"] = "auto"
    extract: ExtractSpec = Field(default_factory=ExtractSpec)
    normalize: NormalizeSpec = Field(default_factory=NormalizeSpec)


class PaginationSpec(StrictModel):
    strategy: Literal["auto", "none", "click", "url", "infinite_scroll"] = "auto"
    max_pages: int | None = Field(default=None, ge=1)


class TraversalSpec(StrictModel):
    mode: Literal["auto", "single", "list", "list_detail"] = "auto"
    item_limit: int | None = Field(default=None, ge=1)
    max_pages: int | None = Field(default=None, ge=1)
    follow_detail: bool = False
    pagination: PaginationSpec = Field(default_factory=PaginationSpec)


class ExecutionSpec(StrictModel):
    mode: ExecutionMode = "auto"
    sample_size: int = Field(default=10, ge=1, le=50)
    sample_strategy: Literal["prefix", "boundary_spread"] = "boundary_spread"
    request_delay_ms: int = Field(default=1000, ge=0)
    max_repair_attempts: int = Field(default=3, ge=0, le=5)
    headless: bool = True
    max_run_seconds: int = Field(default=1800, ge=1, le=86400)
    max_output_bytes: int = Field(default=100_000_000, ge=1)


class PolicySpec(StrictModel):
    allowed_domains: list[str] = Field(default_factory=list)
    respect_robots: bool = True
    allow_cross_domain_navigation: bool = False
    block_private_networks: bool = True

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().lower().rstrip(".") for item in value if item.strip()})


class OutputSpec(StrictModel):
    format: Literal["json", "csv", "xlsx"] = "json"
    filename: str | None = None

    @field_validator("filename")
    @classmethod
    def plain_filename_only(cls, value: str | None) -> str | None:
        if value and ("/" in value or "\\" in value or value in {".", ".."}):
            raise ValueError("output.filename must be a plain file name")
        return value


class CrawlSpec(StrictModel):
    version: Literal["0.1"] = "0.1"
    request_id: str = Field(min_length=1)
    start_urls: list[str] = Field(min_length=1)
    objective: str = Field(min_length=1)
    fields: list[FieldSpec] = Field(min_length=1)
    traversal: TraversalSpec
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)
    policy: PolicySpec = Field(default_factory=PolicySpec)
    output: OutputSpec

    @field_validator("start_urls")
    @classmethod
    def valid_http_urls(cls, value: list[str]) -> list[str]:
        for url in value:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"only absolute http/https URLs are supported: {url}")
            if parsed.username or parsed.password:
                raise ValueError("URLs containing credentials are forbidden")
        return value

    @model_validator(mode="after")
    def unique_fields(self) -> "CrawlSpec":
        names = [item.name for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        return self


class Selector(StrictModel):
    type: SelectorType = "xpath"
    value: str = Field(min_length=1)


class ListModel(StrictModel):
    container_selector: Selector | None = None
    item_selector: Selector


class FieldMapping(StrictModel):
    page_scope: Literal["list", "detail", "single"]
    selector: str = Field(min_length=1)
    selector_type: SelectorType = "xpath"
    frame_selector: str | None = None
    frame_selector_type: Literal["xpath", "css"] = "css"
    fallbacks: list[str] = Field(default_factory=list, max_length=2)
    extract: Literal["text", "attribute", "html", "url"] = "text"
    attribute: str | None = None
    expected_examples: list[str] = Field(default_factory=list, max_length=5)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_attribute(self) -> "FieldMapping":
        if self.extract == "attribute" and not self.attribute:
            raise ValueError("attribute is required for attribute extraction")
        return self


class DetailModel(StrictModel):
    enabled: bool = False
    link_selector: str | None = None
    selector_type: SelectorType = "xpath"
    open_mode: Literal["same_tab", "new_tab"] = "same_tab"

    @model_validator(mode="after")
    def link_required(self) -> "DetailModel":
        if self.enabled and not self.link_selector:
            raise ValueError("detail.link_selector is required when detail is enabled")
        return self


class PagePagination(StrictModel):
    type: Literal["none", "click", "url", "infinite_scroll"] = "none"
    selector: str | None = None
    selector_type: Literal["xpath", "css"] = "xpath"
    fallbacks: list[str] = Field(default_factory=list, max_length=2)
    url_template: str | None = None
    scroll_count: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def selector_required(self) -> "PagePagination":
        if self.type == "click" and not self.selector:
            raise ValueError("pagination.selector is required for click pagination")
        if self.type == "url" and not self.url_template:
            raise ValueError("pagination.url_template is required for URL pagination")
        return self


class WaitModel(StrictModel):
    page_ready_selector: str | None = None
    selector_type: SelectorType = "xpath"
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class HttpPagination(StrictModel):
    type: Literal["none", "page", "cursor"] = "none"
    parameter_location: Literal["query", "json", "form"] = "query"
    page_param: str | None = None
    start_page: int = Field(default=1, ge=0)
    page_size_param: str | None = None
    page_size: int | None = Field(default=None, ge=1, le=10_000)
    cursor_param: str | None = None
    next_cursor_path: str | None = None
    initial_cursor: str | int | None = None

    @model_validator(mode="after")
    def required_pagination_fields(self) -> "HttpPagination":
        if self.type == "page" and not self.page_param:
            raise ValueError("page_param is required for page pagination")
        if self.type == "cursor" and (not self.cursor_param or not self.next_cursor_path):
            raise ValueError("cursor_param and next_cursor_path are required for cursor pagination")
        return self


class HttpRequestModel(StrictModel):
    method: Literal["GET", "POST"] = "GET"
    url: str | None = None
    response_format: Literal["auto", "html", "json"] = "auto"
    headers: dict[str, str] = Field(default_factory=dict, max_length=30)
    params: dict[str, Any] = Field(default_factory=dict)
    json_body: dict[str, Any] | list[Any] | None = None
    form_body: dict[str, Any] | None = None
    pagination: HttpPagination = Field(default_factory=HttpPagination)
    timeout_seconds: float = Field(default=30, ge=1, le=120)

    @field_validator("url")
    @classmethod
    def valid_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("http_request.url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("http_request.url cannot contain credentials")
        return value

    @field_validator("headers")
    @classmethod
    def no_persisted_secret_headers(cls, value: dict[str, str]) -> dict[str, str]:
        found = sorted(name for name in value if _normalized_key(name) in _SENSITIVE_REQUEST_KEYS)
        if found:
            raise ValueError(
                "secret headers cannot be persisted in an AutoSpider workflow: "
                + ", ".join(found)
            )
        return value

    @model_validator(mode="after")
    def one_request_body(self) -> "HttpRequestModel":
        if self.json_body is not None and self.form_body is not None:
            raise ValueError("http_request accepts either json_body or form_body, not both")
        if self.method == "GET" and (self.json_body is not None or self.form_body is not None):
            raise ValueError("GET http_request cannot contain a request body")
        sensitive = set()
        sensitive.update(_sensitive_request_keys(self.params))
        sensitive.update(_sensitive_request_keys(self.json_body))
        sensitive.update(_sensitive_request_keys(self.form_body))
        if sensitive:
            raise ValueError(
                "secret request parameters cannot be persisted in an AutoSpider workflow: "
                + ", ".join(sorted(sensitive))
            )
        return self


class PageModel(StrictModel):
    version: Literal["0.1"] = "0.1"
    url: str
    page_type: Literal["SINGLE", "LIST", "LIST_DETAIL", "PAGINATED_LIST", "UNKNOWN"]
    confidence: float = Field(ge=0, le=1)
    recommended_mode: ResolvedExecutionMode = "playwright"
    http_request: HttpRequestModel | None = None
    list_model: ListModel | None = Field(default=None, alias="list", serialization_alias="list")
    fields: dict[str, FieldMapping]
    detail: DetailModel = Field(default_factory=DetailModel)
    pagination: PagePagination = Field(default_factory=PagePagination)
    waits: WaitModel = Field(default_factory=WaitModel)
    fingerprint: str | None = None
    uncertainty: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def list_shape_present(self) -> "PageModel":
        if self.page_type in {"LIST", "LIST_DETAIL", "PAGINATED_LIST"} and self.list_model is None:
            raise ValueError("list selectors are required for a list page")
        if self.recommended_mode == "http_api":
            if self.http_request is None:
                raise ValueError("http_api PageModel requires http_request")
            if self.http_request.response_format == "html":
                raise ValueError("http_api PageModel cannot declare an HTML response")
            selectors = [mapping.selector_type for mapping in self.fields.values()]
            if self.list_model is not None:
                selectors.append(self.list_model.item_selector.type)
            if any(selector != "jsonpath" for selector in selectors):
                raise ValueError("http_api PageModel requires jsonpath item and field selectors")
        if self.recommended_mode == "http_html" and self.http_request is not None:
            if self.http_request.response_format == "json":
                raise ValueError("http_html PageModel cannot declare a JSON response")
        return self


class Operation(StrEnum):
    OPEN_URL = "OPEN_URL"
    CLICK = "CLICK"
    EXTRACT = "EXTRACT"
    INPUT = "INPUT"
    CUSTOM = "CUSTOM"
    MOVE = "MOVE"
    LOOP_ELEMENTS = "LOOP_ELEMENTS"
    LOOP_PAGES = "LOOP_PAGES"
    CONDITION = "CONDITION"
    WAIT = "WAIT"
    BACK = "BACK"
    SELECT = "SELECT"
    PRESS = "PRESS"
    SCROLL = "SCROLL"
    SCREENSHOT = "SCREENSHOT"
    DOWNLOAD = "DOWNLOAD"


class PlanStep(StrictModel):
    id: str | None = None
    op: Operation
    url: str | None = None
    selector: str | None = None
    relative_selector: str | None = None
    selector_type: SelectorType = "xpath"
    frame_selector: str | None = None
    frame_selector_type: Literal["xpath", "css"] = "css"
    fallbacks: list[str] = Field(default_factory=list, max_length=2)
    field: str | None = None
    source: Literal["text", "attribute", "html", "url"] | None = None
    attribute: str | None = None
    value: str | None = None
    code: str | None = None
    seconds: float | None = Field(default=None, ge=0, le=120)
    max_iterations: int | None = Field(default=None, ge=1)
    body: list["PlanStep"] = Field(default_factory=list)

    @property
    def effective_selector(self) -> str | None:
        return self.relative_selector or self.selector

    @model_validator(mode="after")
    def validate_for_operation(self) -> "PlanStep":
        if self.op == Operation.OPEN_URL and not self.url:
            raise ValueError("OPEN_URL requires url")
        if self.op in {Operation.CLICK, Operation.MOVE, Operation.INPUT, Operation.LOOP_ELEMENTS, Operation.DOWNLOAD} and not self.effective_selector:
            raise ValueError(f"{self.op} requires a selector")
        if self.op == Operation.SELECT and (not self.effective_selector or self.value is None):
            raise ValueError("SELECT requires selector and value")
        if self.op == Operation.PRESS and (not self.effective_selector or not self.value):
            raise ValueError("PRESS requires selector and key value")
        if self.op == Operation.EXTRACT and (not self.field or not self.effective_selector or not self.source):
            raise ValueError("EXTRACT requires field, selector and source")
        if self.op in {Operation.LOOP_ELEMENTS, Operation.LOOP_PAGES} and not self.body:
            raise ValueError(f"{self.op} requires a non-empty body")
        if self.op == Operation.LOOP_PAGES and self.max_iterations is None:
            raise ValueError("LOOP_PAGES requires max_iterations")
        return self


class CrawlPlan(StrictModel):
    version: Literal["0.1"] = "0.1"
    name: str = Field(min_length=1)
    start_urls: list[str] = Field(min_length=1)
    execution_mode: ResolvedExecutionMode = "playwright"
    http_request: HttpRequestModel | None = None
    steps: list[PlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def transport_is_complete(self) -> "CrawlPlan":
        if self.execution_mode == "http_api" and self.http_request is None:
            raise ValueError("http_api CrawlPlan requires http_request")
        steps = list(self.steps)
        flattened: list[PlanStep] = []
        while steps:
            step = steps.pop()
            flattened.append(step)
            steps.extend(step.body)
        selectors = [step.selector_type for step in flattened if step.effective_selector]
        if self.execution_mode == "http_api" and any(kind != "jsonpath" for kind in selectors):
            raise ValueError("http_api CrawlPlan requires jsonpath selectors")
        if self.execution_mode != "http_api" and any(kind == "jsonpath" for kind in selectors):
            raise ValueError("jsonpath selectors require http_api mode")
        if self.execution_mode in {"http_html", "http_api"}:
            browser_only = {
                Operation.CUSTOM,
                Operation.DOWNLOAD,
                Operation.INPUT,
                Operation.MOVE,
                Operation.PRESS,
                Operation.SCREENSHOT,
                Operation.SCROLL,
                Operation.SELECT,
            }
            invalid = sorted({step.op.value for step in flattened if step.op in browser_only})
            if invalid:
                raise ValueError(
                    f"{self.execution_mode} CrawlPlan contains browser-only operations: "
                    + ", ".join(invalid)
                )
        return self


class RepairPatch(StrictModel):
    operation: Literal[
        "REPLACE_SELECTOR",
        "ADD_FALLBACK_SELECTOR",
        "CHANGE_ITEM_CONTAINER",
        "CHANGE_PAGE_SCOPE",
        "CHANGE_DETAIL_NAVIGATION",
        "CHANGE_PAGINATION",
        "ADD_WAIT",
        "REQUEST_REANALYSIS",
    ]
    target: str
    old_value: Any | None = None
    new_value: Any | None = None
    page_scope: Literal["list", "detail", "single"] | None = None
    reason: str | None = None


class ValidationIssue(StrictModel):
    field: str | None = None
    type: str
    severity: Literal["low", "medium", "high"]
    evidence: dict[str, Any] = Field(default_factory=dict)
    suspected_root_cause: str | None = None


class ValidationMetrics(StrictModel):
    required_field_coverage: float = Field(ge=0, le=1)
    required_field_null_rate: float = Field(ge=0, le=1)
    duplicate_rate: float = Field(ge=0, le=1)
    semantic_score: float = Field(ge=0, le=1)
    record_count: int = Field(ge=0)


class StratumFieldCoverage(StrictModel):
    field: str
    observed_count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    null_rate: float = Field(ge=0, le=1)


class ValidationStratum(StrictModel):
    key: str
    record_count: int = Field(ge=1)
    representative_urls: list[str] = Field(default_factory=list, max_length=2)
    fields: list[StratumFieldCoverage] = Field(default_factory=list)


class CoverageCertificate(StrictModel):
    scope: Literal["observed_records"] = "observed_records"
    url_field: str | None = None
    required_null_rate_ceiling: float = Field(ge=0, le=1)
    strata: list[ValidationStratum] = Field(default_factory=list)
    required_stratum_field_pairs: int = Field(ge=0)
    failed_stratum_field_pairs: int = Field(ge=0)
    complete: bool = Field(description="Whether required fields pass within observed records; does not certify unobserved pages or omitted records.")


class EvidenceRequest(StrictModel):
    stratum: str
    fields: list[str] = Field(min_length=1)
    representative_urls: list[str] = Field(default_factory=list, max_length=2)
    page_scope: Literal["list", "detail", "single", "mixed"]
    max_samples: int = Field(ge=1, le=3)
    max_chars: int = Field(ge=400, le=4000)
    reason: str


class ValidationReport(StrictModel):
    status: Literal["PASS", "FAIL"]
    overall_score: float = Field(ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    metrics: ValidationMetrics
    issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage_certificate: CoverageCertificate | None = None
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list, max_length=8)
    repair_patch: RepairPatch | None = None
