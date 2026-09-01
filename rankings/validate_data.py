"""Check the harvested data, not the code that harvested it.

The unit tests prove the parser does what I meant. They cannot tell me the
university changed its markup, that a rebuild quietly blanked two columns, or
that a journal ended up with two different ABDC grades. Those are properties
of the *output*, so they have to be checked against the output.

    python validate_data.py output/unsw_publications.csv
    python validate_data.py output/unsw_publications.csv \
        --staff output/unsw_staff.csv --journals output/journals.csv

Exit code 0 means every check passed, 1 means at least one FAIL. That makes it
usable as a gate: run it before pushing, and the answer is not an opinion.

Three severities:
  FAIL   the data is wrong. A merge should not run on this.
  WARN   probably fine, worth a human look.
  INFO   coverage, reported so a drop between runs is visible.
"""

import argparse
import csv
import difflib
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"

REQUIRED = [
    "researcher_name", "university", "field_of_research", "title",
    "journal_name", "year", "publication_type", "doi", "issn", "quality_rank",
]

# A DOI is a registrant prefix starting 10. and a suffix. Anything else here is
# a URL someone forgot to strip, or a fragment of one.
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")

VALID_RANKS = {"A*", "A", "B", "C", "none"}
VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
VALID_STATUS = {"published", "forthcoming", "working_paper", ""}

# HTML that survived into a field. A title or journal name is text; if it still
# contains a tag or an unresolved entity, the page was read as a string
# somewhere it should have been read as markup.
MARKUP_RE = re.compile(r"</?[a-z][a-z0-9_-]*(\s[^>]*)?/?>|&[a-z]{2,8};"
                       r"|&#\d+;|html_ent", re.I)

# ISSNs that identify a repository or preprint server rather than the journal
# the paper actually appeared in. OpenAlex returns one of these when the DOI it
# resolved points at a working-paper copy, and the result is a row that names
# the right journal and carries the wrong journal's identifier.
AGGREGATOR_ISSNS = {
    "1556-5068": "SSRN Electronic Journal",
    "2331-8422": "arXiv",
}

# Coverage floors. These are not aspirations, they are roughly where the data
# already sits. The point is to notice a DROP, which is what a silently broken
# scraper looks like: it still runs, it still writes a file, the file is
# thinner. This is the check that would have caught the pipeline step being
# skipped, which blanked quality_rank on every row while reporting success.
FLOORS = {
    "journal_name": 0.95,
    "year": 0.95,
    "doi": 0.45,
    "issn": 0.75,
    "quality_rank": 0.70,
    "sjr": 0.60,
    "impact_factor": 0.60,
}


# Mirrors rankings/journal_match.normalise. Kept local so this file has no
# dependencies and can be run against any university's output, but the two must
# agree: if the validator and the scraper disagreed about what counts as the
# same title, one of them would be wrong and neither would say so.
NON_ALNUM = re.compile(r"[^a-z0-9]+")
LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+")


def normalise_title(text):
    text = NON_ALNUM.sub(" ", (text or "").lower()).strip()
    return LEADING_ARTICLE.sub("", re.sub(r"\s+", " ", text))


# Two entries under one DOI whose titles are at least this alike are the same
# article listed twice. Below it, the publisher has issued one DOI for several
# items, which some journals do for book reviews. Measured on the real data:
# genuine repeats score 0.99, two different reviews sharing a DOI score 0.46.
SAME_PAPER = 0.90


def issn_checksum_ok(issn):
    """An ISSN's last character is a check digit over the first seven."""
    digits = issn.replace("-", "")
    if len(digits) != 8:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1)))
    remainder = (11 - total % 11) % 11
    expected = "X" if remainder == 10 else str(remainder)
    return digits[7].upper() == expected


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, check, message, examples=()):
        self.items.append((level, check, message, list(examples)[:5]))

    def failed(self):
        return any(i[0] == FAIL for i in self.items)

    def show(self):
        colour = {FAIL: "\033[31m", WARN: "\033[33m", INFO: "\033[36m"}
        reset = "\033[0m"
        use_colour = sys.stdout.isatty()
        for level, check, message, examples in self.items:
            tag = f"{colour[level]}{level}{reset}" if use_colour else level
            print(f"  {tag:<14} {check:<26} {message}")
            for e in examples:
                print(f"                 {'':<26} e.g. {e}")
        n_fail = sum(1 for i in self.items if i[0] == FAIL)
        n_warn = sum(1 for i in self.items if i[0] == WARN)
        print()
        if n_fail:
            print(f"  {n_fail} failed, {n_warn} warnings. Do not merge this.")
        elif n_warn:
            print(f"  no failures, {n_warn} warnings. Worth a look.")
        else:
            print("  every check passed.")


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def value(row, column):
    v = (row.get(column) or "").strip()
    # "none" is a real ABDC rating meaning "listed but not graded", so it only
    # counts as empty for other columns.
    return "" if v.lower() in ("none", "null", "nan") and column != "quality_rank" else v


# ---------------------------------------------------------------- structure

def check_structure(rows, report):
    if not rows:
        report.add(FAIL, "not empty", "the file has a header and no data rows")
        return False
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        report.add(FAIL, "required columns", f"absent: {', '.join(missing)}")
    blank = sum(1 for r in rows if not any((v or "").strip() for v in r.values()))
    if blank:
        report.add(FAIL, "no blank rows", f"{blank} rows are entirely empty")
    report.add(INFO, "rows", f"{len(rows)} publications, {len(rows[0])} columns")
    return not missing


# ------------------------------------------------------------ field values

def check_values(rows, report):
    this_year = date.today().year
    bad_year, bad_doi, bad_issn, bad_rank, bad_quartile = [], [], [], [], []
    bad_pct, bad_count, no_title, bad_issn_sum = [], [], [], []
    markup, aggregator, bad_status = [], [], []

    for i, r in enumerate(rows, start=2):  # 2 = first data row in a spreadsheet
        year = value(r, "year")
        if year and (not year.isdigit() or not (1900 <= int(year) <= this_year + 1)):
            bad_year.append(f"row {i}: {year!r}")

        doi = value(r, "doi")
        if doi and not DOI_RE.match(doi):
            bad_doi.append(f"row {i}: {doi!r}")

        issn = value(r, "issn")
        if issn:
            if not ISSN_RE.match(issn.upper()):
                bad_issn.append(f"row {i}: {issn!r}")
            elif not issn_checksum_ok(issn):
                bad_issn_sum.append(f"row {i}: {issn!r} ({value(r, 'journal_name')})")

        rank = value(r, "quality_rank")
        if rank and rank not in VALID_RANKS:
            bad_rank.append(f"row {i}: {rank!r}")

        q = value(r, "sjr_quartile")
        if q and q.upper() not in VALID_QUARTILES:
            bad_quartile.append(f"row {i}: {q!r}")

        status = (r.get("publication_status") or "").strip()
        if status and status not in VALID_STATUS:
            bad_status.append(f"row {i}: {status!r}")

        pct = value(r, "citation_percentile")
        if pct:
            try:
                if not 0.0 <= float(pct) <= 1.0:
                    bad_pct.append(f"row {i}: {pct}")
            except ValueError:
                bad_pct.append(f"row {i}: {pct!r} is not a number")

        cited = value(r, "cited_by_count")
        if cited and not cited.isdigit():
            bad_count.append(f"row {i}: {cited!r}")

        if not value(r, "title"):
            no_title.append(f"row {i}")

        for column in ("title", "journal_name"):
            text = value(r, column)
            if text and MARKUP_RE.search(text):
                markup.append(f"row {i} {column}: {text[:70]}")

        if issn in AGGREGATOR_ISSNS:
            journal = value(r, "journal_name")
            if journal and journal.lower() != AGGREGATOR_ISSNS[issn].lower():
                aggregator.append(f"row {i}: {journal[:45]} carries the "
                                  f"{AGGREGATOR_ISSNS[issn]} ISSN")

    for bad, check, message in [
        (no_title, "title present", "publications with no title"),
        (bad_year, "year plausible", "years outside 1900 to next year"),
        (bad_doi, "doi shape", "values that are not a bare DOI"),
        (bad_issn, "issn shape", "values that are not NNNN-NNNC"),
        (bad_rank, "quality_rank values", "ratings outside A*/A/B/C/none"),
        (bad_quartile, "sjr_quartile values", "quartiles outside Q1 to Q4"),
        (bad_status, "publication_status", "statuses outside the agreed set"),
        (bad_pct, "percentile range", "percentiles outside 0 to 1"),
        (bad_count, "citation count", "non-integer citation counts"),
        (markup, "text is text", "fields still containing HTML"),
    ]:
        if bad:
            report.add(FAIL, check, f"{len(bad)} {message}", bad)

    # A bad check digit is usually a transcription slip, not a broken pipeline,
    # so it is a warning: worth chasing, not worth blocking a merge.
    if bad_issn_sum:
        report.add(WARN, "issn check digit",
                   f"{len(bad_issn_sum)} ISSNs fail their checksum", bad_issn_sum)

    if aggregator:
        report.add(WARN, "issn is the journal's",
                   f"{len(aggregator)} rows carry a repository ISSN, not the "
                   "journal's", aggregator)


# ---------------------------------------------------- internal consistency

def check_consistency(rows, report):
    # The same journal cannot be A* on one row and B on another. If it is, the
    # journal name is doing two jobs, which is exactly the
    # "Journal of Banking and Finance: Law and Practice" failure.
    ranks, issns = defaultdict(set), defaultdict(set)
    for r in rows:
        j = value(r, "journal_name")
        if not j:
            continue
        if value(r, "quality_rank"):
            ranks[j].add(value(r, "quality_rank"))
        if value(r, "issn"):
            issns[j].add(value(r, "issn"))

    split_rank = [f"{j}: {sorted(v)}" for j, v in ranks.items() if len(v) > 1]
    if split_rank:
        report.add(FAIL, "one rank per journal",
                   f"{len(split_rank)} journals carry more than one rating",
                   split_rank)

    split_issn = [f"{j}: {sorted(v)}" for j, v in issns.items() if len(v) > 1]
    if split_issn:
        report.add(WARN, "one issn per journal",
                   f"{len(split_issn)} journals carry more than one ISSN",
                   split_issn)

    # A DOI identifies one paper. Two researchers co-authoring it is normal and
    # expected, so this is only a problem if the titles disagree.
    titles = defaultdict(set)
    for r in rows:
        if value(r, "doi"):
            titles[value(r, "doi")].add(value(r, "title").lower())
    clash = [f"{d}: {len(t)} different titles" for d, t in titles.items() if len(t) > 1]
    if clash:
        report.add(WARN, "doi means one paper",
                   f"{len(clash)} DOIs appear with conflicting titles", clash)

    # One researcher cannot hold the same DOI twice. This is the strongest
    # duplicate signal there is, and it does not care how the journal name was
    # spelled, so it catches the same paper listed once as "JOURNAL OF
    # INTERNATIONAL MONEY AND FINANCE" and once with a subtitle attached.
    by_doi = defaultdict(list)
    for r in rows:
        if value(r, "doi"):
            by_doi[(value(r, "researcher_name"), value(r, "doi"))].append(
                normalise_title(value(r, "title")))

    repeated, shared = [], []
    for (name, doi), titles in by_doi.items():
        if len(titles) < 2:
            continue
        alike = any(
            difflib.SequenceMatcher(None, a, b).ratio() >= SAME_PAPER
            for i, a in enumerate(titles) for b in titles[i + 1:])
        (repeated if alike else shared).append(f"{name} | {doi}")

    if repeated:
        report.add(FAIL, "no repeated doi",
                   f"{len(repeated)} researchers hold the same DOI twice under "
                   "the same title", repeated)

    # Not a fault in our data. Some journals, Economic Record among them, issue
    # one DOI to a batch of book reviews, so the same DOI legitimately covers
    # several distinct outputs.
    if shared:
        report.add(INFO, "one doi, several items",
                   f"{len(shared)} DOIs cover more than one distinct title",
                   shared)

    # Same person, same title, same year, same journal. Note the journal:
    # without it this fires on legitimate reprints, where one article runs in
    # both the Goods and Services Tax Journal and the Weekly Tax Bulletin.
    # Those are two real outputs, not one row written twice.
    seen = Counter((value(r, "researcher_name"), value(r, "title").lower(),
                    value(r, "year"), value(r, "journal_name").lower())
                   for r in rows)
    dupes = [f"{n} | {t[:50]} | {y} | {j[:30]}"
             for (n, t, y, j), c in seen.items() if c > 1]
    if dupes:
        report.add(FAIL, "no duplicate rows",
                   f"{len(dupes)} identical researcher/title/year/journal rows",
                   dupes)

    # Reported, not failed: the same title under one researcher in two
    # different outlets is a reprint, which the client counts as separate.
    reprints = Counter((value(r, "researcher_name"), value(r, "title").lower(),
                        value(r, "year")) for r in rows)
    n_reprints = sum(1 for c in reprints.values() if c > 1) - len(dupes)
    if n_reprints > 0:
        report.add(INFO, "reprints",
                   f"{n_reprints} titles appear under one researcher in more "
                   "than one outlet")

    orphan = sum(1 for r in rows
                 if value(r, "quality_rank") and not value(r, "journal_name"))
    if orphan:
        report.add(FAIL, "rating needs a journal",
                   f"{orphan} rows carry a rating but name no journal")

    unis = {value(r, "university") for r in rows} - {""}
    if len(unis) > 1:
        report.add(FAIL, "single university", f"this file mixes {sorted(unis)}")
    elif unis:
        report.add(INFO, "university", sorted(unis)[0])

    types = Counter(value(r, "publication_type") for r in rows)
    if len(types) > 1:
        report.add(WARN, "publication types",
                   f"{len(types)} types present: {dict(types.most_common(5))}")


# ------------------------------------------------------------- cross-file

def check_against_staff(rows, staff_path, report):
    staff = load(staff_path)
    names = {(r.get("name") or "").strip() for r in staff}
    report.add(INFO, "staff",
               f"{len(staff)} researchers in {os.path.basename(staff_path)}")

    unknown = sorted({value(r, "researcher_name") for r in rows} - names)
    if unknown:
        report.add(FAIL, "researcher exists",
                   f"{len(unknown)} names in publications are not in the staff "
                   "file", unknown)

    silent = sorted(names - {value(r, "researcher_name") for r in rows})
    if silent:
        report.add(INFO, "researchers with no output",
                   f"{len(silent)} of {len(names)} produced nothing", silent)


def check_against_journals(rows, journals_path, report):
    journals = load(journals_path)
    known = {(r.get("journal_name") or "").strip() for r in journals}
    report.add(INFO, "journals",
               f"{len(journals)} rows in {os.path.basename(journals_path)}")

    used = {value(r, "journal_name") for r in rows} - {""}
    missing = sorted(used - known)
    if missing:
        report.add(FAIL, "journal exists",
                   f"{len(missing)} journals used by publications are absent "
                   "from journals.csv", missing)

    # publication_count is a claim journals.csv makes about the publications
    # file. If it no longer adds up, one of the two is stale.
    claimed = 0
    for r in journals:
        try:
            claimed += int((r.get("publication_count") or "0").strip() or 0)
        except ValueError:
            pass
    actual = sum(1 for r in rows if value(r, "journal_name"))
    if claimed and claimed != actual:
        report.add(FAIL, "counts agree",
                   f"journals.csv claims {claimed} publications, the file "
                   f"holds {actual}")


# -------------------------------------------------------------- coverage

def check_coverage(rows, report):
    n = len(rows)
    for column, floor in FLOORS.items():
        if column not in rows[0]:
            report.add(WARN, f"coverage: {column}", "column is not in the file")
            continue
        filled = sum(1 for r in rows if value(r, column))
        rate = filled / n
        level = INFO if rate >= floor else WARN
        report.add(level, f"coverage: {column}",
                   f"{filled} of {n} ({rate:.0%}), floor {floor:.0%}")

    empty = [c for c in rows[0] if not any(value(r, c) for r in rows)]
    if empty:
        report.add(WARN, "columns never filled",
                   f"{len(empty)} columns are empty on every row: "
                   f"{', '.join(empty)}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Validate a harvested publications CSV.")
    p.add_argument("publications")
    p.add_argument("--staff", help="unsw_staff.csv, to check every researcher exists")
    p.add_argument("--journals", help="journals.csv, to check every journal exists")
    args = p.parse_args(argv)

    rows = load(args.publications)
    report = Report()
    print(f"\nvalidating {args.publications}\n")

    if check_structure(rows, report):
        check_values(rows, report)
        check_consistency(rows, report)
        if args.staff:
            check_against_staff(rows, args.staff, report)
        if args.journals:
            check_against_journals(rows, args.journals, report)
        check_coverage(rows, report)

    report.show()
    print()
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
