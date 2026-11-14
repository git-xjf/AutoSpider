from types import SimpleNamespace

import pytest

from packages.crawl_core.models import HttpRequestModel, Operation, PlanStep
from packages.executor.http import HttpExecutor
from packages.executor.playwright import PlaywrightExecutor


def _pagination_state():
    return SimpleNamespace(
        spec=SimpleNamespace(traversal=SimpleNamespace(item_limit=None)),
        plan=SimpleNamespace(http_request=None),
        records=[],
        parameters={},
        document={},
    )


def _record_steps(monkeypatch, executor, calls):
    async def execute_step(state, child, _scope):
        page_number = state.parameters["page_number"]
        calls.append((child.id, page_number))
        if child.id == "collect":
            state.records.append({"page": page_number})
        return True

    monkeypatch.setattr(executor, "_execute_step", execute_step)
    monkeypatch.setattr(executor, "_document_signature", lambda state: str(state.parameters["page_number"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("navigation_op", [Operation.CLICK, Operation.OPEN_URL])
@pytest.mark.parametrize("max_pages", [1, 2])
async def test_next_navigation_is_only_executed_before_the_final_page(monkeypatch, navigation_op, max_pages):
    executor = HttpExecutor(SimpleNamespace())
    state = _pagination_state()
    calls = []
    _record_steps(monkeypatch, executor, calls)
    next_step = PlanStep(
        id="next",
        op=navigation_op,
        url="https://example.com/index_{{page_number}}.html",
        selector="//a[@class='next']",
    )
    step = PlanStep(
        op=Operation.LOOP_PAGES,
        max_iterations=max_pages,
        body=[PlanStep(id="collect", op=Operation.WAIT), next_step],
    )

    changed = await executor._loop_pages(state, step)

    assert changed is True
    expected_calls = []
    for page in range(1, max_pages + 1):
        expected_calls.append(("collect", page))
        if page < max_pages:
            expected_calls.append(("next", page))
    assert calls == expected_calls
    assert len(state.records) == max_pages


@pytest.mark.asyncio
async def test_non_next_open_url_executes_on_every_page(monkeypatch):
    executor = HttpExecutor(SimpleNamespace())
    state = _pagination_state()
    calls = []
    _record_steps(monkeypatch, executor, calls)
    step = PlanStep(
        op=Operation.LOOP_PAGES,
        max_iterations=2,
        body=[
            PlanStep(id="open", op=Operation.OPEN_URL, url="https://example.com/page/{{page_number}}"),
            PlanStep(id="collect", op=Operation.WAIT),
        ],
    )

    await executor._loop_pages(state, step)

    assert calls == [("open", 1), ("collect", 1), ("open", 2), ("collect", 2)]


@pytest.mark.asyncio
async def test_non_navigation_step_named_next_is_not_skipped(monkeypatch):
    executor = HttpExecutor(SimpleNamespace())
    state = _pagination_state()
    calls = []
    _record_steps(monkeypatch, executor, calls)
    step = PlanStep(
        op=Operation.LOOP_PAGES,
        max_iterations=1,
        body=[PlanStep(id="collect", op=Operation.WAIT), PlanStep(id="next", op=Operation.WAIT)],
    )

    await executor._loop_pages(state, step)

    assert calls == [("collect", 1), ("next", 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["http", "playwright"])
@pytest.mark.parametrize("max_pages", [1, 2])
async def test_next_page_number_templates_after_extraction_without_fetching_final_next(monkeypatch, engine, max_pages):
    executor = HttpExecutor(SimpleNamespace()) if engine == "http" else PlaywrightExecutor(SimpleNamespace())
    state = _pagination_state()
    state.record = {}
    state.pages_visited = 0
    state.page = SimpleNamespace(url="https://example.com/banking/")
    counters = []
    loaded_urls = []
    original_step = executor._execute_step

    async def execute_step(state, child, scope):
        if child.id == "collect":
            counters.append((state.parameters["page_number"], state.parameters["next_page_number"]))
            state.records.append({"page": state.parameters["page_number"]})
            return True
        return await original_step(state, child, scope)

    async def log(*_args):
        return None

    async def load_http(state, url, **_kwargs):
        loaded_urls.append(url)

    async def goto_browser(url, **_kwargs):
        loaded_urls.append(url)
        state.page.url = url

    async def page_signature(_page, _items_step):
        return str(state.parameters["page_number"])

    monkeypatch.setattr(executor, "_execute_step", execute_step)
    monkeypatch.setattr(executor, "_log", log)
    if engine == "http":
        monkeypatch.setattr(executor, "_load", load_http)
        monkeypatch.setattr(executor, "_document_signature", lambda state: str(state.parameters["page_number"]))
    else:
        state.page.goto = goto_browser
        monkeypatch.setattr(executor, "_page_signature", page_signature)
    step = PlanStep(
        op=Operation.LOOP_PAGES,
        max_iterations=max_pages,
        body=[
            PlanStep(id="collect", op=Operation.WAIT),
            PlanStep(id="next", op=Operation.OPEN_URL, url="https://example.com/banking/{{next_page_number}}.html"),
        ],
    )

    if engine == "http":
        changed = await executor._loop_pages(state, step)
    else:
        changed = await executor._loop_pages(state, step, state.page)

    assert changed is True
    assert counters == [(page, page + 1) for page in range(1, max_pages + 1)]
    assert loaded_urls == [f"https://example.com/banking/{page}.html" for page in range(2, max_pages + 1)]


@pytest.mark.parametrize("current_page,next_page", [(1, 2), (2, 3)])
def test_http_api_pagination_still_uses_current_page_number(current_page, next_page):
    executor = HttpExecutor(SimpleNamespace())
    state = _pagination_state()
    state.parameters.update(page_number=current_page, next_page_number=next_page)
    request = HttpRequestModel(pagination={"type": "page", "start_page": 4, "page_param": "p"})

    params, json_body, form_body = executor._apply_pagination(state, request, {}, None, None)

    assert params == {"p": current_page + 3}
    assert json_body is None
    assert form_body is None
