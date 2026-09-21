from base_scrapers import uwa
from core.clean import clean_pub
from core.schema import norm_type
from export import build_publications, build_staff


def test_ssrn_doi_is_preprint_even_when_source_calls_it_journal_article():
    row = {
        "name": "Example Researcher",
        "title": "A working paper",
        "year": "2024",
        "type": "Journal Article",
        "journal": None,
        "doi": "https://doi.org/10.2139/SSRN.1234567",
        "source": "ORCID",
    }
    clean_pub(row)
    assert row["type"] == "Preprint"
    assert build_publications([row], verbose=False) == []


def test_non_article_pure_labels_are_normalised_and_excluded():
    assert norm_type("other") == "Other"
    assert norm_type("peer-review") == "Other"


def test_client_publications_require_a_verified_journal_name():
    row = {
        "name": "Example Researcher",
        "title": "Unresolved item",
        "year": "2024",
        "type": "Journal Article",
        "journal": None,
        "doi": "10.1000/example",
        "source": "ORCID",
    }
    assert build_publications([row], verbose=False) == []


def test_publication_export_preserves_source_status_without_extra_schema_fields():
    row = {
        "name": "Example Researcher",
        "title": "Accepted paper",
        "year": "2026",
        "type": "Journal Article",
        "journal": "Accounting Review",
        "publication_status": "Accepted/In press",
        "source": "UWA Pure",
    }
    exported = build_publications([row], verbose=False)[0]
    assert exported["publication_status"] == "Accepted/In press"
    assert "publication_type" not in exported


def test_staff_export_keeps_official_raw_job_title():
    records = [{
        "name_clean": "Example Researcher",
        "title": "Professor of Accounting and Head of Department",
        "title_clean": "Professor",
        "level_code": "E",
        "university": uwa.UNIVERSITY,
        "discipline": "Accounting",
        "profile_url": "https://example.test/person",
    }]
    assert build_staff(records)[0]["job_title"] == records[0]["title"]


def test_uwa_cross_affiliation_is_one_person_with_auditable_secondary_field():
    accounting = {
        "name_clean": "Vincent Chong",
        "discipline": "Accounting",
        "profile_url": "https://research-repository.uwa.edu.au/en/persons/vincent-chong/",
        "title": "Professor",
        "level_code": "E",
        "person_type": "Teaching and Research",
    }
    finance = {
        **accounting,
        "discipline": "Finance",
        "title": None,
        "level_code": None,
    }
    rows, audit = uwa._merge_duplicate_staff_affiliations([accounting, finance])
    assert len(rows) == 1
    assert rows[0]["discipline"] == "Accounting"
    assert rows[0]["additional_disciplines"] == ["Finance"]
    assert audit[0]["primary_discipline"] == "Accounting"
