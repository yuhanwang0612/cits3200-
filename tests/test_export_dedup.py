"""Offline tests for FIX F — export.build_publications' dedup rule.

Key = (name, normalised title): NFKC, lowercase, every run of
non-alphanumeric -> one space. DOI-first sort. A later row with the same
key is dropped if it has no DOI, or the same DOI (case-insensitive) as an
already-kept row. Two rows with the same key but genuinely different DOIs
are both kept.

    python -m pytest tests/test_export_dedup.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export import build_publications, _normalise_title           # noqa: E402


def _pub(name="Sarah Adams", title="A Study Of Things", doi=None,
         year="2020", journal="Accounting Review"):
    return {"type": "Journal Article", "name": name, "title": title,
            "doi": doi, "year": year, "journal": journal}


def test_curly_vs_straight_quotes_are_deduplicated():
    """Six ANU rows: a curly-quote no-DOI page copy sitting next to the
    ORCID/OpenAlex copy that has a DOI, e.g. auditors' vs auditors’."""
    a = _pub(title="External auditors' reliance on management's experts", doi=None)
    b = _pub(title="External auditors’ reliance on management’s experts",
             doi="10.1/real")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 1
    assert out[0]["doi"] == "10.1/real"


def test_year_difference_does_not_defeat_dedup():
    """The old (name, title, year) key let a year mismatch hide a real
    duplicate — the key must be (name, normalised title) only."""
    a = _pub(title="Same Paper", doi="10.1/x", year="2021")
    b = _pub(title="Same Paper", doi=None, year="2020")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 1
    assert out[0]["doi"] == "10.1/x"


def test_no_doi_later_row_is_dropped():
    a = _pub(title="Paper One", doi="10.1/one")
    b = _pub(title="Paper One", doi=None)
    out = build_publications([a, b], verbose=False)
    assert len(out) == 1


def test_same_doi_case_insensitive_is_dropped():
    a = _pub(title="Paper Two", doi="10.1/ABC")
    b = _pub(title="Paper Two", doi="10.1/abc")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 1


def test_different_dois_are_both_kept():
    """Same (name, normalised title) but two REAL, different DOIs in two
    different journals — a namesake collision or a reprint, not a
    duplicate — both survive. Journals differ deliberately: same title +
    same year + same journal is exactly what FIX K (scratch/_anu18,
    export.py) now merges even across distinct DOIs — see
    tests/test_export_neardup.py for that case and its own guard test."""
    a = _pub(title="Common Title", doi="10.1/first", journal="Journal One")
    b = _pub(title="Common Title", doi="10.1/second", journal="Journal Two")
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2
    assert {r["doi"] for r in out} == {"10.1/first", "10.1/second"}


def test_different_names_same_title_both_kept():
    a = _pub(name="Sarah Adams", title="Common Title", doi=None)
    b = _pub(name="Bonnie Allan", title="Common Title", doi=None)
    out = build_publications([a, b], verbose=False)
    assert len(out) == 2


def test_normalise_title_helper():
    assert _normalise_title("External auditors’ reliance") == \
        _normalise_title("External auditors' reliance")
    assert _normalise_title("A, B: C!") == "a b c"


def test_verified_doi_typo_is_repaired_repeatably():
    row = _pub(doi="10.1111/j.1468-2443.2006.00055x")
    out = build_publications([row], verbose=False)
    assert out[0]["doi"] == "10.1111/j.1468-2443.2006.00055.x"
    assert out[0]["article_url"] == \
        "https://doi.org/10.1111/j.1468-2443.2006.00055.x"


def test_same_doi_coauthor_rows_receive_consistent_metadata():
    doi = "10.1108/aaaj-10-2023-6704"
    short = _pub(
        name="Tirukumar Thiagarajah",
        title="This is not an experiment: using vignettes in qualitative accounting research",
        doi=doi,
        year="2024",
        journal="Accounting Auditing and Accountability Journal",
    )
    full_title = (
        "Methodological Insights: This is not an experiment: using vignettes "
        "in qualitative accounting research"
    )
    rows = [short]
    for name in ("Leona Wiegmann", "Matthew Hall", "Ralph Kober"):
        rows.append(_pub(name=name, title=full_title, doi=doi, year="2025",
                         journal="Accounting Auditing and Accountability Journal"))

    out = build_publications(rows, verbose=False)

    assert {row["title"] for row in out} == {full_title}
    assert {row["year"] for row in out} == {"2025"}


def test_same_doi_title_typo_and_truncation_are_harmonised():
    doi = "10.1016/j.acclit.2018.03.003"
    full = _pub(
        name="Gladys Lee",
        title="Whistleblowing on accountingy-related misconduct: A synthesis of the literature",
        doi=doi,
        year="2018",
        journal="Accounting Literature",
    )
    short = _pub(
        name="Xinning Xiao",
        title="Whistleblowing on accounting-related misconduct",
        doi=doi,
        year="2018",
        journal="Accounting Literature",
    )
    out = build_publications([full, short], verbose=False)
    assert {row["title"] for row in out} == {full["title"]}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
