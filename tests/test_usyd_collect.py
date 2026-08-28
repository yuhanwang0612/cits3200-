import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[1] / "scripts" / "usyd_collect.py"
spec = importlib.util.spec_from_file_location("usyd_collect", MODULE)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(m)


def test_academic_level_mapping():
    assert m.academic_level_from_job_title("Associate Lecturer") == "A"
    assert m.academic_level_from_job_title("Lecturer in Finance") == "B"
    assert m.academic_level_from_job_title("Senior Lecturer (Teaching and Research)") == "C"
    assert m.academic_level_from_job_title("Associate Professor in Finance") == "D"
    assert m.academic_level_from_job_title("Professor") == "E"


def test_nonstandard_research_fellow_not_guessed():
    assert m.academic_level_from_job_title("Senior Research Fellow") == ""
    assert m.academic_level_from_job_title("Senior Fellow") == ""


def test_orcid_and_doi_normalisation():
    assert m.normalise_orcid("Orcid identifier 0000-0002-4726-165X") == "0000-0002-4726-165X"
    assert m.normalise_doi("https://doi.org/10.1234/ABC.5") == "10.1234/abc.5"


def test_journal_article_filter():
    assert m.is_journal_article({
        "type": "article",
        "primary_location": {"source": {"type": "journal"}},
    })
    assert not m.is_journal_article({
        "type": "article",
        "primary_location": {"source": {"type": "conference"}},
    })
    assert not m.is_journal_article({
        "type": "preprint",
        "primary_location": {"source": {"type": "repository"}},
    })


def test_dedup_uses_doi_first():
    rows = [
        {"doi": "10.1/X", "researcher_id": "r1", "title": "A", "year": 2020},
        {"doi": "https://doi.org/10.1/x", "researcher_id": "r1", "title": "A copy", "year": 2020},
        {"doi": "10.1/X", "researcher_id": "r2", "title": "A", "year": 2020},
    ]
    out = m.dedupe_publications(rows)
    assert len(out) == 2


def test_dedup_fallback_is_per_researcher_title_year():
    rows = [
        {"doi": "", "researcher_id": "r1", "title": " Same title ", "year": 2022},
        {"doi": "", "researcher_id": "r1", "title": "same title", "year": 2022},
        {"doi": "", "researcher_id": "r2", "title": "same title", "year": 2022},
    ]
    assert len(m.dedupe_publications(rows)) == 2
