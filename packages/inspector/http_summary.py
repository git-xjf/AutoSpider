"""Small, deterministic HTML evidence for model-facing HTTP analysis tools.

Pagination and selector counts use the full document. The excerpt is only a
preview; page text and executable scripts are never treated as instructions.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree, html

from .dom_simplifier import DROP_TAGS, simplify_dom


_PAGER_CLASS = re.compile(
    r"(?:pagination|pager|paging|(?:^|[-_\s])pages?(?:$|[-_\s]))", re.I
)
_PAGER_TEXT = re.compile(
    r"^(?:\d{1,4}|下一页|上一页|下页|上页|next|prev(?:ious)?|[<>»«›‹]+)$", re.I
)
_SAFE_CSS_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,79}$")
_PAGER_SCRIPT = re.compile(
    r"(?:createPageHTML|createPagination|pagination|pageCount|totalPages)", re.I
)
_BODY_MARKER = re.compile(
    r"(?:content|article[-_]?body|article[-_]?text|detail[-_]?body|正文)", re.I
)
_NAV_MARKER = re.compile(
    r"(?:^|[-_\s])(?:nav(?:igation|bar)?|menu|header|footer|breadcrumbs?)(?:$|[-_\s])",
    re.I,
)
_SIDE_MARKER = re.compile(
    r"(?:sidebar|side[-_]?bar|list[-_]?right|right[-_]?(?:rail|column|col)|recommend|related|popular|trending|排行|推荐|热点|侧栏)",
    re.I,
)
_DATE_TEXT = re.compile(
    r"(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2})?"
)
_ARTICLE_PATH = re.compile(r"(?:\.s?html?(?:$|[?#])|/detail(?:/|\?))", re.I)
_OVERVIEW_MAX_CHARS = 3300


def _clean_text(value: str, maximum: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:maximum]


def _visible_element_text(element: html.HtmlElement) -> str:
    preview = deepcopy(element)
    for node in preview.xpath(
        ".//script|.//style|.//noscript|.//template|.//svg|.//canvas"
    ):
        node.drop_tree()
    return re.sub(r"\s+", " ", " ".join(preview.itertext())).strip()


def _parse(raw_html: str) -> html.HtmlElement:
    try:
        return html.fromstring(raw_html)
    except (ValueError, etree.ParserError) as exc:
        raise ValueError("HTML response contains no parseable document") from exc


def _safe_url(value: str, base_url: str) -> str | None:
    if not value.strip():
        return None
    try:
        target = urljoin(base_url, value.strip())
        parsed = urlparse(target)
        base = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if (
            parsed.username
            or parsed.password
            or parsed.hostname.lower() != (base.hostname or "").lower()
        ):
            return None
        # Very large URLs consume the model-facing evidence budget and are not
        # useful previews. Do not fabricate a truncated URL that could be used.
        if len(target) > 500:
            return None
        return target
    except ValueError:
        return None


def _is_pagination_link(anchor: html.HtmlElement, text: str) -> bool:
    rel = anchor.get("rel", "").lower().split()
    if (
        "next" in rel
        or "prev" in rel
        or "previous" in rel
        or _PAGER_TEXT.fullmatch(text)
    ):
        return True
    for ancestor in (anchor, *anchor.iterancestors()):
        marker = " ".join((ancestor.get("class", ""), ancestor.get("id", "")))
        if _PAGER_CLASS.search(marker):
            # A page/layout wrapper may contain the entire news list. Only a
            # bounded, non-article link group supplies pagination evidence.
            if (
                len(ancestor.xpath(".//a[@href]")) <= 20
                and not ancestor.xpath(".//article|.//h1|.//h2|.//h3|.//time")
                and not _DATE_TEXT.search(_visible_element_text(ancestor))
            ):
                return True
    return False


def _render_safe_excerpt(tree: html.HtmlElement, url: str, maximum: int) -> str:
    preview = deepcopy(tree)
    for anchor in preview.xpath("//a[@href]"):
        if _safe_url(anchor.get("href", ""), url) is None:
            anchor.attrib.pop("href", None)
    return simplify_dom(html.tostring(preview, encoding="unicode"), max_chars=maximum)


def _element_selector(element: html.HtmlElement) -> str:
    tag = element.tag.lower()
    identifier = element.get("id", "")
    if _SAFE_CSS_CLASS.fullmatch(identifier):
        return f"{tag}#{identifier}"
    tokens = [
        token
        for token in element.get("class", "").split()
        if _SAFE_CSS_CLASS.fullmatch(token)
    ][:3]
    while tokens and len(tag + "".join("." + token for token in tokens)) > 160:
        tokens.pop()
    return tag + "".join("." + token for token in tokens)


def _structural_excerpt(element: html.HtmlElement, url: str) -> str:
    preview = deepcopy(element)
    for child in preview.iter():
        if isinstance(child.tag, str):
            if child.text:
                child.text = _clean_text(child.text, 35)
            if child.tail:
                child.tail = _clean_text(child.tail, 20)
            for name, value in list(child.attrib.items()):
                if name not in {"href", "src"} and len(value) > 120:
                    child.attrib[name] = value[:120]
    return _render_safe_excerpt(preview, url, 400)


def _relative_field_selector(scope: html.HtmlElement, element: html.HtmlElement) -> str:
    if element is scope:
        return "."
    steps = []
    current = element
    while current is not scope:
        classes = [
            token
            for token in current.get("class", "").split()
            if _SAFE_CSS_CLASS.fullmatch(token)
        ]
        if classes:
            marker = (
                f"contains(concat(' ',normalize-space(@class),' '),' {classes[0]} ')"
            )
            return f".//{current.tag}[{marker}]" + "".join(
                f"/{step}" for step in reversed(steps)
            )
        parent = current.getparent()
        if parent is None:
            break
        siblings = [child for child in parent if child.tag == current.tag]
        steps.append(
            current.tag
            + (f"[{siblings.index(current) + 1}]" if len(siblings) > 1 else "")
        )
        current = parent
    return "./" + "/".join(reversed(steps))


def _card_field_hints(
    sample: html.HtmlElement, anchors: list[html.HtmlElement]
) -> dict[str, Any]:
    hints = {}
    title = max(
        anchors, key=lambda anchor: len(_visible_element_text(anchor)), default=None
    )
    date = next(
        (
            element
            for element in sample.iter()
            if isinstance(element.tag, str) and _DATE_TEXT.search(element.text or "")
        ),
        None,
    )
    for name, element in (("title", title), ("publish_time", date)):
        if element is not None and _visible_element_text(element):
            hints[name] = {
                "selector_type": "xpath",
                "selector": _relative_field_selector(sample, element),
                "sample": _clean_text(_visible_element_text(element), 60),
            }
    return hints


def _repeated_blocks(tree: html.HtmlElement, url: str) -> list[dict[str, Any]]:
    candidates = [
        element
        for element in tree.iter()
        if isinstance(element.tag, str)
        and element.tag.lower() not in DROP_TAGS
        and element.tag.lower()
        not in {"html", "body", "head", "meta", "link", "br", "a", "p", "span", "time"}
    ]
    keys = [
        (element.tag.lower(), " ".join(element.get("class", "").split()))
        for element in candidates
    ]
    counts = Counter(keys)
    blocks = []
    seen = set()
    for key, count in counts.most_common():
        if count < 3 or key in seen:
            continue
        tag, classes = key
        if not classes and tag not in {"article", "li", "tr"}:
            continue
        seen.add(key)
        sample = next(
            element for element, candidate in zip(candidates, keys) if candidate == key
        )
        anchors = [
            anchor
            for anchor in sample.xpath(".//a[@href]")
            if _safe_url(anchor.get("href", ""), url) is not None
        ]
        region = (sample, *sample.iterancestors())
        if not anchors or any(
            isinstance(parent.tag, str)
            and (
                parent.tag.lower() in {"nav", "header", "footer"}
                or _NAV_MARKER.search(
                    " ".join((parent.get("class", ""), parent.get("id", "")))
                )
            )
            for parent in region
        ):
            continue
        if all(
            _is_pagination_link(anchor, _clean_text(anchor.text_content(), 60))
            for anchor in anchors
        ):
            continue
        targets = {_safe_url(anchor.get("href", ""), url) for anchor in anchors}
        text = _visible_element_text(sample)
        base_path = urlparse(url).path.rstrip("/") + "/"
        in_route = sum(
            urlparse(target).path.startswith(base_path)
            for target in targets
            if target is not None
        )
        sidebar = any(
            parent.tag.lower() == "aside"
            or parent.get("role", "").lower() == "complementary"
            or _SIDE_MARKER.search(
                " ".join((parent.get("class", ""), parent.get("id", "")))
            )
            for parent in region
            if isinstance(parent.tag, str)
        )
        # Structure and requested-route evidence outrank class-name guesses.
        # Nested groups with the same links describe card parts, not extra lists.
        score = (
            6 * bool(_DATE_TEXT.search(text))
            + 3 * bool(sample.xpath(".//h1|.//h2|.//h3|.//h4"))
            + 4 * in_route / len(targets)
            + 2 * any(_ARTICLE_PATH.search(target) for target in targets if target)
            + bool(sample.xpath(".//img"))
            + bool(re.search(r"(?:news|card|item|article)", classes, re.I))
            + min(count, 30) / 10
            - 10 * sidebar
            - max(0, len(targets) - 3)
        )
        blocks.append(
            (
                score,
                sample,
                targets,
                {
                    "tag": tag[:40],
                    "class": classes[:80],
                    "count": count,
                    "selector": _element_selector(sample),
                    "sample_html": _structural_excerpt(sample, url),
                },
            )
        )
    blocks.sort(
        key=lambda block: (
            -block[0],
            len(list(block[1].iterancestors())),
            -block[3]["count"],
        )
    )
    selected = []
    for candidate in blocks:
        _, sample, targets, block = candidate
        if any(
            targets == other[2]
            and (
                sample in other[1].iterancestors() or other[1] in sample.iterancestors()
            )
            for other in selected
        ):
            continue
        if not selected:
            block["field_hints"] = _card_field_hints(
                sample,
                [
                    anchor
                    for anchor in sample.xpath(".//a[@href]")
                    if _safe_url(anchor.get("href", ""), url) is not None
                ],
            )
        selected.append(candidate)
        if len(selected) == 3:
            break
    return [block for _, _, _, block in selected]


def _representative_card_link(
    tree: html.HtmlElement, url: str, blocks: list[dict[str, Any]]
) -> dict[str, str] | None:
    if not blocks:
        return None
    sample = next(
        (
            element
            for element in tree.iter()
            if isinstance(element.tag, str)
            and _element_selector(element) == blocks[0]["selector"]
        ),
        None,
    )
    if sample is None:
        return None
    links = []
    for anchor in sample.xpath(".//a[@href]"):
        target = _safe_url(anchor.get("href", ""), url)
        text = _clean_text(_visible_element_text(anchor), 60)
        if target is not None and not _is_pagination_link(anchor, text):
            links.append(
                {
                    "url": target,
                    "text": text,
                    "rel": _clean_text(anchor.get("rel", ""), 20),
                }
            )
    links.sort(
        key=lambda entry: (
            not bool(entry["text"]),
            not bool(_ARTICLE_PATH.search(entry["url"])),
            -len(entry["text"]),
        )
    )
    return links[0] if links else None


def _headings(tree: html.HtmlElement) -> list[dict[str, str]]:
    headings = tree.xpath("//h1|//h2") or tree.xpath("//h3")
    return [
        {
            "selector": _element_selector(element),
            "text": _clean_text(_visible_element_text(element), 100),
        }
        for element in headings[:2]
    ]


def _body_candidates(tree: html.HtmlElement) -> list[dict[str, Any]]:
    candidates = []
    for element in tree.xpath(
        "//article|//main|//div[@id or @class]|//section[@id or @class]"
    ):
        marker = " ".join((element.get("id", ""), element.get("class", "")))
        paragraphs = len(element.xpath(".//p"))
        semantic = bool(_BODY_MARKER.search(marker)) or element.tag.lower() in {
            "article",
            "main",
        }
        if not semantic and paragraphs < 2:
            continue
        text = _visible_element_text(element)
        links = element.xpath(".//a")
        if len(text) < 100 or (
            len(links) > 3
            and sum(len(_visible_element_text(link)) for link in links) / len(text)
            > 0.4
        ):
            continue
        candidates.append(
            (
                not bool(element.get("id")) or not semantic,
                -len(list(element.iterancestors())),
                -paragraphs,
                {
                    "selector": _element_selector(element),
                    "paragraph_count": paragraphs,
                    "text_chars": len(text),
                    "preview": text[:100],
                },
            )
        )
    candidates.sort(key=lambda candidate: candidate[:3])
    result = []
    seen = set()
    for *_, candidate in candidates:
        if candidate["selector"] not in seen:
            result.append(candidate)
            seen.add(candidate["selector"])
        if len(result) == 2:
            break
    return result


def summarize_http_html(raw_html: str, url: str) -> dict[str, Any]:
    """Return structure rather than repeated article/DOM text, under 3,300 chars.

    Candidates are observations, not verified extraction rules. Selector tests
    still evaluate the full source; actual next-page hrefs outrank other evidence.
    """
    tree = _parse(raw_html)
    pagination_links: list[dict[str, str]] = []
    other_links: list[dict[str, str]] = []
    seen = set()
    for anchor in tree.xpath("//a[@href]"):
        target = _safe_url(anchor.get("href", ""), url)
        if target is None:
            continue
        text = _clean_text(anchor.text_content(), 60)
        rel = _clean_text(anchor.get("rel", ""), 20)
        key = (target, text, rel)
        if key in seen:
            continue
        seen.add(key)
        entry = {"url": target, "text": text, "rel": rel}
        if _is_pagination_link(anchor, text):
            pagination_links.append(entry)
        else:
            other_links.append(entry)
    pagination_links.sort(
        key=lambda entry: (
            "next" not in entry["rel"].lower().split()
            and not bool(
                re.fullmatch(r"(?:下一页|下页|next|[>»›]+)", entry["text"], re.I)
            ),
            "prev" in entry["rel"].lower().split(),
        )
    )
    pagination_links = pagination_links[:6]
    other_links.sort(key=lambda entry: not bool(_ARTICLE_PATH.search(entry["url"])))
    repeated_blocks = _repeated_blocks(tree, url)
    card_link = _representative_card_link(tree, url, repeated_blocks)
    hints = []
    for script in tree.xpath("//script"):
        source = script.text or ""
        match = _PAGER_SCRIPT.search(source)
        if match:
            hints.append(
                {
                    "verified": False,
                    "snippet": _clean_text(
                        source[max(0, match.start() - 25) : match.end() + 110], 160
                    ),
                }
            )
        if len(hints) == 1:
            break
    result: dict[str, Any] = {
        "url": url,
        "page_title": _clean_text(" ".join(tree.xpath("//title/text()")), 120),
        "headings": _headings(tree),
        "body_candidates": _body_candidates(tree),
        "pagination_links": pagination_links,
        "links_preview": [card_link] if card_link else other_links[:1],
        "repeated_blocks": repeated_blocks,
        "pagination_hints": hints,
    }
    # Long real URLs/classes can make even individually capped sections exceed
    # the response budget. Preserve pagination over lower-priority previews.
    for section in (
        "repeated_blocks",
        "body_candidates",
        "headings",
        "pagination_hints",
        "pagination_links",
        "links_preview",
    ):
        minimum = (
            1
            if section in {"repeated_blocks", "pagination_links", "links_preview"}
            else 0
        )
        while (
            len(result[section]) > minimum
            and len(json.dumps(result, ensure_ascii=False)) > _OVERVIEW_MAX_CHARS
        ):
            result[section].pop()
    # Deep positional selectors are optional hints, not a reason to exceed the
    # model-facing budget. Keep the card and real link; title is already visible
    # there, so prefer retaining publication-time evidence when only one fits.
    for block in result["repeated_blocks"]:
        field_hints = block.get("field_hints", {})
        for name in ("title", "publish_time"):
            if len(json.dumps(result, ensure_ascii=False)) > _OVERVIEW_MAX_CHARS:
                field_hints.pop(name, None)
        if not field_hints:
            block.pop("field_hints", None)
    return result


class SelectorValidationError(ValueError):
    """Bounded selector diagnostics without page text or provider exceptions."""

    def __init__(self, code: str, parameter: str, message: str, hint: str):
        super().__init__(message)
        self.code = code
        self.parameter = parameter
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "parameter": self.parameter,
            "error": str(self),
            "hint": self.hint,
        }


_XPATH_FUNCTIONS = frozenset(
    "last position count id local-name namespace-uri name string concat starts-with contains "
    "substring-before substring-after substring string-length normalize-space translate boolean "
    "not true false lang number sum floor ceiling round node text comment processing-instruction".split()
)
_XPATH_BINARY_OPERATORS = frozenset({"and", "or", "div", "mod"})
_XPATH_FUNCTION_CALL = re.compile(
    r"(?<![A-Za-z0-9_.:-])([A-Za-z_][A-Za-z0-9_.:-]*)\s*\("
)
_XPATH_PREFIX = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_.-]*:(?!:)(?:[A-Za-z_][A-Za-z0-9_.-]*|\*)"
)
_XPATH_LITERAL = re.compile(r"'[^']*'|\"[^\"]*\"")
_XPATH_HINT = "Use standard XPath 1.0 nodes/text selectors; use contains(), starts-with() or positional predicates, not regex/XPath 2.0 functions."


def _selector_error(
    code: str, parameter: str, message: str, hint: str
) -> SelectorValidationError:
    return SelectorValidationError(code, parameter, message, hint)


def _check_xpath_compatibility(selector: str, parameter: str) -> None:
    # lxml compiles unknown functions/prefixes/variables successfully and may
    # never evaluate a bad predicate if its node set is empty. Inspect only
    # expression syntax outside quoted literals, never the page or user data.
    expression = _XPATH_LITERAL.sub(lambda match: " " * len(match.group()), selector)
    if "$" in expression:
        raise _selector_error(
            "unsupported_xpath_variable",
            parameter,
            "XPath variables are not supported by this HTML selector tool.",
            "Remove variable references and use a literal or a structural XPath 1.0 predicate.",
        )
    if _XPATH_PREFIX.search(expression):
        raise _selector_error(
            "unsupported_xpath_namespace",
            parameter,
            "XPath namespace prefixes are not configured for HTML selectors.",
            "Use unprefixed HTML element selectors and standard XPath 1.0; regex prefixes such as re: and fn: are not supported.",
        )
    for match in _XPATH_FUNCTION_CALL.finditer(expression):
        function = match.group(1).rsplit("::", 1)[-1]
        # XPath permits a parenthesized right-hand side of binary operators,
        # e.g. contains(@class,'item') and (@id). They are not function calls.
        if function not in _XPATH_FUNCTIONS and function not in _XPATH_BINARY_OPERATORS:
            raise _selector_error(
                "unsupported_xpath_function",
                parameter,
                "This function is not supported by the XPath 1.0 HTML selector engine.",
                _XPATH_HINT,
            )


def _compile_selector(selector: str, selector_type: str, parameter: str = "selectors"):
    if not isinstance(selector, str) or not selector.strip() or len(selector) > 2000:
        raise _selector_error(
            "invalid_selector",
            parameter,
            "Selectors must be non-empty strings of at most 2000 characters.",
            "Provide a bounded XPath 1.0 or CSS selector using the matching selector_type.",
        )
    try:
        if selector_type == "xpath":
            compiled = etree.XPath(selector)
            _check_xpath_compatibility(selector, parameter)
            return compiled
        from lxml.cssselect import CSSSelector

        return CSSSelector(selector)
    except SelectorValidationError:
        raise
    except Exception:
        # cssselect has its own parse/expression exceptions and callers should
        # receive static diagnostics, not their untrusted expression payload.
        raise _selector_error(
            "invalid_selector_syntax",
            parameter,
            "The selector syntax is invalid for the selected HTML engine.",
            "Check selector_type: XPath 1.0 uses //tag or .//tag; CSS uses tag.class. Browser-only CSS pseudo selectors are not supported.",
        ) from None


def _selected_values(
    compiled, scope: html.HtmlElement, parameter: str = "selectors"
) -> list[Any]:
    try:
        selected = compiled(scope)
    except (etree.XPathError, TypeError, ValueError):
        raise _selector_error(
            "selector_evaluation_error",
            parameter,
            "The selector could not be evaluated by the HTML engine.",
            _XPATH_HINT,
        ) from None
    if isinstance(selected, list):
        if any(not isinstance(value, (etree._Element, str)) for value in selected):
            raise _selector_error(
                "invalid_selector_result",
                parameter,
                "Selectors must return nodes or text, not namespace tuples or other values.",
                "Select HTML elements, text(), or attributes; do not use namespace:: nodes.",
            )
        return selected
    if isinstance(selected, str):
        return [selected] if selected else []
    raise _selector_error(
        "invalid_selector_result",
        parameter,
        "Selectors must return nodes or text, not numbers or booleans.",
        "Select elements or text; replace count()/boolean expressions with the matching node selector.",
    )


def _element_scopes(compiled, tree: html.HtmlElement) -> list[html.HtmlElement]:
    try:
        scopes = compiled(tree)
    except (etree.XPathError, TypeError, ValueError):
        raise _selector_error(
            "selector_evaluation_error",
            "scope_selector",
            "The scope selector could not be evaluated by the HTML engine.",
            _XPATH_HINT,
        ) from None
    if not isinstance(scopes, list) or any(
        not isinstance(scope, etree._Element) or not isinstance(scope.tag, str)
        for scope in scopes
    ):
        raise _selector_error(
            "invalid_scope_result",
            "scope_selector",
            "scope_selector must select HTML elements.",
            "Select item elements such as //article or article.item, not text(), attributes, comments or scalar values.",
        )
    return scopes


def validate_http_select_inputs(
    selectors: dict[str, str],
    *,
    selector_type: str = "xpath",
    scope_selector: str | None = None,
    attributes: dict[str, str] | None = None,
    max_samples: int = 2,
    max_chars: int = 500,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Preflight selector inputs without network access or real page content.

    Strict mode raises ValueError-compatible typed diagnostics. Partial mode
    reports per-field errors while global/scope errors remain fatal. It does not
    silently rewrite expressions or treat invalid selectors as zero matches.
    """
    if selector_type not in {"xpath", "css"}:
        raise _selector_error(
            "invalid_selector_type",
            "selector_type",
            "selector_type must be xpath or css.",
            "Choose xpath for XPath 1.0 expressions or css for CSS selectors.",
        )
    if not isinstance(selectors, dict) or not 1 <= len(selectors) <= 20:
        raise _selector_error(
            "invalid_selector_fields",
            "selectors",
            "Provide between 1 and 20 selectors.",
            "Provide a dictionary mapping requested field names to selectors.",
        )
    if any(
        not isinstance(name, str) or not name.strip() or len(name) > 80
        for name in selectors
    ):
        raise _selector_error(
            "invalid_field_name",
            "selectors",
            "Field names must be non-empty strings of at most 80 characters.",
            "Use short, non-empty field names in the selectors dictionary.",
        )
    if type(max_samples) is not int or not 0 <= max_samples <= 5:
        raise _selector_error(
            "invalid_preview_limit",
            "max_samples",
            "max_samples must be an integer from 0 to 5.",
            "Use a small sample count; this tool is for selector verification, not article delivery.",
        )
    if type(max_chars) is not int or not 1 <= max_chars <= 2000:
        raise _selector_error(
            "invalid_preview_limit",
            "max_chars",
            "max_chars must be an integer from 1 to 2000.",
            "Use a bounded text preview.",
        )
    if attributes is not None and not isinstance(attributes, dict):
        raise _selector_error(
            "invalid_attributes",
            "attributes",
            "attributes must be a dictionary of field names to attribute names.",
            'For links, use attributes={"url":"href"} with an element selector.',
        )
    attributes = attributes or {}
    if any(name not in selectors for name in attributes):
        raise _selector_error(
            "invalid_attributes",
            "attributes",
            "Attributes must refer to a supplied field.",
            "Only add attributes for field names present in selectors.",
        )
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 100
        for value in attributes.values()
    ):
        raise _selector_error(
            "invalid_attributes",
            "attributes",
            "Attribute names must be non-empty strings of at most 100 characters.",
            "Use an HTML attribute name such as href, src or content.",
        )
    if type(allow_partial) is not bool:
        raise _selector_error(
            "invalid_partial_mode",
            "allow_partial",
            "allow_partial must be a boolean.",
            "Enable partial diagnostics only when the caller will explicitly handle field_errors.",
        )
    tree = html.fromstring(
        '<html><body><article id="preflight" class="item"><h1>Title</h1><a href="/example">Link</a><span>2000-01-01</span></article></body></html>'
    )
    if scope_selector is not None:
        _element_scopes(
            _compile_selector(scope_selector, selector_type, "scope_selector"), tree
        )
    valid_fields = []
    field_errors = {}
    for name, selector in selectors.items():
        parameter = f"selectors.{name}"
        try:
            _selected_values(
                _compile_selector(selector, selector_type, parameter), tree, parameter
            )
        except SelectorValidationError as exc:
            if not allow_partial:
                raise
            field_errors[name] = exc.as_dict()
        else:
            valid_fields.append(name)
    return {"valid_fields": valid_fields, "field_errors": field_errors}


def select_http_html(
    raw_html: str,
    url: str,
    selectors: dict[str, str],
    *,
    selector_type: str = "xpath",
    scope_selector: str | None = None,
    attributes: dict[str, str] | None = None,
    max_samples: int = 2,
    max_chars: int = 500,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Check supplied selectors on full HTML, returning counts and short samples.

    With a scope selector, fields are evaluated in up to 50 item scopes. The total
    scope count and whether field-count evaluation was capped remain explicit.
    """
    preflight = validate_http_select_inputs(
        selectors,
        selector_type=selector_type,
        scope_selector=scope_selector,
        attributes=attributes,
        max_samples=max_samples,
        max_chars=max_chars,
        allow_partial=allow_partial,
    )
    attributes = attributes or {}
    try:
        tree = _parse(raw_html)
    except ValueError:
        raise _selector_error(
            "html_parse_error",
            "response_format",
            "The HTTP response contains no parseable HTML document.",
            "Verify the response format; JSON endpoints require http_api, while HTML selectors require an HTML document.",
        ) from None
    compiled = {
        name: _compile_selector(selectors[name], selector_type, f"selectors.{name}")
        for name in preflight["valid_fields"]
    }
    field_errors = preflight["field_errors"]
    if scope_selector is None:
        scopes = [tree]
    else:
        scopes = _element_scopes(
            _compile_selector(scope_selector, selector_type, "scope_selector"), tree
        )
    fields: dict[str, Any] = {}
    for name, selector in compiled.items():
        count = 0
        samples = []
        attribute = attributes.get(name)
        try:
            for scope in scopes[:50]:
                matches = _selected_values(selector, scope, f"selectors.{name}")
                count += len(matches)
                for value in matches:
                    if len(samples) >= max_samples:
                        break
                    if isinstance(value, etree._Element):
                        text = (
                            value.get(attribute, "")
                            if attribute
                            else "".join(value.itertext())
                        )
                    else:
                        text = str(value)
                    is_url = (
                        attribute in {"href", "src"}
                        or getattr(value, "attrname", None) in {"href", "src"}
                        or name.lower() in {"url", "href", "link", "source_url"}
                    )
                    if is_url:
                        text = _safe_url(text, url) or ""
                    text = _clean_text(text, max_chars)
                    if text:
                        samples.append(text)
        except SelectorValidationError as exc:
            if not allow_partial:
                raise
            field_errors[name] = exc.as_dict()
            continue
        fields[name] = {"count": count, "samples": samples}
    return {
        "url": url,
        "selector_type": selector_type,
        "scope_count": len(scopes),
        "scopes_evaluated": min(50, len(scopes)),
        "scope_count_truncated": len(scopes) > 50,
        "fields": fields,
        **({"field_errors": field_errors} if field_errors else {}),
    }
