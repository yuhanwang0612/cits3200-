"""
Tests for the harvest record — CITS3200 Group 20.

Fully offline; every file is written into a temp directory.

Run:  python -m pytest test_harvest.py -v
"""

import csv
import json
import os
import tempfile

import pytest

import harvest


@pytest.fixture
def out():
    return tempfile.mkdtemp()


def rows_in(d):
    with open(os.path.join(d, "harvest.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Shape — this has to match Sean's file exactly or the eight universities
# will not concatenate
# ---------------------------------------------------------------------------
def test_columns_match_seans_uq_output(out):
    harvest.record("University of New South Wales", "UNSW staff profile", 2026, out)
    with open(os.path.join(out, "harvest.csv"), newline="", encoding="utf-8") as f:
        assert csv.DictReader(f).fieldnames == [
            "university", "source", "last_run", "latest_year"]


def test_both_csv_and_json_are_written(out):
    harvest.record("UNSW", "UNSW staff profile", 2026, out)
    assert os.path.exists(os.path.join(out, "harvest.csv"))
    with open(os.path.join(out, "harvest.json"), encoding="utf-8") as f:
        assert json.load(f)[0]["source"] == "UNSW staff profile"


def test_last_run_is_utc_iso_like_seans(out):
    harvest.record("UNSW", "UNSW staff profile", 2026, out)
    stamp = rows_in(out)[0]["last_run"]
    assert "T" in stamp and stamp.endswith("+00:00")


# ---------------------------------------------------------------------------
# Upsert — the whole point
# ---------------------------------------------------------------------------
def test_a_second_source_is_added_not_overwritten(out):
    harvest.record("UNSW", "UNSW staff profile", 2026, out)
    harvest.record("UNSW", "OpenAlex", 2026, out)
    assert {r["source"] for r in rows_in(out)} == {"UNSW staff profile", "OpenAlex"}


def test_rerunning_one_source_updates_only_its_own_row(out):
    """Re-running openalex.py must not make the scraper look like it ran too.
    Without this, `last_run` says nothing useful and FR14 is decorative."""
    harvest.record("UNSW", "UNSW staff profile", 2026, out,
                   last_run="2026-08-01T00:00:00+00:00")
    harvest.record("UNSW", "OpenAlex", 2026, out,
                   last_run="2026-08-02T00:00:00+00:00")
    harvest.record("UNSW", "OpenAlex", 2026, out,
                   last_run="2026-08-22T00:00:00+00:00")

    by_source = {r["source"]: r["last_run"] for r in rows_in(out)}
    assert by_source["UNSW staff profile"] == "2026-08-01T00:00:00+00:00"
    assert by_source["OpenAlex"] == "2026-08-22T00:00:00+00:00"
    assert len(rows_in(out)) == 2


def test_the_same_source_at_two_universities_is_two_rows(out):
    """One row per source *per university* — the dictionary is explicit."""
    harvest.record("UNSW", "OpenAlex", 2026, out)
    harvest.record("University of Queensland", "OpenAlex", 2026, out)
    assert len(rows_in(out)) == 2


def test_rows_are_sorted_so_the_file_does_not_churn(out):
    harvest.record("UNSW", "Scimago", 2025, out)
    harvest.record("UNSW", "ABDC JQL 2025", 2025, out)
    assert [r["source"] for r in rows_in(out)] == ["ABDC JQL 2025", "Scimago"]


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------
def test_a_missing_latest_year_is_blank_not_zero(out):
    """A source with no publication years is not a source whose newest
    publication is year 0."""
    harvest.record("UNSW", "ABDC JQL 2025", None, out)
    assert rows_in(out)[0]["latest_year"] == ""


def test_a_row_needs_both_a_university_and_a_source(out):
    with pytest.raises(ValueError):
        harvest.record("", "OpenAlex", 2026, out)
    with pytest.raises(ValueError):
        harvest.record("UNSW", "", 2026, out)


def test_reading_before_anything_is_recorded_is_empty_not_an_error(out):
    assert harvest.read(out) == []


def test_the_output_directory_is_created_if_missing(out):
    nested = os.path.join(out, "does", "not", "exist")
    harvest.record("UNSW", "OpenAlex", 2026, nested)
    assert os.path.exists(os.path.join(nested, "harvest.csv"))


# ---------------------------------------------------------------------------
# latest_year_in
# ---------------------------------------------------------------------------
def test_latest_year_ignores_blanks_and_junk():
    """Some UNSW entries have no year, and a harvest record is not worth
    failing a scrape over."""
    rows = [{"year": "2019"}, {"year": ""}, {"year": None},
            {"year": "n.d."}, {"year": "2024"}]
    assert harvest.latest_year_in(rows) == 2024


def test_latest_year_is_none_when_nothing_has_a_year():
    assert harvest.latest_year_in([{"year": ""}, {"year": "forthcoming"}]) is None
    assert harvest.latest_year_in([]) is None


def test_latest_year_rejects_implausible_years():
    """A four-digit number in a year column is not automatically a year."""
    assert harvest.latest_year_in([{"year": "2020"}, {"year": "9999"}]) == 2020


# ---------------------------------------------------------------------------
# university_in
# ---------------------------------------------------------------------------
def test_university_is_read_from_the_data_not_guessed():
    rows = [{"university": "University of New South Wales"}] * 3
    assert harvest.university_in(rows) == "University of New South Wales"


def test_a_file_with_two_universities_gets_no_harvest_row():
    """Better to record nothing than to record a row claiming a university
    that is only half of what the file contains."""
    assert harvest.university_in(
        [{"university": "UNSW"}, {"university": "UQ"}]) is None


def test_blank_universities_are_ignored():
    assert harvest.university_in(
        [{"university": "UNSW"}, {"university": ""}, {"university": None}]) == "UNSW"


def test_no_university_column_is_none_not_a_crash():
    assert harvest.university_in([{"title": "a paper"}]) is None
    assert harvest.university_in([]) is None
