"""export.normalize_authors: one author-list format, "Given Surname; Given Surname"."""

import pytest

from export import normalize_authors


@pytest.mark.parametrize("raw, expected", [
    # Monash Pure / ANU profile citations
    ("van Mourik, G., Watson, J. & Onsman, A.", "G. van Mourik; J. Watson; A. Onsman"),
    ("Tam, K & Ho, S", "K Tam; S Ho"),
    ("Chan, C. C. A., Monroe, G. M., Ng, J. and Tan, R. C. W",
     "C. C. A. Chan; G. M. Monroe; J. Ng; R. C. W Tan"),
    ("K.C. Ho, A. Karathanasopoulos, & J. Yu", "K.C. Ho; A. Karathanasopoulos; J. Yu"),
    ("Xuejun Jiang, Jeong-Bong Kim and Yangxin Yu", "Xuejun Jiang; Jeong-Bong Kim; Yangxin Yu"),
    ("with Tekathen, M. and Bui, B", "M. Tekathen; B Bui"),
    ("Mahama, H", "H Mahama"),
    # UWA BibTeX leftovers
    ("Lyndie Bayne; Wee, \Marvin Ge Way\\", "Lyndie Bayne; Marvin Ge Way Wee"),
    ("Smales, \Lee Alan\\", "Lee Alan Smales"),
    # surname-first entries in a semicolon list
    ("Stone, G.; Fiedler, B.", "G. Stone; B. Fiedler"),
    # already standard: unchanged
    ("Nicole Ang; Mandy Man-sum Chen", "Nicole Ang; Mandy Man-sum Chen"),
    ("Yuan (Helen) Ping", "Yuan (Helen) Ping"),
    ("", ""),
    (None, None),
])
def test_normalize_authors(raw, expected):
    assert normalize_authors(raw) == expected


@pytest.mark.parametrize("raw, count, expected", [
    ("Slapnicar, Sergeja", 1, "Sergeja Slapnicar"),
    ("Peters, Matthew Damon", "1", "Matthew Damon Peters"),
    ("Do, Truc (Peter)", 1, "Truc (Peter) Do"),
    ("K.C. Ho, A. Karathanasopoulos", 2, "K.C. Ho; A. Karathanasopoulos"),
])
def test_a_sole_author_in_surname_given_form_is_one_person(raw, count, expected):
    assert normalize_authors(raw, count) == expected
