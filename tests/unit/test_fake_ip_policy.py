import socket

import pytest

from packages.crawl_core.errors import PolicyViolation
from packages.crawl_core.policies import assert_public_host, clear_dns_cache


@pytest.fixture(autouse=True)
def _isolate_dns_cache():
    clear_dns_cache()
    yield
    clear_dns_cache()


@pytest.mark.parametrize("host,ip,trusted,allowed", [
    ("www.cls.cn", "198.18.0.122", "", False),
    ("www.cls.cn", "198.18.0.122", "www.cls.cn", True),
    ("evil.www.cls.cn", "198.18.0.122", "www.cls.cn", False),
    ("www.cls.cn", "127.0.0.1", "www.cls.cn", False),
    ("www.cls.cn", "10.0.0.1", "www.cls.cn", False),
    ("198.18.0.122", "198.18.0.122", "198.18.0.122", False),
    ("www.cls.cn", "2606:4700::1111", "www.cls.cn", True),
    ("finance.ce.cn", "198.18.0.22", "finance.ce.cn", True),
    ("finance.ce.cn", "198.18.0.22", "www.cls.cn", False),
    ("evil.finance.ce.cn", "198.18.0.22", "finance.ce.cn", False),
    ("finance.ce.cn.evil.example", "198.18.0.22", "finance.ce.cn", False),
    ("finance.ce.cn", "127.0.0.1", "finance.ce.cn", False),
    ("finance.ce.cn", "10.0.0.1", "finance.ce.cn", False),
    ("finance.ce.cn", "169.254.169.254", "finance.ce.cn", False),
    ("finance.ce.cn", "::1", "finance.ce.cn", False),
    ("finance.ce.cn", "fd00::1", "finance.ce.cn", False),
])
def test_fake_ip_exception_is_exact_and_opt_in(monkeypatch, host, ip, trusted, allowed):
    monkeypatch.setenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", trusted)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_: [(None, None, None, None, (ip, 0))])
    if allowed:
        assert_public_host(host)
    else:
        with pytest.raises(PolicyViolation):
            assert_public_host(host)


@pytest.mark.parametrize("private_ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "fd00::1"])
def test_trusted_fake_ip_host_rejects_mixed_private_dns_answers(monkeypatch, private_ip):
    monkeypatch.setenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", "finance.ce.cn")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_: [
        (None, None, None, None, ("198.18.0.22", 0)),
        (None, None, None, None, (private_ip, 0)),
    ])

    with pytest.raises(PolicyViolation):
        assert_public_host("finance.ce.cn")
