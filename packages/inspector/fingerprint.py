from __future__ import annotations

import hashlib
import re
from collections import Counter
from urllib.parse import urlparse

from lxml import etree, html


def fingerprint_dom(raw_html: str, url: str = "", page_type: str = "UNKNOWN") -> str:
    try:
        root = html.fromstring(raw_html)
    except (ValueError, etree.ParserError):
        structure = ""
    else:
        tags = Counter(str(element.tag).lower() for element in root.iter() if isinstance(element.tag, str))
        classes = Counter(
            token
            for element in root.iter()
            for token in re.split(r"\s+", element.attrib.get("class", "").strip())
            if token and not any(char.isdigit() for char in token)
        )
        semantic = Counter(
            f"{key}={value}"
            for element in root.iter()
            for key, value in element.attrib.items()
            if key == "role" or key.startswith("aria-") or key.startswith("data-")
        )
        structure = "|".join(
            [
                ",".join(f"{key}:{value}" for key, value in sorted(tags.items())),
                ",".join(f"{key}:{value}" for key, value in sorted(classes.items())),
                ",".join(f"{key}:{value}" for key, value in sorted(semantic.items())),
            ]
        )
    domain = (urlparse(url).hostname or "").lower()
    return hashlib.sha256(f"{domain}|{page_type}|{structure}".encode()).hexdigest()

