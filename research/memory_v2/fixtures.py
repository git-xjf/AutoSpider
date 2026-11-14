"""Frozen, deterministic development and engineering-holdout HTML fixtures.

Labels and expected text belong to the runner/scorer, never to the policy API.
There are no private role markers: body role, Article type, headline, and source
are ordinary public microdata available to every policy's common projector.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from html import escape
import json
import random


SEED = 20261019
SCHEMA_VERSION = "memory-v2-fixtures/1"
RULES = [
    {"rule_id": "content", "selector": "#content", "version": "1", "field_name": "content"},
    {"rule_id": "article", "selector": "#article", "version": "1", "field_name": "content"},
    {"rule_id": "story", "selector": ".story-body", "version": "1", "field_name": "content"},
]
CONTRACT = {"version": "1", "require_source_url": True, "require_body_role": True}


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def payload_hash(value) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def expected_body(index, salt):
    return f"Article {salt}-{index}: the verified report describes the experimental procedure, observations, and supporting evidence."


def make_page(index, salt, *, layout="normal", nav_edit=False, unavailable=False,
              headline_mismatch=False, source_mismatch=False, duplicate_body=False,
              role_missing=False, source_missing=False, headline_missing=False,
              selector_version=False, news_article=False, semantic_body_wrong=False,
              origin="https://fixture.invalid"):
    """The scoring target is returned separately from the public HTML snapshot."""
    url = f"{origin}/news/{salt}-{1000 + index}.html"
    title = f"Report {salt} / {index}"
    body = expected_body(index, salt)
    actual_headline = f"Different source {salt} / {index}" if headline_mismatch else title
    source = f"{origin}/news/foreign-{1000 + index}.html" if source_mismatch else url
    role = "" if role_missing else ' itemprop="articleBody"'
    content_id = "legacy-content" if selector_version else "content"
    selector_node = {
        "normal": f'<div id="{content_id}" class="body"{role}>',
        "article": f'<div id="article" class="body"{role}>',
        "story": f'<div class="story-body"{role}>',
        "unseen": f'<div id="unlisted-body" class="unlisted-body"{role}>',
    }[layout]
    # A mismatched source contains a foreign article rather than the expected
    # report. This avoids calling correct extracted text a scoring error solely
    # because a hidden test label says so.
    selected_text = f"Foreign material for {salt}-{index}; its origin does not match the requested report." if headline_mismatch or source_mismatch else body
    if semantic_body_wrong:
        selected_text = f"Incorrect material for {salt}-{index}; public Article metadata falsely presents this text as the requested report."
    body_markup = selector_node + escape(selected_text) + "</div>"
    if duplicate_body:
        body_markup += selector_node + "Second body from another article; ambiguous selector result.</div>"
    headline_markup = "" if headline_missing else f'<h1 itemprop="headline">{escape(actual_headline)}</h1>'
    source_markup = "" if source_missing else f'<meta itemprop="url" content="{escape(source, quote=True)}">'
    article_type = "NewsArticle" if news_article else "Article"
    true_article = (
        f'<article itemscope="" itemtype="https://schema.org/{article_type}">'
        + headline_markup + source_markup + body_markup + "</article>"
    )
    legacy = ""
    if layout != "normal":
        legacy = (
            '<aside><div id="content" class="recommendations">'
            f"Nonempty recommended topic {salt}-{index}; not the article body."
            "</div></aside>"
        )
    if headline_mismatch or source_mismatch:
        # The correct report exists publicly but no shared selector matches it;
        # a cached rule must not silently extract the foreign report.
        true_article += (
            '<article itemscope="" itemtype="https://schema.org/Article">'
            f'<h1 itemprop="headline">{escape(title)}</h1><meta itemprop="url" content="{escape(url, quote=True)}">'
            f'<div class="correct-but-unlisted" itemprop="articleBody">{escape(body)}</div></article>'
        )
    navigation = '<header><nav class="new-navigation"><a>Changed unrelated navigation</a><span>New promotion</span></nav></header>' if nav_edit else "<header>News navigation</header>"
    markup = f"<html><body>{navigation}<main>{legacy}{true_article}</main></body></html>"
    if unavailable:
        markup = "<html><body>503 temporary service unavailable</body></html>"
    return {
        "url": url, "list_title": title, "markup": markup,
        "status": 503 if unavailable else 200, "mode": "http_html", "complete": True,
    }, body


def _round(round_id, pages, expected, rules=None, contract=None):
    return {
        "round_id": round_id,
        "rules": deepcopy(RULES if rules is None else rules),
        "contract": deepcopy(CONTRACT if contract is None else contract),
        "pages": deepcopy(pages),
        "scoring_expected_text": list(expected),
    }


def _scenario(name, split, description, salt, changes=None, *, budget_note="", shuffle=None,
              rules=None, contract=None, rounds=None, cold_start=False, origin="https://fixture.invalid"):
    history = [make_page(index, salt, origin=origin) for index in range(10)]
    changed = [make_page(index, salt, origin=origin, **(changes or {}).get(index, {})) for index in range(10)]
    order = list(range(10)) if shuffle is None else list(shuffle)
    historical = _round("historical_certification", [history[index][0] for index in order], [history[index][1] for index in order])
    first = _round("changed", [changed[index][0] for index in order], [changed[index][1] for index in order], rules, contract)
    current_rounds = [first, {**deepcopy(first), "round_id": "repeat_same_task"}] if rounds is None else rounds(history, changed, order)
    return {
        "name": name, "split": split, "description": description,
        "budget_note": budget_note, "cold_start": cold_start,
        "history": historical, "rounds": current_rounds,
    }


def build_suite(seed=SEED):
    """Freeze this payload before evaluating or tuning the policy implementation.

    Holdout cases are generated by a separate fixed-seed branch with new URL,
    text, permutation, and structural combinations. This is an engineering
    holdout, not a third-party or researcher-blind benchmark.
    """
    dev = []
    dev.append(_scenario("dev_unrelated_dom_edit", "development", "Only unrelated navigation changes.", "d01", {index: {"nav_edit": True} for index in range(10)}))
    dev.append(_scenario("dev_single_template_drift", "development", "All pages use #article; candidates were shared before evaluation.", "d02", {index: {"layout": "article"} for index in range(10)}))
    dev.append(_scenario("dev_same_route_tail_8_plus_2", "development", "Eight normal pages and two tail variants; old selector returns nonempty recommendations.", "d03", {8: {"layout": "article"}, 9: {"layout": "article"}}))
    dev.append(_scenario("dev_head_variant_overgeneralization", "development", "Two prefix variants followed by eight normal pages.", "d04", {0: {"layout": "article"}, 1: {"layout": "article"}}))

    def failure_recovery(history, changed, order):
        first = _round("temporary_503", [changed[index][0] for index in order], [changed[index][1] for index in order])
        restored = _round("recovered_original_html", [history[index][0] for index in order], [history[index][1] for index in order])
        return [first, restored, {**deepcopy(restored), "round_id": "recovery_repeat"}]

    dev.append(_scenario("dev_temporary_failure_recovery", "development", "A temporary 503 is followed by original healthy content; valid memory should survive.", "d05", {0: {"unavailable": True}}, rounds=failure_recovery))
    dev.append(_scenario("dev_two_new_families_low_budget", "development", "Distinct #article and .story-body variants share a route.", "d06", {8: {"layout": "article"}, 9: {"layout": "story"}}, budget_note="B=0/1 exposes limited audit budget."))
    dev.append(_scenario("dev_unseen_headline_counterexample", "development", "Variant observations must preserve the headline relation; one page contains a foreign report.", "d07", {8: {"layout": "article"}, 9: {"layout": "article", "headline_mismatch": True}}, budget_note="B=1 has a held-out counterexample."))
    dev.append(_scenario("dev_observed_counterexample", "development", "The same conflict can be audited at larger budget; no unrelated rule should be invalidated.", "d08", {8: {"layout": "article"}, 9: {"layout": "article", "headline_mismatch": True}}, budget_note="B=2/4/10 can see more counterevidence."))
    dev.append(_scenario("dev_unknown_repair", "development", "The correct body uses a selector absent from the fixed candidate library.", "d09", {8: {"layout": "unseen"}, 9: {"layout": "unseen"}}))

    rng = random.Random(seed ^ 0x5A17C3)
    holdout = []

    def add(name, description, changes, **kwargs):
        salt = f"h{rng.randrange(100000, 999999)}"
        order = list(range(10))
        rng.shuffle(order)
        holdout.append(_scenario(name, "engineering_holdout", description, salt, changes, shuffle=order, origin="https://holdout.invalid", **kwargs))

    positions = rng.sample(range(10), 2)
    add("holdout_shuffled_same_route_variants", "A separately shuffled 8+2 template mixture, with Article/NewsArticle variants.", {positions[0]: {"layout": "article", "news_article": True}, positions[1]: {"layout": "story"}})
    add("holdout_title_origin_mismatch", "A nonempty selected body belongs to a different headline; correct text is outside the candidate library.", {rng.randrange(10): {"headline_mismatch": True}})
    add("holdout_source_url_mismatch", "Headline matches but public Article URL points to another source; no hidden marker is used.", {rng.randrange(10): {"source_mismatch": True}})
    add("holdout_multiple_candidate_bodies", "A candidate matches two nonempty bodies on the same page.", {rng.randrange(10): {"duplicate_body": True}})
    add("holdout_body_role_missing", "Nonempty correct-looking text lacks the mandatory public articleBody role; contract state should remain PENDING.", {rng.randrange(10): {"role_missing": True}})
    add("holdout_source_markup_missing", "The public Article source URL is absent, so origin evidence is incomplete.", {rng.randrange(10): {"source_missing": True}})
    add("holdout_headline_markup_missing", "The public Article headline is absent rather than explicitly mismatched.", {rng.randrange(10): {"headline_missing": True}})

    def selector_versions(history, changed, order):
        changed_rules = deepcopy(RULES)
        changed_rules[0].update(selector="#legacy-content", version="2")
        new_pages = []
        expected = []
        for index in order:
            old_page = history[index][0]
            page = deepcopy(old_page)
            page["markup"] = page["markup"].replace('id="content"', 'id="legacy-content"')
            new_pages.append(page)
            expected.append(history[index][1])
        revised = _round("selector_and_version_changed", new_pages, expected, changed_rules)
        version_only_rules = deepcopy(changed_rules)
        version_only_rules[0]["version"] = "3"
        return [revised, {**deepcopy(revised), "round_id": "selector_repeat"}, _round("selector_version_only_changed", new_pages, expected, version_only_rules)]

    add("holdout_selector_version_identity", "Selector content and then selector version change; old certificates must not cross identities.", {}, rounds=selector_versions)

    def contract_versions(history, changed, order):
        pages = [history[index][0] for index in order]
        expected = [history[index][1] for index in order]
        revised = deepcopy(CONTRACT)
        revised["version"] = "2"
        first = _round("contract_version_changed", pages, expected, contract=revised)
        return [first, {**deepcopy(first), "round_id": "contract_repeat"}]

    add("holdout_contract_version_identity", "Same HTML, different declared contract version; cache identity must change.", {}, rounds=contract_versions)
    add("holdout_503_then_recovery", "A shuffled task contains a 503 then recovers to its certified original HTML.", {rng.randrange(10): {"unavailable": True}}, rounds=failure_recovery)
    multiple = rng.sample(range(10), 4)
    add("holdout_multifamily_budget_pressure", "Four changed pages across two selector families under B=0/1/2/4/10.", {index: {"layout": "article" if offset % 2 == 0 else "story"} for offset, index in enumerate(multiple)})
    add("holdout_unrelated_edits_all_pages", "All snapshots change outside selector/contract evidence; projection caches should preserve applicability.", {index: {"nav_edit": True} for index in range(10)})
    add("holdout_unknown_repair_mixed_role", "An unseen repair selector and missing role occur together; no policy may access scoring text to invent a selector.", {rng.randrange(10): {"layout": "unseen", "role_missing": True}})
    add("holdout_contract_compliant_wrong_body", "Boundary: valid Article/headline/source/body-role metadata accompanies semantically wrong content. Contract compliance must not be reported as true-body correctness.", {rng.randrange(10): {"semantic_body_wrong": True}})
    add("holdout_cold_start", "No historical receipts are inserted. B=0 cannot certify applicability even for otherwise normal markup.", {}, cold_start=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "candidate_library": deepcopy(RULES),
        "contract": deepcopy(CONTRACT),
        "generation_boundary": "Deterministic development fixtures plus separately generated engineering holdout; not third-party blind testing.",
        "scenarios": dev + holdout,
    }


def freeze_payload(seed=SEED):
    suite = build_suite(seed)
    return {"payload_sha256": payload_hash(suite), "payload": suite}
