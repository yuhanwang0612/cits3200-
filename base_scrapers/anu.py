"""Australian National University adapter.

WHERE THE DATA COMES FROM
--------------------------
Everything here comes from the two ANU College of Business & Economics staff
directories that cover accounting and finance — the Research School of
Accounting (RSA) and the Research School of Finance, Actuarial Studies &
Statistics (RSFAS, finance area only) — and each academic's own profile page
on those two sites. This adapter does not scrape those pages itself: it
reuses `anu_scraper.py` at the repo root, which was written, reviewed and
hand-checked earlier for exactly this university, and calls its `SOURCES`
list, `scrape_directory()` and `scrape_profile()` rather than duplicating any
of that parsing logic.

WHY NOT THE PURE PORTAL
------------------------
researchportalplus.anu.edu.au (ANU's Elsevier Pure instance) is the cleaner,
structured source, but it sits behind enterprise bot detection. The team
decided not to try to defeat that — it is out of scope on both ethical and
robots.txt grounds — so this adapter, like anu_scraper.py itself, never
requests anything from that host.

THE TWO SEED FILES
-------------------
`data/anu_identity.csv` (23 rows) and `data/anu_doi_backfill.csv` (123 rows)
were generated once, by a throwaway script, from two files that were already
hand-verified in earlier work: the root `anu_staff.csv` (ORCID and OpenAlex
author ids, one row per staff member who has either) and the root
`anu_publications.csv` (DOIs that an earlier Crossref title-match pass
attached — the rows where `doi_source == "crossref_title_match"`). This
adapter reads only the two seed CSVs, never the legacy root files, and never
re-derives an ORCID or a DOI itself: every identity and DOI value here was
already confirmed by hand, not guessed at import time.

A NOTE ON NAMES
----------------
`anu_scraper.py`'s own `scrape_directory()` already strips a leading
honorific/rank prefix ("Professor ", "Dr ", ...) off each `Researcher.name`
before returning it — see its `clean_name()`. That happens upstream of this
adapter and is not something this file can see around without editing
`anu_scraper.py`, which is out of scope. So `name` and `name_clean` below are
the same already-cleaned string; `core.titles.split_prefix` is still run on
it, for the same interface every other adapter uses, but on ANU it will
normally find nothing left to strip and return `prefix=None`.

WHAT THIS ADAPTER DELIBERATELY DOES NOT DO
--------------------------------------------
- Does not touch researchportalplus.anu.edu.au.
- Does not include a publication `scrape_profile()` itself marked
  low-confidence ("unparsed") — those are counted and reported, never
  guessed into the output.
- Does not attach an ORCID, OpenAlex author id or backfilled DOI by a fuzzy
  or "closest" match — every join here is exact-string, and an ambiguous
  case is left blank and counted rather than resolved by guesswork.
- Does not cache HTTP responses. `anu_scraper.py` has no cache of its own, so
  every call this adapter makes already fetches live pages; the `refresh`
  argument is accepted (for the same call signature `run.py` uses for every
  adapter) but has no effect here, since there is nothing to bypass.
- Does not remove emeritus staff with no recorded publications — they are
  listed for a human to look at, per the client's 19 Aug rule, not dropped.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import anu_scraper  # noqa: E402  (needs the sys.path fix-up above)

from core.config import DATA_DIR  # noqa: E402
from core.schema import blank_pub  # noqa: E402
from core.titles import level, rank, split_prefix  # noqa: E402

UNIVERSITY = "Australian National University"
ROR = "019wvm592"
SOURCE_NAME = "ANU staff profile"

IDENTITY_CSV = DATA_DIR / "anu_identity.csv"
DOI_BACKFILL_CSV = DATA_DIR / "anu_doi_backfill.csv"

# anu_scraper.Publication.publication_type -> the shared vocabulary in
# core.schema. anu_scraper only ever sets one of these five values (see its
# `publication_type = ...` assignments) — anything else reaching `_type()`
# below would be a new value added there that this adapter does not yet know
# about, so it is mapped to "Other" and reported loudly rather than dropped
# silently.
ANU_TYPE_MAP = {
    "journal_article": "Journal Article",
    "book_chapter": "Book Chapter",
    "conference_paper": "Conference Paper",
    "industry_report": "Research Report",
    "textbook": "Book",
}
UNKNOWN_TYPES = Counter()


def _type(publication_type):
    mapped = ANU_TYPE_MAP.get((publication_type or "").strip())
    if mapped:
        return mapped
    UNKNOWN_TYPES[publication_type or "(blank)"] += 1
    return "Other"


# ---------------------------------------------------------------------------
# Seed files
# ---------------------------------------------------------------------------

def _load_identity(path=IDENTITY_CSV):
    """name -> (orcid, [openalex_author_id]). Exact-name keyed, no fuzzy match."""
    import csv

    by_name = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            orcid = (row.get("orcid") or "").strip() or None
            oa_id = (row.get("openalex_author_id") or "").strip() or None
            if not orcid and not oa_id:
                continue
            by_name[name] = (orcid, [oa_id] if oa_id else [])
    return by_name


_TITLE_NORM_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")


def _normalise_title(title):
    text = (title or "").lower()
    text = _TITLE_NORM_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _load_doi_backfill(path=DOI_BACKFILL_CSV):
    """(researcher_name, normalised title) -> [doi, ...] (kept as a list so an
    ambiguous key — more than one row — is visible rather than silently
    collapsed to whichever happened to be read last)."""
    import csv

    by_key = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("researcher_name") or "").strip()
            doi = (row.get("doi") or "").strip()
            if not name or not doi:
                continue
            key = (name, _normalise_title(row.get("title")))
            by_key.setdefault(key, []).append(doi)
    return by_key


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

def _staff_record(researcher, identity_by_name):
    name_clean, prefix = split_prefix(researcher.name)
    title_clean = rank(researcher.job_title, prefix)
    level_code = level(title_clean)
    level_source = "core.titles" if level_code else None
    if level_code is None and researcher.academic_level:
        level_code = researcher.academic_level
        level_source = "anu_scraper fallback"

    orcid, openalex_author_ids = identity_by_name.get(researcher.name, (None, []))

    return {
        "university": researcher.university,
        "discipline": researcher.field_of_research,
        "name": researcher.name,
        "name_clean": name_clean,
        "prefix": prefix,
        "title": researcher.job_title,
        "title_clean": title_clean,
        "level_code": level_code,
        "level_source": level_source,
        "profile_url": researcher.profile_url,
        "source_id": None,
        "orcid": orcid,
        "openalex_author_ids": openalex_author_ids,
        "less_research_intensive": researcher.less_research_intensive,
    }


# ---------------------------------------------------------------------------
# Publications
# ---------------------------------------------------------------------------

def _map_publication(pub, name_clean, backfill_by_key, doi_stats):
    """One anu_scraper.Publication (already confidently parsed) -> blank_pub()."""
    doi = pub.doi
    doi_from_page = bool(doi)
    year = str(pub.year) if pub.year is not None else None
    title_key = (pub.researcher_name, _normalise_title(pub.title))
    candidates = backfill_by_key.get(title_key, [])

    if doi:
        # Already have a page DOI: never overwritten, but a disagreeing
        # backfill entry for the same (name, title) is worth a look.
        conflicting = [d for d in candidates if d and d != doi]
        if conflicting:
            doi_stats["conflicts"] += 1
    else:
        distinct = sorted(set(candidates))
        if len(distinct) == 1:
            doi = distinct[0]
            doi_stats["carried"] += 1
        elif len(distinct) > 1:
            doi_stats["ambiguous"] += 1
        else:
            doi_stats["not_matched"] += 1

    return blank_pub(
        name=name_clean,
        source_id=None,
        title=pub.title,
        year=year,
        type=_type(pub.publication_type),
        n_authors=pub.author_count,
        authors=pub.coauthors,
        issns=[pub.issn] if pub.issn else [],
        journal=pub.journal_name,
        doi=doi,
        link=pub.article_url,
        source=SOURCE_NAME,
    ), doi_from_page


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def collect(verbose=True, refresh=False):
    """Return (records, pubs) satisfying the core.schema contract.

    `refresh` is accepted for the same call signature every adapter uses
    (see run.py), but anu_scraper.py keeps no cache of its own, so every call
    already fetches live pages regardless of this flag.
    """
    identity_by_name = _load_identity()
    backfill_by_key = _load_doi_backfill()

    researchers = []
    for source in anu_scraper.SOURCES:
        researchers.extend(anu_scraper.scrape_directory(source))
    if verbose:
        print(f"\n  {len(researchers)} academic researchers across RSA + RSFAS")

    identity_matched = set()
    records = []
    for r in researchers:
        rec = _staff_record(r, identity_by_name)
        records.append(rec)
        if r.name in identity_by_name:
            identity_matched.add(r.name)

    unmatched_seed = sorted(set(identity_by_name) - identity_matched)

    pubs = []
    unparsed_total = 0
    pubs_by_name = Counter()
    doi_stats = Counter()
    dois_from_page = 0

    for i, r in enumerate(researchers, 1):
        rec = records[i - 1]
        confident, unparsed, had_section = anu_scraper.scrape_profile(r)
        unparsed_total += len(unparsed)
        for pub in confident:
            mapped, had_page_doi = _map_publication(
                pub, rec["name_clean"], backfill_by_key, doi_stats)
            pubs.append(mapped)
            pubs_by_name[r.name] += 1
            if had_page_doi:
                dois_from_page += 1
        if verbose:
            note = "" if had_section else "  (no Publications section)"
            print(f"  {i:>3}/{len(researchers)}  {r.name:<34} "
                  f"{len(confident):>3} pubs"
                  + (f"  ({len(unparsed)} unparsed)" if unparsed else "")
                  + note)

    staff_no_pubs = [r.name for r in researchers if pubs_by_name[r.name] == 0]
    emeritus_no_pubs = [
        r.name for r in researchers
        if "emeritus" in (r.job_title or "").lower() and pubs_by_name[r.name] == 0
    ]

    if UNKNOWN_TYPES:
        print("\n  ! ANU publication types this adapter does not map:")
        for label, n in UNKNOWN_TYPES.most_common():
            print(f"      {n:>5}  {label!r}")

    if unmatched_seed:
        print(f"\n  {len(unmatched_seed)} identity seed row(s) matched no "
              f"current researcher (name changed or left the roster):")
        for name in unmatched_seed:
            print(f"      {name!r}")

    if emeritus_no_pubs:
        print(f"\n  {len(emeritus_no_pubs)} emeritus staff with zero confident "
              f"publications (kept, for a human to review):")
        for name in emeritus_no_pubs:
            print(f"      {name!r}")

    if verbose:
        print(f"\n  {len(records)} staff, {len(pubs)} publications")
        print(f"  {unparsed_total} publication entries were low-confidence "
              f"and excluded, not guessed at")
        print(f"  {dois_from_page} DOI(s) came from the profile page")
        print(f"  {doi_stats['carried']} DOI(s) carried from the seed backfill "
              f"({doi_stats['ambiguous']} ambiguous, not carried; "
              f"{doi_stats['conflicts']} page/seed conflicts, page kept)")
        print(f"  {len(staff_no_pubs)} staff with no publications")

    return records, pubs
