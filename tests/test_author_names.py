"""Full author names, one shape for all eight universities.

    python -m pytest tests/test_author_names.py -q

The client's words, 2 October: "in publication list, not full name being
recorded, and thus the information is insufficient for me to process and link
to other EXCEL", and separately that the data "has not been uniformed /
standardized".

Both are the same bug. Every adapter scrapes an author list in its own shape,
2,696 rows carried initials only, and enrichment/openalex.py was fetching the
full names on every run and dropping them because the column was already
non-empty.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from enrichment.openalex import (                                  # noqa: E402
    name_fullness, prefer_fuller_authors, extract,
)


# ------------------------------------------------ the real shapes, per uni

INITIALS = [
    "Ang NP; Trotman KT",                          # unsw
    "Stone, G.; Fiedler, B.; Kandunias, C.",       # adelaide
    "Chen, Z; Lu, AY; Yang, Z",                    # unimelb
    "Salignac F, Wilcox T, Marjolin A and Adams S",  # anu
]
FULL = [
    "Nicole Ang; Ken T. Trotman",
    "Sudipta Bose; Maria C. A. Balatbat; Wendy Green",
    "Bin Li; Xinze Xu; Bo Qin",                    # uwa
    "Nouyrigat, Genevieve; Humphrey, Jacquelyn E.",  # uq, full but back to front
]


# Each pair is the same author list in the two shapes, so the comparison is
# like for like. Zipping INITIALS against FULL compared unrelated lists and
# could tie by accident: ANU's four initials score the same as UQ's two full
# names.
SAME_LIST_BOTH_WAYS = [
    ("Ang NP; Trotman KT",                    "Nicole Ang; Ken T. Trotman"),
    ("Stone, G.; Fiedler, B.; Kandunias, C.", "Gregory Stone; Brett Fiedler; Chris Kandunias"),
    ("Chen, Z; Lu, AY; Yang, Z",              "Zhuo Chen; Alice Y. Lu; Zheng Yang"),
    ("Salignac F, Wilcox T and Adams S",      "Fanny Salignac; Tamara Wilcox; Sam Adams"),
]


def test_initials_score_lower_than_the_same_list_written_out():
    for short, long in SAME_LIST_BOTH_WAYS:
        assert name_fullness(short) < name_fullness(long), short


def test_openalex_wins_every_one_of_those_pairs():
    for short, long in SAME_LIST_BOTH_WAYS:
        assert prefer_fuller_authors(short, long) == long


def test_openalex_replaces_an_initials_only_list():
    assert prefer_fuller_authors("Ang NP; Trotman KT",
                                 "Nicole Ang; Ken T. Trotman") == \
        "Nicole Ang; Ken T. Trotman"


def test_a_tie_goes_to_openalex_so_the_shape_is_the_same_everywhere():
    """UQ already has given names, but back to front. The client joins across
    eight universities, so one shape matters as much as one name."""
    assert prefer_fuller_authors("Nouyrigat, Genevieve; Humphrey, Jacquelyn E.",
                                 "Genevieve Nouyrigat; Jacquelyn E. Humphrey") == \
        "Genevieve Nouyrigat; Jacquelyn E. Humphrey"


def test_a_truncated_openalex_list_does_not_win():
    """The guard against trading ten scraped authors for three."""
    scraped = ("Bin Li; Xinze Xu; Bo Qin; Xing Yang; Wei Chen; Jia Liu")
    assert prefer_fuller_authors(scraped, "Bin Li; Xinze Xu") == scraped


def test_an_empty_side_never_wins():
    assert prefer_fuller_authors("", "Nicole Ang") == "Nicole Ang"
    assert prefer_fuller_authors("Nicole Ang", "") == "Nicole Ang"
    assert prefer_fuller_authors("", "") == ""
    assert prefer_fuller_authors(None, None) is None


# ------------------------------------------------ what extract() returns

def work(*names):
    return {"authorships": [{"author": {"display_name": n},
                             "author_position": p}
                            for n, p in zip(names, ["first"] + ["middle"] * 9)]}


def test_extract_joins_on_semicolon_and_keeps_the_given_order():
    """author_position is first/middle/last, not an index, so the order the
    authorships arrive in is the author order."""
    out = extract(work("Sudipta Bose", "Maria C. A. Balatbat", "Wendy Green"))
    assert out["authors"] == "Sudipta Bose; Maria C. A. Balatbat; Wendy Green"
    assert out["n_authors"] == 3


def test_extract_drops_authors_with_no_name():
    w = {"authorships": [{"author": {"display_name": "Nicole Ang"}},
                         {"author": {}},
                         {"author": {"display_name": None}}]}
    out = extract(w)
    assert out["authors"] == "Nicole Ang"
    assert out["n_authors"] == 1


def test_a_work_with_no_authors_yields_none():
    assert extract({"authorships": []})["authors"] is None
