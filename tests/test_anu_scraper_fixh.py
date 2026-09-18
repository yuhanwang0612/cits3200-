"""Offline tests for FIX H — anu_scraper.py's author-list-as-title fix.
Synthetic fixtures copy the real shapes found on Rebecca Tan's and Tracy
(Kun) Wang's live pages (see docs/DECISIONS.md, 15 Sep 2026 FIX G/H
addendum) rather than reusing the exact live text verbatim.

    python -m pytest tests/test_anu_scraper_fixh.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anu_scraper  # noqa: E402


def _researcher(name="Tracy (Kun) Wang"):
    return anu_scraper.Researcher(
        name=name, job_title="Lecturer", academic_level="B",
        field_of_research="Finance",
        profile_url="https://rsa.anu.edu.au/people/x",
    )


def _parse(text, italics=None, name="Tracy (Kun) Wang"):
    block = {"text": text, "links": [], "italics": italics or [], "section": None}
    return anu_scraper.parse_publication(block, _researcher(name))


# ------------------------------------------------------- textbook / book cases

def test_pure_author_list_book_citation_is_unparsed():
    """Rebecca Tan shape: author list, parenthesised year already consumed
    by the existing year-boundary split, italic run is the BOOK title, not
    a journal — must not be guessed at as a journal article."""
    text = ("Horngren, C., Wynder, M., Maguire, W., Tan, R., Datar, S., "
            "Foster, G., Rajan, M. and Ittner, C. (2018). Cost Accounting: "
            "A Managerial Emphasis, 3rd Edition, Pearson Education "
            "Australia, Sydney NSW (ISBN 9781488612640).")
    pub, confident = _parse(text, italics=["Cost Accounting: A Managerial Emphasis"],
                             name="Rebecca Tan")
    assert confident is False
    assert pub.title is None


def test_author_list_with_et_al_book_citation_is_unparsed():
    text = ("Horngren, C., Best, P., Fraser, D., Tan, R., Willett, R. et. al. "
            "(2016). Horngren's Accounting, 8th Edition, Pearson Education "
            "Australia, Sydney NSW.")
    pub, confident = _parse(text, italics=["Horngren's Accounting"], name="Rebecca Tan")
    assert confident is False
    assert pub.title is None


# ----------------------------------------------------- author-list + year + title

def test_author_list_year_title_fixes_the_wrong_year_bug():
    """The exact 1942 bug: 'Wang, K.T., & Wu, Y.** 2024 Corporate social
    responsibility...' with a page-range '1893-1942' further along in the
    citation that must NOT be mistaken for the year now that the real year
    (2024) is confidently read straight off the author-list prefix."""
    text = ("Wang, K.T., & Wu, Y.** 2024 Corporate social responsibility "
            "reporting and investment: Evidence from mergers and "
            "acquisitions. Journal of Business Finance & Accounting, "
            "51 (7-8), 1893-1942. (ABDC: A*, Impact Factor: 2.2).")
    pub, confident = _parse(text, italics=["Journal of Business Finance & Accounting"])
    assert confident is True
    assert pub.year == 2024
    assert pub.title == ("Corporate social responsibility reporting and "
                          "investment: Evidence from mergers and acquisitions")


def test_author_list_year_lowercase_title_is_not_guessed():
    """The page itself writes the real title in lower case prose style —
    refuse rather than capitalise/guess."""
    text = ("Tsang, A., Wang, K.T., Wu, Y.**, & Lee, J.*** 2024. nonfinancial "
            "corporate social responsibility reporting and firm value: "
            "international evidence on the role of financial analysts. "
            "European Accounting Review (ABDC: A*, Impact Factor: 2.845)")
    pub, confident = _parse(text, italics=["European Accounting Review"])
    assert confident is False
    assert pub.title is None


def test_author_list_year_strips_leading_parenthetical_clause():
    """'2022 (First online 2 January 2021), Academy fellow...' — the
    parenthetical is citation metadata glued onto the front of the real
    title, not the title itself."""
    text = ("Li, S., Quan Y., Tian, G.G., Wang, K.T., & Wu, H. 2022 "
            "(First online 2 January 2021), Academy fellow independent "
            "directors and innovation, Asia Pacific Journal of Management, "
            "39, 103-148 (ABDC: A, Impact Factor: 4.9).")
    pub, confident = _parse(text, italics=["Asia Pacific Journal of Management"])
    assert confident is True
    assert pub.year == 2022
    assert pub.title == "Academy fellow independent directors and innovation"


# ---------------------------------------------------------------- year sanity

def test_implausible_year_below_1950_is_blanked():
    """YEAR_RE/AUTHOR_LIST_YEAR_PREFIX_RE only ever match a 19xx/20xx
    4-digit run, so the lowest reachable implausible year is in the
    1900s — 1923 here, well before any real ANU accounting/finance
    citation could genuinely be dated."""
    m = anu_scraper.AUTHOR_LIST_YEAR_PREFIX_RE.match(
        "Smith, J., & Jones, A. 1923 A very old but real accounting study")
    assert m is not None  # sanity: the prefix pattern itself does match
    text = ("Smith, J., & Jones, A. 1923 A very old but real accounting "
            "study of something genuinely interesting here. Accounting "
            "Review (ABDC: A).")
    pub, confident = _parse(text, italics=["Accounting Review"])
    assert pub.year is None


def test_implausible_future_year_is_blanked():
    text = ("Smith, J., & Jones, A. 2099 A confident but impossible future "
            "accounting study of something. Accounting Review (ABDC: A).")
    pub, confident = _parse(text, italics=["Accounting Review"])
    assert pub.year is None


# --------------------------------------------------------------- regex-only

def test_full_author_list_regex_matches_et_al_variant():
    assert anu_scraper.FULL_AUTHOR_LIST_RE.match(
        "Horngren, C., Best, P., Fraser, D., Tan, R., Willett, R. et. al")


def test_full_author_list_regex_does_not_match_real_title():
    assert anu_scraper.FULL_AUTHOR_LIST_RE.match(
        "Analyst expertise and the value of cash holdings") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
