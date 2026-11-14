from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

from .models import CrawlPlan, CrawlSpec, Operation, PlanStep
from .site_scope import reuse_allowed_domains


def _http_route(url: str) -> tuple[str, str, str]:
    if not isinstance(url, str):
        raise ValueError("start_url must be an absolute HTTP(S) URL")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("start_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("start_url cannot contain credentials")
    return parsed.scheme, parsed.netloc.lower(), parsed.path


def _steps(steps: list[PlanStep]):
    for step in steps:
        yield step
        yield from _steps(step.body)


def build_reused_crawl(
    source_spec: dict,
    source_plan: dict,
    *,
    start_url: str,
    item_limit: int,
    output_filename: str | None = None,
    field_names: list[str] | None = None,
    max_pages: int | None = None,
) -> tuple[CrawlSpec, CrawlPlan]:
    """Adapt a validated workflow for the same route without mutating its source.

    Query changes are safe only for workflows whose transport derives its request
    from the start URL. API endpoints and fixed HTTP request parameters require
    fresh analysis so a reused workflow cannot silently retain an old channel ID.
    """
    spec = CrawlSpec.model_validate(source_spec).model_copy(deep=True)
    plan = CrawlPlan.model_validate(source_plan).model_copy(deep=True)
    if len(spec.start_urls) != 1 or len(plan.start_urls) != 1:
        raise ValueError("workflow reuse requires exactly one start URL")
    old_url = spec.start_urls[0]
    if plan.start_urls != spec.start_urls:
        raise ValueError("source spec and plan start URLs must match")
    if _http_route(start_url) != _http_route(old_url):
        raise ValueError(
            "start_url must keep the source scheme, authority and path; reanalysis is required"
        )
    if type(item_limit) is not int or not 1 <= item_limit <= 1000:
        raise ValueError("item_limit must be an integer between 1 and 1000")
    if max_pages is not None and (type(max_pages) is not int or max_pages < 1):
        raise ValueError("max_pages must be a positive integer")

    if start_url != old_url and (
        spec.execution.mode == "http_api" or plan.execution_mode == "http_api"
    ):
        raise ValueError(
            "changing the start URL of an API workflow requires reanalysis"
        )
    query_changed = urlparse(start_url).query != urlparse(old_url).query
    request = plan.http_request
    if (
        query_changed
        and request is not None
        and (request.url is not None or request.params)
    ):
        raise ValueError(
            "changing query parameters with a fixed HTTP request requires reanalysis"
        )
    if query_changed:
        for step in _steps(plan.steps):
            if step.op != Operation.OPEN_URL or step.url in {old_url, "{{start_url}}"}:
                continue
            parsed = urlparse(step.url)
            if (parsed.scheme, parsed.netloc.lower(), parsed.path) == _http_route(
                old_url
            ) and parsed.query:
                raise ValueError(
                    "changing query parameters with a fixed navigation URL requires reanalysis"
                )

    source_fields = {field.name: field for field in spec.fields}
    selected = list(source_fields) if field_names is None else list(field_names)
    if not selected or any(not isinstance(name, str) for name in selected):
        raise ValueError("field_names must contain at least one field name")
    if len(selected) != len(set(selected)):
        raise ValueError("field_names must not contain duplicates")
    extracted = {
        step.field for step in _steps(plan.steps) if step.op == Operation.EXTRACT
    }
    unsupported = [
        name for name in selected if name not in source_fields or name not in extracted
    ]
    if unsupported:
        raise ValueError(
            f"source workflow does not cover requested fields: {', '.join(unsupported)}; reanalysis is required"
        )

    page_limit = (
        max_pages or spec.traversal.pagination.max_pages or spec.traversal.max_pages
    )
    spec_page_limit = page_limit
    if spec_page_limit is None:
        existing_limits = [
            step.max_iterations
            for step in _steps(plan.steps)
            if step.op == Operation.LOOP_PAGES
        ]
        if existing_limits:
            spec_page_limit = max(existing_limits)
    selected_set = set(selected)

    def adapt_steps(steps: list[PlanStep]) -> list[PlanStep]:
        adapted = []
        for step in steps:
            if step.op == Operation.EXTRACT and step.field not in selected_set:
                continue
            step.body = adapt_steps(step.body)
            if step.op == Operation.OPEN_URL and step.url == old_url:
                step.url = start_url
            elif step.op == Operation.LOOP_ELEMENTS:
                step.max_iterations = item_limit
            elif step.op == Operation.LOOP_PAGES and page_limit is not None:
                step.max_iterations = page_limit
            # An extraction-only loop can become empty after field selection.
            if (
                step.op in {Operation.LOOP_ELEMENTS, Operation.LOOP_PAGES}
                and not step.body
            ):
                continue
            adapted.append(step)
        return adapted

    identifier = uuid4().hex
    spec.request_id = f"reuse_{identifier}"
    plan.name = f"reuse-{identifier}"
    spec.start_urls = [start_url]
    spec.policy.allowed_domains = reuse_allowed_domains(spec.policy.allowed_domains, spec.start_urls)
    plan.start_urls = [start_url]
    spec.fields = [source_fields[name] for name in selected]
    spec.objective = f"Collect up to {item_limit} records from {start_url} with fields: {', '.join(selected)}."
    spec.traversal.item_limit = item_limit
    if spec_page_limit is not None:
        spec.traversal.max_pages = spec_page_limit
        spec.traversal.pagination.max_pages = spec_page_limit
    if output_filename is not None and not output_filename.strip():
        raise ValueError("output_filename must be a non-empty plain filename")
    spec.output.filename = (
        output_filename
        if output_filename is not None
        else f"{spec.request_id}.{spec.output.format}"
    )
    plan.steps = adapt_steps(plan.steps)

    # Assignment does not validate Pydantic models; validate the complete results.
    return (
        CrawlSpec.model_validate(spec.model_dump(mode="json")),
        CrawlPlan.model_validate(plan.model_dump(mode="json")),
    )
