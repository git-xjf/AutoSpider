from __future__ import annotations

from copy import deepcopy
from typing import Any

from packages.crawl_core.errors import CompileError
from packages.crawl_core.models import CrawlPlan, CrawlSpec, Operation

from .graph_builder import GraphBuilder
from .output_builder import build_input_parameters, build_output_parameters
from .validation import validate_task
from .versions import EasySpiderV035Profile


class EasySpiderCompiler:
    """Pure, deterministic CrawlPlan -> EasySpider task compiler."""

    profiles = {"0.3.5": EasySpiderV035Profile}

    def compile(
        self,
        crawl_spec: CrawlSpec | dict[str, Any],
        crawl_plan: CrawlPlan | dict[str, Any],
        target_version: str = "0.3.5",
        sample_size: int | None = None,
    ) -> dict[str, Any]:
        spec = crawl_spec if isinstance(crawl_spec, CrawlSpec) else CrawlSpec.model_validate(crawl_spec)
        plan = crawl_plan if isinstance(crawl_plan, CrawlPlan) else CrawlPlan.model_validate(crawl_plan)
        if plan.start_urls != spec.start_urls:
            raise CompileError("CrawlPlan.start_urls must equal CrawlSpec.start_urls")
        if target_version not in self.profiles:
            raise CompileError(f"unsupported EasySpider target version: {target_version}")

        effective_plan = deepcopy(plan)
        if sample_size is not None:
            if sample_size < 1 or sample_size > 50:
                raise CompileError("sample_size must be between 1 and 50")

            def constrain(steps) -> bool:
                for step in steps:
                    if step.op == Operation.LOOP_PAGES:
                        step.max_iterations = 1
                    if step.op == Operation.LOOP_ELEMENTS:
                        selector = str(step.selector)
                        step.selector = f"({selector})[position() <= {sample_size}]"
                        return True
                    if constrain(step.body):
                        return True
                return False

            constrain(effective_plan.steps)

        profile = self.profiles[target_version]()
        graph = GraphBuilder(profile, spec.start_urls[0]).build(effective_plan.steps)
        # EasySpider's visual task format groups adjacent fields from the same
        # page into one extraction node. Its 0.3.5 executor writes one row per
        # extraction node, so emitting a node per field produces progressive,
        # partial rows instead of one complete record.
        removed_ids: set[int] = set()
        for parent in graph:
            compact_sequence: list[int] = []
            for child_id in parent.get("sequence", []):
                child = graph[child_id]
                if (
                    child.get("option") == 3
                    and compact_sequence
                    and graph[compact_sequence[-1]].get("option") == 3
                ):
                    graph[compact_sequence[-1]]["parameters"]["paras"].extend(
                        child["parameters"]["paras"]
                    )
                    removed_ids.add(child_id)
                else:
                    compact_sequence.append(child_id)
            parent["sequence"] = compact_sequence
        if removed_ids:
            id_map = {
                old_id: new_id
                for new_id, old_id in enumerate(index for index in range(len(graph)) if index not in removed_ids)
            }
            graph = [node for old_id, node in enumerate(graph) if old_id not in removed_ids]
            for old_id, node in zip(id_map, graph, strict=True):
                node["id"] = id_map[old_id]
                node["index"] = id_map[old_id]
                node["parentId"] = id_map[node["parentId"]]
                node["sequence"] = [id_map[child_id] for child_id in node.get("sequence", [])]
        # In 0.3.5 every extraction node defaults to creating a new output
        # row. A logical record may contain several extraction nodes, so only
        # the final extraction in each parent sequence may terminate the row.
        for parent in graph:
            extraction_ids = [
                child_id
                for child_id in parent.get("sequence", [])
                if graph[child_id].get("option") == 3
            ]
            for child_id in extraction_ids:
                graph[child_id]["parameters"]["newLine"] = 0
            if extraction_ids:
                graph[extraction_ids[-1]]["parameters"]["newLine"] = 1
        # EasySpider 0.3.5's UI/executor supports xlsx/csv/txt/mysql, but not
        # JSON. For a JSON-facing CrawlSpec, use CSV as the engine interchange
        # format; the MCP ResultReader converts it back to structured records.
        engine_output_format = "csv" if spec.output.format == "json" else spec.output.format
        output_name = spec.output.filename or f"{plan.name}.{spec.output.format}"
        suffix = f".{spec.output.format}"
        if output_name.lower().endswith(suffix):
            output_name = output_name[: -len(suffix)]
        task = {
            "id": -1,
            "name": plan.name,
            "url": spec.start_urls[0],
            "links": "\n".join(spec.start_urls),
            "version": profile.version,
            "saveThreshold": 10,
            # The 0.3.5 executor otherwise keeps its private Chrome process
            # alive for 60 seconds after the task reports Done.
            "quitWaitTime": 0,
            "cloudflare": 0,
            "environment": 0,
            "maxViewLength": 15,
            "outputFormat": engine_output_format,
            "saveName": output_name,
            "dataWriteMode": 2,
            "containJudge": any(node["option"] in {9, 10} for node in graph),
            "desc": spec.objective,
            "inputParameters": build_input_parameters(spec, graph),
            "outputParameters": build_output_parameters(spec),
            "graph": graph,
        }
        validate_task(task)
        return deepcopy(task)
