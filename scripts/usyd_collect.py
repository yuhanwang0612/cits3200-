from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UNIVERSITY = "University of Sydney"

ACCOUNTING_EXPERTS_URL = "https://profiles.sydney.edu.au/groups/652444/experts"
FINANCE_EXPERTS_URL = "https://profiles.sydney.edu.au/groups/652446/experts"
USYD_GROUP_MEMBERS_API = "https://profiles.sydney.edu.au/api/users/membersOfGroup"
USYD_USER_API = "https://profiles.sydney.edu.au/api/users"
USYD_PUBLICATIONS_API = "https://profiles.sydney.edu.au/api/publications/linkedTo"

USER_AGENT = "CITS3200-Group20-USyd/0.4 (student project; conservative requests)"
ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-[\dX]{4}\b", re.I)

PUBLICATION_COLUMNS = [
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


def normalise_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", clean_space(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


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


def stable_usyd_publication_id(record: dict) -> str:
    doi = normalise_doi(record.get("doi"))
    if doi:
        return "doi-" + hashlib.sha1(doi.encode("utf-8")).hexdigest()[:20]
    native_id = clean_space(str(record.get("discoveryId") or record.get("objectId") or ""))
    if native_id:
        return f"usydpub-{native_id}"
    title = clean_space(record.get("title")).casefold()
    year = str(((record.get("publicationDate") or {}).get("year")) or "")
    return "fallback-" + hashlib.sha1(f"{title}|{year}".encode()).hexdigest()[:20]


def fetch_html(url: str, *, timeout: int = 30) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r.text


NON_PROFILE_SLUGS = {
    "about",
    "contact",
    "cookiesettings",
    "disclaimer",
    "feedback",
    "privacy",
    "search",
}


def extract_profile_urls_from_rendered_html(html: str) -> list[str]:
    """Fallback HTML link extractor.

    Production group discovery uses Sydney Profiles' public group-members API,
    but this helper remains useful for diagnostics/tests. Only canonical
    single-slug person URLs are retained.
    """
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = "https://profiles.sydney.edu.au" + href
        parsed = urlparse(href)
        if parsed.netloc.casefold() != "profiles.sydney.edu.au":
            continue
        path = parsed.path.strip("/")
        if not path or "/" in path:
            continue
        if path.casefold() in NON_PROFILE_SLUGS:
            continue

        canonical = f"https://profiles.sydney.edu.au/{path}"
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def group_id_from_url(group_url: str) -> int:
    parts = [part for part in urlparse(group_url).path.split("/") if part]
    try:
        i = parts.index("groups")
        return int(parts[i + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Cannot determine Sydney group ID from {group_url}") from exc


def collect_group_profile_urls(
    group_url: str,
    *,
    timeout: int = 45,
    per_page: int = 25,
    session: requests.Session | None = None,
) -> list[str]:
    """Fetch every member of a Sydney Profiles group using its public JSON API.

    The rendered group page shows only the first page (25 members), so scraping
    DOM links silently truncates larger groups. Follow the API pagination until
    its reported total has been collected.
    """
    group_id = group_id_from_url(group_url)
    client = session or requests.Session()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": group_url,
    }

    urls: list[str] = []
    seen: set[str] = set()
    start = 0

    while True:
        payload = {
            "groupId": group_id,
            "pagination": {"perPage": per_page, "startFrom": start},
            "sort": "lastNameAsc",
        }
        r = client.post(
            USYD_GROUP_MEMBERS_API,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        batch = data.get("resource") or []

        for member in batch:
            slug = clean_space(member.get("discoveryUrlId"))
            if not slug or slug.casefold() in NON_PROFILE_SLUGS:
                continue
            canonical = f"https://profiles.sydney.edu.au/{slug}"
            if canonical not in seen:
                seen.add(canonical)
                urls.append(canonical)

        pagination = data.get("pagination") or {}
        total = int(pagination.get("total") or len(urls))
        if not batch or start + len(batch) >= total:
            break
        start += len(batch)
        time.sleep(0.05)

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


def profile_slug(profile_url: str) -> str:
    return urlparse(profile_url).path.strip("/").split("/")[0]


def fetch_usyd_user(
    profile_url: str,
    *,
    timeout: int = 30,
    session: requests.Session | None = None,
) -> dict | None:
    """Fetch the structured Sydney Profiles researcher record.

    Returns None for stale/invalid profile slugs so one bad group member cannot
    abort collection for the entire university.
    """
    slug = profile_slug(profile_url)
    client = session or requests.Session()
    r = client.get(
        f"{USYD_USER_API}/{slug}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def usyd_user_to_researcher(user: dict, profile_url: str, field: str) -> dict[str, str]:
    name = clean_space(
        user.get("firstNameLastName")
        or f"{clean_space(user.get('firstName'))} {clean_space(user.get('lastName'))}"
    )

    positions = user.get("positions") or []
    job_title = ""
    for position in positions:
        candidate = clean_space(position.get("position"))
        if candidate:
            job_title = candidate
            break

    if not job_title:
        for appointment in user.get("institutionalAppointments") or []:
            candidate = clean_space(appointment.get("position"))
            if candidate:
                job_title = candidate
                break

    raw_orcid = user.get("orcid")
    if isinstance(raw_orcid, dict):
        raw_orcid = raw_orcid.get("value") or raw_orcid.get("uri") or ""
    orcid = normalise_orcid(str(raw_orcid or ""))

    canonical_profile = f"https://profiles.sydney.edu.au/{profile_slug(profile_url)}"
    return {
        "researcher_id": stable_researcher_id(canonical_profile, field),
        "name": name,
        "job_title": job_title,
        "academic_level": academic_level_from_job_title(job_title),
        "university": UNIVERSITY,
        "field_of_research": field,
        "orcid": orcid,
        "openalex_author_id": "",
        "profile_url": canonical_profile,
    }


def fetch_usyd_publication_records(
    profile_url: str,
    *,
    timeout: int = 45,
    per_page: int = 25,
    session: requests.Session | None = None,
) -> list[dict]:
    """Fetch public Sydney Profiles research outputs through its JSON endpoint.

    The endpoint and payload were observed from the public Research Outputs tab.
    Pagination is followed using the returned total/startFrom values.
    """
    slug = profile_slug(profile_url)
    client = session or requests.Session()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": f"https://profiles.sydney.edu.au/{slug}/publications",
    }

    records: list[dict] = []
    start = 0
    while True:
        payload = {
            "objectId": slug,
            "category": "user",
            "pagination": {"perPage": per_page, "startFrom": start},
            "sort": "dateDesc",
            "favouritesFirst": True,
        }
        r = client.post(USYD_PUBLICATIONS_API, json=payload, headers=headers, timeout=timeout)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        batch = data.get("resource") or []
        records.extend(batch)

        pagination = data.get("pagination") or {}
        total = int(pagination.get("total") or len(records))
        if not batch or len(records) >= total:
            break
        start += len(batch)
        time.sleep(0.05)

    return records


def is_usyd_journal_article(record: dict) -> bool:
    return clean_space(record.get("objectTypeDisplayName")).casefold() == "journal article"


def usyd_record_to_publication(record: dict, researcher: dict[str, str]) -> dict[str, object]:
    authors_raw = record.get("authors") or []
    authors = [clean_space(a.get("fullName") or a.get("firstNameLastName") or a.get("nameShortFormat")) for a in authors_raw]
    authors = [a for a in authors if a]
    doi = normalise_doi(record.get("doi"))
    year = (record.get("publicationDate") or {}).get("year") or (record.get("date1") or {}).get("year") or ""
    journal = clean_space(record.get("journal") or record.get("parentTitle"))
    issn = clean_space(record.get("issn"))
    slug = profile_slug(researcher["profile_url"])
    source_url = f"https://profiles.sydney.edu.au/{slug}/publications"
    article_url = clean_space(record.get("publisherUrl")) or (f"https://doi.org/{doi}" if doi else source_url)

    return {
        "espace_id": researcher["researcher_id"],
        "name": researcher["name"],
        "journal_name": journal,
        "title": clean_space(record.get("title")),
        "year": year,
        "author_count": len(authors),
        "authors": "; ".join(authors),
        "quality_rank": "",
        "sjr_quartile": "",
        "doi": doi,
        "article_url": article_url,
        "link": source_url,
        "source": "Sydney Profiles",
        "citation_percentile": "",
        "cited_by_count": "",
        "fwci": "",
        "university": UNIVERSITY,
        "field_of_research": researcher["field_of_research"],
        "researcher_id": researcher["researcher_id"],
        "publication_id": stable_usyd_publication_id(record),
        "issn": issn,
        "eissn": "",
        "item_type": "Journal Article",
        "publication_status": "Published",
        "abdc_match_method": "",
        "abdc_match_status": "",
        "harvested_at": datetime.now(timezone.utc).isoformat(),
        "orcid": researcher["orcid"],
        "openalex_author_id": researcher["openalex_author_id"],
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


def openalex_name_matches(oa_author: dict, researcher: dict[str, str]) -> bool:
    wanted = normalise_name(researcher.get("name"))
    candidates = [oa_author.get("display_name"), *(oa_author.get("display_name_alternatives") or [])]
    return bool(wanted and any(normalise_name(x) == wanted for x in candidates if x))


def openalex_has_usyd_affiliation(oa_author: dict) -> bool:
    institutions = []
    for aff in oa_author.get("affiliations") or []:
        institutions.append((aff.get("institution") or {}).get("display_name"))
    for inst in oa_author.get("last_known_institutions") or []:
        institutions.append(inst.get("display_name"))
    return any("university of sydney" in clean_space(x).casefold() for x in institutions if x)


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
    issn = issn_l or (issns[0] if issns else "")
    eissn = issns[1] if len(issns) > 1 else ""

    percentile = work.get("citation_normalized_percentile") or {}
    percentile_value = percentile.get("value", "") if isinstance(percentile, dict) else ""

    landing = clean_space(loc.get("landing_page_url"))
    article_url = f"https://doi.org/{doi}" if doi else landing

    return {
        "espace_id": researcher["researcher_id"],
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
        "source": "OpenAlex supplementary (validated)",
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


def publication_key(row: dict[str, object]) -> tuple:
    doi = normalise_doi(str(row.get("doi") or ""))
    researcher_id = row.get("researcher_id")
    if doi:
        return ("doi", researcher_id, doi)
    return (
        "fallback",
        researcher_id,
        clean_space(str(row.get("title") or "")).casefold(),
        str(row.get("year") or ""),
    )


def dedupe_publications(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen = set()
    out = []
    for row in rows:
        key = publication_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def merge_source_and_openalex(source_rows: list[dict], oa_rows: list[dict]) -> list[dict]:
    """Keep the university source as canonical and use OpenAlex to fill blanks/add new works."""
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []

    for row in source_rows:
        key = publication_key(row)
        if key not in merged:
            merged[key] = dict(row)
            order.append(key)

    for oa in oa_rows:
        key = publication_key(oa)
        if key not in merged:
            merged[key] = dict(oa)
            order.append(key)
            continue

        base = merged[key]
        for field in ("citation_percentile", "cited_by_count", "fwci", "eissn", "openalex_author_id"):
            if not base.get(field) and oa.get(field) not in (None, ""):
                base[field] = oa[field]
        if "OpenAlex" not in str(base.get("source") or ""):
            base["source"] = f"{base.get('source') or 'Sydney Profiles'} + OpenAlex"

    return [merged[k] for k in order]


def write_csv(path: Path, rows: Iterable[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def collect_all(out_dir: Path, api_key: str = "", *, include_openalex: bool = False) -> tuple[list[dict], list[dict]]:
    staff: list[dict] = []
    source_publications: list[dict] = []

    for field, group_url in [
        ("Accounting", ACCOUNTING_EXPERTS_URL),
        ("Finance", FINANCE_EXPERTS_URL),
    ]:
        urls = collect_group_profile_urls(group_url)
        print(f"{field}: discovered {len(urls)} Sydney Profiles group members")

        for n, profile_url in enumerate(urls, 1):
            user = fetch_usyd_user(profile_url)
            if user is None:
                print(f"SKIP invalid/stale Sydney profile: {profile_url}")
                continue

            person = usyd_user_to_researcher(user, profile_url, field)
            staff.append(person)

            records = fetch_usyd_publication_records(profile_url)
            journal_records = [r for r in records if is_usyd_journal_article(r)]
            person_rows = [usyd_record_to_publication(r, person) for r in journal_records]
            source_publications.extend(person_rows)

            print(
                f"{field} {n}/{len(urls)}: {person['name']} | "
                f"{person['orcid'] or 'no ORCID'} | "
                f"{len(journal_records)}/{len(records)} journal outputs"
            )
            time.sleep(0.10)

    source_publications = dedupe_publications(source_publications)
    publications = list(source_publications)

    if include_openalex:
        if not api_key:
            raise RuntimeError("--include-openalex requires OPENALEX_API_KEY")

        by_researcher: dict[str, list[dict]] = {}
        for row in source_publications:
            by_researcher.setdefault(str(row["researcher_id"]), []).append(row)

        oa_rows: list[dict] = []
        for n, person in enumerate(staff, 1):
            if not person["orcid"]:
                print(f"SKIP OpenAlex (no ORCID): {person['name']}")
                continue

            oa = openalex_author_by_orcid(person["orcid"], api_key)
            if not oa:
                print(f"SKIP OpenAlex (ORCID not found): {person['name']}")
                continue
            if normalise_orcid(oa.get("orcid")) != person["orcid"] or not openalex_name_matches(oa, person):
                print(f"MANUAL OpenAlex author review (identity mismatch): {person['name']}")
                continue

            works = openalex_works_by_author(clean_space(oa.get("id")), api_key)
            journal_works = [w for w in works if is_journal_article(w)]

            source_dois = {
                normalise_doi(str(r.get("doi") or ""))
                for r in by_researcher.get(person["researcher_id"], [])
                if normalise_doi(str(r.get("doi") or ""))
            }
            oa_dois = {normalise_doi(w.get("doi")) for w in journal_works if normalise_doi(w.get("doi"))}
            verified = bool(source_dois & oa_dois) or openalex_has_usyd_affiliation(oa)
            if not verified:
                print(f"MANUAL OpenAlex author review (no DOI overlap/USyd affiliation): {person['name']}")
                continue

            person["openalex_author_id"] = clean_space(oa.get("id"))
            new_rows = [work_to_publication(w, person) for w in journal_works]
            oa_rows.extend(new_rows)
            print(f"OpenAlex {n}/{len(staff)}: {person['name']} -> {len(new_rows)} journal articles")
            time.sleep(0.05)

        publications = merge_source_and_openalex(source_publications, oa_rows)

    publications = dedupe_publications(publications)
    write_csv(out_dir / "usyd_staff.csv", staff, STAFF_COLUMNS)
    write_csv(out_dir / "usyd_publications.csv", publications, PUBLICATION_COLUMNS)
    return staff, publications


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="output")
    p.add_argument(
        "--include-openalex",
        action="store_true",
        help="After collecting Sydney Profiles publications, add validated OpenAlex supplementary works.",
    )
    args = p.parse_args()

    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if args.include_openalex and not api_key:
        raise SystemExit(
            "--include-openalex requires OPENALEX_API_KEY. Never commit the key; "
            "use a Codespaces secret or shell environment variable."
        )

    out = Path(args.out_dir)
    staff, pubs = collect_all(out, api_key, include_openalex=args.include_openalex)
    print(f"\nWrote {len(staff)} staff rows")
    print(f"Wrote {len(pubs)} publication rows")
    print(f"Files: {out/'usyd_staff.csv'} and {out/'usyd_publications.csv'}")


if __name__ == "__main__":
    main()
