"""Tests for the two OpenAlex modules — CITS3200 Group 20.

    python -m pytest tests/test_openalex.py -q

Offline. `cached_get` is stubbed with real response shapes, so no network and
no budget is spent running these.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.schema import blank_pub                     # noqa: E402
from enrichment import openalex as oa_enrich              # noqa: E402
from info import openalex as oa_get               # noqa: E402


def work(doi="10.1111/jofi.12345", issn_l="0022-1082", issns=("0022-1082",),
         source_name="The Journal of Finance", publisher="Wiley",
         percentile=0.98, top10=True, cited=42, fwci=2.1, oa_url=None):
    return {
        "id": "https://openalex.org/W2741809807",
        "doi": f"https://doi.org/{doi}",
        "cited_by_count": cited,
        "fwci": fwci,
        "citation_normalized_percentile": {"value": percentile,
                                           "is_in_top_10_percent": top10},
        "open_access": {"is_oa": bool(oa_url), "oa_status": "green" if oa_url else "closed",
                        "oa_url": oa_url},
        "primary_location": {"source": {"issn_l": issn_l, "issn": list(issns),
                                        "display_name": source_name,
                                        "host_organization_name": publisher}},
    }


@pytest.fixture
def stub(monkeypatch):
    """Serve prepared works from the batched DOI filter."""
    def make(works):
        by_doi = {oa_enrich.bare_doi(w["doi"]): w for w in works}

        def fake(url, params=None, **kw):
            wanted = params["filter"].split("doi:", 1)[1].split("|")
            return {"results": [by_doi[d] for d in wanted if d in by_doi]}

        monkeypatch.setattr(oa_enrich, "cached_get", fake)
    return make


# ------------------------------------------------------------------ the DOI

@pytest.mark.parametrize("raw,expected", [
    ("10.1111/ABC", "10.1111/abc"),
    ("https://doi.org/10.1111/ABC", "10.1111/abc"),
    ("http://dx.doi.org/10.1111/abc", "10.1111/abc"),
    ("https://dx.doi.org/10.1111/abc", "10.1111/abc"),
    ("doi:10.1111/abc", "10.1111/abc"),
    ("  10.1111/abc  ", "10.1111/abc"),
    ("", None), (None, None),
])
def test_doi_normalisation(raw, expected):
    """UNSW's profile pages use the dx.doi.org form, ORCID and Crossref store
    the bare one. Normalising only one side loses the match."""
    assert oa_enrich.bare_doi(raw) == expected


def test_a_dx_doi_org_row_still_matches(stub):
    stub([work(doi="10.1111/jofi.12345")])
    pubs = [blank_pub(doi="http://dx.doi.org/10.1111/JOFI.12345",
                      type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["cited_by_count"] == 42


# ----------------------------------------------------------------- the ISSN

@pytest.mark.parametrize("raw,expected", [
    ("0022-1082", "0022-1082"),
    ("00221082", "0022-1082"),
    ("1467-629x", "1467-629X"),
    ("nonsense", None), ("123", None), ("", None), (None, None),
])
def test_issn_hyphenation(raw, expected):
    """Hyphenated, because ABDC's list is and scimago.py strips hyphens on
    lookup, so that form is the one that matches both."""
    assert oa_enrich.hyphenate(raw) == expected


def test_the_issn_reaches_the_row(stub):
    """The three ranking steps after this one match on `issns` and nothing
    else, and a staff-directory adapter supplies none."""
    stub([work()])
    pubs = [blank_pub(doi="10.1111/jofi.12345", type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["issns"] == ["0022-1082"]


def test_an_issn_the_adapter_supplied_is_kept_and_not_replaced(stub):
    """eSpace's ISSN came off the publisher's own record; ours came off
    whichever copy of the paper OpenAlex resolved."""
    stub([work(issn_l="1111-1111", issns=("1111-1111",))])
    pubs = [blank_pub(doi="10.1111/jofi.12345", issns=["0022-1082"],
                      type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["issns"] == ["0022-1082", "1111-1111"]


def test_a_repository_issn_is_refused(stub):
    """OpenAlex resolved the DOI to the SSRN copy, so it returns SSRN's ISSN
    while the row names a real journal. Scimago rates SSRN Electronic Journal,
    so keeping it gives the row a real-looking SJR belonging to a repository.
    Fifty UNSW rows had exactly this."""
    stub([work(issn_l="1556-5068", issns=("1556-5068",),
               source_name="SSRN Electronic Journal")])
    pubs = [blank_pub(doi="10.1111/jofi.12345", journal="Australian Tax Forum",
                      type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["issns"] == []


def test_ssrns_own_issn_is_kept_on_an_ssrn_paper(stub):
    """The rule is "not the journal's ISSN", not "never SSRN"."""
    stub([work(issn_l="1556-5068", issns=("1556-5068",),
               source_name="SSRN Electronic Journal")])
    pubs = [blank_pub(doi="10.1111/jofi.12345",
                      journal="SSRN Electronic Journal", type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["issns"] == ["1556-5068"]


def test_a_row_with_no_match_keeps_the_issns_it_arrived_with(stub):
    stub([])
    pubs = [blank_pub(doi="10.9999/nothing", issns=["0022-1082"],
                      type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["issns"] == ["0022-1082"]


# -------------------------------------------------------------- other fields

def test_the_metrics_land_on_the_row(stub):
    stub([work(oa_url="https://example.org/paper.pdf")])
    pubs = [blank_pub(doi="10.1111/jofi.12345", type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    row = pubs[0]
    assert row["citation_percentile"] == 0.98
    assert row["citation_top_10_percent"] is True
    assert row["cited_by_count"] == 42
    assert row["fwci"] == 2.1
    assert row["oa_url"] == "https://example.org/paper.pdf"
    assert row["openalex_id"] == "W2741809807"


def test_the_publisher_is_filled_but_never_overwritten(stub):
    stub([work(publisher="Wiley")])
    blank = blank_pub(doi="10.1111/jofi.12345", type="Journal Article")
    already = blank_pub(doi="10.1111/jofi.12345", publisher="Elsevier",
                        type="Journal Article")
    oa_enrich.enrich([blank, already], verbose=False)
    assert blank["publisher"] == "Wiley"
    assert already["publisher"] == "Elsevier"


def test_a_work_with_no_percentile_still_yields_its_citation_count(stub):
    w = work()
    w.pop("citation_normalized_percentile")
    stub([w])
    pubs = [blank_pub(doi="10.1111/jofi.12345", type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["citation_percentile"] is None
    assert pubs[0]["cited_by_count"] == 42


def test_a_work_with_no_source_does_not_crash(stub):
    stub([{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/abc"}])
    pubs = [blank_pub(doi="10.1/abc", type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["issns"] == [] and pubs[0]["openalex_id"] == "W1"


def test_nothing_is_dropped(stub):
    stub([work()])
    pubs = [blank_pub(doi="10.1111/jofi.12345", type="Journal Article"),
            blank_pub(doi=None, title="No DOI", type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert len(pubs) == 2


# ------------------------------------------------------- choosing the key

def test_an_orcid_is_preferred():
    clause, how = oa_get.author_filter({"orcid": "0000-0001-7701-3721",
                                        "openalex_author_ids": ["A5001"]})
    assert clause == "author.orcid:0000-0001-7701-3721" and how == "orcid"


def test_author_ids_are_the_fallback_when_there_is_no_orcid():
    """About forty of UNSW's ninety-three have no ORCID anywhere. Skipping
    them loses their work entirely."""
    clause, how = oa_get.author_filter({"orcid": None,
                                        "openalex_author_ids": ["A1", "A2"]})
    assert clause == "author.id:A1|A2" and how == "author id"


def test_several_author_ids_are_one_call_not_several():
    """OpenAlex or-s them in a single filter. A call per id would multiply the
    cost of the cheapest step in the pipeline by the number of duplicate
    author records, which is exactly the people who have most of them."""
    clause, _ = oa_get.author_filter({"openalex_author_ids": ["A1", "A2", "A3"]})
    assert clause.count("author.id:") == 1


def test_a_researcher_with_neither_is_skipped_not_searched():
    """A name search costs ten times a filter. Falling back to one here would
    put ninety-three of them in a step that runs every time."""
    assert oa_get.author_filter({"orcid": None}) == (None, None)
    assert oa_get.author_filter({"openalex_author_ids": []}) == (None, None)


def test_a_uq_style_record_is_unaffected():
    """UQ records carry an ORCID from eSpace and no author ids at all."""
    clause, how = oa_get.author_filter({"orcid": "0000-0002-1825-0097"})
    assert how == "orcid" and clause.endswith("0000-0002-1825-0097")
