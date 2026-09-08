"""Tests for the UNSW adapter — CITS3200 Group 20.

    python -m pytest tests/test_unsw_adapter.py -q

Fully offline. No Chrome, no network: the roster is the only part that needs a
browser and it is not exercised here. The publication fixtures are UNSW's real
markup, trimmed, including the two entries that caused the parsing rules to
exist in the first place.
"""

import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from adapters import unsw                                    # noqa: E402
from core.schema import TYPES, validate                      # noqa: E402
from export import build_publications                        # noqa: E402


PERSON = {"name_clean": "Ronald Masulis", "source_id": None,
          "profile_url": "https://www.unsw.edu.au/staff/ronald-masulis"}


def item(category="Journal articles", year="2019", title="A paper",
         author="Li H;  Liu L;  Masulis R", journal="Journal of Finance",
         volume="74", page="1-30", publisher=None, href=None, extra=""):
    """One `.publication-item`, in UNSW's own markup."""
    parts = [f'<span class="publication-category">{category}</span>',
             f'<span class="publication-year">{year}</span>']
    if author:
        parts.append(f'<span class="rg-author">{author}</span>')
    if title:
        parts.append(f"<span class=\"rg-title\">'{title}'</span>")
    if journal:
        parts.append(f'<span class="rg-source-title">{journal}</span>')
    if volume:
        parts.append(f'<span class="rg-volume">{volume}</span>')
    if page:
        parts.append(f'<span class="rg-page">{page}</span>')
    if publisher:
        parts.append(f'<span class="rg-publisher">{publisher}</span>')
    if href:
        parts.append(f'<a href="{href}">link</a>')
    return f'<div class="publication-item">{"".join(parts)}{extra}</div>'


def parse(*items):
    soup = BeautifulSoup("".join(items), "html.parser")
    return unsw.parse_publications(soup, PERSON)


# --------------------------------------------------------------- the type map

def test_the_unsw_label_becomes_the_shared_vocabulary():
    """Without this the export's `type != "Journal Article"` filter drops
    every row and writes an empty file without raising anything."""
    pubs, _ = parse(item(category="Journal articles"))
    assert pubs[0]["type"] == "Journal Article"


@pytest.mark.parametrize("label", sorted(unsw.UNSW_TYPES))
def test_every_mapped_label_lands_in_the_vocabulary(label):
    assert unsw.UNSW_TYPES[label] in TYPES


def test_all_twenty_labels_unsw_uses_are_mapped():
    """Measured on the full 4,134-row scrape. A label added by UNSW later is
    caught at runtime by UNKNOWN_TYPES instead."""
    seen_on_the_site = {
        "Journal articles", "Conference Papers", "Preprints", "Media",
        "Book Chapters", "Conference Presentations", "Books", "Working Papers",
        "Other", "Reports", "Conference Abstracts", "Theses / Dissertations",
        "Conference Posters", "Conference Proceedings (Editor of)",
        "Edited Books", "Recorded / Rendered Creative Works",
        "Creative Written Works", "Software / Code", "Scholarly Editions",
        "Patents",
    }
    assert not {s for s in seen_on_the_site if s.lower() not in unsw.UNSW_TYPES}


def test_an_unmapped_label_is_recorded_not_swallowed():
    unsw.UNKNOWN_TYPES.clear()
    unsw._type("Interpretive Dance")
    assert unsw.UNKNOWN_TYPES["Interpretive Dance"] == 1
    unsw.UNKNOWN_TYPES.clear()


# ------------------------------------------------------------ stacked titles

@pytest.mark.parametrize("printed,clean,prefix", [
    ("Emeritus Scientia Professor Roger Simnett", "Roger Simnett", "Emeritus Professor"),
    ("Scientia Professor Ronald Masulis", "Ronald Masulis", "Professor"),
    ("Associate Professor Mark Humphery-Jenner", "Mark Humphery-Jenner", "Associate Professor"),
    ("Adjunct Senior Lecturer Jane Doe", "Jane Doe", "Senior Lecturer"),
    ("Professorial Fellow Sam Smith", "Sam Smith", "Professorial Fellow"),
    ("Dr Jason Zein", "Jason Zein", "Dr"),
    ("Neil Warren", "Neil Warren", None),
])
def test_stacked_honorifics_are_stripped(printed, clean, prefix):
    """`name_clean` is the join key between staff and publications, and the key
    a merged eight-university table matches on. core.titles.split_prefix leaves
    "Scientia Professor Ronald Masulis" whole, which would put the honorific in
    the name."""
    assert unsw._split_prefix(printed) == (clean, prefix)


def test_the_returned_prefix_is_one_core_titles_can_look_up():
    from core.titles import level, rank
    _, prefix = unsw._split_prefix("Emeritus Scientia Professor Roger Simnett")
    assert level(rank("Head of School", prefix)) == "E"


# ------------------------------------------------------------------ decoding

def test_the_literal_html_ent_tag_is_decoded():
    """UNSW emits an escaped entity as a literal tag inside the text. Left
    alone, "Journal of Business Finance & Accounting" matches nothing."""
    # Escaped in the source, which is why it survives BeautifulSoup: get_text
    # unescapes it once and hands back a literal `<html_ent .../>` string.
    markup = ('<div class="publication-item"><span class="rg-source-title">'
              'Journal of Business Finance '
              '&lt;html_ent glyph="@amp;" ascii="&amp;amp;"/&gt;'
              ' Accounting</span><span class="rg-title">\'T\'</span>'
              '<span class="publication-category">Journal articles</span></div>')
    pubs, _ = parse(markup)
    assert pubs[0]["journal"] == "Journal of Business Finance & Accounting"


def test_titles_lose_their_wrapping_quotes():
    pubs, _ = parse(item(title="Stress tests and small business lending"))
    assert pubs[0]["title"] == "Stress tests and small business lending"


# ---------------------------------------------------------------- forthcoming

def test_a_status_suffix_is_taken_off_the_journal_name():
    """"Journal of Financial Economics, forthcoming" breaks the ISSN and ABDC
    joins, and that particular row is an A* paper sitting unrated."""
    pubs, _ = parse(item(journal="Journal of Financial Economics, forthcoming",
                         volume=None, page=None))
    assert pubs[0]["journal"] == "Journal of Financial Economics"
    assert pubs[0]["publication_status"] == "forthcoming"


def test_a_doi_with_no_issue_placement_is_forthcoming():
    pubs, _ = parse(item(volume=None, page=None, href="https://doi.org/10.1111/x"))
    assert pubs[0]["publication_status"] == "forthcoming"


def test_volume_and_pages_mean_published():
    pubs, _ = parse(item(volume="74", page="1-30"))
    assert pubs[0]["publication_status"] == "published"


@pytest.mark.parametrize("journal", ["SSRN Electronic Journal", "arXiv",
                                     "UNSW Working Paper Series"])
def test_a_repository_is_a_working_paper(journal):
    pubs, _ = parse(item(journal=journal, volume=None, page=None))
    assert pubs[0]["publication_status"] == "working_paper"


def test_a_repository_name_is_not_recorded_as_the_journal():
    """core.schema.clean_journal blanks these, so a repository cannot be rated
    as though it were a journal."""
    pubs, _ = parse(item(journal="SSRN Electronic Journal"))
    assert pubs[0]["journal"] is None


# ------------------------------------------------------------------- the DOI

def test_the_doi_is_preferred_over_any_other_link():
    pubs, _ = parse(item(
        href="https://doi.org/10.1111/jofi.12345",
        extra='<a href="https://www.jstor.org/stable/1">jstor</a>'))
    assert pubs[0]["doi"] == "10.1111/jofi.12345"


def test_a_bare_doi_org_link_is_discarded_entirely():
    """UNSW links some entries to "http://dx.doi.org" with nothing after it.
    That is neither an identifier nor a usable URL, so it is dropped rather
    than written out as a link that goes nowhere."""
    pubs, _ = parse(item(href="http://dx.doi.org"))
    assert pubs[0]["doi"] is None
    assert pubs[0]["link"] is None


def test_a_non_doi_link_still_becomes_the_link():
    pubs, _ = parse(item(href="https://www.austlii.edu.au/x"))
    assert pubs[0]["doi"] is None
    assert pubs[0]["link"] == "https://www.austlii.edu.au/x"


# ------------------------------------------------------------------- dedup

def test_the_same_article_under_two_dois_is_one_row():
    """UNSW lists the 1986 Journal of Finance paper twice, once with its JSTOR
    DOI and once with its Wiley one. The DOI is deliberately not in the key."""
    pubs, _ = parse(item(href="https://doi.org/10.1111/wiley.1"),
                    item(href="https://doi.org/10.2307/jstor.1"))
    assert len(pubs) == 1


def test_capitalisation_is_not_a_different_paper():
    pubs, _ = parse(item(title="Stress tests and small business lending"),
                    item(title="Stress Tests and Small Business Lending"))
    assert len(pubs) == 1


def test_the_two_listings_disagreeing_about_the_year_is_still_one_paper():
    """The same JFE article is dated 2019 on one entry and 2017 on the other,
    which is why the year is not in the identity key."""
    pubs, _ = parse(item(year="2019"), item(year="2017"))
    assert len(pubs) == 1


def test_a_reprint_in_two_outlets_stays_two_rows():
    """The client counts these as two outputs, so the journal IS in the key."""
    pubs, _ = parse(item(journal="Goods and Services Tax Journal"),
                    item(journal="Weekly Tax Bulletin"))
    assert len(pubs) == 2


def test_the_same_title_as_two_different_kinds_stays_two_rows():
    pubs, _ = parse(item(category="Conference Papers", journal=None),
                    item(category="Books", journal=None))
    assert len(pubs) == 2


def test_the_copy_carrying_a_doi_wins():
    pubs, _ = parse(item(), item(href="https://doi.org/10.1111/jofi.1"))
    assert len(pubs) == 1 and pubs[0]["doi"] == "10.1111/jofi.1"


def test_two_entries_sharing_a_doi_with_different_titles_stay_apart():
    """Economic Record issues one DOI for a batch of book reviews. Those score
    0.458 on title similarity; genuine repeats score 0.99."""
    pubs, _ = parse(
        item(title="Review of a book about trade", journal="Economic Record",
             href="https://doi.org/10.1111/j.1475-4932.1998.tb01935.x"),
        item(title="Something else entirely", journal="Economic Record",
             href="https://doi.org/10.1111/j.1475-4932.1998.tb01935.x"))
    assert len(pubs) == 2


def test_the_surviving_row_takes_fields_the_other_had():
    """The two listings are rarely equally complete: one carries the volume,
    the other the page range."""
    pubs, _ = parse(
        item(title="A paper", journal="JOURNAL OF FINANCE", volume="74", page=None,
             href="https://doi.org/10.1111/x"),
        item(title="A paper", journal="Journal of Finance: essays", volume=None,
             page="1-30", publisher="Wiley", href="https://doi.org/10.1111/x"))
    assert len(pubs) == 1
    assert pubs[0]["volume"] == "74" and pubs[0]["pages"] == "1-30"
    assert pubs[0]["publisher"] == "Wiley"


# ------------------------------------------------------------------- authors

def test_authors_are_counted_from_the_list_not_a_split_string():
    """Counted from the page's own list so a name containing a semicolon
    cannot inflate it."""
    pubs, _ = parse(item(author="Li H;  Liu L;  Masulis R;  Zein J"))
    assert pubs[0]["n_authors"] == 4
    assert pubs[0]["authors"] == "Li H; Liu L; Masulis R; Zein J"


def test_no_authors_is_blank_not_zero():
    """"We don't know" and "nobody wrote it" are different."""
    pubs, _ = parse(item(author=None))
    assert pubs[0]["n_authors"] is None


# ------------------------------------------------------- refusing to guess

def test_an_entry_with_no_structured_title_is_set_aside_not_parsed():
    """Silently dropping these understates a researcher; silently mis-parsing
    them is worse."""
    markup = ('<div class="publication-item">Smith J, some free text citation, '
              '2019</div>')
    pubs, unparsed = parse(markup)
    assert pubs == []
    assert len(unparsed) == 1
    assert "no structured title" in unparsed[0]["reason"]


def test_issns_are_empty_because_unsw_publishes_none():
    """They have to arrive from enrich/openalex.py, which is why that module
    dropping the ISSN is not a small matter for this university."""
    pubs, _ = parse(item())
    assert pubs[0]["issns"] == []


# ------------------------------------------------------------ the contract

def test_a_parsed_row_satisfies_the_schema_and_survives_the_export():
    person = {"university": unsw.UNIVERSITY, "discipline": "Finance",
              "name": "Scientia Professor Ronald Masulis",
              "name_clean": "Ronald Masulis", "prefix": "Professor",
              "title": "Professor", "title_clean": "Professor",
              "level_code": "E", "profile_url": PERSON["profile_url"],
              "source_id": None, "orcid": None}
    soup = BeautifulSoup(item(href="https://doi.org/10.1111/jofi.1"), "html.parser")
    pubs, _ = unsw.parse_publications(soup, person)

    assert validate([person], pubs, verbose=False) == []

    rows = build_publications(pubs, [person], verbose=False)
    assert len(rows) == 1
    assert rows[0]["name"] == "Ronald Masulis"
    assert rows[0]["journal_name"] == "Journal of Finance"
    assert rows[0]["article_url"] == "https://doi.org/10.1111/jofi.1"


# --------------------------------------------------- columns across universities

def uq_shaped():
    """One record and one publication in the shape adapters/uq.py produces."""
    from core.schema import blank_pub
    record = {"university": "University of Queensland", "discipline": "Finance",
              "name": "Professor Jane Doe", "name_clean": "Jane Doe",
              "prefix": "Professor", "title": "Professor",
              "title_clean": "Professor", "level_code": "E",
              "profile_url": "https://business.uq.edu.au/x",
              "source_id": "12345", "orcid": "0000-0002-1825-0097"}
    pub = blank_pub(name="Jane Doe", source_id="12345", title="A UQ paper",
                    year="2020", type="Journal Article", n_authors=2,
                    authors="Doe J; Smith A", issns=["0022-1082"],
                    journal="The Journal of Finance", publisher="Wiley",
                    doi="10.1111/uq.1", source="UQ eSpace",
                    link="https://espace.library.uq.edu.au/view/UQ:1")
    return [record], [pub]


def unsw_shaped():
    person = {"university": unsw.UNIVERSITY, "discipline": "Finance",
              "name": "Scientia Professor Ronald Masulis",
              "name_clean": "Ronald Masulis", "prefix": "Professor",
              "title": "Professor", "title_clean": "Professor",
              "level_code": "E", "profile_url": PERSON["profile_url"],
              "source_id": None, "orcid": None}
    soup = BeautifulSoup(item(href="https://doi.org/10.1111/jofi.1"), "html.parser")
    pubs, _ = unsw.parse_publications(soup, person)
    return [person], pubs


def headers_of(records, pubs, tmp_path):
    import csv
    from export import export
    export(records, pubs, out_dir=tmp_path, verbose=False)
    out = {}
    for table in ("staff", "publications", "journals", "harvest"):
        with (tmp_path / f"{table}.csv").open(newline="", encoding="utf-8") as f:
            out[table] = next(csv.reader(f))
    return out


@pytest.mark.parametrize("table",
                         ["staff", "publications", "journals", "harvest"])
def test_unsw_and_uq_export_the_same_columns(table, tmp_path):
    """The eight universities are merged into one table, so a column named
    differently by one of them is a column the merge silently loses.

    This holds because the adapter writes no CSV at all: it returns dicts in
    the core.schema shape and export.py, one shared function, writes every
    university's four tables from its own hardcoded keys. There is nowhere for
    an adapter to introduce a column name. This test is here so that stays
    true — if it fails, something has started writing output outside export.py.
    """
    ours = headers_of(*unsw_shaped(), tmp_path / "unsw")
    theirs = headers_of(*uq_shaped(), tmp_path / "uq")
    assert ours[table] == theirs[table]


def test_the_columns_the_merge_joins_on_are_present(tmp_path):
    """name and orcid identify the researcher, doi the paper. Two staff across
    the eight already share the surname Tan, so a name is not a safe key on
    its own and the ORCID carried onto every row is what disambiguates."""
    columns = headers_of(*unsw_shaped(), tmp_path)["publications"]
    for required in ("name", "orcid", "doi", "title", "year", "journal_name",
                     "quality_rank", "source"):
        assert required in columns
