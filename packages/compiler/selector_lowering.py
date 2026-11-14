from packages.crawl_core.errors import CompileError


def lower_selector(selector: str, selector_type: str = "xpath") -> str:
    """Return the XPath understood by EasySpider V0.x.

    CSS is intentionally rejected instead of silently emitting an invalid task.
    The Analyst can test CSS selectors while exploring, but a V0.1 executable
    PageModel must provide XPath selectors.
    """

    value = selector.strip()
    if not value:
        raise CompileError("selector cannot be empty")
    if selector_type != "xpath":
        raise CompileError("EasySpider 0.3.x compilation requires XPath selectors")
    return value


def lower_fallbacks(fallbacks: list[str], selector_type: str = "xpath") -> list[str]:
    return [lower_selector(item, selector_type) for item in fallbacks]

