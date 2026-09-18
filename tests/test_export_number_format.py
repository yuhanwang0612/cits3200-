"""Offline tests for export._whole_numbers (scratch/_anu17, task 1).

pandas promotes an int column to float64 the moment a single value is
missing, so a whole-number count like author_count renders in the csv as
"2.0" instead of "2" as soon as one row is missing it. _whole_numbers casts
a gap-free-or-not numeric column to the nullable Int64 dtype when every
non-null value is mathematically whole, so a gap renders as an empty cell
instead of a float — and leaves a genuinely fractional column alone.
"""

import pandas as pd

from export import _whole_numbers


def _csv_rows(df):
    return _whole_numbers(df.copy()).to_csv(index=False).splitlines()[1:]


def test_column_with_one_missing_value_renders_without_trailing_dot_zero():
    # A second column keeps every row non-empty, matching how write() is
    # always called (a full table schema, never a single-column frame) —
    # pandas quotes a lone empty cell as '""' only when it is the entire
    # row, which is a csv-writer quirk unrelated to _whole_numbers itself.
    df = pd.DataFrame({"author_count": [2, 3, None], "title": ["A", "B", "C"]})
    rows = _csv_rows(df)
    assert rows == ["2,A", "3,B", ",C"]


def test_column_with_no_gaps_is_unchanged():
    df = pd.DataFrame({"author_count": [2, 3, 4], "title": ["A", "B", "C"]})
    rows = _csv_rows(df)
    assert rows == ["2,A", "3,B", "4,C"]


def test_year_and_cited_by_count_get_the_same_treatment():
    df = pd.DataFrame({
        "year": [2020, None, 2022],
        "cited_by_count": [5, 10, None],
    })
    out = _whole_numbers(df.copy())
    rows = out.to_csv(index=False).splitlines()
    assert rows == ["year,cited_by_count", "2020,5", ",10", "2022,"]


def test_fractional_column_is_not_converted():
    df = pd.DataFrame({"fwci": [1.23, None, 0.87]})
    out = _whole_numbers(df.copy())
    assert out["fwci"].dtype == "float64"
    csv_text = out.to_csv(index=False)
    assert "1.23" in csv_text and "0.87" in csv_text


def test_sjr_fractional_column_is_not_converted():
    df = pd.DataFrame({"sjr": [2.5, 1.0, None]})
    out = _whole_numbers(df.copy())
    # 1.0 alone is whole, but the column as a whole has a fractional value
    # (2.5), so it must stay float — every non-null value must be whole,
    # not merely one of them.
    assert out["sjr"].dtype == "float64"


def test_non_numeric_column_is_left_alone():
    df = pd.DataFrame({"title": ["Paper A", "Paper B", None]})
    out = _whole_numbers(df.copy())
    assert out["title"].tolist()[:2] == ["Paper A", "Paper B"]
    assert pd.isna(out["title"].tolist()[2])


def test_all_null_column_is_left_alone():
    df = pd.DataFrame({"quality_rank_numeric": [None, None, None]})
    out = _whole_numbers(df.copy())
    assert out["quality_rank_numeric"].isna().all()
