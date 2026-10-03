from core.schema import blank_pub
from export import (build_journals, fill_missing_publication_ratings,
                    normalize_job_title, university_key)


def test_journal_export_only_contains_names_used_by_publications():
    pubs = [
        blank_pub(journal="Used Journal", type="Journal Article"),
        blank_pub(journal="Unused Book Series", type="Book Chapter"),
    ]
    rows = build_journals(pubs, used_names={"Used Journal"})
    assert [row["journal_name"] for row in rows] == ["Used Journal"]


def test_journal_export_merges_metadata_from_duplicate_source_rows():
    first = blank_pub(journal="Journal of Finance", type="Journal Article",
                      issns=[])
    first.update(abdc_title="Journal of Finance", abdc="A*", sjr=None)
    second = blank_pub(journal="Journal of Finance", type="Journal Article",
                       issns=["0022-1082"])
    second.update(abdc_title="Journal of Finance", abdc="A*", sjr=12.3)
    row = build_journals([first, second], used_names={"Journal of Finance"})[0]
    assert row["issn"] == "0022-1082"
    assert row["sjr"] == 12.3


def test_unknown_publication_journal_has_a_matching_journal_row():
    rows = build_journals([], used_names={"unknown"})
    assert rows == [{
        "journal_name": "unknown", "journal_raw": None, "publisher": None,
        "issn": None, "quality_rank": None, "abdc_edition": None,
        "impact_factor": None, "impact_factor_5yr": None, "jcr_year": None,
        "sjr": None, "sjr_quartile": None, "h_index": None,
        "cites_per_doc_2y": None, "scimago_year": None,
    }]


def test_abdc_title_creates_journal_even_when_source_title_is_missing():
    row = blank_pub(journal=None, issns=["1234-5678"], type="Journal Article")
    row.update(abdc_title="Canonical Journal", abdc="A")
    journals = build_journals([row], used_names={"Canonical Journal"})
    assert journals[0]["journal_name"] == "Canonical Journal"
    assert journals[0]["issn"] == "1234-5678"


def test_job_title_filter_covers_shared_academic_ranks():
    assert normalize_job_title("Professor of Finance") == "Professor"
    assert normalize_job_title("Head of School and Associate Professor") == "Associate Professor"
    assert normalize_job_title("Senior Research Fellow in Accounting") == "Senior Research Fellow"
    assert normalize_job_title("Professorial Fellow in Finance") == "Professorial Fellow"
    assert normalize_job_title("Reader in Accounting") == "Reader"
    assert normalize_job_title("Professor Emeritus") == "Emeritus Professor"


def test_job_title_filter_preserves_unmatched_administrative_roles():
    assert normalize_job_title("Program Director") == "Program Director"
    assert normalize_job_title("Dean, Business School") == "Dean, Business School"


def test_university_codes_and_official_names_share_an_override_key():
    assert university_key("monash") == university_key("Monash University")
    assert university_key("adelaide") == university_key("Adelaide University")
    assert university_key("uwa") == university_key("University of Western Australia")


def test_blank_publication_ratings_are_filled_without_overwriting_existing_values():
    publications = [
        {"journal_name": "Journal A", "quality_rank": None, "sjr_quartile": "Q2"},
        {"journal_name": "Journal A", "quality_rank": "A*", "sjr_quartile": None},
    ]
    journals = [{"journal_name": "Journal A", "quality_rank": "A", "sjr_quartile": "Q1"}]
    assert fill_missing_publication_ratings(publications, journals) == 2
    assert publications == [
        {"journal_name": "Journal A", "quality_rank": "A", "sjr_quartile": "Q2"},
        {"journal_name": "Journal A", "quality_rank": "A*", "sjr_quartile": "Q1"},
    ]
