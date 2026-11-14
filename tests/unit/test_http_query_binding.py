"""Actual request dispatch must preserve URL queries and override by key only."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from packages.crawl_core.errors import PolicyViolation
from packages.executor.http import HttpExecutor


class RecordingPolicy:
    def __init__(self, allow=True):
        self.urls = []
        self.robots = []
        self.allow = allow
    def check_url(self, url, **_):
        assert urlsplit(url).hostname == "binding.example.test"
        self.urls.append(url)
    def robots_allowed(self, url):
        self.robots.append(url)
        return self.allow


def fixture_state(client):
    return SimpleNamespace(client=client, robots_checked=set(), spec=SimpleNamespace(
        start_urls=["https://binding.example.test/list"], policy=SimpleNamespace(
            allowed_domains=["binding.example.test"], block_private_networks=True, respect_robots=True)))


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,expected", [
    ({}, {"channel": ["10"], "page_size": ["10"], "tag": ["a", "b"]}),
    ({"page": 2}, {"channel": ["10"], "page_size": ["10"], "tag": ["a", "b"], "page": ["2"]}),
    ({"channel": 37}, {"channel": ["37"], "page_size": ["10"], "tag": ["a", "b"]}),
    ({"tag": ["x", "y"]}, {"channel": ["10"], "page_size": ["10"], "tag": ["x", "y"]}),
])
async def test_query_is_preserved_and_policy_checks_actual_merged_url(extra, expected):
    received = []
    def handler(request):
        received.append(str(request.url))
        return httpx.Response(200, text="ok")
    policy = RecordingPolicy()
    executor = HttpExecutor(None, policy)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await executor._request(fixture_state(client), "GET",
            "https://binding.example.test/list?channel=10&page_size=10&tag=a&tag=b",
            params=extra, json_body=None, form_body=None)
    assert parse_qs(urlsplit(received[0]).query) == expected
    assert policy.urls == policy.robots == received


@pytest.mark.asyncio
async def test_redirect_does_not_reapply_original_overrides():
    received = []
    def handler(request):
        received.append((str(request.url), request.method, request.content))
        if len(received) == 1:
            return httpx.Response(303, headers={"location": "/next?channel=37"})
        return httpx.Response(200, text="ok")
    policy = RecordingPolicy()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await HttpExecutor(None, policy)._request(fixture_state(client), "POST",
            "https://binding.example.test/list?channel=10", params={"page": 2},
            json_body={"public_fixture": True}, form_body=None)
    assert parse_qs(urlsplit(received[0][0]).query) == {"channel": ["10"], "page": ["2"]}
    assert received[1] == ("https://binding.example.test/next?channel=37", "GET", b"")
    assert policy.urls == policy.robots == [row[0] for row in received]


@pytest.mark.asyncio
async def test_robots_denial_is_checked_on_merged_url_before_send():
    def no_network(_):
        pytest.fail("Robots-denied request must not be dispatched")
    policy = RecordingPolicy(allow=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
        with pytest.raises(PolicyViolation, match="robots"):
            await HttpExecutor(None, policy)._request(fixture_state(client), "GET",
                "https://binding.example.test/list?channel=10", params={"channel": 37},
                json_body=None, form_body=None)
    assert policy.urls == policy.robots == ["https://binding.example.test/list?channel=37"]
