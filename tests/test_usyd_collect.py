from __future__ import annotations

import inspect

from base_scrapers import usyd
from core.clean import clean_pub
from core.schema import validate


def test_shared_identity_and_refresh_contract():
    assert usyd.UNIVERSITY == "University of Sydney"
    assert usyd.ROR == "0384j8v12"
    assert "refresh" in inspect.signature(usyd.collect).parameters


def test_group_id_and_non_profile_filtering():
    assert usyd.group_id_from_url(usyd.ACCOUNTING_EXPERTS_URL) == 652444
    assert "cookiesettings" in usyd.NON_PROFILE_SLUGS


def test_orcid_and_doi_normalisation():
    assert usyd.normalise_orcid("ORCID 0000-0002-4726-165X") == "0000-0002-4726-165X"
    assert usyd.normalise_doi("HTTPS://DOI.ORG/10.1234/ABC.5") == "10.1234/abc.5"


def test_staff_mapping_uses_academic_appointment_when_current_role_is_admin():
    user = {
        "firstNameLastName": "Example Person",
        "positions": [{"position": "Head of Discipline"}],
        "institutionalAppointments": [{"position": "Professor"}],
        "orcid": {"value": "0000-0002-1825-0097"},
    }
    row = usyd.user_to_staff(
        user,
        "https://profiles.sydney.edu.au/example.person",
        "Finance",
    )
    assert row["title"] == "Head of Discipline"
    assert row["title_clean"] == "Professor"
    assert row["level_code"] == "E"
    assert row["source_id"] == "example.person"
    assert row["orcid"] == "0000-0002-1825-0097"


def test_publication_type_mapping():
    assert usyd._publication_type({"objectTypeDisplayName": "Journal article"}) == "Journal Article"
    assert usyd._publication_type({"objectTypeDisplayName": "Preprint"}) == "Preprint"
    assert usyd._publication_type({"objectTypeDisplayName": "Conference contribution"}) == "Conference Paper"


def test_native_record_maps_to_shared_publication_contract():
    person = {
        "name_clean": "Mandeep Singh",
        "source_id": "mandeep.singh",
    }
    record = {
        "discoveryId": "303700",
        "title": "Geographic diversification, climate risk, and bank lending: Evidence from farm loans",
        "doi": "10.1016/J.JFI.2025.101152",
        "issn": "1042-9573",
        "journal": "Journal of Financial Intermediation",
        "publicationDate": {"year": 2025},
        "publisherUrl": "https://doi.org/10.1016/j.jfi.2025.101152",
        "objectTypeDisplayName": "Journal article",
        "authors": [
            {"fullName": "Emdad Islam"},
            {"fullName": "Mandeep Singh"},
        ],
    }
    row = usyd.record_to_publication(record, person)
    assert row["source"] == "Sydney Profiles"
    assert row["doi"] == "10.1016/j.jfi.2025.101152"
    assert row["issns"] == ["1042-9573"]
    assert row["year"] == "2025"
    assert row["n_authors"] == 2
    assert row["type"] == "Journal Article"


def test_dedup_is_per_researcher_and_doi_first():
    rows = [
        {"name": "Alice", "doi": "10.1234/X", "title": "A", "year": "2020"},
        {"name": "Alice", "doi": "https://doi.org/10.1234/x", "title": "A copy", "year": "2020"},
        {"name": "Bob", "doi": "10.1234/X", "title": "A", "year": "2020"},
    ]
    out = usyd.dedupe_publications(rows)
    assert len(out) == 2
    assert {row["name"] for row in out} == {"Alice", "Bob"}


def test_dedup_fallback_is_per_researcher_title_year():
    rows = [
        {"name": "Alice", "doi": None, "title": " Same title ", "year": "2022"},
        {"name": "Alice", "doi": None, "title": "same title", "year": "2022"},
        {"name": "Bob", "doi": None, "title": "same title", "year": "2022"},
    ]
    assert len(usyd.dedupe_publications(rows)) == 2


def test_repec_repository_label_cannot_masquerade_as_journal():
    row = {
        "name": "Example Person",
        "title": "Example paper",
        "year": None,
        "type": "Journal Article",
        "journal": "RePEc: Research Papers in Economics",
        "doi": "10.1108/example.html?utm_source=repec",
    }
    clean_pub(row)
    assert row["type"] == "Preprint"


def test_native_journal_label_without_year_or_journal_is_not_exportable_as_journal():
    person = {"name_clean": "Example Person", "source_id": "example.person"}
    row = usyd.record_to_publication({
        "title": "Old working paper",
        "objectTypeDisplayName": "Journal article",
    }, person)
    assert row["type"] == "Other"
    assert row["year"] is None
    assert row["journal"] is None


def test_mapped_rows_validate_against_shared_contract():
    staff = [{
        "name": "Example Person",
        "name_clean": "Example Person",
        "university": usyd.UNIVERSITY,
        "discipline": "Accounting",
        "profile_url": "https://profiles.sydney.edu.au/example.person",
        "title": "Lecturer in Accounting",
        "title_clean": "Lecturer",
        "level_code": "B",
        "source_id": "example.person",
        "orcid": None,
    }]
    pubs = [usyd.record_to_publication({
        "title": "Example paper",
        "publicationDate": {"year": 2024},
        "objectTypeDisplayName": "Journal article",
        "journal": "Accounting Review",
    }, staff[0])]
    assert validate(staff, pubs, verbose=False) == []
