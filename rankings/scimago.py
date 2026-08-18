"""
Scimago Journal Rank (SJR) join — CITS3200 Group 20.

Adds SJR score, quartile, h-index and citations-per-document to any of our
publications CSVs. Requested by the client on 12 August as a second ranking
source alongside ABDC.

    python scimago.py --publications ../output/unsw_publications.csv \
                      --scimago scimagojr-2025.csv

Writes <name>_with_scimago.csv and <name>_with_scimago_unmatched.csv.

Column names match Sean's UQ output (sjr, sjr_quartile, h_index,
cites_per_doc_2y) so the two universities merge without renaming.

THE LIST FILE IS NOT IN THIS REPO
---------------------------------
Download it from https://www.scimagojr.com/journalrank.php — the "Download data"
link, or https://www.scimagojr.com/journalrank.php?out=xls directly. It is free
and needs no login. Despite the "xls" in the URL it is a **semicolon-separated
CSV**, so don't let Excel convert it before you use it — save it as-is.

Two things about that file that will bite you if you read it with plain
pandas defaults: the separator is `;`, and the decimal separator is `,` — SJR
values look like "104,065", which a naive reader turns into a hundred thousand.
This module handles both.

Matching is shared with abdc.py (journal_match.py), so a journal that ABDC
matches will match here the same way. See that module for why matching is exact
by default.
"""

import argparse
import csv
import sys
from collections import Counter

import journal_match as jm

ADDED_COLUMNS = ["sjr", "sjr_quartile", "h_index", "cites_per_doc_2y",
                 "scimago_categories", "scimago_matched_title",
                 "scimago_match_type"]

# Scimago's headers carry the data year — "Total Docs. (2025)" — so they are
# matched by prefix rather than by literal.
TITLE_HEADERS = ("title",)
ISSN_HEADERS = ("issn",)
SJR_HEADERS = ("sjr",)
QUARTILE_HEADERS = ("sjr best quartile", "best quartile", "quartile")
H_INDEX_HEADERS = ("h index", "h-index")
CITES_HEADERS = ("citations / doc. (2years)", "cites / doc. (2years)",
                 "citations doc 2years", "cites doc 2years")
CATEGORY_HEADERS = ("categories",)
TYPE_HEADERS = ("type",)

QUARTILES = {"Q1", "Q2", "Q3", "Q4"}


def _find_column(headers, candidates):
    normalised = [jm.normalise(h) for h in headers]
    for candidate in candidates:
        key = jm.normalise(candidate)
        if key in normalised:
            return normalised.index(key)
    for candidate in candidates:
        key = jm.normalise(candidate)
        for i, header in enumerate(normalised):
            if header.startswith(key) or header.endswith(key):
                return i
    return None


def clean_number(value):
    """Scimago writes decimals the European way: '104,065' is 104.065."""
    if value is None:
        return None
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    # Only treat the comma as a decimal point — these fields never group
    # thousands with commas, so there is no ambiguity to resolve.
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def clean_quartile(value):
    if not value:
        return None
    text = str(value).strip().upper()
    return text if text in QUARTILES else None


def load_scimago(path, journals_only=True):
    """Read the Scimago export into a lookup table keyed by title and ISSN."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.readline()
        f.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        rows = list(csv.reader(f, delimiter=delimiter))

    if not rows:
        raise SystemExit(f"{path} is empty.")

    headers = rows[0]
    col_title = _find_column(headers, TITLE_HEADERS)
    if col_title is None:
        raise SystemExit(
            f"No Title column in {path}. Headers seen: {headers[:12]}. "
            "Make sure this is the Scimago journal rank export, saved as "
            "downloaded rather than re-saved by Excel.")

    columns = {
        "issn": _find_column(headers, ISSN_HEADERS),
        "sjr": _find_column(headers, SJR_HEADERS),
        "quartile": _find_column(headers, QUARTILE_HEADERS),
        "h_index": _find_column(headers, H_INDEX_HEADERS),
        "cites": _find_column(headers, CITES_HEADERS),
        "categories": _find_column(headers, CATEGORY_HEADERS),
        "type": _find_column(headers, TYPE_HEADERS),
    }

    def cell(row, key):
        i = columns[key]
        return row[i] if i is not None and i < len(row) else None

    index, skipped, non_journal = {}, 0, 0
    for row in rows[1:]:
        if col_title >= len(row):
            continue
        title = (row[col_title] or "").strip()
        if not title:
            skipped += 1
            continue
        # Scimago also lists book series and conference proceedings. The client
        # asked for journals, and a book series carrying a quartile would be
        # misleading in a journal-ranking column.
        kind = (cell(row, "type") or "").strip().lower()
        if journals_only and kind and kind != "journal":
            non_journal += 1
            continue

        issns = jm.split_issns(cell(row, "issn"))
        record = {
            "title": title,
            "sjr": clean_number(cell(row, "sjr")),
            "sjr_quartile": clean_quartile(cell(row, "quartile")),
            "h_index": clean_number(cell(row, "h_index")),
            "cites_per_doc_2y": clean_number(cell(row, "cites")),
            "categories": (cell(row, "categories") or "").strip() or None,
            # Kept on the record so journals.py can fill in an ISSN the
            # university website never gave us.
            "issns": issns,
        }
        key = jm.normalise(title)
        if key:
            index.setdefault(key, record)
        for issn in issns:
            index.setdefault(issn, record)

    if not index:
        raise SystemExit(f"{path} produced no usable rows.")
    return index, skipped, non_journal


def enrich(publications_path, scimago_path, use_fuzzy=False,
           journal_column=None, include_non_journals=False):
    index, skipped, non_journal = load_scimago(
        scimago_path, journals_only=not include_non_journals)
    aliases = jm.build_aliases(index)
    print(f"Scimago: {len(index)} lookup keys, {len(aliases)} unambiguous "
          f"subtitle aliases ({skipped} rows without a title skipped, "
          f"{non_journal} non-journal sources excluded)")

    rows, fieldnames = jm.read_publications(publications_path)
    column, issn_column = jm.find_columns(fieldnames, journal_column)
    print(f"Publications: {len(rows)} rows, matching on '{column}'"
          + (f" and '{issn_column}'" if issn_column else ""))

    counts, unmatched = Counter(), Counter()
    for row in rows:
        journal = row.get(column)
        record, how = jm.match_journal(
            journal, row.get(issn_column) if issn_column else None,
            index, aliases)
        if record is None and use_fuzzy:
            record, how = jm.fuzzy_match(journal, index)
        if record is None:
            row.update({c: None for c in ADDED_COLUMNS})
            if journal and journal.strip():
                counts["unmatched"] += 1
                unmatched[journal.strip()] += 1
            else:
                counts["no journal name"] += 1
            continue
        counts[how] += 1
        row.update({
            "sjr": record["sjr"],
            "sjr_quartile": record["sjr_quartile"],
            "h_index": record["h_index"],
            "cites_per_doc_2y": record["cites_per_doc_2y"],
            "scimago_categories": record["categories"],
            "scimago_matched_title": record["title"],
            "scimago_match_type": how,
        })

    out_path, unmatched_path = jm.write_enriched(
        publications_path, fieldnames, ADDED_COLUMNS, rows, unmatched,
        "with_scimago")
    jm.report(counts, unmatched, len(rows), out_path, unmatched_path,
              skip_keys={"unmatched", "no journal name"})

    quartiles = Counter(r["sjr_quartile"] for r in rows if r.get("sjr_quartile"))
    if quartiles:
        print("  quartiles:", dict(sorted(quartiles.items())))
    return out_path, unmatched_path


def main():
    parser = argparse.ArgumentParser(
        description="Add Scimago SJR data to a publications CSV.")
    parser.add_argument("--publications", required=True,
                        help="a *_publications.csv from any of our scrapers")
    parser.add_argument("--scimago", required=True,
                        help="the Scimago journal rank export (semicolon CSV)")
    parser.add_argument("--journal-column",
                        help="override the journal-name column name")
    parser.add_argument("--include-non-journals", action="store_true",
                        help="also match book series and conference sources")
    parser.add_argument("--fuzzy", action="store_true",
                        help="also attempt conservative fuzzy title matching "
                             "(results are tagged scimago_match_type=fuzzy)")
    args = parser.parse_args()
    enrich(args.publications, args.scimago, args.fuzzy, args.journal_column,
           args.include_non_journals)


if __name__ == "__main__":
    main()
