"""University of Melbourne Accounting and Finance adapter.

``collect()`` returns the shared team contract: a staff list and a list of
researcher-publication rows. Current staff are taken from the official FBE
directory. Publications and persistent author identifiers come first from the
official Minerva Access API; staff absent from Minerva are resolved
conservatively against OpenAlex by exact name plus the UniMelb ROR so the
shared retrieval stage can search for their publications.

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

from core.config import OA_HEADERS, ORCID_HEADERS, openalex_budget
from core.http import cached_get


UNIVERSITY = "University of Melbourne"
ROR = "01ej9dk98"
ROOT = Path(__file__).resolve().parents[1]
IDENTITY_OVERRIDES_FILE = ROOT / "data" / "unimelb_identity_overrides.csv"
API_ROOT = "https://minerva-access.unimelb.edu.au/server/api"
SEARCH_URL = f"{API_ROOT}/discover/search/objects"
USER_AGENT = "CITS3200-Team20/3.0 (academic research; respectful automated collection)"
OPENALEX_AUTHORS_URL = "https://api.openalex.org/authors"
ORCID_EXPANDED_SEARCH_URL = "https://pub.orcid.org/v3.0/expanded-search/"

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


def name_aliases(value: Any) -> list[str]:
    """Return only mechanically defensible aliases for a printed staff name.

    The FBE directory frequently prints both a legal given name and a nickname,
    for example ``Tongqing (Tony) Ding``.  Minerva and OpenAlex commonly use
    either ``Tongqing Ding`` or ``Tony Ding``.  Treating the parenthesised word
    as a mandatory third name made otherwise exact identities invisible.

    No initials are invented and no fuzzy comparison is used here.  A common
    name still has to resolve to one candidate at the University of Melbourne
    (or to candidates carrying a single ORCID) before it is accepted.
    """
    raw = clean(value)
    if not raw:
        return []
    without_parenthetical = clean(re.sub(r"\([^)]*\)", " ", raw))
    aliases = [without_parenthetical, raw]
    outside_tokens = without_parenthetical.split()
    family = outside_tokens[-1] if outside_tokens else ""
    for group in re.findall(r"\(([^)]*)\)", raw):
        nickname = clean(group)
        if nickname and family:
            aliases.append(f"{nickname} {family}")
    return list(dict.fromkeys(alias for alias in aliases if alias))


def person_keys(value: Any) -> set[str]:
    return {key for alias in name_aliases(value) if (key := person_key(alias))}


def _one_token_name_extension(official_keys: set[str], candidate_keys: set[str]) -> bool:
    """Whether a candidate adds exactly one name token to an official name.

    This covers a profile printed as ``Flora Kuang`` while the bibliographic
    identity is ``Yu Flora Kuang``.  It is only used when there is exactly one
    such candidate at UniMelb; it is not a general partial/fuzzy match.
    """
    for official in official_keys:
        official_tokens = set(official.split())
        if len(official_tokens) < 2:
            continue
        for candidate in candidate_keys:
            candidate_tokens = set(candidate.split())
            if official_tokens < candidate_tokens and len(candidate_tokens - official_tokens) == 1:
                return True
    return False


def _middle_initial_compatible(official_keys: set[str], candidate_keys: set[str]) -> bool:
    def without_initials(key: str) -> tuple[str, ...]:
        return tuple(token for token in key.split() if len(token) > 1)

    official = {without_initials(key) for key in official_keys}
    candidate = {without_initials(key) for key in candidate_keys}
    return bool(official.intersection(candidate))


def _openalex_names(author: dict[str, Any]) -> list[str]:
    return [
        name for name in [author.get("display_name"), *(author.get("display_name_alternatives") or [])]
        if name
    ]


def _openalex_rors(author: dict[str, Any]) -> set[str]:
    """All institutions OpenAlex has associated with an author over time."""
    found: set[str] = set()
    for institution in author.get("last_known_institutions") or []:
        if institution.get("ror"):
            found.add(institution["ror"].rsplit("/", 1)[-1].lower())
    for affiliation in author.get("affiliations") or []:
        institution = affiliation.get("institution") or {}
        if institution.get("ror"):
            found.add(institution["ror"].rsplit("/", 1)[-1].lower())
    return found


def load_identity_overrides(path: Path = IDENTITY_OVERRIDES_FILE) -> list[dict[str, Any]]:
    """Load only explicitly approved, profile-keyed identity decisions."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    approved = []
    for row in rows:
        if clean(row.get("review_decision")).lower() not in {"approve", "approved", "accept"}:
            continue
        item = {key: clean(value) for key, value in row.items()}
        item["openalex_author_ids"] = [
            value for value in re.split(r"[;,\s]+", item.get("openalex_author_ids", "")) if value
        ]
        approved.append(item)
    return approved


def _apply_manual_retrieval_overrides(
    records: list[dict[str, Any]], identity_overrides: Iterable[dict[str, Any]]
) -> int:
    by_profile = {
        (item.get("discipline"), item.get("profile_url")): item
        for item in identity_overrides
        if item.get("profile_url")
    }
    applied = 0
    for person in records:
        override = by_profile.get((person.get("discipline"), person.get("profile_url")))
        if not override or person_key(override.get("name")) != person_key(person.get("name_clean")):
            continue
        orcid = clean(override.get("orcid")).removeprefix("https://orcid.org/")
        openalex_ids = [
            clean(value).rsplit("/", 1)[-1]
            for value in (override.get("openalex_author_ids") or [])
            if clean(value)
        ]
        if orcid:
            person["orcid"] = orcid
        if openalex_ids:
            person["openalex_author_ids"] = sorted(set(openalex_ids))
        if orcid or openalex_ids:
            person["identity_source"] = "manual_verified_override"
            person["identity_confidence"] = "high_with_discipline_screen"
            person["identity_evidence_url"] = override.get("evidence_url") or None
            applied += 1
    return applied


def _orcid_names(candidate: dict[str, Any]) -> list[str]:
    names = [candidate.get("credit-name")]
    given = clean(candidate.get("given-names"))
    family = clean(candidate.get("family-names"))
    if given and family:
        names.append(f"{given} {family}")
    names.extend(candidate.get("other-name") or [])
    return [name for name in names if name]


def _has_unimelb_orcid_affiliation(candidate: dict[str, Any]) -> bool:
    return any(
        "university of melbourne" in normalize_text(institution)
        for institution in (candidate.get("institution-name") or [])
    )


def _add_orcid_ids(
    records: list[dict[str, Any]], *, refresh: bool = False, verbose: bool = True
) -> dict[str, Any]:
    """Resolve an ORCID only from exact name plus a UniMelb affiliation.

    ORCID is self-maintained and therefore incomplete, but a positive match is
    stronger identity evidence than an inferred OpenAlex author cluster.  An
    exact name without a University of Melbourne affiliation is reported as a
    candidate, never accepted automatically.
    """
    stats: dict[str, Any] = {
        "queries": 0, "resolved": 0, "ambiguous": 0, "not_found": 0,
        "errors": [], "skipped_existing_orcid": 0,
        "aborted_after_repeated_errors": 0,
    }
    consecutive_errors = 0
    for index, person in enumerate(records):
        person["orcid_identity_candidate_count"] = 0
        person["orcid_review_candidates"] = []
        if person.get("orcid"):
            person["orcid_identity_status"] = "not_needed_existing_orcid"
            stats["skipped_existing_orcid"] += 1
            continue

        aliases = name_aliases(person.get("name_clean"))
        query_name = aliases[0] if aliases else person.get("name_clean")
        stats["queries"] += 1
        try:
            data = cached_get(
                ORCID_EXPANDED_SEARCH_URL,
                params={"q": f'given-and-family-names:"{query_name}"', "rows": 50},
                headers=ORCID_HEADERS,
                sleep=0.2,
                force=refresh,
            )
        except Exception as error:
            person["orcid_identity_status"] = "lookup_error"
            stats["errors"].append({"name": person["name_clean"], "error": str(error)})
            consecutive_errors += 1
            if consecutive_errors >= 3:
                remaining = 0
                for pending in records[index + 1:]:
                    pending.setdefault("orcid_identity_candidate_count", 0)
                    if not pending.get("orcid"):
                        pending["orcid_identity_status"] = "not_attempted_after_repeated_errors"
                        remaining += 1
                stats["aborted_after_repeated_errors"] = remaining
                break
            continue

        consecutive_errors = 0
        official_keys = person_keys(person.get("name_clean"))
        candidates = list(data.get("expanded-result") or [])

        def same_person_name(candidate: dict[str, Any]) -> bool:
            candidate_keys = {
                key for candidate_name in _orcid_names(candidate)
                for key in person_keys(candidate_name)
            }
            return (
                bool(official_keys.intersection(candidate_keys))
                or _middle_initial_compatible(official_keys, candidate_keys)
            )

        def matching_at_unimelb(source: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
            matched: list[dict[str, Any]] = []
            seen: set[str] = set()
            for candidate in source:
                candidate_orcid = candidate.get("orcid-id")
                if (
                    same_person_name(candidate)
                    and _has_unimelb_orcid_affiliation(candidate)
                    and candidate_orcid
                    and candidate_orcid not in seen
                ):
                    matched.append(candidate)
                    seen.add(candidate_orcid)
            return matched

        exact_at_unimelb = matching_at_unimelb(candidates)
        name_parts = query_name.split()
        if not exact_at_unimelb and len(name_parts) >= 2:
            # ORCID's combined-name field is strict about middle initials.
            # A fielded query can find "Patrick Kelly" when the directory says
            # "Patrick J. Kelly", while the institution requirement still
            # prevents a bare common-name match.
            fielded_query = (
                f'given-names:{name_parts[0]} AND family-name:{name_parts[-1]} '
                'AND affiliation-org-name:"University of Melbourne"'
            )
            try:
                stats["queries"] += 1
                fielded = cached_get(
                    ORCID_EXPANDED_SEARCH_URL,
                    params={"q": fielded_query, "rows": 50},
                    headers=ORCID_HEADERS,
                    sleep=0.2,
                    force=refresh,
                )
                fielded_candidates = fielded.get("expanded-result") or []
                candidates.extend(fielded_candidates)
                exact_at_unimelb = matching_at_unimelb(fielded_candidates)
            except Exception as error:
                stats["errors"].append({
                    "name": person["name_clean"], "query": "fielded_fallback", "error": str(error)
                })

        person["orcid_review_candidates"] = [{
            "orcid": candidate.get("orcid-id"),
            "credit_name": candidate.get("credit-name"),
            "given_names": candidate.get("given-names"),
            "family_names": candidate.get("family-names"),
            "institutions": candidate.get("institution-name") or [],
            "has_unimelb_affiliation": _has_unimelb_orcid_affiliation(candidate),
        } for candidate in {
            candidate.get("orcid-id"): candidate
            for candidate in candidates
            if candidate.get("orcid-id") and same_person_name(candidate)
        }.values()]

        orcids = sorted({
            candidate.get("orcid-id") for candidate in exact_at_unimelb
            if candidate.get("orcid-id")
        })
        person["orcid_identity_candidate_count"] = len(orcids)
        if len(orcids) == 1:
            person["orcid"] = orcids[0]
            person["orcid_identity_status"] = "verified_exact_name_and_unimelb_affiliation"
            if not person.get("identity_source"):
                person["identity_source"] = "orcid_exact_name_and_unimelb_affiliation"
                person["identity_confidence"] = "high_with_discipline_screen"
            stats["resolved"] += 1
        elif len(orcids) > 1:
            person["orcid_identity_status"] = "ambiguous"
            stats["ambiguous"] += 1
        else:
            person["orcid_identity_status"] = "not_found"
            stats["not_found"] += 1

    if verbose:
        print(
            f"  ORCID identities: {stats['resolved']} newly resolved; "
            f"{stats['ambiguous']} ambiguous, {stats['not_found']} not found, "
            f"{len(stats['errors'])} lookup errors"
        )
    return stats


def _add_openalex_ids(
    records: list[dict[str, Any]], *, refresh: bool = False, verbose: bool = True
) -> dict[str, Any]:
    """Resolve staff omitted by Minerva to verified OpenAlex author IDs.

    Minerva is still the preferred identity source.  We search OpenAlex only
    when the official roster plus Minerva did not yield an ORCID, and accept a
    result only when an exact, normalised name alias is tied to the UniMelb
    ROR.  Multiple candidates with different ORCIDs are left unresolved.

    This function records identifiers on staff; the shared ``info/openalex``
    stage retrieves the works.  Keeping retrieval there avoids a second copy
    of the OpenAlex publication parser in this university adapter.
    """
    select = (
        "id,display_name,display_name_alternatives,orcid,works_count,"
        "last_known_institutions,affiliations"
    )
    stats: dict[str, Any] = {
        "queries": 0,
        "resolved": 0,
        "ambiguous": 0,
        "not_found": 0,
        "errors": [],
        "skipped_existing_orcid": 0,
        "skipped_existing_identifier": 0,
        "aborted_after_repeated_errors": 0,
    }

    consecutive_errors = 0
    for index, person in enumerate(records):
        person.setdefault("openalex_author_ids", [])
        person["openalex_identity_candidate_count"] = 0
        person["openalex_review_candidates"] = []
        if person.get("orcid") or person.get("openalex_author_ids"):
            person["openalex_identity_status"] = "not_needed_verified_identifier"
            if person.get("orcid"):
                stats["skipped_existing_orcid"] += 1
            stats["skipped_existing_identifier"] += 1
            continue

        aliases = name_aliases(person.get("name_clean"))
        query_name = aliases[0] if aliases else person.get("name_clean")
        stats["queries"] += 1
        try:
            data = cached_get(
                OPENALEX_AUTHORS_URL,
                params={"search": query_name, "per-page": 25, "select": select},
                headers=OA_HEADERS,
                sleep=0.2,
                force=refresh,
            )
        except Exception as error:
            person["openalex_identity_status"] = "lookup_error"
            stats["errors"].append({"name": person["name_clean"], "error": str(error)})
            if verbose:
                print(f"  {person['name_clean']}: OpenAlex {type(error).__name__}: {error}")
            consecutive_errors += 1
            if consecutive_errors >= 3:
                remaining = 0
                for pending in records[index + 1:]:
                    pending.setdefault("openalex_author_ids", [])
                    pending.setdefault("openalex_identity_candidate_count", 0)
                    if not pending.get("orcid"):
                        pending["openalex_identity_status"] = "not_attempted_after_repeated_errors"
                        remaining += 1
                stats["aborted_after_repeated_errors"] = remaining
                break
            continue

        consecutive_errors = 0

        official_keys = person_keys(person.get("name_clean"))
        search_results = data.get("results") or []
        at_unimelb = [
            author for author in search_results
            if ROR.lower() in _openalex_rors(author)
        ]
        keys_by_id = {
            author.get("id"): {
                key
                for candidate_name in _openalex_names(author)
                for key in person_keys(candidate_name)
            }
            for author in at_unimelb
            if author.get("id")
        }
        all_keys_by_id = {
            author.get("id"): {
                key
                for candidate_name in _openalex_names(author)
                for key in person_keys(candidate_name)
            }
            for author in search_results
            if author.get("id")
        }
        review_matches = [
            author for author in search_results
            if (
                official_keys.intersection(all_keys_by_id.get(author.get("id"), set()))
                or _one_token_name_extension(
                    official_keys, all_keys_by_id.get(author.get("id"), set())
                )
            )
        ]
        person["openalex_review_candidates"] = [{
            "openalex_author_id": author["id"].rsplit("/", 1)[-1],
            "display_name": author.get("display_name"),
            "orcid": (author.get("orcid") or "").rsplit("/", 1)[-1] or None,
            "works_count": author.get("works_count"),
            "has_unimelb_affiliation": ROR.lower() in _openalex_rors(author),
        } for author in review_matches]
        exact = [
            author for author in at_unimelb
            if official_keys.intersection(keys_by_id.get(author.get("id"), set()))
        ]
        match_kind = "exact_name"
        if not exact:
            extended = [
                author for author in at_unimelb
                if _one_token_name_extension(
                    official_keys, keys_by_id.get(author.get("id"), set())
                )
            ]
            if len(extended) == 1:
                exact = extended
                match_kind = "one_token_name_extension"
        exact = list({author.get("id"): author for author in exact if author.get("id")}.values())
        person["openalex_identity_candidate_count"] = len(exact)

        if not exact:
            person["openalex_identity_status"] = "not_found"
            stats["not_found"] += 1
            continue

        distinct_orcids = {
            author["orcid"].rsplit("/", 1)[-1]
            for author in exact
            if author.get("orcid")
        }
        if len(exact) > 1 and len(distinct_orcids) != 1:
            person["openalex_identity_status"] = "ambiguous"
            stats["ambiguous"] += 1
            if verbose:
                print(
                    f"  {person['name_clean']}: {len(exact)} exact UniMelb OpenAlex "
                    "candidates; identifiers left blank"
                )
            continue

        person["openalex_author_ids"] = sorted(
            {author["id"].rsplit("/", 1)[-1] for author in exact}
        )
        if len(distinct_orcids) == 1:
            person["orcid"] = next(iter(distinct_orcids))
        person["openalex_identity_status"] = f"verified_{match_kind}_and_unimelb_affiliation"
        if not person.get("identity_source"):
            person["identity_source"] = f"openalex_{match_kind}_and_unimelb_affiliation"
            person["identity_confidence"] = "high_with_discipline_screen"
        stats["resolved"] += 1

    if verbose:
        reachable = sum(bool(person.get("orcid") or person.get("openalex_author_ids")) for person in records)
        print(
            f"  OpenAlex identities: {stats['resolved']} newly resolved; "
            f"{reachable}/{len(records)} staff reachable by ORCID or author ID; "
            f"{stats['ambiguous']} ambiguous, {stats['not_found']} not found, "
            f"{len(stats['errors'])} lookup errors"
        )
        print(
            f"  {stats['queries']} author searches, about ${stats['queries'] * 0.001:.3f} "
            f"of today's ${openalex_budget():.2f} budget; responses are cached"
        )
    return stats


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
            identity_id = identity.get("internal_id") or identity.get("orcid") or identity.get("raw")
            for key in person_keys(identity.get("name")):
                by_name.setdefault(key, {})[identity_id] = identity
    override_map = {
        (item.get("discipline"), item.get("profile_url")): item
        for item in identity_overrides
        if item.get("profile_url")
    }
    identities = []
    for person in staff:
        candidate_map: dict[str, dict[str, str]] = {}
        for key in person_keys(person["name_clean"]):
            candidate_map.update(by_name.get(key, {}))
        candidates = list(candidate_map.values())
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
            "identity_source": "minerva_seed_exact_name" if unambiguous and (internal_ids or orcids) else None,
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
            person["identity_source"] = identity.get("identity_source")
            person["identity_confidence"] = "high"
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


def repository_author_names(value: Any) -> list[str]:
    """Exact Minerva author spellings worth trying for an official name."""
    names: list[str] = []
    for alias in name_aliases(value):
        parts = alias.split()
        if len(parts) >= 2:
            names.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        names.append(alias)
    return list(dict.fromkeys(name for name in names if name))


def _discover_minerva_identity(
    client: HttpClient, identity: dict[str, Any]
) -> tuple[list[dict[str, str]], int]:
    """Find exact internal author identifiers without relying on a seed set.

    DSpace exposes an exact author facet but no standalone author directory.
    We therefore try only deterministic renderings of the official name, then
    require the returned item's internal-author metadata to match one of the
    same exact aliases.  A common name yielding several IDs remains ambiguous.
    """
    official_keys = person_keys(identity["person"]["name_clean"])
    candidates: dict[str, dict[str, str]] = {}
    queries = 0
    for author_name in repository_author_names(identity["person"]["name_clean"]):
        page, total_pages = 0, 1
        while page < total_pages:
            queries += 1
            probe = {"repository_author_name": author_name}
            response = client.get_json(_identity_search_url(probe, page))
            result = response.get("_embedded", {}).get("searchResult", {})
            total_pages = result.get("page", {}).get("totalPages", 1)
            for wrapper in result.get("_embedded", {}).get("objects", []):
                publication = _parse_item(wrapper)
                if not publication:
                    continue
                for author in publication.get("internal_authors", []):
                    if not official_keys.intersection(person_keys(author.get("name"))):
                        continue
                    identity_id = author.get("internal_id") or author.get("orcid") or author.get("raw")
                    if identity_id:
                        candidates[identity_id] = author
            page += 1
    return list(candidates.values()), queries


def _resolve_missing_minerva_identities(
    client: HttpClient,
    identities: list[dict[str, Any]],
    *,
    max_workers: int,
    verbose: bool,
) -> dict[str, Any]:
    """Resolve staff absent from the two small departmental seed collections."""
    unresolved = [identity for identity in identities if identity["confidence"] != "high"]
    stats: dict[str, Any] = {
        "attempted": len(unresolved), "resolved": 0, "ambiguous": 0,
        "not_found": 0, "queries": 0, "errors": [],
    }
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 3))) as pool:
        jobs = {
            pool.submit(_discover_minerva_identity, client, identity): identity
            for identity in unresolved
        }
        for future in as_completed(jobs):
            identity = jobs[future]
            person = identity["person"]
            try:
                candidates, queries = future.result()
                stats["queries"] += queries
            except Exception as error:
                stats["errors"].append({"name": person["name_clean"], "error": str(error)})
                continue

            internal_ids = sorted({item["internal_id"] for item in candidates if item.get("internal_id")})
            orcids = sorted({item["orcid"] for item in candidates if item.get("orcid")})
            unambiguous = bool(candidates) and len(internal_ids) <= 1 and len(orcids) <= 1
            if unambiguous and (internal_ids or orcids):
                identity.update({
                    "internal_id": internal_ids[0] if internal_ids else "",
                    "orcid": orcids[0] if orcids else "",
                    "repository_author_name": candidates[0].get("name", ""),
                    "candidate_count": len(candidates),
                    "confidence": "high",
                    "identity_source": "minerva_repository_exact_author_discovery",
                })
                person["source_id"] = identity["internal_id"] or None
                person["orcid"] = person.get("orcid") or identity["orcid"] or None
                person["identity_source"] = identity["identity_source"]
                person["identity_confidence"] = "high"
                stats["resolved"] += 1
            elif candidates:
                identity["candidate_count"] = len(candidates)
                identity["confidence"] = "ambiguous"
                stats["ambiguous"] += 1
            else:
                stats["not_found"] += 1

    if verbose:
        print(
            f"  Minerva identity discovery: {stats['resolved']} newly resolved from "
            f"{stats['attempted']} staff; {stats['ambiguous']} ambiguous, "
            f"{stats['not_found']} not found, {len(stats['errors'])} errors"
        )
    return stats


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


def _review_guidance(identity: dict[str, Any]) -> dict[str, str | None]:
    person = identity["person"]
    title = person.get("title")
    ambiguous = (
        identity.get("confidence") == "ambiguous"
        or person.get("orcid_identity_status") == "ambiguous"
        or person.get("openalex_identity_status") == "ambiguous"
    )
    teaching_role = bool(re.search(
        r"teaching|education.focus|\btutor\b|assistant lecturer|business manager",
        title or "",
        re.I,
    ))
    if ambiguous:
        priority = "high"
        action = (
            "Compare candidate publications and identifiers with the official profile; "
            "approve only an identifier supported by direct evidence."
        )
    elif teaching_role:
        priority = "low"
        action = (
            "Keep the official staff row usable for headcount. Leave publication coverage "
            "as unresolved unless a CV, ORCID, or profile supplies direct evidence."
        )
    else:
        priority = "medium"
        action = (
            "Check the official profile or CV for an ORCID/author identifier, then record "
            "an approved override with its evidence URL."
        )
    return {"job_title": title, "review_priority": priority, "recommended_action": action}


def _collect_live(
    disciplines: Iterable[str], *, refresh: bool, max_workers: int, limit_staff: int | None,
    verbose: bool, identity_overrides: Iterable[dict[str, Any]] = (),
):
    client = HttpClient(refresh=refresh)
    staff: list[dict[str, Any]] = []
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
        if limit_staff is not None and len(staff) >= limit_staff:
            break

    # Build the identity index from both departmental collections.  A current
    # Accounting staff member can have older Finance-affiliated deposits (and
    # vice versa); limiting their identity lookup to only the current
    # department silently made those people disappear.
    all_seed = [publication for discipline_seed in seeds.values() for publication in discipline_seed]
    identities = _build_identities(staff, all_seed, identity_overrides)
    minerva_discovery_stats = _resolve_missing_minerva_identities(
        client, identities, max_workers=max_workers, verbose=verbose
    )
    manual_retrieval_overrides_applied = _apply_manual_retrieval_overrides(
        staff, identity_overrides
    )
    orcid_identity_stats = _add_orcid_ids(staff, refresh=refresh, verbose=verbose)
    openalex_identity_stats = _add_openalex_ids(staff, refresh=refresh, verbose=verbose)

    # The identity lookups above already require the official staff name and a
    # University of Melbourne affiliation (or a manually reviewed override).
    # Once the person is identified, collect their whole publication career.
    # Restricting every work to the UniMelb ROR would omit papers written at a
    # previous employer and substantially undercount current staff output.
    for person in staff:
        if person.get("orcid") or person.get("openalex_author_ids"):
            person["retrieve_all_career_works"] = True

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
        "minerva_identity_discovery": minerva_discovery_stats,
        "orcid_identity_resolution": orcid_identity_stats,
        "openalex_identity_resolution": openalex_identity_stats,
        "staff_reachable_by_supplementary_orcid_or_openalex_author_id": sum(
            bool(person.get("orcid") or person.get("openalex_author_ids")) for person in staff
        ),
        "staff_without_any_verified_retrieval_identity": sum(
            not (person.get("source_id") or person.get("orcid") or person.get("openalex_author_ids"))
            for person in staff
        ),
        "departmental_seed_publications": seed_counts,
        "departmental_seed_reconciliation": seed_reconciliation,
        "exact_seed_relationships": seed_relationships,
        "researcher_publication_links": len(pubs),
        "live_researcher_searches": len(accepted),
        "manual_identity_overrides_applied": sum(
            identity.get("identity_source") == "manual_verified_override" for identity in identities
        ),
        "manual_retrieval_overrides_applied": manual_retrieval_overrides_applied,
        "search_failures": failures + aborted_searches,
        "searches_aborted_by_circuit_breaker": len(aborted_searches),
        "attribution_rule": "exact Minerva internal author ID or ORCID only",
        "refresh_note": "all records come from the live/cached official endpoints; no previous project dataset is read",
        "minerva_identity_review_queue": [
            {
                "name": identity["person"]["name_clean"],
                "discipline": identity["person"]["discipline"],
                "profile_url": identity["person"]["profile_url"],
                "minerva_identity_confidence": identity["confidence"],
                "minerva_candidate_count": identity["candidate_count"],
                "orcid_identity_status": identity["person"].get("orcid_identity_status"),
                "orcid_candidate_count": identity["person"].get("orcid_identity_candidate_count", 0),
                "orcid_review_candidates": identity["person"].get("orcid_review_candidates", []),
                "openalex_identity_status": identity["person"].get("openalex_identity_status"),
                "openalex_candidate_count": identity["person"].get("openalex_identity_candidate_count", 0),
                "openalex_review_candidates": identity["person"].get("openalex_review_candidates", []),
            }
            for identity in identities
            if identity["confidence"] != "high"
        ],
        # This is the actionable queue.  A missing Minerva identity is no
        # longer labelled as zero publications when OpenAlex independently
        # supplied a verified author identifier.
        "identity_review_queue": [
            {
                "name": identity["person"]["name_clean"],
                "discipline": identity["person"]["discipline"],
                "profile_url": identity["person"]["profile_url"],
                "minerva_identity_confidence": identity["confidence"],
                "minerva_candidate_count": identity["candidate_count"],
                "orcid_identity_status": identity["person"].get("orcid_identity_status"),
                "orcid_candidate_count": identity["person"].get("orcid_identity_candidate_count", 0),
                "orcid_review_candidates": identity["person"].get("orcid_review_candidates", []),
                "openalex_identity_status": identity["person"].get("openalex_identity_status"),
                "openalex_candidate_count": identity["person"].get("openalex_identity_candidate_count", 0),
                "openalex_review_candidates": identity["person"].get("openalex_review_candidates", []),
                "reason": "no verified Minerva, ORCID, or OpenAlex author identity",
                **_review_guidance(identity),
            }
            for identity in identities
            if not (
                identity["confidence"] == "high"
                or identity["person"].get("orcid")
                or identity["person"].get("openalex_author_ids")
            )
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
    identity_overrides: Iterable[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(staff, publications)`` satisfying the shared team contract."""
    selected = tuple(disciplines)
    unknown = set(selected) - set(TARGETS)
    if unknown:
        raise ValueError(f"unknown disciplines: {sorted(unknown)}")
    selected_overrides = (
        load_identity_overrides() if identity_overrides is None else list(identity_overrides)
    )
    staff, pubs, quality = _collect_live(
        selected,
        refresh=refresh,
        max_workers=max_workers,
        limit_staff=limit_staff,
        verbose=verbose,
        identity_overrides=selected_overrides,
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
    write_quality_report(output_dir)


def write_quality_report(output_dir: Path) -> None:
    """Write machine-readable quality evidence and an actionable review CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "unimelb_adapter_quality.json").write_text(
        json.dumps(LAST_QUALITY, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    queue = LAST_QUALITY.get("identity_review_queue") or []
    columns = [
        "name", "discipline", "job_title", "profile_url", "review_priority",
        "reason", "recommended_action",
        "minerva_identity_confidence", "minerva_candidate_count",
        "orcid_identity_status", "orcid_candidate_count", "orcid_review_candidates",
        "openalex_identity_status", "openalex_candidate_count", "openalex_review_candidates",
        "review_decision", "review_notes",
    ]
    with (output_dir / "unimelb_identity_review.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in queue:
            writer.writerow({
                **item,
                "orcid_review_candidates": json.dumps(
                    item.get("orcid_review_candidates") or [], ensure_ascii=False
                ),
                "openalex_review_candidates": json.dumps(
                    item.get("openalex_review_candidates") or [], ensure_ascii=False
                ),
                "review_decision": "",
                "review_notes": "",
            })


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
