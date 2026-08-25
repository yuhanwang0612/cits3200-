"""
Tests for the journal table builder — CITS3200 Group 20.

Offline: both reference files are generated in temp directories.

The interesting tests here are the ISSN conflict ones. Cross-checking ABDC
against Scimago is the only thing that catches a journal being matched to the
wrong journal — each source looks perfectly reasonable on its own.

Run:  python -m pytest test_journals.py -v
"""

import csv
import os
import tempfile

import pytest
from openpyxl import Workbook

import journal_match as jm
import journals


# ---------------------------------------------------------------------------
# Fixtures shaped like the real files
# ---------------------------------------------------------------------------
def build_abdc(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "2025 JQL"
    ws.append(["ABDC Journal Quality List 2025", None, None, None, None])
    ws.append(["Journal Title", "ISSN", "ISSNOnline", "FoR", "2025 rating"])
    ws.append(["Journal of Finance", "0022-1082", "1540-6261", "3502", "A*"])
    ws.append(["Accounting and Finance", "0810-5391", "1467-629X", "3501", "A"])
    # The trap: a practitioner journal whose name starts with a famous one.
    ws.append(["Journal of Banking and Finance - Law and Practice",
               "1034-3040", None, "3501", "A"])
    # No ISSN at all on the ABDC side — Scimago should supply one.
    ws.append(["Australian Tax Forum", None, None, "3501", "A*"])
    wb.save(path)
    return path


SCIMAGO_HEADER = ("Rank;Sourceid;Title;Type;Issn;Publisher;SJR;SJR Best Quartile;"
                  "H index;Citations / Doc. (2years);Categories")
SCIMAGO_ROWS = [
    '1;1;"Journal of Finance";journal;"00221082, 15406261";"Wiley";23,456;Q1;312;8,54;"Finance (Q1)"',
    '2;2;"Accounting and Finance";journal;"08105391";"Wiley";0,845;Q2;61;2,17;"Accounting (Q2)"',
    # The famous journal the practitioner one gets confused with.
    '3;3;"Journal of Banking and Finance";journal;"03784266";"Elsevier";1,954;Q1;225;5,10;"Finance (Q1)"',
    '4;4;"Australian Tax Forum";journal;"0812695X";"Tax Institute";0,25;Q3;12;0,40;"Law (Q3)"',
]


def build_scimago(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(SCIMAGO_HEADER + "\n" + "\n".join(SCIMAGO_ROWS) + "\n")
    return path


@pytest.fixture(scope="module")
def refs():
    d = tempfile.mkdtemp()
    return (build_abdc(os.path.join(d, "abdc.xlsx")),
            build_scimago(os.path.join(d, "scimago.csv")))


def make_publications(journal_names):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "journal_name"])
        w.writeheader()
        for i, name in enumerate(journal_names):
            w.writerow({"title": f"paper {i}", "journal_name": name})
    return path


def run(journal_names, refs, **kwargs):
    abdc, scimago = refs
    out = journals.build(make_publications(journal_names), abdc, scimago, **kwargs)
    with open(out, newline="", encoding="utf-8") as f:
        return {r["journal_name"]: r for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------
def test_one_row_per_journal_not_per_publication(refs):
    rows = run(["Journal of Finance"] * 5 + ["Accounting and Finance"] * 2, refs)
    assert len(rows) == 2
    assert rows["Journal of Finance"]["publication_count"] == "5"
    assert rows["Accounting and Finance"]["publication_count"] == "2"


def test_rows_are_ordered_by_publication_count(refs):
    abdc, scimago = refs
    out = journals.build(make_publications(
        ["Accounting and Finance"] + ["Journal of Finance"] * 3), abdc, scimago)
    with open(out, newline="", encoding="utf-8") as f:
        names = [r["journal_name"] for r in csv.DictReader(f)]
    assert names[0] == "Journal of Finance"


def test_scope_journal_entity_columns_are_present(refs):
    """Scope of Work 3.5.4 models Journal with issn, quality_rank and both
    impact factor fields. They must exist even though Clarivate data does not
    yet, or the merge has nowhere to put it."""
    rows = run(["Journal of Finance"], refs)
    row = rows["Journal of Finance"]
    for column in ("journal_name", "issn", "quality_rank",
                   "impact_factor", "impact_factor_5yr"):
        assert column in row
    assert row["impact_factor"] == ""        # not available yet, but not missing


# ---------------------------------------------------------------------------
# ISSN — the point of the exercise
# ---------------------------------------------------------------------------
def test_issn_comes_from_abdc(refs):
    """UNSW's website never gives us an ISSN; the reference list does."""
    row = run(["Journal of Finance"], refs)["Journal of Finance"]
    assert row["issn"] == "0022-1082"
    assert row["issn_online"] == "1540-6261"


def test_issn_falls_back_to_scimago(refs):
    """ABDC has no ISSN for Australian Tax Forum; Scimago does."""
    row = run(["Australian Tax Forum"], refs)["Australian Tax Forum"]
    assert row["quality_rank"] == "A*"       # still rated by ABDC
    assert row["issn"] == "0812-695X"        # but the ISSN came from Scimago


def test_unrated_journal_is_none_and_has_no_issn(refs):
    row = run(["Weekly Tax Bulletin"], refs)["Weekly Tax Bulletin"]
    assert row["quality_rank"] == "none"     # client's wording, 12 August
    assert row["issn"] == ""
    assert row["sjr"] == ""


# ---------------------------------------------------------------------------
# The conflict guard
# ---------------------------------------------------------------------------
def test_weaker_match_is_dropped_when_issns_disagree(refs):
    """Real record. "Journal of Banking and Finance: Law and Practice" is an
    Australian practitioner journal (ABDC A). Trimming its subtitle turns it
    into the top-tier "Journal of Banking and Finance", which Scimago then
    matches — SJR 1.954, Q1, h-index 225. ABDC matched exactly and Scimago
    only via a trimmed subtitle, so Scimago is the one that is wrong."""
    row = run(["Journal of Banking and Finance: Law and Practice"], refs)[
        "Journal of Banking and Finance: Law and Practice"]
    assert row["quality_rank"] == "A"                    # ABDC kept
    assert row["abdc_match_type"] == "title"
    assert row["sjr"] == ""                              # Scimago rejected
    assert row["sjr_quartile"] == ""
    assert row["scimago_match_type"] == ""
    assert "mis-match" in row["issn_conflict"]


def test_agreeing_sources_are_left_alone(refs):
    row = run(["Journal of Finance"], refs)["Journal of Finance"]
    assert row["quality_rank"] == "A*"
    assert row["sjr_quartile"] == "Q1"
    assert row["issn_conflict"] == ""


def test_equal_strength_conflict_is_flagged_not_resolved():
    """Two different journals can genuinely share a title — "Economia" is one.
    When both sources matched exactly, neither is more trustworthy, so both are
    kept and a human is told."""
    d = tempfile.mkdtemp()
    wb = Workbook(); ws = wb.active; ws.title = "2025 JQL"
    ws.append(["Journal Title", "ISSN", "ISSNOnline", "FoR", "2025 rating"])
    ws.append(["Economia", "0254-4415", "2304-4306", "3801", "C"])
    abdc = os.path.join(d, "a.xlsx"); wb.save(abdc)
    scimago = os.path.join(d, "s.csv")
    with open(scimago, "w", encoding="utf-8") as f:
        f.write(SCIMAGO_HEADER + "\n"
                '1;1;"Economia";journal;"15177580, 23582820";"X";0,32;Q2;20;1,0;"Economics (Q2)"\n')
    out = journals.build(make_publications(["Economia"]), abdc, scimago)
    with open(out, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["quality_rank"] == "C"        # both kept
    assert row["sjr_quartile"] == "Q2"
    assert "needs a human" in row["issn_conflict"]


# ---------------------------------------------------------------------------
# Using an ISSN the publications file already carries
# ---------------------------------------------------------------------------
def test_issn_in_the_publications_file_is_used_to_match(refs):
    """openalex.py adds an `issn` column. If this is ignored, every journal is
    matched on its title and the ISSN might as well not be there — which is
    exactly what happened the first time this ran on real data: identical match
    counts before and after OpenAlex."""
    abdc, scimago = refs
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "journal_name", "issn"])
        w.writeheader()
        # A title no reference list would ever match, but a real ISSN.
        w.writerow({"title": "p", "journal_name": "J. Fin. (typo'd)",
                    "issn": "0022-1082"})
    out = journals.build(path, abdc, scimago)
    with open(out, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["quality_rank"] == "A*"
    assert row["abdc_match_type"] == "issn"
    assert row["sjr_quartile"] == "Q1"


def test_issn_from_the_publications_file_survives_even_with_no_match(refs):
    """A journal nothing rates still keeps the ISSN we already knew, so the
    Clarivate join later can still find it."""
    abdc, scimago = refs
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "journal_name", "issn"])
        w.writeheader()
        w.writerow({"title": "p", "journal_name": "Weekly Tax Bulletin",
                    "issn": "1234-5678"})
    out = journals.build(path, abdc, scimago)
    with open(out, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["quality_rank"] == "none"
    assert row["issn"] == "1234-5678"


def test_at_least_one_drops_unmatched_journals(refs):
    abdc, scimago = refs
    path = make_publications(["Journal of Finance", "Weekly Tax Bulletin"])
    out = journals.build(path, abdc, scimago, at_least_one=True)
    with open(out, newline="", encoding="utf-8") as f:
        names = [r["journal_name"] for r in csv.DictReader(f)]
    assert names == ["Journal of Finance"]


def test_unmatched_journals_are_kept_by_default(refs):
    """The unmatched list is the to-do list for improving coverage — dropping
    it hides the work rather than doing it."""
    rows = run(["Journal of Finance", "Weekly Tax Bulletin"], refs)
    assert "Weekly Tax Bulletin" in rows


def test_blank_journal_names_are_ignored(refs):
    """Book chapters and media have no journal."""
    rows = run(["Journal of Finance", "", "  "], refs)
    assert list(rows) == ["Journal of Finance"]


def test_needs_at_least_one_source(refs):
    with pytest.raises(SystemExit):
        journals.build(make_publications(["Journal of Finance"]))


# ---------------------------------------------------------------------------
# Harvest record — data dictionary 3.5.4 / FR14
# ---------------------------------------------------------------------------
def test_a_ranking_list_is_recorded_as_a_source(refs):
    """The client's 19 August instruction: for citation figures the date that
    counts is when we read the list, not the date the list carries."""
    import harvest
    abdc, scimago = refs
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "journal_name", "university"])
        w.writeheader()
        w.writerow({"title": "p", "journal_name": "Journal of Finance",
                    "university": "University of New South Wales"})
    journals.build(path, abdc, scimago)

    by_source = {r["source"]: r for r in harvest.read(d)}
    assert "ABDC JQL 2025" in by_source
    assert "Scimago" in by_source
    assert by_source["ABDC JQL 2025"]["university"] == "University of New South Wales"
    assert by_source["ABDC JQL 2025"]["last_run"].endswith("+00:00")


def test_no_harvest_row_when_the_file_has_no_university(refs):
    """Rather than inventing one. Older publication exports have no university
    column, and a row naming the wrong institution is worse than no row."""
    import harvest
    abdc, scimago = refs
    path = make_publications(["Journal of Finance"])   # no university column
    journals.build(path, abdc, scimago)
    assert harvest.read(os.path.dirname(path)) == []


def test_edition_year_reads_the_year_out_of_the_filename():
    """Scimago's export carries no edition field inside the file, so the
    filename is the only record of which edition was used."""
    assert journals.edition_year("scimagojr 2025.csv") == "2025"
    assert journals.edition_year("../data/ABDC-JQL-2025-v2-270526.xlsx") == "2025"


def test_edition_year_is_none_rather_than_a_guess():
    assert journals.edition_year("scimago.csv") is None
    assert journals.edition_year("list12345.csv") is None
    assert journals.edition_year(None) is None


# ---------------------------------------------------------------------------
# Writing the ratings back onto the publication rows
# ---------------------------------------------------------------------------
def pubs_with(rows, columns, path=None):
    d = tempfile.mkdtemp()
    path = path or os.path.join(d, "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(columns))
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in columns})
    return path


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_write_back_puts_the_rating_on_every_publication_row(refs):
    """UQ, Monash and Adelaide all carry quality_rank on the publication row.
    Keeping it only in journals.csv makes UNSW read as unrated in a merge."""
    abdc, scimago = refs
    path = pubs_with(
        [{"title": "a", "journal_name": "Journal of Finance"},
         {"title": "b", "journal_name": "Journal of Finance"},
         {"title": "c", "journal_name": "Accounting and Finance"}],
        ["title", "journal_name"])
    journals.build(path, abdc, scimago, write_back=True)
    rows = read_rows(path)
    assert [r["quality_rank"] for r in rows] == ["A*", "A*", "A"]
    assert [r["sjr_quartile"] for r in rows] == ["Q1", "Q1", "Q2"]


def test_write_back_is_off_by_default(refs):
    abdc, scimago = refs
    path = pubs_with([{"title": "a", "journal_name": "Journal of Finance"}],
                     ["title", "journal_name"])
    journals.build(path, abdc, scimago)
    assert "quality_rank" not in read_rows(path)[0]


def test_write_back_never_overwrites_an_issn_from_openalex(refs):
    """An ISSN already on the row came from the publisher via a DOI lookup. One
    derived from a title match is a weaker claim and must not replace it."""
    abdc, scimago = refs
    path = pubs_with(
        [{"title": "a", "journal_name": "Journal of Finance", "issn": "9999-0000"}],
        ["title", "journal_name", "issn"])
    journals.build(path, abdc, scimago, write_back=True)
    assert read_rows(path)[0]["issn"] == "9999-0000"


def test_write_back_fills_an_issn_the_row_did_not_have(refs):
    abdc, scimago = refs
    path = pubs_with([{"title": "a", "journal_name": "Journal of Finance"}],
                     ["title", "journal_name"])
    journals.build(path, abdc, scimago, write_back=True)
    assert read_rows(path)[0]["issn"] == "0022-1082"


def test_write_back_drops_the_dead_placeholder_column(refs):
    """abdc_self_reported has never been filled, is not in the data dictionary,
    and the team's merge script maps it onto quality_rank — so leaving it in
    would blank out the ratings this very function just added."""
    abdc, scimago = refs
    path = pubs_with(
        [{"title": "a", "journal_name": "Journal of Finance",
          "abdc_self_reported": ""}],
        ["title", "journal_name", "abdc_self_reported"])
    journals.build(path, abdc, scimago, write_back=True)
    row = read_rows(path)[0]
    assert "abdc_self_reported" not in row
    assert row["quality_rank"] == "A*"


def test_write_back_marks_an_unrated_journal_none_not_blank(refs):
    """The client's 12 August wording. Blank reads as "we did not check"."""
    abdc, scimago = refs
    path = pubs_with([{"title": "a", "journal_name": "Weekly Tax Bulletin"}],
                     ["title", "journal_name"])
    journals.build(path, abdc, scimago, write_back=True)
    assert read_rows(path)[0]["quality_rank"] == "none"


def test_write_back_leaves_a_row_with_no_journal_blank(refs):
    """A book chapter has no journal, so it has no rating — which is different
    from being an unrated journal."""
    abdc, scimago = refs
    path = pubs_with(
        [{"title": "chapter", "journal_name": ""},
         {"title": "paper", "journal_name": "Journal of Finance"}],
        ["title", "journal_name"])
    journals.build(path, abdc, scimago, write_back=True)
    rows = read_rows(path)
    assert rows[0]["quality_rank"] == ""
    assert rows[1]["quality_rank"] == "A*"


def test_write_back_does_not_carry_the_audit_columns_onto_publications(refs):
    """abdc_match_type and issn_conflict describe how the *journal* matched.
    They belong in journals.csv, not repeated on 2,000 paper rows."""
    abdc, scimago = refs
    path = pubs_with([{"title": "a", "journal_name": "Journal of Finance"}],
                     ["title", "journal_name"])
    journals.build(path, abdc, scimago, write_back=True)
    row = read_rows(path)[0]
    for column in ("abdc_match_type", "scimago_match_type", "issn_conflict",
                   "publication_count", "journal_canonical"):
        assert column not in row


def test_write_back_keeps_every_original_column_and_row(refs):
    abdc, scimago = refs
    original = ["title", "journal_name", "doi", "year", "researcher_name"]
    path = pubs_with(
        [{"title": "a", "journal_name": "Journal of Finance", "doi": "10.1/x",
          "year": "2024", "researcher_name": "Someone"}] * 3, original)
    journals.build(path, abdc, scimago, write_back=True)
    rows = read_rows(path)
    assert len(rows) == 3
    for column in original:
        assert column in rows[0]
    assert rows[0]["doi"] == "10.1/x"


def test_write_back_uses_the_cross_checked_rating_not_a_fresh_lookup(refs):
    """The rating has to come from journals.csv, where a match has already been
    cross-checked against the other source's ISSN. "Journal of Banking and
    Finance: Law and Practice" is ABDC A; a fresh Scimago lookup hands it the
    top-tier journal's Q1 and SJR 1.954. Only the cross-check catches it."""
    abdc, scimago = refs
    path = pubs_with(
        [{"title": "a",
          "journal_name": "Journal of Banking and Finance: Law and Practice"}],
        ["title", "journal_name"])
    journals.build(path, abdc, scimago, write_back=True)
    row = read_rows(path)[0]
    assert row["quality_rank"] == "A"        # ABDC kept
    assert row["sjr_quartile"] == ""         # the wrong Scimago match rejected
    assert row["sjr"] == ""


def test_unrated_rows_are_counted_separately_from_graded_ones(refs):
    """"none" is the client's wording for a journal ABDC does not rate. It is a
    finding about the outlet, not a grade. Counting the two together reported
    1,985 of 2,000 UNSW rows as rated when the real figure was 1,544."""
    abdc, scimago = refs
    path = pubs_with(
        [{"title": "a", "journal_name": "Journal of Finance"},
         {"title": "b", "journal_name": "Weekly Tax Bulletin"},
         {"title": "c", "journal_name": "Weekly Tax Bulletin"},
         {"title": "d", "journal_name": ""}],
        ["title", "journal_name"])
    rows, fieldnames = jm.read_publications(path)
    records = [{"journal_name": "Journal of Finance", "quality_rank": "A*",
                "issn": "0022-1082"},
               {"journal_name": "Weekly Tax Bulletin", "quality_rank": "none"}]
    graded, unrated, issns = journals.apply_to_publications(
        path, records, "journal_name", rows, fieldnames)
    assert (graded, unrated) == (1, 2)
