import socket
import time

import pytest

from packages.crawl_core.errors import PolicyViolation
from packages.crawl_core.policies import assert_public_host, clear_dns_cache


@pytest.fixture(autouse=True)
def _isolate_dns_cache():
    clear_dns_cache()
    yield
    clear_dns_cache()


def _global_addrinfo(*_):
    return [(None, None, None, None, ("93.184.216.34", 0))]


def test_resolution_is_cached_per_host(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: calls.append(a) or _global_addrinfo())
    assert_public_host("example.com")
    assert_public_host("example.com")
    assert_public_host("EXAMPLE.com.")
    assert len(calls) == 1


def test_ttl_zero_disables_cache(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DNS_CACHE_TTL", "0")
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: calls.append(a) or _global_addrinfo())
    assert_public_host("example.com")
    assert_public_host("example.com")
    assert len(calls) == 2


def test_lookup_timeout_raises_and_is_negative_cached(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DNS_TIMEOUT", "0.2")
    monkeypatch.setenv("AUTOSPIDER_DNS_NEGATIVE_TTL", "60")
    calls = []

    def slow_getaddrinfo(*a):
        calls.append(a)
        time.sleep(5)
        return _global_addrinfo()

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    start = time.monotonic()
    with pytest.raises(PolicyViolation, match="timed out"):
        assert_public_host("slow.example.com")
    assert time.monotonic() - start < 2
    # Second call is served from the negative cache without a new lookup.
    with pytest.raises(PolicyViolation):
        assert_public_host("slow.example.com")
    assert len(calls) == 1


def test_resolution_failure_is_negative_cached(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DNS_NEGATIVE_TTL", "60")
    calls = []

    def failing_getaddrinfo(*a):
        calls.append(a)
        raise socket.gaierror("nxdomain")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)
    with pytest.raises(PolicyViolation, match="cannot resolve"):
        assert_public_host("missing.example.com")
    with pytest.raises(PolicyViolation, match="cannot resolve"):
        assert_public_host("missing.example.com")
    assert len(calls) == 1


def test_policy_verdict_not_cached_only_addresses(monkeypatch):
    # Same addresses, different trusted-hosts env per call: verdict must be
    # re-evaluated every time even though the resolution is cached.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_: [(None, None, None, None, ("198.18.0.122", 0))])
    monkeypatch.setenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", "")
    with pytest.raises(PolicyViolation):
        assert_public_host("www.cls.cn")
    monkeypatch.setenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", "www.cls.cn")
    assert_public_host("www.cls.cn")
