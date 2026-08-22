"""
Tests for the shared ABDC rating join — CITS3200 Group 20.

Offline: the ABDC workbook these run against is generated in a temp directory,
so the suite needs no downloaded file and touches no network.

The matching rules are where this module can do real damage — a wrong A* on a
B journal would quietly distort every ranking built on top of it. Most of these
tests exist to pin the cases where a looser matcher would get it wrong.

Run:  python -m pytest test_abdc.py -v
"""

import os
import tempfile

import pytest
from openpyxl import Workbook

import abdc


# ---------------------------------------------------------------------------
# A workbook shaped like the real one: banner rows above the header, several
# editions as separate sheets, the rating column named for its year.
# ---------------------------------------------------------------------------
def build_workbook(path):
    wb = Workbook()
    current = wb.active
    current.title = "2025 JQL"
    current.append(["ABDC Journal Quality List 2025", None, None, None, None])
    current.append([None] * 5)
    current.append(["Journal Title", "ISSN", "ISSN Online", "FoR", "2025 Rating"])
    current.append(["Journal of Finance", "0022-1082", "1540-6261", "3502", "A*"])
    current.append(["Accounting and Finance", "0810-5391", "1467-629X", "3501", "A"])
    current.append(["Australian Tax Review", "0311-094X", None, "3501", "B"])
    current.append(["Journal of Accounting Research", "0021-8456", None, "3501", "A*"])
    current.append(["Journal of Accounting Education", "0748-5751", None, "3901", "B"])
    current.append(["Not Yet Rated Journal", "1111-1111", None, "3501", "Not rated"])
    # ABDC carries a subtitle where UNSW writes the short form.
    current.append(["Auditing: A Journal of Practice and Theory",
                    "0278-0380", None, "3501", "A*"])
    # UNSW appends a redundant "Journal" to this one.
    current.append(["Australian Tax Forum", "0812-695X", None, "3501", "A*"])
    # A prefix that collides with a real full title — the alias must be dropped
    # rather than letting "Journal of Finance" resolve to this C-rated journal.
    current.append(["Journal of Finance: Case Studies", "2222-2222", None, "3502", "C"])

    older = wb.create_sheet("2019 JQL")
    older.append(["Journal Title", "ISSN", "ISSN Online", "FoR", "2019 Rating"])
    older.append(["Journal of Finance", "0022-1082", None, "1502", "A*"])
    older.append(["Australian Tax Review", "0311-094X", None, "1501", "A"])
    wb.save(path)
    return path


@pytest.fixture(scope="module")
def workbook():
    directory = tempfile.mkdtemp()
    return build_workbook(os.path.join(directory, "abdc.xlsx"))


@pytest.fixture(scope="module")
def index(workbook):
    return abdc.load_abdc(workbook)[0]


# ---------------------------------------------------------------------------
# Normalisation — formatting differences, not different journals
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("The Journal of Finance", "journal of finance"),   # leading article
    ("AUSTRALIAN TAX REVIEW", "australian tax review"),  # UNSW writes these in caps
    ("Accounting & Finance", "accounting and finance"),  # ampersand
    ("Journal  of   Corporate Finance", "journal of corporate finance"),
    ("Zeitschrift für Betriebswirtschaft", "zeitschrift fur betriebswirtschaft"),
    ("Journal of Finance.", "journal of finance"),       # trailing punctuation
    (None, ""),
    ("", ""),
])
def test_normalise(raw, expected):
    assert abdc.normalise(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("0022-1082", "0022-1082"),
    ("00221082", "0022-1082"),
    ("ISSN 1467-629X", "1467-629X"),
    ("1467-629x", "1467-629X"),
    ("n/a", None),
    (None, None),
])
def test_normalise_issn(raw, expected):
    assert abdc.normalise_issn(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("A*", "A*"), (" a ", "A"), ("b", "B"), ("C", "C"),
    ("Not rated", None), ("", None), (None, None),
])
def test_clean_rating(raw, expected):
    assert abdc.clean_rating(raw) == expected


# ---------------------------------------------------------------------------
# Reading the workbook
# ---------------------------------------------------------------------------
def test_banner_rows_above_the_header_are_skipped(workbook):
    index, sheet, _ = abdc.load_abdc(workbook)
    assert sheet == "2025 JQL"
    assert index[abdc.normalise("Journal of Finance")]["rating"] == "A*"


def test_latest_edition_is_used_by_default(workbook):
    """Australian Tax Review is A in 2019 and B in 2025 — the default must be
    the newer one, or every rating silently comes from a stale list."""
    index, sheet, _ = abdc.load_abdc(workbook)
    assert sheet == "2025 JQL"
    assert index[abdc.normalise("Australian Tax Review")]["rating"] == "B"


def test_an_older_edition_can_be_selected(workbook):
    index, sheet, _ = abdc.load_abdc(workbook, year=2019)
    assert sheet == "2019 JQL"
    assert index[abdc.normalise("Australian Tax Review")]["rating"] == "A"


def test_rating_column_is_found_whatever_year_names_it(workbook):
    """The column is '2025 Rating' this edition and '2019 Rating' in the last.
    Matching only on the literal 'Rating' finds neither."""
    for year in (2019, None):
        index, _, _ = abdc.load_abdc(workbook, year=year)
        assert index, "no rows parsed — the rating column was not found"


def test_unrated_journals_are_not_in_the_index(workbook):
    index, _, skipped = abdc.load_abdc(workbook)
    assert abdc.normalise("Not Yet Rated Journal") not in index
    assert skipped >= 1


def test_missing_sheet_fails_loudly(workbook):
    with pytest.raises(SystemExit):
        abdc.load_abdc(workbook, year=2099)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def test_issn_is_preferred_over_the_title(index):
    """ISSN is unambiguous; a title is not. If both are present the ISSN wins,
    and the match is labelled so a reviewer can see which was used."""
    record, how = abdc.match_journal("Something Typed Wrong", "0022-1082", index)
    assert how == "issn"
    assert record["title"] == "Journal of Finance"


def test_title_matches_after_normalisation(index):
    record, how = abdc.match_journal("The Journal of Finance", None, index)
    assert (record["rating"], how) == ("A*", "title")


def test_caps_and_ampersands_still_match(index):
    assert abdc.match_journal("AUSTRALIAN TAX REVIEW", None, index)[0]["rating"] == "B"
    assert abdc.match_journal("Accounting & Finance", None, index)[0]["rating"] == "A"


def test_trailing_qualifier_is_a_separate_match_type(index):
    """'Accounting and Finance (Australia)' is the same journal, but the match
    is weaker than an exact one, so it is tagged differently rather than being
    passed off as exact."""
    record, how = abdc.match_journal("Accounting and Finance (Australia)", None, index)
    assert record["rating"] == "A"
    assert how == "title-variant"


def test_unknown_journal_returns_nothing_rather_than_a_guess(index):
    assert abdc.match_journal("Journal of Nothing In Particular", None, index) == (None, None)


def test_blank_journal_returns_nothing(index):
    assert abdc.match_journal(None, None, index) == (None, None)
    assert abdc.match_journal("", None, index) == (None, None)


def test_near_miss_is_not_matched_without_fuzzy(index):
    """'Journal of Accounting Research' (A*) and 'Journal of Accounting
    Education' (B) differ by one word. A truncated title must not silently
    become either of them — this is the case that makes fuzzy matching
    dangerous, and it is why exact matching is the default."""
    assert abdc.match_journal("Journal of Accounting Educatio", None, index) == (None, None)


def test_trailing_journal_word_is_trimmed(index):
    """Real record: UNSW writes 'Australian Tax Forum Journal'; ABDC lists
    'Australian Tax Forum'. That is an A* journal, so losing it matters."""
    record, how = abdc.match_journal("Australian Tax Forum Journal", None, index)
    assert (record["rating"], how) == ("A*", "title-variant")


def test_dash_subtitle_on_our_side_is_trimmed(index):
    """Real record: 'ABACUS - A Journal of Accounting, Finance and Business
    Studies'. Both hyphen and en-dash forms appear in the data."""
    for name in ("Accounting and Finance - The Journal",
                 "Accounting and Finance – Something Else"):
        record, how = abdc.match_journal(name, None, index)
        assert (record["rating"], how) == ("A", "title-variant"), name


# --- aliases: the subtitle problem in the other direction --------------------
@pytest.fixture(scope="module")
def aliases(index):
    return abdc.build_aliases(index)


def test_alias_matches_when_abdc_has_the_subtitle(index, aliases):
    """Real record: UNSW writes 'Auditing'; ABDC lists 'Auditing: A Journal of
    Practice and Theory', which is A*."""
    record, how = abdc.match_journal("Auditing", None, index, aliases)
    assert record["title"].startswith("Auditing:")
    assert (record["rating"], how) == ("A*", "abdc-prefix")


def test_alias_is_dropped_when_it_collides_with_a_real_title(index, aliases):
    """'Journal of Finance: Case Studies' would produce the alias 'journal of
    finance', which is a different, A* journal. Letting that stand would
    silently downgrade every Journal of Finance paper to C."""
    assert abdc.normalise("Journal of Finance") not in aliases
    record, how = abdc.match_journal("Journal of Finance", None, index, aliases)
    assert (record["rating"], how) == ("A*", "title")


def test_aliases_are_not_used_unless_passed(index):
    """Callers that do not opt in get exact matching only."""
    assert abdc.match_journal("Auditing", None, index) == (None, None)


def test_alias_ignores_very_short_prefixes(index, aliases):
    assert all(len(k) >= 8 for k in aliases)


def test_fuzzy_is_opt_in_and_labelled(index):
    record, how = abdc.fuzzy_match("Journal of Accounting Educatio", index)
    assert how == "fuzzy"
    assert record["title"] == "Journal of Accounting Education"


def test_fuzzy_refuses_short_titles(index):
    """Short titles are too easy to confuse, so fuzzy declines them outright."""
    assert abdc.fuzzy_match("Finance", index) == (None, None)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def write_publications(directory, rows, columns):
    import csv
    path = os.path.join(directory, "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_csv(path):
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_enrich_adds_columns_and_keeps_the_originals(workbook):
    directory = tempfile.mkdtemp()
    path = write_publications(directory, [
        {"title": "p1", "journal_name": "The Journal of Finance"},
        {"title": "p2", "journal_name": "Journal of Nothing"},
        {"title": "p3", "journal_name": ""},
    ], ["title", "journal_name"])

    out, unmatched = abdc.enrich(path, workbook)
    rows = read_csv(out)

    assert [r["title"] for r in rows] == ["p1", "p2", "p3"]      # nothing dropped
    assert all(c in rows[0] for c in ["title", "journal_name"])  # originals kept
    assert all(c in rows[0] for c in abdc.ADDED_COLUMNS)
    assert rows[0]["quality_rank"] == "A*"
    # A real journal that ABDC does not rank: "none", per the client's request
    # on 12 August. Not a guess, and not silence.
    assert rows[1]["quality_rank"] == "none"
    assert rows[1]["abdc_match_type"] == ""
    # No journal at all (a book chapter) — nothing to rate, so left blank.
    # "none" here would assert something untrue about a journal that does not
    # exist for this record.
    assert rows[2]["quality_rank"] == ""

    # The unmatched report is a to-do list, and must not include blank journals.
    names = [r["journal_name"] for r in read_csv(unmatched)]
    assert "Journal of Nothing" in names
    assert "" not in names


def test_enrich_works_on_another_universitys_column_name(workbook):
    """Sean's UQ export calls the column journal_key, not journal_name. The
    whole point of this being shared is that it works on all of our files."""
    directory = tempfile.mkdtemp()
    path = write_publications(directory, [
        {"name": "x", "journal_key": "Journal of Finance"},
    ], ["name", "journal_key"])
    rows = read_csv(abdc.enrich(path, workbook)[0])
    assert rows[0]["quality_rank"] == "A*"


def test_enrich_fails_clearly_when_no_journal_column_exists(workbook):
    directory = tempfile.mkdtemp()
    path = write_publications(directory, [{"a": "1", "b": "2"}], ["a", "b"])
    with pytest.raises(SystemExit):
        abdc.enrich(path, workbook)


def test_list_year_is_recorded_on_every_matched_row(workbook):
    """Ratings change between editions, so a rating without its edition is not
    reproducible."""
    directory = tempfile.mkdtemp()
    path = write_publications(directory, [
        {"title": "p", "journal_name": "Australian Tax Review"}], ["title", "journal_name"])
    rows = read_csv(abdc.enrich(path, workbook)[0])
    assert rows[0]["quality_rank"] == "B"
    assert rows[0]["abdc_list_year"] == "2025"


def test_the_rating_column_is_named_quality_rank():
    """Scope of Work 3.5.4 names this field `quality_rank` on the Journal
    entity, journals.csv here writes it under that name, and Sean's UQ export
    uses it too. This module called it `abdc_rating` until 22 August, which
    meant our own two output files disagreed about the name of the same value
    and neither matched the dictionary."""
    assert abdc.ADDED_COLUMNS[0] == "quality_rank"
    assert "abdc_rating" not in abdc.ADDED_COLUMNS
    # The abdc_ prefix still belongs on the provenance columns.
    assert all(c.startswith("abdc_") for c in abdc.ADDED_COLUMNS[1:])
