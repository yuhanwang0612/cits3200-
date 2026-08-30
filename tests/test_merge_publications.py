import csv
import subprocess
import sys
from pathlib import Path


MERGE_SCRIPT = Path(__file__).resolve().parents[1] / "merge_publications.py"


def write_csv(path, rows):
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def run_merge(tmp_path, rows, filename="monash_publications.csv"):
    write_csv(tmp_path / filename, rows)

    result = subprocess.run(
        [sys.executable, str(MERGE_SCRIPT)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )

    output = tmp_path / "combined_publications.csv"
    assert output.exists(), result.stdout + result.stderr

    with output.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_same_doi_for_different_researchers_is_retained(tmp_path):
    rows = [
        {
            "researcher": "Alice Researcher",
            "title": "Shared Paper",
            "year": "2025",
            "doi": "10.1234/shared",
        },
        {
            "researcher": "Bob Researcher",
            "title": "Shared Paper",
            "year": "2025",
            "doi": "10.1234/shared",
        },
    ]

    out = run_merge(tmp_path, rows)

    assert len(out) == 2
    assert {r["researcher"] for r in out} == {
        "Alice Researcher",
        "Bob Researcher",
    }


def test_duplicate_doi_for_same_researcher_is_removed(tmp_path):
    rows = [
        {
            "researcher": "Alice Researcher",
            "title": "Paper One",
            "year": "2025",
            "doi": "10.1234/duplicate",
        },
        {
            "researcher": "Alice Researcher",
            "title": "Paper One",
            "year": "2025",
            "doi": "10.1234/duplicate",
        },
    ]

    out = run_merge(tmp_path, rows)

    assert len(out) == 1


def test_no_doi_title_year_fallback_is_per_researcher(tmp_path):
    rows = [
        {
            "researcher": "Alice Researcher",
            "title": "A Paper: With Punctuation!",
            "year": "2024",
            "doi": "",
        },
        {
            "researcher": "Alice Researcher",
            "title": "A Paper With Punctuation",
            "year": "2024",
            "doi": "",
        },
        {
            "researcher": "Bob Researcher",
            "title": "A Paper With Punctuation",
            "year": "2024",
            "doi": "",
        },
    ]

    out = run_merge(tmp_path, rows)

    # Alice's duplicate is removed, but Bob's copy must survive.
    assert len(out) == 2
    assert {r["researcher"] for r in out} == {
        "Alice Researcher",
        "Bob Researcher",
    }


def test_doi_normalization_handles_url_prefix_and_case(tmp_path):
    rows = [
        {
            "researcher": "Alice Researcher",
            "title": "Same DOI",
            "year": "2025",
            "doi": "HTTPS://DOI.ORG/10.1234/ABC",
        },
        {
            "researcher": "Alice Researcher",
            "title": "Same DOI",
            "year": "2025",
            "doi": "10.1234/abc",
        },
    ]

    out = run_merge(tmp_path, rows)

    assert len(out) == 1
    assert out[0]["doi"] == "10.1234/abc"


def test_openalex_metrics_survive_merge(tmp_path):
    rows = [
        {
            "researcher": "Alice Researcher",
            "title": "Metrics Paper",
            "year": "2025",
            "doi": "10.1234/metrics",
            "citation_percentile": "92.5",
            "cited_by_count": "37",
            "fwci": "2.41",
            "issn": "1234-5678",
            "journal_name": "Test Journal",
        }
    ]

    out = run_merge(tmp_path, rows)

    assert len(out) == 1
    assert out[0]["citation_percentile"] == "92.5"
    assert out[0]["cited_by_count"] == "37"
    assert out[0]["fwci"] == "2.41"
    assert out[0]["issn"] == "1234-5678"
    assert out[0]["journal_name"] == "Test Journal"


def test_usyd_schema_maps_name_and_item_type(tmp_path):
    rows = [
        {
            "name": "Sydney Researcher",
            "title": "Sydney Paper",
            "year": "2026",
            "doi": "10.1234/sydney",
            "item_type": "Journal Article",
            "field_of_research": "Finance",
        }
    ]

    out = run_merge(
        tmp_path,
        rows,
        filename="usyd_publications.csv",
    )

    assert len(out) == 1
    assert out[0]["researcher"] == "Sydney Researcher"
    assert out[0]["publication_type"] == "Journal Article"
    assert out[0]["university"] == "University of Sydney"
