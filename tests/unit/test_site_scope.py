from copy import deepcopy

import pytest

from packages.crawl_core.drafts import build_crawl_draft
from packages.crawl_core.policies import host_allowed
from packages.crawl_core.reuse import build_reused_crawl
from packages.crawl_core.site_scope import default_allowed_domains, site_domain


@pytest.mark.parametrize("host,expected", [
    ("cnews.chinadaily.com.cn", "chinadaily.com.cn"),
    ("jl.chinadaily.com.cn", "chinadaily.com.cn"),
    ("ge.cri.cn", "cri.cn"),
    ("news.publisher.co.uk", "publisher.co.uk"),
    ("x.tenant.github.io", "tenant.github.io"),
    ("x.tenant.blogspot.com", "tenant.blogspot.com"),
    ("localhost", "localhost"),
    ("site.unknownsuffix", "site.unknownsuffix"),
    ("127.0.0.1", "127.0.0.1"),
    ("::1", "::1"),
])
def test_site_scope_uses_offline_psl_and_private_tenant_boundaries(host, expected):
    assert site_domain(host) == expected


@pytest.mark.parametrize("host", ["co.uk", "com.cn", "github.io", "blogspot.com", "com"])
def test_known_public_or_private_suffix_cannot_grant_all_tenants(monkeypatch, host):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    with pytest.raises(ValueError, match="public suffix"):
        default_allowed_domains([f"https://{host}/"])


@pytest.mark.parametrize("host", ["bad label.example.com", "-bad.example.com", "bad..example.com", "evil@example.com"])
def test_invalid_dns_name_cannot_be_broadened_into_a_valid_site(host):
    with pytest.raises(ValueError, match="hostname"):
        site_domain(host)


def test_scope_is_explicit_operator_opt_in_and_unknown_config_fails(monkeypatch):
    urls = ["https://cnews.chinadaily.com.cn/news", "https://jl.chinadaily.com.cn/story"]
    monkeypatch.delenv("AUTOSPIDER_DOMAIN_SCOPE", raising=False)
    assert default_allowed_domains(urls) == ["cnews.chinadaily.com.cn", "jl.chinadaily.com.cn"]
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    assert default_allowed_domains(urls) == ["chinadaily.com.cn"]
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "all")
    with pytest.raises(ValueError, match="AUTOSPIDER_DOMAIN_SCOPE"):
        default_allowed_domains(urls)


def test_site_scope_does_not_join_unrelated_tenants_or_suffixes(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    allowed = default_allowed_domains(["https://tenant.github.io/news"])
    assert host_allowed("assets.tenant.github.io", allowed)
    assert not host_allowed("other.github.io", allowed)
    allowed = default_allowed_domains(["https://cnews.chinadaily.com.cn/news"])
    assert host_allowed("jl.chinadaily.com.cn", allowed)
    assert not host_allowed("chinadaily.com.cn.evil.example", allowed)
    assert not host_allowed("other.com.cn", allowed)


def _draft():
    return build_crawl_draft(
        url="https://cnews.chinadaily.com.cn/news",
        field_selectors={"title": ".//a", "url": ".//a", "content": "//main"},
        item_selector="//article",
        detail_fields=["content"],
        detail_link_selector=".//a",
    )


def test_new_draft_site_scope_still_preserves_other_security_flags(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    spec, _, _ = _draft()
    assert spec.policy.allowed_domains == ["chinadaily.com.cn"]
    assert spec.policy.respect_robots and spec.policy.block_private_networks
    assert not spec.policy.allow_cross_domain_navigation


def test_legacy_reuse_gets_operator_authorized_site_scope_without_source_mutation(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "host")
    source_spec, _, source_plan = _draft()
    spec_data = source_spec.model_dump(mode="json")
    plan_data = source_plan.model_dump(mode="json")
    source = deepcopy((spec_data, plan_data))
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    spec, _ = build_reused_crawl(spec_data, plan_data, start_url=source_spec.start_urls[0], item_limit=2)
    assert spec.policy.allowed_domains == ["chinadaily.com.cn"]
    assert (spec_data, plan_data) == source


def test_reuse_preserves_custom_allowed_domains(monkeypatch):
    monkeypatch.setenv("AUTOSPIDER_DOMAIN_SCOPE", "site")
    source_spec, _, source_plan = _draft()
    source_spec.policy.allowed_domains = ["cnews.chinadaily.com.cn", "approved.other.example"]
    spec, _ = build_reused_crawl(
        source_spec.model_dump(mode="json"), source_plan.model_dump(mode="json"),
        start_url=source_spec.start_urls[0], item_limit=2,
    )
    assert spec.policy.allowed_domains == ["approved.other.example", "cnews.chinadaily.com.cn"]
