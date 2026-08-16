"""
Tests for the UNSW scraper — CITS3200 Group 20.

These run entirely offline against small HTML fixtures defined in this file.
Nothing here touches unsw.edu.au, so the suite is fast, repeatable, and safe to
run in CI or on a plane.

The fixtures are trimmed copies of markup taken from real UNSW profile pages.
Where a test pins an odd-looking rule, the docstring says which real record it
came from — those are the cases that were actually wrong at some point, and the
test exists so they do not quietly come back.

Run:  python -m pytest test_unsw_scraper.py -v
"""

import pytest
from bs4 import BeautifulSoup

import unsw_scraper as s


PERSON = {
    "name": "Test Person",
    "profile_url": "https://www.unsw.edu.au/staff/test-person",
    "field_of_research": "Finance",
}


def parse(html):
    """Wrap fixture markup and run the publication parser over it."""
    soup = BeautifulSoup(html, "html.parser")
    return s.parse_publications(soup, PERSON)


def item(category="Journal articles", year="2026", author="Li H;  Masulis R",
         title="'A title'", source="Journal of Corporate Finance",
         volume=None, page=None, href=None, extra=""):
    """Build one .publication-item the way UNSW's research gateway renders it."""
    parts = [f'<span class="publication-category">{category}</span>']
    if year:
        parts.append(f'<span class="publication-year">{year}</span>')
        parts.append(f'<span class="rg-year">{year}</span>')
    if author:
        parts.append(f'<span class="rg-author">{author}</span>')
    if title:
        parts.append(f'<span class="rg-title">{title}</span>')
    if source:
        parts.append(f'<i class="rg-source-title">{source}</i>')
    if volume:
        parts.append(f'<span class="rg-volume">{volume}</span>')
    if page:
        parts.append(f'<span class="rg-page">{page}</span>')
    if href:
        parts.append(f'<a href="{href}">link</a>')
    return f'<div class="publication-item">{"".join(parts)}{extra}</div>'


# ---------------------------------------------------------------------------
# Academic level
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("job_title,expected_level", [
    ("Emeritus Professor", "E"),
    ("Scientia Professor", "E"),
    ("Professor", "E"),
    ("Associate Professor", "D"),
    ("Senior Lecturer", "C"),
    ("Lecturer", "B"),
    ("Associate Lecturer", "A"),
])
def test_ladder_maps_titles_to_levels(job_title, expected_level):
    assert s.academic_level(job_title)[1] == expected_level


def test_associate_professor_is_not_read_as_associate_lecturer():
    """Ordering in LADDER matters — 'Associate Professor' must win over the
    later 'Associate Lecturer' and 'Professor' entries."""
    assert s.academic_level("Associate Professor") == ("Associate Professor", "D")


def test_administrative_title_falls_back_to_the_name_prefix():
    """Real record: Francisco Barillas Bedoya's job_title is 'Head of School',
    which is not a rank at all. The level has to come from 'Professor …' in his
    name, otherwise three of UNSW's most senior people score blank."""
    assert s.academic_level("Head of School", "Professor ")[1] == "E"
    assert s.academic_level("Deputy Head of School - Research", "Professor ")[1] == "E"


def test_unknown_title_is_left_blank_rather_than_guessed():
    assert s.academic_level("Chief Operating Officer") == (None, None)
    assert s.academic_level(None, None) == (None, None)


def test_education_focused_roles_are_marked_excluded():
    """FR4: education- and teaching-focused roles are out of the rankings."""
    assert s.academic_level("Senior Lecturer (Education Focussed)")[0] == "Exclude"
    assert s.academic_level("Teaching-Focused Lecturer")[0] == "Exclude"


# ---------------------------------------------------------------------------
# Name prefixes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Dr Nicole Ang", "Nicole Ang"),
    ("Professor Wei Chen", "Wei Chen"),
    ("Associate Professor Jeff Coulton", "Jeff Coulton"),
    ("Emeritus Professor Christopher Adam", "Christopher Adam"),
    ("Scientia Professor Manju Ahuja", "Manju Ahuja"),
    # Honorifics stack. This one was shipping as a name until it was caught.
    ("Emeritus Scientia Professor Roger Simnett", "Roger Simnett"),
    ("Adjunct Associate Professor Jane Doe", "Jane Doe"),
    # No prefix at all — must be left alone.
    ("Fahim Khondaker", "Fahim Khondaker"),
])
def test_prefix_is_stripped_from_names(raw, expected):
    assert s.PREFIX.sub("", raw).strip() == expected


def test_stacked_prefix_still_yields_a_level():
    """Stripping must not destroy the level fallback: the whole prefix is what
    gets handed to academic_level()."""
    match = s.PREFIX.match("Emeritus Scientia Professor Roger Simnett")
    assert s.academic_level(None, match.group(0))[1] == "E"


# ---------------------------------------------------------------------------
# Publication parsing
# ---------------------------------------------------------------------------
def test_structured_entry_maps_to_the_right_columns():
    pubs, unparsed = parse(item(
        title="'Does common ownership raise antitrust concerns?'",
        author="Li H;  Liu L;  Masulis R;  Zein J",
        source="Journal of Corporate Finance",
        volume="100", page="pp. 131 - 167",
        href="http://dx.doi.org/10.1016/j.jcorpfin.2026.103037"))
    assert unparsed == []
    assert len(pubs) == 1
    p = pubs[0]
    assert p["title"] == "Does common ownership raise antitrust concerns?"
    assert p["journal_name"] == "Journal of Corporate Finance"
    assert p["year"] == "2026"
    assert p["publication_type"] == "Journal articles"
    assert p["doi"] == "10.1016/j.jcorpfin.2026.103037"
    assert p["coauthors"] == "Li H; Liu L; Masulis R; Zein J"
    assert p["volume"] == "100"
    assert p["pages"] == "pp. 131 - 167"
    assert p["university"] == s.UNIVERSITY
    assert p["researcher_name"] == "Test Person"
    # Filled downstream, not here — but the columns must exist.
    assert p["abdc_self_reported"] is None
    assert p["citation_percentile"] is None


def test_title_quotes_are_stripped():
    assert s._clean_title("'Some title'") == "Some title"
    assert s._clean_title('"Some title"') == "Some title"
    assert s._clean_title("Some title") == "Some title"
    assert s._clean_title(None) is None


def test_authors_split_on_semicolons_and_lose_double_spaces():
    assert s._split_authors("Li H;  Liu L;  Masulis R") == "Li H; Liu L; Masulis R"
    assert s._split_authors(None) is None


def test_bare_dx_doi_link_is_discarded():
    """Real problem on UNSW's side: ~20 entries link to 'http://dx.doi.org'
    with no identifier after it. That resolves nowhere, so it must not be
    written out as an article_url."""
    pubs, _ = parse(item(href="http://dx.doi.org"))
    assert pubs[0]["doi"] is None
    assert pubs[0]["article_url"] is None


def test_doi_link_is_preferred_over_other_links():
    html = item(href="https://example.com/paper.pdf",
                extra='<a href="http://dx.doi.org/10.1111/jofi.12345">doi</a>')
    pubs, _ = parse(html)
    assert pubs[0]["doi"] == "10.1111/jofi.12345"
    assert "doi.org" in pubs[0]["article_url"]


def test_non_doi_link_is_kept_when_there_is_no_doi():
    pubs, _ = parse(item(href="https://www.unsw.edu.au/content/paper.pdf"))
    assert pubs[0]["doi"] is None
    assert pubs[0]["article_url"] == "https://www.unsw.edu.au/content/paper.pdf"


def test_entry_with_no_year_is_kept_with_a_blank_year():
    """SSRN preprints often carry no date on the page. The year is left blank
    rather than inferred from the DOI."""
    pubs, _ = parse(item(year=None, source=None,
                         href="http://dx.doi.org/10.2139/ssrn.3171271"))
    assert len(pubs) == 1
    assert pubs[0]["year"] is None
    assert pubs[0]["doi"] == "10.2139/ssrn.3171271"


# ---------------------------------------------------------------------------
# Deduplication — the subtle part
# ---------------------------------------------------------------------------
def test_same_paper_under_two_dois_is_counted_once():
    """Real record: Peter Swan's 1986 Journal of Finance paper is listed twice,
    once with its JSTOR DOI and once with its Wiley DOI. Counting it twice
    inflates the productivity measure, so the DOI is NOT part of the identity."""
    html = (item(title="'Equilibrium Interest Rates'", year="1986",
                 source="The Journal of Finance",
                 href="http://dx.doi.org/10.2307/2328441")
            + item(title="'Equilibrium Interest Rates'", year="1986",
                   source="The Journal of Finance",
                   href="http://dx.doi.org/10.1111/j.1540-6261.1986.tb05042.x"))
    pubs, _ = parse(html)
    assert len(pubs) == 1


def test_the_surviving_copy_keeps_a_doi():
    """If the first copy has no DOI and a later duplicate does, the DOI is
    carried across rather than lost."""
    html = (item(title="'A paper'", year="2020", source="Journal of Finance")
            + item(title="'A paper'", year="2020", source="Journal of Finance",
                   href="http://dx.doi.org/10.1111/jofi.99999"))
    pubs, _ = parse(html)
    assert len(pubs) == 1
    assert pubs[0]["doi"] == "10.1111/jofi.99999"


def test_same_title_different_type_and_year_is_two_records():
    """Real record: one of Ann Kayis-Kumar's titles appears as a 2015
    conference paper and again as a 2019 book. Those are two different
    outputs, so deduplicating on title alone would undercount her."""
    html = (item(title="'Taxing Multinationals'", year="2015",
                 category="Conference Papers", source=None)
            + item(title="'Taxing Multinationals'", year="2019",
                   category="Books", source=None))
    pubs, _ = parse(html)
    assert len(pubs) == 2


def test_title_case_differences_do_not_create_duplicates():
    html = (item(title="'A Paper'", year="2020", source="Journal of Finance")
            + item(title="'a paper'", year="2020", source="Journal of Finance"))
    pubs, _ = parse(html)
    assert len(pubs) == 1


# ---------------------------------------------------------------------------
# Things that must not be guessed at
# ---------------------------------------------------------------------------
def test_free_text_entry_goes_to_unparsed_not_to_publications():
    """A minority of entries are a bare paragraph with no spans. Guessing at
    the journal would be worse than logging it, so it is logged."""
    html = ('<div class="publication-item">'
            '<span class="publication-category">Journal articles</span>'
            '<span class="publication-year">2025</span>'
            '<p>Masulis, R., S. Shen, and Z. Hong, 2025, Does Changing Liability '
            'Protection Affect Corporate Director Quality?, Management Science</p>'
            '</div>')
    pubs, unparsed = parse(html)
    assert pubs == []
    assert len(unparsed) == 1
    assert unparsed[0]["year"] == "2025"
    assert "Masulis" in unparsed[0]["raw_citation"]
    assert unparsed[0]["researcher_name"] == "Test Person"


def test_empty_publication_item_is_ignored_entirely():
    """Profile pages contain empty .publication-item elements used as spacers.
    They are neither publications nor parse failures."""
    pubs, unparsed = parse('<div class="publication-item"></div>')
    assert pubs == []
    assert unparsed == []


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------
def test_internal_dedup_key_never_escapes_the_parser():
    """parse_publications uses a private _identity field while deduplicating.
    Callers must never see it — it would end up in the JSON output."""
    assert "_identity" not in s.PUB_COLUMNS
    pubs, _ = parse(item())
    assert "_identity" not in pubs[0]
    assert set(s.PUB_COLUMNS) == set(pubs[0])


def test_staff_columns_match_the_scope_of_work_data_dictionary():
    """Section 3.5.4 and the ANU scraper. If this changes, the merge breaks."""
    assert s.STAFF_COLUMNS == [
        "name", "job_title", "academic_level", "field_of_research",
        "profile_url", "university", "research_portal_url", "school",
    ]


# ---------------------------------------------------------------------------
# Profile metadata
# ---------------------------------------------------------------------------
def test_profile_meta_tags_are_read():
    html = ('<html><head>'
            '<meta name="profile-full-name" content="Dr Nicole Ang">'
            '<meta name="profile-school" content="School of Accounting, Auditing and Taxation">'
            '<meta name="profile-university-role" content="Senior Lecturer">'
            '<meta name="profile-faculty" content="Business School">'
            '</head><body></body></html>')
    meta = s.parse_profile(BeautifulSoup(html, "html.parser"))
    assert meta["profile-full-name"] == "Dr Nicole Ang"
    assert meta["profile-school"] in s.TARGET_SCHOOLS
    assert s.TARGET_SCHOOLS[meta["profile-school"]] == "Accounting"


def test_missing_meta_tags_return_none_rather_than_raising():
    meta = s.parse_profile(BeautifulSoup("<html><head></head></html>", "html.parser"))
    assert all(value is None for value in meta.values())


def test_target_schools_use_the_full_school_names_not_the_filter_labels():
    """The directory's filter shows 'Banking & Finance', but profile-school
    says 'School of Banking and Finance'. Mixing the two up is what made the
    first version of this scraper return zero staff."""
    assert "School of Banking and Finance" in s.TARGET_SCHOOLS
    assert "Banking & Finance" not in s.TARGET_SCHOOLS
