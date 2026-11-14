from __future__ import annotations

import re

from .models import CrawlPlan, CrawlSpec, Operation, PageModel, PlanStep


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result[:64] or "crawl"


def _extract_step(field: str, mapping) -> PlanStep:
    selector_key = "relative_selector" if mapping.page_scope == "list" else "selector"
    return PlanStep(
        op=Operation.EXTRACT,
        field=field,
        source=mapping.extract,
        attribute=mapping.attribute,
        selector_type=mapping.selector_type,
        frame_selector=mapping.frame_selector,
        frame_selector_type=mapping.frame_selector_type,
        fallbacks=mapping.fallbacks,
        **{selector_key: mapping.selector},
    )


def build_crawl_plan(spec: CrawlSpec, page: PageModel) -> CrawlPlan:
    missing = [field.name for field in spec.fields if field.name not in page.fields]
    if missing:
        raise ValueError(f"PageModel is missing requested fields: {', '.join(missing)}")

    mode = page.recommended_mode if spec.execution.mode == "auto" else spec.execution.mode
    if mode == "auto":
        mode = "playwright"
    if mode == "http_api" and page.http_request is None:
        raise ValueError("http_api execution requires PageModel.http_request")
    if mode == "http_api":
        selector_types = [mapping.selector_type for mapping in page.fields.values()]
        if page.list_model is not None:
            selector_types.append(page.list_model.item_selector.type)
        if any(selector_type != "jsonpath" for selector_type in selector_types):
            raise ValueError("http_api execution requires jsonpath selectors")
    elif any(mapping.selector_type == "jsonpath" for mapping in page.fields.values()):
        raise ValueError(f"{mode} execution cannot use jsonpath field selectors")
    if mode == "http_html" and page.pagination.type == "infinite_scroll":
        raise ValueError("http_html cannot execute infinite-scroll pagination; use playwright")

    open_url = page.http_request.url if page.http_request and page.http_request.url else "{{start_url}}"
    open_step = PlanStep(id="open", op=Operation.OPEN_URL, url=open_url)
    if page.page_type == "SINGLE":
        extracts = [_extract_step(field.name, page.fields[field.name]) for field in spec.fields]
        return CrawlPlan(
            name=_slug(spec.objective),
            start_urls=spec.start_urls,
            execution_mode=mode,
            http_request=page.http_request if mode != "playwright" else None,
            steps=[open_step, *extracts],
        )

    if page.list_model is None:
        raise ValueError("list page requires item selector")

    list_body = [
        _extract_step(field.name, page.fields[field.name])
        for field in spec.fields
        if page.fields[field.name].page_scope == "list"
    ]
    detail_fields = [field.name for field in spec.fields if page.fields[field.name].page_scope == "detail"]
    if detail_fields:
        if not page.detail.enabled or not page.detail.link_selector:
            raise ValueError("detail fields requested but PageModel has no detail navigation")
        list_body.append(
            PlanStep(
                id="open_detail",
                op=Operation.CLICK,
                relative_selector=page.detail.link_selector,
                selector_type=page.detail.selector_type,
            )
        )
        ready = page.waits.page_ready_selector or page.fields[detail_fields[0]].selector
        ready_type = (
            page.waits.selector_type
            if page.waits.page_ready_selector
            else page.fields[detail_fields[0]].selector_type
        )
        list_body.append(
            PlanStep(
                op=Operation.WAIT,
                selector=ready,
                selector_type=ready_type,
                seconds=page.waits.timeout_seconds,
            )
        )
        list_body.extend(_extract_step(name, page.fields[name]) for name in detail_fields)
        list_body.append(PlanStep(op=Operation.BACK, seconds=spec.execution.request_delay_ms / 1000))

    items = PlanStep(
        id="items",
        op=Operation.LOOP_ELEMENTS,
        selector=page.list_model.item_selector.value,
        selector_type=page.list_model.item_selector.type,
        max_iterations=spec.traversal.item_limit,
        body=list_body,
    )
    # Client-rendered lists may not exist at DOMContentLoaded.
    ready_items = PlanStep(op=Operation.WAIT, selector=items.selector,
                           selector_type=items.selector_type, seconds=page.waits.timeout_seconds)
    work: list[PlanStep] = [ready_items, items]
    http_pagination = page.http_request.pagination if mode == "http_api" and page.http_request else None
    if http_pagination is not None and http_pagination.type != "none":
        max_pages = spec.traversal.pagination.max_pages or spec.traversal.max_pages or 1
        work = [
            PlanStep(
                id="page_loop",
                op=Operation.LOOP_PAGES,
                max_iterations=int(max_pages),
                body=[PlanStep(id="api_page", op=Operation.OPEN_URL, url=open_url), ready_items, items],
            )
        ]
    elif page.pagination.type == "click":
        work.append(
            PlanStep(
                id="next",
                op=Operation.CLICK,
                selector=page.pagination.selector,
                selector_type=page.pagination.selector_type,
                fallbacks=page.pagination.fallbacks,
            )
        )
        max_pages = page.pagination.type != "none" and (
            spec.traversal.pagination.max_pages or spec.traversal.max_pages or 1
        )
        work = [PlanStep(id="page_loop", op=Operation.LOOP_PAGES, max_iterations=int(max_pages), body=work)]
    elif page.pagination.type == "url":
        max_pages = spec.traversal.pagination.max_pages or spec.traversal.max_pages or 1
        work = [
            PlanStep(
                id="page_loop",
                op=Operation.LOOP_PAGES,
                max_iterations=int(max_pages),
                body=[PlanStep(op=Operation.OPEN_URL, url=page.pagination.url_template), ready_items, items],
            )
        ]
    elif page.pagination.type == "infinite_scroll":
        max_pages = spec.traversal.pagination.max_pages or spec.traversal.max_pages or page.pagination.scroll_count
        work = [
            PlanStep(
                id="page_loop",
                op=Operation.LOOP_PAGES,
                max_iterations=int(max_pages),
                body=[ready_items, items, PlanStep(op=Operation.SCROLL, value="end", seconds=spec.execution.request_delay_ms / 1000)],
            )
        ]

    prefix = [] if http_pagination is not None and http_pagination.type != "none" else [open_step]
    return CrawlPlan(
        name=_slug(spec.objective),
        start_urls=spec.start_urls,
        execution_mode=mode,
        http_request=page.http_request if mode != "playwright" else None,
        steps=[*prefix, *work],
    )
