"""Fixes from validating the regenerated UNSW output - CITS3200 Group 20.

    python -m pytest tests/test_unsw_output_fixes.py -q

After the review fixes were merged, `python validate_data.py unsw` still
failed on ISSN format and on nine identical rows. Each test here is one of
those rows.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export import _clean_issns, build_journals, build_publications  # noqa: E402


def pub(**kw):
    row = {"name": "Mandy Cheng", "title": "A paper", "year": "2007",
           "type": "Journal Article", "journal": "Abacus", "doi": None,
           "link": None, "source": "UNSW staff profile"}
    row.update(kw)
    return row


# ------------------------------------------------ ISSNs

def test_issns_are_hyphenated_and_listed_once():
    got = _clean_issns(["0810-5391", "1467-629X", "08105391 1467629X",
                        "1467629X 08105391"])
    assert got == ["0810-5391", "1467-629X"]


def test_words_are_not_issns():
    assert _clean_issns(["Emerald", "Group", "Publishing", "1321-7348"]) == ["1321-7348"]


def test_the_ssrn_issn_is_not_the_journals():
    assert _clean_issns(["0022-1082", "1540-6261", "1556-5068"]) == [
        "0022-1082", "1540-6261"]


def test_journal_row_merges_issns_from_every_copy():
    rows = [pub(journal="Accounting and Finance", issns=["0810-5391"]),
            pub(journal="Accounting and Finance", issns=["08105391 1467629X"])]
    (journal,) = build_journals(rows)
    assert journal["issn"] == "0810-5391; 1467-629X"


# ------------------------------------------------ same paper, two DOIs

def test_a_mistyped_doi_does_not_keep_a_duplicate():
    """UNSW has a dot where the DOI has a hyphen."""
    title = ("Corporate governance and board composition: Diversity and "
             "independence of Australian boards")
    page = pub(doi="10.1111/j.1467.8683.2007.00554.x", title=title,
               journal="Corporate Governance: An International Review")
    orcid = pub(doi="10.1111/j.1467-8683.2007.00554.x", source="ORCID",
                title=title.replace("Diversity", "diversity"),
                journal="Corporate Governance: An International Review")
    assert len(build_publications([page, orcid], verbose=False)) == 1


def test_different_journals_are_not_merged():
    title = "Audit review effectiveness in an analytical review task"
    a = pub(doi="10.1/a", title=title, journal="Journal A")
    b = pub(doi="10.1/b", title=title, journal="Journal B")
    assert len(build_publications([a, b], verbose=False)) == 2


def test_different_years_are_not_merged():
    title = "Audit review effectiveness in an analytical review task"
    a = pub(doi="10.1/a", title=title, year="1999")
    b = pub(doi="10.1/b", title=title, year="2003")
    assert len(build_publications([a, b], verbose=False)) == 2


def test_a_short_repeated_title_is_not_merged():
    a = pub(doi="10.1/a", title="Discussion")
    b = pub(doi="10.1/b", title="Discussion")
    assert len(build_publications([a, b], verbose=False)) == 2
