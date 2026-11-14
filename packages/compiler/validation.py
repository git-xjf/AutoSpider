from packages.crawl_core.errors import CompileError


def validate_task(task: dict) -> None:
    graph = task.get("graph")
    if not isinstance(graph, list) or not graph:
        raise CompileError("compiled task has no graph")
    ids = [node.get("id") for node in graph]
    if ids != list(range(len(graph))):
        raise CompileError("graph IDs must be contiguous and match list indices")
    if graph[0].get("option") != 0:
        raise CompileError("graph must start with a root node")
    for index, node in enumerate(graph):
        if node.get("index") != index:
            raise CompileError(f"node {index} has invalid index")
        if index and node.get("parentId") not in range(len(graph)):
            raise CompileError(f"node {index} has invalid parentId")
        for child in node.get("sequence", []):
            if child not in range(len(graph)) or graph[child].get("parentId") != index:
                raise CompileError(f"node {index} has inconsistent child {child}")
    output_names = [item["name"] for item in task.get("outputParameters", [])]
    if len(output_names) != len(set(output_names)):
        raise CompileError("output parameter names are not unique")

