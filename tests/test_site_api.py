"""The website's API, tested against the loaded database - CITS3200 Group 20.

    python load.py                       # builds site/research.db
    python -m pytest tests/test_site_api.py -q

Acceptance Test C says the filters must produce correct results, and Test D
says the counts on the website must match the database. Both were being
checked by hand. These do it in a few seconds, so a data reload or an API
change cannot quietly break them.

No browser and no running server: Flask's test client calls the routes
directly. The whole file skips when site/research.db has not been built, so
it never fails for someone who has only cloned the repo.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "site" / "research.db"

pytestmark = pytest.mark.skipif(
    not DB.exists(),
    reason="site/research.db not built; run python load.py first",
)


@pytest.fixture(scope="module")
def client():
    import app as site
    site.app.config["TESTING"] = True
    with site.app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def db():
    connection = sqlite3.connect(DB)
    yield connection
    connection.close()


def get(client, url):
    response = client.get(url)
    assert response.status_code == 200, f"{url} returned {response.status_code}"
    return response.get_json()


# --------------------------------------------------------------- the pages

@pytest.mark.parametrize("page", [
    "/", "/researchers.html", "/researcher.html", "/universities.html",
    "/documentation.html",
])
def test_every_page_is_served(client, page):
    assert client.get(page).status_code == 200


# --------------------------------------------------------------- filters

def test_each_university_filter_returns_only_that_university(client):
    """The home page and the rankings table link here with the university
    name in the URL, so a mismatch between the name in the link and the name
    in the database shows as an empty page."""
    for university in [u["name"] for u in get(client, "/api/universities")["universities"]]:
        rows = get(client, f"/api/researchers?university={university}")["researchers"]
        assert rows, f"no researchers for {university}"
        assert {r["university"] for r in rows} == {university}


def test_field_filter(client):
    rows = get(client, "/api/researchers?field=Finance")["researchers"]
    assert rows
    assert {r["field_of_research"] for r in rows} == {"Finance"}


def test_level_filter(client):
    rows = get(client, "/api/researchers?level=E")["researchers"]
    assert rows
    assert {r["academic_level"] for r in rows} == {"E"}


def test_name_search_is_a_partial_match_and_ignores_case(client):
    rows = get(client, "/api/researchers?name=TROT")["researchers"]
    assert rows
    assert all("trot" in r["name"].lower() for r in rows)


def test_filters_combine(client):
    rows = get(client,
               "/api/researchers?university=UNSW Sydney&field=Finance&level=E")["researchers"]
    assert rows
    for r in rows:
        assert (r["university"], r["field_of_research"], r["academic_level"]) == \
               ("UNSW Sydney", "Finance", "E")


def test_a_filter_that_matches_nothing_returns_an_empty_list(client):
    """Not a 500, and not every researcher, which is what a filter the API
    does not recognise would do."""
    assert get(client, "/api/researchers?name=zzzzznobody")["researchers"] == []


# --------------------------------------------- Test D: the counts agree

def test_publication_counts_match_the_database(client, db):
    """The number on the researcher page, the number of rows the API hands
    it, and the number of rows in the database are the same number."""
    researchers = get(client, "/api/researchers")["researchers"]
    sample = sorted(researchers, key=lambda r: -r["publication_count"])[:10]
    for r in sample:
        detail = get(client, f"/api/researchers/{r['id']}")
        listed = get(client, f"/api/researchers/{r['id']}/publications")
        rows = listed["publications"] if isinstance(listed, dict) else listed
        in_db = db.execute(
            "select count(*) from publication where researcher_id = ?",
            (r["id"],)).fetchone()[0]
        assert detail["publication_count"] == in_db == len(rows), r["name"]


def test_no_duplicate_publications_for_a_researcher(client, db):
    """Acceptance Test D: zero duplicate (researcher, title, year) rows."""
    duplicates = db.execute("""
        select r.university, r.name, p.title, p.year, count(*) as n
        from publication p join researcher r using (researcher_id)
        group by p.researcher_id, lower(trim(p.title)), p.year
        having n > 1
    """).fetchall()
    assert duplicates == [], f"{len(duplicates)} duplicated rows, e.g. {duplicates[:3]}"


def test_researcher_counts_match_the_database(client, db):
    api = {u["name"]: u["researcher_count"]
           for u in get(client, "/api/universities")["universities"]}
    rows = db.execute(
        "select university, count(*) from researcher group by university").fetchall()
    assert api == dict(rows)


# --------------------------------------------- what the client will notice

def test_every_researcher_has_a_university_and_a_field(client):
    for r in get(client, "/api/researchers")["researchers"]:
        assert r["university"], r["name"]
        assert r["field_of_research"], r["name"]


def test_every_researcher_has_an_academic_level(client):
    """Acceptance Test B. Reported per university, because one adapter
    missing levels should not read as the whole dataset being broken."""
    missing = {}
    for r in get(client, "/api/researchers")["researchers"]:
        if not r["academic_level"]:
            missing.setdefault(r["university"], []).append(r["name"])
    assert not missing, {u: len(names) for u, names in missing.items()}


def test_the_ranked_counts_add_up(client):
    """A*, A, B, C and unranked are what the table shows, so they have to
    sum to the total or the row contradicts itself."""
    for r in get(client, "/api/researchers")["researchers"]:
        parts = (r["count_a_star"] + r["count_a"] + r["count_b"] + r["count_c"]
                 + r["count_unranked"])
        assert parts == r["publication_count"], r["name"]
        assert r["abdc_ranked_count"] == r["publication_count"] - r["count_unranked"]


def test_a_researcher_page_for_an_unknown_id_is_a_404_not_a_crash(client):
    assert client.get("/api/researchers/999999").status_code == 404
    assert client.get("/api/researchers/999999/publications").status_code == 404


def test_publication_search_and_the_abdc_only_filter(client):
    """Both are on the researcher page."""
    busiest = max(get(client, "/api/researchers")["researchers"],
                  key=lambda r: r["publication_count"])
    listed = get(client, f"/api/researchers/{busiest['id']}/publications")
    rows = listed["publications"] if isinstance(listed, dict) else listed
    word = rows[0]["title"].split()[0]

    found = get(client,
                f"/api/researchers/{busiest['id']}/publications?search={word}")
    found_rows = found["publications"] if isinstance(found, dict) else found
    assert 0 < len(found_rows) <= len(rows)

    ranked = get(client,
                 f"/api/researchers/{busiest['id']}/publications?only_abdc=1")
    ranked_rows = ranked["publications"] if isinstance(ranked, dict) else ranked
    assert all(p["quality_rank"] for p in ranked_rows)
