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
