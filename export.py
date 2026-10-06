"""Filter, deduplicate and write the four tables.

Identical for every university. Filtering happens once, here, at the end —
retrieval upstream is deliberately unfiltered so that exclusions are
visible and reversible rather than baked into each source.
"""

import csv
import difflib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.config import OUTPUT_DIR
from core.titles import level, rank, split_job_title

# scratch/_anu18/REPORT.md FIX 1. base_scrapers.anu's own FIX I title-repair
# rules only ever ran on a title scraped directly off the ANU page
# (base_scrapers.anu._map_publication) — a row for the same ANU researcher
# retrieved via ORCID/Crossref/OpenAlex (added later, by info/orcid.py etc.)
# carries whatever title that source indexed under, which can be the same
# corrupted shape baked into THEIR metadata rather than introduced by this
# pipeline's own page parse (confirmed: Greg Shailer's mangled book-review
# citation survives under `source == "ORCID"`, doi
# 10.1108/18325911111182330). Reused here, not duplicated, so there is one
# single set of repair rules; safe to import (base_scrapers.anu has no
# import of export.py, and every module-level statement it or anu_scraper.py
# runs at import time is a regex compile or an object literal — no network
# call), and applied below only to rows already confirmed to be ANU's own
# via `records`, so it can never touch another university's title text.
from base_scrapers.anu import _repair_title as _anu_repair_title

TABLES = ("staff", "journals", "publications", "harvest")

# Verified publisher DOI corrections.  Keep these in the repeatable export
# path so every fresh pipeline run repairs the source typo rather than relying
# on a hand-edited output CSV.
DOI_CORRECTIONS = {
    "10.1111/j.1468-2443.2006.00055x": "10.1111/j.1468-2443.2006.00055.x",
}

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalise_title(title):
    """NFKC, lowercase, every run of non-alphanumeric characters -> one
    space, then trim. Used only for the dedup key below — curly vs straight
    quotes and similar cosmetic differences must not defeat it."""
    t = unicodedata.normalize("NFKC", title or "").lower()
    return _NON_ALNUM_RE.sub(" ", t).strip()


# --- FIX G: near-duplicate merge (applied AFTER the exact-match rule
# above) -------------------------------------------------------------------
#
# The exact rule only catches an identical normalised title. A page-scraped
# copy of a paper and its ORCID/Crossref/OpenAlex copy sometimes differ by a
# word or two ("...value of cash holdings" vs "...value of cash holding"),
# or the page copy carries an SSRN preprint DOI while the retrieved copy
# carries the real, published DOI for literally the same paper — the exact
# rule sees those as two different, unrelated publications and keeps both.

SSRN_DOI_PREFIX = "10.2139/ssrn."
NEAR_DUP_TITLE_RATIO = 0.85
# Sources that came from a retrieval step rather than the university's own
# page — mirrors screen.py's RETRIEVED set. Preferred over a page source
# when neither row in a near-duplicate pair has a distinguishing DOI.
_RETRIEVED_SOURCES = {"ORCID", "Crossref", "OpenAlex"}
# A trailing "Part N" (or "- Part N", digits or roman numerals) on an
# otherwise near-identical title marks a DIFFERENT instalment of a series,
# not a duplicate — confirmed on UNSW practitioner-note series that would
# otherwise score >0.99 on title similarity alone (e.g. "...Burton has a
# case - Part 1/2/3", three distinct published notes with the same lead-in
# sentence; "...roll-overs and exemptions: part I" / "part II", same shape
# with roman numerals).
_PART_MARKER_RE = re.compile(r"\bpart\s+([ivx]+|\d+)\b", re.IGNORECASE)
_REPOSITORY_JOURNAL_MARKERS = (
    "repository", "archive", "research collection", "eprints", "minerva access",
)


def _repository_like_journal(value):
    journal = (value or "").strip().lower()
    return any(marker in journal for marker in _REPOSITORY_JOURNAL_MARKERS)


def _dedup_doi(doi):
    """For near-duplicate comparison ONLY — never changes the exported doi
    value. An SSRN preprint DOI doesn't identify a specific publication the
    way a real journal DOI does: the same paper's published version usually
    gets its own, different DOI, so an SSRN DOI here counts as no DOI."""
    d = (doi or "").strip().lower()
    return "" if d.startswith(SSRN_DOI_PREFIX) else d


def _correct_doi(doi):
    value = (doi or "").strip()
    return DOI_CORRECTIONS.get(value.lower(), value) or None


def _harmonise_doi_metadata(rows):
    """Make co-author copies of the same DOI use one title/year/journal.

    A DOI occasionally gets reused for unrelated book-review records in
    upstream indexes, so harmonisation is deliberately limited to groups
    whose titles are clearly the same work (normalised equality, one title
    being a subtitle-truncated prefix, or strong fuzzy similarity).

    Once a group is the same work, one researcher keeps one copy: two ORCID
    entries for one paper ("A Liberalization Spillover" and its full title)
    otherwise survive the title-keyed dedup and come out identical here.
    """
    groups = {}
    duplicates = set()
    for row in rows:
        doi = _dedup_doi(row.get("doi"))
        if doi:
            groups.setdefault(doi, []).append(row)

    for group in groups.values():
        if len(group) < 2:
            continue
        title_keys = [_normalise_title(row.get("title")) for row in group]
        nonempty = [title for title in title_keys if title]
        if not nonempty:
            continue
        shortest, longest = min(nonempty, key=len), max(nonempty, key=len)
        years = {str(row.get("year")) for row in group if row.get("year") not in (None, "")}
        journals = {
            _normalise_title(row.get("journal_name"))
            for row in group if row.get("journal_name")
        }
        min_similarity = min(
            difflib.SequenceMatcher(None, a, b).ratio()
            for a in nonempty for b in nonempty
        )
        same_bibliographic_context = len(years) <= 1 and len(journals) <= 1
        same_work = (
            len(set(nonempty)) == 1
            or longest.startswith(shortest + " ")
            or min_similarity >= NEAR_DUP_TITLE_RATIO
            # A source may both truncate the subtitle and introduce a small
            # typo ("accountingy-related" in the verified Monash example).
            # The lower threshold is safe only when year and journal agree.
            or (same_bibliographic_context and min_similarity >= 0.70)
        )
        if not same_work:
            continue

        # Prefer the fullest title. For year/journal use the majority value;
        # ties preserve the first populated value, keeping the policy stable.
        canonical_title = max(
            (row.get("title") for row in group if row.get("title")),
            key=lambda value: len(_normalise_title(value)),
        )
        canonical = {"title": canonical_title}
        for field in ("year", "journal_name"):
            values = [row.get(field) for row in group if row.get(field) not in (None, "")]
            if values:
                counts = Counter(values)
                canonical[field] = max(values, key=lambda value: counts[value])
        # Collapsing needs stronger evidence than harmonising: a one-word
        # prefix ("Editorial" / "Editorial note on ...") can be two items.
        collapse = (
            len(set(nonempty)) == 1
            or min_similarity >= NEAR_DUP_TITLE_RATIO
            or (longest.startswith(shortest + " ")
                and len(shortest) >= _PREFIX_DUP_MIN_TITLE_LEN)
        )
        seen_names = set()
        for row in group:
            row.update(canonical)
            if collapse and row.get("name") in seen_names:
                duplicates.add(id(row))
            seen_names.add(row.get("name"))

    return [row for row in rows if id(row) not in duplicates]


def _differing_part_marker(title_a, title_b):
    ma = _PART_MARKER_RE.search(title_a or "")
    mb = _PART_MARKER_RE.search(title_b or "")
    return bool(ma and mb and ma.group(1).lower() != mb.group(1).lower())


# One listing often drops the subtitle: UNSW has "Elevating professional
# scepticism" where ORCID has "Elevating Professional Scepticism: An
# Exploratory Study Into ...", both under DOI 10.1108/maj-08-2013-0914. The
# similarity ratio is low only because one title is much longer. When both
# rows carry the SAME real DOI and one title is the start of the other, they
# are the same paper. Different titles under one DOI that are not a prefix of
# each other stay separate: Economic Record gave a batch of book reviews one
# DOI, and those are separate items.
MIN_PREFIX_WORDS = 3
MIN_SAME_PAPER_WORDS = 4


def _same_doi_subtitle_dropped(a, b):
    doi_a, doi_b = _dedup_doi(a.get("doi")), _dedup_doi(b.get("doi"))
    if not doi_a or doi_a != doi_b:
        return False
    ta, tb = _normalise_title(a.get("title")), _normalise_title(b.get("title"))
    short, long_ = sorted((ta, tb), key=len)
    if len(short.split()) < MIN_PREFIX_WORDS:
        return False
    return long_ == short or long_.startswith(short + " ")


def is_near_duplicate(a, b):
    """True if publication rows `a` and `b` (each needing name/title/year/
    doi/link keys) are the same researcher's same paper under a fuzzy title
    match. Never true when: the two rows each carry their own distinct real
    (non-SSRN) DOI; the two rows each carry their own distinct, non-empty
    `link` (confirmed on UNSW practitioner notes with no DOI at all, each
    with its own real, distinct source URL — a strong identifier even
    without a DOI); or the titles differ only by a "Part N" marker (a
    different instalment of a series, not a duplicate)."""
    if a.get("name") != b.get("name"):
        return False
    title_a, title_b = a.get("title"), b.get("title")
    if _differing_part_marker(title_a, title_b):
        return False
    ratio = difflib.SequenceMatcher(
        None, _normalise_title(title_a), _normalise_title(title_b)
    ).ratio()
    if ratio < NEAR_DUP_TITLE_RATIO and not _same_doi_subtitle_dropped(a, b):
        return False
    ya, yb = (a.get("year") or "").strip(), (b.get("year") or "").strip()
    if ya and yb:
        try:
            if abs(int(ya) - int(yb)) > 1:
                return False
        except ValueError:
            pass  # non-numeric year text — don't let it block an otherwise-clear match
    doi_a, doi_b = _dedup_doi(a.get("doi")), _dedup_doi(b.get("doi"))
    if doi_a and doi_b and doi_a != doi_b:
        # Some articles have DOI aliases: publisher vs repository deposit,
        # or legacy JSTOR vs current publisher DOI. Merge only when the
        # title/year are exact and the journal identity is compatible.
        exact_title = _normalise_title(title_a) == _normalise_title(title_b)
        exact_year = bool(ya and yb and ya == yb)
        ja = _normalise_title(a.get("journal_name") or "")
        jb = _normalise_title(b.get("journal_name") or "")
        legacy_jstor_alias = (
            bool(ja and jb and ja == jb)
            and (doi_a.startswith("10.2307/") or doi_b.startswith("10.2307/"))
        )
        compatible_journal = (
            legacy_jstor_alias
            or _repository_like_journal(a.get("journal_name"))
            or _repository_like_journal(b.get("journal_name"))
        )
        # The same paper listed twice under two DOIs, one of them mistyped or
        # an old alias: UNSW has 10.1111/j.1467.8683.2007.00554.x where ORCID
        # has 10.1111/j.1467-8683.2007.00554.x, and the Journal of Banking &
        # Finance appears under both its bankfin and jbankfin prefixes. An
        # identical title, year and journal is the same paper. Short titles
        # are left alone, since "Discussion" or "Book review" can repeat.
        same_journal = bool(ja and jb and ja == jb and ja != "unknown")
        long_title = len(_normalise_title(title_a).split()) >= MIN_SAME_PAPER_WORDS
        same_paper = exact_title and exact_year and same_journal and long_title
        if not (exact_title and exact_year and compatible_journal) and not same_paper:
            return False
    # The link guard only applies when NEITHER row has a doi at all (not
    # even an SSRN one) — a `link` is usually doi-derived (e.g.
    # "https://doi.org/<doi>"), so comparing it when an SSRN-vs-real-DOI
    # pair is exactly what should merge would wrongly re-introduce the
    # same false split the DOI check above already resolves.
    if not a.get("doi") and not b.get("doi"):
        link_a, link_b = (a.get("link") or "").strip(), (b.get("link") or "").strip()
        if link_a and link_b and link_a != link_b:
            return False
    return True


def _prefer(e, c):
    """Given two near-duplicate rows, return the one to KEEP. `e` is the
    earlier-encountered row (kept by default — 'otherwise keep the
    first')."""
    doi_e, doi_c = _dedup_doi(e.get("doi")), _dedup_doi(c.get("doi"))
    e_repository = _repository_like_journal(e.get("journal_name"))
    c_repository = _repository_like_journal(c.get("journal_name"))
    if e_repository != c_repository:
        return c if e_repository else e
    if doi_e and not doi_c:
        return e
    if doi_c and not doi_e:
        return c
    e_retrieved = e.get("source") in _RETRIEVED_SOURCES
    c_retrieved = c.get("source") in _RETRIEVED_SOURCES
    if c_retrieved and not e_retrieved:
        return c
    return e


# --- FIX K: exact title/year/journal duplicate merge (scratch/_anu18) -----
#
# is_near_duplicate's own "two rows each carrying their own distinct real
# DOI never merge" guard is correct and stays exactly as it is — it is what
# keeps two genuinely different papers that happen to share a title (e.g.
# ANU's two "Busy directors and firm performance" papers, one in Accounting
# and Finance 2021, one in Pacific-Basin Finance Journal 2020) from being
# wrongly collapsed into one. But that guard also blocks a case it
# shouldn't: the SAME paper indexed twice by a retrieval source under two
# different DOIs (confirmed: Susanna Ho's "Partial Least Squares Structural
# Equation Modeling Approach..." carries both 10.17705/1cais.03823 and
# 10.17705/1cais.038123 — both resolve, at doi.org, to the exact same page,
# https://aisel.aisnet.org/cais/vol38/iss1/23/; the second DOI is the first
# with an extra digit spliced in, not a different paper). A real pair of
# distinct papers will not also share an exact (not just fuzzy) title AND
# the same year AND the same journal — the Busy-directors pair differs on
# both year and journal — so that triple match is checked, and can merge
# two rows past the different-DOI guard, before is_near_duplicate runs.
def _is_exact_title_year_journal_dup(a, b):
    if a.get("name") != b.get("name"):
        return False
    if not a.get("title") or not b.get("title"):
        return False
    if _is_correction_notice(a.get("title")) or _is_correction_notice(b.get("title")):
        return False
    if _normalise_title(a["title"]) != _normalise_title(b["title"]):
        return False
    # A generic one-word heading ("Discussion", "Editorial", "Book Review")
    # repeats legitimately within one journal and year, so an identical
    # title is not evidence of the same paper unless the title is long
    # enough to be distinctive. Without this the rule swallows the
    # confirmed UNSW "Discussion" pair, which is two separate articles.
    norm = _normalise_title(a["title"])
    if len(norm) < _PREFIX_DUP_MIN_TITLE_LEN or len(norm.split()) < MIN_PREFIX_WORDS:
        return False
    ya, yb = (a.get("year") or "").strip(), (b.get("year") or "").strip()
    if not ya or not yb or ya != yb:
        return False
    ja = (a.get("journal_name") or "").strip().lower()
    jb = (b.get("journal_name") or "").strip().lower()
    if not ja or not jb or ja != jb:
        return False
    return True


def _prefer_exact_dup(e, c):
    """Preference for the FIX K rule above. `_dedup_doi` (not the raw doi
    string) decides whether both sides are "really" real DOIs first — an
    SSRN-vs-real pair that happens to also match on title/year/journal
    must still keep the real DOI via `_prefer()` below, not fall into the
    shorter-string tie-break (an SSRN DOI is often the shorter one). Only
    when BOTH sides are real, distinct DOIs does `_prefer()`'s own
    doi-presence check fail to break the tie — that's the actual FIX K
    case (Susanna Ho's pair, both real, both resolving to the same page) —
    and the shorter one is correct there, checked by hand (see FIX K's
    comment above), not an arbitrary tie-break."""
    doi_e, doi_c = _dedup_doi(e.get("doi")), _dedup_doi(c.get("doi"))
    if doi_e and doi_c and doi_e != doi_c:
        return e if len((e.get("doi") or "")) <= len((c.get("doi") or "")) else c
    return _prefer(e, c)


# --- FIX L: prefix-containment duplicate merge (scratch/_anu19) -----------
#
# ORCID returns a truncated, main-title-only version of some papers; the
# ANU staff profile page returns the full title including its subtitle.
# Same paper, but title-similarity is far below NEAR_DUP_TITLE_RATIO
# (confirmed ~0.61 on Tracy (Kun) Wang's "Analyst Coverage and Corporate
# Innovation" vs "...: Evidence from Exogenous Changes in Analyst
# Coverage" — a short prefix against a much longer one scores low on a
# whole-string ratio even though it's an exact textual prefix), and FIX K's
# rule needs an identical title, so neither existing rule sees it. 21 such
# pairs exist in ANU.
#
# Deliberately narrower than a fuzzy-ratio rule: same researcher, the
# shorter normalised title is at least 20 characters (so a short common
# opening phrase on two otherwise-different papers can't match), the
# longer title is a STRICT prefix of the shorter (followed by a space —
# i.e. a real word boundary under _normalise_title's own normalisation,
# not a mid-word cut), and same journal AND same year, both exactly. This
# does not touch is_near_duplicate's own different-real-DOI guard (checked
# first, in merge_near_duplicates_traced, same as FIX K) — the two genuine
# "Busy directors and firm performance" papers (different journal AND
# different year) fail this rule on both of those grounds independently,
# same as they already fail FIX K's.
_PREFIX_DUP_MIN_TITLE_LEN = 20

# --- FIX L bug: a correction notice is not a subtitle ----------------------
#
# Web of Science indexes a PUBLISHED CORRECTION under the original article's
# own title plus a trailing "(vol N, pg N, YYYY)" locator pointing back at
# it — e.g. "In our ivory towers? ... (vol 44, pg 104, 2014)". That suffix
# makes the correction's title a textbook case of FIX L's own
# shorter-is-a-strict-prefix-of-longer shape, so without a guard it reads as
# "the same paper, one copy with a subtitle" and either merges into the
# prefix-dup rule or the exact-title-year-journal rule — either way risking
# the genuine article ending up dropped in favour of a row that is not a
# second, independent publication at all. Two confirmed cases: Adelaide's
# Basil Tucker (10.1080/00014788.2013.798234 vs .877214) and UNSW's
# Fariborz Moshirian (10.1016/s0378-4266(02)00467-3 vs (03)00049-9).
#
# The client's 9 Sep rule already excludes corrigenda/errata outright, so
# the correct treatment is not "merge" but "drop the correction row" — see
# the exclusion applied in build_publications() below.
_CORRECTION_NOTICE_RE = re.compile(
    r"\(\s*vol\.?\s*\d+\s*,?\s*p[pg]\.?\s*\d+\s*(?:,\s*\d{4}\s*)?\)\s*$",
    re.IGNORECASE,
)


def _is_correction_notice(title):
    return bool(title and _CORRECTION_NOTICE_RE.search(title))


# Pairs the rule below intentionally does NOT merge, kept here for the
# report rather than silently dropped: same researcher/journal, title is a
# strict prefix relationship, but doi is on neither row or both rows (no
# doi-presence signal to decide which copy to keep), or the years are off
# by 1 and this rule deliberately requires an exact year match. Cleared at
# the top of build_publications() so repeated calls (e.g. in tests) don't
# accumulate stale entries.
SKIPPED_PREFIX_DUPS = []


def _is_prefix_duplicate(a, b):
    if a.get("name") != b.get("name"):
        return False
    # Two rows carrying the SAME doi are the same paper by definition, so
    # there is nothing ambiguous to report: hand the pair to
    # _same_doi_subtitle_dropped inside is_near_duplicate, which merges it
    # and picks the surviving copy. Without this, _prefix_dup_winner sees
    # "a doi on both rows", returns None, and the pair is reported instead
    # of merged — this rule's no-signal guard was written for two DIFFERENT
    # dois and must not claim the identical-doi case.
    doi_a, doi_b = _dedup_doi(a.get("doi")), _dedup_doi(b.get("doi"))
    if doi_a and doi_a == doi_b:
        return False
    if _is_correction_notice(a.get("title")) or _is_correction_notice(b.get("title")):
        return False
    ta, tb = _normalise_title(a.get("title")), _normalise_title(b.get("title"))
    if not ta or not tb or ta == tb:
        return False
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(shorter) < _PREFIX_DUP_MIN_TITLE_LEN:
        return False
    if not longer.startswith(shorter + " "):
        return False
    ya, yb = (a.get("year") or "").strip(), (b.get("year") or "").strip()
    if not ya or not yb or ya != yb:
        return False
    ja = (a.get("journal_name") or "").strip().lower()
    jb = (b.get("journal_name") or "").strip().lower()
    if not ja or not jb or ja != jb:
        return False
    return True


def _prefix_dup_winner(e, c):
    """Keep whichever row has a doi; the other is the ORCID-truncated
    copy. Returns None — do not merge, report instead — when both rows
    have a doi or neither does (no signal to decide which copy is which)."""
    doi_e, doi_c = (e.get("doi") or "").strip(), (c.get("doi") or "").strip()
    if doi_e and not doi_c:
        return e
    if doi_c and not doi_e:
        return c
    return None


def merge_near_duplicates_traced(rows):
    """Same logic as merge_near_duplicates, but also returns the list of
    (kept_row, dropped_row) pairs it merged — used by build_publications
    (which only needs the filtered list) and by
    scratch/_anu16/neardup_simulate.py (which needs to show every pair it
    would merge), so the two never drift apart.

    Groups by name first (a near-duplicate is only ever the same
    researcher's own paper), compares within each group against surviving
    representatives only (not every pair), and preserves the original
    relative row order of whichever row in each pair survives.
    """
    keep = [True] * len(rows)
    pairs: list[tuple[dict, dict]] = []
    by_name: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_name.setdefault(r.get("name"), []).append(i)

    for idxs in by_name.values():
        representatives: list[int] = []
        for i in idxs:
            r = rows[i]
            matched, kind = None, None
            for pos, rep_i in enumerate(representatives):
                if _is_exact_title_year_journal_dup(r, rows[rep_i]):
                    matched, kind = pos, "exact"
                    break
                if _is_prefix_duplicate(r, rows[rep_i]):
                    matched, kind = pos, "prefix"
                    break
                if is_near_duplicate(r, rows[rep_i]):
                    matched, kind = pos, "fuzzy"
                    break
            if matched is None:
                representatives.append(i)
                continue
            rep_i = representatives[matched]

            if kind == "prefix":
                winner = _prefix_dup_winner(rows[rep_i], r)
                if winner is None:
                    # No doi-presence signal to pick a side — report, don't
                    # merge. `r` stands as its own representative; the
                    # existing representative is untouched.
                    SKIPPED_PREFIX_DUPS.append((rows[rep_i], r))
                    representatives.append(i)
                    continue
            else:
                prefer_fn = _prefer_exact_dup if kind == "exact" else _prefer
                winner = prefer_fn(rows[rep_i], r)

            if winner is rows[rep_i]:
                keep[i] = False
                pairs.append((rows[rep_i], r))
            else:
                keep[rep_i] = False
                pairs.append((r, rows[rep_i]))
                representatives[matched] = i

    return [r for i, r in enumerate(rows) if keep[i]], pairs


def merge_near_duplicates(rows):
    """FIX G: collapse near-duplicate rows the exact-match rule above can't
    see. See merge_near_duplicates_traced for the algorithm."""
    kept, _pairs = merge_near_duplicates_traced(rows)
    return kept



# ------------------------------------------------------- academic/admin title
#
# Yuanji, 2 October: "For each individual researcher, it can have a column
# Academic Level (B-E), Academic Title and Admin Title. Usually the Academic
# Level determines the Academic Title. It means, both level BC calls 'Dr',
# level D is 'Associate Professor', E js 'Professor'. You could put Admin
# Title as a separate column additional info, because it cannot be inferred".
#
# job_title stays as the university's own raw string. These two are derived
# beside it, so nothing downstream that reads job_title changes.
#
# The team chose the academic rank over the quote's "Dr" for B and C: "Dr" is
# a qualification, and the rank is what distinguishes a Lecturer from a
# Senior Lecturer. The rank comes from the person's own title where it names
# one at their level ("Senior Research Fellow" stays that, not "Senior
# Lecturer"), else the generic rank for the level.

ACADEMIC_TITLE_BY_LEVEL = {
    "A": "Associate Lecturer",
    "B": "Lecturer",
    "C": "Senior Lecturer",
    "D": "Associate Professor",
    "E": "Professor",
}

# Sean's mapping table of 25 September, which the client approved ("Yes.
# Note that these are administrative title, not academic title."). Longest
# form first: "Deputy Head of School" has to be tested before "Head of
# School", and "Associate Dean" before "Dean", or every deputy becomes a head.
_ADMIN_TITLES = [
    "Deputy Head of Department",
    "Deputy Head of School",
    "Head of Department",
    "Head of School",
    "Associate Dean",
    "Assistant Dean",
    "Deputy Dean",
    "Dean",
    "Program Director",
    "Programme Director",
    "Director",
]


# FR4: education- and teaching-focused positions are excluded from the
# rankings. UNSW's scraper already skips "Education Focused" titles; this
# applies the same rule, plus plainly teaching-only titles, to every
# university at export.
TEACHING_ROLE = re.compile(
    r"education[-\s]?focus|teaching[-\s]?focus"
    r"|teaching (fellow|specialist|associate)|\btutor\b|casual teaching|\btchg\b",
    re.I)


def is_teaching_role(job_title):
    return bool(job_title and TEACHING_ROLE.search(job_title))


def academic_title_for_level(level_code, job_title=None):
    """The academic rank for a level ("Senior Lecturer"), or None off the
    scale. The job title's own rank wins when it is at the same level."""
    code = (level_code or "").strip().upper()
    if code not in ACADEMIC_TITLE_BY_LEVEL:
        return None
    own = rank(job_title) if job_title else None
    if own and level(own) == code:
        return own
    return ACADEMIC_TITLE_BY_LEVEL[code]


def admin_title_from(job_title):
    """The administrative role(s) inside a raw job title, in canonical form.

    "Dean, School of Accounting and Finance" -> "Dean"
    "Joint Deputy Head of Department (Research and Engagement)"
        -> "Deputy Head of Department"
    "Senior Lecturer" -> None, that is an academic rank, not an admin role.

    The rules live in core.titles (split_job_title), which also catches roles
    written after the rank and keeps several roles: "Professor & Convenor of
    HDR, Co-Director of ANCAAR" -> "HDR Convenor; Co-Director".
    """
    return split_job_title(job_title)[1] if job_title and job_title.strip() else None


def build_staff(records):
    return [{
        "name": p["name_clean"],
        # The agreed staff dictionary asks for the official/raw job title.
        # title_clean is only the derived academic-rank label and previously
        # erased valid roles such as teaching-focused appointments.
        "job_title": p.get("title") or p.get("title_clean"),
        "academic_level": p.get("level_code") or level(p.get("title_clean")),
        # Filled in export() once overrides and normalisation have run, but
        # declared here so they sit beside academic_level in the CSV.
        "academic_title": None,
        "admin_title": None,
        "university": p["university"],
        "field_of_research": p["discipline"],
        "source_id": p.get("source_id"),
        "orcid": p.get("orcid"),
        "profile_url": p["profile_url"],
    } for p in records]


_REPOSITORY_ISSNS = {"1556-5068"}   # SSRN Electronic Journal
_ISSN_RE = re.compile(r"\b(\d{4})-?(\d{3}[\dXx])\b")


def _clean_issns(values):
    """Every ISSN in `values`, hyphenated, each once, in first-seen order.

    Sources disagree on the format: some give "0810-5391", some "08105391",
    and some a space-joined pair "08105391 1467629X" in a single entry. One
    UNSW row even carried the publisher, "Emerald Group Publishing", split
    into three ISSN slots. ABDC and Scimago join on the hyphenated form, so
    anything that is not an ISSN is dropped and the rest are written one way.

    SSRN's own ISSN is dropped too. ORCID copies it from a preprint record
    onto the published article, so The Journal of Finance ended up carrying
    it, and it is never the ISSN of the journal the row names.
    """
    out = []
    for value in values or []:
        for a, b in _ISSN_RE.findall(str(value)):
            issn = f"{a}-{b.upper()}"
            if issn not in out and issn not in _REPOSITORY_ISSNS:
                out.append(issn)
    return out


# ------------------------------------------------------- journal spelling
#
# The same journal reached the exports spelled several ways ("PLoS ONE",
# "Plos One", "PLOS ONE"; "The Journal of Business", "JOURNAL OF BUSINESS"),
# and journal_name is the key the publications and journals tables join on,
# so each spelling became a separate journal. One spelling per journal, chosen
# by a rule that gives the same answer in every university's export:
#   1. the ABDC title, where the journal is on the ABDC list (as before);
#   2. otherwise Scimago's title for the row's ISSN - but only when it is the
#      same name spelled differently, never a rename: a wrong ISSN (an SSRN
#      copy, the CIMA magazine sharing "Financial Management") must not turn
#      a journal into another one;
#   3. otherwise a fixed tidy: "&" -> "and", no leading "The", title case.

_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
                "of", "on", "or", "the", "to", "with", "o"}


def _journal_key(name):
    """Same journal -> same key: case, "&"/"and", a leading "The" and
    punctuation are ignored."""
    k = re.sub(r"[^a-z0-9]+", " ", (name or "").lower().replace("&", " and ")).strip()
    return re.sub(r"^the\s+", "", k)


def _tidy_journal_name(name):
    t = " ".join((name or "").split())
    t = re.sub(r"\s*&\s*", " and ", t)
    t = re.sub(r"^the\s+", "", t, flags=re.I)
    shouting = t.isupper()
    words, out, start = t.split(" "), [], True
    for w in words:
        core = re.sub(r"[^A-Za-z]", "", w)
        if not core:
            out.append(w)
        elif not shouting and (core[1:] != core[1:].lower()):
            out.append(w)                       # acronym or camel case: PLoS, ISACA, eJournal
        elif not start and core.lower() in _SMALL_WORDS:
            out.append(w.lower())
        else:
            low = w.lower()
            i = next((j for j, ch in enumerate(low) if ch.isalpha()), 0)
            out.append(low[:i] + low[i].upper() + low[i + 1:])
        start = w.endswith(":")
    return " ".join(out)


def canonical_journal_name(x):
    """The one spelling of this publication's journal; see the note above."""
    if x.get("abdc_title"):
        return x["abdc_title"]
    name = x.get("journal")
    if not name:
        return name
    scimago = x.get("scimago_title")
    if scimago and _journal_key(scimago) == _journal_key(name):
        return scimago
    return _tidy_journal_name(name)


def build_journals(pubs, used_names=None):
    """One row per journal, keyed on the ABDC canonical title where we have
    one. Keying on ISSN splits print from online; keying on the raw name
    splits 'and' from '&'. The canonical title collapses both."""
    out = {}
    for x in pubs:
        # A sparse ORCID row can arrive without a journal title but still be
        # matched to ABDC by an ISSN. In that case `abdc_title` is the canonical
        # journal name and must be enough to create the referenced journal row.
        if not (x.get("abdc_title") or x.get("journal")):
            continue
        key = canonical_journal_name(x)
        if used_names is not None and key not in used_names:
            continue
        candidate = {
            "journal_name": key,
            "journal_raw": x["journal"],
            "publisher": x.get("publisher"),
            "issn": "; ".join(_clean_issns(x.get("issns"))) or None,
            "quality_rank": x.get("abdc"),
            "abdc_edition": x.get("abdc_edition"),
            "impact_factor": x.get("impact_factor"),
            "impact_factor_5yr": x.get("impact_factor_5yr"),
            "jcr_year": x.get("jcr_year"),
            "sjr": x.get("sjr"),
            "sjr_quartile": x.get("sjr_quartile"),
            "h_index": x.get("h_index"),
            "cites_per_doc_2y": x.get("cites_per_doc_2y"),
            "scimago_year": x.get("scimago_year"),
        }
        if key not in out:
            out[key] = candidate
            continue

        # Several source copies can represent the same journal. Retain the
        # first non-empty scalar value and union their ISSNs rather than
        # letting the first (possibly sparse) copy permanently win.
        current = out[key]
        for field, value in candidate.items():
            if field == "issn":
                existing = [v.strip() for v in (current.get(field) or "").split(";") if v.strip()]
                incoming = [v.strip() for v in (value or "").split(";") if v.strip()]
                current[field] = "; ".join(_clean_issns(existing + incoming)) or None
            elif not current.get(field) and value:
                current[field] = value

    if used_names is not None and "unknown" in used_names:
        out.setdefault("unknown", {
            "journal_name": "unknown", "journal_raw": None, "publisher": None,
            "issn": None, "quality_rank": None, "abdc_edition": None,
            "impact_factor": None, "impact_factor_5yr": None, "jcr_year": None,
            "sjr": None, "sjr_quartile": None, "h_index": None,
            "cites_per_doc_2y": None, "scimago_year": None,
        })
    return list(out.values())


# --- ANU-only: SSRN-DOI working papers with no journal and no ABDC rank ---
#
# scratch/_anu17/REPORT.md, task 4. An SSRN preprint DOI on its own is not
# grounds for exclusion — several ANU rows carry an SSRN preprint DOI for a
# paper that IS published in a real, ranked journal (e.g. Kathy Wang, The
# European Accounting Review, A*) and those must be kept. But a row that is
# an SSRN DOI AND has no journal name AND never matched an ABDC rank is a
# working paper that was never published, not a journal article.
#
# Scoped to ANU by staff membership (`records`, which carries `university`),
# not by the row's own `source` field — a `source` of "ORCID"/"Crossref"/
# "OpenAlex" looks identical whichever university retrieved it (an earlier
# version of this rule gated on `source == "ANU staff profile"` and missed
# all 4 target rows for exactly this reason: they're ORCID-retrieved
# copies, not page-scraped ones). This can still never change another
# university's export, because `x["name"]` only matches an ANU
# `name_clean` for an ANU researcher's own row.
_ANU_UNIVERSITY = "Australian National University"
_SSRN_DOI_PREFIX = "10.2139/ssrn."


def _anu_staff_names(records):
    return {r["name_clean"] for r in (records or [])
            if r.get("university") == _ANU_UNIVERSITY}


def _is_anu_unranked_ssrn_preprint(x, anu_names):
    if x.get("name") not in anu_names:
        return False
    doi = (x.get("doi") or "").strip().lower()
    if not doi.startswith(_SSRN_DOI_PREFIX):
        return False
    if x.get("journal"):
        return False
    return not x.get("abdc")


# --- ANU off-field clinical-journal screen ---------------------------------
#
# 22 Sep 2026 (v22): 40 of Wai-Man (Raymond) Liu's rows were excluded by
# name+DOI/title as reviewed rows in data/publication_exclusions.csv (he is a
# genuine ANU accounting/finance academic who also, genuinely, co-authors
# clinical medicine papers — an MChD holder publishing outside this
# dataset's accounting/finance scope, not a namesake). That list is a set of
# specific rows: a fresh scrape that finds MORE of his clinical output (as
# one did on 22 Sep — 10 more rows, none of them among the original 40)
# walks straight past it, because a list can only ever catch rows it has
# already seen.
#
# This is a rule instead: screened on JOURNAL NAME, checked case-
# insensitively as a substring, never on DOI presence and never on ABDC
# rank — two of Liu's legitimate finance/economics rows also have no DOI
# (a page-scraped copy with a truncated journal name, "Journal of Money" /
# "Annals of Operations" — both real papers, verified by title, just an
# incidental parsing gap unrelated to this fix), and a DOI- or rank-based
# rule would wrongly drop real accounting/finance work right alongside the
# clinical papers it's meant to catch. Keyword stems below are derived
# directly from the 10
# confirmed clinical journal names in the current ANU export (verified: no
# other ANU row's journal_name matches any of them, and
# "European Journal of Health Economics" — Liu's own genuinely in-scope,
# ABDC A-rated health-economics paper — matches none either; see the
# regression test in tests/test_export_neardup.py). Visible and editable
# here, not buried inside a function, since the underlying judgement call
# (which fields count as "off-field") is exactly the kind of thing a future
# reviewer needs to be able to find and adjust without reading the rest of
# this file.
ANU_OFF_FIELD_JOURNAL_KEYWORDS = [
    "anaesth",             # anaesthesia, anaesthesiology (British spelling)
    "anesth",              # anesthesia, anesthesiology (American spelling)
    "palliative",
    "rural health",
    "nurse practitioner",
    "pain medicine",
    "arthroplasty",
]


def _is_anu_off_field_journal(x, anu_names):
    if x.get("name") not in anu_names:
        return False
    journal = (x.get("abdc_title") or x.get("journal") or "").lower()
    if not journal:
        return False
    return any(kw in journal for kw in ANU_OFF_FIELD_JOURNAL_KEYWORDS)


# --- ANU-only (v26, docs/DECISIONS.md 28 Sep): author fallback, repository
# journal names, and title text guards ---------------------------------------
#
# Every function below returns immediately for a row whose `name` is not an
# ANU staff member (`anu_names`, built from `records` as above), so no other
# university's rows can be changed — tests/test_anu_export_guards.py checks
# that on a non-ANU row for each one.


def _anu_author_fallback(x, anu_names):
    """base_scrapers/anu.py leaves n_authors/authors blank on an ANU profile
    row that has a DOI, so OpenAlex enrichment fills them from the DOI
    record. Only if enrichment found nothing is the profile's own count used
    (already owner-inclusive, and only set when real co-author text exists).
    Returns True when the fallback was applied."""
    if x.get("name") not in anu_names:
        return False
    if not x.get("_anu_profile_n_authors"):
        return False
    if x.get("n_authors"):
        # v28: a DOI record that names its authors by surname only ("Tam;
        # Ho", the JSTOR record of Susanna Ho's MISQ 2006 paper) yields to
        # the profile's readable list of the same people, same count.
        names = [n.strip() for n in (x.get("authors") or "").split(";") if n.strip()]
        if not (names and all(len(n.split()) == 1 for n in names)
                and len(names) == x["_anu_profile_n_authors"] == x["n_authors"]):
            return False
        x["authors"] = x.get("_anu_profile_authors")
        return True
    x["n_authors"] = x["_anu_profile_n_authors"]
    if not x.get("authors"):
        x["authors"] = x.get("_anu_profile_authors")
    return True


def _anu_citation_journal(citation, title):
    """The one exact-ABDC journal title named in `citation`, a full citation
    string, provided the citation is for `title`. None if there is no such
    journal, or more than one."""
    from enrichment.abdc import known_titles, normalise_title

    if not citation or not title:
        return None
    if _normalise_title(title) not in _normalise_title(citation):
        return None
    titles = known_titles()
    hits = {seg.strip(" .'\"‘’“”") for seg in re.split(r"[,.]\s", citation)}
    hits = {seg for seg in hits if seg and normalise_title(seg) in titles}
    return hits.pop() if len(hits) == 1 else None


def _anu_repository_journal_repair(x, anu_names, fetch=None):
    """An ANU row whose journal is a repository's name (OpenAlex sometimes
    reports a university repository as the primary location) gets the
    journal named in OpenAlex's own `raw_source_name` citation for that DOI —
    only when that name is an exact ABDC title and the citation is for this
    paper. The ABDC rating and ISSNs follow from that exact title, the same
    way enrichment/abdc.py's title fallback sets them. Returns the journal
    applied, or None."""
    if x.get("name") not in anu_names:
        return None
    if not _repository_like_journal(x.get("journal")) or not x.get("doi"):
        return None
    from enrichment.abdc import normalise_title, title_issns, title_rating

    if fetch is None:
        from core.config import OA_HEADERS, OPENALEX_BASE
        from core.http import cached_get

        def fetch(doi):
            return cached_get(f"{OPENALEX_BASE}/doi:{doi}", headers=OA_HEADERS,
                              sleep=0.5, allow_404=True)
    try:
        work = fetch(x["doi"])
    except Exception as e:
        print(f"    ANU repository-journal repair: OpenAlex lookup failed for "
              f"{x['doi']}: {type(e).__name__} {e}")
        return None
    citations = {loc.get("raw_source_name") for loc in
                 [(work or {}).get("primary_location") or {}] + ((work or {}).get("locations") or [])}
    found = {_anu_citation_journal(c, x.get("title")) for c in citations if c}
    found.discard(None)
    if len(found) != 1:
        return None
    journal = found.pop()
    x["journal"] = journal
    x["abdc"] = title_rating(normalise_title(journal))
    x["abdc_title"] = journal
    x["abdc_match"] = "title (ANU repository-journal repair)"
    if not x.get("issns"):
        x["issns"] = list(title_issns(normalise_title(journal)) or [])
    return journal


# --- ANU-only (v28, docs/DECISIONS.md 5 Oct 2026): a rating for a journal
# that did not yet exist ------------------------------------------------------
#
# The ABDC title fallback matches on the journal's NAME. Rebecca Tan's "Flights
# of fancy", cited as "Journal of Financial Reporting, 1(2): 1-10 (2000)", was
# rated A as the AAA's Journal of Financial Reporting, whose ABDC "Year
# Inception" is 2016. A paper dated before the journal began cannot be in it,
# so the rating is withdrawn rather than guessed. When the row's ISSNs came
# from that same ABDC title match (`issn_source == "abdc_title"`), the
# Scimago and Clarivate values joined on those ISSNs are withdrawn too.
ANU_INCEPTION_LOG = []
_ISSN_JOINED_FIELDS = ("sjr", "sjr_quartile", "h_index", "cites_per_doc_2y", "scimago_year",
                       "scimago_title", "impact_factor", "impact_factor_5yr", "jcr_year")


def _anu_predates_abdc_inception(x, anu_names):
    """Withdraw the ABDC match from an ANU row dated before the matched
    journal's ABDC inception year. Returns (journal, inception) when it
    does, else None."""
    if x.get("name") not in anu_names or not x.get("abdc_title"):
        return None
    from enrichment.abdc import normalise_title, title_inception

    inception = title_inception(normalise_title(x["abdc_title"]))
    try:
        year = int(str(x.get("year"))[:4])
    except (TypeError, ValueError):
        return None
    if not inception or year >= inception:
        return None
    journal = x["abdc_title"]
    x["abdc"] = x["abdc_title"] = x["abdc_edition"] = None
    x["abdc_match"] = f"withdrawn: {year} is before the ABDC inception year {inception}"
    if x.get("issn_source") == "abdc_title":
        x["issns"] = []
        x["issn_source"] = None
        for field in _ISSN_JOINED_FIELDS:
            x[field] = None
    ANU_INCEPTION_LOG.append((x["name"], x.get("title"), year, journal, inception))
    return journal, inception


_MOJIBAKE_MARKERS = ("â€", "Ã", "Â")


def _anu_fix_mojibake(text):
    """Undo UTF-8 text that was decoded as cp1252 ("auditorsâ€™" ->
    "auditors’"), only when the repair is exact: the repaired string must
    encode back to the original bytes and carry no marker itself."""
    if not text or not any(m in text for m in _MOJIBAKE_MARKERS):
        return text
    try:
        fixed = text.encode("cp1252").decode("utf-8")
        if fixed.encode("utf-8").decode("cp1252") != text:
            return text
    except UnicodeError:
        return text
    if any(m in fixed for m in _MOJIBAKE_MARKERS):
        return text
    return fixed


def _single_case(title):
    letters = [c for c in title or "" if c.isalpha()]
    return len(letters) >= 3 and (all(c.islower() for c in letters)
                                  or all(c.isupper() for c in letters))


def _anu_cased_title(title, candidates):
    """Among `candidates` (other titles for the same DOI), the most common
    one that is the SAME title after normalisation, not itself single-case,
    and free of mojibake. Ties keep the first seen. None if there is none."""
    wanted = _normalise_title(title)
    ok = [c for c in candidates
          if c and _normalise_title(c) == wanted and not _single_case(c)
          and not any(m in c for m in _MOJIBAKE_MARKERS)]
    if not ok:
        return None
    counts = Counter(ok)
    return max(ok, key=lambda c: counts[c])


def _anu_crossref_title(x, fetch=None):
    """Crossref's registered title for the row's DOI, returned only if the
    record passes the v25 strict check against the row: same normalised
    title, same journal, year within one, and the ANU owner's surname among
    the authors."""
    from enrichment.abdc import normalise_title

    if fetch is None:
        from core.config import CR_HEADERS, CROSSREF_BASE
        from core.http import cached_get

        def fetch(doi):
            data = cached_get(f"{CROSSREF_BASE}/{doi}", headers=CR_HEADERS,
                              sleep=0.5, allow_404=True)
            return (data or {}).get("message")
    try:
        msg = fetch(x["doi"]) or {}
    except Exception as e:
        print(f"    ANU title-case repair: Crossref lookup failed for "
              f"{x['doi']}: {type(e).__name__} {e}")
        return None
    cr_title = re.sub(r"\s+", " ", ((msg.get("title") or [""])[0] or "")).strip()
    if not cr_title or _normalise_title(cr_title) != _normalise_title(x.get("title")):
        return None
    import html as _html
    journals = {normalise_title(_html.unescape(j)) for j in msg.get("container-title") or []}
    if normalise_title(x.get("journal_name") or x.get("journal")) not in journals:
        return None
    years = [(msg.get(k) or {}).get("date-parts", [[None]])[0][0]
             for k in ("published-online", "published-print", "issued")]
    try:
        row_year = int(x.get("year"))
    except (TypeError, ValueError):
        return None
    if not any(y and abs(int(y) - row_year) <= 1 for y in years):
        return None
    surname = (x.get("name") or "").split()[-1:]
    families = {(a.get("family") or "").lower() for a in msg.get("author") or []}
    if not surname or surname[0].lower() not in families:
        return None
    return cr_title


def _anu_repair_title_text(rows, pubs, anu_names, crossref_fetch=None):
    """Runs AFTER _harmonise_doi_metadata, on ANU rows only. That shared step
    picks one title per DOI across co-author copies, and for some ANU papers
    the one it picks is mis-encoded or entirely lower/upper case. This is a
    guard on the ANU side, not a fix to that step:
      1. mojibake in title/journal_name is undone when the repair is exact;
      2. an entirely lower- or upper-case title is replaced by a properly
         cased copy of the SAME title — from the pipeline's own other copies
         of that DOI, else from a strictly-verified Crossref record. If
         neither exists the title is left alone (Greg Shailer's 1994
         all-caps title is Crossref's own registered form, so it stays).
    Returns a list of (name, field, before, after) changes."""
    by_doi = {}
    for p in pubs:
        doi = (p.get("doi") or "").strip().lower()
        if doi and p.get("title"):
            by_doi.setdefault(doi, []).append(p["title"])
    changes = []
    for row in rows:
        if row.get("name") not in anu_names:
            continue
        for field in ("title", "journal_name"):
            fixed = _anu_fix_mojibake(row.get(field))
            if fixed != row.get(field):
                changes.append((row["name"], field, row[field], fixed))
                row[field] = fixed
        title = row.get("title")
        if not _single_case(title) or not row.get("doi"):
            continue
        cased = _anu_cased_title(title, [_anu_fix_mojibake(t) for t in
                                         by_doi.get(row["doi"].strip().lower(), [])])
        if cased is None:
            cr = _anu_crossref_title(row, crossref_fetch)
            cased = cr if cr and not _single_case(cr) else None
        if cased and cased != title:
            changes.append((row["name"], "title", title, cased))
            row["title"] = cased
    return changes


# --- ANU-only (v27, docs/DECISIONS.md 5 Oct 2026): one record per DOI, book
# reviews, profile copies of published rows --------------------------------
#
# Same gating as above: every function returns at once for a row whose
# `name` is not an ANU staff member, and tests/test_anu_final_rules.py runs
# each one on a non-ANU row and checks it comes back unchanged.

# A book review: the Crossref record has a volume, runs to at most this many
# pages, and registers no abstract. Measured on all 386 ANU DOIs with a
# Crossref record on 5 Oct 2026 (313 register a page range): exactly two
# span 4 pages or fewer (2 and 3 pages, both book reviews, no abstract); the
# next shortest is 5 pages with an abstract, and the shortest with no
# abstract is 9 pages. The volume test keeps out early-access records, which
# Crossref registers as pages "1-1" before an issue is assigned.
ANU_BOOK_REVIEW_MAX_PAGES = 4

# A DOI-less ANU profile row is a copy of a published row when, for the same
# researcher and the same journal, its year is within ANU_PROFILE_COPY_YEARS
# of the DOI record's online or print year, its title shares at least
# ANU_PROFILE_COPY_MIN_TITLE of its content words with the published title
# (see _title_containment), and, where both rows name co-authors, they share
# one. The title threshold sits in the gap measured on 5 Oct 2026: across
# every same-researcher, same-journal pair the highest-scoring pair of two
# different papers scored 0.50, and the lowest-scoring confirmed copy 0.67.
ANU_PROFILE_COPY_YEARS = 2
ANU_PROFILE_COPY_MIN_TITLE = 0.6

_TITLE_STOPWORDS = {"a", "an", "the", "of", "and", "in", "on", "for", "to", "from",
                    "with", "by", "at", "as", "is", "are", "do", "does", "its", "their"}
# Spaces (not a line break) between two words: where the publisher's
# metadata dropped an italic run ("The impact of   on generations of
# research" for "The impact of Ball and Brown (1968) on ...").
_DROPPED_RUN_RE = re.compile(r"[\w,)] {2,}[\w(]")
# A footnote marker at the end of a title: a digit glued to the last word
# ("...Consumer Search Theory1"), or an asterisk or dagger ("...Audit
# Quality? *", "...Disclosures†"). Publishers register these in the title
# field; they point to a footnote, not part of the title.
_TRAILING_FOOTNOTE_RE = re.compile(r"(?:(?<=[a-z]{3})\d|\s*[*†‡]+)$")

ANU_V27_LOG = []


def _anu_crossref_message(doi, fetch=None):
    """Crossref's record for `doi` ({} when there is none)."""
    if fetch is None:
        from core.config import CR_HEADERS, CROSSREF_BASE
        from core.http import cached_get

        def fetch(d):
            data = cached_get(f"{CROSSREF_BASE}/{d}", headers=CR_HEADERS,
                              sleep=0.5, allow_404=True)
            return (data or {}).get("message")
    try:
        return fetch(doi) or {}
    except Exception as e:
        print(f"    ANU DOI record: Crossref lookup failed for {doi}: "
              f"{type(e).__name__} {e}")
        return {}


def _loose_journal_key(name):
    import html as _html
    key = _normalise_title(_html.unescape(name or "").replace("&", " and "))
    return re.sub(r"^the ", "", key)


def _record_years(msg):
    def year(k):
        parts = ((msg.get(k) or {}).get("date-parts") or [[None]])[0]
        return parts[0] if parts and parts[0] else None
    return {k: year(k) for k in ("published-online", "published-print", "issued")}


def _record_year(msg):
    """The year this pipeline exports for a DOI: the print (issue) year when
    the record has one, else the record's issued year. Measured on 5 Oct
    2026, the existing pipeline already gave the print year on 96 of the 133
    ANU DOI rows whose online and print years differ (the profile pages and
    ORCID both cite the issue)."""
    years = _record_years(msg)
    return years["published-print"] or years["issued"] or years["published-online"]


def _fill_dropped_run(raw_title, candidates):
    """Repair a registered title whose italic run was dropped, from a
    pipeline copy that matches it exactly on both sides of the gap."""
    parts = [_normalise_title(p) for p in re.split(r" {2,}", raw_title)]
    if len(parts) < 2 or not all(parts):
        return None
    pattern = re.compile(r"^" + r" (.{1,80}?) ".join(re.escape(p) for p in parts) + r"$")
    for c in candidates:
        if c and pattern.match(_normalise_title(c)):
            return c
    return None


def _anu_registered_title(msg, candidates=()):
    """(title, note) from a Crossref record: tags and entities removed,
    whitespace collapsed, subtitle appended when registered separately. A
    title with a dropped run is repaired from `candidates` or refused."""
    from core.clean import clean_text

    raw = ((msg.get("title") or [""])[0] or "")
    if not raw.strip():
        return None, "no registered title"
    if _DROPPED_RUN_RE.search(raw.replace("\n", "\x00")):
        filled = _fill_dropped_run(re.sub(r"\s*\n\s*", " ", raw), candidates)
        if filled:
            return filled, "dropped run filled from a pipeline copy"
        # Layout spacing, not a gap: a pipeline copy reads the same with the
        # spaces closed up ("...Evidence and               Issues").
        closed = clean_text(raw)
        if not any(_normalise_title(c) == _normalise_title(closed) for c in candidates if c):
            return None, "registered title has a dropped run"
        raw = closed
    title = clean_text(raw)
    subtitle = clean_text(((msg.get("subtitle") or [""])[0] or ""))
    if subtitle and _normalise_title(subtitle) not in _normalise_title(title) \
            and not _is_running_head(subtitle, title):
        title = f"{title}: {subtitle}"
    return title, "registered"


def _is_running_head(subtitle, title):
    """True when a registered `subtitle` is a running head (the journal's
    short title), not part of the title. Wiley registers its running head in
    Crossref's subtitle field: "Integrated Reporting for Not-for-Profit
    Sector", "EARNINGS MANAGEMENT SIGNALS AND FORECAST ACCURACY". A running
    head is all caps, or mostly repeats the main title: at least
    ANU_PROFILE_COPY_MIN_TITLE of its content words already appear in it.
    A genuine subtitle adds words ("The motivations behind private equity
    activity in Australia")."""
    letters = [c for c in subtitle if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True
    words = set(_title_tokens(subtitle))
    if not words:
        return False
    main = set(_title_tokens(title))
    hit = sum(1 for w in words if w in main or any(
        difflib.SequenceMatcher(None, w, o).ratio() >= 0.85 for o in main))
    return hit / len(words) >= ANU_PROFILE_COPY_MIN_TITLE


def _title_tokens(title):
    words = _normalise_title(title).split()
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in words if w not in _TITLE_STOPWORDS and len(w) > 1]


def _title_containment(a, b):
    """Share of the shorter title's distinct content words found in the
    other title (a near-identical spelling counts: "stakeholers"). Word order
    and wording around them do not matter, which is how a working title
    differs from the published one."""
    ta, tb = set(_title_tokens(a)), set(_title_tokens(b))
    if not ta or not tb:
        return 0.0
    short, other = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hit = sum(1 for w in short if w in other or any(
        difflib.SequenceMatcher(None, w, o).ratio() >= 0.85 for o in other))
    return hit / len(short)


def _coauthor_surnames(authors, owner):
    from base_scrapers.anu import _fold_name, _split_name

    _, _, owner_surname = _split_name(owner)
    out = set()
    for name in (authors or "").split(";"):
        words = name.replace(".", " ").split()
        if words:
            out.add(_fold_name(words[-1]))
    out.discard(_fold_name(owner_surname))
    return out


def _anu_align_doi_records(rows, pubs, anu_names, fetch=None):
    """Every ANU row with a DOI: the DOI is lowercase, and the title and year
    are the DOI record's (the registered title; the print year, else the
    issued year). Refused, with the reason logged, when the record names a
    different journal or its title has lost words the row still has.
    Returns {doi: Crossref message} for the later rules."""
    from enrichment.abdc import normalise_title as abdc_key

    titles_by_doi, issns_by_doi = {}, {}
    for p in pubs:
        doi = (p.get("doi") or "").strip().lower()
        if doi and p.get("title"):
            titles_by_doi.setdefault(doi, []).append(p["title"])
        if doi:
            issns_by_doi.setdefault(doi, set()).update(
                i.replace("-", "").upper() for i in (p.get("issns") or []) if i)
    all_titles = [p.get("title") for p in pubs]
    records = {}
    for row in rows:
        if row.get("name") not in anu_names or not row.get("doi"):
            continue
        doi = row["doi"].strip().lower()
        if doi != row["doi"]:
            ANU_V27_LOG.append(("doi lowercased", row["name"], row["doi"], doi))
            row["doi"] = doi
            row["article_url"] = f"https://doi.org/{doi}"
            if (row.get("link") or "").lower() == f"https://doi.org/{doi}":
                row["link"] = row["article_url"]
        if doi not in records:
            records[doi] = _anu_crossref_message(doi, fetch)
        msg = records[doi]
        if not msg:
            continue
        journals = {_loose_journal_key(j) for j in msg.get("container-title") or []}
        journals |= {_loose_journal_key(abdc_key(j)) for j in msg.get("container-title") or []}
        mine = _loose_journal_key(row.get("journal_name"))
        same_journal = (
            mine in journals
            # "International Small Business Journal" is registered with its
            # subtitle ": Researching Entrepreneurship"
            or any(j.startswith(mine + " ") for j in journals if mine)
            or bool(issns_by_doi.get(doi, set())
                    & {i.replace("-", "").upper() for i in msg.get("ISSN") or []}))
        if not same_journal:
            ANU_V27_LOG.append(("refused: record names another journal", row["name"], doi,
                                f"{row.get('journal_name')!r} vs {msg.get('container-title')}"))
            continue
        title, note = _anu_registered_title(msg, titles_by_doi.get(doi, []) + all_titles)
        # A single-case registered title ("GENERAL EQUILIBRIUM ANALYSIS OF
        # HOLD-UP ...") takes the casing of a pipeline copy of the same title:
        # this DOI's copies first, else any copy. When every copy is single-
        # case it stays as registered (Greg Shailer's 1994 title).
        if title and _single_case(title):
            title = (_anu_cased_title(title, titles_by_doi.get(doi, []))
                     or _anu_cased_title(title, all_titles) or title)
        # A registered title that is the row's title with words missing from
        # the end (a subtitle the registry lacks) is refused, unless those
        # extra words only repeat the registered title (a doubled subtitle:
        # "...: International Evidence: International Evidence").
        current = _normalise_title(row.get("title"))
        registered = _normalise_title(title)
        if title and current.startswith(registered + " ") \
                and not set(current[len(registered):].split()) <= set(registered.split()):
            ANU_V27_LOG.append(("refused: registered title is shorter", row["name"], doi, title))
            title = None
        if title is None:
            if note != "registered":
                ANU_V27_LOG.append((f"refused: {note}", row["name"], doi, row.get("title")))
        elif title != row.get("title"):
            ANU_V27_LOG.append(("title from DOI record", row["name"], doi,
                                f"{row.get('title')!r} -> {title!r}"))
            row["title"] = title
        year = _record_year(msg)
        if year and str(year) != str(row.get("year") or ""):
            ANU_V27_LOG.append(("year from DOI record", row["name"], doi,
                                f"{row.get('year')} -> {year}"))
            row["year"] = str(year)
    return records


def _anu_strip_footnote_marker(rows, anu_names):
    """'...Personalization Contexts1' -> '...Personalization Contexts'."""
    for row in rows:
        if row.get("name") not in anu_names:
            continue
        title = row.get("title") or ""
        if len(title.split()) >= 4 and _TRAILING_FOOTNOTE_RE.search(title):
            fixed = _TRAILING_FOOTNOTE_RE.sub("", title).rstrip()
            ANU_V27_LOG.append(("footnote marker stripped", row["name"], row.get("doi"), title))
            row["title"] = fixed


def _anu_one_title_per_doi(rows, anu_names):
    """Where ANU rows sharing a DOI still disagree (the DOI record was
    refused or missing for one of them), all take the most common title and
    year; a tie goes to the fuller title and the later year."""
    groups = {}
    for row in rows:
        if row.get("name") in anu_names and row.get("doi"):
            groups.setdefault(row["doi"].lower(), []).append(row)
    for doi, group in groups.items():
        for field, tie in (("title", lambda v: len(_normalise_title(v))),
                           ("year", lambda v: str(v))):
            values = [r.get(field) for r in group if r.get(field) not in (None, "")]
            if len(set(values)) < 2:
                continue
            counts = Counter(values)
            chosen = max(values, key=lambda v: (counts[v], tie(v)))
            for r in group:
                if r.get(field) != chosen:
                    ANU_V27_LOG.append((f"{field} unified across the DOI", r["name"], doi,
                                        f"{r.get(field)!r} -> {chosen!r}"))
                    r[field] = chosen


def _page_span(page):
    m = re.match(r"^\D*(\d+)\s*[-–]\s*\D*(\d+)$", (page or "").strip())
    if not m:
        return None
    first, last = int(m.group(1)), int(m.group(2))
    return last - first + 1 if last >= first else None


def _is_book_review_record(msg):
    if not msg or not msg.get("volume") or msg.get("abstract"):
        return False
    span = _page_span(msg.get("page"))
    return span is not None and span <= ANU_BOOK_REVIEW_MAX_PAGES


def _anu_drop_book_reviews(rows, anu_names, records):
    """Drop ANU rows whose DOI record is a book review (see
    ANU_BOOK_REVIEW_MAX_PAGES), and a DOI-less copy of the same title for
    the same researcher."""
    dropped = set()
    for row in rows:
        if row.get("name") in anu_names and row.get("doi") \
                and _is_book_review_record(records.get(row["doi"].lower())):
            dropped.add((row["name"], _normalise_title(row.get("title"))))
    if not dropped:
        return rows
    kept = []
    for row in rows:
        if row.get("name") in anu_names and (row["name"], _normalise_title(row.get("title"))) in dropped:
            ANU_V27_LOG.append(("dropped: book review", row["name"], row.get("doi"), row.get("title")))
            TYPE_REVIEW_LOG.append({
                "action": "dropped", "reason": "book review (Crossref: short, no abstract)",
                "name": row["name"], "title": row.get("title"), "year": row.get("year"),
                "journal": row.get("journal_name"), "doi": row.get("doi"), "source": row.get("source")})
            continue
        kept.append(row)
    return kept


# A DOI-less ANU row that only OpenAlex supplied, and whose OpenAlex record
# names a conference as the source in its own citation text
# (`raw_source_name`), is a proceedings paper. OpenAlex files several AIS
# conference papers (PACIS 2008, 2010, 2015; ECIS 2009) under the Journal of
# the Association for Information Systems, which is how they came to be
# rated A*.
_PROCEEDINGS_RE = re.compile(r"\b(proceedings|conference)\b", re.IGNORECASE)


def _anu_openalex_proceedings_source(row, anu_names, fetch=None):
    """The conference `raw_source_name` that marks this row as a
    proceedings paper, else None."""
    if row.get("name") not in anu_names or row.get("doi") or row.get("source") != "OpenAlex":
        return None
    work_id = (row.get("link") or "").rsplit("/", 1)[-1]
    if not re.fullmatch(r"W\d+", work_id):
        return None
    if fetch is None:
        from core.config import OA_HEADERS, OPENALEX_BASE
        from core.http import cached_get

        def fetch(wid):
            return cached_get(f"{OPENALEX_BASE}/{wid}", headers=OA_HEADERS,
                              sleep=0.5, allow_404=True)
    try:
        work = fetch(work_id) or {}
    except Exception as e:
        print(f"    ANU proceedings check: OpenAlex lookup failed for {work_id}: "
              f"{type(e).__name__} {e}")
        return None
    names = [loc.get("raw_source_name") for loc in
             [work.get("primary_location") or {}] + (work.get("locations") or [])]
    return next((n for n in names if n and _PROCEEDINGS_RE.search(n)), None)


def _anu_drop_openalex_proceedings(rows, anu_names, fetch=None):
    kept = []
    for row in rows:
        venue = _anu_openalex_proceedings_source(row, anu_names, fetch)
        if venue:
            ANU_V27_LOG.append(("dropped: conference paper (OpenAlex)", row["name"],
                                row.get("link"), f"{row.get('title')!r} - {venue}"))
            TYPE_REVIEW_LOG.append({
                "action": "dropped", "reason": f"conference paper (OpenAlex citation: {venue})",
                "name": row["name"], "title": row.get("title"), "year": row.get("year"),
                "journal": row.get("journal_name"), "doi": row.get("doi"), "source": row.get("source")})
            continue
        kept.append(row)
    return kept


def _anu_one_row_per_doi(rows, anu_names):
    """A researcher keeps one row per DOI (the first, which build_publications
    sorts DOI-first and source-ordered)."""
    seen, kept = set(), []
    for row in rows:
        key = (row.get("name"), (row.get("doi") or "").lower())
        if row.get("name") in anu_names and key[1]:
            if key in seen:
                ANU_V27_LOG.append(("dropped: second row for the same DOI", row["name"],
                                    row["doi"], row.get("title")))
                continue
            seen.add(key)
        kept.append(row)
    return kept


def anu_profile_copy_of(cand, published, record=None):
    """True when DOI-less profile row `cand` is a copy of DOI row
    `published` for the same researcher (see ANU_PROFILE_COPY_*)."""
    if cand.get("doi") or cand.get("source") != "ANU staff profile" or not published.get("doi"):
        return False
    if cand.get("name") != published.get("name"):
        return False
    if _loose_journal_key(cand.get("journal_name")) != _loose_journal_key(published.get("journal_name")):
        return False
    years = {y for y in _record_years(record or {}).values() if y}
    if published.get("year"):
        years.add(int(published["year"]))
    try:
        if not any(abs(int(cand["year"]) - y) <= ANU_PROFILE_COPY_YEARS for y in years):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    if _title_containment(cand.get("title"), published.get("title")) < ANU_PROFILE_COPY_MIN_TITLE:
        return False
    a = _coauthor_surnames(cand.get("authors"), cand["name"])
    b = _coauthor_surnames(published.get("authors"), cand["name"])
    return not (a and b) or bool(a & b)


def _anu_drop_profile_copies(rows, anu_names, records):
    by_name = {}
    for row in rows:
        if row.get("name") in anu_names and row.get("doi"):
            by_name.setdefault(row["name"], []).append(row)
    kept = []
    for row in rows:
        if row.get("name") in anu_names and not row.get("doi"):
            match = next((p for p in by_name.get(row["name"], [])
                          if anu_profile_copy_of(row, p, records.get(p["doi"].lower()))), None)
            if match:
                ANU_V27_LOG.append(("dropped: profile copy of a published row", row["name"],
                                    match["doi"], f"{row.get('title')!r} ~ {match.get('title')!r}"))
                continue
        kept.append(row)
    return kept


# --- ANU-only (v28): forthcoming status ---------------------------------------
#
# Client's 19 Aug rule: a paper is forthcoming only when it is explicitly
# labelled so, never inferred (on 2 Sep the client called "no volume yet" on
# its own too broad). An ANU row is "forthcoming" when the researcher's own
# profile entry for it says "forthcoming" or "in press" AND it is not yet in
# an issue: it has no DOI, or its DOI record has neither a volume nor a print
# date. A labelled entry that has since reached an issue is "published";
# profile pages are often not updated.
ANU_STATUS_LOG = []


def _in_an_issue(record):
    """True when a Crossref record shows the paper in an issue; None when
    there is no record to tell."""
    if not record:
        return None
    return bool(record.get("volume") or _record_years(record)["published-print"])


def _labelled_profile_entry_for(row, labelled):
    """The researcher's labelled profile entry that `row` is the exported
    form of (same DOI, same title, or a profile copy of it), else None."""
    doi = (row.get("doi") or "").lower()
    for p in labelled.get(row.get("name"), []):
        if doi and (p.get("doi") or "").lower() == doi:
            return p
        if _normalise_title(p.get("title")) == _normalise_title(row.get("title")):
            return p
        if not p.get("doi") and doi and anu_profile_copy_of(
                {"name": p["name"], "doi": None, "source": "ANU staff profile",
                 "title": p.get("title"), "year": p.get("year"),
                 "journal_name": canonical_journal_name(p), "authors": p.get("authors")},
                row):
            return p
    return None


# A DOI-less row has no record to show whether it has reached an issue, so
# its label is trusted only while recent: dated this year or last. Isabel
# Wang's "2023 ... forthcoming" entry was printed in March 2023 (Crossref,
# the record v28 deliberately leaves unattached). This can only keep a row
# "published"; it never infers "forthcoming".
ANU_FORTHCOMING_MAX_AGE_YEARS = 1


def _anu_publication_status(rows, pubs, anu_names, records, this_year=None):
    this_year = this_year or datetime.now(timezone.utc).year
    labelled = {}
    for p in pubs:
        if p.get("name") in anu_names and p.get("_anu_forthcoming_label"):
            labelled.setdefault(p["name"], []).append(p)
    entries = {id(row): _labelled_profile_entry_for(row, labelled)
               for row in rows if row.get("name") in anu_names}
    # Status belongs to the paper: an ANU co-author's label covers every ANU
    # row of the same DOI (Kun Li's copy of Xin (Kelly) Liu's paper).
    labelled_dois = {(row.get("doi") or "").lower() for row in rows
                     if entries.get(id(row)) and row.get("doi")}
    for row in rows:
        if row.get("name") not in anu_names:
            continue
        doi = (row.get("doi") or "").lower()
        issue = _in_an_issue(records.get(doi)) if doi else False
        entry = entries.get(id(row)) or (doi in labelled_dois or None)
        recent = True
        if entry and not doi:
            try:
                recent = int(str(row.get("year"))[:4]) >= this_year - ANU_FORTHCOMING_MAX_AGE_YEARS
            except (TypeError, ValueError):
                recent = False
        if entry and recent and (not doi or issue is False):
            row["publication_status"] = "forthcoming"
            ANU_STATUS_LOG.append(("forthcoming", row["name"], doi, row.get("title")))
        else:
            row["publication_status"] = "published"
            if entry:
                why = ("no DOI and the label is not recent" if not recent else
                       "no DOI record to check" if issue is None else "now in an issue")
                ANU_STATUS_LOG.append((f"labelled, kept published: {why}", row["name"], doi,
                                       row.get("title")))
            elif doi and issue is False:
                ANU_STATUS_LOG.append(("online-first, not labelled: published", row["name"], doi,
                                       row.get("title")))


def _anu_final_rules(rows, pubs, anu_names, crossref_fetch=None):
    """The v27 ANU rules, in order. Non-ANU rows pass through untouched."""
    records = _anu_align_doi_records(rows, pubs, anu_names, crossref_fetch)
    _anu_strip_footnote_marker(rows, anu_names)
    _anu_one_title_per_doi(rows, anu_names)
    rows = _anu_one_row_per_doi(rows, anu_names)
    rows = _anu_drop_book_reviews(rows, anu_names, records)
    # A caller that supplies its own Crossref lookup (a test) is offline, so
    # the OpenAlex lookup is stubbed too.
    rows = _anu_drop_openalex_proceedings(rows, anu_names,
                                          (lambda w: {}) if crossref_fetch else None)
    rows = _anu_drop_profile_copies(rows, anu_names, records)
    ANU_STATUS_LOG.clear()
    _anu_publication_status(rows, pubs, anu_names, records)
    return rows


# Initials as a citation writes them: "G.", "C. A.", "R. C. W", "S", "J.-P.".
# Single capitals only, so a short surname ("Ho", "Ng", "Wu") is not one.
_INITIALS_RE = re.compile(r"^(?:[A-Z]\.?(?:\s+|-)?)+$")


def normalize_authors(text):
    r"""One author-list format for every university: "Given Surname; Given Surname".

    Most sources already give that. Three do not:
      Monash Pure / ANU profile citations
          "van Mourik, G., Watson, J. & Onsman, A."  -> "G. van Mourik; J. Watson; A. Onsman"
          "K.C. Ho, A. Karathanasopoulos, & J. Yu"   -> "K.C. Ho; A. Karathanasopoulos; J. Yu"
      UWA Pure BibTeX, with escaped braces left behind
          "Lyndie Bayne; Wee, \Marvin Ge Way\"       -> "Lyndie Bayne; Marvin Ge Way Wee"
    Without semicolons a comma list cannot be split reliably, so names could
    not be counted or searched.
    """
    if not text:
        return text
    bibtex = "\\" in text
    t = " ".join(text.replace("\\", "").split())
    t = re.sub(r"^with\s+", "", t, flags=re.I)
    if ";" in t or bibtex:
        # BibTeX names are already one per entry: "Smales, Lee Alan" is one person.
        parts = [p.strip() for p in t.split(";") if p.strip()]
    elif "," in t or " & " in t or " and " in t:
        t = re.sub(r",?\s+(?:&|and)\s+", ", ", t)
        tokens = [x.strip() for x in t.split(",") if x.strip()]
        parts, i = [], 0
        while i < len(tokens):
            if i + 1 < len(tokens) and _INITIALS_RE.match(tokens[i + 1]) \
                    and not _INITIALS_RE.match(tokens[i]):
                parts.append(f"{tokens[i]}, {tokens[i + 1]}")
                i += 2
            else:
                parts.append(tokens[i])
                i += 1
    else:
        return t
    out = []
    for part in parts:
        if part.count(",") == 1:
            surname, given = (x.strip() for x in part.split(","))
            if surname and given:
                part = f"{given} {surname}"
        out.append(part)
    return "; ".join(out)


# ------------------------------------------------------- non-articles
#
# The sources call many things a journal article that are not research:
# editorials, book reviews, front matter, retractions. OpenAlex types each
# DOI properly, so its type decides, for the kinds nobody would defend as a
# research article; a retracted or withdrawn article is dropped too. Book
# chapters, books, reports, preprints and "paratext" are only listed for
# review: OpenAlex calls some real articles in book-series journals
# chapters, and has typed A* papers as paratext. Every row dropped or
# flagged is written to <uni>_type_review.csv. A DOI in data/publication_keep.csv is never dropped
# (for OpenAlex mistakes).
NON_ARTICLE_TYPES = {"editorial", "book-review", "retraction", "erratum",
                     "letter", "conference-abstract"}
REVIEW_TYPES = {"Book Chapter", "Book", "Research Report", "Preprint", "paratext"}
_NON_ARTICLE_TITLE = re.compile(
    r"^\W*(foreword|preface|prelims|front matter|back matter|in memoriam|"
    r"obituary|editorial board|contents|index|errata|introduction|editorial|"
    r"guest editorial|editorial introduction|editor'?s'? note)\W*$"
    r"|^\W*(retraction|withdrawal) (note|notice)\b|^\W*(retracted|withdrawn)( article)?\s*:"
    # A title that opens with one of these is that kind of item, whatever
    # follows: "Book Review: GDP ...", "In Memoriam Dr. ...". Foreword and
    # preface need punctuation or on/to/by/for after them ("Foreword on
    # Special Issue: ...", "Preface - Editors' Note"), so a research title
    # such as "Foreword guidance and ..." is kept.
    r"|^\W*(book reviews?|in memoriam|editor'?s'?\s+note|editorial note)\b"
    r"|^\W*(foreword|preface)\s*([:\-–—]|(on|to|by|for)\b)", re.I)
# A discussant's piece on someone else's paper ("Discussion of ...",
# "... - Discussion", "... - Comment"). Journals print them alongside the
# paper, but they are not research articles.
_DISCUSSION_TITLE = re.compile(
    r"^\W*(discussion|comment)\W*$|^\W*discussion of\b"
    r"|(\s[-\u2013\u2014]|:)\s*(discussion|comment)\W*$", re.I)
TYPE_REVIEW_LOG = []

_keep_path = Path(__file__).resolve().parent / "data" / "publication_keep.csv"
_KEEP_DOIS = set()
if _keep_path.exists():
    with _keep_path.open(encoding="utf-8") as _f:
        _KEEP_DOIS = {(r.get("doi") or "").strip().lower()
                      for r in csv.DictReader(_f) if (r.get("doi") or "").strip()}


def non_article_reason(x):
    """Why this row is not a research article, or None. ('drop'|'review', why)."""
    doi = (x.get("doi") or "").strip().lower()
    if doi and doi in _KEEP_DOIS:
        return None
    t = x.get("oa_type")
    if t in NON_ARTICLE_TYPES:
        return ("drop", f"OpenAlex type: {t}")
    # Frontiers registers its conference abstracts as 10.3389/conf.*, and
    # OpenAlex types them as articles.
    if doi.startswith("10.3389/conf."):
        return ("drop", "conference abstract (Frontiers)")
    if x.get("oa_retracted"):
        return ("drop", "retracted or withdrawn (OpenAlex)")
    if _NON_ARTICLE_TITLE.match(x.get("title") or ""):
        return ("drop", "title marks it as front matter or a notice")
    if _DISCUSSION_TITLE.search(x.get("title") or ""):
        return ("drop", "discussant piece")
    if t in REVIEW_TYPES:
        return ("review", f"OpenAlex type: {t}")
    return None


def _log_non_article(x, action, why):
    TYPE_REVIEW_LOG.append({
        "action": action, "reason": why, "name": x.get("name"),
        "title": x.get("title"), "year": x.get("year"),
        "journal": x.get("journal"), "doi": x.get("doi"),
        "source": x.get("source")})


def build_publications(pubs, records=None, keep_type="Journal Article",
                       verbose=True, crossref_fetch=None):
    """Records sort DOI-first so the better-catalogued copy survives dedup.

    `crossref_fetch` (doi -> Crossref message) replaces the live Crossref
    lookup the ANU rules make; tests pass one so they stay offline.

    ORCID is carried onto each row: names collide across eight universities
    (two staff already share the surname Tan), so a name is not a safe join
    key in a merged table.
    """
    SKIPPED_PREFIX_DUPS.clear()
    ANU_INCEPTION_LOG.clear()
    TYPE_REVIEW_LOG.clear()
    orcid_by_name = {r["name_clean"]: r.get("orcid") for r in (records or [])}
    anu_names = _anu_staff_names(records)
    out = []
    kept_dois_by_key = {}
    excluded_ssrn_preprints = 0
    excluded_correction_notices = 0
    excluded_off_field_journals = 0
    anu_title_repairs = 0
    anu_repository_repairs = []
    anu_author_fallbacks = 0
    missing_journal = 0
    # (name, title) of every dropped non-article, so a DOI-less copy of the
    # same item (a profile page listing the editorial) cannot slip through.
    dropped_non_articles = set()
    for x in sorted(pubs, key=lambda r: (r.get("doi") is None)):
        if x.get("type") != keep_type or not x.get("title"):
            continue
        # The client's 9 Sep rule excludes corrigenda/errata. A Web of
        # Science "(vol N, pg N, YYYY)" locator is that same kind of
        # correction notice, just pointing back at the original article by
        # citation rather than by the word "erratum" — see FIX L bug above
        # _is_prefix_duplicate. Applies to every university, not just ANU.
        if _is_correction_notice(x.get("title")):
            excluded_correction_notices += 1
            continue
        flag = non_article_reason(x)
        title_key = (x.get("name"), _normalise_title(x["title"]))
        if flag and flag[0] == "drop":
            _log_non_article(x, "dropped", flag[1])
            dropped_non_articles.add(title_key)
            continue
        if not x.get("doi") and title_key in dropped_non_articles:
            _log_non_article(x, "dropped", "copy of a dropped non-article")
            continue
        # Title repair runs before anything reads the title: the dedup key
        # below is computed from it, so repairing afterwards would key the
        # row on the mangled form and defeat FIX K / FIX L.
        if x.get("name") in anu_names:
            repaired = _anu_repair_title(x["title"])
            if repaired != x["title"]:
                anu_title_repairs += 1
                x["title"] = repaired
        repaired_journal = _anu_repository_journal_repair(x, anu_names)
        if repaired_journal:
            anu_repository_repairs.append((x["name"], x["title"], repaired_journal))
        _anu_predates_abdc_inception(x, anu_names)
        journal_name = canonical_journal_name(x)
        # A row cannot be delivered as a verified journal article when no
        # journal can be named. Keep such records upstream for review, but do
        # not let them into the client-facing publication table.
        if not journal_name:
            missing_journal += 1
            continue
        if _is_anu_off_field_journal(x, anu_names):
            excluded_off_field_journals += 1
            continue
        if _is_anu_unranked_ssrn_preprint(x, anu_names):
            excluded_ssrn_preprints += 1
            continue
        corrected_doi = _correct_doi(x.get("doi"))
        if corrected_doi != x.get("doi"):
            x["doi"] = corrected_doi
            if x.get("link", "").lower().startswith("https://doi.org/"):
                x["link"] = f"https://doi.org/{corrected_doi}"
        k = (x["name"], _normalise_title(x["title"]))
        doi = (x.get("doi") or "").strip().lower()
        if k not in kept_dois_by_key:
            kept_dois_by_key[k] = set()
            if doi:
                kept_dois_by_key[k].add(doi)
        else:
            # A later row with the same (name, normalised title) is a
            # duplicate unless it carries a DOI genuinely different from
            # every DOI already kept under this key — curly vs straight
            # quotes and cosmetic differences must not let a no-DOI page
            # copy survive next to the properly-identified one.
            if not doi or doi in kept_dois_by_key[k]:
                continue
            kept_dois_by_key[k].add(doi)
        if _anu_author_fallback(x, anu_names):
            anu_author_fallbacks += 1
        if flag:
            _log_non_article(x, "review", flag[1])
        out.append({
            "name": x["name"],
            "orcid": orcid_by_name.get(x["name"]),
            "source_id": x.get("source_id"),
            "journal_name": journal_name,
            "title": x["title"],
            "year": x.get("year"),
            "author_count": x.get("n_authors"),
            "authors": normalize_authors(x.get("authors")),
            "doi": x.get("doi"),
            "article_url": (f"https://doi.org/{x['doi']}" if x.get("doi")
                            else x.get("link")),
            "link": x.get("link"),
            "quality_rank": x.get("abdc"),
            "sjr_quartile": x.get("sjr_quartile"),
            "citation_percentile": x.get("citation_percentile"),
            "cited_by_count": x.get("cited_by_count"),
            "fwci": x.get("fwci"),
            "oa_status": x.get("oa_status"),
            "oa_url": x.get("oa_url"),
            "publication_status": x.get("publication_status") or "published",
            "source": x.get("source"),
        })

    before_near_dup = len(out)
    out = merge_near_duplicates(out)
    out = _harmonise_doi_metadata(out)
    anu_text_changes = _anu_repair_title_text(out, pubs, anu_names)
    ANU_V27_LOG.clear()
    if anu_names:
        out = _anu_final_rules(out, pubs, anu_names, crossref_fetch)

    if verbose:
        dropped = Counter((x.get("source"), x.get("type"))
                          for x in pubs if x.get("type") != keep_type)
        if dropped:
            print("  excluded by type:")
            for (s, t), n in dropped.most_common(10):
                print(f"    {n:4}  {s or '?':10} {t}")
        if missing_journal:
            print(f"  excluded {missing_journal} journal-article row(s) with no verified journal name")
        _dropped = sum(1 for r in TYPE_REVIEW_LOG if r["action"] == "dropped")
        _review = len(TYPE_REVIEW_LOG) - _dropped
        if _dropped or _review:
            print(f"  non-articles: dropped {_dropped}, {_review} flagged for review "
                  f"(see <uni>_type_review.csv)")
        if excluded_correction_notices:
            print(f"  excluded {excluded_correction_notices} published-correction "
                  f"row(s) carrying a '(vol N, pg N, YYYY)' locator")
        near_dup_removed = before_near_dup - len(out)
        if near_dup_removed:
            print(f"  removed {near_dup_removed} near-duplicate row(s) (FIX G)")
        if excluded_off_field_journals:
            print(f"  excluded {excluded_off_field_journals} ANU row(s) in an "
                  f"off-field clinical journal (see ANU_OFF_FIELD_JOURNAL_KEYWORDS)")
        if excluded_ssrn_preprints:
            print(f"  excluded {excluded_ssrn_preprints} ANU SSRN working "
                  f"paper row(s) with no journal and no ABDC rank")
        for name, title, journal in anu_repository_repairs:
            print(f"  ANU repository-journal repair: {name}: {title[:60]!r} -> {journal!r}")
        if anu_author_fallbacks:
            print(f"  {anu_author_fallbacks} ANU profile row(s) kept the profile's own "
                  f"author count (DOI enrichment returned no authors)")
        for name, field, before, after in anu_text_changes:
            print(f"  ANU {field} text repair: {name}: {before[:60]!r} -> {after[:60]!r}")
        for status, name, doi, title in ANU_STATUS_LOG:
            print(f"  ANU status: {status}: {name}: {doi or '(no DOI)'} {str(title)[:60]!r}")
        for name, title, year, journal, inception in ANU_INCEPTION_LOG:
            print(f"  ANU rating withdrawn: {name}: {title[:60]!r} ({year}) predates "
                  f"{journal!r} (ABDC inception {inception})")
        if ANU_V27_LOG:
            print(f"  ANU DOI-record/duplicate rules (v27): "
                  + ", ".join(f"{k} {n}" for k, n in
                              Counter(e[0] for e in ANU_V27_LOG).most_common()))
            for entry in ANU_V27_LOG:
                if not entry[0].startswith(("title from", "year from")):
                    print("    " + " | ".join(str(v)[:110] for v in entry))
        if anu_title_repairs:
            print(f"  repaired {anu_title_repairs} ANU title(s) at export "
                  f"time (FIX I, applied here for a row from a non-page source)")
        if SKIPPED_PREFIX_DUPS:
            print(f"  {len(SKIPPED_PREFIX_DUPS)} prefix-duplicate pair(s) "
                  f"(FIX L) matched but NOT merged — no doi to pick a side:")
            for kept_side, other_side in SKIPPED_PREFIX_DUPS:
                print(f"    {kept_side.get('name')!r}: "
                      f"{kept_side.get('title')!r} / {other_side.get('title')!r}")
    return out


def build_harvest(records, pubs, publications, sources=None):
    """One row per university per source (data dictionary 3.5.4)."""
    now = datetime.now(timezone.utc).isoformat()
    unis = {r["university"] for r in records}
    srcs = sources or {x.get("source") for x in pubs if x.get("source")}

    rows = []
    for uni in sorted(unis):
        for src in sorted(s for s in srcs if s):
            years = [int(p["year"]) for p in publications
                     if p.get("year") and p.get("source") == src]
            rows.append({
                "university": uni,
                "source": src,
                "last_run": now,
                "latest_year": max(years) if years else None,
                "record_count": sum(1 for x in pubs if x.get("source") == src),
            })
    return rows


def _whole_numbers(df):
    """CSV-only: pandas promotes an int column to float64 the moment one
    value is missing (NaN has no integer representation), so a column like
    author_count renders in the csv as "2.0" instead of "2" as soon as a
    single row is missing it — 571 of ANU's 574 rows, see
    scratch/_anu17/REPORT.md task 1. Cast every numeric column whose
    non-null values are all mathematically whole (author_count, year,
    cited_by_count, and any other column that happens to be gap-free
    integers) to the nullable Int64 dtype, so a gap renders as an empty
    cell instead of a float. A genuinely fractional column (fwci, sjr, ...)
    is left alone because at least one of its non-null values has a
    fractional part. JSON output is untouched — this only runs on the
    DataFrame built for the csv.
    """
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        if (non_null % 1 == 0).all():
            df[col] = df[col].astype("Int64")
    return df


def write(tables, out_dir=None, verbose=True):
    """Write the four tables into `final output/<uni>/`, named `<uni>_<table>`.

    The university prefix is redundant inside a folder already named after it,
    but the agreed structure asks for it and it means a file still says which
    university it belongs to once someone has copied it somewhere else, which
    is how these files actually travel.

    The prefix is the folder's own name, so nothing has to be passed down and
    it cannot disagree with the folder it is written into.
    """
    out_dir = out_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{out_dir.name}_" if out_dir != OUTPUT_DIR else ""
    for name, data in tables.items():
        stem = f"{prefix}{name}"
        with open(out_dir / f"{stem}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _whole_numbers(pd.DataFrame(data)).to_csv(out_dir / f"{stem}.csv", index=False)
        if verbose:
            print(f"  {name:14} {len(data):5}  ->  {out_dir / (stem + '.csv')}")


def export(records, pubs, out_dir=None, drop_staff_without_pubs=False,
           verbose=True):
    publications = build_publications(pubs, records, verbose=verbose)
    staff = build_staff(records)

    # job_title stays the university's own raw string. Where it gives no
    # academic rank (nothing listed, or only a role such as "Program
    # Director"), a rank confirmed by hand in data/staff_overrides.csv fills
    # academic_level if blank, so the person is ranked; a blank job_title is
    # filled with the override's title. A level the source supports is never
    # overridden.
    _overrides = {}
    _overrides_path = Path(__file__).resolve().parent / "data" / "staff_overrides.csv"
    if _overrides_path.exists():
        import csv as _csv
        with _overrides_path.open(encoding="utf-8") as _f:
            _overrides = {
                (row["university"].strip().lower(), row["name"].strip()): row["job_title"].strip()
                for row in _csv.DictReader(_f) if row.get("job_title", "").strip()
            }

    # The file names a university by its output-folder key ("uq"); the record
    # carries the full name ("University of Queensland"), so an exact match
    # never fired. Each key maps to a phrase found only in its own university's
    # name ("university of sydney", not "sydney", which UNSW Sydney contains).
    _UNI_KEYS = {
        "adelaide": "adelaide", "anu": "australian national",
        "monash": "monash", "unimelb": "university of melbourne",
        "unsw": "unsw", "uq": "university of queensland",
        "usyd": "university of sydney", "uwa": "university of western australia",
    }

    def _override_for(s):
        uni = (s.get("university") or "").strip().lower()
        name = (s.get("name") or "").strip()
        return next((v for (u, n), v in _overrides.items()
                     if n == name and _UNI_KEYS.get(u, u) in uni), None)

    _applied = 0
    for _s in staff:
        _job, _ = split_job_title(_s.get("job_title"), _s.get("academic_level"))
        # A title with no rank in it ("Enterprise Fellow in data ...") still
        # leaves the level blank, so an override applies there too.
        if _job and _s.get("academic_level"):
            continue
        _ov = _override_for(_s)
        if not _ov:
            continue
        if not _s.get("job_title"):
            _s["job_title"] = _ov
        if not _s.get("academic_level"):
            _s["academic_level"] = level(rank(split_job_title(_ov)[0]))
        _applied += 1
    if verbose and _applied:
        print(f"  applied {_applied} staff title override(s) from staff_overrides.csv")

    # The client's three columns. Runs after the overrides and the
    # normalisation above, so it sees the job title the CSV will actually
    # carry rather than the scraped one.
    _titled = _admin = 0
    _no_level = []
    for _s in staff:
        _s["academic_title"] = academic_title_for_level(_s.get("academic_level"),
                                                        _s.get("job_title"))
        _s["admin_title"] = admin_title_from(_s.get("job_title"))
        if _s["academic_title"]:
            _titled += 1
        else:
            _no_level.append(_s["name"])
        if _s["admin_title"]:
            _admin += 1
    if verbose:
        print(f"  academic_title set on {_titled} of {len(staff)} staff, "
              f"admin_title on {_admin}")
        if _no_level:
            print(f"  {len(_no_level)} with no academic title, because their "
                  f"academic_level is blank or outside B-E: "
                  f"{', '.join(_no_level[:4])}"
                  + (" ..." if len(_no_level) > 4 else ""))


    # Runs after the overrides so a title filled from staff_overrides.csv
    # ("Teaching Specialist") counts too. Their publications go with them,
    # and the journals table below is built from what is left.
    _teaching = {s["name"] for s in staff if is_teaching_role(s.get("job_title"))}
    # A person listed by two universities counts at one of them only:
    # data/staff_exclusions.csv names the listing to drop and why.
    _excl_path = Path(__file__).resolve().parent / "data" / "staff_exclusions.csv"
    if _excl_path.exists():
        import csv as _csv
        with _excl_path.open(encoding="utf-8") as _f:
            _excluded = {(row["university"].strip().lower(), row["name"].strip())
                         for row in _csv.DictReader(_f) if row.get("name", "").strip()}
        _dropped = {s["name"] for s in staff
                    if any(n == s["name"] and _UNI_KEYS.get(u, u) in (s.get("university") or "").lower()
                           for u, n in _excluded)}
        staff = [s for s in staff if s["name"] not in _dropped]
        publications = [p for p in publications if p["name"] not in _dropped]
        if verbose and _dropped:
            print(f"  excluded {len(_dropped)} staff listed in staff_exclusions.csv: "
                  f"{', '.join(sorted(_dropped))}")
    if _teaching:
        staff = [s for s in staff if s["name"] not in _teaching]
        publications = [p for p in publications if p["name"] not in _teaching]
        if verbose:
            print(f"  excluded {len(_teaching)} teaching-focused staff (FR4): "
                  f"{', '.join(sorted(_teaching)[:4])}"
                  + (" ..." if len(_teaching) > 4 else ""))

    if drop_staff_without_pubs:
        have = {p["name"] for p in publications}
        before = len(staff)
        staff = [s for s in staff if s["name"] in have]
        if verbose and before != len(staff):
            print(f"  dropped {before - len(staff)} staff with no publications")

    tables = {
        "staff": staff,
        "journals": build_journals(
            pubs, used_names={p["journal_name"] for p in publications}
        ),
        "publications": publications,
        "harvest": build_harvest(records, pubs, publications),
    }
    write(tables, out_dir, verbose)
    _review_dir = out_dir or OUTPUT_DIR
    _prefix = f"{_review_dir.name}_" if _review_dir != OUTPUT_DIR else ""
    _review_file = _review_dir / f"{_prefix}type_review.csv"
    if TYPE_REVIEW_LOG:
        pd.DataFrame(TYPE_REVIEW_LOG).to_csv(_review_file, index=False)
    elif _review_file.exists():
        _review_file.unlink()
    return tables
