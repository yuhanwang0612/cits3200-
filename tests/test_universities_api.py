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
