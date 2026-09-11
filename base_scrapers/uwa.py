"""UWA Accounting and Finance adapter.

The public entry point is :func:`collect`, which returns ``(staff, pubs)``
using the contract in the team's ``core.schema`` module.  The file is also
standalone and can write an auditable snapshot when run directly.

Data provenance
---------------
* Staff: official UWA Pure organisation/person pages.
* Publications: each current staff member's official Pure publications RSS
  feed, followed by the corresponding publication detail pages.

The personal feed is the attribution evidence: a publication is linked to a
researcher only when it appears in that researcher's official feed.  The
collector never guesses a researcher from a similar name.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_module
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup


UNIVERSITY = "University of Western Australia"
ROR = "03qn8fb07"
USER_AGENT = "CITS3200-Team20/3.0 (academic research; respectful automated collection)"
ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "Accounting": {
        "slug": "accounting",
        "org_url": "https://research-repository.uwa.edu.au/en/organisations/accounting/",
    },
    "Finance": {
        "slug": "finance-2",
        "org_url": "https://research-repository.uwa.edu.au/en/organisations/finance-2/",
    },
}

PREFIX_RE = re.compile(
    r"^(Associate Professor|Emeritus Professor|Professor|Dr|Mr|Mrs|Ms|Miss|A/Prof|Prof|Assoc\.? Prof\.?)\.?\s+",
    re.I,
)
LADDER = [
    ("Emeritus Professor", r"emeritus prof"),
    ("Associate Professor", r"associate prof|a/prof"),
    ("Associate Lecturer", r"associate lecturer"),
    ("Senior Lecturer", r"senior lecturer"),
    ("Senior Research Fellow", r"senior research fellow"),
    ("Research Fellow", r"research fellow"),
    ("Teaching Associate", r"teaching associate"),
    ("Professor", r"\bprofessor\b|chair in"),
    ("Lecturer", r"\blecturer\b"),
]
LEVELS = {
    "Associate Lecturer": "A",
    "Lecturer": "B",
    "Fellow": "B",
    "Research Fellow": "B",
    "Senior Lecturer": "C",
    "Senior Fellow": "C",
    "Senior Research Fellow": "C",
    "Associate Professor": "D",
    "Reader": "D",
    "Professor": "E",
    "Professorial Fellow": "E",
    "Professor Emeritus": "E",
    "Emeritus Professor": "E",
}
NON_RANK_PREFIXES = {"dr", "mr", "mrs", "ms", "miss"}

TYPE_PATTERNS = [
    ("Journal Article", r"contribution to journal|journal article|review article|article"),
    ("Preprint", r"preprint|posted content"),
    ("Working Paper", r"working paper|discussion paper"),
    ("Conference Paper", r"conference"),
    ("Book Chapter", r"chapter|encyclopedia|dictionary"),
    ("Book", r"\bbook\b"),
    ("Thesis", r"thesis|dissertation"),
    ("Research Report", r"report"),
    ("Data Collection", r"data ?set|data collection"),
    ("Newspaper Article", r"newspaper"),
]


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def split_prefix(name: str) -> tuple[str, str | None]:
    match = PREFIX_RE.match(clean(name))
    return PREFIX_RE.sub("", clean(name)).strip(), match.group(1) if match else None


def rank(title: str | None, prefix: str | None = None) -> str | None:
    for label, pattern in LADDER:
        if title and re.search(pattern, title, re.I):
            return label
    if prefix and prefix.lower() not in NON_RANK_PREFIXES:
        return prefix
    return None


def normalize_doi(value: Any) -> str | None:
    result = clean(value).lower()
    result = re.sub(r"^doi:\s*", "", result)
    result = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", result)
    result = re.sub(r"[?#].*$", "", result)
    result = re.sub(r"[.,;:)]+$", "", result)
    return result or None


def normalize_issn(value: Any) -> str | None:
    compact = re.sub(r"[^0-9X]", "", clean(value).upper())
    return f"{compact[:4]}-{compact[4:]}" if len(compact) == 8 else None


def extract_issns(*values: Any) -> list[str]:
    found: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            candidates: Iterable[Any] = value
        else:
            candidates = [value]
        for candidate in candidates:
            for raw in re.findall(r"\b\d{4}-?\d{3}[\dX]\b", clean(candidate), re.I):
                issn = normalize_issn(raw)
                if issn and issn not in found:
                    found.append(issn)
    return found


def normalize_url(value: str, base: str) -> str:
    if not value:
        return ""
    absolute = urljoin(base, value)
    parsed = urlparse(absolute)
    path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
    return parsed._replace(path=path, fragment="").geturl()


def normalize_type(raw: str | None) -> str:
    value = clean(raw)
    lower = value.lower()
    # Pure uses hierarchical labels.  The leading category is more reliable
    # than a later word (for example "Book/Film/Article review" is still a
    # journal contribution, not a book).
    if "contribution to journal" in lower:
        return "Journal Article"
    if "working paper" in lower:
        return "Preprint" if "preprint" in lower else "Working Paper"
    if "contribution to conference" in lower or "conference paper" in lower:
        return "Conference Paper"
    if "chapter in book" in lower:
        return "Book Chapter"
    if "book/report" in lower:
        return "Research Report" if "report" in lower else "Book"
    for label, pattern in TYPE_PATTERNS:
        if re.search(pattern, lower, re.I):
            return label
    return "Other"


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


class HttpClient:
    """Small cached HTTP client with bounded retries and global throttling."""

    def __init__(self, *, refresh: bool = False, timeout: int = 30, min_interval: float = 0.15):
        self.refresh = refresh
        self.timeout = timeout
        self.min_interval = min_interval
        self.cache_ttl = int(os.environ.get("CITS3200_CACHE_TTL_SECONDS", "86400"))
        cache_root = Path(os.environ.get("CITS3200_CACHE_DIR", Path.home() / ".cache" / "cits3200"))
        self.cache_dir = cache_root / "uwa"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._rate_lock = threading.Lock()
        self._last_request = 0.0

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.cache"

    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get_text(self, url: str, *, accept: str = "text/html,application/xhtml+xml", attempts: int = 3) -> str:
        cache = self._cache_path(url)
        cache_is_fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < self.cache_ttl
        if cache_is_fresh and not self.refresh:
            return cache.read_text(encoding="utf-8")

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._throttle()
                response = self.session.get(url, headers={"Accept": accept}, timeout=self.timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code} for {url}", response=response)
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                text = response.text
                if re.search(r"security verification|cf-chl-|正在进行安全验证", text, re.I):
                    raise RuntimeError(f"verification page returned for {url}")
                temporary = cache.with_suffix(f".{threading.get_ident()}.tmp")
                temporary.write_text(text, encoding="utf-8")
                temporary.replace(cache)
                return text
            except Exception as error:  # requests exposes several transport subclasses
                last_error = error
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status and status < 500 and status != 429:
                    break
                if attempt < attempts:
                    time.sleep(0.6 * (2 ** (attempt - 1)))

        # A forced refresh must never disguise stale cache content as fresh
        # source data.  In that mode the caller should fail and keep serving
        # the previously published dataset.
        if cache.exists() and not self.refresh:
            print(f"  warning: live request failed; using cached response for {url}")
            return cache.read_text(encoding="utf-8")
        raise RuntimeError(f"failed to fetch {url}: {last_error}")


def _rss_items(xml_text: str, base: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        values: dict[str, str] = {}
        for child in element:
            values[child.tag.rsplit("}", 1)[-1].lower()] = clean(child.text)
        link = normalize_url(values.get("link", ""), base)
        if link:
            items.append({"url": link, "description": values.get("description", "")})
    return items


def _property_value(soup: BeautifulSoup, label: str) -> str:
    for row in soup.select("table.properties tr"):
        heading, value = row.find("th"), row.find("td")
        if heading and value and clean(heading.get_text(" ", strip=True)).lower() == label.lower():
            return clean(value.get_text(" ", strip=True))
    return ""


def _bibtex_field(text: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}\s*=\s*[\"{{]([^\"}}]+)", text, re.I)
    return clean(match.group(1)) if match else ""


def _parse_staff_profile(html: str, profile_url: str, discipline: str, person_type: str = "") -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    org_slug = TARGETS[discipline]["slug"]
    affiliation = None
    for row in soup.select(".rendering_personorganisationlistrendererportal li"):
        link = row.select_one('a[rel="Organisation"]')
        href = normalize_url(link.get("href", ""), profile_url) if link else ""
        if f"/en/organisations/{org_slug}/" in href:
            affiliation = row
            break
    if affiliation is None:
        return None

    heading = soup.select_one(".page-section-header h1") or soup.find("h1")
    name = clean(heading.get_text(" ", strip=True) if heading else "")
    if not name:
        return None
    name_clean, prefix = split_prefix(name)
    title_node = affiliation.select_one(".job-title")
    title = clean(title_node.get_text(" ", strip=True) if title_node else "") or None
    title_clean = rank(title, prefix)
    orcid_link = soup.select_one('a[href*="orcid.org/"]')
    orcid = None
    if orcid_link:
        orcid = re.sub(r"^https?://(?:www\.)?orcid\.org/", "", orcid_link.get("href", ""), flags=re.I) or None
    count_match = re.search(r"View all\s+(\d+)\s+research outputs", clean(soup.get_text(" ", strip=True)), re.I)
    source_id = profile_url.rstrip("/").split("/")[-1]
    # The official department roster defines inclusion. Appointment details
    # remain useful quality notes, but must never remove a listed person.
    review_reasons = []
    if not title_clean:
        review_reasons.append("academic level requires confirmation")
    if re.search(r"teaching|honorary|emeritus|adjunct|visitor|contractor|fellow", title or "", re.I):
        review_reasons.append("appointment category requires client decision")
    if person_type and not re.search(r"research", person_type, re.I):
        review_reasons.append("Pure profile is not classified as research")
    return {
        "name": name,
        "name_clean": name_clean,
        "university": UNIVERSITY,
        "discipline": discipline,
        "profile_url": profile_url,
        "title": title,
        "title_clean": title_clean,
        "level_code": LEVELS.get(title_clean),
        "prefix": prefix,
        "source_id": source_id,
        "orcid": orcid,
        "reported_publication_count": int(count_match.group(1)) if count_match else None,
        "person_type": person_type or None,
        "official_roster_included": True,
        "scope_note": "; ".join(review_reasons) or None,
        "inclusion_review_required": False,
        "inclusion_review_reason": None,
    }


def _parse_publication(html: str, article_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    bibtex_node = soup.select_one(".rendering_researchoutput_bibtex")
    bibtex = clean(bibtex_node.get_text(" ", strip=True) if bibtex_node else "")
    status = _property_value(soup, "Publication status")
    doi_link = soup.select_one('a[href*="doi.org/"]')
    doi = normalize_doi(doi_link.get("href", "") if doi_link else "")
    journal = _property_value(soup, "Journal") or _bibtex_field(bibtex, "journal") or None
    issns = extract_issns(
        _bibtex_field(bibtex, "issn"),
        _property_value(soup, "ISSN"),
        _property_value(soup, "Electronic ISSN"),
    )
    # Linked profile anchors contain only internal authors.  BibTeX contains
    # the complete byline, including external co-authors, so it is the
    # authoritative source for author_count.
    bibtex_authors = _bibtex_field(bibtex, "author")
    authors = [clean(re.sub(r"[{}]", "", value)) for value in re.split(r"\s+and\s+", bibtex_authors)]
    authors = [name for name in authors if name]
    if not authors:
        author_nodes = soup.select(".rendering_researchoutput_associatespersonsclassifiedportal a[rel='Person']")
        authors = [clean(node.get_text(" ", strip=True).lstrip(",")) for node in author_nodes]
        authors = [name for name in authors if name]
    heading = soup.select_one(".page-section-header h1") or soup.find("h1")
    title = clean(heading.get_text(" ", strip=True) if heading else "")
    year_match = re.search(r"\b(18|19|20)\d{2}\b", status)
    if not year_match:
        year_match = re.search(r"\b(18|19|20)\d{2}\b", _bibtex_field(bibtex, "year"))
    type_node = soup.select_one(".rendering_researchoutput_publicationcontenttyperendererportalng .type") or soup.select_one("p.type")
    raw_type = clean(type_node.get_text(" ", strip=True) if type_node else "")
    raw_type = re.sub(r"^Research output:\s*", "", raw_type, flags=re.I)
    return {
        "publication_id": stable_id("uwa-pub", doi or article_url),
        "record_id": article_url.split("/en/publications/")[-1].replace("/", ""),
        "title": title,
        "year": year_match.group(0) if year_match else None,
        "type": normalize_type(raw_type),
        "raw_type": raw_type or None,
        "n_authors": len(authors) or None,
        "authors": "; ".join(authors) or None,
        "issns": issns,
        "journal": journal,
        "journal_canonical": None,
        "publisher": _property_value(soup, "Publisher") or _bibtex_field(bibtex, "publisher") or None,
        "doi": doi,
        "link": article_url,
        "source": "UWA Pure",
        "publication_status": status or None,
    }


def _discover_staff(client: HttpClient, discipline: str, verbose: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target = TARGETS[discipline]
    org_url = target["org_url"]
    feed = f"{org_url}persons/?format=rss"
    candidates: dict[str, str] = {}
    page = 0
    while page < 100:
        url = feed if page == 0 else f"{feed}&page={page}"
        batch = _rss_items(client.get_text(url, accept="application/rss+xml,text/xml"), feed)
        before = len(candidates)
        for item in batch:
            description = BeautifulSoup(item["description"], "html.parser")
            type_node = description.select_one("p.type")
            raw_person_type = clean(type_node.get_text(" ", strip=True) if type_node else "").replace("Person:", "").strip()
            candidates[item["url"]] = html_module.unescape(html_module.unescape(raw_person_type))
        if not batch or len(candidates) == before:
            break
        page += 1

    failures: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    def fetch_and_parse(url: str, person_type: str):
        return _parse_staff_profile(client.get_text(url), url, discipline, person_type)

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {
            pool.submit(fetch_and_parse, url, person_type): url
            for url, person_type in candidates.items()
        }
        for future in as_completed(jobs):
            url = jobs[future]
            try:
                record = future.result()
                if record:
                    records.append(record)
            except Exception as error:
                failures.append({"url": url, "error": str(error)})
    records.sort(key=lambda row: row["name_clean"].casefold())
    if verbose:
        print(f"  {discipline}: {len(records)} staff from {len(candidates)} candidate profiles")
    return records, failures


def _discover_publication_urls(client: HttpClient, person: dict[str, Any]) -> tuple[list[str], str | None]:
    feed = f"{person['profile_url'].rstrip('/')}/publications/?format=rss"
    expected = person.get("reported_publication_count")
    max_pages = math.ceil(expected / 50) + 2 if expected else 100
    urls: set[str] = set()
    try:
        for page in range(max_pages):
            page_url = feed if page == 0 else f"{feed}&page={page}"
            batch = _rss_items(client.get_text(page_url, accept="application/rss+xml,text/xml"), feed)
            before = len(urls)
            urls.update(item["url"] for item in batch)
            if not batch or len(urls) == before or (expected and len(urls) >= expected):
                break
        return sorted(urls), None
    except Exception as error:
        return sorted(urls), str(error)


def _collect_live(
    disciplines: Iterable[str], *, refresh: bool, max_workers: int, limit_staff: int | None, verbose: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    client = HttpClient(refresh=refresh)
    staff: list[dict[str, Any]] = []
    staff_failures: list[dict[str, Any]] = []
    for discipline in disciplines:
        rows, failures = _discover_staff(client, discipline, verbose)
        staff.extend(rows)
        staff_failures.extend(failures)
    if limit_staff is not None:
        staff = staff[:limit_staff]

    discoveries: dict[str, list[str]] = {}
    discovery_failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as pool:
        jobs = {pool.submit(_discover_publication_urls, client, person): person for person in staff}
        for future in as_completed(jobs):
            person = jobs[future]
            urls, error = future.result()
            discoveries[f"{person['discipline']}|{person['profile_url']}"] = urls
            if error:
                discovery_failures.append({"profile_url": person["profile_url"], "error": error})

    all_urls = sorted({url for urls in discoveries.values() for url in urls})
    parsed: dict[str, dict[str, Any]] = {}
    detail_failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as pool:
        jobs = {pool.submit(lambda u: _parse_publication(client.get_text(u), u), url): url for url in all_urls}
        for index, future in enumerate(as_completed(jobs), 1):
            url = jobs[future]
            try:
                parsed[url] = future.result()
            except Exception as error:
                detail_failures.append({"url": url, "error": str(error)})
            if verbose and index % 100 == 0:
                print(f"  publication details: {index}/{len(all_urls)}")

    pubs: list[dict[str, Any]] = []
    # A person may legitimately belong to both Accounting and Finance.  Keep
    # one relationship per discipline rather than silently dropping the
    # second affiliation (currently relevant to Vincent Chong).
    seen: set[tuple[str, str, str]] = set()
    for person in staff:
        key = f"{person['discipline']}|{person['profile_url']}"
        for url in discoveries.get(key, []):
            raw = parsed.get(url)
            if not raw:
                continue
            relation_key = (person["discipline"], person["profile_url"], raw["publication_id"])
            if relation_key in seen:
                continue
            seen.add(relation_key)
            pubs.append({
                **raw,
                "name": person["name_clean"],
                "source_id": person.get("source_id"),
                "discipline": person["discipline"],
                "researcher_match_method": "profile_feed_membership",
                "researcher_match_confidence": "high",
                "requires_review": False,
            })

    pubs.sort(key=lambda row: (row["name"].casefold(), -(int(row["year"]) if row.get("year") else 0), row["title"].casefold()))
    discovery_checks = []
    for person in staff:
        key = f"{person['discipline']}|{person['profile_url']}"
        expected = person.get("reported_publication_count")
        discovered = len(discoveries.get(key, []))
        discovery_checks.append({
            "name": person["name_clean"],
            "discipline": person["discipline"],
            "profile_url": person["profile_url"],
            "reported_publication_count": expected,
            "discovered_publication_count": discovered,
            "count_reconciles": None if expected is None else expected == discovered,
        })
    unreconciled = [row for row in discovery_checks if row["count_reconciles"] is False]
    without_reported_count = [row for row in discovery_checks if row["count_reconciles"] is None]

    quality = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live",
        "staff_records": len(staff),
        "unique_publication_urls": len(all_urls),
        "researcher_publication_links": len(pubs),
        "publication_feed_reconciliation": discovery_checks,
        "publication_feeds_not_reconciled": unreconciled,
        "publication_feeds_without_reported_count": without_reported_count,
        "staff_failures": staff_failures,
        "discovery_failures": discovery_failures,
        "detail_failures": detail_failures,
        "attribution_rule": "official personal Pure publication feed membership",
    }
    return staff, pubs, quality


LAST_QUALITY: dict[str, Any] = {}


def collect(
    verbose: bool = True,
    *,
    disciplines: Iterable[str] = ("Accounting", "Finance"),
    refresh: bool = False,
    max_workers: int = 4,
    limit_staff: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(staff, publications)`` satisfying the shared team contract."""
    selected = tuple(disciplines)
    unknown = set(selected) - set(TARGETS)
    if unknown:
        raise ValueError(f"unknown disciplines: {sorted(unknown)}")
    staff, pubs, quality = _collect_live(
        selected,
        refresh=refresh,
        max_workers=max_workers,
        limit_staff=limit_staff,
        verbose=verbose,
    )
    LAST_QUALITY.clear()
    LAST_QUALITY.update(quality)
    if verbose:
        print(f"  contract output: {len(staff)} staff, {len(pubs)} researcher-publication rows")
    return staff, pubs


def _csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return str(value).lower()
    return "" if value is None else value


def write_snapshot(staff: list[dict[str, Any]], pubs: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("uwa_staff_adapter", staff), ("uwa_publications_adapter", pubs)):
        (output_dir / f"{name}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        headers = list(dict.fromkeys(key for row in rows for key in row))
        with (output_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows({key: _csv_value(value) for key, value in row.items()} for row in rows)
    (output_dir / "uwa_adapter_quality.json").write_text(
        json.dumps(LAST_QUALITY, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect UWA Accounting/Finance staff and official Pure publications")
    parser.add_argument("--discipline", choices=["Accounting", "Finance", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "final output" / "uwa")
    parser.add_argument("--refresh", action="store_true", help="ignore HTTP cache")
    parser.add_argument("--limit-staff", type=int, default=None, help="small smoke-test subset")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    disciplines = TARGETS if args.discipline == "all" else (args.discipline,)
    staff, pubs = collect(
        disciplines=disciplines,
        refresh=args.refresh,
        max_workers=args.max_workers,
        limit_staff=args.limit_staff,
    )
    write_snapshot(staff, pubs, args.output_dir)
    print(json.dumps(LAST_QUALITY, indent=2))


if __name__ == "__main__":
    main()
