"""University of Sydney adapter for the shared CITS3200 pipeline.

Sydney Profiles is the university-native source used here.  The public group
pages only render the first page of members, so roster discovery uses the
structured group-members endpoint with pagination.  Staff metadata and
research outputs are then read from the corresponding Sydney Profiles JSON
endpoints.

The adapter deliberately returns the shared ``core.schema`` shape and leaves
cross-source retrieval / ranking enrichment to ``run.py``.  In particular,
OpenAlex is *not* treated as an authoritative USyd publication source here;
it is handled later by the common retrieval/enrichment stages.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import requests

from core.schema import blank_pub, clean_journal
from core.titles import level, rank

UNIVERSITY = "University of Sydney"
ROR = "0384j8v12"

ACCOUNTING_EXPERTS_URL = "https://profiles.sydney.edu.au/groups/652444/experts"
FINANCE_EXPERTS_URL = "https://profiles.sydney.edu.au/groups/652446/experts"
TARGETS = [
    (ACCOUNTING_EXPERTS_URL, "Accounting"),
    (FINANCE_EXPERTS_URL, "Finance"),
]

GROUP_MEMBERS_API = "https://profiles.sydney.edu.au/api/users/membersOfGroup"
USER_API = "https://profiles.sydney.edu.au/api/users"
PUBLICATIONS_API = "https://profiles.sydney.edu.au/api/publications/linkedTo"

USER_AGENT = "CITS3200-Group20-USyd/1.0 (student project; conservative requests)"
ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-[\dX]{4}\b", re.I)

# One bad navigation slug should never become a researcher.  This was the
# source of the historical /cookiesettings false-positive.
NON_PROFILE_SLUGS = {
    "about", "contact", "cookiesettings", "disclaimer", "feedback",
    "privacy", "search",
}


def clean_space(value) -> str:
    return " ".join(str(value or "").split())


def normalise_orcid(value) -> str | None:
    if not value:
        return None
    m = ORCID_RE.search(str(value))
    return m.group(0).upper() if m else None


def normalise_doi(value) -> str | None:
    value = clean_space(value)
    if not value:
        return None
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    value = value.strip().rstrip(".,;:)").lower()
    return value or None


def profile_slug(profile_url: str) -> str:
    return urlparse(profile_url).path.strip("/").split("/")[0]


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
    """Return every canonical person profile URL in a Sydney Profiles group.

    The rendered page is paged and historically yielded only the first 25
    people, so this follows the API's ``pagination.total`` value until every
    page has been consumed.
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
        response = client.post(
            GROUP_MEMBERS_API,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        batch = data.get("resource") or []

        for member in batch:
            slug = clean_space(member.get("discoveryUrlId"))
            if not slug or slug.casefold() in NON_PROFILE_SLUGS or "/" in slug:
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


def fetch_user(
    profile_url: str,
    *,
    timeout: int = 30,
    session: requests.Session | None = None,
) -> dict | None:
    """Fetch one structured Sydney Profiles user; stale slugs are non-fatal."""
    slug = profile_slug(profile_url)
    client = session or requests.Session()
    response = client.get(
        f"{USER_API}/{slug}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _position_candidates(user: dict) -> list[str]:
    out: list[str] = []
    for position in user.get("positions") or []:
        candidate = clean_space(position.get("position"))
        if candidate and candidate not in out:
            out.append(candidate)
    for appointment in user.get("institutionalAppointments") or []:
        candidate = clean_space(appointment.get("position"))
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def user_to_staff(user: dict, profile_url: str, discipline: str) -> dict:
    """Map one Sydney user object to the shared staff contract.

    Administrative current roles such as ``Head of Discipline`` do not encode
    an academic level.  Keep that raw current role in ``title``, but search the
    structured appointments for the first title that maps onto the academic
    ladder so ``title_clean``/``level_code`` are not needlessly blank.
    """
    name = clean_space(
        user.get("firstNameLastName")
        or f"{clean_space(user.get('firstName'))} {clean_space(user.get('lastName'))}"
    )
    candidates = _position_candidates(user)
    raw_title = candidates[0] if candidates else None

    academic_title = None
    for candidate in candidates:
        mapped = rank(candidate)
        if mapped:
            academic_title = mapped
            break

    raw_orcid = user.get("orcid")
    if isinstance(raw_orcid, dict):
        raw_orcid = raw_orcid.get("value") or raw_orcid.get("uri")

    slug = profile_slug(profile_url)
    return {
        "name": name,
        "name_clean": name,
        "university": UNIVERSITY,
        "discipline": discipline,
        "profile_url": f"https://profiles.sydney.edu.au/{slug}",
        "title": raw_title,
        "title_clean": academic_title,
        "level_code": level(academic_title),
        "prefix": None,
        "source_id": slug,
        "orcid": normalise_orcid(raw_orcid),
    }


def fetch_publication_records(
    profile_url: str,
    *,
    timeout: int = 45,
    per_page: int = 25,
    session: requests.Session | None = None,
) -> list[dict]:
    """Fetch all Research Outputs for a profile through the public JSON API."""
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
        response = client.post(
            PUBLICATIONS_API,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        batch = data.get("resource") or []
        records.extend(batch)

        pagination = data.get("pagination") or {}
        total = int(pagination.get("total") or len(records))
        if not batch or len(records) >= total:
            break
        start += len(batch)
        time.sleep(0.05)

    return records


def _publication_type(record: dict) -> str:
    raw = clean_space(record.get("objectTypeDisplayName")).casefold()
    if raw == "journal article" or "journal article" in raw:
        return "Journal Article"
    if "preprint" in raw:
        return "Preprint"
    if "conference" in raw:
        return "Conference Paper"
    if "book chapter" in raw or "chapter" in raw:
        return "Book Chapter"
    if raw == "book" or raw.startswith("book "):
        return "Book"
    if "working paper" in raw:
        return "Working Paper"
    if "report" in raw:
        return "Research Report"
    if "thesis" in raw:
        return "Thesis"
    return "Other"


def _issns(record: dict) -> list[str]:
    raw = record.get("issn")
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = re.split(r"[,;]", str(raw))
    out = []
    for value in values:
        value = clean_space(value)
        if value and value not in out:
            out.append(value)
    return out


def record_to_publication(record: dict, person: dict) -> dict:
    authors_raw = record.get("authors") or []
    authors = [
        clean_space(a.get("fullName") or a.get("firstNameLastName") or a.get("nameShortFormat"))
        for a in authors_raw
    ]
    authors = [a for a in authors if a]

    year = (
        (record.get("publicationDate") or {}).get("year")
        or (record.get("date1") or {}).get("year")
    )
    doi = normalise_doi(record.get("doi"))
    slug = person["source_id"]
    profile_outputs = f"https://profiles.sydney.edu.au/{slug}/publications"
    publisher_url = clean_space(record.get("publisherUrl")) or None
    journal = clean_journal(record.get("journal") or record.get("parentTitle"))
    pub_type = _publication_type(record)

    # The client wants journal publications only.  A handful of Sydney
    # Profiles records are labelled "Journal article" even though the native
    # record supplies neither a year nor a journal/parent title (for example
    # old working-paper/SSRN records).  With no journal identity and no year
    # there is not enough native evidence to export them as journal articles,
    # so leave them visible to the pipeline as Other instead of guessing.
    if pub_type == "Journal Article" and not year and not journal:
        pub_type = "Other"

    return blank_pub(
        name=person["name_clean"],
        source_id=slug,
        title=clean_space(record.get("title")) or None,
        year=str(year) if year else None,
        type=pub_type,
        n_authors=len(authors) or None,
        authors="; ".join(authors) or None,
        issns=_issns(record),
        journal=journal,
        publisher=clean_space(record.get("publisher")) or None,
        doi=doi,
        link=(f"https://doi.org/{doi}" if doi else publisher_url or profile_outputs),
        source="Sydney Profiles",
        publication_id=clean_space(record.get("discoveryId") or record.get("objectId")) or None,
    )


def _pub_key(pub: dict) -> tuple:
    """Deduplicate only within a researcher, never globally across coauthors."""
    doi = normalise_doi(pub.get("doi"))
    if doi:
        return (pub.get("name"), "doi", doi)
    return (
        pub.get("name"),
        "fallback",
        clean_space(pub.get("title")).casefold(),
        str(pub.get("year") or ""),
    )


def dedupe_publications(pubs: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for pub in pubs:
        key = _pub_key(pub)
        if key in seen:
            continue
        seen.add(key)
        out.append(pub)
    return out


def collect(verbose: bool = True, refresh: bool = False):
    """Return ``(staff, publications)`` satisfying ``core.schema``.

    Sydney Profiles is queried live on every call and currently has no local
    adapter cache of its own.  ``refresh`` is therefore accepted for the common
    ``run.py`` interface but requires no special action here.
    """
    del refresh  # interface compatibility; requests below are always live

    session = requests.Session()
    staff: list[dict] = []
    pubs: list[dict] = []
    seen_profiles: set[str] = set()

    for group_url, discipline in TARGETS:
        urls = collect_group_profile_urls(group_url, session=session)
        if verbose:
            print(f"  {discipline}: {len(urls)} Sydney Profiles members")

        for i, profile_url in enumerate(urls, 1):
            slug = profile_slug(profile_url)
            if slug in seen_profiles:
                if verbose:
                    print(f"  {discipline} {i}/{len(urls)}: {slug} already listed in another target")
                continue

            user = fetch_user(profile_url, session=session)
            if user is None:
                if verbose:
                    print(f"  {discipline} {i}/{len(urls)}: stale profile {slug}, skipped")
                continue

            person = user_to_staff(user, profile_url, discipline)
            seen_profiles.add(slug)
            staff.append(person)

            raw = fetch_publication_records(profile_url, session=session)
            mapped = [record_to_publication(record, person) for record in raw]
            pubs.extend(mapped)

            if verbose:
                journals = sum(1 for row in mapped if row.get("type") == "Journal Article")
                print(
                    f"  {discipline} {i}/{len(urls)}: {person['name_clean']} — "
                    f"{journals}/{len(mapped)} journal outputs"
                )
            time.sleep(0.05)

    pubs = dedupe_publications(pubs)
    if verbose:
        print(f"  USyd: {len(staff)} staff, {len(pubs)} native research outputs")
    return staff, pubs
