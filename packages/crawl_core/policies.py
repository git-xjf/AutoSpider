from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .errors import PolicyViolation
from .models import CrawlSpec
from .public_dns import (
    assert_verified_public_dns,
    clear_public_dns_cache,
    normalize_public_dns_name,
)
from .site_scope import default_allowed_domains, normalize_host


def host_allowed(host: str, allowed_domains: list[str]) -> bool:
    try:
        normalized = normalize_host(host)
        allowed = [normalize_host(domain) for domain in allowed_domains]
    except (ValueError, TypeError, AttributeError):
        return False
    return any(
        normalized == domain or normalized.endswith(f".{domain}") for domain in allowed
    )


# DNS resolution cache. socket.getaddrinfo is blocking and has no built-in
# timeout; under a TUN/Fake-IP resolver a single lookup can stall for minutes.
# The probe browser calls assert_public_host for every intercepted request, so
# results are cached per hostname and the wait is bounded.
_dns_cache: dict[str, tuple[float, frozenset | None, str]] = {}
_dns_lock = threading.Lock()
_dns_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="autospider-dns")


def clear_dns_cache() -> None:
    """Drop all cached DNS results (mainly for tests and network changes)."""
    with _dns_lock:
        _dns_cache.clear()
    clear_public_dns_cache()


def _resolve_host_addresses(host: str) -> set[str]:
    """Resolve *host* to IP addresses with a TTL cache and a bounded wait.

    Successes are cached for AUTOSPIDER_DNS_CACHE_TTL seconds (default 300),
    failures for AUTOSPIDER_DNS_NEGATIVE_TTL seconds (default 30), and a single
    lookup is given up after AUTOSPIDER_DNS_TIMEOUT seconds (default 3).
    """
    ttl = float(os.getenv("AUTOSPIDER_DNS_CACHE_TTL", "300"))
    negative_ttl = float(os.getenv("AUTOSPIDER_DNS_NEGATIVE_TTL", "30"))
    timeout = float(os.getenv("AUTOSPIDER_DNS_TIMEOUT", "3"))
    key = host.lower().rstrip(".")
    now = time.monotonic()
    with _dns_lock:
        entry = _dns_cache.get(key)
        if entry is not None and entry[0] > now:
            if entry[1] is not None:
                return set(entry[1])
            raise PolicyViolation(entry[2])
    future = _dns_executor.submit(socket.getaddrinfo, host, None)
    addresses: frozenset | None
    try:
        addresses = frozenset(item[4][0] for item in future.result(timeout=timeout))
        error = ""
    except (FutureTimeoutError, TimeoutError):
        addresses = None
        error = f"DNS resolution timed out after {timeout:.1f}s for host {host!r}"
    except socket.gaierror:
        addresses = None
        error = f"cannot resolve host {host!r}"
    with _dns_lock:
        expiry = now + (ttl if addresses is not None else negative_ttl)
        _dns_cache[key] = (expiry, addresses, error)
    if addresses is None:
        raise PolicyViolation(error)
    return set(addresses)


def assert_public_host(host: str) -> None:
    # Both compatibility modes are administrator-only and never authorize an
    # IP literal, a broader crawl domain, or a non-Fake-IP private address.
    mode = os.getenv("AUTOSPIDER_FAKE_IP_MODE", "strict").strip().lower()
    if mode not in {"strict", "verified_public"}:
        raise PolicyViolation(
            "AUTOSPIDER_FAKE_IP_MODE must be strict or verified_public"
        )
    trusted = {
        value.strip().lower().rstrip(".")
        for value in os.getenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", "").split(",")
        if value.strip()
    }
    try:
        ipaddress.ip_address(host)
        dns_name = False
    except ValueError:
        dns_name = True
    addresses = _resolve_host_addresses(host)
    if not addresses:
        raise PolicyViolation(f"cannot resolve host {host!r} to any IP address")
    fake_ip = ipaddress.ip_network("198.18.0.0/15")
    fake_addresses = []
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise PolicyViolation(
                f"DNS returned an invalid address for {host!r}"
            ) from exc
        if not address.is_global:
            if dns_name and address in fake_ip:
                fake_addresses.append(raw)
                continue
            raise PolicyViolation(
                f"private, loopback, link-local or reserved target is forbidden: {host} -> {raw}"
            )
    if not fake_addresses:
        return
    if mode == "verified_public":
        # Verify ALL Fake-IP DNS names, including old manual exceptions. A
        # negative public answer or unavailable resolver never falls back.
        assert_verified_public_dns(normalize_public_dns_name(host))
        return
    if host.lower().rstrip(".") not in trusted:
        raise PolicyViolation(
            f"private, loopback, link-local or reserved target is forbidden: {host} -> {fake_addresses[0]}"
        )


class PolicyEngine:
    def check_url(
        self,
        url: str,
        *,
        allowed_domains: list[str] | None = None,
        block_private_networks: bool = True,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PolicyViolation("only absolute http/https URLs are supported")
        if parsed.username or parsed.password:
            raise PolicyViolation("URLs containing credentials are forbidden")
        allowed = allowed_domains or default_allowed_domains([url])
        if not host_allowed(
            parsed.hostname, [item.lower().rstrip(".") for item in allowed]
        ):
            raise PolicyViolation(
                f"domain {parsed.hostname!r} is not in allowed_domains"
            )
        if block_private_networks:
            assert_public_host(parsed.hostname)

    def check(self, spec: CrawlSpec) -> None:
        allowed = spec.policy.allowed_domains
        if not allowed:
            allowed = default_allowed_domains(spec.start_urls)
        for url in spec.start_urls:
            self.check_url(
                url,
                allowed_domains=allowed,
                block_private_networks=spec.policy.block_private_networks,
            )

    def robots_allowed(
        self, url: str, user_agent: str = "AutoSpider/0.1", timeout: float = 10
    ) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            response = httpx.get(robots_url, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError:
            return False
        if response.status_code in {401, 403}:
            return False
        if response.status_code >= 400:
            return True
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser.can_fetch(user_agent, url)
