from __future__ import annotations

import re

from lxml import etree, html

DROP_TAGS = {"script", "style", "svg", "path", "noscript", "template", "canvas"}
KEEP_ATTRS = {"id", "class", "name", "href", "role", "title", "alt", "type", "value"}


def simplify_dom(raw_html: str, max_chars: int = 16_000) -> str:
    try:
        root = html.fromstring(raw_html)
    except (ValueError, etree.ParserError):
        return ""
    for element in list(root.iter()):
        if str(element.tag).lower() in DROP_TAGS:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                continue
        for key in list(element.attrib):
            lower = key.lower()
            if lower not in KEEP_ATTRS and not lower.startswith("aria-") and not lower.startswith("data-"):
                del element.attrib[key]
        if element.text:
            element.text = re.sub(r"\s+", " ", element.text).strip()[:1000]
        if element.tail:
            element.tail = re.sub(r"\s+", " ", element.tail).strip()[:500]
    rendered = html.tostring(root, encoding="unicode", method="html")
    return rendered[:max_chars]


def visible_text(raw_html: str, max_chars: int = 6_000) -> str:
    try:
        root = html.fromstring(raw_html)
    except (ValueError, etree.ParserError):
        return ""
    for bad in root.xpath("//script|//style|//noscript|//template"):
        bad.drop_tree()
    return re.sub(r"\s+", " ", root.text_content()).strip()[:max_chars]
