"""Academic Title and Admin Title - CITS3200 Group 20.

    python -m pytest tests/test_staff_titles.py -q

The client's own words, 2 October:

    "For each individual researcher, it can have a column Academic Level
    (B-E), Academic Title and Admin Title. Usually the Academic Level
    determines the Academic Title. It means, both level BC calls 'Dr', level
    D is 'Associate Professor', E js 'Professor'. You could put Admin Title
    as a separate column additional info, because it cannot be inferred..."

and, on Sean's mapping table the week before, "Yes. Note that these are
administrative title, not academic title."

The team then chose the academic rank over "Dr" for B and C ("Dr" is a
qualification), so Academic Title reads Lecturer / Senior Lecturer there.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export import (                                               # noqa: E402
    ACADEMIC_TITLE_BY_LEVEL, academic_title_for_level, admin_title_from,
)


# ------------------------------------------------ academic title

def test_each_level_reads_as_its_rank():
    assert ACADEMIC_TITLE_BY_LEVEL == {
        "A": "Associate Lecturer",
        "B": "Lecturer", "C": "Senior Lecturer",
        "D": "Associate Professor",
        "E": "Professor",
    }


def test_b_and_c_are_ranks_not_dr():
    assert academic_title_for_level("B") == "Lecturer"
    assert academic_title_for_level("C") == "Senior Lecturer"


def test_the_job_titles_own_rank_wins_at_the_same_level():
    assert academic_title_for_level("C", "Senior Research Fellow") == "Senior Research Fellow"
    assert academic_title_for_level("E", "Emeritus Professor") == "Emeritus Professor"
    assert academic_title_for_level("C", "Senior Lecturer in Finance") == "Senior Lecturer"


def test_a_job_title_at_another_level_does_not_override_the_level():
    """A staff override can set the level when the title has no rank, or
    disagree with it; the level decides."""
    assert academic_title_for_level("E", "Dean, School of Accounting") == "Professor"
    assert academic_title_for_level("C", "Program Director") == "Senior Lecturer"


def test_d_and_e():
    assert academic_title_for_level("D") == "Associate Professor"
    assert academic_title_for_level("E") == "Professor"


def test_level_a_reads_associate_lecturer():
    """With ranks rather than "Dr", level A has an honest title too."""
    assert academic_title_for_level("A") == "Associate Lecturer"


def test_a_missing_level_gives_no_title():
    """32 researchers across five universities have no academic_level, so
    they get no academic title either. That is the hole the client will see,
    and it is those universities' data to fix, not something to paper over."""
    for value in ("", None, "   ", "Professor"):
        assert academic_title_for_level(value) is None


def test_the_level_is_read_case_and_space_insensitively():
    assert academic_title_for_level(" e ") == "Professor"


# ------------------------------------------------ admin title

def test_seans_approved_mappings():
    """Every row of the table the client said yes to."""
    for raw, expected in [
        ("Dean, School of Accounting and Finance",   "Dean"),
        ("Associate Dean (Global Engagement)",       "Associate Dean"),
        ("Associate Dean (International)",           "Associate Dean"),
        ("Assistant Dean (Graduate Research)",       "Assistant Dean"),
        ("Head of School",                           "Head of School"),
        ("Head of School, School of Finance",        "Head of School"),
        ("Deputy Head of School - Research",         "Deputy Head of School"),
        ("Deputy Head of School - Education",        "Deputy Head of School"),
        ("Deputy Head of School (Education)",        "Deputy Head of School"),
        ("Head of Department",                       "Head of Department"),
        ("Head of Department - Accounting & Finance", "Head of Department"),
        ("Deputy Head of Department",                "Deputy Head of Department"),
        ("Joint Deputy Head of Department (Research and Engagement)",
                                                     "Deputy Head of Department"),
        # The approved table gave "Deputy Head of Department" here; the role
        # rules now keep every role in the title, so the second one stays too.
        ("Joint Deputy Head of Department (Teaching and Learning), "
         "Finance Major Coordinator",                "Deputy Head of Department; Major Coordinator"),
    ]:
        assert admin_title_from(raw) == expected, raw


def test_a_deputy_is_never_shortened_to_a_head():
    """Ordering trap: 'Deputy Head of School' contains 'Head of School', and
    'Associate Dean' contains 'Dean'. Match the longest role first or every
    deputy is promoted."""
    assert admin_title_from("Deputy Head of School") == "Deputy Head of School"
    assert admin_title_from("Associate Dean") == "Associate Dean"
    assert admin_title_from("Deputy Head of Department") == "Deputy Head of Department"


def test_an_academic_rank_is_not_an_admin_title():
    for rank in ("Professor", "Associate Professor", "Senior Lecturer",
                 "Lecturer", "Research Professor"):
        assert admin_title_from(rank) is None, rank


def test_the_roles_actually_in_the_data():
    assert admin_title_from("Program Director") == "Program Director"
    assert admin_title_from("Director, Research School of Accounting") == "Director"


def test_a_blank_job_title_has_no_admin_title():
    for value in ("", "   ", None):
        assert admin_title_from(value) is None


# ------------------------------------------------ FR4: teaching-focused staff

import pytest                                                      # noqa: E402
from export import export, is_teaching_role                        # noqa: E402


@pytest.mark.parametrize("title", [
    "Lecturer (Education Focused)", "Senior Lecturer - Education Focussed",
    "Lecturer in Audit (Teaching Focused)", "Associate Professor of Finance (Education Focused)",
    "Tutor - Education Focussed", "Teaching Fellow", "Teaching Associate",
    "Teaching Specialist", "Casual Teaching Lecturer", "P/T Tchg Lecturer"])
def test_teaching_focused_titles_are_recognised(title):
    assert is_teaching_role(title)


@pytest.mark.parametrize("title", [
    "Senior Lecturer", "Professor", "Associate Dean (Teaching and Learning)",
    "Head of School", "Senior Research Fellow", "Lecturer in Finance", None, ""])
def test_research_and_admin_titles_are_kept(title):
    assert not is_teaching_role(title)


def _record(name, title):
    return {"name_clean": name, "title": title, "university": "University of Sydney",
            "discipline": "Finance", "profile_url": "https://x"}


def _paper(name, doi):
    return {"type": "Journal Article", "name": name, "title": f"Paper {doi}",
            "doi": doi, "year": "2020", "journal": "Accounting Review"}


def test_export_drops_teaching_staff_and_their_papers(tmp_path):
    records = [_record("Ann Research", "Senior Lecturer"),
               _record("Ted Teach", "Lecturer (Education Focused)")]
    pubs = [_paper("Ann Research", "10.1/a"), _paper("Ted Teach", "10.1/t")]
    tables = export(records, pubs, out_dir=tmp_path / "usyd", verbose=False)
    assert [s["name"] for s in tables["staff"]] == ["Ann Research"]
    assert [p["name"] for p in tables["publications"]] == ["Ann Research"]


def test_an_override_fills_the_level_when_the_title_names_no_rank(tmp_path):
    """Stuart Black: 'Enterprise Fellow in data, analytics, disruption and
    innovation' has no rank word; staff_overrides.csv says Assistant Professor."""
    records = [{"name_clean": "Stuart Black", "university": "University of Melbourne",
                "title": "Enterprise Fellow in data, analytics, disruption and innovation",
                "discipline": "Accounting", "profile_url": "https://x"}]
    pubs = [_paper("Stuart Black", "10.1/s")]
    staff = export(records, pubs, out_dir=tmp_path / "unimelb", verbose=False)["staff"]
    assert staff[0]["academic_level"] == "B"
    assert staff[0]["job_title"].startswith("Enterprise Fellow")


def test_a_staff_exclusion_drops_one_listing_and_its_papers(tmp_path):
    """Roger Simnett counts at Monash; his UNSW Emeritus listing is dropped."""
    unsw = [{"name_clean": "Roger Simnett", "title": "Emeritus Professor",
             "university": "UNSW Sydney", "discipline": "Accounting", "profile_url": "https://x"}]
    tables = export(unsw, [_paper("Roger Simnett", "10.1/r")], out_dir=tmp_path / "unsw", verbose=False)
    assert tables["staff"] == [] and tables["publications"] == []
    monash = [dict(unsw[0], name_clean="Roger Simnett", university="Monash University")]
    tables = export(monash, [_paper("Roger Simnett", "10.1/r")], out_dir=tmp_path / "monash", verbose=False)
    assert len(tables["staff"]) == 1
