"""Offline tests for FIX G — export.py's near-duplicate merge, applied
after the existing exact-match dedup rule.

    python -m pytest tests/test_export_neardup.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export import build_publications, is_near_duplicate  # noqa: E402


def _pub(name="Louise Lu", title="A Study Of Things", doi=None,
         year="2020", journal="Accounting Review", link=None, source=None):
    return {"type": "Journal Article", "name": name, "title": title,
            "doi": doi, "year": year, "journal": journal, "link": link,
            "source": source}


# ------------------------------------------------------------- required cases

def test_ssrn_page_copy_merges_with_journal_doi_copy():
    """Neil Fargher / Marvin Wee shape: page row carries the SSRN preprint
    DOI, the retrieved row carries the real journal DOI for the same
    paper — must merge, keeping the real DOI."""
    page = _pub(title="The impact of Ball and Brown (1968) on generations of research",
                doi="10.2139/ssrn.3304915", year="2019", source="ANU staff profile")
    retrieved = _pub(title="The impact of Ball and Brown (1968) on generations of research",
                      doi="10.1016/j.pacfin.2019.01.006", year="2019", source="ORCID")
    out = build_publications([page, retrieved], verbose=False)
    assert len(out) == 1
    assert out[0]["doi"] == "10.1016/j.pacfin.2019.01.006"


def test_two_different_real_dois_never_merge():
    """'Busy directors and firm performance': 2020 Pacific-Basin Finance
    Journal vs 2021 Accounting and Finance — same near-identical title,
    each with its OWN distinct real DOI. Must both survive."""
    a = _pub(title="Busy directors and firm performance",
             doi="10.1016/j.pacfin.2020.101434", year="2020", journal="Pacific-Basin Finance Journal")
    b = _pub(title="Busy directors and firm performance",
             doi="10.1111/acfi.12631", year="2021", journal="Accounting and Finance")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2
    assert {r["doi"] for r in out} == {"10.1016/j.pacfin.2020.101434", "10.1111/acfi.12631"}


def test_isabel_wang_different_papers_not_merged():
    """Two real, different Isabel Wang papers that share a long common
    prefix — must NOT be treated as the same paper."""
    a = _pub(
        name="Isabel Wang",
        title=("The effects of tone at the top and coordination with external "
               "auditors on internal auditors’ assessments of the likelihood "
               "of financial misstatements"),
        doi=None, year="2015",
    )
    b = _pub(
        name="Isabel Wang",
        title=("The effects of tone at the top and coordination with external "
               "auditors on internal auditors’ fraud risk assessments"),
        doi="10.1111/acfi.12191", year="2017",
    )
    assert is_near_duplicate(a, b) is False
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2


def test_year_off_by_one_still_merges():
    page = _pub(title="Do banks audited by specialist engage in less real activities management",
                doi=None, year="2019", source="ANU staff profile")
    retrieved = _pub(title="Do Banks Audited by Specialists Engage in Less Real Activities Management",
                      doi="10.2308/ajpt-52017", year="2018", source="OpenAlex")
    out = build_publications([page, retrieved], verbose=False)
    assert len(out) == 1
    assert out[0]["doi"] == "10.2308/ajpt-52017"


def test_year_off_by_two_does_not_merge():
    """Same near-fuzzy-match shape as the year-off-by-one case above (a
    genuine one-word title difference, so the exact-match rule above FIX G
    can't already have merged it), but two years apart instead of one."""
    a = _pub(title="A Study of Corporate Governance Practices", doi=None,
             year="2018", source="ANU staff profile")
    b = _pub(title="A Study of Corporate Governance Practice", doi="10.1/real",
             year="2020", source="ORCID")
    assert is_near_duplicate(a, b) is False
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2


def test_ratio_just_below_threshold_does_not_merge():
    a = _pub(title="Completely unrelated financial reporting topic alpha")
    b = _pub(title="A wholly different accounting research subject beta")
    assert is_near_duplicate(a, b) is False


def test_ratio_just_above_threshold_merges():
    """A single-word capitalisation/typo difference on an otherwise long,
    identical title — ratio comfortably above 0.85."""
    a = _pub(title="Order size, order imbalance and the volatility-volume relation in a bull versus a bear market",
             doi=None, year="2012", source="ANU staff profile")
    b = _pub(title="Order size, order imbalance and the volume-volatility relation in a bull versus a bear market",
             doi="10.1111/j.1467-629x.2011.00420.x", year="2012", source="ORCID")
    assert is_near_duplicate(a, b) is True
    out = build_publications([a, b], verbose=False)
    assert len(out) == 1


# ------------------------------------------------------------- guard cases
# (added while tightening the rule against confirmed false positives in the
# committed UNSW data — see scratch/_anu16/neardup_simulation.txt)

def test_differing_part_number_never_merges():
    a = _pub(name="Dale Boccabella",
             title="No full FITO on US capital gains as CGT discount - Part 1", year="2019")
    b = _pub(name="Dale Boccabella",
             title="No full FITO on US capital gains as CGT discount - Part 2", year="2019")
    assert is_near_duplicate(a, b) is False


def test_differing_part_roman_numeral_never_merges():
    a = _pub(name="Someone", title="Dealing with goodwill - small business roll-overs: part I", year="2005")
    b = _pub(name="Someone", title="Dealing with goodwill - small business roll-overs: part II", year="2005")
    assert is_near_duplicate(a, b) is False


def test_differing_link_with_no_doi_never_merges():
    """Two page-sourced rows, neither with a DOI, each with its own real,
    distinct source URL — a strong identifier even without a DOI."""
    a = _pub(name="Gordon Mackenzie",
             title="So, you want to get into the SMSF market? Here's what you need to know.",
             doi=None, year="2015", link="http://example.com/note-2015")
    b = _pub(name="Gordon Mackenzie",
             title="So, you want to get into the SMSF market? Here's what you need to know. Part 2",
             doi=None, year="2016", link="http://example.com/note-2016")
    assert is_near_duplicate(a, b) is False


def test_ssrn_link_difference_does_not_block_the_ssrn_merge():
    """A link value derived from a doi (e.g. https://doi.org/<doi>) differs
    between an SSRN-DOI row and a real-DOI row for the same reason the
    DOIs differ — the link guard must not re-introduce the split the SSRN
    rule above already resolves."""
    page = _pub(title="The Opioid Crisis, Employee Health Capital, and Corporate Information Production",
                doi="10.2139/ssrn.4602184", year="2023", source="ANU staff profile",
                link="https://doi.org/10.2139/ssrn.4602184")
    retrieved = _pub(title="The Opioid Crisis, Employee Health Capital, and Corporate Information Production",
                      doi="10.1080/09638180.2023.2272622", year="2023", source="Crossref",
                      link="https://doi.org/10.1080/09638180.2023.2272622")
    assert is_near_duplicate(page, retrieved) is True


def test_exact_article_with_repository_doi_keeps_journal_version():
    journal = _pub(
        title="Reporting Bias and Monitoring", year="2021",
        doi="10.1111/article", journal="Contemporary Accounting Research",
        source="UniMelb Minerva",
    )
    repository = _pub(
        title="Reporting Bias and Monitoring", year="2021",
        doi="10.18154/deposit",
        journal="Zurich Open Repository and Archive (University of Zurich)",
        source="OpenAlex",
    )
    out = build_publications([journal, repository], verbose=False)
    assert len(out) == 1
    assert out[0]["doi"] == "10.1111/article"


def test_exact_same_journal_doi_aliases_are_merged():
    legacy = _pub(
        title="Bayesian arbitrage threshold analysis", year="1999",
        doi="10.2307/1392294",
        journal="Journal of Business & Economic Statistics",
    )
    publisher = _pub(
        title="Bayesian arbitrage threshold analysis", year="1999",
        doi="10.1080/07350015.1999.10524825",
        journal="Journal of Business & Economic Statistics", source="ORCID",
    )
    assert len(build_publications([legacy, publisher], verbose=False)) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
