"""Filter, deduplicate and write the four tables.

Identical for every university. Filtering happens once, here, at the end —
retrieval upstream is deliberately unfiltered so that exclusions are
visible and reversible rather than baked into each source.
"""

import difflib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from core.config import OUTPUT_DIR
from core.titles import level

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


def _dedup_doi(doi):
    """For near-duplicate comparison ONLY — never changes the exported doi
    value. An SSRN preprint DOI doesn't identify a specific publication the
    way a real journal DOI does: the same paper's published version usually
    gets its own, different DOI, so an SSRN DOI here counts as no DOI."""
    d = (doi or "").strip().lower()
    return "" if d.startswith(SSRN_DOI_PREFIX) else d


def _differing_part_marker(title_a, title_b):
    ma = _PART_MARKER_RE.search(title_a or "")
    mb = _PART_MARKER_RE.search(title_b or "")
    return bool(ma and mb and ma.group(1).lower() != mb.group(1).lower())


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
    if ratio < NEAR_DUP_TITLE_RATIO:
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
    if _normalise_title(a["title"]) != _normalise_title(b["title"]):
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


def build_staff(records):
    return [{
        "name": p["name_clean"],
        "job_title": p.get("title_clean"),
        "academic_level": p.get("level_code") or level(p.get("title_clean")),
        "university": p["university"],
        "field_of_research": p["discipline"],
        "source_id": p.get("source_id"),
        "orcid": p.get("orcid"),
        "profile_url": p["profile_url"],
    } for p in records]


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
        key = x.get("abdc_title") or x.get("journal")
        if used_names is not None and key not in used_names:
            continue
        candidate = {
            "journal_name": key,
            "journal_raw": x["journal"],
            "publisher": x.get("publisher"),
            "issn": "; ".join(x.get("issns") or []) or None,
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
                merged = existing + [v for v in incoming if v not in existing]
                current[field] = "; ".join(merged) or None
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


def build_publications(pubs, records=None, keep_type="Journal Article",
                       verbose=True):
    """Records sort DOI-first so the better-catalogued copy survives dedup.

    ORCID is carried onto each row: names collide across eight universities
    (two staff already share the surname Tan), so a name is not a safe join
    key in a merged table.
    """
    SKIPPED_PREFIX_DUPS.clear()
    orcid_by_name = {r["name_clean"]: r.get("orcid") for r in (records or [])}
    anu_names = _anu_staff_names(records)
    out = []
    kept_dois_by_key = {}
    excluded_ssrn_preprints = 0
    anu_title_repairs = 0
    for x in sorted(pubs, key=lambda r: (r.get("doi") is None)):
        if x.get("type") != keep_type or not x.get("title"):
            continue
        if x.get("name") in anu_names:
            repaired = _anu_repair_title(x["title"])
            if repaired != x["title"]:
                anu_title_repairs += 1
                x["title"] = repaired
        if _is_anu_unranked_ssrn_preprint(x, anu_names):
            excluded_ssrn_preprints += 1
            continue
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
        out.append({
            "name": x["name"],
            "orcid": orcid_by_name.get(x["name"]),
            "source_id": x.get("source_id"),
            "journal_name": x.get("abdc_title") or x.get("journal") or "unknown",
            "title": x["title"],
            "year": x.get("year"),
            "author_count": x.get("n_authors"),
            "authors": x.get("authors"),
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
            "publication_status": "published",
            "source": x.get("source"),
        })

    before_near_dup = len(out)
    out = merge_near_duplicates(out)

    if verbose:
        dropped = Counter((x.get("source"), x.get("type"))
                          for x in pubs if x.get("type") != keep_type)
        if dropped:
            print("  excluded by type:")
            for (s, t), n in dropped.most_common(10):
                print(f"    {n:4}  {s or '?':10} {t}")
        near_dup_removed = before_near_dup - len(out)
        if near_dup_removed:
            print(f"  removed {near_dup_removed} near-duplicate row(s) (FIX G)")
        if excluded_ssrn_preprints:
            print(f"  excluded {excluded_ssrn_preprints} ANU SSRN working "
                  f"paper row(s) with no journal and no ABDC rank")
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
    return tables
