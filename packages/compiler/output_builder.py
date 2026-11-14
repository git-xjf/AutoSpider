from packages.crawl_core.models import CrawlSpec


def build_input_parameters(spec: CrawlSpec, graph: list[dict]) -> list[dict]:
    open_node = next(node for node in graph if node["option"] == 1)
    value = "\n".join(spec.start_urls)
    return [
        {
            "id": 0,
            "name": "urlList_0",
            "nodeId": open_node["id"],
            "nodeName": open_node["title"],
            "value": value,
            "desc": "要采集的网址列表，多行以\\n分开",
            "type": "string",
            "exampleValue": spec.start_urls[0],
        }
    ]


def build_output_parameters(spec: CrawlSpec) -> list[dict]:
    return [
        {
            "id": index,
            "name": field.name,
            "desc": field.description,
            "type": "text" if field.type == "string" else field.type,
            "exampleValue": "",
            "recordASField": True,
        }
        for index, field in enumerate(spec.fields)
    ]

