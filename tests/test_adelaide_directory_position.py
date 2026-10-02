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
