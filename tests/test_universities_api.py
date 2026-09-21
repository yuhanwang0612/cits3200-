import app as webapp

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Journal, Publication, Researcher


def test_university_rankings_are_aggregated_from_database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine)
    monkeypatch.setattr(webapp, "Session", test_session)

    session = test_session()
    uwa_accounting = Researcher(
        name="Accounting Researcher",
        university="The University of Western Australia",
        field_of_research="Accounting",
    )
    uwa_finance = Researcher(
        name="Finance Researcher",
        university="The University of Western Australia",
        field_of_research="Finance",
    )
    ranked_journal = Journal(
        journal_name="Ranked Journal",
        impact_factor=4.0,
        impact_factor_5yr=5.0,
    )
    missing_jif_journal = Journal(journal_name="Journal Without JIF")
    session.add_all([
        uwa_accounting,
        uwa_finance,
        ranked_journal,
        missing_jif_journal,
    ])
    session.flush()
    session.add_all([
        Publication(
            researcher=uwa_accounting,
            journal=ranked_journal,
            title="Accounting paper",
            quality_rank="A*",
        ),
        Publication(
            researcher=uwa_finance,
            journal=missing_jif_journal,
            title="Finance paper",
            quality_rank="B",
        ),
    ])
    session.commit()
    session.close()

    response = webapp.app.test_client().get("/api/universities")

    assert response.status_code == 200
    rows = response.get_json()["universities"]
    assert len(rows) == 1
    uwa = rows[0]
    assert uwa["code"] == "UWA"
    assert uwa["researcher_count"] == 2
    assert uwa["publication_count"] == 2
    assert uwa["abdc_ranked_count"] == 2
    assert uwa["top_tier_count"] == 1
    assert uwa["avg_jif"] == 4.0
    assert uwa["avg_jif_5"] == 5.0
    assert uwa["avg_articles"] == 1.0
    assert uwa["accounting"]["researcher_count"] == 1
    assert uwa["accounting"]["top_tier_count"] == 1
    assert uwa["finance"]["researcher_count"] == 1
    assert uwa["finance"]["avg_jif"] is None


def test_university_page_uses_database_api():
    html = (webapp.BASE_DIR + "/site/universities.html")
    with open(html, encoding="utf-8") as handle:
        source = handle.read()

    assert "fetch('/api/universities'" in source
    assert "data/universities.json" not in source
    assert "data/researchers.json" not in source


def test_abdc_ranked_count_excludes_unranked_publications(monkeypatch):
    """The home page reports ABDC coverage, so an unrated paper must not count."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine)
    monkeypatch.setattr(webapp, "Session", test_session)

    session = test_session()
    researcher = Researcher(
        name="Adelaide Researcher",
        university="Adelaide University",
        field_of_research="Accounting",
    )
    journal = Journal(journal_name="Some Journal")
    session.add_all([researcher, journal])
    session.flush()
    session.add_all([
        Publication(
            researcher=researcher,
            journal=journal,
            title="Rated paper",
            quality_rank="C",
        ),
        Publication(
            researcher=researcher,
            journal=journal,
            title="Unrated paper",
            quality_rank=None,
        ),
        Publication(
            researcher=researcher,
            journal=journal,
            title="Blank rating paper",
            quality_rank="  ",
        ),
    ])
    session.commit()
    session.close()

    rows = webapp.app.test_client().get("/api/universities").get_json()["universities"]

    assert len(rows) == 1
    adelaide = rows[0]
    # The database stores Adelaide under its current name, not "University of Adelaide".
    assert adelaide["code"] == "UA"
    assert adelaide["publication_count"] == 3
    assert adelaide["abdc_ranked_count"] == 1
    assert adelaide["top_tier_count"] == 0
    assert adelaide["accounting"]["abdc_ranked_count"] == 1


def test_home_page_uses_database_api():
    html = (webapp.BASE_DIR + "/site/index.html")
    with open(html, encoding="utf-8") as handle:
        source = handle.read()

    assert "fetch('/api/universities'" in source
    assert "data/universities.json" not in source
    assert "banner-sample" not in source
