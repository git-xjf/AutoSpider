import json
from pathlib import Path

import pytest

from packages.site_memory import SiteMemoryRepository

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_memory_is_evidence_backed(tmp_path):
    repo = SiteMemoryRepository(tmp_path / "memory.db")
    page = load("page_model.json")
    with pytest.raises(ValueError):
        repo.commit_validated(
            page_model=page,
            crawl_plan={"version": "0.1"},
            crawl_spec_hash="abc",
            workflow_name="fixture",
            validation_score=0.69,
        )
    repo.commit_validated(
        page_model=page,
        crawl_plan={"version": "0.1"},
        crawl_spec_hash="abc",
        workflow_name="fixture",
        validation_score=0.95,
    )
    matches = repo.lookup("example.com", page_type="LIST_DETAIL", fingerprint=page["fingerprint"])
    assert matches[0]["fingerprint_similarity"] == 1
    assert matches[0]["match_kind"] == "exact_page_type"
    assert {item["field_name"] for item in matches[0]["selectors"]} >= {"title", "content"}

    fallback = repo.lookup("example.com", page_type="DETAIL")
    assert fallback[0]["match_kind"] == "domain_fallback"
    assert fallback[0]["page_type"] == "LIST_DETAIL"

    workflows = repo.lookup_workflows("example.com")
    assert workflows[0]["name"] == "fixture"
    assert workflows[0]["crawl_plan"] == {"version": "0.1"}

    workflow = repo.get_workflow(workflows[0]["id"])
    assert workflow["domain"] == "example.com"
    assert workflow["crawl_plan"] == {"version": "0.1"}

    with pytest.raises(KeyError):
        repo.get_workflow(999)
