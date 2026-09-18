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
        if not (exact_title and exact_year and compatible_journal):
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
            matched = next(
                (pos for pos, rep_i in enumerate(representatives)
                 if is_near_duplicate(r, rows[rep_i])),
                None,
            )
            if matched is None:
                representatives.append(i)
                continue
            rep_i = representatives[matched]
            winner = _prefer(rows[rep_i], r)
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
        # The agreed staff dictionary asks for the official/raw job title.
        # title_clean is only the derived academic-rank label and previously
        # erased valid roles such as teaching-focused appointments.
        "job_title": p.get("title") or p.get("title_clean"),
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


def build_publications(pubs, records=None, keep_type="Journal Article",
                       verbose=True):
    """Records sort DOI-first so the better-catalogued copy survives dedup.

    ORCID is carried onto each row: names collide across eight universities
    (two staff already share the surname Tan), so a name is not a safe join
    key in a merged table.
    """
    orcid_by_name = {r["name_clean"]: r.get("orcid") for r in (records or [])}
    out = []
    kept_dois_by_key = {}
    missing_journal = 0
    for x in sorted(pubs, key=lambda r: (r.get("doi") is None)):
        if x.get("type") != keep_type or not x.get("title"):
            continue
        journal_name = x.get("abdc_title") or x.get("journal")
        # A row cannot be delivered as a verified journal article when no
        # journal can be named. Keep such records upstream for review, but do
        # not let them into the client-facing publication table.
        if not journal_name:
            missing_journal += 1
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
            "journal_name": journal_name,
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
            "publication_status": x.get("publication_status") or "published",
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
        if missing_journal:
            print(f"  excluded {missing_journal} journal-article row(s) with no verified journal name")
        near_dup_removed = before_near_dup - len(out)
        if near_dup_removed:
            print(f"  removed {near_dup_removed} near-duplicate row(s) (FIX G)")
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
        pd.DataFrame(data).to_csv(out_dir / f"{stem}.csv", index=False)
        if verbose:
            print(f"  {name:14} {len(data):5}  ->  {out_dir / (stem + '.csv')}")


def export(records, pubs, out_dir=None, drop_staff_without_pubs=False,
           verbose=True):
    publications = build_publications(pubs, records, verbose=verbose)
    staff = build_staff(records)

    # Apply manual staff title overrides from data/staff_overrides.csv.
    # This ensures titles confirmed from profile screenshots survive pipeline reruns.
    _overrides_path = Path(__file__).resolve().parent / "data" / "staff_overrides.csv"
    if _overrides_path.exists():
        import csv as _csv
        with _overrides_path.open(encoding="utf-8") as _f:
            _overrides = {
                (row["university"].strip().lower(), row["name"].strip()): row["job_title"].strip()
                for row in _csv.DictReader(_f)
            }
        _applied = 0
        for _s in staff:
            _key = (_s.get("university", "").strip().lower(), (_s.get("name") or "").strip())
            if _key in _overrides and not _s.get("job_title"):
                _s["job_title"] = _overrides[_key]
                _applied += 1
        if verbose and _applied:
            print(f"  applied {_applied} staff title override(s) from staff_overrides.csv")

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
