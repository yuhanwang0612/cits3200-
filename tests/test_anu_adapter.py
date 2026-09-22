"""Tests for the ANU adapter.

    python -m pytest tests/test_anu_adapter.py -q

Fully offline. No network, no live directory or profile fetch: anu_scraper's
Researcher/Publication dataclasses are built by hand, and the two seed CSVs
are written to a temp directory rather than reading the real data/ files.
"""

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anu_scraper                                            # noqa: E402
from base_scrapers import anu                                 # noqa: E402
from core.schema import TYPES, validate                       # noqa: E402


def researcher(name="Sarah Adams", job_title="Senior Lecturer",
               academic_level="C", field_of_research="Accounting",
               profile_url="https://rsa.anu.edu.au/people/sarah-adams",
               less_research_intensive=False):
    return anu_scraper.Researcher(
        name=name, job_title=job_title, academic_level=academic_level,
        field_of_research=field_of_research, profile_url=profile_url,
        less_research_intensive=less_research_intensive,
    )


def publication(researcher_name="Sarah Adams", title="A working paper",
                journal_name="Accounting Review", year=2022, doi=None,
                article_url=None, coauthors="Bond, D", author_count=2,
                issn=None, publication_type="journal_article"):
    return anu_scraper.Publication(
        researcher_name=researcher_name,
        researcher_profile_url="https://rsa.anu.edu.au/people/sarah-adams",
        title=title, journal_name=journal_name, year=year, doi=doi,
        article_url=article_url, abdc_self_reported="none",
        coauthors=coauthors, author_count=author_count, issn=issn,
        publication_type=publication_type,
    )


# ------------------------------------------------------------- module basics

def test_university_and_ror():
    assert anu.UNIVERSITY == "Australian National University"
    assert anu.ROR == "019wvm592"


def test_collect_accepts_shared_refresh_option():
    assert "refresh" in inspect.signature(anu.collect).parameters


# -------------------------------------------------------------- staff mapping

def test_staff_mapping_uses_core_titles_when_available():
    r = researcher(job_title="Associate Professor", academic_level="C")
    rec = anu._staff_record(r, {})
    assert rec["name"] == "Sarah Adams"
    assert rec["name_clean"] == "Sarah Adams"
    assert rec["title"] == "Associate Professor"
    assert rec["title_clean"] == "Associate Professor"
    assert rec["level_code"] == "D"
    assert rec["level_source"] == "core.titles"
    assert rec["source_id"] is None


def test_staff_mapping_falls_back_to_anu_scraper_level():
    """'Director, Research School of Accounting' carries no rank word core.titles
    knows, so level_code comes back None from core.titles and the adapter must
    fall back to the level anu_scraper's own ladder already worked out from the
    name-prefix heading line. title_clean is then filled in generically from
    that level (scratch/_anu17/REPORT.md task 5 — 'the rank from the heading
    line when the subtitle is a role rather than a rank'), not left blank."""
    r = researcher(job_title="Director, Research School of Accounting",
                   academic_level="E")
    rec = anu._staff_record(r, {})
    assert rec["title_clean"] == "Professor"
    assert rec["level_code"] == "E"
    assert rec["level_source"] == "anu_scraper fallback"


def test_staff_mapping_no_level_from_either_path():
    r = researcher(job_title="Research Assistant", academic_level=None)
    rec = anu._staff_record(r, {})
    assert rec["title_clean"] is None
    assert rec["level_code"] is None
    assert rec["level_source"] is None


def test_identity_seed_attaches_orcid_and_openalex_ids_on_exact_match():
    identity = {"Sarah Adams": ("0000-0002-4737-4507", ["A5100632160"])}
    rec = anu._staff_record(researcher(), identity)
    assert rec["orcid"] == "0000-0002-4737-4507"
    assert rec["openalex_author_ids"] == ["A5100632160"]


def test_identity_seed_does_not_fuzzy_match():
    identity = {"Sarah J Adams": ("0000-0002-4737-4507", [])}
    rec = anu._staff_record(researcher(name="Sarah Adams"), identity)
    assert rec["orcid"] is None
    assert rec["openalex_author_ids"] == []


# --------------------------------------------------------- publication types

@pytest.mark.parametrize("anu_type,expected", [
    ("journal_article", "Journal Article"),
    ("book_chapter", "Book Chapter"),
    ("conference_paper", "Conference Paper"),
    ("industry_report", "Research Report"),
    ("textbook", "Book"),
])
def test_every_type_map_entry(anu_type, expected):
    assert anu._type(anu_type) == expected


def test_unmapped_type_falls_back_to_other_and_is_recorded():
    anu.UNKNOWN_TYPES.clear()
    assert anu._type("something_new") == "Other"
    assert anu.UNKNOWN_TYPES["something_new"] == 1
    anu.UNKNOWN_TYPES.clear()


def test_blank_type_falls_back_to_other():
    anu.UNKNOWN_TYPES.clear()
    assert anu._type(None) == "Other"
    anu.UNKNOWN_TYPES.clear()


# ------------------------------------------------------------- pub mapping

def test_publication_mapping_shape():
    pub = publication(doi="10.1/abc", article_url="https://rsa.anu.edu.au/x",
                      issn="1234-5678")
    mapped, had_page_doi = anu._map_publication(pub, "Sarah Adams", {}, __import__("collections").Counter())
    assert mapped["name"] == "Sarah Adams"
    assert mapped["title"] == "A working paper"
    assert mapped["journal"] == "Accounting Review"
    assert mapped["year"] == "2022"
    assert mapped["doi"] == "10.1/abc"
    assert mapped["link"] == "https://rsa.anu.edu.au/x"
    assert mapped["n_authors"] == 2
    assert mapped["authors"] == "Bond, D"
    assert mapped["issns"] == ["1234-5678"]
    assert mapped["source"] == "ANU staff profile"
    assert had_page_doi is True


def test_publication_mapping_no_issn_is_empty_list():
    pub = publication(issn=None)
    mapped, _ = anu._map_publication(pub, "Sarah Adams", {}, __import__("collections").Counter())
    assert mapped["issns"] == []


def test_source_is_never_orcid_crossref_or_openalex():
    """screen.py treats ORCID/Crossref/OpenAlex as *retrieved* rows, distinct
    from what the university itself listed. Getting this wrong would make
    every ANU-listed row look like a retrieval-step row to screen.py."""
    pub = publication()
    mapped, _ = anu._map_publication(pub, "Sarah Adams", {}, __import__("collections").Counter())
    assert mapped["source"] not in {"ORCID", "Crossref", "OpenAlex"}


# ---------------------------------------------------------------- DOI carry-over

def test_doi_carried_on_exact_single_match():
    from collections import Counter
    pub = publication(title="A Working Paper!", doi=None)
    backfill = {("Sarah Adams", "a working paper"): ["10.9/carried"]}
    stats = Counter()
    mapped, had_page_doi = anu._map_publication(pub, "Sarah Adams", backfill, stats)
    assert mapped["doi"] == "10.9/carried"
    assert had_page_doi is False
    assert stats["carried"] == 1


def test_doi_not_carried_when_ambiguous():
    from collections import Counter
    pub = publication(title="A Working Paper", doi=None)
    backfill = {("Sarah Adams", "a working paper"): ["10.9/one", "10.9/two"]}
    stats = Counter()
    mapped, _ = anu._map_publication(pub, "Sarah Adams", backfill, stats)
    assert mapped["doi"] is None
    assert stats["ambiguous"] == 1
    assert stats["carried"] == 0


def test_page_doi_is_never_overwritten():
    from collections import Counter
    pub = publication(title="A Working Paper", doi="10.1/page-doi")
    backfill = {("Sarah Adams", "a working paper"): ["10.9/seed-doi"]}
    stats = Counter()
    mapped, had_page_doi = anu._map_publication(pub, "Sarah Adams", backfill, stats)
    assert mapped["doi"] == "10.1/page-doi"
    assert had_page_doi is True
    assert stats["conflicts"] == 1


def test_matching_backfill_doi_is_not_a_conflict():
    from collections import Counter
    pub = publication(title="A Working Paper", doi="10.1/same")
    backfill = {("Sarah Adams", "a working paper"): ["10.1/same"]}
    stats = Counter()
    mapped, _ = anu._map_publication(pub, "Sarah Adams", backfill, stats)
    assert mapped["doi"] == "10.1/same"
    assert stats["conflicts"] == 0


def test_title_normalisation_ignores_case_and_punctuation():
    assert anu._normalise_title("A Working Paper!") == "a working paper"
    assert anu._normalise_title("A   Working, Paper.") == "a working paper"


# -------------------------------------------------------------------- seeds

def test_load_identity_from_csv(tmp_path):
    path = tmp_path / "identity.csv"
    path.write_text(
        "name,orcid,openalex_author_id\n"
        "Sarah Adams,0000-0002-4737-4507,A5100632160\n"
        "Bonnie Allan,,\n",
        encoding="utf-8",
    )
    by_name = anu._load_identity(path)
    assert by_name["Sarah Adams"] == ("0000-0002-4737-4507", ["A5100632160"])
    assert "Bonnie Allan" not in by_name


def test_load_doi_backfill_from_csv(tmp_path):
    path = tmp_path / "backfill.csv"
    path.write_text(
        "researcher_name,title,year,doi\n"
        "Sarah Adams,A Working Paper,2022,10.9/carried\n",
        encoding="utf-8",
    )
    by_key = anu._load_doi_backfill(path)
    assert by_key[("Sarah Adams", "a working paper")] == ["10.9/carried"]


# ------------------------------------------------------------------ contract

def test_mapped_records_satisfy_the_schema_contract():
    r = researcher()
    rec = anu._staff_record(r, {})
    pub = publication(doi="10.1/abc")
    mapped, _ = anu._map_publication(pub, rec["name_clean"], {}, __import__("collections").Counter())
    assert validate([rec], [mapped], verbose=False) == []
    assert mapped["type"] in TYPES


if __name__ == "__main__":
    import unittest
    sys.exit(pytest.main([__file__, "-q"]))
