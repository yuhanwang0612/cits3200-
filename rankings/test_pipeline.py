"""
Tests for the pipeline driver — CITS3200 Group 20.

Offline. The OpenAlex step is replaced with a stub, because the point of these
tests is the wiring between the steps, not what OpenAlex returns.

Run:  python -m pytest test_pipeline.py -v
"""

import csv
import os
import tempfile

import pytest

import pipeline
from test_journals import build_abdc, build_scimago


@pytest.fixture(scope="module")
def refs():
    d = tempfile.mkdtemp()
    return (build_abdc(os.path.join(d, "abdc.xlsx")),
            build_scimago(os.path.join(d, "scimago.csv")))


def make_publications(rows, columns=("title", "journal_name", "doi", "university")):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "unsw_publications.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(columns))
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in columns})
    return path


@pytest.fixture
def fake_openalex(monkeypatch):
    """Stand in for the network call, adding the `issn` column it would add.

    It writes the same `_with_openalex.csv` filename the real one does, so the
    handoff being tested is the real handoff.
    """
    calls = []

    def enrich(publications_path, mailto=None, limit=None, use_cache=True):
        calls.append(publications_path)
        with open(publications_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows, fields = list(reader), reader.fieldnames
        base, _ = os.path.splitext(publications_path)
        out = f"{base}_with_openalex.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields + ["issn"])
            w.writeheader()
            for row in rows:
                # Every DOI in these fixtures resolves to the Journal of Finance.
                row["issn"] = "0022-1082" if row.get("doi") else ""
                w.writerow(row)
        return out, f"{base}_with_openalex_notfound.csv"

    monkeypatch.setattr(pipeline.openalex_mod, "enrich", enrich)
    return calls


# ---------------------------------------------------------------------------
# The handoff — the thing a human keeps getting wrong
# ---------------------------------------------------------------------------
def test_the_ranking_step_reads_the_openalex_output_not_the_raw_file(refs, fake_openalex):
    """The whole reason this module exists. The journal title here is one no
    reference list would ever match, so the only way it can come out rated is
    if the ISSN openalex added was actually carried through."""
    abdc, scimago = refs
    pubs = make_publications([{"title": "p", "journal_name": "J. Fin. (typo'd)",
                               "doi": "10.1111/x", "university": "UNSW"}])
    enriched, journals_path = pipeline.run(pubs, abdc, scimago)

    assert enriched.endswith("_with_openalex.csv")
    with open(journals_path, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["quality_rank"] == "A*"
    assert row["abdc_match_type"] == "issn"


def test_openalex_is_given_the_raw_file(refs, fake_openalex):
    abdc, scimago = refs
    pubs = make_publications([{"title": "p", "journal_name": "Journal of Finance",
                               "doi": "10.1111/x", "university": "UNSW"}])
    pipeline.run(pubs, abdc, scimago)
    assert fake_openalex == [pubs]


def test_the_journal_table_lands_next_to_the_publications(refs, fake_openalex):
    abdc, scimago = refs
    pubs = make_publications([{"title": "p", "journal_name": "Journal of Finance",
                               "doi": "10.1111/x", "university": "UNSW"}])
    _, journals_path = pipeline.run(pubs, abdc, scimago)
    assert os.path.dirname(journals_path) == os.path.dirname(pubs)
    assert os.path.basename(journals_path) == "journals.csv"


def test_a_harvest_row_is_written_for_every_source(refs, fake_openalex):
    import harvest
    abdc, scimago = refs
    pubs = make_publications([{"title": "p", "journal_name": "Journal of Finance",
                               "doi": "10.1111/x", "university": "UNSW"}])
    pipeline.run(pubs, abdc, scimago)
    sources = {r["source"] for r in harvest.read(os.path.dirname(pubs))}
    assert "ABDC JQL 2025" in sources and "Scimago" in sources


# ---------------------------------------------------------------------------
# Skipping OpenAlex
# ---------------------------------------------------------------------------
def test_skip_openalex_ranks_the_raw_file(refs, fake_openalex):
    abdc, scimago = refs
    pubs = make_publications([{"title": "p", "journal_name": "Journal of Finance",
                               "doi": "10.1111/x", "university": "UNSW"}])
    enriched, journals_path = pipeline.run(pubs, abdc, scimago, skip_openalex=True)
    assert enriched is None
    assert fake_openalex == []          # not called at all
    with open(journals_path, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["quality_rank"] == "A*"          # still matched, but on the title
    assert row["abdc_match_type"] == "title"


# ---------------------------------------------------------------------------
# Refusing to run on nonsense
# ---------------------------------------------------------------------------
def test_a_missing_publications_file_stops_before_anything_runs(refs, fake_openalex):
    abdc, scimago = refs
    with pytest.raises(SystemExit):
        pipeline.run("no/such/file.csv", abdc, scimago)
    assert fake_openalex == []


def test_no_reference_list_stops_before_the_expensive_step(refs, fake_openalex):
    """Calling OpenAlex and then discovering there is nothing to match against
    wastes the slow part of the run."""
    pubs = make_publications([{"title": "p", "journal_name": "Journal of Finance",
                               "doi": "10.1111/x", "university": "UNSW"}])
    with pytest.raises(SystemExit):
        pipeline.run(pubs)
    assert fake_openalex == []
