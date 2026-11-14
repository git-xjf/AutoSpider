"""Evidence-bound memory for a fixed, deterministic body-source contract.

The complete projection is complete ONLY for the contract implemented here.
Exact projection reuse is memoization, not a semantic-truth guarantee or a new
patent claim. All projection work is charged separately from audit invocations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from time import perf_counter
from typing import Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

from cssselect import SelectorError
from lxml import etree, html


PROJECTOR_VERSION = "body-source-relations/2"
VERIFIER_VERSION = "deterministic-body-contract/2"
POLICIES = (
    "always_reuse", "prefix_contract_checks", "structural_representatives",
    "exact_url_receipts", "complete_projection_fifo",
    "complete_projection_greedy", "counterexample_scoped",
)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normalized_text(value: str) -> str:
    return " ".join(value.split())


def _text(node) -> str:
    return normalized_text(" ".join(node.itertext()))


def _url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


@dataclass(frozen=True)
class Rule:
    rule_id: str
    selector: str
    version: str = "1"
    field_name: str = "content"

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class FieldContract:
    version: str = "1"
    require_source_url: bool = True
    require_body_role: bool = True

    @property
    def fingerprint(self) -> str:
        return digest({**asdict(self), "verifier_version": VERIFIER_VERSION})


@dataclass(frozen=True)
class Snapshot:
    url: str
    list_title: str
    markup: str
    status: int = 200
    mode: str = "http_html"
    complete: bool = True

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class FieldEvidence:
    url: str
    snapshot_hash: str
    rule_id: str
    rule_hash: str
    contract_hash: str
    scope_key: str
    facts: dict
    structural_key: str
    selector: str
    mode: str = "http_html"
    binding_hash: str = ""

    def __post_init__(self):
        if not self.binding_hash:
            object.__setattr__(self, "binding_hash", _evidence_binding(self))


def _evidence_binding(evidence: FieldEvidence) -> str:
    value = asdict(evidence)
    value.pop("binding_hash")
    return digest(value)


def _scope_key(url: str, mode: str, rule_hash: str, contract_hash: str, facts: dict) -> str:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return digest({"projector": PROJECTOR_VERSION, "origin": origin, "mode": mode,
                   "rule": rule_hash, "contract": contract_hash, "facts": facts})


def _validate_evidence(evidence: FieldEvidence):
    # This checks internal binding consistency, not authenticity of an external
    # caller's HTML/facts. Precomputed observations are a trusted research API.
    expected = _scope_key(evidence.url, evidence.mode, evidence.rule_hash,
                          evidence.contract_hash, evidence.facts)
    if expected != evidence.scope_key:
        raise ValueError("scope/facts binding mismatch")
    if evidence.binding_hash != _evidence_binding(evidence):
        raise ValueError("evidence identity binding mismatch")


@dataclass(frozen=True)
class PageObservation:
    url: str
    snapshot_hash: str
    transport: str
    evidence: dict[str, FieldEvidence]
    cost: dict


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str


ARTICLE_TYPES = {"https://schema.org/Article", "http://schema.org/Article",
                 "https://schema.org/NewsArticle", "http://schema.org/NewsArticle"}


def _owned_properties(owner, name: str):
    """Exclude properties belonging to nested, independent microdata items."""
    result = []
    for item in owner.cssselect(f'[itemprop~="{name}"]'):
        if item is owner or "itemscope" in item.attrib:
            continue
        for ancestor in item.iterancestors():
            if ancestor is owner:
                result.append(item)
                break
            if "itemscope" in ancestor.attrib:
                break
    return result


def observe_page(snapshot: Snapshot, rules: Sequence[Rule], contract: FieldContract) -> PageObservation:
    """Extract every input read by audit_evidence; expose no scoring labels.

    Relation comparisons (title equality, source-URL equality, body role) are
    computational work, NOT free metadata supplied by a semantic oracle.
    """
    started = perf_counter()
    if snapshot.status in {401, 403}:
        transport = "policy_blocked"
    elif snapshot.status in {408, 425, 429} or snapshot.status >= 500:
        transport = "temporary_unavailable"
    elif snapshot.status != 200 or not snapshot.complete:
        transport = "not_ready"
    else:
        transport = "ready"
    tree = None
    parses = 0
    if transport == "ready":
        parses = 1
        try:
            tree = html.fromstring(snapshot.markup)
        except (ValueError, etree.ParserError):
            transport = "parse_error"
    evidence = {}
    for rule in rules:
        facts = {
            "ready": transport == "ready", "selector_valid": True,
            "match_count": 0, "nonempty": False, "article_count": 0,
            "headline_relation": "missing", "source_relation": "missing",
            "body_role": False, "require_source_url": contract.require_source_url,
            "require_body_role": contract.require_body_role,
        }
        structural = {"match_count": 0, "tag": None, "ancestry": []}
        if tree is not None:
            try:
                nodes = tree.cssselect(rule.selector)
            except (ValueError, etree.Error, SelectorError):
                nodes = []
                facts["selector_valid"] = False
            facts["match_count"] = len(nodes)
            structural["match_count"] = len(nodes)
            if nodes:
                node = nodes[0]
                structural["tag"] = node.tag
                structural["ancestry"] = [item.tag for item in [node, *node.iterancestors()]]
                facts["nonempty"] = bool(_text(node))
                facts["body_role"] = "articleBody" in node.get("itemprop", "").split()
                owners = [item for item in [node, *node.iterancestors()]
                          if ARTICLE_TYPES.intersection(item.get("itemtype", "").split())]
                if len(owners) == 1:
                    # A body inside an independent Person/other item does not
                    # inherit the surrounding Article's provenance assertions.
                    for ancestor in [node, *node.iterancestors()]:
                        if ancestor is owners[0]:
                            break
                        if "itemscope" in ancestor.attrib:
                            owners = []
                            break
                facts["article_count"] = len(owners)
                if len(owners) == 1:
                    owner = owners[0]
                    headlines = _owned_properties(owner, "headline")
                    facts["headline_relation"] = (
                        "missing" if not headlines else "ambiguous" if len(headlines) != 1
                        else "missing" if not _text(headlines[0]) or not normalized_text(snapshot.list_title)
                        else "match" if _text(headlines[0]) == normalized_text(snapshot.list_title) else "mismatch"
                    )
                    source_nodes = _owned_properties(owner, "url")
                    sources = [item.get("href") or item.get("content") for item in source_nodes]
                    if not sources:
                        facts["source_relation"] = "missing"
                    elif len(sources) != 1:
                        facts["source_relation"] = "ambiguous"
                    elif not sources[0]:
                        facts["source_relation"] = "missing"
                    else:
                        facts["source_relation"] = "match" if _url(urljoin(snapshot.url, sources[0])) == _url(snapshot.url) else "mismatch"
        scope = _scope_key(snapshot.url, snapshot.mode, rule.fingerprint, contract.fingerprint, facts)
        evidence[rule.rule_id] = FieldEvidence(
            snapshot.url, snapshot.fingerprint, rule.rule_id, rule.fingerprint,
            contract.fingerprint, scope, facts, digest(structural), rule.selector, snapshot.mode,
        )
    return PageObservation(snapshot.url, snapshot.fingerprint, transport, evidence, {
        "html_parses": parses, "candidate_observations": len(rules) if tree is not None else 0,
        "snapshot_bytes": len(snapshot.markup.encode("utf-8")),
        "projection_seconds": perf_counter() - started,
    })


def audit_evidence(evidence: FieldEvidence) -> Verdict:
    """Fixed verifier: all dependencies are declared in observe_page facts."""
    _validate_evidence(evidence)
    facts = evidence.facts
    if not facts["ready"]:
        return Verdict("PENDING", "transport_not_ready")
    if not facts["selector_valid"]:
        return Verdict("INVALID", "invalid_selector")
    if facts["match_count"] == 0:
        return Verdict("INVALID", "no_match")
    if facts["match_count"] != 1:
        return Verdict("PENDING", "ambiguous_matches")
    if not facts["nonempty"]:
        return Verdict("INVALID", "empty_match")
    if facts["article_count"] != 1:
        return Verdict("PENDING", "unproven_article_owner")
    if facts["require_body_role"] and not facts["body_role"]:
        return Verdict("PENDING", "unproven_body_role")
    if facts["headline_relation"] == "mismatch":
        return Verdict("INVALID", "headline_origin_mismatch")
    if facts["headline_relation"] != "match":
        return Verdict("PENDING", "unproven_headline_origin")
    if facts["require_source_url"]:
        if facts["source_relation"] == "mismatch":
            return Verdict("INVALID", "source_url_mismatch")
        if facts["source_relation"] != "match":
            return Verdict("PENDING", "unproven_source_url")
    return Verdict("VALID", "fixed_source_contract_passed")


class MemoryStore:
    """Research-owned SQLite receipts; no production database is opened."""
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS receipts (
                receipt_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL,
                url TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
                rule_id TEXT NOT NULL, rule_hash TEXT NOT NULL,
                contract_hash TEXT NOT NULL, state TEXT NOT NULL,
                reason TEXT NOT NULL, facts TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS receipts_scope ON receipts(scope_key);
            CREATE TABLE IF NOT EXISTS quarantines (scope_key TEXT PRIMARY KEY, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transport (url TEXT PRIMARY KEY, state TEXT NOT NULL);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.connection.close()

    def quarantine(self, scope_key: str, reason: str):
        self.connection.execute("INSERT OR REPLACE INTO quarantines VALUES (?, ?)", (scope_key, reason))
        self.connection.commit()

    def mark_transport(self, observation: PageObservation):
        self.connection.execute("INSERT OR REPLACE INTO transport VALUES (?, ?)", (observation.url, observation.transport))
        self.connection.commit()

    def lookup(self, evidence: FieldEvidence, exact_url: bool = False) -> dict | None:
        _validate_evidence(evidence)
        quarantine = self.connection.execute("SELECT reason FROM quarantines WHERE scope_key=?", (evidence.scope_key,)).fetchone()
        if quarantine:
            return {"state": "PENDING", "reason": "quarantined:" + quarantine["reason"], "quarantined": True}
        query = "SELECT * FROM receipts WHERE scope_key=?"
        values = [evidence.scope_key]
        if exact_url:
            query += " AND url=? AND snapshot_hash=?"
            values.extend([evidence.url, evidence.snapshot_hash])
        query += " ORDER BY rowid DESC LIMIT 1"
        row = self.connection.execute(query, values).fetchone()
        return dict(row) if row else None

    def record(self, evidence: FieldEvidence, verdict: Verdict) -> dict:
        if verdict.state not in {"VALID", "INVALID", "PENDING"}:
            raise ValueError("invalid memory state")
        previous = self.lookup(evidence)
        if previous and previous["state"] in {"VALID", "INVALID"} and verdict.state in {"VALID", "INVALID"} and previous["state"] != verdict.state:
            self.quarantine(evidence.scope_key, "contradictory_receipts")
        identifier = digest({"scope": evidence.scope_key, "snapshot": evidence.snapshot_hash,
                             "url": evidence.url, "state": verdict.state, "reason": verdict.reason})
        self.connection.execute("INSERT OR IGNORE INTO receipts VALUES (?,?,?,?,?,?,?,?,?,?)", (
            identifier, evidence.scope_key, evidence.url, evidence.snapshot_hash,
            evidence.rule_id, evidence.rule_hash, evidence.contract_hash, verdict.state,
            verdict.reason, canonical(evidence.facts),
        ))
        self.connection.commit()
        return self.lookup(evidence) or {}

    def summary(self) -> dict:
        counts = dict(self.connection.execute("SELECT state,COUNT(*) FROM receipts GROUP BY state").fetchall())
        return {"receipts": sum(counts.values()), "states": counts,
                "quarantines": self.connection.execute("SELECT COUNT(*) FROM quarantines").fetchone()[0],
                "transport": dict(self.connection.execute("SELECT url,state FROM transport").fetchall())}


def evaluate_policy(
    policy: str, pages: Sequence[Snapshot], rules: Sequence[Rule], contract: FieldContract,
    store: MemoryStore, budget: int, observations: Sequence[PageObservation] | None = None,
) -> dict:
    if policy not in POLICIES:
        raise ValueError("unknown policy")
    if budget < 0 or not rules or len({rule.rule_id for rule in rules}) != len(rules):
        raise ValueError("invalid budget or rule library")
    started = perf_counter()
    observed = list(observations) if observations is not None else [observe_page(page, rules, contract) for page in pages]
    if len(observed) != len(pages):
        raise ValueError("observation count mismatch")
    for page, observation in zip(pages, observed):
        if page.fingerprint != observation.snapshot_hash:
            raise ValueError("snapshot binding mismatch")
        if page.url != observation.url:
            raise ValueError("observation URL binding mismatch")
        if set(observation.evidence) != {rule.rule_id for rule in rules}:
            raise ValueError("candidate library mismatch")
        for rule in rules:
            evidence = observation.evidence[rule.rule_id]
            if evidence.rule_hash != rule.fingerprint or evidence.contract_hash != contract.fingerprint:
                raise ValueError("rule/contract binding mismatch")
            if (evidence.snapshot_hash != observation.snapshot_hash or evidence.url != page.url
                    or evidence.rule_id != rule.rule_id or evidence.selector != rule.selector
                    or evidence.mode != page.mode):
                raise ValueError("evidence identity binding mismatch")
            _validate_evidence(evidence)
        store.mark_transport(observation)
    exact = policy == "exact_url_receipts"
    audited = []
    audit_seconds = 0.0
    cache_hits = 0

    def cached(index, rule):
        return store.lookup(observed[index].evidence[rule.rule_id], exact_url=exact)

    def audit(index):
        nonlocal audit_seconds
        if index in audited or observed[index].transport != "ready":
            return
        if len(audited) >= budget:
            raise RuntimeError("audit budget exhausted")
        audit_started = perf_counter()
        for rule in rules:
            evidence = observed[index].evidence[rule.rule_id]
            store.record(evidence, audit_evidence(evidence))
        audit_seconds += perf_counter() - audit_started
        audited.append(index)

    def settled(index):
        entries = [cached(index, rule) for rule in rules]
        return any(item and item["state"] == "VALID" for item in entries) or all(item is not None for item in entries)

    if policy in {"prefix_contract_checks", "structural_representatives"}:
        queue = [index for index, item in enumerate(observed) if item.transport == "ready"]
        if policy == "structural_representatives":
            seen = set()
            queue = [index for index in queue if not (
                tuple(item.structural_key for item in observed[index].evidence.values()) in seen
                or seen.add(tuple(item.structural_key for item in observed[index].evidence.values()))
            )]
        for index in queue[:budget]:
            audit(index)
    elif policy != "always_reuse":
        while len(audited) < budget:
            pending = [index for index, item in enumerate(observed)
                       if item.transport == "ready" and index not in audited and not settled(index)]
            if not pending:
                break
            if policy in {"complete_projection_greedy", "counterexample_scoped"}:
                # Weighted maximum uncovered evidence coverage; ties are stable.
                def coverage(index):
                    keys = {item.scope_key for item in observed[index].evidence.values()}
                    return sum(
                        sum(observed[other_index].evidence[rule.rule_id].scope_key in keys
                            and cached(other_index, rule) is None for rule in rules)
                        for other_index in pending
                    )
                target = min(pending, key=lambda index: (-coverage(index), index))
            else:
                target = pending[0]
            audit(target)

    decisions = []
    legacy = policy in {"always_reuse", "prefix_contract_checks", "structural_representatives"}
    legacy_rule = rules[0] if policy == "always_reuse" else None
    if legacy and audited:
        legacy_rule = next((rule for rule in rules if all(
            (store.lookup(observed[index].evidence[rule.rule_id]) or {}).get("state") == "VALID"
            for index in audited)), None)
    for index, item in enumerate(observed):
        base = {"url": item.url, "snapshot_hash": item.snapshot_hash, "transport": item.transport,
                "selector": None, "rule_id": None, "state": "PENDING", "reason": item.transport}
        if item.transport != "ready":
            decisions.append(base)
            continue
        if legacy:
            selected = legacy_rule
            if index in audited:
                selected = next((rule for rule in rules if (cached(index, rule) or {}).get("state") == "VALID"), None)
            if selected:
                base.update(selector=selected.selector, rule_id=selected.rule_id, state="VALID", reason="legacy_scope_assumption")
        else:
            for rule in rules:
                receipt = cached(index, rule)
                if receipt and receipt["state"] == "VALID":
                    base.update(selector=rule.selector, rule_id=rule.rule_id, state="VALID",
                                reason="bound_contract_receipt", scope_key=observed[index].evidence[rule.rule_id].scope_key,
                                receipt_id=receipt["receipt_id"])
                    cache_hits += int(index not in audited)
                    break
            if base["selector"] is None:
                receipts = [cached(index, rule) for rule in rules]
                if all(receipt and receipt["state"] == "INVALID" for receipt in receipts):
                    base.update(state="INVALID", reason="all_candidates_disproved_for_scope")
                else:
                    base.update(reason="insufficient_or_ambiguous_evidence")
        decisions.append(base)
    return {
        "policy": policy, "decisions": decisions, "audit_count": len(audited),
        "audited_indices": audited, "cache_hits": cache_hits,
        "audit_seconds": audit_seconds, "policy_seconds": perf_counter() - started,
        "projection_cost": {"html_parses": sum(item.cost["html_parses"] for item in observed),
                            "candidate_observations": sum(item.cost["candidate_observations"] for item in observed),
                            "snapshot_bytes": sum(item.cost["snapshot_bytes"] for item in observed),
                            "projection_seconds": sum(item.cost["projection_seconds"] for item in observed)},
        "memory_summary": store.summary(),
    }
