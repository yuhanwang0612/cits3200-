import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[1] / "scripts" / "usyd_collect.py"
spec = importlib.util.spec_from_file_location("usyd_collect", MODULE)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(m)


def test_academic_level_mapping():
    assert m.academic_level_from_job_title("Associate Lecturer") == "A"
    assert m.academic_level_from_job_title("Lecturer in Finance") == "B"
    assert m.academic_level_from_job_title("Senior Lecturer (Teaching and Research)") == "C"
    assert m.academic_level_from_job_title("Associate Professor in Finance") == "D"
    assert m.academic_level_from_job_title("Professor") == "E"


def test_nonstandard_research_fellow_not_guessed():
    assert m.academic_level_from_job_title("Senior Research Fellow") == ""
    assert m.academic_level_from_job_title("Senior Fellow") == ""


def test_orcid_and_doi_normalisation():
    assert m.normalise_orcid("Orcid identifier 0000-0002-4726-165X") == "0000-0002-4726-165X"
    assert m.normalise_doi("https://doi.org/10.1234/ABC.5") == "10.1234/abc.5"


def test_journal_article_filter():
    assert m.is_journal_article({
        "type": "article",
        "primary_location": {"source": {"type": "journal"}},
    })
    assert not m.is_journal_article({
        "type": "article",
        "primary_location": {"source": {"type": "conference"}},
    })
    assert not m.is_journal_article({
        "type": "preprint",
        "primary_location": {"source": {"type": "repository"}},
    })


def test_dedup_uses_doi_first():
    rows = [
        {"doi": "10.1/X", "researcher_id": "r1", "title": "A", "year": 2020},
        {"doi": "https://doi.org/10.1/x", "researcher_id": "r1", "title": "A copy", "year": 2020},
        {"doi": "10.1/X", "researcher_id": "r2", "title": "A", "year": 2020},
    ]
    out = m.dedupe_publications(rows)
    assert len(out) == 2


def test_dedup_fallback_is_per_researcher_title_year():
    rows = [
        {"doi": "", "researcher_id": "r1", "title": " Same title ", "year": 2022},
        {"doi": "", "researcher_id": "r1", "title": "same title", "year": 2022},
        {"doi": "", "researcher_id": "r2", "title": "same title", "year": 2022},
    ]
    assert len(m.dedupe_publications(rows)) == 2


def test_usyd_filter_excludes_preprints():
    assert m.is_usyd_journal_article({"objectTypeDisplayName": "Journal article"})
    assert not m.is_usyd_journal_article({"objectTypeDisplayName": "Preprint"})
    assert not m.is_usyd_journal_article({"objectTypeDisplayName": "Conference contribution"})


def test_usyd_record_conversion_uses_native_fields():
    person = {
        "researcher_id": "usyd-finance-mandeep.singh",
        "name": "Mandeep Singh",
        "field_of_research": "Finance",
        "orcid": "0000-0003-2808-2100",
        "openalex_author_id": "",
        "profile_url": "https://profiles.sydney.edu.au/mandeep.singh",
    }
    record = {
        "discoveryId": "303700",
        "title": "Geographic diversification, climate risk, and bank lending: Evidence from farm loans",
        "doi": "10.1016/J.JFI.2025.101152",
        "issn": "1042-9573",
        "journal": "Journal of Financial Intermediation",
        "publicationDate": {"year": 2025},
        "publisher": "Elsevier",
        "publisherUrl": "https://doi.org/10.1016/j.jfi.2025.101152",
        "objectTypeDisplayName": "Journal article",
        "authors": [
            {"fullName": "Emdad Islam"},
            {"fullName": "Mandeep Singh"},
        ],
    }
    row = m.usyd_record_to_publication(record, person)
    assert row["source"] == "Sydney Profiles"
    assert row["doi"] == "10.1016/j.jfi.2025.101152"
    assert row["issn"] == "1042-9573"
    assert row["year"] == 2025
    assert row["author_count"] == 2
    assert row["item_type"] == "Journal Article"


def test_source_wins_when_openalex_duplicate_is_merged():
    source = [{
        "researcher_id": "r1", "doi": "10.1/x", "title": "A", "year": 2024,
        "source": "Sydney Profiles", "journal_name": "Native Journal",
        "citation_percentile": "", "cited_by_count": "", "fwci": "",
        "eissn": "", "openalex_author_id": "",
    }]
    oa = [{
        "researcher_id": "r1", "doi": "https://doi.org/10.1/X", "title": "A", "year": 2024,
        "source": "OpenAlex supplementary (validated)", "journal_name": "Different Name",
        "citation_percentile": 0.91, "cited_by_count": 12, "fwci": 1.5,
        "eissn": "1234-5678", "openalex_author_id": "https://openalex.org/A1",
    }]
    out = m.merge_source_and_openalex(source, oa)
    assert len(out) == 1
    assert out[0]["journal_name"] == "Native Journal"
    assert out[0]["citation_percentile"] == 0.91
    assert out[0]["cited_by_count"] == 12
    assert "Sydney Profiles" in out[0]["source"]
    assert "OpenAlex" in out[0]["source"]


def test_same_doi_for_two_researchers_is_retained_twice():
    rows = [
        {"doi": "10.1/shared", "researcher_id": "r1", "title": "Shared", "year": 2024},
        {"doi": "10.1/shared", "researcher_id": "r2", "title": "Shared", "year": 2024},
    ]
    assert len(m.dedupe_publications(rows)) == 2


def test_rendered_link_extractor_rejects_static_links_and_canonicalizes():
    html = """
    <a href="/mandeep.singh?nsh">Mandeep</a>
    <a href="https://profiles.sydney.edu.au/cookiesettings">Cookies</a>
    <a href="/search">Search</a>
    <a href="/groups/652446">Group</a>
    """
    assert m.extract_profile_urls_from_rendered_html(html) == [
        "https://profiles.sydney.edu.au/mandeep.singh"
    ]


def test_usyd_user_conversion_uses_structured_metadata():
    user = {
        "firstNameLastName": "Mandeep Singh",
        "orcid": {
            "value": "0000-0003-2808-2100",
            "uri": "https://orcid.org/0000-0003-2808-2100",
        },
        "positions": [
            {
                "position": "Lecturer in Finance, Discipline of Finance",
                "department": "Business School",
            }
        ],
    }
    person = m.usyd_user_to_researcher(
        user,
        "https://profiles.sydney.edu.au/mandeep.singh?nsh",
        "Finance",
    )
    assert person["name"] == "Mandeep Singh"
    assert person["job_title"] == "Lecturer in Finance, Discipline of Finance"
    assert person["academic_level"] == "B"
    assert person["orcid"] == "0000-0003-2808-2100"
    assert person["profile_url"] == "https://profiles.sydney.edu.au/mandeep.singh"


def test_group_member_api_follows_pagination():
    class Response:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

        def json(self):
            return self._data

    class Session:
        def __init__(self):
            self.starts = []

        def post(self, url, json, headers, timeout):
            self.starts.append(json["pagination"]["startFrom"])
            start = json["pagination"]["startFrom"]
            if start == 0:
                resource = [
                    {"discoveryUrlId": "alpha.person"},
                    {"discoveryUrlId": "beta.person"},
                ]
            else:
                resource = [{"discoveryUrlId": "gamma.person"}]
            return Response({
                "resource": resource,
                "pagination": {
                    "startFrom": start,
                    "perPage": 2,
                    "total": 3,
                    "totalIsLowerBound": False,
                },
            })

    session = Session()
    urls = m.collect_group_profile_urls(
        "https://profiles.sydney.edu.au/groups/652444/experts",
        per_page=2,
        session=session,
    )
    assert session.starts == [0, 2]
    assert urls == [
        "https://profiles.sydney.edu.au/alpha.person",
        "https://profiles.sydney.edu.au/beta.person",
        "https://profiles.sydney.edu.au/gamma.person",
    ]


def test_publication_api_404_is_empty_not_fatal():
    class Response:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("404 should have been handled before raise_for_status")

    class Session:
        def post(self, url, json, headers, timeout):
            return Response()

    assert m.fetch_usyd_publication_records(
        "https://profiles.sydney.edu.au/no.outputs",
        session=Session(),
    ) == []
