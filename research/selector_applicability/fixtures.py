"""Authored mechanism diagnostics, not natural website changes or a benchmark.

Only the scorer consumes expected_urls. Decision functions receive URLs, HTML,
the source selector and derived observations, never FixtureCase or its labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qs, urlencode, urlsplit


ORIGIN = "https://binding.example.test"
KEYS = ("channel", "page_size", "tracking")
FAMILIES = (
    "generic_channel_change", "literal_attribute", "literal_class",
    "encoded_constant", "unrelated_layout_constant", "equal_text_regions",
    "literal_union", "tracking_only", "joint_parameter_interaction",
    "same_query_drift", "anchor_reassigned", "missing_unique_anchor",
)


def make_url(values):
    return ORIGIN + "/list?" + urlencode(values)


@dataclass(frozen=True)
class FixtureCase:
    family: str
    old: int

    @property
    def name(self):
        return f"{self.family}-{self.old}"

    @property
    def source_values(self):
        return {key: str(self.old) for key in KEYS}

    @property
    def source_url(self):
        return make_url(self.source_values)

    @property
    def target_values(self):
        values = self.source_values
        if self.family == "tracking_only":
            values["tracking"] = str(self.old + 43)
        elif self.family != "same_query_drift":
            values["channel"] = str(self.old + 43)
            if self.family == "joint_parameter_interaction":
                values["page_size"] = str(self.old + 43)
        return values

    @property
    def target_url(self):
        return make_url(self.target_values)

    @property
    def selector(self):
        if self.family in {"literal_attribute", "equal_text_regions", "tracking_only", "anchor_reassigned"}:
            return f"//section[@data-channel='{self.old}']/article"
        if self.family == "literal_class":
            return f"//section[@class='channel_{self.old}']/article"
        if self.family == "encoded_constant":
            return f"//section[@data-code='c{self.old:04d}']/article"
        if self.family == "unrelated_layout_constant":
            return f"//section[@id='slot']/article[@data-layout='{self.old}']"
        if self.family == "literal_union":
            return f"//section[@id='absent']/article | //section[@data-channel='{self.old}']/article"
        if self.family in {"joint_parameter_interaction", "same_query_drift"}:
            return "//section[@class='picked']/article"
        if self.family == "missing_unique_anchor":
            return "//section[@data-current='yes']/article"
        return "//section[@id='slot']/article"

    def observations(self):
        yield "source", self.source_url, self.render(self.source_url), None
        yield "source_repeat", self.source_url, self.render(self.source_url), None
        for key in KEYS:
            values = self.source_values
            values[key] = str(self.old + 17)
            url = make_url(values)
            yield "probe_" + key, url, self.render(url), key
        yield "target", self.target_url, self.render(self.target_url, after=True), None

    def render(self, url, *, after=False):
        values = parse_qs(urlsplit(url).query, strict_parsing=True)
        channel = int(values["channel"][0])
        other = self.old + 17 if channel == self.old else self.old
        interaction = (self.family == "joint_parameter_interaction"
                       and channel != self.old and int(values["page_size"][0]) != self.old)
        drift = self.family == "same_query_drift" and after
        reassign = self.family == "anchor_reassigned" and after
        def section(value, current):
            attrs = {"data-channel": str(value), "data-code": f"c{value:04d}",
                     "data-current": "yes" if current else "no"}
            if self.family != "missing_unique_anchor":
                attrs["id"] = ("new-slot" if current else "slot") if reassign else ("slot" if current else "secondary")
            if self.family in {"joint_parameter_interaction", "same_query_drift"}:
                picked = current != (interaction or drift)
                attrs["class"] = "picked" if picked else "other"
            else:
                attrs["class"] = f"channel_{value}"
            attributes = " ".join(f'{key}="{escape(item, quote=True)}"' for key, item in attrs.items())
            articles = []
            # This authored server caps every response at three records. It does
            # not establish page_size independence outside the sampled values.
            for index in range(1, 4):
                text_group = "shared" if self.family == "equal_text_regions" else str(value)
                articles.append(
                    f'<article data-layout="{self.old}"><h2>Diagnostic {text_group} record {index}</h2>'
                    f'<a href="/article/{value}-{index}">Read</a>'
                    f'<p>Body for diagnostic {text_group}, item {index}, with distinct text per item.</p></article>')
            return f"<section {attributes}>" + "".join(articles) + "</section>"
        return ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
                + section(channel, True) + section(other, False) + "</body></html>").encode()

    def expected_urls(self):
        """Private scoring contract: requested current channel, not old region ID."""
        channel = self.target_values["channel"]
        return {ORIGIN + f"/article/{channel}-{index}" for index in range(1, 4)}


def cases():
    return [FixtureCase(family, old) for family in FAMILIES for old in (10, 42, 73)]
