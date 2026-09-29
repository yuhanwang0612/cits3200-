"""The pages in a real browser - CITS3200 Group 20.

    pip install playwright && playwright install chromium
    python load.py
    python -m pytest tests/test_site_browser.py -q

Acceptance Test C is about what a person sees: the filters, the sorting, the
pager, the CSV button. `test_site_api.py` proves the API answers correctly,
which is not the same thing, because the table is drawn by JavaScript and a
page can ask the right question and still show the wrong answer.

Skips when Playwright is not installed or the database has not been built,
so it is optional for everyone else and green in a plain checkout.
"""

import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "site" / "research.db"
playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright is not installed")
expect = playwright_api.expect
expect.set_options(timeout=10_000)
pytestmark = pytest.mark.skipif(not DB.exists(), reason="run python load.py first")

PORT = 5051
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server():
    """The real app on a spare port, in a thread, shut down afterwards."""
    from werkzeug.serving import make_server
    import app as site

    httpd = make_server("127.0.0.1", PORT, site.app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield BASE
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def page(server):
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text)
                if m.type == "error" else None)
        yield page
        browser.close()


def settle(page):
    """Wait for the table to be redrawn.

    Every filter change starts a fetch and the table is redrawn when it
    lands, so a fixed sleep is either slow or flaky. The assertions below use
    expect(), which retries for up to ten seconds; this is only for the steps
    where there is nothing specific to wait for.
    """
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def rows(page):
    return page.locator("#tbody tr").count()


def cell(page, row, column):
    return page.locator("#tbody tr").nth(row).inner_text().split("\t")[column]


def column(page, index):
    """Every value in one column, read in a single call. Reading row by row
    races with the table being redrawn underneath."""
    return [text.split("\t")[index] for text in
            page.locator("#tbody tr").all_inner_texts()]


def total(page):
    """The 'of N' figure from the pager.

    Comparing the pager's whole text does not work: inner_text() keeps the
    line breaks between the pagination buttons, expect().to_have_text()
    normalises whitespace differently, and two identical tables then compare
    unequal. The number is the thing being asserted anyway.
    """
    found = re.search(r"of\s*([\d,]+)", page.locator("#pager").inner_text())
    return int(found.group(1).replace(",", "")) if found else None


def wait_for_total(page, expected, timeout=10_000):
    deadline = timeout
    seen = None
    while deadline > 0:
        seen = total(page)
        if seen == expected:
            return seen
        page.wait_for_timeout(250)
        deadline -= 250
    return seen


def wait_for_column(page, index, expected, timeout=10_000):
    """Poll one column until it holds exactly `expected`, then return it.

    expect() retries, but only on the thing asserted. Asserting on the pager
    and then reading a column in the next statement can read the table
    mid-redraw, which makes a passing app look broken. This waits on the
    column itself.
    """
    deadline = timeout
    seen = None
    while deadline > 0:
        seen = set(column(page, index))
        if seen == expected:
            return seen
        page.wait_for_timeout(250)
        deadline -= 250
    return seen


# --------------------------------------------------------------- the pages

@pytest.mark.parametrize("path", ["/", "/researchers.html", "/universities.html",
                                  "/documentation.html"])
def test_each_page_renders_without_a_script_error(page, server, path):
    page.errors.clear()
    page.goto(server + path, wait_until="networkidle")
    page.wait_for_timeout(500)
    assert page.errors == []


# --------------------------------------------------------------- researchers

def test_the_table_fills_and_the_pager_agrees_with_it(page, server):
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    assert rows(page) > 0
    assert "Showing" in page.locator("#pager").inner_text()


def test_filtering_by_university_shows_only_that_university(page, server):
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.select_option("#f-uni", "UNSW Sydney")
    page.wait_for_timeout(800)
    expect(page.locator("#tbody tr").first).to_contain_text("UNSW Sydney")
    assert wait_for_column(page, 4, {"UNSW Sydney"}) == {"UNSW Sydney"}


def test_filters_combine_and_reset_clears_them(page, server):
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    everyone = total(page)
    page.select_option("#f-uni", "UNSW Sydney")
    settle(page)
    page.select_option("#f-level", "E")
    # Known failure. Waits on the level column itself, not on the pager, so
    # this is not the table merely being slow: on 29 Sep it held C, D and E
    # for the full ten seconds because ?university=UNSW+Sydney was answered
    # at 19:01:24, after ?university=UNSW+Sydney&level=E at 19:01:23, and
    # overwrote it. Timing-dependent, so it can pass when run alone.
    # See docs/TEST_PLAN.md, issue 7.
    assert wait_for_column(page, 3, {"E"}) == {"E"}
    assert total(page) < everyone, "two filters did not narrow the list"
    page.click("#f-reset")
    assert wait_for_total(page, everyone) == everyone, "reset did not restore every row"


def test_the_name_search_narrows_to_the_person_typed(page, server):
    """Typed key by key, the way a person does it, not filled in one go."""
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.type("#f-name", "trotman", delay=60)
    settle(page)
    page.wait_for_timeout(700)
    # Known failure: the page sends one request per keystroke and draws
    # whichever answer arrives last. Confirmed on 29 Sep by the server log:
    # ?name=trotman answered at 18:49:40, ?name=t answered at 18:49:41, and
    # the table kept the 50 rows for "t". See docs/TEST_PLAN.md, issue 7.
    assert rows(page) == 1
    assert "Trotman" in cell(page, 0, 1)


def test_clicking_a_column_header_sorts_both_ways(page, server):
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.locator("thead th").nth(1).click()
    page.wait_for_timeout(400)
    ascending = cell(page, 0, 1)
    page.locator("thead th").nth(1).click()
    page.wait_for_timeout(400)
    descending = cell(page, 0, 1)
    assert ascending != descending
    assert ascending.lower() < descending.lower()


def test_the_page_size_and_the_pager_work(page, server):
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.select_option("#f-size", "25")
    expect(page.locator("#tbody tr")).to_have_count(25)
    first_page = cell(page, 0, 1)
    page.get_by_text("Next", exact=False).first.click()
    settle(page)
    assert cell(page, 0, 1) != first_page


def test_the_csv_download_matches_what_is_on_screen(page, server):
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.select_option("#f-uni", "UNSW Sydney")
    page.wait_for_timeout(800)
    with page.expect_download() as download:
        page.click("#btn-export")
    path = download.value.path()
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Researcher,University")
    assert all("UNSW Sydney" in line for line in lines[1:])


# --------------------------------------------------------------- universities

def test_changing_the_metric_re_sorts_the_rankings(page, server):
    page.goto(server + "/universities.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    order = lambda: [page.locator("tbody tr").nth(i).inner_text().split("\t")[1]
                     for i in range(3)]
    by_count = order()
    page.select_option("#rank-by", "avg_jif")
    page.click("#update")
    page.wait_for_timeout(500)
    assert order() != by_count, "the table did not re-sort on average JIF"


def test_a_university_links_through_to_its_researchers(page, server):
    page.goto(server + "/universities.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.locator("tbody tr a").first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    assert "researchers.html" in page.url
    assert len(set(column(page, 4))) == 1


# --------------------------------------------------------------- one researcher

def test_the_researcher_page_shows_the_same_count_as_the_list(page, server):
    page.goto(server + "/researchers.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.type("#f-name", "trotman", delay=60)
    settle(page)
    page.wait_for_timeout(700)
    listed = int(cell(page, 0, 10))
    page.locator("#tbody tr a").first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    assert f"{listed} publication" in page.inner_text("body")
