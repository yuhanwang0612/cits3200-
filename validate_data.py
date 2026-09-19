"""Check the harvested data, not the code that harvested it.

    python validate_data.py unsw
    python validate_data.py unsw --dir "final output"
    python validate_data.py all

The unit tests prove each parser does what its author meant. They cannot tell
you that a university changed its markup, that a rebuild quietly blanked a
column, that one journal ended up with two different ABDC grades, or that two
researchers are sharing an ORCID. Those are properties of the OUTPUT, so they
have to be checked against the output.

Exit code 0 means every check passed, 1 means at least one FAIL. That makes it
usable as a gate: run it before pushing and the answer is not an opinion.

Three severities:
  FAIL   the data is wrong. A merge should not run on this.
  WARN   probably fine, worth a human look.
  INFO   coverage and shape, reported so a DROP between runs is visible.

WHAT THIS IS NOT
----------------
It cannot tell you a publication is genuinely someone else's. Every row here
looked perfectly normal while one of UNSW's professors was matched to a
stranger's ORCID. Checks that compare our data against itself cannot catch
that; only comparing against the outside world can, which is what the
completeness check in base_scrapers/unsw.py and a manual spot-check are for.

WHERE THE FIXES GO
------------------
Not in here, and usually not in code. A wrong value with a DOI is corrected by
adding a line to data/overrides.csv:

    doi,field,value
    10.1086/503649,title,"Asymmetry, Loss Aversion, and Forecasting"

core/clean.py applies those on every run, so the correction survives a
re-scrape and is reviewable in a diff. Rows with no DOI cannot be corrected
that way, which is a real gap for any university scraped off a staff directory.
"""

import argparse
import csv
import difflib
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"

# A DOI is a registrant prefix starting 10. and a suffix. Anything else is a
# URL someone forgot to strip, or a fragment of one.
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")
ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")

VALID_RANKS = {"A*", "A", "B", "C", "none"}
VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
VALID_STATUS = {"published", "forthcoming", "working_paper", ""}

# HTML that survived into a field. A title or journal name is text; if it still
# contains a tag or an unresolved entity, a page was read as a string somewhere
# it should have been read as markup.
MARKUP_RE = re.compile(r"</?[a-z][a-z0-9_-]*(\s[^>]*)?/?>|&[a-z]{2,8};"
                       r"|&#\d+;|html_ent", re.I)

# ISSNs that identify a repository rather than the journal the paper appeared
# in. OpenAlex returns one of these when the DOI it resolved points at a
# working-paper copy, and the result is a row naming the right journal while
# carrying the wrong journal's identifier.
AGGREGATOR_ISSNS = {
    "1556-5068": "SSRN Electronic Journal",
    "2331-8422": "arXiv",
}

# A journals table should hold journals. These words in a venue name mean the
# row is a conference, and ABDC cannot rate it however good the paper was.
NOT_A_JOURNAL_RE = re.compile(
    r"\b(conference|symposium|proceedings|workshop|congress|colloquium"
    r"|annual meeting|expert seminar)\b", re.I)

# A venue name this long is almost always a paper title that landed in the
# journal field. Measured: the longest real journal name in the current data is
# about 95 characters, and everything past 120 was a title.
VENUE_TOO_LONG = 120

# Coverage floors. Not aspirations, roughly where the data already sits. The
# point is to notice a DROP, which is what a silently broken scraper looks
# like: it still runs, it still writes a file, the file is thinner.
FLOORS = {
    "title": 1.00,
    "year": 0.95,
    "journal_name": 0.95,
    "doi": 0.45,
    "article_url": 0.60,
    "quality_rank": 0.40,
    "sjr_quartile": 0.40,
    "cited_by_count": 0.40,
}

# Values that mean "we have nothing" but were written as text. Scimago writes
# "-" for a journal with no quartile and export.py writes "unknown" when a row
# names no journal, and both travel all the way to the website, where they
# render as data rather than as a blank.
PLACEHOLDERS = {"-", "--", "n/a", "na", "none", "null", "unknown", "nan"}

# Universities do not agree on how to separate a list inside one CSV cell.
# UNSW and UQ use "; ", ANU uses ", " and " and ", and Adelaide, Monash and UWA
# separate ISSNs with a space. Anything reading these files has to cope with
# all of them, so the separator in use is reported rather than assumed.
LIST_SPLIT = re.compile(r"\s*;\s*|\s+and\s+|\s*,\s*|\s+")

NON_ALNUM = re.compile(r"[^a-z0-9]+")
LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+")


def column_note(row, column, got):
    """Name the journal alongside a bad value, so it can be looked up."""
    where = (row.get("journal_name") or "").strip()[:40]
    return f"{got!r}" + (f"  ({where})" if where else "")


def is_placeholder(text):
    return text.strip().lower() in PLACEHOLDERS


def split_list(text):
    """For ISSNs and other lists of tokens with no internal spaces."""
    return [p for p in LIST_SPLIT.split(text.strip()) if p]


def split_authors(text):
    """Author names contain spaces, so they cannot be split on whitespace.

    Detect the separator per row instead of assuming one: UNSW and UQ join with
    "; ", ANU with ", " and " and ". Splitting "Cheng M; Wu S" on whitespace
    gives four authors, which is how an earlier version of this check reported
    1,528 disagreements that were not there.
    """
    text = text.strip()
    if not text:
        return []
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    return None        # ambiguous, see below

# Two entries under one DOI whose titles are at least this alike are the same
# article listed twice. Below it, the publisher has issued one DOI for several
# items, which some journals do for book reviews. Measured on the real data:
# genuine repeats score 0.99, two different reviews sharing a DOI score 0.46.
SAME_PAPER = 0.90


def normalise_title(text):
    text = NON_ALNUM.sub(" ", (text or "").lower()).strip()
    return LEADING_ARTICLE.sub("", re.sub(r"\s+", " ", text))


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
            print(f"  {tag:<14} {check:<28} {message}")
            for e in examples:
                print(f"                 {'':<28} e.g. {e}")
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
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def value(row, column):
    return (row.get(column) or "").strip()


# --------------------------------------------------------------- structure

REQUIRED_PUBS = ["name", "title", "year", "journal_name", "doi",
                 "quality_rank", "article_url"]
REQUIRED_STAFF = ["name", "university", "field_of_research", "academic_level"]
REQUIRED_JOURNALS = ["journal_name", "issn", "quality_rank"]


def check_structure(pubs, staff, journals, report):
    ok = True
    for rows, required, what in ((pubs, REQUIRED_PUBS, "publications"),
                                 (staff, REQUIRED_STAFF, "staff"),
                                 (journals, REQUIRED_JOURNALS, "journals")):
        if rows is None:
            report.add(FAIL, "file exists", f"{what}.csv is missing")
            ok = False
            continue
        if not rows:
            report.add(FAIL, "not empty", f"{what}.csv has a header and no rows")
            ok = False
            continue
        missing = [c for c in required if c not in rows[0]]
        if missing:
            report.add(FAIL, f"{what} columns", f"absent: {', '.join(missing)}")
            ok = False
        report.add(INFO, what, f"{len(rows)} rows, {len(rows[0])} columns")
    return ok


# ------------------------------------------------------------ field values

def check_values(pubs, journals, report):
    this_year = date.today().year
    bad_year, bad_doi, bad_rank, bad_quartile = [], [], [], []
    bad_pct, bad_count, no_title, bad_status, markup = [], [], [], [], []
    bad_authors, placeholder = [], []
    ambiguous = 0

    for i, r in enumerate(pubs, start=2):   # 2 = first data row in a spreadsheet
        year = value(r, "year")
        if year and (not year.isdigit() or not (1900 <= int(year) <= this_year + 1)):
            bad_year.append(f"row {i}: {year!r}")

        doi = value(r, "doi")
        if doi and not DOI_RE.match(doi):
            bad_doi.append(f"row {i}: {doi!r}")

        rank = value(r, "quality_rank")
        if rank and rank not in VALID_RANKS:
            bad_rank.append(f"row {i}: {rank!r}")

        q = value(r, "sjr_quartile")
        if q and q.upper() not in VALID_QUARTILES:
            (placeholder if is_placeholder(q) else bad_quartile).append(
                f"row {i}: {column_note(r, 'sjr_quartile', q)}")

        status = value(r, "publication_status")
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
        if cited and not re.fullmatch(r"\d+(\.0)?", cited):
            bad_count.append(f"row {i}: {cited!r}")

        if not value(r, "title"):
            no_title.append(f"row {i}")

        for column in ("title", "journal_name", "authors"):
            text = value(r, column)
            if text and MARKUP_RE.search(text):
                markup.append(f"row {i} {column}: {text[:70]}")

        # author_count is a claim about the authors column. If they disagree,
        # one of them was written by a different code path than the other.
        n, authors = value(r, "author_count"), value(r, "authors")
        if n and authors:
            try:
                claimed = int(float(n))
            except ValueError:
                claimed = None
            names = split_authors(authors)
            if names is None:
                # "Cahan, C., Chen, C., Chen, L" uses a comma both between
                # authors and inside a name, so the string alone cannot say how
                # many people it holds. Counting anyway produced 1,528 false
                # disagreements on UNSW. Report the format instead of guessing.
                ambiguous += 1
            elif claimed is not None and claimed != len(names):
                bad_authors.append(f"row {i}: author_count {claimed}, "
                                   f"{len(names)} names listed")

    for bad, check, message in [
        (no_title, "title present", "publications with no title"),
        (bad_year, "year plausible", "years outside 1900 to next year"),
        (bad_doi, "doi shape", "values that are not a bare DOI"),
        (bad_rank, "quality_rank values", "ratings outside A*/A/B/C/none"),
        (bad_quartile, "sjr_quartile values", "quartiles outside Q1 to Q4"),
        (bad_status, "publication_status", "statuses outside the agreed set"),
        (bad_pct, "percentile range", "percentiles outside 0 to 1"),
        (bad_count, "citation count", "non-integer citation counts"),
        (markup, "text is text", "fields still containing HTML"),
        (bad_authors, "author_count agrees", "rows where the count and the "
                                             "author list disagree by more "
                                             "than one"),
    ]:
        if bad:
            report.add(FAIL, check, f"{len(bad)} {message}", bad)

    # Not wrong, but not blank either: a placeholder that reached the output
    # will be rendered on the website as though it were a value.
    if ambiguous:
        report.add(WARN, "author list separator",
                   f"{ambiguous} rows separate authors with commas rather than "
                   f"semicolons, so the names cannot be counted reliably")

    if placeholder:
        report.add(WARN, "placeholders blanked",
                   f"{len(placeholder)} rows carry a placeholder where a blank "
                   f"was meant", placeholder)


def check_journal_values(journals, report):
    bad_issn, bad_sum, aggregator, not_journals, too_long = [], [], [], [], []
    duplicated = []

    for i, r in enumerate(journals, start=2):
        name = value(r, "journal_name")
        seen_here = set()
        for issn in split_list(value(r, "issn")):
            if not ISSN_RE.match(issn.upper()):
                # keep the value AND the formatted note: the classification
                # below has to test the ISSN, not the sentence describing it
                bad_issn.append((issn, f"row {i}: {issn!r} ({name[:34]})"))
            elif not issn_checksum_ok(issn):
                bad_sum.append(f"row {i}: {issn!r} ({name[:40]})")
            flat = issn.upper().replace("-", "")
            if flat in seen_here:
                duplicated.append(f"row {i}: {name[:40]} lists {issn} twice, "
                                  f"once hyphenated and once not")
            seen_here.add(flat)

            if issn in AGGREGATOR_ISSNS:
                if name and name.lower() != AGGREGATOR_ISSNS[issn].lower():
                    aggregator.append(f"row {i}: {name[:45]} carries the "
                                      f"{AGGREGATOR_ISSNS[issn]} ISSN")

        if name and NOT_A_JOURNAL_RE.search(name):
            not_journals.append(f"row {i}: {name[:70]}")
        elif len(name) > VENUE_TOO_LONG:
            too_long.append(f"row {i}: {name[:70]}...")

    # Adelaide, Monash and UWA write "10959955 10452354" where UNSW and UQ
    # write "0022-1082; 1540-6261". Both are ISSNs; only one matches ABDC's
    # list, which is hyphenated, so this is not cosmetic.
    unhyphenated = [note for v, note in bad_issn
                    if re.fullmatch(r"\d{7}[\dX]", v.upper())]
    malformed = [note for v, note in bad_issn
                 if not re.fullmatch(r"\d{7}[\dX]", v.upper())]
    if unhyphenated:
        report.add(FAIL, "issn hyphenated",
                   f"{len(unhyphenated)} ISSNs are written without the hyphen. "
                   f"ABDC's list is hyphenated, so these will not join",
                   unhyphenated)
    if malformed:
        report.add(FAIL, "issn shape",
                   f"{len(malformed)} values are not an ISSN at all", malformed)
    # A bad check digit is usually a transcription slip, not a broken pipeline,
    # so it is a warning: worth chasing, not worth blocking a merge.
    if bad_sum:
        report.add(WARN, "issn check digit",
                   f"{len(bad_sum)} ISSNs fail their checksum", bad_sum)
    if duplicated:
        report.add(WARN, "issn listed once",
                   f"{len(duplicated)} journals list the same ISSN twice in "
                   f"one cell", duplicated)
    if aggregator:
        report.add(WARN, "issn is the journal's",
                   f"{len(aggregator)} rows carry a repository ISSN rather "
                   f"than the journal's", aggregator)
    if not_journals:
        report.add(WARN, "journals are journals",
                   f"{len(not_journals)} rows name a conference, not a journal",
                   not_journals)
    if too_long:
        report.add(WARN, "venue name length",
                   f"{len(too_long)} venue names are over {VENUE_TOO_LONG} "
                   f"characters and look like paper titles", too_long)


# ---------------------------------------------------- internal consistency

def check_consistency(pubs, report):
    # A DOI identifies one paper. Two researchers co-authoring it is normal, so
    # this only matters if the titles disagree.
    titles = defaultdict(set)
    for r in pubs:
        if value(r, "doi"):
            titles[value(r, "doi")].add(value(r, "title").lower())
    clash = [f"{d}: {len(t)} different titles" for d, t in titles.items() if len(t) > 1]
    if clash:
        report.add(WARN, "doi means one paper",
                   f"{len(clash)} DOIs appear with conflicting titles", clash)

    # One researcher cannot hold the same DOI twice. The strongest duplicate
    # signal there is, and it does not care how the journal name was spelled.
    by_doi = defaultdict(list)
    for r in pubs:
        if value(r, "doi"):
            by_doi[(value(r, "name"), value(r, "doi"))].append(
                normalise_title(value(r, "title")))

    repeated, shared = [], []
    for (name, doi), got in by_doi.items():
        if len(got) < 2:
            continue
        alike = any(difflib.SequenceMatcher(None, a, b).ratio() >= SAME_PAPER
                    for i, a in enumerate(got) for b in got[i + 1:])
        (repeated if alike else shared).append(f"{name} | {doi}")

    if repeated:
        report.add(FAIL, "no repeated doi",
                   f"{len(repeated)} researchers hold the same DOI twice under "
                   f"the same title", repeated)
    # Not a fault. Some journals issue one DOI to a batch of book reviews, so
    # the same DOI legitimately covers several distinct outputs.
    if shared:
        report.add(INFO, "one doi, several items",
                   f"{len(shared)} DOIs cover more than one distinct title",
                   shared)

    # Same person, same title, same year, same journal. The journal matters:
    # without it this fires on legitimate reprints, where one article runs in
    # two outlets. Those are two real outputs, not one row written twice.
    seen = Counter((value(r, "name"), value(r, "title").lower(),
                    value(r, "year"), value(r, "journal_name").lower())
                   for r in pubs)
    dupes = [f"{n} | {t[:46]} | {y} | {j[:26]}"
             for (n, t, y, j), c in seen.items() if c > 1]
    if dupes:
        report.add(FAIL, "no duplicate rows",
                   f"{len(dupes)} identical researcher/title/year/journal rows",
                   dupes)

    # The near-duplicate case the exact rule cannot see: same person, same
    # title, DIFFERENT year. Usually a working paper and its published version,
    # which is a judgement call rather than a fault, so it is reported.
    pairs = defaultdict(set)
    for r in pubs:
        pairs[(value(r, "name"), normalise_title(value(r, "title")))].add(
            value(r, "year"))
    near = [f"{n} | {t[:50]} | years {sorted(y)}"
            for (n, t), y in pairs.items() if len(y) > 1]
    if near:
        report.add(WARN, "same title, two years",
                   f"{len(near)} titles appear under one researcher with "
                   f"different years", near)

    orphan = sum(1 for r in pubs
                 if value(r, "quality_rank") and not value(r, "journal_name"))
    if orphan:
        report.add(FAIL, "rating needs a journal",
                   f"{orphan} rows carry a rating but name no journal")


# ------------------------------------------------------------- cross-file

def check_against_staff(pubs, staff, report):
    names = {value(r, "name") for r in staff}

    unknown = sorted({value(r, "name") for r in pubs} - names)
    if unknown:
        report.add(FAIL, "researcher exists",
                   f"{len(unknown)} names in publications are not in the staff "
                   f"file", unknown)

    silent = sorted(names - {value(r, "name") for r in pubs})
    if silent:
        report.add(INFO, "researchers with no output",
                   f"{len(silent)} of {len(names)} produced nothing", silent)

    unis = {value(r, "university") for r in staff} - {""}
    if len(unis) > 1:
        report.add(FAIL, "single university", f"this slice mixes {sorted(unis)}")
    elif unis:
        report.add(INFO, "university", sorted(unis)[0])

    # An ORCID identifies one person. Two researchers sharing one means a name
    # search matched the wrong record, which is exactly how a UNSW lecturer
    # acquired a medical physicist's publications.
    bad_shape, by_orcid = [], defaultdict(set)
    for r in staff:
        orcid = value(r, "orcid")
        if not orcid:
            continue
        if not ORCID_RE.match(orcid.upper()):
            bad_shape.append(f"{value(r, 'name')}: {orcid!r}")
        by_orcid[orcid.upper()].add(value(r, "name"))

    if bad_shape:
        report.add(FAIL, "orcid shape",
                   f"{len(bad_shape)} ORCIDs are not NNNN-NNNN-NNNN-NNNC",
                   bad_shape)
    clashes = [f"{o}: {sorted(n)}" for o, n in by_orcid.items() if len(n) > 1]
    if clashes:
        report.add(FAIL, "one orcid per person",
                   f"{len(clashes)} ORCIDs are held by more than one "
                   f"researcher", clashes)

    held = sum(1 for r in staff if value(r, "orcid"))
    report.add(INFO, "orcid coverage",
               f"{held} of {len(staff)} researchers have one")


def check_against_journals(pubs, journals, report):
    known = {value(r, "journal_name") for r in journals}
    used = {value(r, "journal_name") for r in pubs} - {""}

    # export.py writes "unknown" when a row names no journal. That is its own
    # null marker, not a journal record that has gone missing.
    unnamed = sum(1 for r in pubs if is_placeholder(value(r, "journal_name")))
    if unnamed:
        report.add(WARN, "journal named",
                   f"{unnamed} publications carry a placeholder instead of a "
                   f"journal name")
    used = {u for u in used if not is_placeholder(u)}

    missing = sorted(used - known)
    if missing:
        report.add(FAIL, "journal exists",
                   f"{len(missing)} journals used by publications are absent "
                   f"from journals.csv", missing)

    # The other direction is not a fault, but a journals table that is mostly
    # rows nothing points at is carrying dead weight into the merged file.
    unused = sorted(known - used)
    if unused:
        share = len(unused) / max(len(journals), 1)
        level = WARN if share > 0.25 else INFO
        report.add(level, "journals are used",
                   f"{len(unused)} of {len(journals)} journal rows ({share:.0%}) "
                   f"appear in no publication", unused)

    # The same journal cannot be A* in one file and B in the other. The client
    # raised this exact risk on 15 September: editing one file and not the
    # other.
    by_name = {value(r, "journal_name"): r for r in journals}
    disagree = []
    for r in pubs:
        j = by_name.get(value(r, "journal_name"))
        if not j:
            continue
        for column in ("quality_rank", "sjr_quartile"):
            a, b = value(r, column), value(j, column)
            if a and b and a != b:
                disagree.append(f"{value(r, 'journal_name')[:40]}: "
                                f"publications say {a}, journals say {b}")
    if disagree:
        report.add(FAIL, "the two files agree",
                   f"{len(set(disagree))} journals are rated differently in "
                   f"publications.csv and journals.csv", sorted(set(disagree)))


# -------------------------------------------------------------- coverage

def check_coverage(pubs, report):
    n = len(pubs)
    for column, floor in FLOORS.items():
        if column not in pubs[0]:
            report.add(WARN, f"coverage: {column}", "column is not in the file")
            continue
        filled = sum(1 for r in pubs if value(r, column))
        rate = filled / n
        level = INFO if rate >= floor else WARN
        report.add(level, f"coverage: {column}",
                   f"{filled} of {n} ({rate:.0%}), floor {floor:.0%}")

    empty = [c for c in pubs[0] if not any(value(r, c) for r in pubs)]
    if empty:
        report.add(WARN, "columns never filled",
                   f"{len(empty)} columns are empty on every row: "
                   f"{', '.join(empty)}")


# ------------------------------------------------------------------ driver

def validate(uni, root, report):
    folder = root / uni
    pubs = load(folder / f"{uni}_publications.csv")
    staff = load(folder / f"{uni}_staff.csv")
    journals = load(folder / f"{uni}_journals.csv")

    if not check_structure(pubs, staff, journals, report):
        return
    check_values(pubs, journals, report)
    check_journal_values(journals, report)
    check_consistency(pubs, report)
    check_against_staff(pubs, staff, report)
    check_against_journals(pubs, journals, report)
    check_coverage(pubs, report)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Validate one university's exported tables.")
    p.add_argument("uni", help="folder name under the output directory, "
                              "or 'all'")
    p.add_argument("--dir", default="final output",
                   help="output directory (default: 'final output')")
    args = p.parse_args(argv)

    root = Path(args.dir)
    if not root.exists():
        print(f"no such directory: {root}")
        return 1

    unis = ([d.name for d in sorted(root.iterdir()) if d.is_dir()]
            if args.uni == "all" else [args.uni])

    worst = 0
    for uni in unis:
        print(f"\n{uni}")
        print("  " + "-" * 76)
        report = Report()
        validate(uni, root, report)
        report.show()
        worst = max(worst, 1 if report.failed() else 0)
    return worst


if __name__ == "__main__":
    sys.exit(main())
