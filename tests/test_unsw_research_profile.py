"""ORCIDs from UNSW's own research profiles — CITS3200 Group 20.

    python -m pytest tests/test_unsw_research_profile.py -q

The fixtures are the real pages. Yan Xu and Yanbin Xu are two actual UNSW
academics whose slugs differ by three characters, and Richard Morris really
does have two profiles.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base_scrapers import unsw                                  # noqa: E402


def search_page(*hrefs):
    return "<html><body>" + "".join(
        f'<a href="{h}">x</a>' for h in hrefs) + "</body></html>"


PROFILE = """<html><body>
  <h1>Professor Michael Walpole</h1>
  <div>ORCID as entered in ROS</div>
  <a href="https://orcid.org/0000-0002-5755-2768">https://orcid.org/0000-0002-5755-2768</a>
</body></html>"""

NO_ORCID = "<html><body><h1>Someone</h1><div>Location Business</div></body></html>"


@pytest.fixture
def pages(monkeypatch):
    """Serve canned HTML instead of the network, keyed on URL."""
    store = {}

    def fake(session, url, refresh=False):
        for key, html in store.items():
            if key in url:
                return html
        return None

    monkeypatch.setattr(unsw, "_research_html", fake)
    return store


# ------------------------------------------------------- name/slug matching

def test_a_name_and_its_slug_agree_on_hyphens():
    """'Ann Kayis-Kumar' must line up with 'professor-ann-kayis-kumar'.

    An earlier version folded the name to 'kayiskumar' but left the slug
    hyphenated, so it never matched and she came back not found."""
    assert unsw._name_words("Ann Kayis-Kumar") == ["ann", "kayis", "kumar"]
    segs = unsw._slug_words("/people/professor-ann-kayis-kumar")
    assert all(w in segs for w in unsw._name_words("Ann Kayis-Kumar"))


def test_a_shorter_name_does_not_match_a_longer_one():
    """The real trap. 'yan' is a substring of 'yanbin', so substring matching
    puts Yan Xu on Yanbin Xu's profile and hands over a stranger's ORCID."""
    yan = unsw._name_words("Yan Xu")
    assert not all(w in unsw._slug_words("/people/dr-yanbin-xu") for w in yan)
    assert all(w in unsw._slug_words("/people/associate-professor-yan-xu") for w in yan)


def test_initials_and_single_letters_are_ignored():
    assert unsw._name_words("Su-Ming Wong") == ["su", "ming", "wong"]
    assert "a" not in unsw._name_words("A Smith")


def test_accents_are_formatting_not_identity():
    assert unsw._name_words("Luís Gonçalves-Pinto") == ["luis", "goncalves", "pinto"]


# ------------------------------------------------------------- finding one

def test_a_researcher_is_found_by_surname(pages):
    pages["key=walpole"] = search_page("/people/professor-michael-walpole",
                                       "/people/dr-someone-else")
    assert unsw.find_research_profile(None, "Michael Walpole") == \
        "https://research.unsw.edu.au/people/professor-michael-walpole"


def test_two_matching_profiles_return_nothing(pages):
    """Richard Morris has richard-morris and richard-morris-0. Picking one is
    a coin flip that attaches a real ORCID to possibly the wrong person."""
    pages["key=morris"] = search_page("/people/associate-professor-richard-morris",
                                      "/people/associate-professor-richard-morris-0")
    assert unsw.find_research_profile(None, "Richard Morris") is None


def test_no_match_returns_nothing(pages):
    pages["key=mackenzie"] = search_page("/people/conjoint-professor-tara-mackenzie")
    assert unsw.find_research_profile(None, "Gordon Mackenzie") is None


def test_an_unreachable_search_is_not_an_answer(pages):
    assert unsw.find_research_profile(None, "Anyone At All") is None


def test_query_strings_and_trailing_slashes_do_not_defeat_the_match(pages):
    pages["key=walpole"] = search_page("/people/professor-michael-walpole/?utm=x")
    assert unsw.find_research_profile(None, "Michael Walpole").endswith(
        "/people/professor-michael-walpole")


def test_a_sub_page_is_not_a_profile(pages):
    """'/people/professor-michael-walpole/publications' is a different page."""
    pages["key=walpole"] = search_page("/people/professor-michael-walpole/publications")
    assert unsw.find_research_profile(None, "Michael Walpole") is None


# ------------------------------------------------------------ reading one

def test_the_orcid_is_taken_off_the_profile(pages):
    pages["key=walpole"] = search_page("/people/professor-michael-walpole")
    pages["/people/professor-michael-walpole"] = PROFILE
    records = [{"name_clean": "Michael Walpole"}]
    assert unsw.add_research_profile_orcids(None, records, verbose=False) == 1
    assert records[0]["orcid"] == "0000-0002-5755-2768"
    assert records[0]["orcid_source"] == "research.unsw.edu.au"


def test_a_profile_with_no_orcid_leaves_the_field_alone(pages):
    """Not everyone has one entered in ROS. That is an answer, and OpenAlex
    gets asked instead; it must not be recorded as an ORCID."""
    pages["key=lee"] = search_page("/people/dr-suk-lee")
    pages["/people/dr-suk-lee"] = NO_ORCID
    records = [{"name_clean": "Suk Lee"}]
    assert unsw.add_research_profile_orcids(None, records, verbose=False) == 0
    assert not records[0].get("orcid")


def test_an_orcid_we_already_hold_is_not_overwritten(pages):
    records = [{"name_clean": "Michael Walpole", "orcid": "0000-0000-0000-0001"}]
    unsw.add_research_profile_orcids(None, records, verbose=False)
    assert records[0]["orcid"] == "0000-0000-0000-0001"


def test_the_checksum_x_is_a_valid_final_character(pages):
    pages["key=smith"] = search_page("/people/dr-jane-smith")
    pages["/people/dr-jane-smith"] = (
        '<a href="https://orcid.org/0000-0002-1825-009X">id</a>')
    records = [{"name_clean": "Jane Smith"}]
    unsw.add_research_profile_orcids(None, records, verbose=False)
    assert records[0]["orcid"] == "0000-0002-1825-009X"


# ------------------------------------------- what it saves downstream

def test_a_researcher_with_an_orcid_costs_no_openalex_search(monkeypatch):
    """The point of the whole exercise. The OpenAlex author search is the only
    metered call in this pipeline and the only one that can return the wrong
    person, so anyone already answered must not reach it."""
    def explode(*a, **kw):
        raise AssertionError("add_openalex_ids searched for someone who "
                             "already had an ORCID")

    monkeypatch.setattr(unsw, "cached_get", explode)
    records = [{"name_clean": "Michael Walpole", "orcid": "0000-0002-5755-2768"}]
    unsw.add_openalex_ids(records, verbose=False)
    assert records[0]["orcid"] == "0000-0002-5755-2768"
    assert records[0]["openalex_author_ids"] == []


def test_a_researcher_without_one_still_reaches_openalex(monkeypatch):
    called = []

    def fake(url, **kw):
        called.append(kw.get("params", {}).get("search"))
        return {"results": []}

    monkeypatch.setattr(unsw, "cached_get", fake)
    unsw.add_openalex_ids([{"name_clean": "Gordon Mackenzie"}], verbose=False)
    assert called == ["Gordon Mackenzie"]


# ----------------------------------------------- completeness against UNSW

COUNTS_HTML = """<ul>
 <li><a href="/people/x/publications?type=bookchapters">Book Chapters
     <span class="badge">11</span></a></li>
 <li><a href="/people/x/publications?type=journalarticles">Journal articles
     <span class="badge">130</span></a></li>
 <li><a href="/people/x/publications?type=media">Media
     <span class="badge">1</span></a></li>
 <li><a href="/people/x/publications?type=other">Other
     <span class="badge">24</span></a></li>
</ul>"""


def article(name, n, kind="Journal Article"):
    return [{"name": name, "type": kind} for _ in range(n)]


def test_the_counts_are_read_off_the_profile():
    assert unsw.profile_counts(COUNTS_HTML) == {
        "Book Chapter": 11, "Journal Article": 130,
        "Other": 25,                      # Media 1 + Other 24
    }


def test_labels_that_share_a_type_are_summed_not_overwritten():
    """UNSW has eight labels that all become 'Other'. Assigning rather than
    summing would report the last one seen and hide the rest."""
    assert unsw.profile_counts(COUNTS_HTML)["Other"] == 25


def test_a_page_with_no_counts_block_gives_nothing():
    assert unsw.profile_counts("<html><body>no publications here</body></html>") == {}


def test_a_label_we_do_not_map_is_ignored_rather_than_guessed():
    html = ('<a href="/people/x/publications?type=somethingnew">Something New '
            '<span class="badge">5</span></a>')
    assert unsw.profile_counts(html) == {}


def test_a_shortfall_is_reported():
    """The point of the check: we parsed 40 journal articles, UNSW says 130."""
    records = [{"name_clean": "Dale Boccabella",
                "profile_counts": {"Journal Article": 130}}]
    short = unsw.report_completeness(records, article("Dale Boccabella", 40),
                                     verbose=False)
    assert short == [("Dale Boccabella", "Journal Article", 40, 130)]


def test_a_small_difference_is_not_reported():
    """The two systems are updated separately, so one or two either way is
    normal and reporting it would bury a real gap in noise."""
    records = [{"name_clean": "X", "profile_counts": {"Journal Article": 40}}]
    assert unsw.report_completeness(records, article("X", 39), verbose=False) == []


def test_having_more_than_unsw_claims_is_not_a_shortfall():
    """The staff page sometimes lists work ROS has not caught up with. That is
    not data loss and must not be flagged."""
    records = [{"name_clean": "X", "profile_counts": {"Journal Article": 10}}]
    assert unsw.report_completeness(records, article("X", 25), verbose=False) == []


def test_a_researcher_with_no_matched_profile_is_skipped():
    """No second opinion is available, which is not the same as a gap."""
    records = [{"name_clean": "Gordon Mackenzie"}]
    assert unsw.report_completeness(records, [], verbose=False) == []


def test_each_type_is_compared_separately():
    """A scrape that got the journal articles but missed the book chapters
    must not be hidden by the totals happening to look close."""
    records = [{"name_clean": "X", "profile_counts": {"Journal Article": 30,
                                                      "Book Chapter": 20}}]
    short = unsw.report_completeness(
        records, article("X", 30) + article("X", 1, "Book Chapter"),
        verbose=False)
    assert short == [("X", "Book Chapter", 1, 20)]


def test_the_check_changes_nothing():
    """It is a second opinion, not an authority. The staff page is the source
    we are entitled to; this only reports."""
    records = [{"name_clean": "X", "profile_counts": {"Journal Article": 99}}]
    pubs = article("X", 3)
    unsw.report_completeness(records, pubs, verbose=False)
    assert len(pubs) == 3
    assert records[0] == {"name_clean": "X",
                          "profile_counts": {"Journal Article": 99}}
