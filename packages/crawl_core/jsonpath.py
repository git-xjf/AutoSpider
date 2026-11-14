from __future__ import annotations

import re
from typing import Any


def select_json_path(value: Any, path: str) -> list[Any]:
    """Evaluate the bounded JSONPath subset used by AutoSpider.

    Supported syntax: ``$``, dotted object keys, bracket-quoted keys, numeric
    list indexes and ``[*]`` wildcards. Filters and executable expressions are
    deliberately unsupported so stored workflows remain deterministic.
    """
    normalized = path.strip()
    if normalized in {"", "$"}:
        return [value]
    if normalized.startswith("$."):
        normalized = normalized[2:]
    elif normalized.startswith("$"):
        normalized = normalized[1:]
    normalized = re.sub(r"\[['\"]([^'\"]+)['\"]\]", r".\1", normalized)
    normalized = normalized.replace("[*]", ".*")
    normalized = re.sub(r"\[(\d+)\]", r".\1", normalized)
    if any(character in normalized for character in ("?", "(", ")", "@")):
        raise ValueError("JSONPath filters and expressions are not supported")
    tokens = [token for token in normalized.split(".") if token]
    nodes = [value]
    for token in tokens:
        next_nodes: list[Any] = []
        for node in nodes:
            if token == "*":
                if isinstance(node, list):
                    next_nodes.extend(node)
                elif isinstance(node, dict):
                    next_nodes.extend(node.values())
            elif token.isdigit() and isinstance(node, list):
                index = int(token)
                if 0 <= index < len(node):
                    next_nodes.append(node[index])
            elif isinstance(node, dict) and token in node:
                next_nodes.append(node[token])
        nodes = next_nodes
        if not nodes:
            break
    return nodes
