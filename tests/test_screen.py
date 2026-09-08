"""Tests for the discipline screen — CITS3200 Group 20.

    python -m pytest tests/test_screen.py -q

The numbers in these fixtures are the real UNSW ones. Suk Lee is an actual
finance lecturer whose recovered ORCID belonged to a medical physicist;
Moshirian and Gordon Phillips are the two legitimate cases a volume guard
would have thrown away with him.
"""

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import screen as sc                                      # noqa: E402


def pub(name, source="ORCID", abdc=None, journal="A Journal", title="T",
        kind="Journal Article"):
    """A journal article in a named journal unless told otherwise, because that
    is the only shape the screen forms a judgement from."""
    return {"name": name, "source": source, "abdc": abdc, "journal": journal,
            "title": title, "year": "2020", "doi": None, "type": kind}


def person(name, orcid="0000-0002-1234-5678"):
    return {"name_clean": name, "orcid": orcid, "openalex_author_ids": ["A1"]}


def contaminated(name="Suk Lee", n=57):
    """A namesake's career: nothing in an ABDC journal."""
    return ([pub(name, "UNSW staff profile", abdc="A*",
                 journal="Journal of Financial Economics")]
            + [pub(name, journal="Journal of the Korean Physical Society")
               for _ in range(n)])


def legitimate(name="Fariborz Moshirian", listed=13, retrieved=79, rated=78):
    return ([pub(name, "UNSW staff profile", abdc="A") for _ in range(listed)]
            + [pub(name, abdc="A*", journal="Journal of Banking & Finance")
               for _ in range(rated)]
            + [pub(name, journal="Some Unrated Outlet")
               for _ in range(retrieved - rated)])


# ------------------------------------------------------------ the judgement

def test_a_researcher_whose_retrieved_work_is_all_out_of_discipline_is_flagged():
    stats = sc.summarise(contaminated())
    assert sc.suspect(stats["Suk Lee"])


def test_a_researcher_whose_retrieved_work_is_in_discipline_is_not():
    stats = sc.summarise(legitimate())
    assert not sc.suspect(stats["Fariborz Moshirian"])


def test_a_researcher_the_university_lists_nothing_for_is_not_flagged():
    """Gordon Phillips has 0 publications on his UNSW page and 32 retrieved,
    all in finance journals. A volume guard rejects him; the discipline test
    keeps him, which is the right answer."""
    pubs = [pub("Gordon Phillips", abdc="A*", journal="The Journal of Finance")
            for _ in range(31)] + [pub("Gordon Phillips", journal="unknown")]
    assert not sc.suspect(sc.summarise(pubs)["Gordon Phillips"])


def test_volume_alone_would_get_this_wrong():
    """The measured reason for not using a ratio: the contaminated case and the
    legitimate case have almost the same shape by volume."""
    suk = sc.summarise(contaminated())["Suk Lee"]
    mosh = sc.summarise(legitimate())["Fariborz Moshirian"]
    assert suk["retrieved"] / max(suk["listed"], 1) > 50     # 57 vs 1
    assert mosh["retrieved"] / max(mosh["listed"], 1) > 5    # 79 vs 13
    assert suk["share"] == 0 and mosh["share"] > 0.9         # what separates them


def test_a_few_retrieved_rows_are_not_enough_to_judge():
    """Three retrieved papers, one in a medical journal, is a co-authorship,
    not a wrong ORCID."""
    pubs = [pub("Someone", "UNSW staff profile", abdc="A")] + \
           [pub("Someone") for _ in range(sc.MIN_ROWS - 1)]
    assert not sc.suspect(sc.summarise(pubs)["Someone"])


def test_a_researcher_with_no_retrieved_rows_at_all_is_never_flagged():
    pubs = [pub("Someone", "UNSW staff profile") for _ in range(10)]
    entry = sc.summarise(pubs)["Someone"]
    assert entry["share"] is None and not sc.suspect(entry)


@pytest.mark.parametrize("source", ["ORCID", "Crossref", "OpenAlex"])
def test_every_retrieval_source_is_in_scope(source):
    pubs = [pub("X", "UNSW staff profile", abdc="A")] + \
           [pub("X", source) for _ in range(20)]
    assert sc.suspect(sc.summarise(pubs)["X"])


# --------------------------------------------------------------- the removal

def test_the_out_of_discipline_rows_are_removed():
    pubs = contaminated()
    kept = sc.screen([person("Suk Lee")], pubs, verbose=False)
    assert len(kept) == 1


def test_the_rows_the_university_listed_are_never_touched():
    """Those came from the institution's own record of its own staff. If they
    are wrong that is a different problem, and deleting them hides it."""
    kept = sc.screen([person("Suk Lee")], contaminated(), verbose=False)
    assert [r["source"] for r in kept] == ["UNSW staff profile"]
    assert kept[0]["journal"] == "Journal of Financial Economics"


def test_a_legitimate_researcher_loses_nothing():
    pubs = legitimate()
    before = len(pubs)
    kept = sc.screen([person("Fariborz Moshirian")], pubs, verbose=False)
    assert len(kept) == before


def test_one_bad_researcher_does_not_affect_another():
    pubs = contaminated() + legitimate()
    kept = sc.screen([person("Suk Lee"), person("Fariborz Moshirian")],
                     pubs, verbose=False)
    assert sum(1 for r in kept if r["name"] == "Suk Lee") == 1
    assert sum(1 for r in kept if r["name"] == "Fariborz Moshirian") == 92


def test_nothing_to_do_returns_the_same_list():
    pubs = legitimate()
    assert sc.screen([person("Fariborz Moshirian")], pubs, verbose=False) is pubs


# ------------------------------------------------------------- the aftermath

def test_the_wrong_orcid_is_taken_off_the_staff_record():
    """The ORCID is the thing that was wrong. Leaving it on the staff row puts
    it in the merged table and invites the next person to trust it."""
    records = [person("Suk Lee")]
    sc.screen(records, contaminated(), verbose=False)
    assert records[0]["orcid"] is None
    assert records[0]["openalex_author_ids"] == []


def test_a_good_researchers_orcid_survives():
    records = [person("Fariborz Moshirian")]
    sc.screen(records, legitimate(), verbose=False)
    assert records[0]["orcid"] == "0000-0002-1234-5678"


def test_everything_removed_is_written_out_with_a_reason(tmp_path):
    """The judgement is a heuristic, so a human has to be able to overrule it."""
    sc.screen([person("Suk Lee")], contaminated(), out_dir=tmp_path,
              verbose=False)
    with (tmp_path / f"{tmp_path.name}_screened_out.csv").open(
            newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 57
    assert all(r["name"] == "Suk Lee" for r in rows)
    assert "ABDC" in rows[0]["screened_reason"]
    assert "0%" in rows[0]["screened_reason"]


def test_no_file_is_written_when_nothing_is_removed(tmp_path):
    sc.screen([person("Fariborz Moshirian")], legitimate(), out_dir=tmp_path,
              verbose=False)
    assert not (tmp_path / f"{tmp_path.name}_screened_out.csv").exists()


# ------------------------------------------------------------ the threshold

def test_the_threshold_sits_in_the_measured_gap():
    """Measured on the real UNSW run: contaminated 0%, lowest legitimate 60%
    (Peter Swan, 3 of 5). Anywhere in between works, which is what makes this
    a rule rather than a guess."""
    assert 0 < sc.MIN_ABDC_SHARE < 0.60


# ----------------------------------------------- what "could be rated" means

def unrateable(name, source="ORCID", n=1, journal=None, kind="Preprint"):
    """Rows ABDC could never rate: no journal name, or not a journal article.
    Most of what the retrieval steps add looks like this."""
    return [pub(name, source, journal=journal, kind=kind) for _ in range(n)]


def rateable_rows(name, source="ORCID", n=1, abdc="A*"):
    return [pub(name, source, abdc=abdc, journal="Journal of Finance")
            for _ in range(n)]


def test_a_preprint_with_no_journal_is_not_evidence_of_anything():
    """The bug this replaced: 96 of Peter Swan's 101 retrieved rows had no
    journal name, so they could not be ABDC-rated by construction. Counting
    them as out-of-discipline flagged 23 researchers instead of one."""
    pubs = ([pub("Peter Swan", "UNSW staff profile", abdc="A*") for _ in range(132)]
            + unrateable("Peter Swan", n=96)
            + rateable_rows("Peter Swan", n=3)
            + [pub("Peter Swan", journal="Some Unrated Outlet") for _ in range(2)])
    entry = sc.summarise(pubs)["Peter Swan"]
    assert entry["retrieved"] == 101      # what a naive count would judge on
    assert entry["judged"] == 5           # what can actually be judged
    assert entry["share"] == 0.6
    assert not sc.suspect(entry)


def test_a_book_chapter_in_a_named_outlet_is_still_not_judged():
    """A journal name is necessary but not sufficient: the row has to be the
    kind of thing ABDC rates."""
    assert not sc.rateable({"journal": "Some Book", "type": "Book Chapter"})
    assert not sc.rateable({"journal": None, "type": "Journal Article"})
    assert sc.rateable({"journal": "Journal of Finance", "type": "Journal Article"})


def test_a_researcher_with_only_unrateable_rows_is_never_flagged():
    """Lili Dai: 27 retrieved rows, every one of them without a journal name.
    Zero evidence is not evidence of contamination."""
    pubs = ([pub("Lili Dai", "UNSW staff profile", abdc="A") for _ in range(19)]
            + unrateable("Lili Dai", n=27))
    entry = sc.summarise(pubs)["Lili Dai"]
    assert entry["judged"] == 0 and entry["share"] is None
    assert not sc.suspect(entry)


def test_one_bad_rateable_row_is_below_the_evidence_floor():
    """Mark Humphery-Jenner: 46 retrieved rows, 45 with no journal and one in a
    repository. One row is not a pattern."""
    pubs = ([pub("MHJ", "UNSW staff profile", abdc="A") for _ in range(405)]
            + unrateable("MHJ", n=45)
            + [pub("MHJ", journal="Zurich Open Repository")])
    assert not sc.suspect(sc.summarise(pubs)["MHJ"])


def test_the_contaminated_case_still_fires_after_all_that():
    """Suk Lee's rows are journal articles in real, named journals. They are
    rateable; they are simply in physics."""
    pubs = ([pub("Suk Lee", "UNSW staff profile", abdc="A*",
                  journal="Journal of Financial Economics")]
            + [pub("Suk Lee", journal="Journal of the Korean Physical Society")
               for _ in range(27)]
            + unrateable("Suk Lee", n=30))
    entry = sc.summarise(pubs)["Suk Lee"]
    assert entry["judged"] == 27 and entry["share"] == 0
    assert sc.suspect(entry)
    kept = sc.screen([person("Suk Lee")], pubs, verbose=False)
    # Everything retrieved goes, preprints included: if the ORCID is wrong,
    # everything that came from it is wrong, whether or not it was judgeable.
    assert len(kept) == 1 and kept[0]["source"] == "UNSW staff profile"
