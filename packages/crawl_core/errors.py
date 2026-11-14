class AutoSpiderError(Exception):
    """Base exception for expected AutoSpider failures."""


class PolicyViolation(AutoSpiderError):
    """The requested crawl violates a configured safety policy."""


class CompileError(AutoSpiderError):
    """The CrawlPlan cannot be lowered to the requested executor profile."""


class AdapterError(AutoSpiderError):
    """A crawler executor operation failed or returned invalid state."""


class SelectorMatchError(AdapterError):
    """A required extraction failed; carry structure rather than article data."""

    def __init__(self, *, field: str, selector: str, selector_type: str, url: str, page_scope: str):
        from urllib.parse import parse_qsl, urlsplit

        super().__init__(f"selector matched no values: {selector[:500]}")
        self.diagnostic = {"code": "selector_no_match", "field": field,
                           "selector_type": selector_type, "page_scope": page_scope}
        if len(selector) <= 500:
            self.diagnostic["selector"] = selector
        parsed = urlsplit(url)
        secret_keys = {"token", "access_token", "api_key", "apikey", "password", "secret", "signature", "authorization"}
        if len(url) <= 4096 and not parsed.username and not parsed.password and not any(k.lower() in secret_keys for k, _ in parse_qsl(parsed.query)):
            self.diagnostic["url"] = url


class InspectionError(AutoSpiderError):
    """A browser probe failed."""
