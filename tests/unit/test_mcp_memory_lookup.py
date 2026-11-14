import json
from pathlib import Path

from packages.site_memory import SiteMemoryRepository
from services.easyspider_mcp import server


def test_memory_lookup_is_metadata_only_until_requested(monkeypatch, tmp_path):
    fixtures = Path(__file__).parents[1] / "fixtures"
    spec = json.loads((fixtures / "crawl_spec.json").read_text(encoding="utf-8"))
    page = json.loads((fixtures / "page_model.json").read_text(encoding="utf-8"))
    plan = server.crawl_plan_build(spec, page)
    repository = SiteMemoryRepository(tmp_path / "memory.db")
    repository.commit_validated(
        page_model=page,
        crawl_plan=plan,
        crawl_spec_hash="fixture",
        workflow_name=plan["name"],
        validation_score=1,
        easyspider_task_id=7,
    )
    monkeypatch.setattr(server, "memory", repository)

    result = server.memory_lookup_site(spec["start_urls"][0])
    assert result["recommended_workflow_id"] == 1
    assert "page_model" not in result["matches"][0]
    assert "crawl_plan" not in result["workflows"][0]
    assert set(result["workflows"][0]["plan_summary"]["field_names"]) == set(page["fields"])
    assert "content" in result["matches"][0]["field_names"]

    detailed = server.memory_lookup_site(spec["start_urls"][0], include_page_model=True)
    assert detailed["matches"][0]["page_model"] == page
