from core.schema import blank_pub
from export import build_journals


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
