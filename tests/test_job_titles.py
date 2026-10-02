"""core.titles.split_job_title: academic rank in job_title, role in admin_title."""

import pytest

from core.titles import rank, level, split_job_title


@pytest.mark.parametrize("title, level_code, job, admin", [
    # rank buried in the title, any case
    ("Sydney Horizon Fellow (Senior Lecturer) University of Sydney Business School", "C", "Senior Lecturer", None),
    ("Associate Lecturer in Finance (Education Focused)", "A", "Associate Lecturer", None),
    ("ASSOCIATE PROFESSOR", "D", "Associate Professor", None),
    ("SENIOR RESEARCH FELLOW", "C", "Senior Research Fellow", None),
    ("Senior Lecturer in Finance", "C", "Senior Lecturer", None),
    # special professorships
    ("Scientia Professor", "E", "Professor", None),
    ("GL Wood Chair of Accounting", "E", "Professor", None),
    ("Emeritus Professor Honorary", "E", "Emeritus Professor", None),
    # leadership: rank from the level, role into admin_title
    ("Dean, School of Accounting and Finance", "E", "Professor", "Dean"),
    ("Head of School, School of Finance", "E", "Professor", "Head of School"),
    ("Deputy Head of School - Research", "E", "Professor", "Deputy Head of School"),
    ("Joint Deputy Head of Department (Research and Engagement)", "E", "Professor", "Deputy Head of Department"),
    ("Head of Department - Accounting & Finance", "D", "Associate Professor", "Head of Department"),
    # "dean" in the role must not make a Level D person a Professor
    ("Associate Dean (International)", "D", "Associate Professor", "Associate Dean"),
    ("Assistant Dean (Graduate Research)", "D", "Associate Professor", "Assistant Dean"),
    ("Director, Research School of Accounting", "E", "Professor", "Director"),
    ("Deputy Director (Education)", "D", "Associate Professor", "Deputy Director"),
    # role and rank in the same title
    ("Program Director Senior Lecturer", "C", "Senior Lecturer", "Program Director"),
    ("Honours Coordinator Associate Professor", "D", "Associate Professor", "Honours Coordinator"),
    ("Joint PhD Program Director (Finance)", "D", "Associate Professor", "PhD Program Director"),
    ("Discipline Convenor, Finance", "D", "Associate Professor", "Discipline Convenor"),
    ("Research Hub Co Leader", "E", "Professor", "Research Hub Co-Leader"),
    # a role with no level: the rank is unknown, not the role
    ("Deputy Head of School (Education)", None, None, "Deputy Head of School"),
    ("Program Director", None, None, "Program Director"),
    # affiliate appointments keep their qualifier
    ("ADJUNCT PROFESSOR", "E", "Adjunct Professor", None),
    ("Adjunct Senior Lecturer", "C", "Adjunct Senior Lecturer", None),
    ("Honorary Associate Professor", "D", "Honorary Associate Professor", None),
    ("Principal Fellow Honorary", None, "Honorary Principal Fellow", None),
    # teaching roles with no rank and no level stay as listed
    ("Teaching Fellow", None, "Teaching Fellow", None),
    ("Tutor - Education Focussed", None, "Tutor - Education Focussed", None),
    # a department name scraped into the title field falls back to the level
    ("Research and Executive Education", "E", "Professor", None),
    # already standard, and empty
    ("Lecturer", "B", "Lecturer", None),
    ("", "B", "", None),
    (None, "B", None, None),
])
def test_split_job_title(title, level_code, job, admin):
    assert split_job_title(title, level_code) == (job, admin)


def test_assistant_professor_is_a_lecturer_not_a_professor():
    assert rank("Assistant Professor") == "Lecturer"
    assert level(rank("Assistant Professor")) == "B"
    assert split_job_title("Assistant Professor", "B") == ("Lecturer", None)
