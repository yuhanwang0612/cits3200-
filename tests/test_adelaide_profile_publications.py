"""Adelaide publications come from each researcher's own profile page.

The markup is trimmed from https://researchers.adelaide.edu.au/profile/kartick.gupta.
Before this, Adelaide took publications only from ORCID-keyed indexes, so staff
without an ORCID on their profile (the Dean among them) had none at all.
"""

from bs4 import BeautifulSoup

from base_scrapers import adelaide


def section(heading, rows):
    return f"""
<div class="accordion-item"><div class="accordion-body">
  <h3 class="accordion-header" id="heading-"><button class="accordion-button collapsed" type="button">
    {heading}
  </button></h3>
  <div class="accordion-collapse collapse"><div class="accordion-body"><div class="table-responsive">
  <table class="c-table"><thead><tr><th>Year</th><th>Citation</th></tr></thead><tbody>{rows}</tbody></table>
  </div></div></div>
</div></div>"""


ARTICLE = """<tr><td>2025</td><td><a href="https://hdl.handle.net/11541.2/44299"><span>Banerjee, R., Gupta, K., Han, H. D., &amp; Krishnamurti, C. (2025). Do corrupt practices lead to increased cash holdings in firms? International evidence. <i>Pacific Basin Finance Journal</i>, <i>93</i>(102908), 15 pages.</span></a><br> <a href="http://dx.doi.org/10.1016/j.pacfin.2025.102908" class="js-hide-icon">DOI</a> <span class="altmetric-embed" data-doi="10.1016/j.pacfin.2025.102908"></span> <span class="citation-counts scopus">Scopus<span>1</span></span> <span class="citation-counts wos">WoS<span>1</span></span></td></tr>"""

QUESTION_TITLE = """<tr><td>2021</td><td><a href="https://hdl.handle.net/11541.2/147336"><span>Banerjee, R., &amp; Gupta, K. (2021). Do country or firm-specific factors matter more to R&amp;D spending in firms?. <i>International Review of Economics and Finance</i>, <i>76</i>, 1-9.</span></a><br> <a href="http://dx.doi.org/10.1016/j.iref.2021.05.003" class="js-hide-icon">DOI</a></td></tr>"""

NO_DOI = """<tr><td>2018</td><td><a href="https://hdl.handle.net/11541.2/116561"><span>Gupta, K. (2018). Environmental sustainability and implied cost of equity: international evidence. <i>Journal of business ethics</i>, <i>147</i>(2), 343-365.</span></a></td></tr>"""

CHAPTER = """<tr><td>2019</td><td><a href="https://hdl.handle.net/x"><span>Gupta, K. (2019). A chapter. In <i>Some Book</i>.</span></a></td></tr>"""


def parse(html):
    return adelaide._parse_profile_publications(
        BeautifulSoup(html, "html.parser"), "Kartick Gupta", "kartick.gupta")


def test_journal_article_is_read_from_the_citation():
    [row] = parse(section("Journals", ARTICLE))

    assert row["title"] == "Do corrupt practices lead to increased cash holdings in firms? International evidence"
    assert row["journal"] == "Pacific Basin Finance Journal"
    assert row["year"] == "2025"
    assert row["authors"] == "Banerjee, R.; Gupta, K.; Han, H. D.; Krishnamurti, C."
    assert row["n_authors"] == 4
    assert row["doi"] == "10.1016/j.pacfin.2025.102908"
    assert row["link"] == "https://hdl.handle.net/11541.2/44299"
    assert row["name"] == "Kartick Gupta"
    assert row["source_id"] == "kartick.gupta"
    assert row["type"] == "Journal Article"


def test_rows_are_official_so_the_screen_never_removes_them():
    import screen
    [row] = parse(section("Journals", ARTICLE))
    assert row["source"] == adelaide.PROFILE_SOURCE
    assert row["source"] not in screen.RETRIEVED


def test_question_mark_title_loses_the_citation_full_stop():
    [row] = parse(section("Journals", QUESTION_TITLE))
    assert row["title"] == "Do country or firm-specific factors matter more to R&D spending in firms?"


def test_entry_without_doi_keeps_its_repository_link():
    [row] = parse(section("Journals", NO_DOI))
    assert row["doi"] is None
    assert row["link"] == "https://hdl.handle.net/11541.2/116561"
    assert row["journal"] == "Journal of business ethics"


def test_only_the_journals_table_is_read():
    rows = parse(section("Journals", ARTICLE) + section("Book Chapters", CHAPTER))
    assert [r["title"] for r in rows] == [
        "Do corrupt practices lead to increased cash holdings in firms? International evidence"
    ]


def test_the_same_article_listed_twice_counts_once():
    assert len(parse(section("Journals", ARTICLE + ARTICLE))) == 1


def test_profile_without_publications_gives_none():
    assert parse("<h1>Heather Prider</h1><p>Lecturer</p>") == []


def test_collect_gathers_profile_publications_and_reports_unreadable_tables(monkeypatch, capsys):
    staff = [
        {"name_clean": "Kartick Gupta", "_pubs": parse(section("Journals", ARTICLE)), "_pub_error": None},
        {"name_clean": "Someone Else", "_pubs": [], "_pub_error": "AttributeError: boom"},
    ]
    monkeypatch.setattr(adelaide, "scrape_staff", lambda verbose=True: staff)

    records, pubs = adelaide.collect(verbose=True)

    assert len(pubs) == 1 and pubs[0]["name"] == "Kartick Gupta"
    # Both people are kept; the private working keys are removed.
    assert [r["name_clean"] for r in records] == ["Kartick Gupta", "Someone Else"]
    assert all("_pubs" not in r and "_pub_error" not in r for r in records)
    assert "Someone Else: AttributeError: boom" in capsys.readouterr().out
