"""Counts and years are written as whole numbers — CITS3200 Group 20.

    python -m pytest tests/test_export_number_format.py -q

The client asked on 15 September for the author count to appear in the
downloadable CSV. It was appearing as "2.0" for UNSW and "2" for UQ, because
pandas turns an integer column into a float as soon as one value is missing,
and four of UNSW's 2,183 rows have no author count.

A column whose type depends on whether anyone happened to be missing a value
cannot be joined across eight universities.
"""

import csv
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export import _whole_numbers, write                       # noqa: E402


def csv_column(tmp_path, rows, column, table="publications"):
    write({table: rows}, out_dir=tmp_path, verbose=False)
    path = tmp_path / f"{tmp_path.name}_{table}.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return [r[column] for r in csv.DictReader(f)]


# ------------------------------------------------------------- the real case

def test_a_missing_author_count_does_not_turn_the_others_into_decimals(tmp_path):
    """UNSW's four rows with no author count were making the other 2,179
    render as 2.0, 3.0, 5.0."""
    rows = [{"name": "A", "author_count": 2},
            {"name": "B", "author_count": 3},
            {"name": "C", "author_count": None}]
    assert csv_column(tmp_path, rows, "author_count") == ["2", "3", ""]


def test_a_column_with_no_gaps_is_unaffected(tmp_path):
    """UQ was already correct and must stay byte-identical."""
    rows = [{"name": "A", "author_count": 2}, {"name": "B", "author_count": 3}]
    assert csv_column(tmp_path, rows, "author_count") == ["2", "3"]


def test_citations_get_the_same_treatment(tmp_path):
    """cited_by_count was rendering as 41.0 on BOTH universities, so this one
    is not a UNSW quirk."""
    rows = [{"name": "A", "cited_by_count": 41},
            {"name": "B", "cited_by_count": None}]
    assert csv_column(tmp_path, rows, "cited_by_count") == ["41", ""]


def test_journal_metrics_that_are_years_or_counts(tmp_path):
    rows = [{"journal_name": "J", "h_index": 88, "jcr_year": 2025,
             "impact_factor": 2.7},
            {"journal_name": "K", "h_index": None, "jcr_year": None,
             "impact_factor": None}]
    write({"journals": rows}, out_dir=tmp_path, verbose=False)
    with (tmp_path / f"{tmp_path.name}_journals.csv").open(
            newline="", encoding="utf-8") as f:
        got = list(csv.DictReader(f))[0]
    assert got["h_index"] == "88"
    assert got["jcr_year"] == "2025"
    assert got["impact_factor"] == "2.7"          # a real decimal, left alone


# -------------------------------------------------- what it must NOT do

def test_a_genuine_decimal_is_never_truncated():
    """If a value in one of these columns is fractional, the column is not what
    the list assumes. Rounding it away would be a worse bug than '2.0'."""
    frame = _whole_numbers(pd.DataFrame([{"cited_by_count": 1.5},
                                         {"cited_by_count": 2.0}]))
    assert frame["cited_by_count"].tolist() == [1.5, 2.0]


def test_columns_that_are_meant_to_be_fractional_are_left_alone(tmp_path):
    """fwci and citation_percentile are not counts. They are not in the list,
    and a run where they happen to be whole must not silently make them ints,
    or the column type would differ between runs."""
    rows = [{"name": "A", "fwci": 1.0, "citation_percentile": 99.0}]
    write({"publications": rows}, out_dir=tmp_path, verbose=False)
    with (tmp_path / f"{tmp_path.name}_publications.csv").open(
            newline="", encoding="utf-8") as f:
        got = list(csv.DictReader(f))[0]
    assert got["fwci"] == "1.0"
    assert got["citation_percentile"] == "99.0"


def test_an_all_empty_column_is_left_alone():
    """source_id is empty on every UNSW row, because UNSW has no repository."""
    frame = _whole_numbers(pd.DataFrame([{"source_id": None},
                                         {"source_id": None}]))
    assert frame["source_id"].isna().all()


def test_a_text_column_of_digits_stays_text():
    """year is a string in the schema. It already writes correctly and must not
    be converted, in case a source ever supplies something like '2009-2010'."""
    frame = _whole_numbers(pd.DataFrame([{"year": "2013"}, {"year": None}]))
    assert frame["year"].tolist()[0] == "2013"


def test_the_json_was_always_right(tmp_path):
    """Only the CSV goes through pandas, so the JSON never had this problem and
    must not be changed by the fix."""
    import json
    write({"publications": [{"name": "A", "author_count": 2}]},
          out_dir=tmp_path, verbose=False)
    data = json.loads((tmp_path / f"{tmp_path.name}_publications.json")
                      .read_text(encoding="utf-8"))
    assert data[0]["author_count"] == 2
