"""Operator-selected crawl scope using the bundled, offline Public Suffix List.

Site scope grants navigation within one registrable site, not every host sharing
the last two labels. Private PSL entries keep separate hosted tenants isolated.
This module never grants private-network or proxy/Fake-IP exceptions.
"""
from __future__ import annotations

import ipaddress
import os
import re
from functools import lru_cache
from typing import Iterable
from urllib.parse import urlparse

import tldextract


_extract = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, fallback_to_snapshot=True,
    include_psl_private_domains=True,
)


def configured_domain_scope() -> str:
    scope = os.getenv("AUTOSPIDER_DOMAIN_SCOPE", "host").strip().lower()
    if scope not in {"host", "site"}:
        raise ValueError("AUTOSPIDER_DOMAIN_SCOPE must be host or site")
    return scope


def normalize_host(host: str) -> str:
    """Normalize valid DNS/IP syntax without DNS lookups or network permission."""
    normalized = host.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(normalized))
    except ValueError:
        try:
            ascii_host = normalized.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("invalid hostname") from exc
        if len(ascii_host) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in ascii_host.split(".")
        ):
            raise ValueError("invalid hostname")
        return ascii_host


@lru_cache(maxsize=1024)
def site_domain(host: str) -> str:
    """Return the registrable site, or retain an unrecognized/IP host exactly."""
    normalized = normalize_host(host)
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        result = _extract(normalized)
        if result.suffix and not result.domain:
            raise ValueError("a public suffix alone cannot define a registrable site scope")
        return result.top_domain_under_public_suffix or normalized
    return normalized


def default_allowed_domains(start_urls: Iterable[str]) -> list[str]:
    """Derive a default whitelist; explicit caller whitelists bypass this helper."""
    hosts = {normalize_host(urlparse(url).hostname) for url in start_urls if urlparse(url).hostname}
    if configured_domain_scope() == "site":
        return sorted({site_domain(host) for host in hosts})
    return sorted(hosts)


def reuse_allowed_domains(allowed_domains: list[str], start_urls: list[str]) -> list[str]:
    """Upgrade cloned legacy default-host scopes only under operator site opt-in.

    Legacy artifacts do not record whether an exact-start whitelist was implicit.
    This narrow migration treats only an empty list or that historical default as
    operator-authorized; any other customized whitelist is preserved unchanged.
    Stored workflows and completed/failed execution IDs are never modified.
    """
    hosts = sorted({normalize_host(urlparse(url).hostname) for url in start_urls if urlparse(url).hostname})
    normalized = sorted({normalize_host(domain) for domain in allowed_domains})
    if not normalized or normalized == hosts:
        return default_allowed_domains(start_urls)
    return list(allowed_domains)
