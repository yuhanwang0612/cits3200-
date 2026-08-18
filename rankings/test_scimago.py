"""
Tests for the Scimago SJR join — CITS3200 Group 20.

Offline: the Scimago export these run against is written to a temp directory,
shaped exactly like the real download — semicolon-separated, European decimal
commas, several ISSNs packed into one field, quoted titles containing commas.

Those three quirks are the whole reason this file exists. Read with default
settings, Scimago's "104,065" becomes a hundred and four thousand, and every
SJR-based ranking built on it is silently wrong.

Run:  python -m pytest test_scimago.py -v
"""

import os
import tempfile

import pytest

import scimago


HEADER = ("Rank;Sourceid;Title;Type;Issn;Publisher;SJR;SJR Best Quartile;"
          "H index;Total Docs. (2025);Citations / Doc. (2years);Categories;Areas")

ROWS = [
    # Two ISSNs in one field, no hyphens — exactly how Scimago writes them.
    '1;1;"Journal of Finance";journal;"00221082, 15406261";"Wiley";'
    '23,456;Q1;312;60;8,54;"Finance (Q1); Economics (Q1)";"Economics"',
    # Decimal comma in every numeric field.
    '2;2;"Accounting and Finance";journal;"08105391, 1467629X";"Wiley";'
    '0,845;Q2;61;120;2,17;"Accounting (Q2)";"Business"',
    # A title containing a comma, inside quotes — must not split the row.
    '3;3;"Auditing: A Journal of Practice and Theory";journal;"02780380";"AAA";'
    '1,204;Q1;88;30;3,01;"Accounting (Q1)";"Business"',
    # A book series, not a journal.
    '4;4;"Palgrave Studies in Economic Thought";book series;"12345678";"Palgrave";'
    '0,101;Q4;9;15;0,20;"Economics (Q4)";"Economics"',
    # No quartile at all — some sources have none.
    '5;5;"Weekly Tax Bulletin";journal;"";"Thomson";;;3;10;;"Law";"Social Sciences"',
]


def build_export(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        f.write("\n".join(ROWS) + "\n")
    return path


@pytest.fixture(scope="module")
def export():
    return build_export(os.path.join(tempfile.mkdtemp(), "scimagojr 2025.csv"))


@pytest.fixture(scope="module")
def index(export):
    return scimago.load_scimago(export)[0]


# ---------------------------------------------------------------------------
# The three file quirks
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("23,456", 23.456),      # European decimal comma
    ("0,845", 0.845),
    ("8,54", 8.54),
    ("312", 312.0),
    ("", None),
    (None, None),
    ("n/a", None),
])
def test_european_decimal_comma_is_read_correctly(raw, expected):
    """'104,065' is 104.065, not 104065. Getting this wrong inflates SJR by
    three orders of magnitude and nothing looks broken."""
    assert scimago.clean_number(raw) == expected


def test_semicolon_separator_is_detected(index):
    """The file is semicolon-separated but full of commas, so a reader that
    guesses wrong produces one giant column and silently finds nothing."""
    assert index, "no rows parsed — the delimiter was probably read as a comma"
    assert scimago.jm.normalise("Journal of Finance") in index


def test_title_containing_a_comma_is_not_split(index):
    record = index[scimago.jm.normalise("Auditing: A Journal of Practice and Theory")]
    assert record["title"] == "Auditing: A Journal of Practice and Theory"
    assert record["sjr_quartile"] == "Q1"


def test_all_issns_in_a_packed_field_are_indexed(index):
    """Scimago packs several ISSNs into one field: '00221082, 15406261'.
    Both should find the journal, and both are written without hyphens."""
    assert index["0022-1082"]["title"] == "Journal of Finance"
    assert index["1540-6261"]["title"] == "Journal of Finance"


def test_issn_with_a_trailing_x_is_handled(index):
    assert index["1467-629X"]["title"] == "Accounting and Finance"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def test_values_are_parsed_onto_the_record(index):
    record = index[scimago.jm.normalise("Journal of Finance")]
    assert record["sjr"] == 23.456
    assert record["sjr_quartile"] == "Q1"
    assert record["h_index"] == 312
    assert record["cites_per_doc_2y"] == 8.54
    assert "Finance (Q1)" in record["categories"]


def test_book_series_are_excluded_by_default(export):
    """A book series carrying a quartile in a journal-ranking column would be
    misleading — the client asked for journals."""
    index, _, non_journal = scimago.load_scimago(export)
    assert scimago.jm.normalise("Palgrave Studies in Economic Thought") not in index
    assert non_journal == 1


def test_book_series_can_be_included_on_request(export):
    index, _, _ = scimago.load_scimago(export, journals_only=False)
    assert scimago.jm.normalise("Palgrave Studies in Economic Thought") in index


def test_journal_with_no_quartile_is_kept_with_a_blank(index):
    """Absent is not the same as Q4."""
    record = index[scimago.jm.normalise("Weekly Tax Bulletin")]
    assert record["sjr_quartile"] is None
    assert record["sjr"] is None
    assert record["h_index"] == 3


@pytest.mark.parametrize("raw,expected", [
    ("Q1", "Q1"), ("q3", "Q3"), (" Q2 ", "Q2"),
    ("-", None), ("", None), (None, None),
])
def test_quartile_cleaning(raw, expected):
    assert scimago.clean_quartile(raw) == expected


def test_a_file_that_is_not_the_scimago_export_fails_clearly():
    path = os.path.join(tempfile.mkdtemp(), "wrong.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("a;b;c\n1;2;3\n")
    with pytest.raises(SystemExit):
        scimago.load_scimago(path)


# ---------------------------------------------------------------------------
# Matching — shared with abdc.py, so this checks the wiring, not the rules
# ---------------------------------------------------------------------------
def test_matching_uses_the_same_normalisation_as_abdc(index):
    aliases = scimago.jm.build_aliases(index)
    record, how = scimago.jm.match_journal("The Journal of Finance", None,
                                           index, aliases)
    assert (record["sjr_quartile"], how) == ("Q1", "title")


def test_short_form_matches_via_the_subtitle_alias(index):
    """We write "Auditing"; Scimago writes "Auditing: A Journal of Practice and
    Theory" — the same alias rule ABDC uses."""
    aliases = scimago.jm.build_aliases(index)
    record, how = scimago.jm.match_journal("Auditing", None, index, aliases)
    assert (record["sjr_quartile"], how) == ("Q1", "prefix")


def test_issn_beats_a_mistyped_title(index):
    record, how = scimago.jm.match_journal("Typed Wrong", "0022-1082", index)
    assert (record["title"], how) == ("Journal of Finance", "issn")


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def write_publications(rows, columns):
    import csv
    path = os.path.join(tempfile.mkdtemp(), "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_csv(path):
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_enrich_adds_the_columns_sean_uses(export):
    """Column names match Sean's UQ output so UNSW and UQ merge without
    renaming: sjr, sjr_quartile, h_index, cites_per_doc_2y."""
    path = write_publications([
        {"title": "p1", "journal_name": "The Journal of Finance"},
        {"title": "p2", "journal_name": "Journal of Nothing"},
        {"title": "p3", "journal_name": ""},
    ], ["title", "journal_name"])

    out, unmatched = scimago.enrich(path, export)
    rows = read_csv(out)

    for column in ("sjr", "sjr_quartile", "h_index", "cites_per_doc_2y"):
        assert column in rows[0]
    assert [r["title"] for r in rows] == ["p1", "p2", "p3"]
    assert rows[0]["sjr_quartile"] == "Q1"
    assert rows[0]["sjr"] == "23.456"
    assert rows[1]["sjr_quartile"] == ""      # unmatched, not guessed
    assert rows[2]["sjr_quartile"] == ""      # no journal at all

    names = [r["journal_name"] for r in read_csv(unmatched)]
    assert "Journal of Nothing" in names
    assert "" not in names


def test_enrich_works_on_another_universitys_column_name(export):
    path = write_publications([{"name": "x", "journal_key": "Journal of Finance"}],
                              ["name", "journal_key"])
    rows = read_csv(scimago.enrich(path, export)[0])
    assert rows[0]["sjr_quartile"] == "Q1"


def test_enrich_fails_clearly_when_no_journal_column_exists(export):
    path = write_publications([{"a": "1", "b": "2"}], ["a", "b"])
    with pytest.raises(SystemExit):
        scimago.enrich(path, export)
