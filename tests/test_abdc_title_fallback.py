"""Offline tests for FIX E — the ABDC ISSN-first, title-fallback matcher in
enrichment/abdc.py. No network; the real ABDC spreadsheet load is patched
out with a small in-memory lookup built the same way _build() builds one.

    python -m pytest tests/test_abdc_title_fallback.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from enrichment import abdc                                    # noqa: E402


@pytest.fixture(autouse=True)
def _reset_abdc_cache():
    """_build() caches module globals across calls — reset around every
    test so one test's patched lookup can't leak into another."""
    abdc._lookup = None
    abdc._title_lookup = None
    yield
    abdc._lookup = None
    abdc._title_lookup = None


def _install_lookup(rows):
    """rows: [(title, rating, issn_or_issns_or_None), ...]. The third
    element may be a single ISSN string, a list of ISSNs, or None/empty.
    Builds _lookup / _title_lookup exactly as _build() would (including the
    "issns" list FIX E2 reads), without touching the real spreadsheet."""
    lookup, title_lookup = {}, {}
    for title, rating, issn in rows:
        issns = [issn] if isinstance(issn, str) else list(issn or [])
        for i in issns:
            lookup[i] = {"rating": rating, "title": title}
        key = abdc.normalise_title(title)
        title_lookup[key] = {"rating": rating, "title": title, "issns": issns}
    abdc._lookup = lookup
    abdc._title_lookup = title_lookup


# ------------------------------------------------------------ normalisation

def test_normalise_title_rules():
    assert abdc.normalise_title("The Journal of Money, Credit & Banking") == \
        "journal of money credit and banking"
    assert abdc.normalise_title("Accounting  Review!!") == "accounting review"
    assert abdc.normalise_title("") == ""
    assert abdc.normalise_title(None) == ""


def test_and_and_ampersand_normalise_the_same():
    assert abdc.normalise_title("Journal of Money, Credit & Banking") == \
        abdc.normalise_title("Journal of Money, Credit and Banking")


# ----------------------------------------------------------------- matching

def test_issn_match_wins_over_title():
    _install_lookup([("Accounting Review", "A*", "1234-5678")])
    pubs = [{"type": "Journal Article", "issns": ["1234-5678"],
             "journal": "Different Name Entirely"}]
    abdc.enrich(pubs, verbose=False)
    assert pubs[0]["abdc"] == "A*"
    assert pubs[0]["abdc_match"] == "issn"


def test_title_fallback_used_when_no_issn_hit():
    _install_lookup([("Journal of Financial Economics", "A*", None)])
    pubs = [{"type": "Journal Article", "issns": [],
             "journal": "Journal of Financial Economics"}]
    abdc.enrich(pubs, verbose=False)
    assert pubs[0]["abdc"] == "A*"
    assert pubs[0]["abdc_match"] == "title"
    assert pubs[0]["abdc_title"] == "Journal of Financial Economics"


def test_no_match_leaves_abdc_blank_and_match_none():
    _install_lookup([("Accounting Review", "A*", None)])
    pubs = [{"type": "Journal Article", "issns": [], "journal": "Unknown Journal"}]
    abdc.enrich(pubs, verbose=False)
    assert pubs[0]["abdc"] is None
    assert pubs[0]["abdc_match"] is None


def test_abdc_match_not_added_to_export_columns():
    """abdc_match is an internal diagnostic key, not an exported column —
    export.build_publications must not leak it."""
    from export import build_publications
    _install_lookup([("Accounting Review", "A*", None)])
    pubs = [{"type": "Journal Article", "issns": [], "journal": "Accounting Review",
             "name": "Sarah Adams", "title": "A study of things"}]
    abdc.enrich(pubs, verbose=False)
    out = build_publications(pubs, verbose=False)
    assert "abdc_match" not in out[0]
    assert out[0]["quality_rank"] == "A*"


# --------------------------------------------------------------- FIX E2

def test_title_matched_row_with_no_issns_gets_abdc_issns():
    _install_lookup([("Journal of Financial Economics", "A*", ["0304-405X", "1879-2774"])])
    pubs = [{"type": "Journal Article", "issns": [],
             "journal": "Journal of Financial Economics"}]
    abdc.enrich(pubs, verbose=False)
    assert pubs[0]["abdc_match"] == "title"
    assert pubs[0]["issns"] == ["0304-405X", "1879-2774"]
    assert pubs[0]["issn_source"] == "abdc_title"


def test_title_matched_row_with_existing_issns_is_untouched():
    _install_lookup([("Journal of Financial Economics", "A*", ["0304-405X"])])
    pubs = [{"type": "Journal Article", "issns": ["9999-9999"],
             "journal": "Journal of Financial Economics"}]
    abdc.enrich(pubs, verbose=False)
    assert pubs[0]["abdc_match"] == "title"
    assert pubs[0]["issns"] == ["9999-9999"]
    assert "issn_source" not in pubs[0]


def test_issn_matched_row_issns_field_is_untouched():
    _install_lookup([("Accounting Review", "A*", "1234-5678")])
    pubs = [{"type": "Journal Article", "issns": ["1234-5678"],
             "journal": "Accounting Review"}]
    abdc.enrich(pubs, verbose=False)
    assert pubs[0]["abdc_match"] == "issn"
    assert pubs[0]["issns"] == ["1234-5678"]
    assert "issn_source" not in pubs[0]


# --------------------------------------------------------------- collision

def test_title_collision_with_different_ratings_raises(monkeypatch):
    """Exercises the real _build() collision check: two spreadsheet rows
    whose titles normalise to the same key but carry different ratings
    must fail loudly, not silently pick one."""
    import pandas as pd

    df = pd.DataFrame({
        "Journal Title": ["Abacus", "Abacus!!"],
        "2025 rating": ["A", "B"],
        "ISSN": ["1111-1111", ""],
        "ISSNOnline": ["", ""],
    })
    monkeypatch.setattr(abdc.pd, "read_excel", lambda *a, **k: df)
    abdc._lookup = None
    abdc._title_lookup = None
    with pytest.raises(RuntimeError, match="collision"):
        abdc._build()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
