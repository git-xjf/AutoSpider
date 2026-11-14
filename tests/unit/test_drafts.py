from copy import deepcopy
import json

import pytest

from packages.crawl_core.drafts import CrawlDraftRepository, build_crawl_draft
from packages.crawl_core.models import CrawlPlan, CrawlSpec, Operation, PageModel


URL = "https://example.com/news"


def build(**overrides):
    arguments = {
        "url": URL,
        "field_selectors": {"title": ".//a", "url": ".//a"},
        "item_selector": "//article",
    }
    arguments.update(overrides)
    return build_crawl_draft(**arguments)


def steps(plan):
    def walk(children):
        for child in children:
            yield child
            yield from walk(child.body)
    return list(walk(plan.steps))


def test_list_draft_creates_strict_models_and_safe_defaults():
    spec, page, plan = build()
    assert isinstance(spec, CrawlSpec)
    assert isinstance(page, PageModel)
    assert isinstance(plan, CrawlPlan)
    assert spec.start_urls == plan.start_urls == [page.url] == [URL]
    assert spec.traversal.item_limit is None
    assert spec.traversal.max_pages == 1
    assert spec.execution.mode == page.recommended_mode == plan.execution_mode == "http_html"
    assert spec.policy.allowed_domains == ["example.com"]
    assert spec.policy.respect_robots and spec.policy.block_private_networks
    assert not spec.policy.allow_cross_domain_navigation
    assert all(field.required for field in spec.fields)
    assert page.page_type == "LIST"
    assert page.fields["url"].extract == "attribute"
    assert page.fields["url"].attribute == "href"
    assert spec.fields[1].type == "url"
    assert len(page.fingerprint) == 64
    assert page.uncertainty
    assert spec.output.format == "json"


def test_single_page_scopes_fields_to_single():
    spec, page, plan = build(item_selector=None, field_selectors={"title": "//h1"})
    assert spec.traversal.mode == "single"
    assert page.page_type == "SINGLE"
    assert page.fields["title"].page_scope == "single"
    assert [step.op for step in plan.steps] == [Operation.OPEN_URL, Operation.EXTRACT]


def test_detail_draft_preserves_fields_and_pagination_evidence():
    spec, page, plan = build(
        field_selectors={"title": ".//h2/a", "url": ".//h2/a", "content": "//main"},
        detail_fields=["content"],
        detail_link_selector=".//h2/a",
        pagination_selector="//a[@rel='next']",
        page_limit=2,
        item_limit=50,
        output_filename="news.csv",
    )
    assert page.page_type == "LIST_DETAIL"
    assert spec.traversal.mode == "list_detail" and spec.traversal.follow_detail
    assert page.fields["content"].page_scope == "detail"
    assert page.fields["title"].page_scope == "list"
    assert page.detail.link_selector == ".//h2/a"
    assert page.pagination.type == "click"
    assert spec.output.format == "csv" and spec.output.filename == "news.csv"
    assert plan.steps[1].max_iterations == 2
    assert {step.field for step in steps(plan) if step.op == Operation.EXTRACT} == {"title", "url", "content"}


def test_pagination_url_default_opens_each_page_before_extracting():
    _, page, plan = build(
        pagination_url_template=URL + "/{{page_number}}.html", page_limit=2
    )
    assert page.pagination.type == "url"
    assert plan.steps[1].body[0].op == Operation.OPEN_URL
    assert plan.steps[1].body[0].url == URL + "/{{page_number}}.html"


def test_pagination_after_extract_retains_homepage_and_skippable_next():
    _, _, plan = build(
        pagination_url_template=URL + "/index_{{page_number}}.shtml",
        pagination_after_extract=True,
        page_limit=2,
    )
    assert plan.steps[0].url == "{{start_url}}"
    assert [step.op for step in plan.steps[1].body] == [
        Operation.WAIT, Operation.LOOP_ELEMENTS, Operation.OPEN_URL
    ]
    assert plan.steps[1].body[-1].id == "next"


def test_pagination_after_extract_accepts_next_page_number():
    _, page, plan = build(
        pagination_url_template=URL + "//{{next_page_number}}.html",
        pagination_after_extract=True,
        page_limit=2,
    )
    assert page.pagination.url_template == URL + "//{{next_page_number}}.html"
    assert plan.steps[1].body[-1].url == page.pagination.url_template


def test_api_mode_keeps_link_values_as_text_and_native_page_pagination():
    spec, page, plan = build(
        mode="http_api", selector_type="jsonpath",
        item_selector="$.items[*]",
        field_selectors={"title": "$.title", "url": "$.url"},
        http_request={"response_format": "json", "pagination": {"type": "page", "page_param": "page"}},
        page_limit=2,
    )
    assert plan.execution_mode == "http_api"
    assert page.fields["url"].extract == "text"
    assert spec.fields[1].type == "url"
    assert plan.steps[0].op == Operation.LOOP_PAGES
    assert plan.steps[0].max_iterations == 2


@pytest.mark.parametrize("request_model", [
    {"response_format": "json", "method": "GET", "params": {"category": "bank"}},
    {"response_format": "json", "method": "POST", "json_body": {"category": "bank"}},
])
def test_api_url_pagination_cannot_silently_drop_request_parameters(request_model):
    with pytest.raises(ValueError, match="native API pagination"):
        build(mode="http_api", selector_type="jsonpath", item_selector="$.items[*]",
              field_selectors={"title": "$.title"}, http_request=request_model,
              pagination_url_template=URL + "/{{page_number}}.json", page_limit=2)


def test_css_playwright_custom_attributes_and_types():
    _, page, plan = build(
        mode="playwright", selector_type="css", item_selector="article",
        field_selectors={"title": "h2 a", "url": "a", "publish_time": "time"},
        field_attributes={"publish_time": "datetime"},
        field_types={"publish_time": "datetime"},
    )
    assert plan.execution_mode == "playwright"
    assert page.fields["publish_time"].attribute == "datetime"
    assert page.fields["publish_time"].extract == "attribute"


def test_input_maps_are_not_mutated_and_fingerprint_is_layout_stable():
    arguments = {"field_selectors": {"title": ".//h2", "url": ".//a"}, "field_types": {"title": "string"}}
    original = deepcopy(arguments)
    first = build(**arguments)
    second = build(**arguments, url=URL + "?channel=2", item_limit=2)
    assert arguments == original
    assert first[0].request_id != second[0].request_id
    assert first[1].fingerprint == second[1].fingerprint
    assert first[1].fingerprint != build(field_selectors={"title": ".//h3", "url": ".//a"})[1].fingerprint


@pytest.mark.parametrize("overrides", [
    {"url": "/relative"}, {"url": "file:///news"}, {"url": "https://user:secret@example.com/news"},
    {"mode": "auto"}, {"mode": "invalid"}, {"selector_type": "invalid"},
    {"selector_type": "jsonpath"}, {"field_selectors": {}},
    {"field_selectors": {"title": ""}}, {"field_selectors": {"invalid-name": ".//a"}},
    {"field_selectors": {"title": "//*["}}, {"field_selectors": {"url": ".//@href"}},
    {"field_attributes": {"missing": "href"}}, {"field_attributes": {"title": ""}},
    {"field_types": {"missing": "string"}}, {"field_types": {"title": "invalid"}},
    {"detail_fields": ["missing"], "detail_link_selector": ".//a"},
    {"detail_fields": ["title", "title"], "detail_link_selector": ".//a"},
    {"detail_fields": ["title"]}, {"item_selector": None, "detail_fields": ["title"], "detail_link_selector": ".//a"},
    {"page_limit": 0}, {"page_limit": 1001}, {"page_limit": True}, {"page_limit": "2"},
    {"item_limit": 0}, {"item_limit": 1001}, {"item_limit": True}, {"item_limit": 1.2},
    {"page_limit": 2},
    {"pagination_selector": "//a", "pagination_url_template": URL + "/{{page_number}}.html"},
    {"pagination_after_extract": True},
    {"pagination_url_template": "/{{page_number}}.html"},
    {"pagination_url_template": "https://other.example/{{page_number}}.html"},
    {"pagination_url_template": URL + "/2.html"},
    {"pagination_url_template": URL + "/{{next_page_number}}/{{unknown}}.html"},
    {"output_filename": "../result.json"}, {"output_filename": ""},
    {"output_filename": "report.exe"},
    {"http_request": {"headers": {"Authorization": "secret"}}},
])
def test_invalid_draft_inputs_fail_before_execution(overrides):
    with pytest.raises(ValueError):
        build(**overrides)


@pytest.mark.parametrize("request_model", [
    None, {"response_format": "html"}, {"headers": {"Cookie": "secret"}},
    {"params": {"api_key": "secret"}}, {"method": "GET", "json_body": {"page": 1}},
])
def test_api_request_strict_validation_and_no_persisted_secrets(request_model):
    with pytest.raises(ValueError):
        build(mode="http_api", selector_type="jsonpath", item_selector="$.items[*]",
              field_selectors={"title": "$.title"}, http_request=request_model)


def test_repository_roundtrip_is_independent_and_integrity_checked(tmp_path):
    original = build()
    repository = CrawlDraftRepository(tmp_path)
    identifier = repository.create(*original)
    assert len(identifier) == 32
    stored = tmp_path / "drafts" / f"{identifier}.json"
    assert stored.is_file()
    restored = repository.get(identifier)
    assert [model.model_dump(mode="json") for model in restored] == [model.model_dump(mode="json") for model in original]
    restored[0].objective = "Changed result"
    assert repository.get(identifier)[0].objective == original[0].objective
    original[1].fields["title"].selector = ".//changed"
    assert repository.get(identifier)[1].fields["title"].selector == ".//a"
    payload = json.loads(stored.read_text(encoding="utf-8"))
    payload["payload"]["crawl_spec"]["objective"] = "Tampered"
    stored.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        repository.get(identifier)


@pytest.mark.parametrize("identifier", ["../result", "../" + "0" * 32, "0" * 31, "g" * 32, "A" * 32, "", None])
def test_repository_rejects_path_traversal_or_non_opaque_id(tmp_path, identifier):
    with pytest.raises(ValueError, match="draft_id"):
        CrawlDraftRepository(tmp_path).get(identifier)


def test_repository_rejects_mismatched_start_urls(tmp_path):
    spec, page, plan = build()
    page.url = URL + "/other"
    with pytest.raises(ValueError, match="URL"):
        CrawlDraftRepository(tmp_path).create(spec, page, plan)


def test_repository_rejects_wrong_version_and_missing_draft(tmp_path):
    repository = CrawlDraftRepository(tmp_path)
    with pytest.raises(FileNotFoundError):
        repository.get("0" * 32)
    identifier = repository.create(*build())
    path = tmp_path / "drafts" / f"{identifier}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = "999"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        repository.get(identifier)


def test_repository_result_roundtrip_does_not_mutate_draft_or_source(tmp_path):
    repository = CrawlDraftRepository(tmp_path)
    identifier = repository.create(*build())
    draft_path = tmp_path / "drafts" / f"{identifier}.json"
    original_draft = draft_path.read_bytes()
    assert repository.get_result(identifier) is None
    result = {"status": "completed", "validation": {"status": "PASS"}, "output_files": ["result.json"]}
    original = deepcopy(result)
    repository.save_result(identifier, result)
    result["validation"]["status"] = "FAIL"
    restored = repository.get_result(identifier)
    assert restored == original
    restored["validation"]["status"] = "FAIL"
    assert repository.get_result(identifier) == original
    assert draft_path.read_bytes() == original_draft
    assert (tmp_path / "drafts" / f"{identifier}.result.json").is_file()


@pytest.mark.parametrize("identifier", ["../result", "../" + "0" * 32, "g" * 32, ""])
def test_repository_results_reject_path_traversal(tmp_path, identifier):
    repository = CrawlDraftRepository(tmp_path)
    with pytest.raises(ValueError, match="draft_id"):
        repository.get_result(identifier)
    with pytest.raises(ValueError, match="draft_id"):
        repository.save_result(identifier, {})


def test_repository_rejects_oversized_result_without_overwriting_prior(tmp_path):
    repository = CrawlDraftRepository(tmp_path)
    identifier = repository.create(*build())
    repository.save_result(identifier, {"status": "completed"})
    with pytest.raises(ValueError, match="100000"):
        repository.save_result(identifier, {"body": "x" * 100_000})
    assert repository.get_result(identifier) == {"status": "completed"}
