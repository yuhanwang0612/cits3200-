"""Offline tests for the v25 ANU journal-name fixes in anu_scraper.py:
a journal name cut short at a comma or at the end of a partial italic run
is completed only to an exact ABDC title, and a "Special Issue" label is
dropped from the journal name. Synthetic fixtures copy the real shapes
found on Raymond Liu's, Alex Wang's and Greg Shailer's live pages (see
docs/DECISIONS.md, 28 Sep 2026).

    python -m pytest tests/test_anu_journal_names.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anu_scraper  # noqa: E402


def _researcher(name="Wai-Man (Raymond) Liu"):
    return anu_scraper.Researcher(
        name=name, job_title="Professor", academic_level="E",
        field_of_research="Finance",
        profile_url="https://rsa.anu.edu.au/people/x",
    )


def _parse(text, italics=None, name="Wai-Man (Raymond) Liu"):
    block = {"text": text, "links": [], "italics": italics or [], "section": None}
    return anu_scraper.parse_publication(block, _researcher(name))


# ------------------------------------------------ comma inside a journal name

def test_journal_name_containing_a_comma_is_not_cut_at_the_comma():
    pub, ok = _parse("19. Liu, W.-M. , Yu, J. & Zhang, B. (2022) Foreign Investment "
                     "under the Spotlight of Home Media. Journal of Money, Credit & Banking.")
    assert ok
    assert pub.title == "Foreign Investment under the Spotlight of Home Media"
    assert pub.journal_name == "Journal of Money, Credit & Banking"
    assert pub.year == 2022


def test_partial_italic_run_is_completed_to_the_abdc_title():
    """The page italicises only 'Annals of Operations'; 'Research' follows
    as plain text."""
    text = ("14. Le, A. T., Le, T.-H., Liu, W.-M. & Fong, K., Y. (2019) Dynamic limit "
            "order placement strategies: Survival analysis with a multiple-spell "
            "duration model. Annals of Operations Research, forthcoming.")
    pub, ok = _parse(text, italics=["Annals of Operations"])
    assert ok
    assert pub.journal_name == "Annals of Operations Research"


def test_known_journal_is_never_extended():
    pub, _ = _parse("Smith, J. (2020) A long enough article title here. "
                    "Accounting and Finance, Finance Research Letters.")
    assert pub.journal_name == "Accounting and Finance"


def test_no_abdc_match_leaves_the_parsed_name_alone():
    """No exact ABDC title reachable: nothing is guessed."""
    assert anu_scraper._complete_journal_name(
        "Journal of Things", "Paper title. Journal of Things, Stuff & Widgets.",
    ) == "Journal of Things"


def test_fragment_occurring_twice_is_not_extended():
    """Greg Shailer shape: the parsed 'journal' ('Australian') also occurs
    inside the title, so it is a title/journal mis-split, not a truncated
    name, and must stay as it was."""
    text = ("Review of Post-CLERP 9 Australian Auditor Independence Research . "
            "Australian Accounting Review , 24(4) 2014: 370-380.")
    assert anu_scraper._complete_journal_name("Australian", text) == "Australian"


def test_extension_stops_at_the_next_full_stop():
    assert anu_scraper._complete_journal_name(
        "Annals of Operations", "Title. Annals of Operations. Research notes.",
    ) == "Annals of Operations"


# ------------------------------------------------------ special-issue tails

def test_special_issue_label_is_dropped_from_journal():
    text = ("Strategizing in the Midst of Management Controls: A Longitudinal Case Study "
            "on The Relationship between Management Controls and Promises on Strategies "
            "(2019), Accounting and Finance, A Special Issue for Qualitative Accounting "
            "Research. (with Tekathen, M. and Bui, B.) (ABDC – A)")
    pub, ok = _parse(
        text,
        italics=["Accounting and Finance, A Special Issue for Qualitative Accounting Research."],
        name="Alex Wang",
    )
    assert ok
    assert pub.journal_name == "Accounting and Finance"
    assert pub.year == 2019


def test_special_issue_on_tail_after_dash_is_dropped():
    assert anu_scraper._strip_journal_junk(
        "Journal of Banking and Finance - Special Issue on Fintech"
    ) == "Journal of Banking and Finance"


def test_name_starting_with_special_issue_is_not_emptied():
    assert anu_scraper._strip_journal_junk("Special Issues in Accounting") == "Special Issues in Accounting"
