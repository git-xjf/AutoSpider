import concurrent.futures
import copy
import socket
import threading
import time

import httpx
import pytest

from packages.crawl_core.errors import PolicyViolation
from packages.crawl_core.policies import (
    assert_public_host,
    clear_dns_cache,
    host_allowed,
)


@pytest.fixture(autouse=True)
def isolated_policy(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_FAKE_IP_MODE", "verified_public")
    monkeypatch.setenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", "")
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_: [(None, None, None, None, ("198.18.0.9", 0))]
    )
    clear_dns_cache()
    yield
    clear_dns_cache()


def response_payload(host, record_type, *, address=None, ttl=60):
    if address is None:
        address = "93.184.216.34" if record_type == 1 else "2606:4700::1111"
    return {
        "Status": 0,
        "TC": False,
        "Question": [{"name": host + ".", "type": record_type}],
        "Answer": [
            {"name": host + ".", "type": record_type, "TTL": ttl, "data": address}
        ],
    }


def install_doh(monkeypatch, callback=None):
    from packages.crawl_core import public_dns

    calls = []
    settings = []

    def handler(request):
        host = request.url.params["name"]
        record_type = int(request.url.params["type"])
        calls.append((host, record_type))
        payload = response_payload(host, record_type)
        result = callback(host, record_type, payload) if callback else payload
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)

    original_client = httpx.Client

    def factory(**kwargs):
        settings.append(kwargs)
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(public_dns.httpx, "Client", factory)
    return calls, settings


def test_verified_public_mode_automatically_allows_public_fake_ip_host(monkeypatch):
    calls, settings = install_doh(monkeypatch)
    assert_public_host("ge.cri.cn")
    assert_public_host("GE.CRI.CN.")
    assert sorted(calls) == [("ge.cri.cn", 1), ("ge.cri.cn", 28)]
    assert settings[0]["verify"] is True
    assert settings[0]["follow_redirects"] is False


def test_strict_remains_default_and_does_not_query_doh(monkeypatch):
    calls, _ = install_doh(monkeypatch)
    monkeypatch.delenv("AUTOSPIDER_FAKE_IP_MODE")
    with pytest.raises(PolicyViolation, match="reserved target"):
        assert_public_host("ge.cri.cn")
    assert calls == []


@pytest.mark.parametrize(
    "host",
    [
        "198.18.0.9",
        "127.0.0.1",
        "localhost",
        "news.local",
        "news.internal",
        "news.lan",
        "news.home.arpa",
        "*.cri.cn",
        "user@cri.cn",
        "cri.cn/path",
        "foo..cri.cn",
        "foo_bar.cri.cn",
        "2130706433",
        "foo.123",
        "cri.cn..",
    ],
)
def test_automatic_exception_rejects_non_public_dns_names_before_doh(monkeypatch, host):
    calls, _ = install_doh(monkeypatch)
    with pytest.raises(PolicyViolation):
        assert_public_host(host)
    assert calls == []


def test_idna_host_verified_using_exact_ascii_name(monkeypatch):
    calls, _ = install_doh(monkeypatch)
    assert_public_host("例子.中国")
    assert sorted(calls) == [
        ("xn--fsqu00a.xn--fiqs8s", 1),
        ("xn--fsqu00a.xn--fiqs8s", 28),
    ]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "198.19.1.2",
        "198.17.0.9",
        "192.0.2.1",
        "::1",
        "fd00::1",
        "fe80::1",
    ],
)
def test_other_private_local_answers_never_reach_doh(monkeypatch, address):
    calls, _ = install_doh(monkeypatch)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_: [(None, None, None, None, (address, 0))]
    )
    # 198.19 is inside /15; 198.17 is an ordinary public IP, not a new exception.
    if address == "198.19.1.2":
        assert_public_host("ge.cri.cn")
        assert len(calls) == 2
    elif address == "198.17.0.9":
        assert_public_host("ge.cri.cn")
        assert calls == []
    else:
        with pytest.raises(PolicyViolation):
            assert_public_host("ge.cri.cn")
        assert calls == []


def test_mixed_local_fake_and_private_answers_denied_before_doh(monkeypatch):
    calls, _ = install_doh(monkeypatch)
    monkeypatch.setenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", "ge.cri.cn")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_: [
            (None, None, None, None, ("198.18.0.9", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ],
    )
    with pytest.raises(PolicyViolation):
        assert_public_host("ge.cri.cn")
    assert calls == []


def test_normal_global_local_answer_never_queries_doh(monkeypatch):
    calls, _ = install_doh(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    assert_public_host("ge.cri.cn")
    assert calls == []


def test_invalid_mode_fails_closed_even_for_public_addresses(monkeypatch):
    install_doh(monkeypatch)
    monkeypatch.setenv("AUTOSPIDER_FAKE_IP_MODE", "allow_anything")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    with pytest.raises(PolicyViolation, match="FAKE_IP_MODE"):
        assert_public_host("ge.cri.cn")


@pytest.mark.parametrize(
    "mutation",
    [
        "status",
        "tc",
        "question_host",
        "question_type",
        "private_a",
        "private_aaaa",
        "malformed_address",
        "unrelated_owner",
        "bad_ttl",
        "mixed_private",
        "no_answer",
        "conflicting_cname",
        "cname_cycle",
    ],
)
def test_malformed_or_non_public_doh_answers_fail_closed(monkeypatch, mutation):
    def altered(host, record_type, original):
        payload = copy.deepcopy(original)
        if mutation == "status":
            payload["Status"] = 3
        elif mutation == "tc":
            payload["TC"] = True
        elif mutation == "question_host":
            payload["Question"][0]["name"] = "evil.example.org."
        elif mutation == "question_type":
            payload["Question"][0]["type"] = 255
        elif mutation == "private_a" and record_type == 1:
            payload["Answer"][0]["data"] = "10.0.0.5"
        elif mutation == "private_aaaa" and record_type == 28:
            payload["Answer"][0]["data"] = "fd00::1"
        elif mutation == "malformed_address":
            payload["Answer"][0]["data"] = "not-an-address"
        elif mutation == "unrelated_owner":
            payload["Answer"][0]["name"] = "evil.example.org."
        elif mutation == "bad_ttl":
            payload["Answer"][0]["TTL"] = -1
        elif mutation == "mixed_private":
            payload["Answer"].append(
                {
                    "name": host + ".",
                    "type": record_type,
                    "TTL": 60,
                    "data": "10.0.0.5" if record_type == 1 else "fd00::1",
                }
            )
        elif mutation == "no_answer":
            payload["Answer"] = []
        elif mutation == "conflicting_cname":
            payload["Answer"].extend(
                [
                    {
                        "name": host + ".",
                        "type": 5,
                        "TTL": 60,
                        "data": "one.example.org.",
                    },
                    {
                        "name": host + ".",
                        "type": 5,
                        "TTL": 60,
                        "data": "two.example.org.",
                    },
                ]
            )
        elif mutation == "cname_cycle":
            payload["Answer"].extend(
                [
                    {
                        "name": host + ".",
                        "type": 5,
                        "TTL": 60,
                        "data": "edge.example.org.",
                    },
                    {
                        "name": "edge.example.org.",
                        "type": 5,
                        "TTL": 60,
                        "data": host + ".",
                    },
                ]
            )
        return payload

    calls, _ = install_doh(monkeypatch, altered)
    with pytest.raises(PolicyViolation, match="public DNS verification"):
        assert_public_host("ge.cri.cn")
    first_calls = len(calls)
    with pytest.raises(PolicyViolation):
        assert_public_host("ge.cri.cn")
    assert len(calls) == first_calls


def test_cname_chain_and_no_aaaa_record_are_supported(monkeypatch):
    def cname(host, record_type, payload):
        if record_type == 28:
            payload["Answer"] = []
        else:
            payload["Answer"][0]["name"] = "cdn.example.org."
            payload["Answer"].insert(
                0,
                {"name": host + ".", "type": 5, "TTL": 25, "data": "cdn.example.org."},
            )
        return payload

    calls, _ = install_doh(monkeypatch, cname)
    assert_public_host("ge.cri.cn")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "failure", ["redirect", "timeout", "invalid_json", "too_large"]
)
def test_doh_transport_failures_deny_and_are_negative_cached(monkeypatch, failure):
    def fail(host, record_type, payload):
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        if failure == "timeout":
            raise httpx.ReadTimeout("unavailable")
        if failure == "invalid_json":
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, content=b"x" * 65537)

    calls, _ = install_doh(monkeypatch, fail)
    with pytest.raises(PolicyViolation, match="public DNS verification"):
        assert_public_host("ge.cri.cn")
    assert len(calls) == 1
    with pytest.raises(PolicyViolation):
        assert_public_host("ge.cri.cn")
    assert len(calls) == 1


def test_public_verification_cache_expires_at_authoritative_ttl(monkeypatch):
    from packages.crawl_core import public_dns

    clock = [100.0]
    monkeypatch.setattr(public_dns.time, "monotonic", lambda: clock[0])
    calls, _ = install_doh(
        monkeypatch,
        lambda host, record_type, payload: response_payload(host, record_type, ttl=10),
    )
    assert_public_host("ge.cri.cn")
    clock[0] += 9
    assert_public_host("ge.cri.cn")
    assert len(calls) == 2
    clock[0] += 2
    assert_public_host("ge.cri.cn")
    assert len(calls) == 4


def test_public_verification_cache_never_exceeds_300_seconds(monkeypatch):
    from packages.crawl_core import public_dns

    clock = [100.0]
    monkeypatch.setattr(public_dns.time, "monotonic", lambda: clock[0])
    calls, _ = install_doh(
        monkeypatch,
        lambda host, record_type, payload: response_payload(
            host, record_type, ttl=1000
        ),
    )
    assert_public_host("ge.cri.cn")
    clock[0] += 301
    assert_public_host("ge.cri.cn")
    assert len(calls) == 4


def test_concurrent_same_host_has_one_pair_of_public_dns_requests(monkeypatch):
    entered = threading.Event()

    def delay(host, record_type, payload):
        entered.set()
        time.sleep(0.08)
        return payload

    calls, _ = install_doh(monkeypatch, delay)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(assert_public_host, "ge.cri.cn") for _ in range(8)]
        assert entered.wait(1)
        for future in futures:
            future.result(timeout=2)
    assert len(calls) == 2


def test_mode_change_rechecks_even_with_verification_cache(monkeypatch):
    install_doh(monkeypatch)
    assert_public_host("ge.cri.cn")
    monkeypatch.setenv("AUTOSPIDER_FAKE_IP_MODE", "strict")
    with pytest.raises(PolicyViolation):
        assert_public_host("ge.cri.cn")


def test_manual_trusted_host_does_not_override_failed_verified_mode(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS", "ge.cri.cn")
    install_doh(monkeypatch, lambda *_: {"Status": 3, "TC": False})
    with pytest.raises(PolicyViolation, match="public DNS verification"):
        assert_public_host("ge.cri.cn")


def test_outer_wait_is_bounded_even_if_transport_stalls(monkeypatch):
    from packages.crawl_core import public_dns

    completed = threading.Event()

    def stall(host, record_type, payload):
        time.sleep(0.15)
        completed.set()
        return payload

    install_doh(monkeypatch, stall)
    monkeypatch.setattr(public_dns, "_WAIT_SECONDS", 0.03)
    started = time.monotonic()
    with pytest.raises(PolicyViolation, match="timed out"):
        assert_public_host("ge.cri.cn")
    assert time.monotonic() - started < 0.12
    assert completed.wait(1)


def test_empty_local_dns_results_fail_closed(monkeypatch):
    calls, _ = install_doh(monkeypatch)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_: [])
    with pytest.raises(PolicyViolation):
        assert_public_host("ge.cri.cn")
    assert calls == []


@pytest.mark.parametrize(
    "host,allowed,expected",
    [
        ("新闻.例子.中国", ["xn--fsqu00a.xn--fiqs8s"], True),
        ("xn--fsqu00a.xn--fiqs8s", ["例子.中国"], True),
        ("例子.中国.evil.com", ["例子.中国"], False),
        ("not例子.中国", ["例子.中国"], False),
        ("EXAMPLE.COM.", ["example.com"], True),
        ("127.0.0.1", ["127.0.0.1"], True),
        ("localhost", ["localhost"], True),
        ("bad host.com", ["bad host.com"], False),
        ("example.com", ["*.com"], False),
        ("foo.example.com", ["example.com.evil.com"], False),
    ],
)
def test_host_allowlist_normalizes_idna_without_widening(host, allowed, expected):
    assert host_allowed(host, allowed) is expected
