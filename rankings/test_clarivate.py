"""Tests for clarivate.py. All offline: no test may reach the network.

Every fixture below is copied from a real response. An earlier version of this
file used invented ones, and they all passed happily while the module matched
0 of 437 real journals, because the invented shape put the impact factor in
the search response and the real API does not put it there at all. That is why
these are pasted rather than imagined.

The real sequence is three calls:

    GET /journals?q=0022-1082                 -> an id, and where it matched
    GET /journals/J_FINANC                    -> detail, and no metrics at all
    GET /journals/J_FINANC/reports/year/2025  -> the figures
"""

import csv
import urllib.error

import pytest

import clarivate as c


LIVE_SEARCH = {
    "metadata": {"total": 1, "page": 1, "limit": 3},
    "hits": [
        {
            "id": "J_FINANC",
            "self": "/journals/J_FINANC",
            "name": "JOURNAL OF FINANCE",
            # Note: no `issn` key. The ISSN is here, wrapped in highlight tags.
            "matches": [{"field": "issn", "value": ["<em>0022-1082</em>"]}],
        }
    ],
}

LIVE_DETAIL = {
    "id": "J_FINANC",
    "name": "JOURNAL OF FINANCE",
    "issn": "0022-1082",
    "eIssn": "1540-6261",
    "firstIssueYear": 1946,
    "publisher": {"name": "WILEY"},
    "journalCitationReports": [
        {"year": 2025, "url": "/journals/J_FINANC/reports/year/2025"},
        {"year": 2024, "url": "/journals/J_FINANC/reports/year/2024"},
        {"year": 1997, "url": "/journals/J_FINANC/reports/year/1997"},
    ],
}

LIVE_REPORT = {
    "year": 2025,
    "journal": {"id": "J_FINANC", "name": "JOURNAL OF FINANCE"},
    "metrics": {
        "impactMetrics": {
            "totalCites": 53100,
            "jif": "12.2",                  # a string
            "jifWithoutSelfCitations": "11.9",
            "jif5Years": 12.3,              # plural, and a number
            "immediacyIndex": 1.9,
            "jci": 2.94,
        }
    },
    "journalProfile": {"startYear": 2023, "endYear": 2025},
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a test tried to reach the network")
    monkeypatch.setattr(c.urllib.request, "urlopen", forbidden)
    monkeypatch.setenv(c.KEY_ENV, "test-key-not-a-real-one")


def responding(search=LIVE_SEARCH, detail=LIVE_DETAIL, report=LIVE_REPORT):
    """Stand in for request_json across all three calls of a lookup."""
    def fake(path, params, key, retries=c.MAX_RETRIES):
        fake.paths.append(path)
        fake.calls.append(params)
        if path == "/journals":
            return search
        if "/reports/year/" in path:
            return report
        return detail
    fake.paths, fake.calls = [], []
    return fake


# ------------------------------------------------------------------- the key

def test_a_missing_key_is_a_clear_error_not_a_crash(monkeypatch):
    monkeypatch.delenv(c.KEY_ENV, raising=False)
    with pytest.raises(c.MissingKey) as caught:
        c.api_key()
    assert c.KEY_ENV in str(caught.value)


def test_a_blank_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv(c.KEY_ENV, "   ")
    with pytest.raises(c.MissingKey):
        c.api_key()


def test_the_key_is_never_a_command_line_argument():
    # An argument ends up in shell history and in any screenshot of a terminal.
    import inspect
    source = inspect.getsource(c.main)
    assert "--key" not in source and "--api-key" not in source


# --------------------------------------------------------- finding the ISSN

def test_the_issn_is_found_inside_the_highlighted_matches_block():
    # The exact shape that made the first full run match 0 of 437.
    assert "0022-1082" in c.issns_in(LIVE_SEARCH["hits"][0])


def test_highlight_tags_do_not_break_the_issn():
    # A highlighter is free to split the number itself.
    assert c.issns_in({"v": ["<em>0022</em>-1082"]}) == {"0022-1082"}


def test_issn_normalisation_accepts_both_written_forms():
    assert c.normalise_issn("00221082") == "0022-1082"
    assert c.normalise_issn("0022-1082") == "0022-1082"
    assert c.normalise_issn("2434-561X") == "2434-561X"


def test_eight_letters_is_not_an_issn():
    # "nonsense" is eight alphanumerics. Formatting it as an ISSN would create
    # a join key that matches nothing and looks entirely real.
    assert c.normalise_issn("nonsense") is None


# ---------------------------------------------------------- the three calls

def test_a_lookup_returns_the_figures_from_the_year_report(monkeypatch):
    fake = responding()
    monkeypatch.setattr(c, "request_json", fake)
    assert c.lookup("0022-1082", "k") == (12.2, 12.3, 2025, c.MATCH_ISSN)
    assert fake.paths == ["/journals", "/journals/J_FINANC",
                          "/journals/J_FINANC/reports/year/2025"]


def test_the_detail_call_alone_never_yields_metrics():
    # The assumption the whole three-call design rests on.
    assert c.find_number(LIVE_DETAIL, c.JIF_KEYS) is None
    assert c.find_number(LIVE_DETAIL, c.JIF5_KEYS) is None


def test_the_five_year_jif_is_the_plural_field():
    # jif5Years. Looking for "jif5Year" filled 0 of 571 journals.
    assert c.find_number(LIVE_REPORT, c.JIF5_KEYS) == 12.3


def test_the_impact_factor_survives_arriving_as_a_string():
    assert c.find_number(LIVE_REPORT, c.JIF_KEYS) == 12.2


def test_a_search_hit_for_a_different_journal_is_rejected(monkeypatch):
    # Free text search returns near matches. Accepting one would attach the
    # wrong journal's impact factor, which nothing downstream could detect.
    other = {"hits": [{"id": "J_JBFLP",
                       "name": "Journal of Banking and Finance: Law and Practice",
                       "matches": [{"field": "issn",
                                    "value": ["<em>1443-8483</em>"]}]}]}
    fake = responding(search=other)
    monkeypatch.setattr(c, "request_json", fake)
    _, _, _, match = c.lookup("0022-1082", "k")
    assert match == c.MATCH_NONE
    assert fake.paths == ["/journals"]      # never fetched the wrong journal


def test_a_match_on_the_electronic_issn_still_counts(monkeypatch):
    search = {"hits": [{"id": "J_FINANC", "name": "JOURNAL OF FINANCE",
                        "matches": [{"field": "issn",
                                     "value": ["<em>1540-6261</em>"]}]}]}
    monkeypatch.setattr(c, "request_json", responding(search=search))
    jif, _, _, match = c.lookup("1540-6261", "k")
    assert (match, jif) == (c.MATCH_ISSN, 12.2)


def test_an_empty_result_is_not_found_not_a_failure(monkeypatch):
    monkeypatch.setattr(c, "request_json", responding(search={"hits": []}))
    assert c.lookup("0000-0000", "k")[3] == c.MATCH_NONE


# -------------------------------------------------------------- the JCR year

def test_the_newest_report_year_is_used_not_the_first_listed():
    # Reading the first entry put 1997 and 2005 in jcr_year for 14 journals,
    # and a 1997 impact factor presented as current is a wrong number.
    assert c.latest_report_year(LIVE_DETAIL) == 2025
    assert c.latest_report_year(
        {"journalCitationReports": [{"year": 1997}, {"year": 2025},
                                    {"year": 2010}]}) == 2025


def test_the_founding_year_is_not_mistaken_for_a_jcr_year():
    # firstIssueYear 1946 sits in the same record.
    assert c.latest_report_year(LIVE_DETAIL) == 2025


def test_a_journal_with_no_reports_is_indexed_but_unrated(monkeypatch):
    # Being in JCR and having an impact factor are different facts.
    bare = {"id": "J_X", "name": "SOMETHING", "journalCitationReports": []}
    fake = responding(detail=bare)
    monkeypatch.setattr(c, "request_json", fake)
    jif, jif5, year, match = c.lookup("0022-1082", "k")
    assert match == c.MATCH_ISSN
    assert (jif, jif5, year) == (None, None, None)
    assert not any("/reports/" in p for p in fake.paths)


# ----------------------------------------------------- failure vs not found

def test_a_failed_search_is_told_apart_from_not_found(monkeypatch):
    # Caching a network blip as "not in JCR" turns a temporary problem into a
    # permanent wrong answer in the data.
    monkeypatch.setattr(c, "request_json", lambda *a, **k: None)
    assert c.lookup("0022-1082", "k")[3] == c.MATCH_FAILED
    assert c.MATCH_FAILED != c.MATCH_NONE


def test_a_failed_detail_call_is_a_failure_not_a_missing_impact_factor(monkeypatch):
    monkeypatch.setattr(c, "request_json", responding(detail=None))
    assert c.lookup("0022-1082", "k")[3] == c.MATCH_FAILED


def test_a_failed_report_call_is_a_failure_not_a_blank(monkeypatch):
    monkeypatch.setattr(c, "request_json", responding(report=None))
    assert c.lookup("0022-1082", "k")[3] == c.MATCH_FAILED


# ------------------------------------------------------------ number parsing

@pytest.mark.parametrize("value,expected", [
    (12.2, 12.2), ("12.2", 12.2), (7, 7.0), ("1,234", 1234.0),
    ("", None), ("N/A", None), (None, None), (True, None), ("abc", None),
])
def test_numbers_are_read_and_rubbish_is_rejected(value, expected):
    assert c.as_number(value) == expected


# ------------------------------------------------------------- HTTP handling

def _http_error(code):
    return urllib.error.HTTPError("u", code, "msg", {}, None)


def test_a_rejected_key_stops_immediately_rather_than_retrying(monkeypatch):
    attempts = []

    def failing(*args, **kwargs):
        attempts.append(1)
        raise _http_error(401)

    monkeypatch.setattr(c.urllib.request, "urlopen", failing)
    with pytest.raises(c.MissingKey):
        c.request_json("/journals", {"q": "x"}, "bad-key")
    # Hammering an endpoint with a rejected key is how a key gets suspended.
    assert len(attempts) == 1


def test_a_404_is_an_empty_result_not_a_failure(monkeypatch):
    monkeypatch.setattr(c.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(404)))
    assert c.request_json("/journals", {"q": "x"}, "k") == {}


def test_throttling_is_retried_then_given_up_on(monkeypatch):
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    monkeypatch.setattr(c.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(429)))
    with pytest.raises(c.RateLimited):
        c.request_json("/journals", {"q": "x"}, "k", retries=2)


def test_requests_are_paced_under_the_documented_limit():
    # Clarivate documents 5 requests per second.
    assert c.MIN_INTERVAL >= 0.2


# ------------------------------------------------------------------ the files

COLUMNS = ["journal_name", "issn", "quality_rank", "sjr", "publication_count"]


def journals_csv(tmp_path, rows):
    path = tmp_path / "journals.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return str(path)


def test_the_journal_table_gains_the_columns_and_keeps_the_old_ones(tmp_path, monkeypatch):
    path = journals_csv(tmp_path, [
        {"journal_name": "The Journal of Finance", "issn": "0022-1082",
         "quality_rank": "A*", "sjr": "20.1", "publication_count": "12"},
    ])
    monkeypatch.setattr(c, "request_json", responding())
    rows, counts = c.enrich_journals(path, "k")

    assert rows[0]["impact_factor"] == 12.2
    assert rows[0]["impact_factor_5yr"] == 12.3
    assert rows[0]["jcr_year"] == 2025
    assert rows[0]["clarivate_match_type"] == c.MATCH_ISSN
    # Nothing already present may be lost.
    assert rows[0]["quality_rank"] == "A*"
    assert rows[0]["sjr"] == "20.1"
    assert counts[c.MATCH_ISSN] == 1


def test_a_journal_with_no_issn_is_skipped_without_a_lookup(tmp_path, monkeypatch):
    path = journals_csv(tmp_path, [
        {"journal_name": "Weekly Tax Bulletin", "issn": "",
         "quality_rank": "none", "sjr": "", "publication_count": "3"},
    ])
    fake = responding()
    monkeypatch.setattr(c, "request_json", fake)
    rows, counts = c.enrich_journals(path, "k")

    assert fake.calls == []          # no request was made at all
    assert counts["no-issn"] == 1
    assert rows[0]["clarivate_match_type"] == ""


def test_a_run_that_matches_nothing_says_so_in_the_counts(tmp_path, monkeypatch):
    # 437 lookups, 0 matched, 0 failed was the honest report of a real bug.
    # Keeping those counts distinct is what made it diagnosable at all.
    path = journals_csv(tmp_path, [
        {"journal_name": "The Journal of Finance", "issn": "0022-1082",
         "quality_rank": "A*", "sjr": "", "publication_count": "1"},
    ])
    monkeypatch.setattr(c, "request_json", responding(search={"hits": []}))
    rows, counts = c.enrich_journals(path, "k")
    assert counts[c.MATCH_NONE] == 1
    assert counts.get(c.MATCH_ISSN, 0) == 0
    assert rows[0]["impact_factor"] == ""


def test_the_figures_reach_the_publication_rows(tmp_path):
    pubs = tmp_path / "pubs.csv"
    with open(pubs, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["journal_name", "title", "quality_rank"])
        w.writeheader()
        w.writerow({"journal_name": "The Journal of Finance", "title": "A paper",
                    "quality_rank": "A*"})
        w.writerow({"journal_name": "Unknown Journal", "title": "Another",
                    "quality_rank": ""})

    journal_rows = [{"journal_name": "The Journal of Finance",
                     "impact_factor": 12.2, "impact_factor_5yr": 12.3}]
    filled, total = c.apply_to_publications(str(pubs), journal_rows)

    assert (filled, total) == (1, 2)
    with open(pubs, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["impact_factor"] == "12.2"
    assert rows[1]["impact_factor"] == ""      # blank, never carried over
    assert rows[0]["quality_rank"] == "A*"     # existing data untouched


def test_limit_stops_early_so_a_trial_run_is_cheap(tmp_path, monkeypatch):
    rows_in = [{"journal_name": f"J{i}", "issn": "0022-1082",
                "quality_rank": "", "sjr": "", "publication_count": "1"}
               for i in range(5)]
    path = journals_csv(tmp_path, rows_in)
    fake = responding()
    monkeypatch.setattr(c, "request_json", fake)
    c.enrich_journals(path, "k", limit=2)
    assert len([p for p in fake.paths if p == "/journals"]) == 2
