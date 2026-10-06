"""Offline tests for the v26 ANU fixes (docs/DECISIONS.md, 28 Sep 2026):
author data on profile rows, the repository-journal repair, and the
title-text guards that run after export._harmonise_doi_metadata. Each export
guard is also run on a non-ANU row, which must come back unchanged.

    python -m pytest tests/test_anu_export_guards.py -q
"""
import copy
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anu_scraper                                            # noqa: E402
import export                                                 # noqa: E402
from base_scrapers import anu                                 # noqa: E402

ANU = {"Louise Lu", "Greg Shailer", "Neil Fargher", "Sarah Adams", "Tracy (Kun) Wang"}


def _publication(researcher_name="Louise Lu", doi=None, coauthors=None, author_count=1):
    return anu_scraper.Publication(
        researcher_name=researcher_name,
        researcher_profile_url="https://rsa.anu.edu.au/people/x",
        title="A paper about corporate information production", journal_name="European Accounting Review",
        year=2023, doi=doi, article_url=None, abdc_self_reported="none",
        coauthors=coauthors, author_count=author_count,
    )


# ------------------------------------------------------------ Task A: authors

def test_hyphenated_initials_are_not_counted_as_authors():
    assert anu_scraper.count_named_authors("Liu, W.-M. , Yu, J. & Zhang, B") == 3
    assert anu_scraper.count_named_authors("Le, A. T., Le, T.-H., Liu, W.-M. & Fong, K., Y.") == 4


def test_doi_row_leaves_authors_to_enrichment_with_owner_inclusive_fallback():
    pub = _publication(doi="10.1/x", coauthors="Kathy Wang, Leye Li and Mark Wilson", author_count=3)
    row, _ = anu._map_publication(pub, "Louise Lu", {}, Counter())
    assert row["n_authors"] is None and row["authors"] is None
    assert row["_anu_profile_n_authors"] == 4          # 3 named + Louise Lu herself
    assert row["_anu_profile_authors"] == "Louise Lu; Kathy Wang; Leye Li; Mark Wilson"


def test_doi_row_without_coauthor_text_has_no_fallback():
    """The parser's default count of 1 is a guess, not a count: blank wins."""
    row, _ = anu._map_publication(_publication(doi="10.1/x"), "Louise Lu", {}, Counter())
    assert row["n_authors"] is None
    assert "_anu_profile_n_authors" not in row


def test_no_doi_row_strips_list_number_and_counts_owner_once():
    pub = _publication(researcher_name="Wai-Man (Raymond) Liu",
                       coauthors="19. Liu, W.-M. , Yu, J. & Zhang, B", author_count=4)
    row, _ = anu._map_publication(pub, "Wai-Man (Raymond) Liu", {}, Counter())
    # v27: the owner keeps their place in the citation, under their display name.
    assert row["authors"] == "Wai-Man (Raymond) Liu; J. Yu; B Zhang"
    assert row["n_authors"] == 3                         # owner already named


def test_no_doi_row_count_never_below_named_authors():
    pub = _publication(coauthors="L.Y. Lu & M. Wilson", researcher_name="Greg Shailer")
    row, _ = anu._map_publication(pub, "Greg Shailer", {}, Counter())
    assert row["n_authors"] == 3                         # 2 named + the owner


def test_author_fallback_applies_only_when_enrichment_found_nothing():
    row = {"name": "Louise Lu", "n_authors": None, "authors": None,
           "_anu_profile_n_authors": 4, "_anu_profile_authors": "Kathy Wang, Leye Li and Mark Wilson"}
    assert export._anu_author_fallback(row, ANU)
    assert row["n_authors"] == 4
    enriched = {"name": "Louise Lu", "n_authors": 4, "authors": "Dongyue Wang; Leye Li; Louise Yi Lu; Mark D. Wilson",
                "_anu_profile_n_authors": 3}
    assert not export._anu_author_fallback(enriched, ANU)
    assert enriched["n_authors"] == 4


def test_author_fallback_leaves_non_anu_row_untouched():
    row = {"name": "Someone Else", "n_authors": None, "authors": None,
           "_anu_profile_n_authors": 4, "_anu_profile_authors": "A, B"}
    before = copy.deepcopy(row)
    assert not export._anu_author_fallback(row, ANU)
    assert row == before


# ------------------------------------------------- Task C: repository journals

_CITATION = ("Adams, S, Kilcullen, L, Callis, Z & Flatau, P 2020, 'Measuring and accounting for "
             "outcomes in Australian human services charities', Third Sector Review, vol. 26, no. 1, "
             "pp. 108-138.")


def _openalex(citation=_CITATION):
    return lambda doi: {"primary_location": {"raw_source_name": citation}, "locations": []}


def _adams_row(name="Sarah Adams"):
    return {"name": name, "doi": "10.3316/informit.446762770484313",
            "title": "Measuring and accounting for outcomes in Australian human services charities",
            "journal": "UWA Profiles and Research Repository (University of Western Australia)",
            "abdc": None, "abdc_title": None, "issns": []}


def test_repository_journal_is_replaced_by_the_citation_journal():
    row = _adams_row()
    assert export._anu_repository_journal_repair(row, ANU, fetch=_openalex()) == "Third Sector Review"
    assert row["journal"] == "Third Sector Review"
    assert row["abdc"] == "C"


def test_repository_repair_needs_the_citation_to_be_for_this_paper():
    row = _adams_row()
    other = _CITATION.replace("Measuring and accounting for outcomes", "A different paper")
    assert export._anu_repository_journal_repair(row, ANU, fetch=_openalex(other)) is None
    assert row["journal"].startswith("UWA Profiles")


def test_repository_repair_leaves_non_anu_row_untouched():
    row = _adams_row(name="Someone Else")
    before = copy.deepcopy(row)
    assert export._anu_repository_journal_repair(row, ANU, fetch=_openalex()) is None
    assert row == before


# ---------------------------------------------------- Task E: title text guards

def test_mojibake_is_repaired_only_when_exact():
    assert export._anu_fix_mojibake("on auditorsâ€™ evaluation") == "on auditors’ evaluation"
    assert export._anu_fix_mojibake("Ã") == "Ã"                   # not a clean round trip
    assert export._anu_fix_mojibake("plain title") == "plain title"


def test_lowercase_title_takes_the_cased_copy_of_the_same_doi():
    rows = [{"name": "Greg Shailer", "doi": "10.1016/j.ememar.2014.11.002",
             "title": "government ownership and the cost of debt for chinese listed corporations",
             "journal_name": "Emerging Markets Review"}]
    pubs = [{"doi": "10.1016/j.ememar.2014.11.002",
             "title": "government ownership and the cost of debt for chinese listed corporations"},
            {"doi": "10.1016/j.ememar.2014.11.002",
             "title": "Government ownership and the cost of debt for Chinese listed corporations"}]
    export._anu_repair_title_text(rows, pubs, ANU, crossref_fetch=lambda d: {})
    assert rows[0]["title"] == "Government ownership and the cost of debt for Chinese listed corporations"


def test_lowercase_title_uses_strictly_verified_crossref_record():
    rows = [{"name": "Greg Shailer", "doi": "10.1111/j.1099-1123.2004.00095.x",
             "title": "discretionary pricing in a monopolistic audit market",
             "journal_name": "International Journal of Auditing", "year": "2004"}]
    pubs = [dict(rows[0])]
    record = {"title": ["Discretionary Pricing in a Monopolistic Audit Market"],
              "container-title": ["International Journal of Auditing"],
              "issued": {"date-parts": [[2004]]}, "author": [{"family": "Shailer"}]}
    export._anu_repair_title_text(rows, pubs, ANU, crossref_fetch=lambda d: record)
    assert rows[0]["title"] == "Discretionary Pricing in a Monopolistic Audit Market"


def test_crossref_record_failing_the_strict_check_is_not_used():
    rows = [{"name": "Greg Shailer", "doi": "10.1/x", "title": "discretionary pricing in a monopolistic audit market",
             "journal_name": "International Journal of Auditing", "year": "2004"}]
    wrong_journal = {"title": ["Discretionary Pricing in a Monopolistic Audit Market"],
                     "container-title": ["Some Other Journal"],
                     "issued": {"date-parts": [[2004]]}, "author": [{"family": "Shailer"}]}
    export._anu_repair_title_text(rows, [dict(rows[0])], ANU, crossref_fetch=lambda d: wrong_journal)
    assert rows[0]["title"] == "discretionary pricing in a monopolistic audit market"


def test_shailer_1994_all_caps_title_stays():
    title = "ASSET SPECIFICITY, AGENCY AND INFORMATION ASYMMETRY IN OWNER-MANAGED FIRMS"
    rows = [{"name": "Greg Shailer", "doi": "10.1142/s0218495894000240", "title": title,
             "journal_name": "Journal of Enterprising Culture", "year": "1994"}]
    crossref = {"title": [title], "container-title": ["Journal of Enterprising Culture"],
                "issued": {"date-parts": [[1994]]}, "author": [{"family": "Shailer"}]}
    export._anu_repair_title_text(rows, [dict(rows[0])], ANU, crossref_fetch=lambda d: crossref)
    assert rows[0]["title"] == title


def test_title_guards_leave_non_anu_row_untouched():
    rows = [{"name": "Someone Else", "doi": "10.1016/j.ememar.2014.11.002",
             "title": "government ownership and the cost of debt for chinese listed corporations",
             "journal_name": "Emerging Markets Review â€™"}]
    pubs = [{"doi": "10.1016/j.ememar.2014.11.002",
             "title": "Government ownership and the cost of debt for Chinese listed corporations"}]
    before = copy.deepcopy(rows)
    assert export._anu_repair_title_text(rows, pubs, ANU, crossref_fetch=lambda d: {}) == []
    assert rows == before


def test_footnote_asterisks_do_not_add_authors():
    """Tracy (Kun) Wang's page marks student co-authors with * / ** / ***."""
    assert anu_scraper.count_named_authors("Sun, A.***, Wang, K.T. , Wu, Y.**, Zhu, N.Z.**") == 4
