"""Fixes from the UNSW data review on 19 September - CITS3200 Group 20.

    python -m pytest tests/test_unsw_review_fixes.py -q

A teammate checked the UNSW output by discipline and found wrong-person
papers, duplicate rows, malformed links and "-" as an SJR quartile. Each test
here is one of those findings, using the real rows.
"""

import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base_scrapers import unsw                                   # noqa: E402
from core.clean import _exclusion_key, clean_pubs                # noqa: E402
from enrichment.scimago import _quartile                         # noqa: E402
from export import build_publications, export                    # noqa: E402


def pub(**kw):
    row = {"name": "Noel Harding", "title": "A paper", "year": "2017",
           "type": "Journal Article", "journal": "Abacus", "doi": None,
           "link": None, "source": "UNSW staff profile"}
    row.update(kw)
    return row


# ------------------------------------------------ wrong-person papers

def test_a_namesake_paper_with_a_doi_is_dropped():
    wrong = pub(name="Andrew Jackson", doi="10.1021/ja0728270",
                title="Tungsten(II) monocarbonyl bis(acetylacetonate)",
                journal="Journal of the American Chemical Society")
    assert clean_pubs([wrong]) == []


def test_a_namesake_paper_with_no_doi_is_dropped_by_title():
    """UNSW lists this 1983 article on an auditing professor's profile, with
    no DOI, so a (name, doi) key could never reach it."""
    wrong = pub(name="Roger Simnett", year="1983",
                title="AIR-MOTOR DRIVES FOR SMALL PUMPS.",
                journal="Chemical Engineering New York")
    assert clean_pubs([wrong]) == []


def test_the_title_match_is_only_for_that_researcher():
    other = pub(name="Someone Else", title="AIR-MOTOR DRIVES FOR SMALL PUMPS.")
    assert len(clean_pubs([other])) == 1


def test_a_blank_doi_is_never_a_key_by_itself():
    """Otherwise one row with no DOI and no title would drop every DOI-less
    paper that researcher has."""
    assert _exclusion_key("Roger Simnett", "", "") is None
    assert _exclusion_key("Roger Simnett", None, None) is None


def test_his_real_doi_less_papers_are_kept():
    real = pub(name="Roger Simnett", title="Assurance on sustainability reports")
    assert len(clean_pubs([real])) == 1


def test_jeff_coultons_space_resources_papers_are_kept():
    """Flagged as a space-mining namesake, but these are co-authored with
    Saydam and Dempster at UNSW and are his. Not in the exclusion list."""
    real = pub(name="Jeff Coulton", doi="10.1016/j.actaastro.2025.04.041",
               title="Placing lunar resources research in the context of "
                     "mining feasibility studies", journal="Acta Astronautica")
    assert len(clean_pubs([real])) == 1


# ------------------------------------------------ duplicates

def test_same_doi_with_the_subtitle_dropped_is_one_paper():
    page = pub(doi="10.1108/maj-08-2013-0914", year="2014",
               title="Elevating professional scepticism")
    orcid = pub(doi="10.1108/maj-08-2013-0914", year="2014", source="ORCID",
                title="Elevating Professional Scepticism: An Expoloratory Study "
                      "Into the Impact of Accountability Pressure")
    assert len(build_publications([page, orcid], verbose=False)) == 1


def test_the_other_harding_pair_merges_too():
    page = pub(doi="10.1108/13217340710763726", year="2007",
               title="The importance in accounting of ambiguity tolerance at "
                     "the national level")
    orcid = pub(doi="10.1108/13217340710763726", year="2007", source="ORCID",
                title="The IMportance in Accounting of Ambiguity Tolerance at the "
                      "National Level: Evidence from Australia and China")
    assert len(build_publications([page, orcid], verbose=False)) == 1


def test_different_titles_under_one_doi_stay_separate():
    """Book reviews batched under one DOI are not a subtitle of each other."""
    a = pub(doi="10.1/batch", title="Agricultural Reform in China, by Yiping Huang")
    b = pub(doi="10.1/batch", title="Vietnam's Reforms and Economic Growth, by "
                                    "Charles Harvie and Tran Van Hoa")
    assert len(build_publications([a, b], verbose=False)) == 2


def test_a_short_prefix_is_not_enough():
    a = pub(doi="10.1/x", title="Editorial")
    b = pub(doi="10.1/x", title="Editorial note on audit quality research")
    assert len(build_publications([a, b], verbose=False)) == 2


# ------------------------------------------------ links

def test_a_doubled_doi_link_is_rebuilt():
    row = pub(doi="http://dx.doi.org/10.2308/bria-50333",
              link="https://doi.org/http://dx.doi.org/10.2308/bria-50333")
    out = clean_pubs([row])[0]
    assert out["doi"] == "10.2308/bria-50333"
    assert out["link"] == "https://doi.org/10.2308/bria-50333"


def test_a_non_doi_link_is_left_alone():
    row = pub(doi="10.2308/bria-50333", link="https://ssrn.com/abstract=1")
    assert clean_pubs([row])[0]["link"] == "https://ssrn.com/abstract=1"


def test_a_relative_unsw_link_is_made_absolute():
    html = ('<div class="publication-item">'
            '<span class="publication-category">Journal articles</span>'
            '<span class="publication-year">2023</span>'
            "<span class=\"rg-title\">'How big is the tax gap'</span>"
            '<span class="rg-source-title">eJournal of Tax Research</span>'
            '<a href="/content/dam/pdfs/business/V20-No2-P203.pdf">pdf</a>'
            '</div>')
    person = {"name_clean": "Neil Warren", "source_id": None,
              "profile_url": "https://www.unsw.edu.au/staff/neil-warren"}
    pubs, _ = unsw.parse_publications(BeautifulSoup(html, "html.parser"), person)
    assert pubs[0]["link"] == ("https://www.unsw.edu.au/content/dam/pdfs/"
                               "business/V20-No2-P203.pdf")


# ------------------------------------------------ SJR quartile

def test_scimago_dash_means_no_quartile():
    assert _quartile("-") is None
    assert _quartile("nan") is None
    assert _quartile(" Q2 ") == "Q2"


# ------------------------------------------------ export runs at all

def test_export_runs_end_to_end(tmp_path):
    """main's export() used Path without importing it, so every run crashed
    at the last step with NameError."""
    records = [{"name_clean": "Noel Harding", "university": "UNSW Sydney",
                "discipline": "Accounting", "profile_url": "https://x"}]
    tables = export(records, [pub(doi="10.1/a")], out_dir=tmp_path / "unsw",
                    verbose=False)
    assert len(tables["publications"]) == 1
