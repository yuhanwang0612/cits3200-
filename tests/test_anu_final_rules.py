"""Offline tests for the v27 ANU rules (docs/DECISIONS.md, 5 Oct 2026):
owner-inclusive author names, unparsed entries recovered from their own DOI,
one record per DOI (registered title, print year), book reviews, profile
copies of published rows, and the trailing footnote marker. Every export
rule is also run on a non-ANU row, which must come back unchanged.

    python -m pytest tests/test_anu_final_rules.py -q
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

ANU = {"Kathy Wang", "Janet Lee", "Mark Wilson", "Lijuan Zhang", "Isabel Wang",
       "Xin (Kelly) Liu", "Greg Shailer", "Susanna Ho", "Lily Chen", "Louise Lu"}


def _pub(name, raw, coauthors=None, doi=None, title="A paper", kind="journal_article"):
    return anu_scraper.Publication(
        researcher_name=name, researcher_profile_url="https://rsa.anu.edu.au/people/x",
        title=title, journal_name="Accounting and Finance", year=2020, doi=doi,
        article_url=None, abdc_self_reported=None, coauthors=coauthors, raw=raw,
        publication_type=kind,
    )


# ------------------------------------------------- Task E: owner-inclusive names

def test_with_clause_puts_the_owner_first():
    pub = _pub("Louise Lu", '2024. "Government Spending..." with Xuejun Jiang, Jeong-Bong Kim '
                            "and Yangxin Yu. Journal of Accounting and Public Policy",
               coauthors="Xuejun Jiang, Jeong-Bong Kim and Yangxin Yu")
    names, how, ok = anu._owner_inclusive_names(pub, "Louise Lu", "Louise Lu")
    assert names == ["Louise Lu", "Xuejun Jiang", "Jeong-Bong Kim", "Yangxin Yu"] and ok


def test_with_clause_does_not_mistake_a_namesake_coauthor_for_the_owner():
    """'with Wang, C., ...' on Alex Wang's page: C. Wang is someone else."""
    pub = _pub("Alex Wang", "Effects of ... (2025), Advances in Management Accounting "
                            "(with Wang, C., Wu, H. and Chong, V.) (ABDC - A)",
               coauthors="Wang, C., Wu, H. and Chong, V")
    names, _, _ = anu._owner_inclusive_names(pub, "Alex Wang", "Alex Wang")
    assert names == ["Alex Wang", "C. Wang", "H. Wu", "V Chong"]


def test_leading_author_list_is_read_and_owner_keeps_their_place():
    pub = _pub("Janet Lee", "Lee, J. and Shailer, G. (2008) “The Effect of Board Related Reforms "
                            "on Investors’ Confidence”, Australian Accounting Review, Vol. 18")
    names, how, ok = anu._owner_inclusive_names(pub, "Janet Lee", "Janet Lee")
    assert names == ["Janet Lee", "G. Shailer"] and how == "leading author list"
    pub = _pub("Mark Wilson", "Shailer, G. and M. Wilson, 2001, \"Accounting for Owner's Equity in "
                              "Closely-held Corporations\", Asian Review of Accounting")
    assert anu._owner_inclusive_names(pub, "Mark Wilson", "Mark Wilson")[0] == ["G. Shailer", "Mark Wilson"]


def test_owner_written_surname_then_given_name():
    pub = _pub("Mark Wilson", "Wilson, Mark and Greg Shailer, 2004, 'The Term Structure of Discount "
                              "Rates and Capital Budgeting Practice', Journal of Applied ...")
    assert anu._owner_inclusive_names(pub, "Mark Wilson", "Mark Wilson")[0] == ["Mark Wilson", "Greg Shailer"]


def test_a_citation_that_starts_with_its_title_is_owner_only():
    pub = _pub("Sonali Walpola", "‘The potential of section 100A ITAA36 to address tax avoidance "
                                 "using trusts’ (2024) 39(4) Australian Tax Forum 519")
    assert anu._owner_inclusive_names(pub, "Sonali Walpola", "Sonali Walpola")[:2] == \
        (["Sonali Walpola"], "owner only")


def test_dash_with_clause():
    pub = _pub("Sonali Walpola", "‘Justice and the Australian income tax base: taxing capital gains "
                                 "and trusts’ Australian Tax Forum (2025)—with J Minas; in press.")
    assert anu._owner_inclusive_names(pub, "Sonali Walpola", "Sonali Walpola")[0] == \
        ["Sonali Walpola", "J Minas"]


def test_owner_published_under_another_initial():
    """Isabel Z. Wang publishes as 'Z. Wang'; one same-surname name is the owner."""
    pub = _pub("Isabel Wang", "Fukofuka, P., N. Fargher and Z. Wang, 2014, The influence of ...",
               coauthors="Fukofuka, P., N. Fargher and Z. Wang")
    assert anu._owner_inclusive_names(pub, "Isabel Wang", "Isabel Wang")[0] == \
        ["P. Fukofuka", "N. Fargher", "Isabel Wang"]


def test_no_doi_row_count_equals_names():
    pub = _pub("Janet Lee", "Kober, R., Lee, J. and Ng, J. (2012) “A common conceptual framework”, "
                            "Accounting and Business Research")
    row, _ = anu._map_publication(pub, "Janet Lee", {}, Counter())
    assert row["authors"] == "R. Kober; Janet Lee; J. Ng"
    assert row["n_authors"] == 3 == len(export.normalize_authors(row["authors"]).split("; "))


def test_garbled_list_is_not_used_as_a_doi_row_fallback():
    pub = _pub("Lily Chen", "2023. Hou, F. Wang, R. Ng, S., Zhu, F., Chen, L. Anistropic span ...",
               coauthors="Hou, F. Wang, R. Ng, S., Zhu, F., Chen, L.", doi="10.1017/s1351324924000019")
    row, _ = anu._map_publication(pub, "Lily Chen", {}, Counter())
    assert row["n_authors"] is None and "_anu_profile_n_authors" not in row


# --------------------------------------- Task G: unparsed entry with its own DOI

_TKDE = {"type": "journal-article", "title": ["Fine-Grained Entity Typing with a Type Taxonomy: "
                                              "a Systematic Review"],
         "container-title": ["IEEE Transactions on Knowledge and Data Engineering"],
         "issued": {"date-parts": [[2022]]}, "published-print": {"date-parts": [[2022]]},
         "author": [{"family": "Wang"}, {"family": "Chen"}], "ISSN": ["1041-4347"]}
_TKDE_RAW = ("2022. Wang, R., Hou, F.,Cahan, Chen, L., Jia, X., S., Ji, W. Fine-grained entity typing "
             "with a type taxonomy: a systematic review. IEEE Transactions on Knowledge and Data "
             "Engineering , first online February 2022. doi: 10.1109/TKDE.2022.3148980 .")


def test_unparsed_entry_with_its_own_doi_is_recovered():
    pub = _pub("Lily Chen", _TKDE_RAW, doi="10.1109/TKDE.2022.3148980")
    row = anu._rescue_unparsed_with_doi(pub, "Lily Chen", fetch=lambda d: _TKDE)
    assert row["doi"] == "10.1109/tkde.2022.3148980" and row["year"] == "2022"
    assert row["journal"] == "IEEE Transactions on Knowledge and Data Engineering"
    assert row["title"].startswith("Fine-Grained Entity Typing") and row["type"] == "Journal Article"


def test_unparsed_entry_is_not_recovered_when_the_record_does_not_match():
    pub = _pub("Lily Chen", _TKDE_RAW, doi="10.1109/TKDE.2022.3148980")
    other_title = dict(_TKDE, title=["A Different Paper Altogether"])
    not_hers = dict(_TKDE, author=[{"family": "Wang"}])
    chapter = dict(_TKDE, type="book-chapter")
    for msg in (other_title, not_hers, chapter, {}):
        assert anu._rescue_unparsed_with_doi(pub, "Lily Chen", fetch=lambda d, m=msg: m) is None
    assert anu._rescue_unparsed_with_doi(_pub("Lily Chen", _TKDE_RAW), "Lily Chen",
                                         fetch=lambda d: _TKDE) is None          # no DOI printed


# ------------------------------------------- Task D/F: one record per DOI

def _record(title, journal, online=None, printed=None, issued=None, **extra):
    msg = {"title": [title], "container-title": [journal], **extra}
    for key, year in (("published-online", online), ("published-print", printed),
                      ("issued", issued or online or printed)):
        if year:
            msg[key] = {"date-parts": [[year]]}
    return msg


def test_rows_sharing_a_doi_take_the_registered_title_and_print_year():
    rows = [{"name": "Sonali Walpola", "doi": "10.1080/09638180.2024.2433231",
             "title": "R&D Tax Incentive Reforms and Firm Value: Worldwide Evidence", "year": "2024",
             "journal_name": "The European Accounting Review"},
            {"name": "Tracy (Kun) Wang", "doi": "10.1080/09638180.2024.2433231",
             "title": "R&D Tax Incentive Reforms Around the World: The Impact on Firm Value",
             "year": "2024", "journal_name": "The European Accounting Review"}]
    rec = _record("R&D Tax Incentive Reforms Around the World: The Impact on Firm Value",
                  "European Accounting Review", online=2024, printed=2025)
    export._anu_align_doi_records(rows, rows, {"Sonali Walpola", "Tracy (Kun) Wang"}, fetch=lambda d: rec)
    assert {r["title"] for r in rows} == {"R&D Tax Incentive Reforms Around the World: The Impact on Firm Value"}
    assert {r["year"] for r in rows} == {"2025"}


def test_doi_is_lowercased_and_markup_removed():
    rows = [{"name": "Lijuan Zhang", "doi": "10.1111/ACFI.13008", "title": "Manager equity",
             "year": "2022", "journal_name": "Accounting and Finance", "link": "https://doi.org/10.1111/acfi.13008"}]
    rec = _record("Managers'\n   <scp>equity‐based</scp>\n   compensation and\n   <scp>soft‐talk</scp>"
                  "\n   management cash flow forecasts", "Accounting &amp; Finance", online=2022, printed=2026)
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: rec)
    assert rows[0]["doi"] == "10.1111/acfi.13008"
    assert rows[0]["title"] == "Managers' equity‐based compensation and soft‐talk management cash flow forecasts"
    assert rows[0]["year"] == "2026"


def test_lily_chen_typos_follow_the_doi_record():
    rows = [{"name": "Lily Chen", "doi": "10.1017/s1351324924000019", "year": "2023",
             "journal_name": "Natural Language Engineering",
             "title": "Anistropic span embeddings and the negative impact of higher-order inference "
                      "for coreference resolution: an empricial analysis"}]
    rec = _record("Anisotropic span embeddings and the negative impact of higher-order inference for "
                  "coreference resolution: An empirical analysis", "Natural Language Engineering",
                  online=2024, printed=2024)
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: rec)
    assert rows[0]["title"].startswith("Anisotropic") and rows[0]["year"] == "2024"


def test_record_for_another_journal_is_refused():
    rows = [{"name": "Greg Shailer", "doi": "10.1/x", "title": "Original", "year": "2010",
             "journal_name": "Abacus"}]
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: _record("Other", "Some Other Journal",
                                                                           printed=2011))
    assert rows[0]["title"] == "Original" and rows[0]["year"] == "2010"


def test_dropped_italic_run_is_filled_from_a_pipeline_copy_or_refused():
    full = "The impact of Ball and Brown (1968) on generations of research"
    rows = [{"name": "Marvin Wee", "doi": "10.1016/j.pacfin.2019.01.006", "title": full,
             "year": "2019", "journal_name": "Pacific-Basin Finance Journal"}]
    rec = _record("The impact of   on generations of research", "Pacific-Basin Finance Journal", printed=2019)
    export._anu_align_doi_records(rows, rows, {"Marvin Wee"}, fetch=lambda d: rec)
    assert rows[0]["title"] == full
    short = [{"name": "Marvin Wee", "doi": "10.1/y", "title": "Responsible science", "year": "2019",
              "journal_name": "Pacific-Basin Finance Journal"}]
    rec = _record("Responsible science: Celebrating the 50-year legacy of   using a registration-based "
                  "framework", "Pacific-Basin Finance Journal", printed=2019)
    export._anu_align_doi_records(short, short, {"Marvin Wee"}, fetch=lambda d: rec)
    assert short[0]["title"] == "Responsible science"              # nothing to fill it from


def test_all_caps_registered_title_stays():
    title = "ASSET SPECIFICITY, AGENCY AND INFORMATION ASYMMETRY IN OWNER-MANAGED FIRMS"
    rows = [{"name": "Greg Shailer", "doi": "10.1142/s0218495894000240", "title": title,
             "year": "1994", "journal_name": "Journal of Enterprising Culture"}]
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: _record(
        title, "Journal of Enterprising Culture", printed=1994))
    assert rows[0]["title"] == title


def test_trailing_footnote_marker_is_stripped():
    rows = [{"name": "Susanna Ho", "title": "Nudging Moods to Induce Unplanned Purchases in "
                                            "Imperfect Mobile Personalization Contexts1"},
            {"name": "Susanna Ho", "title": "Industry 4"},
            {"name": "Susanna Ho", "title": "The role of COVID19"}]
    export._anu_strip_footnote_marker(rows, ANU)
    assert rows[0]["title"].endswith("Personalization Contexts")
    assert rows[1]["title"] == "Industry 4" and rows[2]["title"] == "The role of COVID19"


def test_one_row_per_researcher_and_doi():
    rows = [{"name": "Xin (Kelly) Liu", "doi": "10.1017/s0022109022001466", "title": "A"},
            {"name": "Xin (Kelly) Liu", "doi": "10.1017/s0022109022001466", "title": "A"},
            {"name": "Kun Li", "doi": "10.1017/s0022109022001466", "title": "A"}]
    assert len(export._anu_one_row_per_doi(rows, ANU | {"Kun Li"})) == 2


# --------------------------------------------------------- Task C: book reviews

_REVIEW = {"volume": "7", "page": "408-410", "title": ["Auditing and Assurance Services ..."]}


def test_book_review_record():
    assert export._is_book_review_record(_REVIEW)
    assert export._is_book_review_record({"volume": "59", "page": "2034-2035"})
    assert not export._is_book_review_record(dict(_REVIEW, abstract="<p>We study</p>"))
    assert not export._is_book_review_record({"page": "1-1"})               # early access
    assert not export._is_book_review_record({"volume": "50", "page": "447-480"})
    assert not export._is_book_review_record({"volume": "50", "page": "101265"})


def test_book_review_and_its_doi_less_copy_are_dropped():
    title = "Auditing and Assurance Services and Ethics in Australia: An Integrated Approach"
    rows = [{"name": "Greg Shailer", "doi": "10.1108/18325911111182330", "title": title},
            {"name": "Greg Shailer", "doi": None, "title": title},
            {"name": "Greg Shailer", "doi": "10.1/real", "title": "A real paper"}]
    kept = export._anu_drop_book_reviews(rows, ANU, {"10.1108/18325911111182330": _REVIEW})
    assert [r["title"] for r in kept] == ["A real paper"]


# ---------------------------------------------------- Task B: profile copies

def _copy(name, title, year, journal="Accounting and Finance", authors=""):
    return {"name": name, "doi": None, "source": "ANU staff profile", "title": title,
            "year": str(year), "journal_name": journal, "authors": authors}


def _published(name, title, year, doi, journal="Accounting and Finance", authors=""):
    return {"name": name, "doi": doi, "source": "ORCID", "title": title, "year": str(year),
            "journal_name": journal, "authors": authors}


def test_the_five_known_profile_copies_are_caught():
    cases = [
        (_copy("Isabel Wang", "The effects of tone at the top and coordination with external auditors on "
               "internal auditors’ assessments of the likelihood of financial misstatements", 2015,
               authors="Isabel Wang; N. Fargher"),
         _published("Isabel Wang", "The effects of tone at the top and coordination with external auditors "
                    "on internal auditors’ fraud risk assessments", 2017, "10.1111/acfi.12191",
                    authors="Isabel Z. Wang; NEIL L. FARGHER"),
         _record("x", "Accounting & Finance", online=2015, printed=2017)),
        (_copy("Mark Wilson", "Tax-Loss Selling, Corporate Shareholdings and the (Mis)pricing of Information "
               "Asymmetry", 2025, "Contemporary Accounting Research", "Mark Wilson; L. Zhang"),
         _published("Mark Wilson", "Corporate shareholdings, tax‐loss selling, and the (mis)pricing of "
                    "information asymmetry", 2025, "10.1111/1911-3846.13067",
                    "Contemporary Accounting Research", "Mark D. Wilson; Lijuan Zhang"), None),
        (_copy("Xin (Kelly) Liu", "The Role of Credit Rating Purchases in S&P 500 Membership Decisions", 2025,
               "Management Science", "Xin (Kelly) Liu; Shang-Jin Wei; Kun Li"),
         _published("Xin (Kelly) Liu", "Credit Rating Purchases and S&P 500 Index Membership Decisions",
                    2026, "10.1287/mnsc.2024.08157", "Management Science", "Kun Li; Xin Liu; Shang-Jin Wei"),
         None),
        (_copy("Janet Lee", "A common conceptual framework: Perspectives of public sector stakeholers", 2012,
               "Accounting and Business Research", "R. Kober; Janet Lee; J. Ng"),
         _published("Janet Lee", "Conceptual Framework Issues: Perspectives of Australian Public Sector "
                    "Stakeholders", 2012, "10.1080/00014788.2012.670383", "Accounting and Business Research",
                    "Ralph Kober; Janet Lee; Juliana Ng"), None),
        (_copy("Lijuan Zhang", "Manager equity based compensation and cash flow forecasts", 2022,
               authors="W. Wang; Lijuan Zhang"),
         _published("Lijuan Zhang", "Managers' equity‐based compensation and soft‐talk management cash flow "
                    "forecasts", 2026, "10.1111/acfi.13008", authors="Weixiao Wang; Lijuan Zhang"),
         _record("x", "Accounting & Finance", online=2022, printed=2026)),
    ]
    for cand, pub, rec in cases:
        assert export.anu_profile_copy_of(cand, pub, rec), cand["title"]


def test_a_different_paper_in_the_same_journal_is_not_a_copy():
    cand = _copy("Marvin Wee", "Responsible science: Celebrating the 50-year legacy of Ball and Brown (1968) "
                 "using a registration-based framework", 2019, "Pacific-Basin Finance Journal",
                 "H. Aman; W. Beekes; Marvin Wee")
    other = _published("Marvin Wee", "The impact of Ball and Brown (1968) on generations of research", 2019,
                       "10.1016/j.pacfin.2019.01.006", "Pacific-Basin Finance Journal",
                       "NEIL L. FARGHER; Marvin Wee")
    assert not export.anu_profile_copy_of(cand, other)


def test_profile_copy_needs_year_journal_and_shared_coauthor():
    cand = _copy("Isabel Wang", "The effects of tone at the top on auditors", 2015,
                 authors="Isabel Wang; N. Fargher")
    pub = _published("Isabel Wang", "The effects of tone at the top on auditors", 2015, "10.1/x",
                     authors="Isabel Wang; N. Fargher")
    assert export.anu_profile_copy_of(cand, pub)
    assert not export.anu_profile_copy_of(cand, dict(pub, year="2019"))
    assert not export.anu_profile_copy_of(cand, dict(pub, journal_name="Abacus"))
    assert not export.anu_profile_copy_of(cand, dict(pub, authors="Isabel Wang; P. Somebody"))
    assert not export.anu_profile_copy_of(dict(cand, source="ORCID"), pub)


# ------------------------------------------------------------- gating

def _non_anu_rows():
    return [{"name": "Someone Else", "doi": "10.1111/ACFI.12191", "source": "ORCID",
             "title": "The effects of tone at the top Contexts1", "year": "2017",
             "journal_name": "Accounting and Finance", "authors": "A. B; C. D", "link": None},
            {"name": "Someone Else", "doi": "10.1111/ACFI.12191", "source": "ORCID",
             "title": "The effects of tone at the top Contexts1", "year": "2017",
             "journal_name": "Accounting and Finance", "authors": "A. B; C. D", "link": None},
            {"name": "Someone Else", "doi": None, "source": "ANU staff profile",
             "title": "The effects of tone at the top", "year": "2015",
             "journal_name": "Accounting and Finance", "authors": "", "link": None},
            {"name": "Someone Else", "doi": "10.1108/18325911111182330", "source": "ORCID",
             "title": "A book review", "year": "2011", "journal_name": "J", "authors": "", "link": None}]


def test_v27_rules_leave_non_anu_rows_untouched():
    rows = _non_anu_rows()
    before = copy.deepcopy(rows)
    calls = []

    def fetch(doi):
        calls.append(doi)
        return _REVIEW
    out = export._anu_final_rules(rows, rows, ANU, crossref_fetch=fetch)
    assert out == before and rows == before
    assert calls == []                                   # no lookups for non-ANU rows


# ------------------------------------------------------- refinements (run 1)

def test_footnote_symbols_are_stripped_too():
    rows = [{"name": "Susanna Ho", "title": "Do Alma Mater Ties Between the Auditor and Audit "
                                            "Committee Affect Audit Quality? *"},
            {"name": "Susanna Ho", "title": "Product Market Competition and Voluntary Corporate "
                                            "Social Responsibility Disclosures†"}]
    export._anu_strip_footnote_marker(rows, ANU)
    assert rows[0]["title"].endswith("Audit Quality?") and rows[1]["title"].endswith("Disclosures")


def test_journal_registered_with_a_subtitle_or_matched_by_issn():
    rows = [{"name": "Greg Shailer", "doi": "10.1177/0266242694123003", "title": "x", "year": "1994",
             "journal_name": "International Small Business Journal"}]
    rec = _record("Capitalists and Entrepreneurs in Owner-Managed Firms",
                  "International Small Business Journal: Researching Entrepreneurship", printed=1994)
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: rec)
    assert rows[0]["title"] == "Capitalists and Entrepreneurs in Owner-Managed Firms"
    rows = [{"name": "Susanna Ho", "doi": "10.1145/1453794.1453800", "title": "x", "year": "2008",
             "journal_name": "Data Base for Advances in Information Systems"}]
    pubs = [dict(rows[0], issns=["0095-0033"])]
    rec = _record("Personalization and choice behavior", "ACM SIGMIS Database: the DATABASE for "
                  "Advances in Information Systems", printed=2008, ISSN=["0095-0033", "1532-0936"],
                  subtitle=["the role of personality traits"])
    export._anu_align_doi_records(rows, pubs, ANU, fetch=lambda d: rec)
    assert rows[0]["title"] == "Personalization and choice behavior: the role of personality traits"


def test_doubled_subtitle_takes_the_registered_title():
    rows = [{"name": "Tracy (Kun) Wang", "doi": "10.1111/1911-3846.12729", "year": "2021",
             "journal_name": "Contemporary Accounting Research",
             "title": "Corporate Governance Reforms and Cross-Listings: International Evidence: "
                      "International Evidence"}]
    rec = _record("Corporate Governance Reforms and\n  <scp>Cross‐Listings</scp>\n  : International "
                  "Evidence*", "Contemporary Accounting Research", printed=2021)
    export._anu_align_doi_records(rows, rows, {"Tracy (Kun) Wang"}, fetch=lambda d: rec)
    export._anu_strip_footnote_marker(rows, {"Tracy (Kun) Wang"})
    assert rows[0]["title"] == "Corporate Governance Reforms and Cross‐Listings: International Evidence"


def test_registered_title_missing_a_subtitle_is_refused():
    rows = [{"name": "Greg Shailer", "doi": "10.1/z", "year": "2010", "journal_name": "Abacus",
             "title": "Audit fees: evidence from the Australian market"}]
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: _record("Audit fees", "Abacus",
                                                                           printed=2010))
    assert rows[0]["title"] == "Audit fees: evidence from the Australian market"


def test_rows_sharing_a_doi_are_unified_when_one_record_was_refused():
    rows = [{"name": "Greg Shailer", "doi": "10.1/q", "title": "Short", "year": "2010"},
            {"name": "Mark Wilson", "doi": "10.1/q", "title": "Short title, longer", "year": "2011"},
            {"name": "Louise Lu", "doi": "10.1/q", "title": "Short title, longer", "year": "2011"}]
    export._anu_one_title_per_doi(rows, ANU)
    assert {r["title"] for r in rows} == {"Short title, longer"} and {r["year"] for r in rows} == {"2011"}
    other = [{"name": "Someone Else", "doi": "10.1/q", "title": "A", "year": "1"},
             {"name": "Someone Else", "doi": "10.1/q", "title": "B", "year": "2"}]
    before = copy.deepcopy(other)
    export._anu_one_title_per_doi(other, ANU)
    assert other == before


def test_layout_spacing_is_not_a_dropped_run():
    rows = [{"name": "Greg Shailer", "doi": "10.1177/026624268900700405", "year": "1989",
             "journal_name": "International Small Business Journal",
             "title": "The Predictability of Small Enterprise Failures: Evidence and Issues"}]
    rec = _record("The Predictability of Small Enterprise Failures: Evidence and               Issues",
                  "International Small Business Journal", printed=1989)
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: rec)
    assert rows[0]["title"] == "The Predictability of Small Enterprise Failures: Evidence and Issues"
    assert not [e for e in export.ANU_V27_LOG if e[0].startswith("refused") and e[2] == rows[0]["doi"]]


def test_rescue_compares_the_last_word_of_a_crossref_family_name():
    raw = ('2024. "Government Spending and CEO Equity Incentives" with Xuejun Jiang. Journal of '
           "Accounting and Public Policy. doi: 10.1016/j.jaccpubpol.2024.107263")
    rec = dict(_TKDE, title=["Government Spending and CEO Equity Incentives"],
               author=[{"given": "Louise", "family": "Yi Lu"}])
    pub = _pub("Louise Lu", raw, doi="10.1016/j.jaccpubpol.2024.107263")
    assert anu._rescue_unparsed_with_doi(pub, "Louise Lu", fetch=lambda d: rec) is not None


# ---------------------------------------- Task C: OpenAlex proceedings papers

def _oa_work(raw_source_name):
    return {"primary_location": {"raw_source_name": "Journal of the Association for Information Systems"},
            "locations": [{"raw_source_name": raw_source_name}]}


def test_openalex_only_proceedings_paper_is_dropped():
    row = {"name": "Susanna Ho", "doi": None, "source": "OpenAlex", "link": "https://openalex.org/W70483390",
           "title": "The Effects of Location-Based Mobile Personalization on Users' Behavior"}
    kept = export._anu_drop_openalex_proceedings([row], ANU, fetch=lambda w: _oa_work("PACIS 2010 Proceedings"))
    assert kept == []


def test_proceedings_rule_needs_openalex_source_no_doi_and_a_conference_citation():
    base = {"name": "Susanna Ho", "doi": None, "source": "OpenAlex", "link": "https://openalex.org/W1",
            "title": "A journal paper"}
    journal = lambda w: _oa_work("Journal of the Association for Information Systems")
    conf = lambda w: _oa_work("PACIS 2010 Proceedings")
    assert export._anu_drop_openalex_proceedings([base], ANU, fetch=journal) == [base]
    for other in (dict(base, doi="10.1/x"), dict(base, source="ANU staff profile"),
                  dict(base, name="Someone Else")):
        assert export._anu_drop_openalex_proceedings([other], ANU, fetch=conf) == [other]


# ------------------------------------------- v27.1: running heads and casing

def test_running_head_subtitle_is_not_appended():
    cases = [
        ("Integrated Reporting: An Opportunity for Australia's Not-for-Profit Sector",
         "Integrated Reporting for Not-for-Profit Sector"),
        ("Do Publicly Signalled Earnings Management Incentives Affect Analyst Forecast Accuracy?",
         "EARNINGS MANAGEMENT SIGNALS AND FORECAST ACCURACY"),
        ("General equilibrium analysis of hold-up problem and non-exclusive franchise contract",
         "hold-up problem and franchise contracts"),
    ]
    for main, head in cases:
        assert export._is_running_head(head, main), head
        title, _ = export._anu_registered_title({"title": [main], "subtitle": [head]})
        assert title == main


def test_genuine_subtitle_is_still_appended():
    for main, sub in (("Private equity coming out of the dark",
                       "The motivations behind private equity activity in Australia"),
                      ("Personalization and choice behavior", "the role of personality traits"),
                      ("Reverse merger audit fee premium", "Evidence from China")):
        assert not export._is_running_head(sub, main), sub
        assert export._anu_registered_title({"title": [main], "subtitle": [sub]})[0] == f"{main}: {sub}"


def test_all_caps_registered_title_yields_to_a_mixed_case_copy():
    cased = "General equilibrium analysis of hold-up problem and non-exclusive franchise contract"
    rows = [{"name": "Wai-Man (Raymond) Liu", "doi": "10.1111/j.1468-0106.2010.00523.x", "year": "2010",
             "journal_name": "Pacific Economic Review", "title": cased}]
    rec = _record(cased.upper(), "Pacific Economic Review", printed=2010,
                  subtitle=["hold-up problem and franchise contracts"])
    export._anu_align_doi_records(rows, rows, {"Wai-Man (Raymond) Liu"}, fetch=lambda d: rec)
    assert rows[0]["title"] == cased


def test_all_caps_with_no_mixed_case_copy_stays_all_caps():
    title = "ASSET SPECIFICITY, AGENCY AND INFORMATION ASYMMETRY IN OWNER-MANAGED FIRMS"
    rows = [{"name": "Greg Shailer", "doi": "10.1142/s0218495894000240", "title": title,
             "year": "1994", "journal_name": "Journal of Enterprising Culture"}]
    pubs = rows + [{"title": "Some other paper", "doi": "10.1/other"}]
    export._anu_align_doi_records(rows, pubs, ANU, fetch=lambda d: _record(
        title, "Journal of Enterprising Culture", printed=1994))
    assert rows[0]["title"] == title


def test_running_head_rule_leaves_non_anu_rows_untouched():
    rows = [{"name": "Someone Else", "doi": "10.1111/j.1835-2561.2011.00143.x", "year": "2011",
             "journal_name": "Australian Accounting Review", "title": "As it was"}]
    before = copy.deepcopy(rows)
    rec = _record("INTEGRATED REPORTING", "Australian Accounting Review", printed=2011,
                  subtitle=["Integrated Reporting for Not-for-Profit Sector"])
    export._anu_align_doi_records(rows, rows, ANU, fetch=lambda d: rec)
    assert rows == before


# --------------------------------------------- v28 task A: ABDC inception year

def _tan_row(name="Rebecca Tan", year="2000", issn_source="abdc_title"):
    return {"name": name, "title": "Flights of fancy", "year": year,
            "journal": "Journal of Financial Reporting", "abdc": "A",
            "abdc_title": "Journal of Financial Reporting", "abdc_edition": "2025",
            "abdc_match": "title", "issns": ["2380-2154", "2380-2146"], "issn_source": issn_source,
            "sjr": 1.2, "sjr_quartile": "Q1", "impact_factor": 2.0}


def test_rating_withdrawn_when_year_predates_abdc_inception():
    row = _tan_row()
    assert export._anu_predates_abdc_inception(row, {"Rebecca Tan"}) == ("Journal of Financial Reporting", 2016)
    assert row["abdc"] is None and row["abdc_title"] is None
    assert row["issns"] == [] and row["sjr_quartile"] is None and row["impact_factor"] is None
    assert export.canonical_journal_name(row) == "Journal of Financial Reporting"


def test_rating_kept_from_the_inception_year_on():
    row = _tan_row(year="2016")
    assert export._anu_predates_abdc_inception(row, {"Rebecca Tan"}) is None
    assert row["abdc"] == "A"


def test_own_issns_are_kept_when_only_the_rating_is_withdrawn():
    row = _tan_row(issn_source=None)
    export._anu_predates_abdc_inception(row, {"Rebecca Tan"})
    assert row["abdc"] is None and row["issns"] == ["2380-2154", "2380-2146"] and row["sjr_quartile"] == "Q1"


def test_inception_rule_leaves_non_anu_rows_untouched():
    row = _tan_row(name="Someone Else")
    before = copy.deepcopy(row)
    assert export._anu_predates_abdc_inception(row, {"Rebecca Tan"}) is None
    assert row == before


# ---------------------------------------------- v28 task C: forthcoming status

def _status_rows():
    return [
        {"name": "Xin (Kelly) Liu", "doi": "10.1287/mnsc.2024.08157", "year": "2026",
         "title": "Credit Rating Purchases and S&P 500 Index Membership Decisions",
         "journal_name": "Management Science", "authors": "Kun Li; Xin Liu; Shang-Jin Wei"},
        {"name": "Kun Li", "doi": "10.1287/mnsc.2024.08157", "year": "2026",
         "title": "Credit Rating Purchases and S&P 500 Index Membership Decisions",
         "journal_name": "Management Science", "authors": "Kun Li; Xin Liu; Shang-Jin Wei"},
        {"name": "Mark Wilson", "doi": "10.1111/acfi.13262", "year": "2024",
         "title": "Aggregate analyst characteristics and forecasting performance",
         "journal_name": "Accounting and Finance", "authors": "Mark Wilson; Y. Wu"},
        {"name": "Sonali Walpola", "doi": None, "year": "2025",
         "title": "Justice and the Australian income tax base", "journal_name": "Australian Tax Forum",
         "authors": "Sonali Walpola; J Minas"},
        {"name": "Isabel Wang", "doi": None, "year": "2023",
         "title": "Performance measurement systems design choice", "journal_name": "Advances",
         "authors": "Isabel Wang"},
        {"name": "Scarlett Luo", "doi": "10.1111/abac.70030", "year": "2026", "title": "Good for CEOs",
         "journal_name": "Abacus", "authors": "Scarlett Luo"},
    ]


def _labelled_pubs():
    return [
        {"name": "Xin (Kelly) Liu", "doi": None, "_anu_forthcoming_label": True, "year": "2025",
         "title": "The Role of Credit Rating Purchases in S&P 500 Membership Decisions",
         "journal": "Management Science", "abdc_title": "Management Science",
         "authors": "Xin (Kelly) Liu; Shang-Jin Wei; Kun Li"},
        {"name": "Mark Wilson", "doi": None, "_anu_forthcoming_label": True, "year": "2024",
         "title": "Aggregate analyst characteristics and forecasting performance",
         "journal": "Accounting and Finance"},
        {"name": "Sonali Walpola", "doi": None, "_anu_forthcoming_label": True, "year": "2025",
         "title": "Justice and the Australian income tax base", "journal": "Australian Tax Forum"},
        {"name": "Isabel Wang", "doi": None, "_anu_forthcoming_label": True, "year": "2023",
         "title": "Performance measurement systems design choice", "journal": "Advances"},
    ]


_RECORDS = {"10.1287/mnsc.2024.08157": {"published-online": {"date-parts": [[2026, 3, 3]]}},
            "10.1111/acfi.13262": {"volume": "64", "published-print": {"date-parts": [[2024, 6]]}},
            "10.1111/abac.70030": {"published-online": {"date-parts": [[2026, 3, 10]]}}}


def test_forthcoming_only_when_labelled_and_not_in_an_issue():
    rows = _status_rows()
    names = {r["name"] for r in rows}
    export._anu_publication_status(rows, _labelled_pubs(), names, _RECORDS, this_year=2026)
    status = {r["name"]: r["publication_status"] for r in rows}
    assert status["Xin (Kelly) Liu"] == "forthcoming"        # labelled; no volume yet
    assert status["Kun Li"] == "forthcoming"                 # same paper, same status
    assert status["Mark Wilson"] == "published"              # labelled, but now in an issue
    assert status["Sonali Walpola"] == "forthcoming"         # labelled "in press", no DOI
    assert status["Isabel Wang"] == "published"              # a 2023 label is stale
    assert status["Scarlett Luo"] == "published"             # online-first, never labelled


def test_status_rule_leaves_non_anu_rows_untouched():
    rows = [dict(r, name="Someone Else") for r in _status_rows()]
    before = copy.deepcopy(rows)
    export._anu_publication_status(rows, [dict(p, name="Someone Else") for p in _labelled_pubs()],
                                   {"Xin (Kelly) Liu"}, _RECORDS, this_year=2026)
    assert rows == before


def test_surname_only_doi_author_list_yields_to_the_profile_list():
    row = {"name": "Susanna Ho", "n_authors": 2, "authors": "Tam; Ho",
           "_anu_profile_n_authors": 2, "_anu_profile_authors": "K Tam; Susanna Ho"}
    assert export._anu_author_fallback(row, ANU)
    assert row["authors"] == "K Tam; Susanna Ho" and row["n_authors"] == 2
    fuller = {"name": "Susanna Ho", "n_authors": 2, "authors": "Kar Yan Tam; Shuk Ying Ho",
              "_anu_profile_n_authors": 2, "_anu_profile_authors": "K Tam; Susanna Ho"}
    assert not export._anu_author_fallback(fuller, ANU)
    other = dict(row, name="Someone Else")
    before = copy.deepcopy(other)
    assert not export._anu_author_fallback(other, ANU) and other == before
