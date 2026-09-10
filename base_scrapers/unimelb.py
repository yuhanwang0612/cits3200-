"""University of Melbourne Accounting and Finance adapter.

``collect()`` returns the shared team contract: a staff list and a list of
researcher-publication rows.  Current staff are taken from the official FBE
directory.  Publications and persistent author identifiers come from the
official Minerva Access API.

The collector is deliberately independent of previous project output.  It can
bootstrap from an empty data directory using the live FBE directory and
Minerva API.  Minerva attribution uses exact internal IDs or ORCIDs;
ambiguous name-only relationships are excluded and reported for review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_module
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup


UNIVERSITY = "University of Melbourne"
ROR = "01ej9dk98"
ROOT = Path(__file__).resolve().parents[1]
API_ROOT = "https://minerva-access.unimelb.edu.au/server/api"
SEARCH_URL = f"{API_ROOT}/discover/search/objects"
USER_AGENT = "CITS3200-Team20/3.0 (academic research; respectful automated collection)"

TARGETS = {
    "Accounting": {
        "staff_url": "https://fbe.unimelb.edu.au/about/academic-staff?queries_tags_query=4895951",
        "department": "Department of Accounting",
        "collection_id": "10c7b2a9-76da-5115-b275-96e65d024912",
    },
    "Finance": {
        "staff_url": "https://fbe.unimelb.edu.au/about/academic-staff?queries_tags_query=4895953",
        "department": "Department of Finance",
        "collection_id": "c9dc98ca-3fac-599b-8dee-557a4fb3f5b9",
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
PERSON_TITLES = {
    "associate", "doctor", "dr", "emeritus", "honorary", "miss", "mr", "mrs", "ms", "prof", "professor"
}

TYPE_MAP = {
    "journal article": "Journal Article",
    "article": "Journal Article",
    "review": "Journal Article",
    "report": "Research Report",
    "conference paper": "Conference Paper",
    "chapter": "Book Chapter",
    "book chapter": "Book Chapter",
    "book": "Book",
    "phd thesis": "Thesis",
    "thesis": "Thesis",
    "working paper": "Working Paper",
    "preprint": "Preprint",
    "data set": "Data Collection",
    "dataset": "Data Collection",
    "newspaper article": "Newspaper Article",
}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(str(value or ""))).strip()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", clean(value))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def person_key(value: Any) -> str:
    tokens = [token for token in normalize_text(value).split() if token not in PERSON_TITLES]
    return " ".join(sorted(set(tokens)))


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
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for candidate in candidates:
            for raw in re.findall(r"\b\d{4}-?\d{3}[\dX]\b", clean(candidate), re.I):
                issn = normalize_issn(raw)
                if issn and issn not in found:
                    found.append(issn)
    return found


def normalize_type(raw: str | None) -> str:
    value = clean(raw)
    mapped = TYPE_MAP.get(value.lower())
    if mapped:
        return mapped
    lower = value.lower()
    if "journal" in lower and ("article" in lower or "review" in lower):
        return "Journal Article"
    if "conference" in lower:
        return "Conference Paper"
    if "chapter" in lower:
        return "Book Chapter"
    if "thesis" in lower or "dissertation" in lower:
        return "Thesis"
    return "Other"


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


class HttpClient:
    def __init__(self, *, refresh: bool = False, timeout: int = 60, min_interval: float = 0.15):
        self.refresh = refresh
        self.timeout = timeout
        self.min_interval = min_interval
        self.cache_ttl = int(os.environ.get("CITS3200_CACHE_TTL_SECONDS", "86400"))
        cache_root = Path(os.environ.get("CITS3200_CACHE_DIR", Path.home() / ".cache" / "cits3200"))
        self.cache_dir = cache_root / "unimelb"
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

    def get_text(self, url: str, *, accept: str = "text/html,application/xhtml+xml", attempts: int = 4) -> str:
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
                    raise RuntimeError(f"Cloudflare verification page returned for {url}")
                temporary = cache.with_suffix(f".{threading.get_ident()}.tmp")
                temporary.write_text(text, encoding="utf-8")
                temporary.replace(cache)
                return text
            except Exception as error:
                last_error = error
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status and status < 500 and status != 429:
                    break
                if attempt < attempts:
                    time.sleep(0.6 * (2 ** (attempt - 1)))
        # Do not silently publish old cache entries during a forced refresh.
        # The versioned pipeline will leave its current dataset untouched if
        # this request fails.
        if cache.exists() and not self.refresh:
            print(f"  warning: live request failed; using cached response for {url}")
            return cache.read_text(encoding="utf-8")
        raise RuntimeError(f"failed to fetch {url}: {last_error}")

    def get_json(self, url: str) -> dict[str, Any]:
        return json.loads(self.get_text(url, accept="application/json"))


def _metadata_values(metadata: dict[str, Any], key: str) -> list[str]:
    return [clean(entry.get("value")) for entry in metadata.get(key, []) if clean(entry.get("value"))]


def _metadata_first(metadata: dict[str, Any], key: str) -> str:
    values = _metadata_values(metadata, key)
    return values[0] if values else ""


def _parse_internal_author(value: str) -> dict[str, str]:
    parts = [clean(part) for part in clean(value).split(";") if clean(part)]
    orcid_match = re.search(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", clean(value), re.I)
    internal_id = next((part for part in parts[1:] if part.isdigit()), "")
    return {
        "name": parts[0] if parts else "",
        "internal_id": internal_id,
        "orcid": orcid_match.group(0).upper() if orcid_match else "",
        "raw": clean(value),
    }


def _parse_item(wrapper: dict[str, Any]) -> dict[str, Any] | None:
    item = wrapper.get("_embedded", {}).get("indexableObject") or wrapper
    if not item or item.get("type") not in (None, "item"):
        return None
    metadata = item.get("metadata") or {}
    doi = normalize_doi(_metadata_first(metadata, "dc.identifier.doi"))
    issued = _metadata_first(metadata, "dc.date.issued")
    handle = item.get("handle") or ""
    item_url = f"https://hdl.handle.net/{handle}" if handle else ""
    open_access_url = _metadata_first(metadata, "melbourne.openaccess.url")
    authors = _metadata_values(metadata, "dc.contributor.author")
    internal = [
        _parse_internal_author(value)
        for value in _metadata_values(metadata, "melbourne.internal.authorids")
        if _parse_internal_author(value)["name"]
    ]
    issns = extract_issns(
        _metadata_first(metadata, "dc.identifier.issn"),
        _metadata_first(metadata, "dc.identifier.eissn"),
    )
    year_match = re.search(r"\b(18|19|20)\d{2}\b", issued)
    record_key = doi or item.get("uuid") or item.get("id") or item_url
    return {
        "publication_id": stable_id("unimelb-pub", str(record_key)),
        "record_id": item.get("uuid") or item.get("id") or None,
        "handle": handle or None,
        "title": _metadata_first(metadata, "dc.title") or clean(item.get("name")),
        "year": year_match.group(0) if year_match else None,
        "issued_date": issued or None,
        "doi": doi,
        "link": f"https://doi.org/{doi}" if doi else open_access_url or item_url or None,
        "journal": _metadata_first(metadata, "melbourne.source.title") or None,
        "journal_canonical": None,
        "issns": issns,
        "volume": _metadata_first(metadata, "melbourne.source.volume") or None,
        "issue": _metadata_first(metadata, "melbourne.source.issue") or None,
        "pages": _metadata_first(metadata, "melbourne.source.pages") or None,
        "authors_list": authors,
        "authors": "; ".join(authors) or None,
        "melbourne_authors": _metadata_values(metadata, "melbourne.contributor.author"),
        "internal_authors": internal,
        "n_authors": len(authors) or None,
        "raw_type": _metadata_first(metadata, "dc.type") or None,
        "type": normalize_type(_metadata_first(metadata, "dc.type")),
        "publisher": _metadata_first(metadata, "dc.publisher") or None,
        "departments": _metadata_values(metadata, "melbourne.affiliation.department"),
        "source": "UniMelb Minerva",
    }


def _staff_from_live_html(html: str, discipline: str) -> list[dict[str, Any]]:
    if re.search(r"security verification|cf-chl-|正在进行安全验证", html, re.I):
        raise RuntimeError("UniMelb FBE returned a Cloudflare verification page")
    soup = BeautifulSoup(html, "html.parser")
    expected_department = TARGETS[discipline]["department"]
    records: list[dict[str, Any]] = []
    # The FBE source contains invalid ``div/style`` elements inside tbody.
    # Python's html.parser may therefore close the table early even though
    # all staff rows remain in the document.  Searching all tr elements is
    # deliberate and mirrors the repaired browser DOM used by the JS version.
    for row in soup.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            cells = row.find_all("td")
        if not cells:
            continue
        heading = row.find("h5")
        profile = heading.find("a", href=True) if heading else row.select_one('a[href*="findanexpert"]')
        if not heading or not profile:
            continue
        department_node = cells[2].find("em") if len(cells) > 2 else None
        department = clean(department_node.get_text(" ", strip=True) if department_node else "") or expected_department
        if expected_department.lower() not in department.lower():
            continue
        name = clean(heading.get_text(" ", strip=True))
        name_clean, prefix = split_prefix(name)
        roles = [clean(node.get_text(" ", strip=True)) for node in cells[0].find_all("p")]
        title = "; ".join(role for role in roles if role) or None
        title_clean = rank(title, prefix)
        orcid_link = row.select_one('a[href*="orcid.org/"]')
        orcid = None
        if orcid_link:
            orcid = re.sub(r"^https?://(?:www\.)?orcid\.org/", "", orcid_link.get("href", ""), flags=re.I) or None
        records.append({
            "name": name,
            "name_clean": name_clean,
            "university": UNIVERSITY,
            "discipline": discipline,
            "profile_url": profile.get("href"),
            "title": title,
            "title_clean": title_clean,
            "level_code": LEVELS.get(title_clean),
            "prefix": prefix,
            "source_id": None,
            "orcid": orcid,
            "roster_source": "live official FBE staff directory",
            "official_roster_included": True,
            "scope_note": None,
            "inclusion_review_required": False,
            "inclusion_review_reason": None,
        })
    if not records:
        raise RuntimeError(f"no {discipline} staff rows found; directory markup may have changed")
    return records


def _load_roster(client: HttpClient, discipline: str, *, verbose: bool):
    records = _staff_from_live_html(client.get_text(TARGETS[discipline]["staff_url"]), discipline)
    if verbose:
        print(f"  {discipline}: live FBE roster ({len(records)} staff)")
    return records, "live"


def _seed_from_live(
    client: HttpClient, discipline: str, verbose: bool
) -> tuple[list[dict[str, Any]], int | None]:
    collection_id = TARGETS[discipline]["collection_id"]
    collection = client.get_json(f"{API_ROOT}/core/collections/{collection_id}")
    page, total_pages = 0, 1
    rows: list[dict[str, Any]] = []
    while page < total_pages:
        query = urlencode({"scope": collection_id, "page": page, "size": 100, "sort": "dc.date.available,DESC"})
        response = client.get_json(f"{SEARCH_URL}?{query}")
        result = response.get("_embedded", {}).get("searchResult", {})
        total_pages = result.get("page", {}).get("totalPages", 1)
        for wrapper in result.get("_embedded", {}).get("objects", []):
            parsed = _parse_item(wrapper)
            if parsed:
                rows.append(parsed)
        page += 1
    unique = {row["publication_id"]: row for row in rows}
    reported = collection.get("archivedItemsCount")
    if verbose:
        note = "" if reported in (None, len(unique)) else f" (repository reports {reported})"
        print(f"  {discipline}: {len(unique)} departmental seed publications{note}")
    return list(unique.values()), reported


def _build_identities(
    staff: list[dict[str, Any]],
    seed: list[dict[str, Any]],
    identity_overrides: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, dict[str, str]]] = {}
    for publication in seed:
        for identity in publication.get("internal_authors", []):
            key = person_key(identity.get("name"))
            if not key:
                continue
            identity_id = identity.get("internal_id") or identity.get("orcid") or identity.get("raw")
            by_name.setdefault(key, {})[identity_id] = identity
    override_map = {
        (item.get("discipline"), item.get("profile_url")): item
        for item in identity_overrides
        if item.get("profile_url")
    }
    identities = []
    for person in staff:
        candidates = list(by_name.get(person_key(person["name_clean"]), {}).values())
        internal_ids = sorted({item["internal_id"] for item in candidates if item.get("internal_id")})
        orcids = sorted({item["orcid"] for item in candidates if item.get("orcid")})
        unambiguous = bool(candidates) and len(internal_ids) <= 1 and len(orcids) <= 1
        identity = {
            "person": person,
            "internal_id": internal_ids[0] if unambiguous and internal_ids else "",
            "orcid": orcids[0] if unambiguous and orcids else "",
            "repository_author_name": candidates[0].get("name", "") if unambiguous else "",
            "candidate_count": len(candidates),
            "confidence": "high" if unambiguous and (internal_ids or orcids) else "none" if not candidates else "ambiguous",
        }
        override = override_map.get((person.get("discipline"), person.get("profile_url")))
        if (
            override
            and person_key(override.get("name")) == person_key(person.get("name_clean"))
            and override.get("repository_author_name")
            and (override.get("internal_id") or override.get("orcid"))
        ):
            identity.update({
                "internal_id": str(override.get("internal_id") or "").strip(),
                "orcid": str(override.get("orcid") or "").strip().removeprefix("https://orcid.org/"),
                "repository_author_name": str(override["repository_author_name"]).strip(),
                "confidence": "high",
                "identity_source": "manual_verified_override",
                "evidence_url": override.get("evidence_url"),
            })
        if identity["confidence"] == "high":
            person["source_id"] = identity["internal_id"] or None
            person["orcid"] = person.get("orcid") or identity["orcid"] or None
        identities.append(identity)
    return identities


def _relationship_match(publication: dict[str, Any], identity: dict[str, Any]) -> str | None:
    internal_authors = publication.get("internal_authors", [])
    if identity.get("orcid") and any(author.get("orcid") == identity["orcid"] for author in internal_authors):
        return "exact_orcid"
    if identity.get("internal_id") and any(author.get("internal_id") == identity["internal_id"] for author in internal_authors):
        return "exact_internal_author_id"
    return None


def _identity_search_url(identity: dict[str, Any], page: int) -> str:
    author_name = identity.get("repository_author_name")
    if not author_name:
        raise ValueError("resolved Minerva identity has no repository author name")
    # DSpace exposes `author` as an exact Discovery filter.  This is much
    # narrower and faster than a whole-repository free-text ORCID query.
    # Returned records are still checked against the internal author ID or
    # ORCID below; the name filter alone is never attribution evidence.
    query = urlencode({
        "f.author": f"{author_name},equals",
        "dsoType": "item",
        "page": page,
        "size": 100,
        "sort": "dc.date.issued,DESC",
    })
    return f"{SEARCH_URL}?{query}"


def _search_identity(client: HttpClient, identity: dict[str, Any]) -> list[dict[str, Any]]:
    page, total_pages = 0, 1
    found: dict[str, dict[str, Any]] = {}
    while page < total_pages:
        response = client.get_json(_identity_search_url(identity, page))
        result = response.get("_embedded", {}).get("searchResult", {})
        total_pages = result.get("page", {}).get("totalPages", 1)
        for wrapper in result.get("_embedded", {}).get("objects", []):
            publication = _parse_item(wrapper)
            if publication and _relationship_match(publication, identity):
                found[publication["publication_id"]] = publication
        page += 1
    return list(found.values())


def _publication_contract(publication: dict[str, Any], identity: dict[str, Any], method: str) -> dict[str, Any]:
    person = identity["person"]
    return {
        "name": person["name_clean"],
        "source_id": identity.get("internal_id") or person.get("source_id"),
        "title": publication["title"],
        "year": publication.get("year"),
        "type": publication.get("type") or "Other",
        "n_authors": publication.get("n_authors"),
        "authors": publication.get("authors"),
        "issns": publication.get("issns") or [],
        "journal": publication.get("journal"),
        "journal_canonical": publication.get("journal_canonical"),
        "publisher": publication.get("publisher"),
        "doi": publication.get("doi"),
        "link": publication.get("link"),
        "source": "UniMelb Minerva",
        "publication_id": publication["publication_id"],
        "record_id": publication.get("record_id"),
        "raw_type": publication.get("raw_type"),
        "discipline": person["discipline"],
        "researcher_match_method": method,
        "researcher_match_confidence": "high",
        "requires_review": False,
    }


def _collect_live(
    disciplines: Iterable[str], *, refresh: bool, max_workers: int, limit_staff: int | None,
    verbose: bool, identity_overrides: Iterable[dict[str, Any]] = (),
):
    client = HttpClient(refresh=refresh)
    staff: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    roster_sources: dict[str, str] = {}
    seed_counts: dict[str, int] = {}
    seed_reconciliation: dict[str, dict[str, Any]] = {}
    seeds: dict[str, list[dict[str, Any]]] = {}
    for discipline in disciplines:
        local_staff, roster_source = _load_roster(client, discipline, verbose=verbose)
        if limit_staff is not None:
            local_staff = local_staff[: max(0, limit_staff - len(staff))]
        seed, reported_seed_count = _seed_from_live(client, discipline, verbose)
        seed_counts[discipline] = len(seed)
        seed_reconciliation[discipline] = {
            "reported_record_count": reported_seed_count,
            "extracted_record_count": len(seed),
            "count_reconciles": None if reported_seed_count is None else reported_seed_count == len(seed),
        }
        seeds[discipline] = seed
        roster_sources[discipline] = roster_source
        staff.extend(local_staff)
        identities.extend(_build_identities(local_staff, seed, identity_overrides))
        if limit_staff is not None and len(staff) >= limit_staff:
            break

    accepted = [identity for identity in identities if identity["confidence"] == "high"]
    failures: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seed_relationships = 0
    for identity in accepted:
        discipline = identity["person"]["discipline"]
        seed_matches = [
            publication
            for publication in seeds.get(discipline, [])
            if _relationship_match(publication, identity)
        ]
        for publication in seed_matches:
            method = _relationship_match(publication, identity)
            if method:
                rows.append(_publication_contract(publication, identity, method))
                seed_relationships += 1

    # Search every resolved author, including on a first-ever run.  Exact
    # identifier validation below prevents broad Minerva name searches from
    # assigning another researcher's output.
    aborted_searches: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 3))) as pool:
        jobs = {pool.submit(_search_identity, client, identity): identity for identity in accepted}
        for index, future in enumerate(as_completed(jobs), 1):
            identity = jobs[future]
            try:
                for publication in future.result():
                    method = _relationship_match(publication, identity)
                    if method:
                        rows.append(_publication_contract(publication, identity, method))
            except Exception as error:
                failures.append({"name": identity["person"]["name_clean"], "error": str(error)})
                # When Minerva stops responding, do not let dozens of queued
                # authors each consume their full retry budget.  Cancel work
                # that has not started; the pipeline will reject this partial
                # run and a later cached retry can resume it.
                if len(failures) >= 3:
                    for pending, pending_identity in jobs.items():
                        if pending.cancel():
                            aborted_searches.append({
                                "name": pending_identity["person"]["name_clean"],
                                "error": "search not started after three Minerva failures in this run",
                            })
                    break
            if verbose:
                print(f"  live researcher searches: {index}/{len(accepted)}")
    unique = {
        (row["discipline"], row["name"], row["publication_id"]): row
        for row in rows
    }
    pubs = list(unique.values())
    pubs.sort(key=lambda row: (row["name"].casefold(), -(int(row["year"]) if row.get("year") else 0), row["title"].casefold()))
    quality = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live",
        "roster_sources": roster_sources,
        "staff_records": len(staff),
        "staff_with_high_confidence_minerva_identity": len(accepted),
        "staff_without_high_confidence_identity": len(staff) - len(accepted),
        "departmental_seed_publications": seed_counts,
        "departmental_seed_reconciliation": seed_reconciliation,
        "exact_seed_relationships": seed_relationships,
        "researcher_publication_links": len(pubs),
        "live_researcher_searches": len(accepted),
        "manual_identity_overrides_applied": sum(
            identity.get("identity_source") == "manual_verified_override" for identity in identities
        ),
        "search_failures": failures + aborted_searches,
        "searches_aborted_by_circuit_breaker": len(aborted_searches),
        "attribution_rule": "exact Minerva internal author ID or ORCID only",
        "refresh_note": "all records come from the live/cached official endpoints; no previous project dataset is read",
        "identity_review_queue": [
            {
                "name": identity["person"]["name_clean"],
                "discipline": identity["person"]["discipline"],
                "profile_url": identity["person"]["profile_url"],
                "identity_confidence": identity["confidence"],
                "candidate_count": identity["candidate_count"],
            }
            for identity in identities
            if identity["confidence"] != "high"
        ],
    }
    return staff, pubs, quality


LAST_QUALITY: dict[str, Any] = {}


def collect(
    verbose: bool = True,
    *,
    disciplines: Iterable[str] = ("Accounting", "Finance"),
    refresh: bool = False,
    max_workers: int = 3,
    limit_staff: int | None = None,
    identity_overrides: Iterable[dict[str, Any]] = (),
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
        identity_overrides=identity_overrides,
    )
    LAST_QUALITY.clear()
    LAST_QUALITY.update(quality)
    if verbose:
        print(f"  contract output: {len(staff)} staff, {len(pubs)} high-confidence researcher-publication rows")
    return staff, pubs


def _csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return str(value).lower()
    return "" if value is None else value


def write_snapshot(staff: list[dict[str, Any]], pubs: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("unimelb_staff_adapter", staff), ("unimelb_publications_adapter", pubs)):
        (output_dir / f"{name}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        headers = list(dict.fromkeys(key for row in rows for key in row))
        with (output_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows({key: _csv_value(value) for key, value in row.items()} for row in rows)
    (output_dir / "unimelb_adapter_quality.json").write_text(
        json.dumps(LAST_QUALITY, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect UniMelb Accounting/Finance staff and Minerva publications")
    parser.add_argument("--discipline", choices=["Accounting", "Finance", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "final output" / "unimelb")
    parser.add_argument("--refresh", action="store_true", help="ignore HTTP cache")
    parser.add_argument("--limit-staff", type=int, default=None, help="small smoke-test subset")
    parser.add_argument("--max-workers", type=int, default=3)
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
