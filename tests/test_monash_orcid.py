"""The Monash adapter only uses OpenAlex author records whose ORCID matches.

OpenAlex's `orcid:` filter on /authors also returns records carrying other
ORCIDs. The adapter used to take the first result: for Chen Chen that was a
biomedical researcher's 3836-work record, for Wen He a battery chemist's, for
Li Ge a software engineer's. Under Pure attribution that no longer adds wrong
papers, but it left their real Pure papers without DOIs or ISSNs. The ORCIDs
and record ids below are the real ones.
"""

import requests

from base_scrapers import monash


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def author(short_id, orcid):
    return {"id": f"https://openalex.org/{short_id}", "orcid": f"https://orcid.org/{orcid}"}


def run(monkeypatch, authors):
    """Fetch through a fake OpenAlex; return the works filter the adapter asked for."""
    asked = []

    def fake_get(url, params):
        if url.endswith("/authors"):
            return FakeResponse({"results": authors})
        asked.append(params["filter"])
        return FakeResponse({"results": [], "meta": {"next_cursor": None}})

    monkeypatch.setattr(monash, "_oa_get", fake_get)
    monkeypatch.setattr(monash.time, "sleep", lambda s: None)
    return asked


def test_first_result_with_another_orcid_is_ignored(monkeypatch):
    asked = run(monkeypatch, [
        author("A5100418548", "0000-0003-2104-534X"),   # 3836 works, cancer biology
        author("A5100418579", "0000-0003-4438-1258"),   # Chen Chen, corporate finance
    ])

    monash._fetch_pubs_openalex("Chen Chen", orcid="0000-0003-4438-1258")

    assert asked == ["authorships.author.id:A5100418579"]


def test_every_matching_record_is_used(monkeypatch):
    """Li Ge is split across two OpenAlex records; taking one loses half his papers."""
    asked = run(monkeypatch, [
        author("A5100447682", "0000-0002-5828-0186"),   # software engineering
        author("A5101795073", "0000-0001-9988-005X"),
        author("A5101795074", "0000-0001-9988-005X"),
    ])

    monash._fetch_pubs_openalex("Li Ge", orcid="0000-0001-9988-005X")

    assert asked == ["authorships.author.id:A5101795073|A5101795074"]


def test_no_matching_record_means_no_openalex_candidates(monkeypatch):
    asked = run(monkeypatch, [author("A5100606135", "0000-0001-6119-971X")])

    pubs = monash._fetch_pubs_openalex("Wen He", orcid="0000-0001-7421-6208")

    assert pubs == [] and asked == []


def test_orcid_comparison_ignores_url_prefix_and_case():
    assert monash._orcid_key("https://orcid.org/0000-0002-1825-009x") == "0000-0002-1825-009X"
    assert monash._orcid_key("0000-0002-1825-009X") == "0000-0002-1825-009X"
    assert monash._orcid_key(None) == ""


def test_requests_carry_the_shared_openalex_headers():
    """So OPENALEX_API_KEY from .env is sent, rather than drawing on the keyless budget."""
    from core.config import OA_HEADERS
    assert monash._OA_HEADERS is OA_HEADERS


def test_one_failing_pure_feed_does_not_abort_the_run(monkeypatch):
    """A moved or blocked profile used to raise out of collect() and stop every other person."""
    def broken_feed(record):
        raise requests.HTTPError("404 Client Error: Not Found")

    monkeypatch.setattr(monash, "_fetch_pure_publications", broken_feed)
    monkeypatch.setattr(monash, "_openalex_pubs", lambda record: [])
    person = {"name_clean": "Test Person", "profile_url": "https://research.monash.edu/en/persons/x/"}

    record, pubs, error = monash._process_phase3(person)

    assert record is person and pubs == []
    assert "404" in error
