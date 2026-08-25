"""
Tests for OpenAlex author discovery — CITS3200 Group 20.

Fully offline. The two API calls are stubbed with real response shapes taken
from live lookups on 25 August, so these run with no network and no key.

The Jason Zein fixture is the real thing, and it is the reason this module is
shaped the way it is: three author records come back for that name, one of
which is a different person and one of which is the same person again without
an ORCID.

Run:  python -m pytest test_authors.py -v
"""

import csv
import os
import tempfile

import pytest

import authors

# Captured at import, before the autouse fixture below replaces it. One test
# needs the real function: it is testing request_json's own handling of HTTP
# 429, which cannot be tested through a stub of itself.
REAL_REQUEST_JSON = authors.request_json


# ---------------------------------------------------------------------------
# Real API shapes
# ---------------------------------------------------------------------------
UNSW = {"id": "https://openalex.org/I31746571",
        "ror": "https://ror.org/03r8z3t63", "display_name": "UNSW Sydney"}
ELSEWHERE = {"id": "https://openalex.org/I4099518",
             "ror": "https://ror.org/04zq3xb25",
             "display_name": "Illinois Environmental Protection Agency"}

ZEIN_REAL = {
    "id": "https://openalex.org/A5022211414",
    "display_name": "Jason Zein",
    "orcid": "https://orcid.org/0000-0001-7701-3721",
    "works_count": 45,
    "last_known_institutions": [UNSW],
}
ZEIN_DUPLICATE = {          # the same person, split into a second record
    "id": "https://openalex.org/A5140822079",
    "display_name": "Jason Zein",
    "orcid": None,
    "works_count": 1,
    "last_known_institutions": [UNSW],
}
EL_ZEIN = {                 # genuinely somebody else
    "id": "https://openalex.org/A5003171701",
    "display_name": "Jason El-Zein",
    "orcid": None,
    "works_count": 1,
    "last_known_institutions": [ELSEWHERE],
}

ARTICLE = {
    "id": "https://openalex.org/W4241177567",
    "doi": "https://doi.org/10.1016/j.jfineco.2019.06.009",
    "title": "Inventor CEOs",
    "publication_year": 2019,
    "type": "article",
    "cited_by_count": 133,
    "fwci": 14.2,
    "citation_normalized_percentile": {"value": 0.98, "is_in_top_10_percent": True},
    "primary_location": {"source": {
        "display_name": "Journal of Financial Economics",
        "type": "journal",
        "issn_l": "0304-405X",
        "issn": ["0304-405X"],
        "host_organization_name": "Elsevier BV",
    }},
    "authorships": [
        {"author": {"display_name": "Emdad Islam"}},
        {"author": {"display_name": "Jason Zein"}},
    ],
    "biblio": {"volume": "135", "first_page": "505", "last_page": "527"},
}

PREPRINT = {                # type "article", but the source is not a journal
    "id": "https://openalex.org/W111",
    "doi": "https://doi.org/10.2139/ssrn.999999",
    "title": "A working paper",
    "publication_year": 2023,
    "type": "article",
    "primary_location": {"source": {"display_name": "SSRN Electronic Journal",
                                    "type": "repository"}},
    "authorships": [{"author": {"display_name": "Jason Zein"}}],
}

BOOK = {"id": "https://openalex.org/W222", "doi": None, "title": "A book",
        "publication_year": 2020, "type": "book",
        "primary_location": {"source": {"display_name": "Springer",
                                        "type": "publisher"}},
        "authorships": []}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in these tests may touch the network."""
    def refuse(*a, **k):
        raise AssertionError("a test tried to call the real API")
    monkeypatch.setattr(authors, "request_json", refuse)
    monkeypatch.setattr(authors.time, "sleep", lambda *a: None)


def stub(monkeypatch, found=(), works=()):
    monkeypatch.setattr(authors, "search_authors",
                        lambda session, name, mailto: list(found))
    monkeypatch.setattr(authors, "works_of",
                        lambda session, ids, mailto, limit=None: list(works))


def staff_csv(rows, columns=("name", "profile_url", "university", "field_of_research")):
    path = os.path.join(tempfile.mkdtemp(), "unsw_staff.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(columns))
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in columns})
    return path


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


ZARIN_STAFF = [{"name": "Jason Zein", "profile_url": "https://x/staff/jason-zein",
                "university": "UNSW Sydney", "field_of_research": "Finance"}]


# ---------------------------------------------------------------------------
# Name folding
# ---------------------------------------------------------------------------
def test_accents_and_punctuation_are_formatting_not_identity():
    assert authors.fold("Luís Filipe Gonçalves-Pinto") == \
           authors.fold("Luis Filipe Goncalves-Pinto")
    assert authors.fold("O'Neill") == authors.fold("ONeill")


def test_word_order_is_not_normalised_away():
    """"Li Yang" and "Yang Li" may well be two different people."""
    assert authors.fold("Li Yang") != authors.fold("Yang Li")


def test_initial_form():
    assert authors.initial_form("Nicole Ang") == "n ang"
    assert authors.initial_form("Ang") == ""


# ---------------------------------------------------------------------------
# Choosing an author — the real Jason Zein case
# ---------------------------------------------------------------------------
def test_a_namesake_at_another_institution_is_excluded():
    """Jason El-Zein at the Illinois EPA is not our finance professor. Without
    the institution filter his citations would land on her researcher."""
    primary, dupes, how, note = authors.choose(
        "Jason Zein", [ZEIN_REAL, EL_ZEIN], "03r8z3t63")
    assert primary is ZEIN_REAL
    assert how == authors.MATCH_NAME


def test_a_duplicate_record_of_the_same_person_is_kept_not_dropped():
    """OpenAlex splits one person across several author ids and puts the ORCID
    on only one. Using the ORCID record alone loses the other's works."""
    primary, dupes, how, note = authors.choose(
        "Jason Zein", [ZEIN_REAL, ZEIN_DUPLICATE, EL_ZEIN], "03r8z3t63")
    assert primary is ZEIN_REAL
    assert dupes == [ZEIN_DUPLICATE]
    assert how == authors.MATCH_NAME


def test_two_orcid_holders_with_one_name_is_ambiguous_not_a_guess():
    """Two real people can share a name at one institution. Picking the one
    with more works would be a coin toss dressed up as a decision."""
    other = dict(ZEIN_REAL, id="https://openalex.org/A999",
                 orcid="https://orcid.org/0000-0002-0000-0000", works_count=80)
    primary, dupes, how, note = authors.choose(
        "Jason Zein", [ZEIN_REAL, other], "03r8z3t63")
    assert primary is None
    assert how == authors.MATCH_AMBIGUOUS
    assert "orcid" in note


def test_no_orcid_among_several_is_also_ambiguous():
    twin = dict(ZEIN_DUPLICATE, id="https://openalex.org/A888")
    primary, dupes, how, note = authors.choose(
        "Jason Zein", [ZEIN_DUPLICATE, twin], "03r8z3t63")
    assert primary is None and how == authors.MATCH_AMBIGUOUS


def test_nobody_at_the_institution_is_not_found():
    primary, dupes, how, note = authors.choose("Jason Zein", [EL_ZEIN], "03r8z3t63")
    assert primary is None and how == authors.MATCH_NONE


def test_an_initial_only_record_matches_but_is_tagged_as_weaker():
    initial = dict(ZEIN_REAL, display_name="J. Zein")
    primary, dupes, how, note = authors.choose("Jason Zein", [initial], "03r8z3t63")
    assert primary is initial
    assert how == authors.MATCH_VARIANT


def test_display_name_alternatives_are_searched_too():
    alt = dict(ZEIN_DUPLICATE, display_name="J Zein",
               display_name_alternatives=["Jason Zein"])
    primary, dupes, how, note = authors.choose("Jason Zein", [alt], "03r8z3t63")
    assert primary is alt and how == authors.MATCH_NAME


# ---------------------------------------------------------------------------
# What counts as a journal article
# ---------------------------------------------------------------------------
def test_only_journal_articles_are_kept():
    """The client's 19 August decision. An SSRN preprint is type "article" too,
    which is why the source type is checked as well."""
    assert authors.is_journal_article(ARTICLE)
    assert not authors.is_journal_article(PREPRINT)
    assert not authors.is_journal_article(BOOK)


# ---------------------------------------------------------------------------
# Turning a work into a publication row
# ---------------------------------------------------------------------------
def test_a_work_becomes_a_row_in_the_scrapers_column_names():
    row = authors.to_row(ARTICLE, {"name": "Jason Zein", "profile_url": "u",
                                   "university": "UNSW Sydney",
                                   "field_of_research": "Finance"},
                         authors.MATCH_NAME, "A5022211414")
    assert row["title"] == "Inventor CEOs"
    assert row["journal_name"] == "Journal of Financial Economics"
    assert row["doi"] == "10.1016/j.jfineco.2019.06.009"
    assert row["year"] == 2019
    assert row["issn"] == "0304-405X"
    assert row["author_count"] == 2
    assert row["pages"] == "505-527"
    assert row["publisher"] == "Elsevier BV"
    assert set(row) <= set(authors.DISCOVERED_COLUMNS)


def test_the_source_column_says_where_the_row_came_from():
    """Data dictionary 3.5.4 has a `source` field precisely so a row found by
    OpenAlex is distinguishable from one the university published."""
    row = authors.to_row(ARTICLE, {}, authors.MATCH_NAME, "A1")
    assert row["source"] == "OpenAlex"
    assert row["author_match_type"] == authors.MATCH_NAME


def test_author_count_is_the_length_of_the_list_not_a_split_string():
    work = dict(ARTICLE, authorships=[{"author": {"display_name": "Zhang; Wei"}}])
    assert authors.to_row(work, {}, "x", "A1")["author_count"] == 1


# ---------------------------------------------------------------------------
# Not re-reporting what we already have
# ---------------------------------------------------------------------------
def test_a_work_we_already_scraped_is_not_reported_as_new():
    row = {"doi": "10.1016/j.jfineco.2019.06.009", "title": "Inventor CEOs",
           "year": 2019}
    assert not authors.is_new(row, {"10.1016/j.jfineco.2019.06.009"}, set())


def test_a_work_with_no_doi_falls_back_to_title_and_year():
    """893 of UNSW's 2,000 articles have no DOI, so this is not an edge case."""
    import journal_match as jm
    row = {"doi": None, "title": "Inventor CEOs", "year": 2019}
    titles = {(jm.normalise("Inventor CEOs"), "2019")}
    assert not authors.is_new(row, set(), titles)
    assert authors.is_new({"doi": None, "title": "Inventor CEOs", "year": 2020},
                          set(), titles)


def test_a_genuinely_new_work_is_reported():
    row = {"doi": "10.1/new", "title": "Something else", "year": 2024}
    assert authors.is_new(row, {"10.1/old"}, set())


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def test_discovery_writes_orcid_and_the_missing_publication(monkeypatch):
    stub(monkeypatch, found=[ZEIN_REAL, ZEIN_DUPLICATE, EL_ZEIN], works=[ARTICLE])
    staff_out, discovered_out = authors.discover(
        staff_csv(ZARIN_STAFF), ror="03r8z3t63", use_cache=False)

    person = read(staff_out)[0]
    assert person["orcid"] == "0000-0001-7701-3721"
    assert person["author_match_type"] == authors.MATCH_NAME
    assert person["openalex_author_ids"] == "A5022211414; A5140822079"

    found = read(discovered_out)
    assert len(found) == 1
    assert found[0]["title"] == "Inventor CEOs"
    assert found[0]["researcher_name"] == "Jason Zein"
    assert found[0]["source"] == "OpenAlex"


def test_an_ambiguous_researcher_contributes_no_publications(monkeypatch):
    """The whole point. A paper missing is recoverable; a paper attributed to
    the wrong person is invisible and corrupts every ranking built on it."""
    other = dict(ZEIN_REAL, id="https://openalex.org/A999",
                 orcid="https://orcid.org/0000-0002-0000-0000")
    stub(monkeypatch, found=[ZEIN_REAL, other], works=[ARTICLE])
    staff_out, discovered_out = authors.discover(
        staff_csv(ZARIN_STAFF), ror="03r8z3t63", use_cache=False)

    assert read(discovered_out) == []
    person = read(staff_out)[0]
    assert person["author_match_type"] == authors.MATCH_AMBIGUOUS
    assert person["author_candidates"]          # the reviewer is told who they were


def test_a_publication_already_on_file_is_not_reported_again(monkeypatch):
    stub(monkeypatch, found=[ZEIN_REAL], works=[ARTICLE])
    pubs = os.path.join(tempfile.mkdtemp(), "pubs.csv")
    with open(pubs, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "doi", "year", "journal_name"])
        w.writeheader()
        w.writerow({"title": "Inventor CEOs",
                    "doi": "10.1016/j.jfineco.2019.06.009",
                    "year": "2019", "journal_name": "Journal of Financial Economics"})

    _, discovered_out = authors.discover(staff_csv(ZARIN_STAFF), pubs,
                                         ror="03r8z3t63", use_cache=False)
    assert read(discovered_out) == []


def test_a_researcher_openalex_does_not_know_is_recorded_not_skipped(monkeypatch):
    stub(monkeypatch, found=[], works=[])
    staff_out, discovered_out = authors.discover(
        staff_csv(ZARIN_STAFF), ror="03r8z3t63", use_cache=False)
    assert read(staff_out)[0]["author_match_type"] == authors.MATCH_NONE
    assert read(discovered_out) == []


def test_every_staff_row_survives_even_when_unmatched(monkeypatch):
    stub(monkeypatch, found=[], works=[])
    staff = ZARIN_STAFF + [{"name": "Nobody At All", "university": "UNSW Sydney"}]
    staff_out, _ = authors.discover(staff_csv(staff), ror="03r8z3t63",
                                    use_cache=False)
    assert len(read(staff_out)) == 2


def test_a_harvest_row_is_written(monkeypatch):
    import harvest
    stub(monkeypatch, found=[ZEIN_REAL], works=[ARTICLE])
    path = staff_csv(ZARIN_STAFF)
    authors.discover(path, ror="03r8z3t63", use_cache=False)
    sources = {r["source"] for r in harvest.read(os.path.dirname(path))}
    assert "OpenAlex authors" in sources


def test_an_empty_staff_file_stops_rather_than_writing_nothing(monkeypatch):
    with pytest.raises(SystemExit):
        authors.discover(staff_csv([]), ror="03r8z3t63", use_cache=False)


def test_a_staff_file_with_no_name_column_stops(monkeypatch):
    path = staff_csv([{"who": "x"}], columns=["who"])
    with pytest.raises(SystemExit):
        authors.discover(path, ror="03r8z3t63", use_cache=False)


# ---------------------------------------------------------------------------
# Institutions across a career, not just the newest one
# ---------------------------------------------------------------------------
CARSON_STYLE = {
    "id": "https://openalex.org/A5000000001",
    "display_name": "Elizabeth Carson",
    "orcid": "https://orcid.org/0000-0003-0000-0001",
    "works_count": 60,
    # OpenAlex's "last known" is whatever was on the newest paper — here a
    # co-author's institution, not hers.
    "last_known_institutions": [ELSEWHERE],
    "affiliations": [
        {"institution": {"ror": "https://ror.org/03r8z3t63",
                         "display_name": "UNSW Sydney"},
         "years": [2024, 2019, 2011]},
    ],
}


def test_an_affiliation_counts_even_when_the_last_known_one_does_not():
    """Filtering on last_known_institutions alone reported 38 of UNSW's 93
    researchers as "not found", including people with a hundred publications on
    their own staff page. That is not a fact about them."""
    assert "03r8z3t63" in authors.institution_rors(CARSON_STYLE)
    primary, dupes, how, note = authors.choose(
        "Elizabeth Carson", [CARSON_STYLE], "03r8z3t63")
    assert primary is CARSON_STYLE
    assert how == authors.MATCH_NAME


def test_someone_never_at_our_institution_is_still_excluded():
    """Widening to past affiliations must not widen to everybody."""
    assert authors.institution_rors(EL_ZEIN) == {"04zq3xb25"}
    primary, _, how, _ = authors.choose("Jason Zein", [EL_ZEIN], "03r8z3t63")
    assert how == authors.MATCH_NONE


def test_an_author_with_no_institutions_at_all_is_excluded():
    assert authors.institution_rors({"display_name": "x"}) == set()


# ---------------------------------------------------------------------------
# A failed lookup is not the same as nobody being there
# ---------------------------------------------------------------------------
def test_a_failed_lookup_is_recorded_as_failed_not_as_not_found(monkeypatch):
    """One says OpenAlex has nobody; the other says we never got to ask.
    Recording them the same way turns a bad afternoon into a permanent wrong
    answer about a real person."""
    monkeypatch.setattr(authors, "search_authors", lambda s, n, m: None)
    monkeypatch.setattr(authors, "works_of", lambda *a, **k: [])
    staff_out, discovered_out = authors.discover(
        staff_csv(ZARIN_STAFF), ror="03r8z3t63", use_cache=False)
    assert read(staff_out)[0]["author_match_type"] == authors.MATCH_FAILED
    assert read(discovered_out) == []


def test_a_failed_lookup_is_not_written_to_the_cache(monkeypatch, tmp_path):
    """Otherwise re-running never retries it, and the wrong answer is now
    permanent."""
    monkeypatch.setattr(authors, "search_authors", lambda s, n, m: None)
    monkeypatch.setattr(authors, "works_of", lambda *a, **k: [])
    path = staff_csv(ZARIN_STAFF)
    authors.discover(path, ror="03r8z3t63", use_cache=True)
    cache = authors.load_cache(
        os.path.join(os.path.dirname(path), "openalex_authors_cache.json"), True)
    assert all(v is not None for v in cache.values())
    assert not any(k.startswith("name:") for k in cache)


def test_a_genuine_empty_result_is_cached(monkeypatch):
    """The opposite case still has to be remembered, or every run re-asks."""
    monkeypatch.setattr(authors, "search_authors", lambda s, n, m: [])
    monkeypatch.setattr(authors, "works_of", lambda *a, **k: [])
    path = staff_csv(ZARIN_STAFF)
    authors.discover(path, ror="03r8z3t63", use_cache=True)
    cache = authors.load_cache(
        os.path.join(os.path.dirname(path), "openalex_authors_cache.json"), True)
    assert any(k.startswith("name:") for k in cache)


# ---------------------------------------------------------------------------
# OpenAlex's daily budget
# ---------------------------------------------------------------------------
def test_the_run_stops_when_the_daily_budget_is_spent(monkeypatch):
    """OpenAlex charges per request against a daily allowance. Retrying cannot
    succeed until midnight UTC, and carrying on would mark every remaining
    researcher "not found" for a reason that has nothing to do with them.

    Nothing is written either — see
    test_an_aborted_run_does_not_overwrite_a_good_previous_result for why.
    """
    calls = []

    def spent(session, name, mailto):
        calls.append(name)
        raise authors.BudgetExhausted("Insufficient budget")

    monkeypatch.setattr(authors, "search_authors", spent)
    monkeypatch.setattr(authors, "works_of", lambda *a, **k: [])
    staff = [{"name": f"Person {n}", "university": "UNSW Sydney"} for n in range(5)]
    staff_out, discovered_out = authors.discover(
        staff_csv(staff), ror="03r8z3t63", use_cache=False)

    assert len(calls) == 1                       # stopped, did not burn the rest
    assert not os.path.exists(staff_out)         # and wrote nothing over anything
    assert not os.path.exists(discovered_out)


def test_budget_exhaustion_is_told_apart_from_throttling():
    """Both arrive as HTTP 429 and need opposite responses. A throttle is worth
    waiting out; a spent daily budget cannot succeed until midnight, so
    retrying it just burns the clock before mismarking everyone left."""
    class Response:
        status_code = 429

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        def __init__(self, payload):
            self._payload = payload
            self.calls = 0

        def get(self, *a, **k):
            self.calls += 1
            return Response(self._payload)

    budget = Session({"error": "Rate limit exceeded",
                      "message": "Insufficient budget. This request costs $0.001"})
    with pytest.raises(authors.BudgetExhausted):
        REAL_REQUEST_JSON(budget, "u", {}, None)
    assert budget.calls == 1                      # not retried, it cannot succeed

    throttle = Session({"message": "too many requests"})
    assert REAL_REQUEST_JSON(throttle, "u", {}, None) is None
    assert throttle.calls == authors.MAX_RETRIES  # this one is worth retrying


def test_a_429_with_no_json_body_is_treated_as_a_throttle():
    """Being wrong in this direction costs a few seconds. Being wrong the other
    way marks real researchers as missing."""
    class Response:
        status_code = 429

        def json(self):
            raise ValueError("not json")

    class Session:
        calls = 0

        def get(self, *a, **k):
            Session.calls += 1
            return Response()

    assert REAL_REQUEST_JSON(Session(), "u", {}, None) is None
    assert Session.calls == authors.MAX_RETRIES


def test_an_aborted_run_does_not_overwrite_a_good_previous_result(monkeypatch):
    """A run that stopped on its very first request replaced a complete
    204-publication file with an empty one. Everything fetched is cached, so
    the next run rebuilds it instantly; partial output that overwrites complete
    output is strictly worse than no output."""
    monkeypatch.setattr(authors, "works_of", lambda *a, **k: [])

    # A good run first.
    monkeypatch.setattr(authors, "search_authors", lambda s, n, m: [ZEIN_REAL])
    monkeypatch.setattr(authors, "works_of", lambda *a, **k: [ARTICLE])
    path = staff_csv(ZARIN_STAFF)
    staff_out, discovered_out = authors.discover(path, ror="03r8z3t63",
                                                 use_cache=False)
    assert len(read(discovered_out)) == 1
    good_staff = read(staff_out)

    # Then one that dies immediately on the budget.
    def spent(session, name, mailto):
        raise authors.BudgetExhausted("Insufficient budget")
    monkeypatch.setattr(authors, "search_authors", spent)
    authors.discover(path, ror="03r8z3t63", use_cache=False)

    assert len(read(discovered_out)) == 1        # still there
    assert read(staff_out) == good_staff         # and unchanged


def test_an_aborted_run_still_returns_the_paths_it_would_have_written(monkeypatch):
    def spent(session, name, mailto):
        raise authors.BudgetExhausted("Insufficient budget")
    monkeypatch.setattr(authors, "search_authors", spent)
    monkeypatch.setattr(authors, "works_of", lambda *a, **k: [])
    staff_out, discovered_out = authors.discover(
        staff_csv(ZARIN_STAFF), ror="03r8z3t63", use_cache=False)
    assert staff_out.endswith("_with_orcid.csv")
    assert discovered_out.endswith("discovered_publications.csv")
