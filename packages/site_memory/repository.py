from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from .similarity import fingerprint_similarity


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_success_at TEXT,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    url_pattern TEXT NOT NULL,
    page_type TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    structure_hash TEXT,
    page_model TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(site_id, url_pattern, page_type, fingerprint)
);
CREATE TABLE IF NOT EXISTS selectors (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    selector_type TEXT NOT NULL,
    selector TEXT NOT NULL,
    fallback_selector TEXT,
    page_scope TEXT NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    UNIQUE(page_id, field_name, selector)
);
CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    crawl_spec_hash TEXT NOT NULL,
    crawl_plan TEXT NOT NULL,
    easyspider_task_id INTEGER,
    version TEXT NOT NULL,
    success_rate REAL NOT NULL DEFAULT 0,
    last_success_at TEXT,
    UNIQUE(site_id, name, crawl_spec_hash)
);
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
    external_execution_id INTEGER,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    validation_score REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
    failure_type TEXT NOT NULL,
    old_selector TEXT,
    new_selector TEXT,
    diagnosis TEXT,
    repair_success INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SiteMemoryRepository:
    def __init__(self, path: str | Path = "data/site_memory.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def lookup(self, domain: str, *, page_type: str | None = None, fingerprint: str | None = None) -> list[dict[str, Any]]:
        domain = domain.lower().rstrip(".")
        query = """
            SELECT p.*, s.domain FROM pages p JOIN sites s ON s.id = p.site_id
            WHERE s.domain = ?
        """
        values: list[Any] = [domain]
        if page_type:
            query += " AND p.page_type = ?"
            values.append(page_type)
        query += " ORDER BY p.last_seen DESC"
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
            match_kind = "exact_page_type" if page_type else "domain"
            if page_type and not rows:
                rows = connection.execute(
                    """SELECT p.*, s.domain FROM pages p JOIN sites s ON s.id = p.site_id
                       WHERE s.domain = ? ORDER BY p.last_seen DESC""",
                    (domain,),
                ).fetchall()
                match_kind = "domain_fallback"
            results = []
            for row in rows:
                item = dict(row)
                item["match_kind"] = match_kind
                item["page_model"] = json.loads(item["page_model"])
                item["fingerprint_similarity"] = fingerprint_similarity(fingerprint, item["fingerprint"])
                selectors = connection.execute(
                    "SELECT * FROM selectors WHERE page_id = ? ORDER BY confidence DESC", (item["id"],)
                ).fetchall()
                item["selectors"] = [dict(selector) for selector in selectors]
                results.append(item)
            return results

    def lookup_workflows(self, domain: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Return the best validated plans independently of page-type matching."""
        domain = domain.lower().rstrip(".")
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT w.id, w.name, w.crawl_spec_hash, w.crawl_plan,
                          w.easyspider_task_id, w.version, w.success_rate, w.last_success_at
                   FROM workflows w JOIN sites s ON s.id = w.site_id
                   WHERE s.domain = ?
                   ORDER BY w.success_rate DESC, w.last_success_at DESC LIMIT ?""",
                (domain, max(1, min(limit, 20))),
            ).fetchall()
        workflows = [dict(row) for row in rows]
        for workflow in workflows:
            workflow["crawl_plan"] = json.loads(workflow["crawl_plan"])
        return workflows

    def get_workflow(self, workflow_id: int) -> dict[str, Any]:
        """Return one full validated workflow after a compact lookup selected it."""
        if workflow_id < 1:
            raise KeyError(f"site-memory workflow does not exist: {workflow_id}")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT w.id, s.domain, w.name, w.crawl_spec_hash, w.crawl_plan,
                          w.easyspider_task_id, w.version, w.success_rate, w.last_success_at
                   FROM workflows w JOIN sites s ON s.id = w.site_id
                   WHERE w.id = ?""",
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"site-memory workflow does not exist: {workflow_id}")
        workflow = dict(row)
        workflow["crawl_plan"] = json.loads(workflow["crawl_plan"])
        return workflow

    def commit_validated(
        self,
        *,
        page_model: dict[str, Any],
        crawl_plan: dict[str, Any],
        crawl_spec_hash: str,
        workflow_name: str,
        validation_score: float,
        easyspider_task_id: int | None = None,
    ) -> int:
        if validation_score < 0.7:
            raise ValueError("site memory accepts only validated evidence with score >= 0.7")
        domain = (urlparse(page_model["url"]).hostname or "").lower()
        if not domain or not page_model.get("fingerprint"):
            raise ValueError("page_model requires a URL and fingerprint")
        now = _now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO sites(domain, created_at, updated_at, last_success_at, success_count)
                   VALUES(?, ?, ?, ?, 1)
                   ON CONFLICT(domain) DO UPDATE SET
                     updated_at=excluded.updated_at,
                     last_success_at=excluded.last_success_at,
                     success_count=sites.success_count+1""",
                (domain, now, now, now),
            )
            site_id = connection.execute("SELECT id FROM sites WHERE domain = ?", (domain,)).fetchone()[0]
            url_pattern = page_model.get("url_pattern") or page_model["url"]
            connection.execute(
                """INSERT INTO pages(site_id, url_pattern, page_type, fingerprint, structure_hash, page_model, last_seen)
                   VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(site_id, url_pattern, page_type, fingerprint) DO UPDATE SET
                     page_model=excluded.page_model, last_seen=excluded.last_seen""",
                (
                    site_id,
                    url_pattern,
                    page_model["page_type"],
                    page_model["fingerprint"],
                    page_model.get("structure_hash"),
                    json.dumps(page_model, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            page_id = connection.execute(
                "SELECT id FROM pages WHERE site_id=? AND url_pattern=? AND page_type=? AND fingerprint=?",
                (site_id, url_pattern, page_model["page_type"], page_model["fingerprint"]),
            ).fetchone()[0]
            for field_name, mapping in page_model.get("fields", {}).items():
                connection.execute(
                    """INSERT INTO selectors(page_id, field_name, selector_type, selector, fallback_selector,
                       page_scope, success_count, confidence, last_verified_at)
                       VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(page_id, field_name, selector) DO UPDATE SET
                         success_count=selectors.success_count+1,
                         confidence=excluded.confidence,
                         last_verified_at=excluded.last_verified_at""",
                    (
                        page_id,
                        field_name,
                        mapping.get("selector_type", "xpath"),
                        mapping["selector"],
                        json.dumps(mapping.get("fallbacks", []), ensure_ascii=False),
                        mapping["page_scope"],
                        mapping.get("confidence", validation_score),
                        now,
                    ),
                )
            connection.execute(
                """INSERT INTO workflows(site_id, name, crawl_spec_hash, crawl_plan, easyspider_task_id,
                   version, success_rate, last_success_at) VALUES(?, ?, ?, ?, ?, '0.1', ?, ?)
                   ON CONFLICT(site_id, name, crawl_spec_hash) DO UPDATE SET
                     crawl_plan=excluded.crawl_plan,
                     easyspider_task_id=excluded.easyspider_task_id,
                     success_rate=excluded.success_rate,
                     last_success_at=excluded.last_success_at""",
                (
                    site_id,
                    workflow_name,
                    crawl_spec_hash,
                    json.dumps(crawl_plan, ensure_ascii=False, sort_keys=True),
                    easyspider_task_id,
                    validation_score,
                    now,
                ),
            )
            return page_id

    def record_failure(
        self,
        domain: str,
        failure_type: str,
        diagnosis: str,
        *,
        old_selector: str | None = None,
        new_selector: str | None = None,
        repair_success: bool = False,
    ) -> None:
        now = _now()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sites(domain, created_at, updated_at) VALUES(?, ?, ?)",
                (domain.lower().rstrip("."), now, now),
            )
            site_id = connection.execute(
                "SELECT id FROM sites WHERE domain = ?", (domain.lower().rstrip("."),)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO failures(site_id, failure_type, old_selector, new_selector,
                   diagnosis, repair_success, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (site_id, failure_type, old_selector, new_selector, diagnosis, int(repair_success), now),
            )
