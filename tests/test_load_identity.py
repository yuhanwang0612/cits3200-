"""Loader identity tests.

The staff directories are scraped, so two different people occasionally arrive
with the same ORCID. That must not delete a researcher or hand one of them the
other's publications.
"""

import csv

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import load as loader
from models import Publication, Researcher


STAFF_FIELDS = [
    "name", "job_title", "academic_level", "university",
    "field_of_research", "source_id", "orcid", "profile_url",
]

PUB_FIELDS = ["name", "orcid", "source_id", "journal_name", "title", "year"]


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def run_loader(tmp_path, monkeypatch, capsys):
    """Run load.main() over a throwaway university directory."""

    def run(staff_rows, publication_rows):
        data_dir = tmp_path / "final output"
        uni_dir = data_dir / "monash"
        uni_dir.mkdir(parents=True)

        write_csv(uni_dir / "monash_staff.csv", STAFF_FIELDS, staff_rows)
        write_csv(
            uni_dir / "monash_publications.csv", PUB_FIELDS, publication_rows
        )
        write_csv(
            uni_dir / "monash_journals.csv",
            ["journal_name", "quality_rank"],
            [{"journal_name": "Journal of Finance", "quality_rank": "A*"}],
        )

        db_path = tmp_path / "test.db"
        monkeypatch.setattr(loader, "DATA_DIR", data_dir)
        monkeypatch.setattr(loader, "DB", f"sqlite:///{db_path}")
        loader.main()

        engine = create_engine(f"sqlite:///{db_path}")
        return sessionmaker(bind=engine)(), capsys.readouterr().out

    return run


def staff(name, orcid):
    return {
        "name": name,
        "job_title": "Emeritus Professor",
        "academic_level": "E",
        "university": "Monash University",
        "field_of_research": "Finance",
        "source_id": "",
        "orcid": orcid,
        "profile_url": "https://research.monash.edu/en/persons/x/",
    }


def paper(name, orcid, title):
    return {
        "name": name,
        "orcid": orcid,
        "source_id": "",
        "journal_name": "Journal of Finance",
        "title": title,
        "year": "2020",
    }


SHARED_ORCID = "0000-0002-0306-2982"


def test_shared_orcid_keeps_both_researchers(run_loader):
    """Neither person is dropped when the directory repeats one ORCID."""

    session, output = run_loader(
        [staff("Christine Brown", SHARED_ORCID), staff("Kym Brown", SHARED_ORCID)],
        [
            paper("Christine Brown", SHARED_ORCID, "Barrier exchange options"),
            paper("Kym Brown", SHARED_ORCID, "Banking crises"),
        ],
    )

    names = {r.name for r in session.query(Researcher).all()}
    assert names == {"Christine Brown", "Kym Brown"}

    # The scraped ORCID is still reported on both records; only the identity
    # lookup ignores it.
    assert all(r.orcid == SHARED_ORCID for r in session.query(Researcher).all())
    assert SHARED_ORCID in output


def test_shared_orcid_does_not_duplicate_publications(run_loader):
    """Each paper lands on the named researcher, once."""

    session, _ = run_loader(
        [staff("Christine Brown", SHARED_ORCID), staff("Kym Brown", SHARED_ORCID)],
        [
            paper("Christine Brown", SHARED_ORCID, "Barrier exchange options"),
            paper("Kym Brown", SHARED_ORCID, "Banking crises"),
        ],
    )

    assert session.query(Publication).count() == 2

    by_name = {
        r.name: sorted(p.title for p in r.publications)
        for r in session.query(Researcher).all()
    }
    assert by_name == {
        "Christine Brown": ["Barrier exchange options"],
        "Kym Brown": ["Banking crises"],
    }


def test_unique_orcid_still_matches_publications(run_loader):
    """An ORCID that identifies one person is still used, even if the
    publication row spells the name differently."""

    session, _ = run_loader(
        [staff("Stephen Brown", "0000-0002-0210-4562")],
        [paper("S. Brown", "0000-0002-0210-4562", "Asset pricing")],
    )

    researcher = session.query(Researcher).one()
    assert researcher.name == "Stephen Brown"
    assert [p.title for p in researcher.publications] == ["Asset pricing"]


def test_same_person_listed_twice_is_deduplicated(run_loader):
    """The original dedupe behaviour is unchanged for a genuine repeat."""

    session, _ = run_loader(
        [
            staff("Stephen Brown", "0000-0002-0210-4562"),
            staff("Stephen Brown", "0000-0002-0210-4562"),
        ],
        [paper("Stephen Brown", "0000-0002-0210-4562", "Asset pricing")],
    )

    assert session.query(Researcher).count() == 1
