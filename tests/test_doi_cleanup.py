"""DOI cleanup, working-paper series and the reviewed DOI overrides.

    python -m pytest tests/test_doi_cleanup.py -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.clean import clean_doi, override_fields   # noqa: E402
from core.schema import clean_journal               # noqa: E402


@pytest.mark.parametrize("raw, expected", [
    ("10.1111/j.1467-6281.2012.00373.x/pdf", "10.1111/j.1467-6281.2012.00373.x"),
    ("10.1108/AAAJ-02-2023-187/full/pdf", "10.1108/AAAJ-02-2023-187"),
    ("10.1108/MEDAR-03-2020-0803/full", "10.1108/MEDAR-03-2020-0803"),
    ("https://doi.org/10.3389/fnins.2012.00150/pdf", "10.3389/fnins.2012.00150"),
    ("10.1016/j.bankfin.2006.06.018", "10.1016/j.jbankfin.2006.06.018"),
    ("10.1016/j.jbankfin.2006.06.018", "10.1016/j.jbankfin.2006.06.018"),
    ("10.1111/jofi.12345", "10.1111/jofi.12345"),
])
def test_publisher_page_paths_and_the_jbf_typo_are_cleaned(raw, expected):
    assert clean_doi(raw) == expected


@pytest.mark.parametrize("name", [
    "NBER Working Paper Series",
    "Federal Reserve Bank of Dallas, Globalization Institute Working Papers",
    "European Corporate Governance Institute – Finance Working Paper",
])
def test_a_working_paper_series_is_not_a_journal(name):
    assert clean_journal(name) is None


@pytest.mark.parametrize("name", ["Journal of Finance", "Journal of Financial Economics",
                                  "Accounting, Organizations and Society"])
def test_a_real_journal_is_kept(name):
    assert clean_journal(name) == name


@pytest.mark.parametrize("raw, fixed", [
    ("10.5555/0927-7544.24.2.359", "10.1080/10835547.2016.12090433"),
    ("10.1061/(asce)0733-9496(1995)121:3(235", "10.1061/(asce)0733-9496(1995)121:3(235)"),
    ("10.3386/w27305", "10.1017/s0022109022001466"),
    ("10.3386/w6427", "10.3905/jpm.2000.319767"),
])
def test_reviewed_wrong_dois_are_overridden(raw, fixed):
    assert override_fields({}, raw)["doi"] == fixed


@pytest.mark.parametrize("name", [
    "Finance and Economics Discussion Series", "Bank of Finland Research Discussion Paper",
    "HKU Scholars Hub (University of Hong Kong)", "Research Online (University of Wollongong)",
    "RMIT Research Repository (RMIT University Library)", "Zenodo (CERN European Organization for Nuclear Research)",
    "AgEcon Search (University of Minnesota, USA)", "CFA Digest"])
def test_discussion_series_and_repositories_are_not_journals(name):
    assert clean_journal(name) is None


@pytest.mark.parametrize("name", ["Sociological Research Online", "University of New South Wales Law Journal",
                                  "Review of Applied Economics", "Journal of Banking & Finance"])
def test_journals_with_similar_words_are_kept(name):
    assert clean_journal(name) == name
