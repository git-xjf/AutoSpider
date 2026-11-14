"""Collect primary-source HTML and verified metadata for a patent study.

No production database or crawling configuration is accessed.  Existing source
snapshots are reused; use a new output directory for a new retrieval date.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def metadata(soup: BeautifulSoup, name: str) -> list[str]:
    return [tag.get("content", "") for tag in soup.select(f'meta[name="{name}"]')]


def fetch(client: httpx.Client, url: str, path: Path, delay: float) -> dict:
    if path.exists():
        content = path.read_bytes()
        return {"url": url, "path": path.name, "status": "cached", "sha256": sha256(content), "bytes": len(content)}
    try:
        response = client.get(url)
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            return {"url": url, "status": "not_html", "content_type": response.headers.get("content-type")}
        content = response.content
        if len(content) > 10_000_000:
            return {"url": url, "status": "too_large", "bytes": len(content)}
        path.write_bytes(content)
        return {"url": str(response.url), "path": path.name, "status": "downloaded", "sha256": sha256(content), "bytes": len(content)}
    except (httpx.HTTPError, OSError) as exc:
        return {"url": url, "status": "fetch_failed", "error": str(exc)[:250]}
    finally:
        time.sleep(delay)


def paper_record(client, seed, folder, delay):
    identifier = seed["arxiv_id"]
    result = {**seed, "kind": "paper", "primary_url": f"https://arxiv.org/abs/{identifier}"}
    abstract = fetch(client, result["primary_url"], folder / f"{seed['id']}_abstract.html", delay)
    result["abstract_snapshot"] = abstract
    if "path" not in abstract:
        result["verification"] = "manual_needed"
        return result
    soup = BeautifulSoup((folder / abstract["path"]).read_bytes(), "html.parser")
    titles = metadata(soup, "citation_title")
    arxiv_ids = metadata(soup, "citation_arxiv_id")
    history = soup.select_one(".submission-history")
    history_text = history.get_text(" ", strip=True) if history else ""
    first_submitted = (metadata(soup, "citation_date") or [None])[0]
    if first_submitted is None:
        first_version = re.search(r"\[v1\]\s*([A-Za-z]{3},\s*\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", history_text)
        if first_version:
            first_submitted = datetime.strptime(first_version.group(1), "%a, %d %b %Y").strftime("%Y/%m/%d")
    result.update(
        title=titles[0] if titles else "",
        authors=metadata(soup, "citation_author"),
        first_submitted=first_submitted,
        metadata_online_date=(metadata(soup, "citation_online_date") or [None])[0],
        journal_metadata=metadata(soup, "citation_journal_title"),
        original_publication_date_metadata=metadata(soup, "citation_publication_date"),
        original_publication_doi_metadata=metadata(soup, "citation_doi"),
        doi=f"10.48550/arXiv.{identifier}",
        publication_type="arXiv repository record; peer-review status not inferred",
        verification="verified_primary_metadata" if titles and identifier in arxiv_ids else "manual_needed",
    )
    result["submission_history"] = history_text or None
    html_link = soup.select_one('a[href*="/html/"]')
    # Use the exact version linked by the repository, not an inferred v1.
    if html_link:
        full_url = str(html_link["href"])
        if full_url.startswith("/"):
            full_url = "https://arxiv.org" + full_url
        result["fulltext_snapshot"] = fetch(client, full_url, folder / f"{seed['id']}_fulltext.html", delay)
        if "path" in result["fulltext_snapshot"]:
            full = BeautifulSoup((folder / result["fulltext_snapshot"]["path"]).read_bytes(), "html.parser")
            result["fulltext_title"] = full.title.get_text(" ", strip=True) if full.title else None
    else:
        result["fulltext_snapshot"] = {"status": "html_not_available", "note": "Repository abstract verified; full-text analysis not asserted."}
    if seed.get("crossref_doi"):
        crossref_url = f"https://api.crossref.org/works/{seed['crossref_doi']}"
        snapshot = folder / f"{seed['id']}_crossref.json"
        try:
            if not snapshot.exists():
                response = client.get(crossref_url)
                response.raise_for_status()
                snapshot.write_bytes(response.content)
            crossref = read_json(snapshot)["message"]
            crossref_title = crossref["title"][0]
            same_title = re.sub(r"\W", "", crossref_title).lower() == re.sub(r"\W", "", result["title"]).lower()
            result["crossref_crosscheck"] = {
                "url": crossref_url, "path": snapshot.name,
                "sha256": sha256(snapshot.read_bytes()),
                "verification": "verified_title_and_doi" if same_title and crossref["DOI"].lower() == seed["crossref_doi"].lower() else "mismatch",
                "title": crossref_title,
                "authors": [f"{author.get('family', '')}, {author.get('given', '')}" for author in crossref.get("author", [])],
                "year": crossref["published"]["date-parts"][0][0],
                "journal": (crossref.get("container-title") or [""])[0],
                "volume": crossref.get("volume"), "pages": crossref.get("page"),
                "doi": crossref["DOI"], "source": "Crossref publisher-deposited metadata",
            }
        except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
            result["crossref_crosscheck"] = {"url": crossref_url, "verification": "manual_needed", "error": str(exc)[:200]}
    return result


def escape_bib(value: str) -> str:
    return re.sub(r"([&%_#])", r"\\\1", value.replace("{", "").replace("}", ""))


def export_bib(records: list[dict]) -> str:
    entries = []
    for record in records:
        if record.get("verification") != "verified_primary_metadata":
            continue
        journal_record = record.get("crossref_crosscheck", {})
        if journal_record.get("verification") == "verified_title_and_doi":
            entries.append(
                f"@article{{{record['id'].lower()}_jair1145,\n"
                f"  title = {{{escape_bib(journal_record['title'])}}},\n"
                f"  author = {{{escape_bib(' and '.join(journal_record['authors']))}}},\n"
                f"  journal = {{{escape_bib(journal_record['journal'])}}},\n"
                f"  year = {{{journal_record['year']}}},\n"
                f"  doi = {{{journal_record['doi']}}},\n"
                f"  url = {{https://doi.org/{journal_record['doi']}}}\n}}"
            )
            continue
        author = " and ".join(record["authors"])
        year = str(record.get("first_submitted") or "")[:4]
        entries.append(
            f"@misc{{{record['id'].lower()}_{record['arxiv_id'].replace('.', '')},\n"
            f"  title = {{{escape_bib(record['title'])}}},\n"
            f"  author = {{{escape_bib(author)}}},\n"
            f"  year = {{{year}}},\n"
            f"  eprint = {{{record['arxiv_id']}}},\n"
            "  archivePrefix = {arXiv},\n"
            f"  doi = {{{record['doi']}}},\n"
            f"  url = {{{record['primary_url']}}},\n"
            "  note = {Repository submission date; not a claim of first journal publication}\n}"
        )
    return "\n\n".join(entries) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--patents", type=Path)
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    paper_folder = args.out / "papers"
    paper_folder.mkdir(exist_ok=True)
    papers = []
    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "AutoSpider-Patent-Research/0.1"}) as client:
        for seed in read_json(args.papers):
            record = paper_record(client, seed, paper_folder, args.delay)
            papers.append(record)
            print(json.dumps({"id": record["id"], "verification": record["verification"], "fulltext": record.get("fulltext_snapshot", {}).get("status")}), flush=True)
        patents = []
        if args.patents:
            payload = read_json(args.patents)
            seeds = payload if isinstance(payload, list) else payload.get("sources", payload.get("patents", []))
            patent_folder = args.out / "patents"
            patent_folder.mkdir(exist_ok=True)
            for seed in seeds:
                number = seed.get("publication_number") or seed.get("number") or seed.get("patent_number")
                url = seed.get("primary_url") or seed.get("url") or seed.get("source_url")
                if not number or not url:
                    patents.append({**seed, "snapshot": {"status": "manifest_fields_missing"}})
                    continue
                snapshot = fetch(client, url, patent_folder / f"{number}.html", args.delay)
                patents.append({**seed, "snapshot": snapshot})
                print(json.dumps({"patent": number, "snapshot": snapshot["status"]}), flush=True)
    result = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "papers": papers, "patents": patents}
    (args.out / "collection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "references.bib").write_text(export_bib(papers), encoding="utf-8")
    print(json.dumps({"papers": len(papers), "metadata_verified": sum(p.get("verification") == "verified_primary_metadata" for p in papers), "patents": len(patents)}))


if __name__ == "__main__":
    main()
