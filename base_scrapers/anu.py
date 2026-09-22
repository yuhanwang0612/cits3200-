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

import csv
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import anu_scraper  # noqa: E402  (needs the sys.path fix-up above)

from core.config import DATA_DIR, ORCID_BASE, ORCID_HEADERS  # noqa: E402
from core.http import cached_get  # noqa: E402
from core.schema import blank_pub  # noqa: E402
from core.titles import level, rank, rank_from_level, split_prefix  # noqa: E402

ORCID_DECISIONS_LOG = _REPO_ROOT / "scratch" / "_anu15" / "orcid_decisions.csv"

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
        if title_clean is None:
            # scratch/_anu17/REPORT.md task 5. researcher.job_title here is
            # a role subtitle, not a rank ("Reader", "Director, Research
            # School of Accounting", "Deputy Director (Education)") — the
            # rank itself lives in the heading line above it on the staff
            # card ("Associate Professor Keturah Whitford"), which
            # anu_scraper.scrape_directory already reads (that's exactly
            # how `researcher.academic_level` got set here, via its own
            # name-prefix fallback) but doesn't carry through as text. The
            # generic rank word for that level is the best available
            # substitute for the literal heading text. Applies to every ANU
            # staff member in this situation, not just the three it was
            # found on.
            title_clean = rank_from_level(level_code)

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
# FIX D — page ORCIDs
# ---------------------------------------------------------------------------
#
# Sixteen ANU staff publish their own ORCID on their RSA/RSFAS profile page,
# but data/anu_identity.csv (the hand-verified seed) only covers 18 of the
# 46 staff, so ORCID/Crossref/OpenAlex retrieval skips most of the roster.
# A page ORCID is accepted only when ALL of: exactly one distinct ORCID
# appears on the page, its ISO 7064 mod 11-2 checksum is valid, and the
# public ORCID record's name matches the staff member. The seed always
# wins over a page ORCID; a disagreement between the two is reported, not
# silently overridden. Every accepted and rejected candidate is logged to
# ORCID_DECISIONS_LOG for hand review.

def _orcid_checksum_valid(orcid: str) -> bool:
    digits = orcid.replace("-", "")
    if len(digits) != 16 or not digits[:-1].isdigit():
        return False
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    check_char = "X" if result == 10 else str(result)
    return digits[-1].upper() == check_char


def _fold_name(s):
    """Lower-case, accent- and hyphen/space-stripped, for a tolerant name
    comparison ('ignoring case, accents and hyphens')."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s\-]", "", s).lower()


_NAME_PARTS_RE = re.compile(r"^(?P<first>\S+)\s*(?:\((?P<bracket>[^)]+)\)\s*)?(?P<rest>.+)$")


def _split_name(name: str):
    """'Tracy (Kun) Wang' -> ('Tracy', 'Kun', 'Wang'). A name with no
    bracketed preferred name just leaves that slot None."""
    m = _NAME_PARTS_RE.match((name or "").strip())
    if not m:
        return (name or "").strip(), None, ""
    first, bracket, rest = m.group("first"), m.group("bracket"), m.group("rest").strip()
    surname = rest.split()[-1] if rest else ""
    return first, bracket, surname


def _orcid_record_matches(person, researcher_name: str) -> bool:
    """True if the ORCID public /person record's family name equals the
    staff member's surname and its given name's first letter matches the
    first given name or the bracketed preferred name (e.g. 'Tracy (Kun)
    Wang' accepts a record whose given name starts with T or K)."""
    if not person:
        return False
    name = person.get("name") or {}
    family = ((name.get("family-name") or {}) or {}).get("value") or ""
    given = ((name.get("given-names") or {}) or {}).get("value") or ""
    if not family.strip() or not given.strip():
        return False
    first, bracket, surname = _split_name(researcher_name)
    if not surname or _fold_name(family) != _fold_name(surname):
        return False
    accepted_initials = {s[0].lower() for s in (first, bracket) if s}
    return given.strip()[0].lower() in accepted_initials


def _fetch_orcid_person(orcid: str):
    try:
        return cached_get(f"{ORCID_BASE}/{orcid}/person",
                           headers=ORCID_HEADERS, sleep=0.5, allow_404=True)
    except Exception as e:
        print(f"    ORCID lookup failed for {orcid}: {type(e).__name__} {e}")
        return None


def _apply_page_orcid_fallback(researchers, records, log_path=ORCID_DECISIONS_LOG, verbose=True):
    """Second pass over `records` (after anu_scraper.scrape_profile has run
    for every researcher and populated anu_scraper.PROFILE_ORCIDS): fill in
    a validated page ORCID for any staff member the seed left blank, and
    log every accepted/rejected candidate. Mutates `records` in place."""
    decisions = []
    accepted = conflicts = 0

    for researcher, rec in zip(researchers, records):
        candidates = anu_scraper.PROFILE_ORCIDS.get(researcher.name, [])
        seed_orcid = rec.get("orcid")

        if seed_orcid:
            for c in candidates:
                if c != seed_orcid:
                    conflicts += 1
                    decisions.append({
                        "name": researcher.name, "orcid": c,
                        "decision": "rejected",
                        "reason": f"seed already has {seed_orcid!r}; page disagrees",
                    })
                    print(f"    ! ORCID conflict for {researcher.name}: "
                          f"seed={seed_orcid} page={c} — keeping seed")
            continue

        if not candidates:
            continue
        if len(candidates) > 1:
            for c in candidates:
                decisions.append({
                    "name": researcher.name, "orcid": c, "decision": "rejected",
                    "reason": f"{len(candidates)} distinct ORCIDs on page, not exactly 1",
                })
            continue

        candidate = candidates[0]
        if not _orcid_checksum_valid(candidate):
            decisions.append({
                "name": researcher.name, "orcid": candidate, "decision": "rejected",
                "reason": "invalid ISO 7064 mod 11-2 checksum",
            })
            continue

        person = _fetch_orcid_person(candidate)
        if not _orcid_record_matches(person, researcher.name):
            decisions.append({
                "name": researcher.name, "orcid": candidate, "decision": "rejected",
                "reason": "ORCID public record name does not match staff member"
                          if person else "could not fetch ORCID public record",
            })
            continue

        rec["orcid"] = candidate
        accepted += 1
        decisions.append({
            "name": researcher.name, "orcid": candidate, "decision": "accepted",
            "reason": "exactly 1 candidate; checksum valid; ORCID record name matches",
        })

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "orcid", "decision", "reason"])
        w.writeheader()
        w.writerows(decisions)

    if verbose:
        print(f"\n  page-ORCID fallback: {accepted} accepted, "
              f"{len(decisions) - accepted - conflicts} rejected, "
              f"{conflicts} seed/page conflicts (seed kept) "
              f"-> {log_path}")
    return decisions


# ---------------------------------------------------------------------------
# FIX I — known title-corruption patterns (scratch/_anu17, task 2)
# ---------------------------------------------------------------------------
#
# Four shapes of corrupted `pub.title` text, each confirmed against the
# current `final output/anu/anu_publications.csv` and checked against every
# other ANU title before being coded as a rule (never patched by hand-
# editing a CSV — see scratch/_anu17/REPORT.md for the full check). Each
# rule is deliberately narrow — anchored to the exact shape observed —
# rather than a general "strip anything that looks like X" heuristic.

# (a) A trailing conference-award sentence the source page appends straight
# onto the citation, e.g. "...CSR performance. Best Paper Award at the 2021
# AFAANZ annual conference" (4 Lily Chen rows). Anchored to a sentence
# boundary ("\.\s+") so it can never match "award-winning" used as a live
# adjective mid-title with no preceding full stop — which is how Louise
# Lu's and Kathy Wang's own genuine titles use the word ("...competitor
# CEOs' award-winning events"); neither matches this rule.
_AWARD_SUFFIX_RE = re.compile(
    r"\.\s+(?:Best Paper Award\b.*|(?:\d{4}\s+)?[A-Z][\w' -]*\bManuscript Award\b.*)$"
)

# (b) A trailing "with X. Surname and Y. Surname" co-author clause the page
# appends after the real title, e.g. Antje Berndt's "The Decline of Too Big
# To Fail with D. Duffie and Y. Zhu" — those coauthors are already carried
# separately in `pub.coauthors`; the clause is corrupted title text.
_TRAILING_COAUTHOR_CLAUSE_RE = re.compile(
    r"\s+with\s+[A-Z]\.\s?[\w'-]+(?:(?:,\s*|\s+and\s+)[A-Z]\.\s?[\w'-]+)*$"
)

# (c) The title field is actually a citation string — "Surname, I., Surname,
# I., and Surname, I. (forthcoming) 'Real Title...'" — with the real title
# inside the quote marks (Susanna Ho). Recovers everything after the
# opening quote; a missing closing quote (the source text was itself
# truncated) is left as-is rather than guessed at.
_CITATION_TITLE_RE = re.compile(
    r"^[A-Z][A-Za-z'-]+,\s*[A-Z][.,].*?\((?:forthcoming|\d{4})\)\s*[‘’']"
    r"(?P<title>.+)$"
)

# (d) A mangled book-review citation: the review's own title, immediately
# followed (no space) by a run of digits, then the book's author list —
# "...An Integrated Approach20111Alvin A. Arens, Peter Best, ..." (Greg
# Shailer). Only applied when the recovered prefix reappears verbatim later
# in the same string — the book title is cited a second time in the same
# mangled paragraph — so this can't fire on an unrelated title that happens
# to butt up against a number; the digit-run shape alone is not enough.
_DIGIT_RUN_RE = re.compile(r"\d{4,}(?=[A-Z])")

TITLE_REPAIR_COUNTS = Counter()


def _repair_title(title):
    """Undo one of the four known title-corruption shapes above, if any
    applies. Returns `title` unchanged otherwise."""
    if not title:
        return title

    m = _AWARD_SUFFIX_RE.search(title)
    if m:
        TITLE_REPAIR_COUNTS["trailing_award_clause"] += 1
        return title[:m.start()].strip()

    m = _TRAILING_COAUTHOR_CLAUSE_RE.search(title)
    if m:
        TITLE_REPAIR_COUNTS["trailing_coauthor_clause"] += 1
        return title[:m.start()].strip()

    m = _CITATION_TITLE_RE.match(title)
    if m:
        TITLE_REPAIR_COUNTS["citation_string_title"] += 1
        return m.group("title").strip()

    m = _DIGIT_RUN_RE.search(title)
    if m:
        prefix = title[:m.start()].rstrip()
        if len(prefix) >= 15 and prefix[:1].isupper() and prefix in title[m.end():]:
            TITLE_REPAIR_COUNTS["mangled_review_citation"] += 1
            return prefix

    return title


# ---------------------------------------------------------------------------
# FIX J — prose mistaken for a title (scratch/_anu17, task 2e)
# ---------------------------------------------------------------------------
#
# One ANU row (a Wai-Man (Raymond) Liu monograph description) has abstract
# prose in `title` and the next sentence of that same prose in `journal` —
# the source page's structure defeated the citation parser entirely, so
# there is no real title anywhere in the text to recover. Excluded rather
# than guessed at.
#
# Word count alone is NOT enough: a live re-scrape turned up Alex Wang's
# real "Strategizing in the Midst of Management Controls: A Longitudinal
# Case Study..." (24 words) paired with the real special-issue journal name
# "Accounting and Finance, A Special Issue for Qualitative Accounting
# Research" (10 words) — a genuine title/journal pair that happens to be
# long, not prose, and it was wrongly caught by a word-count-only version
# of this rule during the scratch/_anu17 pipeline run (see
# scratch/_anu17/REPORT.md). The word-count thresholds below stay (a real
# journal name is essentially never this long, and Greg Shailer's mangled
# book-review citation — repaired by FIX I(d) above, not excluded here — is
# the only other title this long), but the rule additionally requires both
# doi and year to be blank: every real citation on this page-scraped corpus
# carries at least one of the two, including Alex Wang's row (year 2019).
# Raymond Liu's prose row has neither.
_PROSE_TITLE_MIN_WORDS = 20
_PROSE_JOURNAL_MIN_WORDS = 10

EXCLUDED_PROSE_TITLES = []


def _is_prose_not_title(pub):
    if pub.get("doi") or pub.get("year"):
        return False
    title, journal = pub.get("title"), pub.get("journal")
    if not title or not journal:
        return False
    return (len(title.split()) >= _PROSE_TITLE_MIN_WORDS
            and len(journal.split()) >= _PROSE_JOURNAL_MIN_WORDS)


# ---------------------------------------------------------------------------
# Publications
# ---------------------------------------------------------------------------

def _map_publication(pub, name_clean, backfill_by_key, doi_stats):
    """One anu_scraper.Publication (already confidently parsed) -> blank_pub()."""
    doi = pub.doi
    doi_from_page = bool(doi)
    year = str(pub.year) if pub.year is not None else None
    # Backfill is keyed on the title as originally scraped, not the
    # repaired one — data/anu_doi_backfill.csv was generated against the
    # page text before FIX I existed.
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
        title=_repair_title(pub.title),
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
            if _is_prose_not_title(mapped):
                EXCLUDED_PROSE_TITLES.append({
                    "name": r.name, "title": mapped["title"], "journal": mapped["journal"],
                })
                continue
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

    _apply_page_orcid_fallback(researchers, records, verbose=verbose)

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

    if EXCLUDED_PROSE_TITLES:
        print(f"\n  {len(EXCLUDED_PROSE_TITLES)} row(s) excluded — title is "
              f"prose, not a citation, and journal is the next sentence of "
              f"the same prose (FIX J, no real title to recover):")
        for row in EXCLUDED_PROSE_TITLES:
            print(f"      {row['name']!r}: {row['title'][:70]!r}...")

    if verbose:
        print(f"\n  {len(records)} staff, {len(pubs)} publications")
        print(f"  {unparsed_total} publication entries were low-confidence "
              f"and excluded, not guessed at")
        print(f"  {dois_from_page} DOI(s) came from the profile page")
        print(f"  {doi_stats['carried']} DOI(s) carried from the seed backfill "
              f"({doi_stats['ambiguous']} ambiguous, not carried; "
              f"{doi_stats['conflicts']} page/seed conflicts, page kept)")
        print(f"  {len(staff_no_pubs)} staff with no publications")
        if TITLE_REPAIR_COUNTS:
            print(f"  title repairs (FIX I): "
                  + ", ".join(f"{k}={v}" for k, v in TITLE_REPAIR_COUNTS.items()))

    return records, pubs
