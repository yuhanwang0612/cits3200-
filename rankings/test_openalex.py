"""
Tests for the OpenAlex join — CITS3200 Group 20.

Offline. The network call is replaced with a stub returning a real OpenAlex
response shape (taken from an actual lookup of an Accounting and Finance
paper), so these run with no key, no network and no rate limiting.

Run:  python -m pytest test_openalex.py -v
"""

import csv
import os
import tempfile

import pytest

import openalex


# A real response, trimmed. Note OpenAlex lowercases the DOI it returns and
# wraps it in a URL, which is why normalise_doi exists.
WORK = {
    "id": "https://openalex.org/W2052544672",
    "doi": "https://doi.org/10.1111/j.1467-629x.2010.00398.x",
    "publication_year": 2011,
    "type": "article",
    "cited_by_count": 133,
    "fwci": 14.2357,
    "citation_normalized_percentile": {
        "value": 0.9844651,
        "is_in_top_1_percent": False,
        "is_in_top_10_percent": True,
    },
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S4210171597",
            "display_name": "Accounting and Finance",
            "issn_l": "0810-5391",
            "issn": ["0810-5391", "1467-629X"],
        }
    },
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs against the stub, never the real API."""
    def fake(session, dois, mailto):
        known = openalex.normalise_doi(WORK["doi"])
        return {known: WORK} if known in dois else {}
    monkeypatch.setattr(openalex, "fetch_batch", fake)


def write_publications(rows):
    path = os.path.join(tempfile.mkdtemp(), "pubs.csv")
    columns = ["title", "journal_name", "doi", "citation_percentile"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
    return path


def run(rows):
    out, notfound = openalex.enrich(write_publications(rows), use_cache=False)
    read = lambda p: list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    return read(out), read(notfound)


# ---------------------------------------------------------------------------
# DOI normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("10.1111/ABC", "10.1111/abc"),
    ("https://doi.org/10.1111/ABC", "10.1111/abc"),
    ("http://dx.doi.org/10.1111/abc", "10.1111/abc"),
    ("doi:10.1111/abc", "10.1111/abc"),
    ("  10.1111/abc  ", "10.1111/abc"),
    ("", None),
    (None, None),
])
def test_doi_normalisation(raw, expected):
    """Our DOIs come with URL prefixes and mixed case; OpenAlex returns them
    lowercased and wrapped. Both sides have to be reduced to the same thing or
    nothing matches."""
    assert openalex.normalise_doi(raw) == expected


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def test_fields_are_pulled_off_a_work():
    got = openalex.extract(WORK)
    assert got["citation_percentile"] == 0.9844651
    assert got["citation_top_10_percent"] is True
    assert got["cited_by_count"] == 133
    assert got["fwci"] == 14.2357
    assert got["openalex_id"] == "W2052544672"


def test_issn_is_taken_from_the_work():
    """The whole reason this runs before the ranking joins: OpenAlex gives us
    the ISSN that the university website never did."""
    assert openalex.extract(WORK)["issn"] == "0810-5391"


def test_missing_pieces_do_not_crash():
    """Preprints and older records often have no percentile and no source."""
    assert openalex.extract({"id": "https://openalex.org/W1"}) == {
        "citation_percentile": None, "citation_top_10_percent": None,
        "cited_by_count": None, "fwci": None, "issn": None, "openalex_id": "W1",
    }


def test_a_work_with_no_percentile_still_yields_its_citation_count():
    work = dict(WORK)
    work.pop("citation_normalized_percentile")
    got = openalex.extract(work)
    assert got["citation_percentile"] is None
    assert got["cited_by_count"] == 133


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def test_matched_row_is_filled_in():
    rows, _ = run([{"title": "p1", "doi": "10.1111/j.1467-629X.2010.00398.x"}])
    assert rows[0]["citation_percentile"] == "0.9844651"
    assert rows[0]["issn"] == "0810-5391"
    assert rows[0]["cited_by_count"] == "133"


def test_case_and_url_prefix_still_match():
    """Our data has DOIs written several ways; all should hit the same work."""
    for written in ("10.1111/J.1467-629X.2010.00398.X",
                    "https://doi.org/10.1111/j.1467-629x.2010.00398.x"):
        rows, _ = run([{"title": "p", "doi": written}])
        assert rows[0]["cited_by_count"] == "133", written


def test_publication_with_no_doi_is_reported_not_guessed():
    """No DOI means no lookup. Matching on title would risk attaching another
    paper's citation count to someone's name."""
    rows, notfound = run([{"title": "a book chapter", "doi": ""}])
    assert rows[0]["citation_percentile"] == ""
    assert notfound[0]["reason"] == "no DOI recorded"


def test_doi_absent_from_openalex_is_reported():
    rows, notfound = run([{"title": "p", "doi": "10.9999/not-real"}])
    assert rows[0]["citation_percentile"] == ""
    assert notfound[0]["reason"] == "DOI not found in OpenAlex"


def test_nothing_is_dropped():
    rows, _ = run([
        {"title": "p1", "doi": "10.1111/j.1467-629X.2010.00398.x"},
        {"title": "p2", "doi": ""},
        {"title": "p3", "doi": "10.9999/not-real"},
    ])
    assert [r["title"] for r in rows] == ["p1", "p2", "p3"]


def test_existing_issn_is_not_overwritten():
    """If a scraper did capture an ISSN, it came from the publisher and beats
    ours."""
    path = os.path.join(tempfile.mkdtemp(), "pubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "doi", "issn"])
        w.writeheader()
        w.writerow({"title": "p", "doi": "10.1111/j.1467-629X.2010.00398.x",
                    "issn": "1234-5678"})
    out, _ = openalex.enrich(path, use_cache=False)
    row = list(csv.DictReader(open(out, newline="", encoding="utf-8")))[0]
    assert row["issn"] == "1234-5678"


def test_existing_percentile_column_is_reused_not_duplicated():
    """The scrapers already emit an empty citation_percentile column, which is
    the one Scope 3.5.4 defines. Adding a second one would split the data."""
    rows, _ = run([{"title": "p", "doi": "10.1111/j.1467-629X.2010.00398.x"}])
    assert list(rows[0]).count("citation_percentile") == 1
    assert rows[0]["citation_percentile"] == "0.9844651"


def test_missing_doi_column_fails_clearly():
    path = os.path.join(tempfile.mkdtemp(), "bad.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("title,journal_name\na,b\n")
    with pytest.raises(SystemExit):
        openalex.enrich(path, use_cache=False)
