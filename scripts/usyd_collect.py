from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UNIVERSITY = "University of Sydney"

ACCOUNTING_EXPERTS_URL = "https://profiles.sydney.edu.au/groups/652444/experts"
FINANCE_EXPERTS_URL = "https://profiles.sydney.edu.au/groups/652446/experts"

USER_AGENT = "CITS3200-Group20-USyd/0.3 (student project; conservative requests)"
ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-[\dX]{4}\b", re.I)

PUBLICATION_COLUMNS = [
    # Keep the common fields used by current main-branch university CSVs first.
    "espace_id",
    "name",
    "journal_name",
    "title",
    "year",
    "author_count",
    "authors",
    "quality_rank",
    "sjr_quartile",
    "doi",
    "article_url",
    "link",
    "source",
    "citation_percentile",
    "cited_by_count",
    "fwci",
    "university",
    "field_of_research",
    "researcher_id",
    "publication_id",
    "issn",
    "eissn",
    "item_type",
    "publication_status",
    "abdc_match_method",
    "abdc_match_status",
    "harvested_at",
    # Client-requested stable researcher identifier / useful OpenAlex provenance.
    "orcid",
    "openalex_author_id",
]

STAFF_COLUMNS = [
    "researcher_id",
    "name",
    "job_title",
    "academic_level",
    "university",
    "field_of_research",
    "orcid",
    "openalex_author_id",
    "profile_url",
]

TITLE_PATTERNS = [
    re.compile(r"^Associate Professor(?:\s+.*)?$", re.I),
    re.compile(r"^Fractional Professor(?:\s+.*)?$", re.I),
    re.compile(r"^Senior Lecturer(?:\s+.*)?$", re.I),
    re.compile(r"^Associate Lecturer(?:\s+.*)?$", re.I),
    re.compile(r"^Professor(?:\s+.*)?$", re.I),
    re.compile(r"^Lecturer(?:\s+.*)?$", re.I),
    re.compile(r"^Senior Research Fellow(?:\s+.*)?$", re.I),
    re.compile(r"^Senior Fellow(?:\s+.*)?$", re.I),
    re.compile(r"^Senior Horizon Fellow(?:\s+.*)?$", re.I),
]


def clean_space(value: str | None) -> str:
    return " ".join((value or "").split())


def normalise_orcid(value: str | None) -> str:
    if not value:
        return ""
    m = ORCID_RE.search(value)
    return m.group(0).upper() if m else ""


def normalise_doi(value: str | None) -> str:
    value = clean_space(value)
    if not value:
        return ""
    low = value.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if low.startswith(prefix):
            value = value[len(prefix):]
            break
    return value.strip().rstrip(".,;:)").lower()


def academic_level_from_job_title(job_title: str | None) -> str:
    """Client-confirmed standard mapping. Non-standard research fellow titles
    are deliberately left blank for manual/team review.
    """
    title = clean_space(job_title).casefold()
    if "associate lecturer" in title:
        return "A"
    if "senior lecturer" in title:
        return "C"
    if "lecturer" in title:
        return "B"
    if "associate professor" in title:
        return "D"
    if "professor" in title:
        return "E"
    return ""


def stable_researcher_id(profile_url: str, field: str) -> str:
    slug = urlparse(profile_url).path.strip("/").replace("/", "-") or "unknown"
    prefix = "accounting" if field.casefold().startswith("account") else "finance"
    return f"usyd-{prefix}-{slug}"


def stable_publication_id(work: dict) -> str:
    doi = normalise_doi(work.get("doi"))
    if doi:
        return "doi-" + hashlib.sha1(doi.encode("utf-8")).hexdigest()[:20]
    oa_id = clean_space(work.get("id")).rsplit("/", 1)[-1]
    if oa_id:
        return oa_id.lower()
    title = clean_space(work.get("title") or work.get("display_name")).casefold()
    year = str(work.get("publication_year") or "")
    return "fallback-" + hashlib.sha1(f"{title}|{year}".encode()).hexdigest()[:20]


def fetch_html(url: str, *, timeout: int = 30) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r.text


def extract_profile_urls_from_rendered_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = "https://profiles.sydney.edu.au" + href
        p = urlparse(href)
        if p.netloc.casefold() != "profiles.sydney.edu.au":
            continue
        path = p.path.strip("/")
        if not path or "/" in path:
            continue
        if path.casefold() in {"search", "about"}:
            continue
        if href not in seen:
            seen.add(href)
            out.append(href)
    return out


def collect_group_profile_urls(group_url: str) -> list[str]:
    """Sydney Profiles group pages are JS-driven. Use Playwright to render them."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(group_url, wait_until="networkidle", timeout=90_000)

        # Scroll a few times in case entries lazy-load.
        previous = -1
        stable = 0
        for _ in range(20):
            count = page.locator("a").count()
            stable = stable + 1 if count == previous else 0
            if stable >= 3:
                break
            previous = count
            page.mouse.wheel(0, 12000)
            page.wait_for_timeout(500)

        html = page.content()
        browser.close()

    urls = extract_profile_urls_from_rendered_html(html)
    if not urls:
        raise RuntimeError(f"No Sydney person profiles found at {group_url}")
    return urls


def extract_profile_metadata(html: str, profile_url: str, field: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = clean_space(h1.get_text(" ", strip=True) if h1 else "")
    name = re.sub(r"\s+Profile page$", "", name, flags=re.I)

    text = soup.get_text("\n", strip=True)
    orcid = normalise_orcid(text)

    # Limit title search to the early header region to avoid matching titles in the bio.
    lines = [clean_space(x) for x in text.splitlines() if clean_space(x)]
    header = []
    for line in lines[:100]:
        if line.casefold() in {"bio", "school", "certifications", "postgraduate training"}:
            break
        header.append(line)

    job_title = ""
    for line in header:
        for pat in TITLE_PATTERNS:
            if pat.match(line):
                job_title = line
                break
        if job_title:
            break

    researcher_id = stable_researcher_id(profile_url, field)
    return {
        "researcher_id": researcher_id,
        "name": name,
        "job_title": job_title,
        "academic_level": academic_level_from_job_title(job_title),
        "university": UNIVERSITY,
        "field_of_research": field,
        "orcid": orcid,
        "openalex_author_id": "",
        "profile_url": profile_url,
    }


def openalex_author_by_orcid(orcid: str, api_key: str) -> dict | None:
    if not orcid:
        return None
    url = f"https://api.openalex.org/authors/https://orcid.org/{orcid}"
    r = requests.get(
        url,
        params={"api_key": api_key},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def openalex_works_by_author(author_id: str, api_key: str) -> list[dict]:
    """Use cursor pagination and the author ID filter recommended by OpenAlex."""
    short_id = clean_space(author_id).rsplit("/", 1)[-1]
    cursor = "*"
    works = []

    while cursor:
        r = requests.get(
            "https://api.openalex.org/works",
            params={
                "api_key": api_key,
                "filter": f"authorships.author.id:{short_id},type:article",
                "per_page": 100,
                "cursor": cursor,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=45,
        )
        r.raise_for_status()
        data = r.json()
        works.extend(data.get("results") or [])
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not (data.get("results") or []):
            break
        time.sleep(0.05)

    return works


def is_journal_article(work: dict) -> bool:
    if work.get("type") != "article":
        return False
    source = ((work.get("primary_location") or {}).get("source") or {})
    return source.get("type") == "journal"


def work_to_publication(work: dict, researcher: dict[str, str]) -> dict[str, object]:
    loc = work.get("primary_location") or {}
    src = loc.get("source") or {}
    authorships = work.get("authorships") or []

    authors = [
        clean_space((a.get("author") or {}).get("display_name"))
        for a in authorships
        if clean_space((a.get("author") or {}).get("display_name"))
    ]
    doi = normalise_doi(work.get("doi"))
    issns = src.get("issn") or []
    issn_l = clean_space(src.get("issn_l"))

    # Keep the first ISSN in `issn`; an ISSN-L is often the best canonical
    # join identifier but the team can later split/rename this consistently.
    issn = issn_l or (issns[0] if issns else "")
    eissn = ""
    if len(issns) > 1:
        eissn = issns[1]

    percentile = work.get("citation_normalized_percentile") or {}
    percentile_value = percentile.get("value", "") if isinstance(percentile, dict) else ""

    landing = clean_space(loc.get("landing_page_url"))
    article_url = f"https://doi.org/{doi}" if doi else landing

    return {
        "espace_id": researcher["researcher_id"],  # legacy/common join column in current CSVs
        "name": researcher["name"],
        "journal_name": clean_space(src.get("display_name")),
        "title": clean_space(work.get("title") or work.get("display_name")),
        "year": work.get("publication_year") or "",
        "author_count": work.get("authors_count") or len(authors),
        "authors": "; ".join(authors),
        "quality_rank": "",
        "sjr_quartile": "",
        "doi": doi,
        "article_url": article_url,
        "link": clean_space(work.get("id")),
        "source": "OpenAlex (via USyd ORCID)",
        "citation_percentile": percentile_value,
        "cited_by_count": work.get("cited_by_count", ""),
        "fwci": work.get("fwci", ""),
        "university": UNIVERSITY,
        "field_of_research": researcher["field_of_research"],
        "researcher_id": researcher["researcher_id"],
        "publication_id": stable_publication_id(work),
        "issn": issn,
        "eissn": eissn,
        "item_type": "Journal Article",
        "publication_status": "Published",
        "abdc_match_method": "",
        "abdc_match_status": "",
        "harvested_at": datetime.now(timezone.utc).isoformat(),
        "orcid": researcher["orcid"],
        "openalex_author_id": researcher["openalex_author_id"],
    }


def dedupe_publications(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen = set()
    out = []
    for row in rows:
        doi = normalise_doi(str(row.get("doi") or ""))
        if doi:
            key = ("doi", doi, row.get("researcher_id"))
        else:
            key = (
                "fallback",
                clean_space(str(row.get("title") or "")).casefold(),
                str(row.get("year") or ""),
                row.get("researcher_id"),
            )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def write_csv(path: Path, rows: Iterable[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def collect_all(out_dir: Path, api_key: str) -> tuple[list[dict], list[dict]]:
    staff: list[dict] = []
    for field, group_url in [
        ("Accounting", ACCOUNTING_EXPERTS_URL),
        ("Finance", FINANCE_EXPERTS_URL),
    ]:
        urls = collect_group_profile_urls(group_url)
        for n, profile_url in enumerate(urls, 1):
            html = fetch_html(profile_url)
            person = extract_profile_metadata(html, profile_url, field)
            if person["orcid"]:
                oa = openalex_author_by_orcid(person["orcid"], api_key)
                if oa:
                    person["openalex_author_id"] = clean_space(oa.get("id"))
            staff.append(person)
            print(f"{field} {n}/{len(urls)}: {person['name']} | {person['orcid'] or 'no ORCID'}")
            time.sleep(0.15)

    publications: list[dict] = []
    for n, person in enumerate(staff, 1):
        if not person["openalex_author_id"]:
            print(f"SKIP OpenAlex works (no matched author): {person['name']}")
            continue
        works = openalex_works_by_author(person["openalex_author_id"], api_key)
        journal_works = [w for w in works if is_journal_article(w)]
        publications.extend(work_to_publication(w, person) for w in journal_works)
        print(f"Works {n}/{len(staff)}: {person['name']} -> {len(journal_works)} journal articles")
        time.sleep(0.05)

    publications = dedupe_publications(publications)

    write_csv(out_dir / "usyd_staff.csv", staff, STAFF_COLUMNS)
    write_csv(out_dir / "usyd_publications.csv", publications, PUBLICATION_COLUMNS)
    return staff, publications


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="output")
    args = p.parse_args()

    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "OPENALEX_API_KEY is not set. Create a free key at "
            "https://openalex.org/settings/api and add it as a Codespaces secret "
            "or shell environment variable. Never commit it."
        )

    out = Path(args.out_dir)
    staff, pubs = collect_all(out, api_key)
    print(f"\nWrote {len(staff)} staff rows")
    print(f"Wrote {len(pubs)} publication rows")
    print(f"Files: {out/'usyd_staff.csv'} and {out/'usyd_publications.csv'}")


if __name__ == "__main__":
    main()
