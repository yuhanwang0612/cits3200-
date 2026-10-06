"""Adelaide: the People Directory fills an empty Researcher Profiles position."""

from types import SimpleNamespace

import pytest

pytest.importorskip("bs4")
from base_scrapers.adelaide import _directory_position


class FakeSession:
    def __init__(self, status, html):
        self.status, self.html = status, html

    def get(self, url, headers=None, timeout=None):
        return SimpleNamespace(status_code=self.status, text=self.html)


def page(title):
    return f"<html><head><title>{title}</title></head><body></body></html>"


@pytest.mark.parametrize("title, expected", [
    ("Prof Carol Tilt,  Adjunct Research Professor  | Adelaide University People Directory",
     "Adjunct Research Professor"),
    ("Prof Lin Crase,  Dean, School of Accounting and Finance  | Adelaide University People Directory",
     "Dean, School of Accounting and Finance"),
    ("Mr Nicholas Marzohl,  Casual Employee (Prof Staff)  | Adelaide University People Directory",
     "Casual Employee (Prof Staff)"),
    # a page that states no position
    ("| Adelaide University People Directory", None),
])
def test_position_from_directory_title(title, expected):
    assert _directory_position(FakeSession(200, page(title)), "x") == expected


def test_missing_directory_page_gives_none():
    assert _directory_position(FakeSession(404, ""), "x") is None


# ------------------------------------------- research students are not staff

from bs4 import BeautifulSoup                                   # noqa: E402
from base_scrapers.adelaide import _is_research_student          # noqa: E402


def profile(body):
    return BeautifulSoup(f"<html><body><h1>Mr X</h1>{body}</body></html>", "html.parser")


def test_an_hdr_candidate_is_not_staff():
    soup = profile('<p class="u-lead-text hdr-desc">Higher Degree by Research Candidate</p>')
    assert _is_research_student(soup)


def test_a_staff_member_is_not_a_student():
    soup = profile('<p class="u-lead-text position">Senior Lecturer, Accounting</p>')
    assert not _is_research_student(soup)


def test_a_staff_member_also_enrolled_stays_staff():
    soup = profile('<p class="u-lead-text position">Lecturer</p>'
                   '<p class="u-lead-text hdr-desc">Higher Degree by Research Candidate</p>')
    assert not _is_research_student(soup)
