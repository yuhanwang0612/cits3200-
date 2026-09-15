"""Offline tests for the 15 Sep ANU data-quality fixes (FIX A-D in
anu_scraper.py / base_scrapers/anu.py). No network — small synthetic HTML
fixtures copy the real page structures the fixes were written for.

    python -m pytest tests/test_anu_scraper_fixes.py -q
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anu_scraper                                             # noqa: E402
from base_scrapers import anu                                  # noqa: E402


# ===========================================================================
# FIX A — extract_publications_block only ends at a same-or-higher heading
# ===========================================================================

def _page(body_html: str) -> str:
    return f"<html><body>{body_html}</body></html>"


def test_h3_publications_stops_only_at_next_h2_or_h3():
    """Tracy (Kun) Wang / Mark Wilson shape: h3 'Publications', then h4
    'Recent Media Interview' (skip), then h4 'Refereed Journal Publications'
    (keep) — must NOT stop at the first h4 encountered."""
    html = _page("""
        <h3>Publications</h3>
        <h4>Recent Media Interview</h4>
        <p>Talked to the ABC about tax reform, The Conversation, 2023.</p>
        <h4>Refereed Journal Publications</h4>
        <p>A very real accounting paper about firms. <i>Journal of Accounting Research</i> (2021).</p>
        <h3>Teaching</h3>
        <p>Should never be reached.</p>
    """)
    blocks = anu_scraper.extract_publications_block(html)
    texts = [b["text"] for b in blocks]
    assert any("very real accounting paper" in t for t in texts)
    assert not any("Talked to the ABC" in t for t in texts)
    assert not any("Should never be reached" in t for t in texts)


def test_h4_subsections_are_kept_and_labelled():
    """h3 Publications -> h4 'Refereed journal articles' / 'Professional
    articles' / 'Books' — none of these labels are on the skip list, so all
    three sub-sections' content survives, each tagged with its own label."""
    html = _page("""
        <h3>Publications</h3>
        <h4>Refereed journal articles</h4>
        <p>A real refereed paper on audit quality. <i>Accounting Review</i> (2020).</p>
        <h4>Professional articles</h4>
        <p>A practitioner note on tax practice for accountants everywhere.</p>
        <h4>Books</h4>
        <p>A textbook on introductory accounting, 4th Edition, 2019.</p>
    """)
    blocks = anu_scraper.extract_publications_block(html)
    sections = {b["section"] for b in blocks}
    assert sections == {"Refereed journal articles", "Professional articles", "Books"}
    assert len(blocks) == 3


def test_subsection_skip_list_drops_matching_labels():
    labels = ["Media", "Newspaper interviews", "Working Papers",
              "Seminar presentations", "Grants and awards"]
    for label in labels:
        html = _page(f"""
            <h3>Publications</h3>
            <h4>{label}</h4>
            <p>Some entry that must not survive under this heading at all costs.</p>
        """)
        blocks = anu_scraper.extract_publications_block(html)
        assert blocks == [], f"expected {label!r} sub-section to be skipped"


def test_bold_label_paragraph_sets_section_and_is_not_a_publication():
    html = _page("""
        <h3>Publications</h3>
        <p><strong>Selected working papers:</strong></p>
        <p>An unpublished draft about market microstructure and pricing.</p>
        <p><b>Publication:</b></p>
        <p>A real accounting paper. <i>Journal of Accounting Research</i> (2022).</p>
    """)
    blocks = anu_scraper.extract_publications_block(html)
    # the working-paper draft is dropped (label matches the skip list);
    # the labels themselves are never returned as blocks
    texts = [b["text"] for b in blocks]
    assert not any("unpublished draft" in t for t in texts)
    assert not any(t.strip() in ("Selected working papers:", "Publication:") for t in texts)
    assert any("A real accounting paper" in t for t in texts)
    assert blocks[0]["section"] == "Publication"


# ===========================================================================
# FIX B — not-yet-published entries are excluded, forthcoming/in press kept
# ===========================================================================

def _researcher(name="Sarah Adams"):
    return anu_scraper.Researcher(
        name=name, job_title="Senior Lecturer", academic_level="C",
        field_of_research="Accounting",
        profile_url="https://rsa.anu.edu.au/people/sarah-adams",
    )


@pytest.mark.parametrize("phrase", [
    "R&R at Journal of Accounting Research",
    "revise and resubmit at Journal of Finance",
    "currently under review at Accounting Review",
    "submitted to The Accounting Review",
    "working paper, Australian National University",
])
def test_not_yet_published_markers_are_excluded_and_counted(phrase):
    anu_scraper.NOT_YET_PUBLISHED_COUNTS.clear()
    block = {"text": f"A study of audit committees and firm outcomes, {phrase}, 2023.",
             "links": [], "italics": [], "section": None}
    pub, confident = anu_scraper.parse_publication(block, _researcher())
    assert pub is None
    assert anu_scraper.NOT_YET_PUBLISHED_COUNTS["Sarah Adams"] == 1


@pytest.mark.parametrize("phrase", ["forthcoming", "in press"])
def test_forthcoming_and_in_press_are_accepted(phrase):
    anu_scraper.NOT_YET_PUBLISHED_COUNTS.clear()
    block = {
        "text": f"A study of audit committees and firm outcomes ({phrase}), "
                f"Journal of Accounting Research.",
        "links": [], "italics": ["Journal of Accounting Research"], "section": None,
    }
    pub, confident = anu_scraper.parse_publication(block, _researcher())
    assert pub is not None
    assert pub.forthcoming is True


# ===========================================================================
# FIX C — title/journal/year parsing
# ===========================================================================

def test_comma_flow_prefers_known_multi_segment_journal_title():
    """'Journal of Money, Credit & Banking' must not split into journal
    'Credit and Banking' or journal '& Banking' — the comma-flow splitter
    must recognise the whole thing as one known ABDC title."""
    known = {"journal of money credit and banking"}
    with patch.object(anu_scraper, "_abdc_titles", return_value=known):
        result = anu_scraper._split_comma_flow_title_journal(
            "A study of monetary policy and bank lending, "
            "Journal of Money, Credit & Banking"
        )
    assert result is not None
    title, journal = result
    assert journal == "Journal of Money, Credit & Banking"
    assert title == "A study of monetary policy and bank lending"


def test_conjunction_guard_rejects_and_only_journal():
    """'Privatization, Distortions, and Productivity' with no known-journal
    match must not come out with journal 'and Productivity'."""
    with patch.object(anu_scraper, "_abdc_titles", return_value=set()):
        result = anu_scraper._split_comma_flow_title_journal(
            "Privatization, Distortions, and Productivity"
        )
    assert result is None


def test_year_never_taken_from_inside_the_title():
    """Sonali Walpola's 2021 paper: the only year INSIDE the title is
    1987 (from '(1987-2016)'); the real year, 2021, sits later in the
    citation after the closing quote."""
    text = ("'After the Australia Acts: the High Court's Attitude to "
            "Changing the Common Law (1987-2016)' (2021) 21(1) Oxford "
            "University Commonwealth Law Journal 31-72.")
    block = {"text": text, "links": [], "italics": [], "section": None}
    pub, confident = anu_scraper.parse_publication(block, _researcher("Sonali Walpola"))
    assert pub is not None
    assert pub.year == 2021
    # the in-title year is real title content and stays in the title —
    # only the YEAR FIELD must not be taken from inside it
    assert "1987-2016" in (pub.title or "")


def test_year_blank_when_only_year_is_inside_the_title():
    text = "'A study of the 1987 crash and its long shadow' with D. Duffie, Journal of Finance."
    block = {"text": text, "links": [], "italics": ["Journal of Finance"], "section": None}
    pub, confident = anu_scraper.parse_publication(block, _researcher())
    assert pub is not None
    assert pub.year is None


def test_journal_tail_junk_is_stripped():
    assert anu_scraper._strip_journal_junk(
        "Accounting and Finance, forthcoming (ABDC: A)"
    ) == "Accounting and Finance"
    assert anu_scraper._strip_journal_junk(
        "Accounting and Finance 62: 2467-2496. (ABDC: A)"
    ) == "Accounting and Finance"
    assert anu_scraper._strip_journal_junk(
        "Journal of Banking and Finance (Impact Factor: 3.5)"
    ) == "Journal of Banking and Finance"


# ===========================================================================
# FIX D — page ORCIDs
# ===========================================================================

def test_extract_orcids_from_href_and_text():
    html = _page("""
        <a href="https://orcid.org/0000-0002-4737-4507">ORCID</a>
        <p>Also see https://orcid.org/0000-0002-4737-4507 and
        orcid.org/0000-0001-5678-123X for a colleague.</p>
    """)
    ids = anu_scraper.extract_orcids(html)
    assert ids == ["0000-0002-4737-4507", "0000-0001-5678-123X"]


def test_orcid_checksum_validation():
    assert anu._orcid_checksum_valid("0000-0002-4737-4507") is True
    assert anu._orcid_checksum_valid("0000-0002-4737-4508") is False


def test_orcid_record_match_accepts_bracketed_preferred_name():
    person = {"name": {"family-name": {"value": "Wang"},
                        "given-names": {"value": "Kun"}}}
    assert anu._orcid_record_matches(person, "Tracy (Kun) Wang") is True


def test_orcid_record_match_rejects_wrong_person():
    person = {"name": {"family-name": {"value": "Smith"},
                        "given-names": {"value": "Bob"}}}
    assert anu._orcid_record_matches(person, "Tracy (Kun) Wang") is False


def test_apply_page_orcid_fallback_accepts_valid_single_candidate(tmp_path):
    r = anu_scraper.Researcher(
        name="Tracy (Kun) Wang", job_title="Lecturer", academic_level="B",
        field_of_research="Finance",
        profile_url="https://rsfas.anu.edu.au/people/tracy-wang",
    )
    rec = {"name": r.name, "name_clean": r.name, "orcid": None}
    anu_scraper.PROFILE_ORCIDS.clear()
    anu_scraper.PROFILE_ORCIDS[r.name] = ["0000-0002-4737-4507"]

    with patch.object(anu, "_fetch_orcid_person", return_value={
        "name": {"family-name": {"value": "Wang"}, "given-names": {"value": "Kun"}}
    }):
        log_path = tmp_path / "orcid_decisions.csv"
        anu._apply_page_orcid_fallback([r], [rec], log_path=log_path, verbose=False)

    assert rec["orcid"] == "0000-0002-4737-4507"
    assert log_path.exists()


def test_apply_page_orcid_fallback_rejects_multiple_candidates(tmp_path):
    r = anu_scraper.Researcher(
        name="Steven Wu", job_title="Lecturer", academic_level="B",
        field_of_research="Finance",
        profile_url="https://rsfas.anu.edu.au/people/steven-wu",
    )
    rec = {"name": r.name, "name_clean": r.name, "orcid": None}
    anu_scraper.PROFILE_ORCIDS.clear()
    anu_scraper.PROFILE_ORCIDS[r.name] = ["0000-0002-4737-4507", "0000-0001-5678-123X"]

    log_path = tmp_path / "orcid_decisions.csv"
    anu._apply_page_orcid_fallback([r], [rec], log_path=log_path, verbose=False)

    assert rec["orcid"] is None


def test_apply_page_orcid_fallback_keeps_seed_and_reports_conflict(tmp_path):
    r = anu_scraper.Researcher(
        name="Sonali Walpola", job_title="Lecturer", academic_level="B",
        field_of_research="Accounting",
        profile_url="https://rsa.anu.edu.au/people/sonali-walpola",
    )
    rec = {"name": r.name, "name_clean": r.name, "orcid": "0000-0002-4737-4507"}
    anu_scraper.PROFILE_ORCIDS.clear()
    anu_scraper.PROFILE_ORCIDS[r.name] = ["0000-0001-9999-9999"]

    log_path = tmp_path / "orcid_decisions.csv"
    decisions = anu._apply_page_orcid_fallback([r], [rec], log_path=log_path, verbose=False)

    assert rec["orcid"] == "0000-0002-4737-4507"
    assert any(d["decision"] == "rejected" and "seed" in d["reason"] for d in decisions)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
