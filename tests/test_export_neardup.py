"""Offline tests for FIX G — export.py's near-duplicate merge, applied
after the existing exact-match dedup rule.

    python -m pytest tests/test_export_neardup.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import export as exp  # noqa: E402
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


# --------------------------------------------------------- FIX K (scratch/_anu18)
# Exact title+year+journal duplicate merge, checked BEFORE is_near_duplicate's
# own "two distinct real DOIs never merge" guard — for the same paper indexed
# twice under two different DOIs by a retrieval source, not two genuinely
# different papers that happen to share a title.

def test_same_paper_indexed_twice_with_different_dois_merges():
    """Susanna Ho shape: same normalised title, same year, same journal,
    but two different real DOIs (10.17705/1cais.03823 vs
    10.17705/1cais.038123 — the second is the first with an extra digit
    spliced in; both resolve to the exact same page at doi.org). The
    is_near_duplicate DOI guard alone would keep both; FIX K merges them."""
    a = _pub(name="Susanna Ho",
             title=("Partial Least Squares Structural Equation Modeling "
                    "Approach for Analyzing a Model with a Binary Indicator "
                    "as an Endogenous Variable"),
             doi="10.17705/1cais.03823", year="2016",
             journal="Communications of the Association for Information Systems")
    b = _pub(name="Susanna Ho",
             title=("Partial Least Squares Structural Equation Modeling "
                    "Approach for Analyzing a Model with a Binary Indicator "
                    "as an Endogenous Variable"),
             doi="10.17705/1cais.038123", year="2016",
             journal="Communications of the Association for Information Systems")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 1
    # shorter/canonical DOI wins
    assert out[0]["doi"] == "10.17705/1cais.03823"


def test_busy_directors_pair_still_not_touched_by_fix_k():
    """Same fixture as test_two_different_real_dois_never_merge above,
    re-asserted here to pin down that FIX K specifically (year AND journal
    both differ) doesn't merge it either, not just is_near_duplicate."""
    a = _pub(title="Busy directors and firm performance",
             doi="10.1016/j.pacfin.2020.101434", year="2020",
             journal="Pacific-Basin Finance Journal")
    b = _pub(title="Busy directors and firm performance",
             doi="10.1111/acfi.12631", year="2021",
             journal="Accounting and Finance")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2


def test_exact_dup_rule_requires_same_journal_too():
    """Exact title and year, but a different journal — a real coincidence,
    not a double-indexed duplicate. Must not merge."""
    a = _pub(name="Someone", title="A Shared Title By Coincidence",
             doi="10.1/aaa", year="2020", journal="Journal A")
    b = _pub(name="Someone", title="A Shared Title By Coincidence",
             doi="10.1/bbb", year="2020", journal="Journal B")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2


# --------------------------------------------------------- FIX L (scratch/_anu19)
# Prefix-containment duplicate merge: ORCID returns a truncated main-title-only
# copy, the ANU staff page returns the full title including its subtitle —
# same paper, but title-similarity ratio is far below NEAR_DUP_TITLE_RATIO and
# FIX K needs an identical title, so neither existing rule sees it.

def test_tracy_wang_prefix_subtitle_merges_keeping_the_doi_copy():
    short = _pub(name="Tracy (Kun) Wang", title="Analyst Coverage and Corporate Innovation",
                 doi="10.1111/abac.12360", year="2025", journal="Abacus")
    long_ = _pub(name="Tracy (Kun) Wang",
                 title=("Analyst Coverage and Corporate Innovation: Evidence "
                        "from Exogenous Changes in Analyst Coverage"),
                 doi=None, year="2025", journal="Abacus")
    out = build_publications([short, long_], verbose=False)
    assert len(out) == 1
    assert out[0]["doi"] == "10.1111/abac.12360"
    assert out[0]["title"] == "Analyst Coverage and Corporate Innovation"


def test_busy_directors_pair_not_merged_by_fix_l():
    """Negative test required by the brief: the genuine 'Busy directors and
    firm performance' pair must survive FIX L too, not just FIX K/G — it
    fails on title (identical, not a strict prefix — there is no subtitle
    to strip) as well as on year and journal, so it was never at risk, but
    this pins that down explicitly rather than leaving it implicit."""
    a = _pub(name="Sorin Daniliuc", title="Busy directors and firm performance",
             doi="10.1111/acfi.12631", year="2021", journal="Accounting and Finance")
    b = _pub(name="Sorin Daniliuc", title="Busy directors and firm performance",
             doi="10.1016/j.pacfin.2020.101434", year="2020",
             journal="Pacific-Basin Finance Journal")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2
    assert {r["doi"] for r in out} == {"10.1111/acfi.12631", "10.1016/j.pacfin.2020.101434"}


def test_prefix_rule_requires_shorter_title_at_least_20_chars():
    """A short common opening phrase on two otherwise-unrelated titles must
    not match just because one happens to start with the other's words."""
    a = _pub(name="Someone", title="An Audit Study", doi="10.1/aaa",
             year="2020", journal="Journal A")
    b = _pub(name="Someone", title="An Audit Study of Board Composition",
             doi=None, year="2020", journal="Journal A")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2


def test_prefix_rule_requires_a_word_boundary_not_a_mid_word_cut():
    """The shorter title must be a whole-word prefix of the longer one —
    "...corporate innovation" must not match "...corporate innovations"."""
    a = _pub(name="Someone", title="A Study of Corporate Innovation Outcomes",
             doi="10.1/aaa", year="2020", journal="Journal A")
    b = _pub(name="Someone", title="A Study of Corporate Innovation Outcomeses Elsewhere",
             doi=None, year="2020", journal="Journal A")
    assert exp._is_prefix_duplicate(a, b) is False


def test_prefix_rule_requires_same_year():
    """Confirmed real case: Sorin Daniliuc's 'Busy Directors and Firm
    Performance: a Replication and Extension of Hauser' (2020, no doi)
    prefix-matches his real, doi'd 'Busy directors and firm performance'
    row — but only the 2021 Accounting and Finance one, which is a
    DIFFERENT year. Must not merge — this is one of the 6 report-only
    year-off-by-one pairs, not a merge candidate."""
    a = _pub(name="Sorin Daniliuc", title="Busy directors and firm performance",
             doi="10.1111/acfi.12631", year="2021", journal="Accounting and Finance")
    b = _pub(name="Sorin Daniliuc",
             title="Busy Directors and Firm Performance: a Replication and Extension of Hauser",
             doi=None, year="2020", journal="Accounting and Finance")
    assert exp._is_prefix_duplicate(a, b) is False
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2


def test_prefix_rule_requires_same_journal():
    a = _pub(name="Someone", title="A Long Enough Title About Something Specific",
             doi="10.1/aaa", year="2020", journal="Journal A")
    b = _pub(name="Someone",
             title="A Long Enough Title About Something Specific: With a Subtitle",
             doi=None, year="2020", journal="Journal B")
    assert exp._is_prefix_duplicate(a, b) is False


def test_prefix_rule_skips_and_reports_when_neither_row_has_a_doi():
    a = _pub(name="Someone", title="A Long Enough Title About Something Specific",
             doi=None, year="2020", journal="Journal A")
    b = _pub(name="Someone",
             title="A Long Enough Title About Something Specific: With a Subtitle",
             doi=None, year="2020", journal="Journal A")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2
    assert len(exp.SKIPPED_PREFIX_DUPS) == 1


def test_prefix_rule_skips_and_reports_when_both_rows_have_a_doi():
    a = _pub(name="Someone", title="A Long Enough Title About Something Specific",
             doi="10.1/aaa", year="2020", journal="Journal A")
    b = _pub(name="Someone",
             title="A Long Enough Title About Something Specific: With a Subtitle",
             doi="10.1/bbb", year="2020", journal="Journal A")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2
    assert len(exp.SKIPPED_PREFIX_DUPS) == 1


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


# --------------------------------------------------------- FIX L bug: a
# correction notice is not a subtitle. Web of Science titles a PUBLISHED
# CORRECTION as the original article's own title plus a trailing
# "(vol N, pg N, YYYY)" locator — textually a strict prefix relationship,
# the exact shape FIX L/FIX K are built to merge, but the two rows are not
# the same independent publication: one of them is a correction notice for
# the other. Two confirmed real cases: Adelaide's Basil Tucker
# (10.1080/00014788.2013.798234 / .877214) and UNSW's Fariborz Moshirian
# (10.1016/s0378-4266(02)00467-3 / (03)00049-9).

def test_correction_notice_pair_is_not_treated_as_a_prefix_duplicate():
    """Basil Tucker shape: without the FIX L bug guard, the correction
    row's title is a textbook strict-prefix 'subtitle' of the original's,
    same year, same journal — _is_prefix_duplicate must refuse the pair
    outright rather than let the two get merged/preferred against each
    other."""
    original = _pub(
        name="Basil Tucker",
        title="In our ivory towers? The research-practice gap in management accounting",
        doi="10.1080/00014788.2013.798234", year="2014",
        journal="Accounting and Business Research",
    )
    correction = _pub(
        name="Basil Tucker",
        title=("In our ivory towers? The research-practice gap in management "
               "accounting (vol 44, pg 104, 2014)"),
        doi="10.1080/00014788.2013.877214", year="2014",
        journal="Accounting and Business Research",
    )
    assert exp._is_prefix_duplicate(original, correction) is False
    assert exp._is_exact_title_year_journal_dup(original, correction) is False


def test_correction_notice_row_is_excluded_from_export():
    """Fariborz Moshirian shape: the correction row must not survive into
    the exported table at all — the client's 9 Sep corrigenda/errata rule
    means it is dropped outright, not merged into (and definitely not
    preferred over) the genuine article's own row."""
    original = _pub(
        name="Fariborz Moshirian",
        title="Markets and institutions:: Global perspectives",
        doi="10.1016/s0378-4266(02)00467-3", year="2003",
        journal="Journal of Banking & Finance",
    )
    correction = _pub(
        name="Fariborz Moshirian",
        title="Markets and institutions:: Global perspectives (vol 27, pg 377, 2003)",
        doi="10.1016/s0378-4266(03)00049-9", year="2003",
        journal="Journal of Banking & Finance",
    )
    out = build_publications([original, correction], verbose=False)
    assert len(out) == 1
    assert out[0]["doi"] == "10.1016/s0378-4266(02)00467-3"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
