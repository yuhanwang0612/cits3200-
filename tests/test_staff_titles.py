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
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export import (                                               # noqa: E402
    ACADEMIC_TITLE_BY_LEVEL, academic_title_for_level, admin_title_from,
)


# ------------------------------------------------ academic title

def test_the_mapping_is_exactly_what_the_client_specified():
    assert ACADEMIC_TITLE_BY_LEVEL == {
        "B": "Dr", "C": "Dr",
        "D": "Associate Professor",
        "E": "Professor",
    }


def test_b_and_c_both_read_dr():
    assert academic_title_for_level("B") == academic_title_for_level("C") == "Dr"


def test_d_and_e():
    assert academic_title_for_level("D") == "Associate Professor"
    assert academic_title_for_level("E") == "Professor"


def test_level_a_is_left_blank_rather_than_guessed():
    """The spec says B-E. 8 people sit at A (UNSW 1, USyd 6, UWA 1) and an
    Associate Lecturer may hold no doctorate, so "Dr" would be invented.
    Blank until the client answers."""
    assert academic_title_for_level("A") is None


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
