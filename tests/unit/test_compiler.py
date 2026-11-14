import json
from pathlib import Path

from packages.compiler import EasySpiderCompiler
from packages.crawl_core.models import CrawlSpec, PageModel
from packages.crawl_core.planner import build_crawl_plan

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_compile_is_deterministic_and_graph_is_consistent():
    spec = CrawlSpec.model_validate(load("crawl_spec.json"))
    plan = build_crawl_plan(spec, PageModel.model_validate(load("page_model.json")))
    compiler = EasySpiderCompiler()
    first = compiler.compile(spec, plan)
    second = compiler.compile(spec, plan)
    assert first == second
    assert first["quitWaitTime"] == 0
    assert first["outputFormat"] == "csv"
    item_loop = next(node for node in first["graph"] if node["option"] == 8 and node["parameters"]["loopType"] == 1)
    extraction_nodes = [first["graph"][node_id] for node_id in item_loop["sequence"] if first["graph"][node_id]["option"] == 3]
    assert [len(node["parameters"]["paras"]) for node in extraction_nodes] == [3, 2]
    assert [node["parameters"]["newLine"] for node in extraction_nodes] == [0, 1]
    assert [node["id"] for node in first["graph"]] == list(range(len(first["graph"])))
    assert first["graph"][0]["sequence"] == [1, 2]
    for node in first["graph"][1:]:
        assert node["id"] in first["graph"][node["parentId"]]["sequence"]


def test_sample_compilation_limits_pages_and_items():
    spec = CrawlSpec.model_validate(load("crawl_spec.json"))
    plan = build_crawl_plan(spec, PageModel.model_validate(load("page_model.json")))
    task = EasySpiderCompiler().compile(spec, plan, sample_size=3)
    loops = [node for node in task["graph"] if node["option"] == 8]
    assert loops[0]["parameters"]["exitCount"] == 1
    assert "position() <= 3" in loops[1]["parameters"]["xpath"]


def test_url_field_uses_easyspider_link_extraction():
    spec = CrawlSpec.model_validate(load("crawl_spec.json"))
    plan = build_crawl_plan(spec, PageModel.model_validate(load("page_model.json")))
    task = EasySpiderCompiler().compile(spec, plan)
    url_param = next(
        param
        for node in task["graph"] if node["option"] == 3
        for param in node["parameters"]["paras"]
        if param["name"] == "url"
    )
    assert url_param["nodeType"] == 2
    assert url_param["contentType"] == 0
