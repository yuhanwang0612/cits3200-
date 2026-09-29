"""The loaded database matches the exported files - CITS3200 Group 20.

    python load.py
    python -m pytest tests/test_database_load.py -q

`load.py` is the step between the CSVs we produce and the site the client
sees, and it is the one step nothing else checks. If it drops rows, attaches
a publication to the wrong researcher, or leaves a journal unlinked, every
number on the website is wrong while every CSV is right.

Skips when site/research.db has not been built.
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "site" / "research.db"
OUT = ROOT / "final output"
UNIS = ["adelaide", "anu", "monash", "unimelb", "unsw", "uq", "usyd", "uwa"]

pytestmark = pytest.mark.skipif(
    not DB.exists(),
    reason="site/research.db not built; run python load.py first",
)


@pytest.fixture(scope="module")
def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def csvs():
    out = {}
    for uni in UNIS:
        path = OUT / uni / f"{uni}_publications.csv"
        staff = OUT / uni / f"{uni}_staff.csv"
        if path.exists() and staff.exists():
            out[uni] = (pd.read_csv(path, dtype=str).fillna(""),
                        pd.read_csv(staff, dtype=str).fillna(""))
    return out


# --------------------------------------------------------------- nothing lost

def test_every_researcher_in_the_files_is_in_the_database(db, csvs):
    in_db = {(r["name"], r["university"])
             for r in db.execute("select name, university from researcher")}
    for uni, (_, staff) in csvs.items():
        missing = [n for n in staff["name"]
                   if (n, staff["university"].iloc[0]) not in in_db]
        assert not missing, f"{uni}: {len(missing)} researchers not loaded, e.g. {missing[:3]}"


def test_the_publication_count_per_university_matches_the_files(db, csvs):
    rows = db.execute("""
        select r.university, count(*) as n
        from publication p join researcher r using (researcher_id)
        group by r.university
    """).fetchall()
    loaded = {r["university"]: r["n"] for r in rows}
    for uni, (pubs, staff) in csvs.items():
        university = staff["university"].iloc[0]
        assert loaded.get(university) == len(pubs), \
            f"{uni}: {len(pubs)} rows in the file, {loaded.get(university)} in the database"


# --------------------------------------------------------------- integrity

def test_no_publication_is_orphaned(db):
    orphans = db.execute("""
        select count(*) from publication p
        left join researcher r using (researcher_id) where r.researcher_id is null
    """).fetchone()[0]
    assert orphans == 0


def test_every_journal_id_points_at_a_journal(db):
    broken = db.execute("""
        select count(*) from publication p
        where p.journal_id is not null
          and not exists (select 1 from journal j where j.journal_id = p.journal_id)
    """).fetchone()[0]
    assert broken == 0


def test_every_publication_has_a_title_and_a_year(db):
    bad = db.execute("""
        select count(*) from publication
        where title is null or trim(title) = '' or year is null
    """).fetchone()[0]
    assert bad == 0


def test_a_researcher_is_listed_once(db):
    repeated = db.execute("""
        select name, university, count(*) n from researcher
        group by lower(trim(name)), university having n > 1
    """).fetchall()
    assert not repeated, [dict(r) for r in repeated]


def test_no_journal_is_listed_twice(db):
    repeated = db.execute("""
        select journal_name, count(*) n from journal
        group by lower(trim(journal_name)) having n > 1
    """).fetchall()
    assert not repeated, [dict(r) for r in repeated][:5]


def test_ranked_publications_carry_a_rank_the_website_understands(db):
    used = {r[0] for r in db.execute(
        "select distinct quality_rank from publication where quality_rank is not null")}
    assert used <= {"A*", "A", "B", "C"}, sorted(used)


def test_the_database_holds_all_eight_universities(db):
    assert db.execute("select count(distinct university) from researcher").fetchone()[0] == 8
