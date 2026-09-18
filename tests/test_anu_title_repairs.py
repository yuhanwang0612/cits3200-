"""Offline tests for FIX I/J/K — the ANU-only fixes in
scratch/_anu17/REPORT.md (task 2 title repairs, task 2e prose exclusion,
task 4 SSRN working-paper exclusion, task 5 job-title-from-level fallback).

Fully offline: exercises the pure helper functions directly rather than
scraping a live page.
"""

from base_scrapers import anu
from core.titles import rank_from_level
from export import _is_anu_unranked_ssrn_preprint


# --- task 2: title repairs -------------------------------------------------

def test_strips_trailing_best_paper_award_clause():
    title = ("Shareholder election of CSR committee members and its effects "
             "on CSR performance. Best Paper Award at the 2021 AFAANZ "
             "annual conference")
    assert anu._repair_title(title) == (
        "Shareholder election of CSR committee members and its effects "
        "on CSR performance"
    )


def test_strips_trailing_manuscript_award_clause():
    title = ("Do local social norms affect investors’ involvement in "
             "social activism? Revisiting the case of US institutional "
             "investors. 2021 Peter Brownell Manuscript Award")
    assert anu._repair_title(title) == (
        "Do local social norms affect investors’ involvement in "
        "social activism? Revisiting the case of US institutional investors"
    )


def test_does_not_strip_award_winning_used_as_a_live_adjective():
    # Louise Lu / Kathy Wang's genuine title — no sentence boundary before
    # "award", so the award-suffix rule must never touch it.
    title = ("External labor market competitions and stock price crash "
             "risk: Evidence from exposures to competitor CEOs' "
             "award-winning events")
    assert anu._repair_title(title) == title


def test_strips_trailing_coauthor_clause():
    title = "The Decline of Too Big To Fail with D. Duffie and Y. Zhu"
    assert anu._repair_title(title) == "The Decline of Too Big To Fail"


def test_recovers_title_from_citation_string():
    title = ("Ho, S, Choi, S., and Yang, F. (forthcoming) 'Harnessing the "
             "Power of Twitter: How are Tweets Associated with Forecast "
             "Accuracy?")
    assert anu._repair_title(title) == (
        "Harnessing the Power of Twitter: How are Tweets Associated with "
        "Forecast Accuracy?"
    )


def test_recovers_title_from_mangled_book_review_citation():
    title = ("Auditing and Assurance Services and Ethics in Australia: An "
             "Integrated Approach20111Alvin A. Arens, Peter Best, Greg "
             "Shailer, and Brenton Fiedlerd. Auditing and Assurance "
             "Services and Ethics in Australia: An Integrated Approach. "
             "2009. 8th ed.")
    assert anu._repair_title(title) == (
        "Auditing and Assurance Services and Ethics in Australia: An "
        "Integrated Approach"
    )


def test_digit_run_without_a_repeated_prefix_is_left_alone():
    # Same digit-run shape, but the "title" before it never reappears
    # later in the string — not enough confidence to recover anything.
    title = "Some Title 20231Smith, J. and a completely different paper"
    assert anu._repair_title(title) == title


def test_ordinary_title_is_unchanged():
    title = "Non-financial performance: Are your non-financial KPIs useful?"
    assert anu._repair_title(title) == title


# --- task 2e: prose mistaken for a title ------------------------------------

def test_prose_title_with_prose_journal_is_detected():
    pub = {
        "title": ("This research monograph develops an overarching "
                   "framework to resurrect the classical notion of "
                   "division of labour and specialization which is the "
                   "essential source of increasing nation’s wealth"),
        "journal": ("The new framework has many conceptual and policy "
                     "implications different from those of orthodox analysis"),
        "doi": None, "year": None,
    }
    assert anu._is_prose_not_title(pub) is True


def test_long_real_title_with_long_real_special_issue_journal_is_not_flagged():
    # Found on a live re-scrape (scratch/_anu17/REPORT.md): a genuine Alex
    # Wang title/journal pair that is long on both sides but is not prose —
    # word count alone wrongly caught this one; the doi/year-blank check is
    # what correctly keeps it.
    pub = {
        "title": ("Strategizing in the Midst of Management Controls: A "
                   "Longitudinal Case Study on The Relationship between "
                   "Management Controls and Promises on Strategies"),
        "journal": ("Accounting and Finance, A Special Issue for "
                     "Qualitative Accounting Research"),
        "doi": None, "year": "2019",
    }
    assert anu._is_prose_not_title(pub) is False


def test_long_title_with_ordinary_short_journal_is_not_flagged():
    pub = {
        "title": ("Body Mass Index More Than 45 kg/m2 as a Cutoff Point Is "
                   "Associated With Dramatically Increased Postoperative "
                   "Complications in Total Knee Arthroplasty and Total Hip "
                   "Arthroplasty"),
        "journal": "The Journal of Arthroplasty",
    }
    assert anu._is_prose_not_title(pub) is False


def test_ordinary_title_with_long_but_real_journal_description_is_not_flagged():
    pub = {
        "title": "Non-financial performance: Are your non-financial KPIs useful?",
        "journal": ("Research report from grant received from the "
                     "Institute of Chartered Accountants of Scotland"),
    }
    assert anu._is_prose_not_title(pub) is False


# --- scratch/_anu18 FIX 1: FIX I also runs at export time, for a row from a
# non-page source (ORCID/Crossref/OpenAlex) that carries the same corrupted
# title shape in its own metadata --------------------------------------------

def test_mangled_review_title_is_repaired_even_from_an_orcid_sourced_row():
    """Greg Shailer's mangled book-review title survives under
    source == "ORCID" with a real doi/journal/year already attached
    (10.1108/18325911111182330, Journal of Accounting & Organizational
    Change, 2011) — base_scrapers.anu._map_publication (FIX I's only other
    call site) never sees this row at all, since it wasn't scraped off the
    ANU page. build_publications must repair it too."""
    from core.schema import blank_pub
    from export import build_publications

    row = blank_pub(
        name="Greg Shailer", type="Journal Article", source="ORCID",
        title=("Auditing and Assurance Services and Ethics in Australia: "
               "An Integrated Approach20111Alvin A. Arens, Peter Best, Greg "
               "Shailer, and Brenton Fiedlerd. Auditing and Assurance "
               "Services and Ethics in Australia: An Integrated Approach. "
               "2009. 8th ed."),
        journal="Journal of Accounting & Organizational Change", year="2011",
        doi="10.1108/18325911111182330",
    )
    records = [{"name_clean": "Greg Shailer",
                "university": "Australian National University"}]
    out = build_publications([row], records=records, verbose=False)
    assert len(out) == 1
    assert out[0]["title"] == (
        "Auditing and Assurance Services and Ethics in Australia: An "
        "Integrated Approach"
    )
    # everything else about the row is untouched
    assert out[0]["doi"] == "10.1108/18325911111182330"
    assert out[0]["year"] == "2011"
    assert out[0]["journal_name"] == "Journal of Accounting & Organizational Change"


def test_export_time_repair_never_touches_a_non_anu_researchers_title():
    from core.schema import blank_pub
    from export import build_publications

    row = blank_pub(
        name="Someone Else", type="Journal Article", source="ORCID",
        title="A Real Title20111Some Author, Another Author. A Real Title. 2020.",
        journal="Some Journal", year="2020", doi="10.1/xyz",
    )
    records = [{"name_clean": "Someone Else", "university": "Some Other University"}]
    out = build_publications([row], records=records, verbose=False)
    assert out[0]["title"] == row["title"]


# --- task 4: SSRN working-paper exclusion (export.py, ANU-scoped) ---------

ANU_NAMES = {"Greg Shailer", "Kathy Wang"}


def test_ssrn_doi_with_no_journal_and_no_abdc_rank_is_excluded():
    # An ORCID-retrieved copy, not a page-scraped one — source is "ORCID",
    # not "ANU staff profile" (the shape all 4 real target rows have).
    row = {"name": "Greg Shailer", "source": "ORCID",
           "doi": "10.2139/ssrn.2209508", "journal": None, "abdc": None}
    assert _is_anu_unranked_ssrn_preprint(row, ANU_NAMES) is True


def test_ssrn_doi_published_in_a_real_ranked_journal_is_kept():
    row = {"name": "Kathy Wang", "source": "ANU staff profile",
           "doi": "10.2139/ssrn.4602184",
           "journal": "The European Accounting Review", "abdc": "A*"}
    assert _is_anu_unranked_ssrn_preprint(row, ANU_NAMES) is False


def test_non_ssrn_doi_is_never_excluded_by_this_rule():
    row = {"name": "Greg Shailer", "source": "ANU staff profile",
           "doi": "10.1257/aer.20220846", "journal": None, "abdc": None}
    assert _is_anu_unranked_ssrn_preprint(row, ANU_NAMES) is False


def test_rule_is_scoped_to_anu_and_never_touches_another_university():
    # Same shape as a real exclusion, but the name isn't in the ANU staff
    # set built from this run's own `records` — must never fire.
    row = {"name": "Someone Else", "source": "ORCID",
           "doi": "10.2139/ssrn.2209508", "journal": None, "abdc": None}
    assert _is_anu_unranked_ssrn_preprint(row, ANU_NAMES) is False


# --- task 5: job title from level, when the page only shows a role subtitle

def test_rank_from_level_covers_the_ladder():
    assert rank_from_level("D") == "Associate Professor"
    assert rank_from_level("E") == "Professor"
    assert rank_from_level("Z") is None


def test_staff_record_fills_job_title_from_level_when_subtitle_is_a_role():
    import anu_scraper

    r = anu_scraper.Researcher(
        name="Keturah Whitford", job_title="Reader", academic_level="D",
        field_of_research="Accounting",
        profile_url="https://rsa.anu.edu.au/people/keturah-whitford",
    )
    rec = anu._staff_record(r, identity_by_name={})
    assert rec["title_clean"] == "Associate Professor"
    assert rec["level_code"] == "D"


def test_staff_record_does_not_override_a_real_rank_word():
    import anu_scraper

    r = anu_scraper.Researcher(
        name="Sarah Adams", job_title="Senior Lecturer", academic_level="C",
        field_of_research="Accounting",
        profile_url="https://rsa.anu.edu.au/people/sarah-adams",
    )
    rec = anu._staff_record(r, identity_by_name={})
    assert rec["title_clean"] == "Senior Lecturer"
