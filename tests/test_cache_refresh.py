"""Stale-cache handling: core.http.max_age_days and the OpenAlex retry.

    python -m pytest tests/test_cache_refresh.py -q

Offline: requests.get and cached_get are stubbed.
"""

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.http as http                                  # noqa: E402
from core.schema import blank_pub                          # noqa: E402
from enrichment import openalex as oa_enrich               # noqa: E402


class _Resp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "CACHE_DIR", tmp_path)
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(url)
        return _Resp({"n": len(calls)})

    monkeypatch.setattr(http.requests, "get", fake_get)
    return calls


def _age(tmp_path, days):
    for f in tmp_path.glob("*.json"):
        t = time.time() - days * 86400
        os.utime(f, (t, t))


def test_without_max_age_the_cache_never_expires(server, tmp_path):
    http.cached_get("https://x.org/a")
    _age(tmp_path, 365)
    assert http.cached_get("https://x.org/a") == {"n": 1}
    assert len(server) == 1


def test_a_fresh_file_is_reused_under_max_age(server, tmp_path):
    http.cached_get("https://x.org/a")
    _age(tmp_path, 2)
    assert http.cached_get("https://x.org/a", max_age_days=7) == {"n": 1}


def test_an_old_file_is_fetched_again_under_max_age(server, tmp_path):
    http.cached_get("https://x.org/a")
    _age(tmp_path, 8)
    assert http.cached_get("https://x.org/a", max_age_days=7) == {"n": 2}
    # and the refreshed copy is what a normal call now reads
    assert http.cached_get("https://x.org/a") == {"n": 2}


def _work(doi, cited):
    return {"doi": f"https://doi.org/{doi}", "cited_by_count": cited,
            "primary_location": {"source": None}}


def test_a_complete_batch_is_not_asked_again(monkeypatch):
    seen = []

    def fake(url, params=None, **kw):
        seen.append(kw.get("max_age_days"))
        return {"results": [_work("10.1/a", 5)]}

    monkeypatch.setattr(oa_enrich, "cached_get", fake)
    oa_enrich.enrich([blank_pub(doi="10.1/a")], verbose=False)
    assert seen == [None]


def test_an_incomplete_batch_is_asked_again_with_a_max_age(monkeypatch):
    seen = []

    def fake(url, params=None, **kw):
        seen.append(kw.get("max_age_days"))
        works = [_work("10.1/a", 5)]
        if kw.get("max_age_days"):
            works.append(_work("10.1/new", 3))      # indexed since
        return {"results": works}

    monkeypatch.setattr(oa_enrich, "cached_get", fake)
    pubs = [blank_pub(doi="10.1/a"), blank_pub(doi="10.1/new")]
    oa_enrich.enrich(pubs, verbose=False)
    assert seen == [None, oa_enrich.INCOMPLETE_MAX_AGE_DAYS]
    assert pubs[1]["cited_by_count"] == 3


def test_a_failed_retry_keeps_the_cached_partial_answer(monkeypatch):
    def fake(url, params=None, **kw):
        if kw.get("max_age_days"):
            raise ConnectionError("offline")
        return {"results": [_work("10.1/a", 5)]}

    monkeypatch.setattr(oa_enrich, "cached_get", fake)
    pubs = [blank_pub(doi="10.1/a"), blank_pub(doi="10.1/missing")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["cited_by_count"] == 5


def test_a_doi_with_two_openalex_works_does_not_push_others_off_the_page(monkeypatch):
    """25 DOIs, three of which OpenAlex holds twice: 28 works must all fit."""
    dois = [f"10.1/p{i:02d}" for i in range(25)]
    works = [_work(d, 1) for d in dois] + [_work(d, 1) for d in dois[:3]]

    def fake(url, params=None, **kw):
        # OpenAlex returns the first per-page results and leaves the rest on page 2
        return {"results": sorted(works, key=lambda w: w["doi"])[:params["per-page"]]}

    monkeypatch.setattr(oa_enrich, "cached_get", fake)
    pubs = [blank_pub(doi=d) for d in dois]
    oa_enrich.enrich(pubs, verbose=False)
    assert all(p["cited_by_count"] == 1 for p in pubs)
