"""Non-research items dropped at export by OpenAlex type or title.

    python -m pytest tests/test_non_articles.py -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import export                                           # noqa: E402
from export import build_publications, non_article_reason  # noqa: E402


def _pub(title="A Study Of Things", doi="10.1/x", oa_type="Journal Article", **kw):
    row = {"type": "Journal Article", "name": "Sarah Adams", "title": title,
           "doi": doi, "year": "2020", "journal": "Accounting Review",
           "oa_type": oa_type}
    row.update(kw)
    return row


@pytest.mark.parametrize("oa_type", sorted(export.NON_ARTICLE_TYPES))
def test_a_non_article_type_is_dropped(oa_type):
    assert build_publications([_pub(oa_type=oa_type)], verbose=False) == []
    assert export.TYPE_REVIEW_LOG[0]["action"] == "dropped"
    assert oa_type in export.TYPE_REVIEW_LOG[0]["reason"]


@pytest.mark.parametrize("oa_type", [None, "Journal Article"])
def test_an_article_or_unknown_type_is_kept(oa_type):
    assert len(build_publications([_pub(oa_type=oa_type)], verbose=False)) == 1
    assert export.TYPE_REVIEW_LOG == []


@pytest.mark.parametrize("oa_type", sorted(export.REVIEW_TYPES))
def test_a_chapter_or_book_is_kept_but_flagged(oa_type):
    assert len(build_publications([_pub(oa_type=oa_type)], verbose=False)) == 1
    assert export.TYPE_REVIEW_LOG[0]["action"] == "review"


@pytest.mark.parametrize("title", ["Foreword", "PREFACE", "Prelims", "In Memoriam",
                                   "In memoriam.", "Front Matter", "Editorial Board"])
def test_a_front_matter_title_is_dropped(title):
    assert build_publications([_pub(title=title, oa_type=None)], verbose=False) == []


@pytest.mark.parametrize("title", ["Foreword guidance and analyst forecasts",
                                   "A preface to corporate governance reform",
                                   "Index funds and corporate voting"])
def test_a_real_title_that_starts_with_those_words_is_kept(title):
    assert len(build_publications([_pub(title=title, oa_type=None)], verbose=False)) == 1


def test_the_keep_list_overrules_openalex(monkeypatch):
    monkeypatch.setattr(export, "_KEEP_DOIS", {"10.1/x"})
    assert non_article_reason(_pub(doi="10.1/X", oa_type="editorial")) is None
    assert len(build_publications([_pub(oa_type="editorial")], verbose=False)) == 1


def test_the_openalex_type_never_overwrites_the_row_type(monkeypatch):
    from core.schema import blank_pub
    from enrichment import openalex as oa_enrich

    w = {"doi": "https://doi.org/10.1/x", "type": "editorial", "cited_by_count": 1,
         "primary_location": {"source": None}}
    monkeypatch.setattr(oa_enrich, "cached_get", lambda url, params=None, **kw: {"results": [w]})
    pubs = [blank_pub(doi="10.1/x", type="Journal Article")]
    oa_enrich.enrich(pubs, verbose=False)
    assert pubs[0]["type"] == "Journal Article"
    assert pubs[0]["oa_type"] == "editorial"


def test_paratext_is_only_flagged():
    # OpenAlex typed real A* papers (JBF, REStat) as paratext
    assert len(build_publications([_pub(oa_type="paratext")], verbose=False)) == 1
    assert export.TYPE_REVIEW_LOG[0]["action"] == "review"


def test_a_retracted_article_is_dropped():
    assert build_publications([_pub(oa_retracted=True)], verbose=False) == []
    assert "retracted" in export.TYPE_REVIEW_LOG[0]["reason"]


@pytest.mark.parametrize("title", ["Withdrawal notice to: Heterogeneity in ESG pay",
                                   "Retraction Note: Foreign investment and growth",
                                   "RETRACTED ARTICLE: Foreign investment and growth",
                                   "WITHDRAWN: Heterogeneity in ESG pay"])
def test_a_notice_title_is_dropped(title):
    assert build_publications([_pub(title=title, oa_type=None)], verbose=False) == []


@pytest.mark.parametrize("title", ["Retractions and firm value",
                                   "Withdrawn shareholders and proxy contests"])
def test_a_research_title_about_retractions_is_kept(title):
    assert len(build_publications([_pub(title=title, oa_type=None)], verbose=False)) == 1


def test_the_reprint_wrongly_flagged_retracted_is_on_the_keep_list():
    assert "10.1016/j.bar.2025.101559" in export._KEEP_DOIS
