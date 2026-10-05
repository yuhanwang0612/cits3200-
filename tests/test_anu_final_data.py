"""Checks on the exported ANU data itself (final output/anu/), so that a
re-run that silently undoes the v27 cleaning fails here (docs/DECISIONS.md,
5 Oct 2026). The rule functions are tested offline in
tests/test_anu_final_rules.py; this file checks their result.

    python -m pytest tests/test_anu_final_data.py -q
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import export                                                 # noqa: E402

OUT = ROOT / "final output" / "anu"
PUBS_CSV = OUT / "anu_publications.csv"

pytestmark = pytest.mark.skipif(not PUBS_CSV.exists(), reason="no ANU export")


@pytest.fixture(scope="module")
def pubs():
    with PUBS_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _norm(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def test_row_count_is_in_the_expected_range(pubs):
    assert 470 <= len(pubs) <= 540, len(pubs)


def test_no_profile_copy_of_a_published_row(pubs):
    """Task B rule, without the Crossref online/print dates (rows' own years)."""
    by_name = defaultdict(list)
    for row in pubs:
        if row["doi"]:
            by_name[row["name"]].append(row)
    copies = [(row["title"], pub["doi"]) for row in pubs if not row["doi"]
              for pub in by_name[row["name"]] if export.anu_profile_copy_of(row, pub)]
    assert not copies, copies


def test_one_title_and_one_year_per_doi(pubs):
    titles, years = defaultdict(set), defaultdict(set)
    for row in pubs:
        if row["doi"]:
            titles[row["doi"].lower()].add(row["title"])
            years[row["doi"].lower()].add(row["year"])
    assert not {d: t for d, t in titles.items() if len(t) > 1}
    assert not {d: y for d, y in years.items() if len(y) > 1}


def test_dois_are_lowercase(pubs):
    assert not [row["doi"] for row in pubs if row["doi"] != row["doi"].lower()]


def test_one_row_per_researcher_and_doi(pubs):
    seen = set()
    for row in pubs:
        key = (row["name"], row["doi"])
        assert not (row["doi"] and key in seen), key
        seen.add(key)


# Rows whose author_count is known to differ from the names shown. Empty on
# 5 Oct 2026; add a row here only with a reason.
AUTHOR_COUNT_EXCEPTIONS = {}


def test_author_count_equals_names_shown(pubs):
    bad = []
    for row in pubs:
        names = [n for n in (row["authors"] or "").split("; ") if n.strip()]
        if str(len(names)) != (row["author_count"] or "0") \
                and (row["name"], row["doi"] or row["title"]) not in AUTHOR_COUNT_EXCEPTIONS:
            bad.append((row["name"], row["title"][:50], row["author_count"], len(names)))
    assert not bad, bad


def test_every_row_names_its_owner_or_has_a_doi_author_list(pubs):
    """A DOI-less profile row always lists the profile owner (task E)."""
    missing = [(r["name"], r["title"][:50]) for r in pubs
               if not r["doi"] and r["source"] == "ANU staff profile" and r["name"] not in r["authors"]]
    assert not missing, missing


@pytest.mark.parametrize("name, doi, year", [
    ("Kathy Wang", "10.1016/j.jcae.2026.100583", "2026"),     # JCAE, lost on 1 Oct, task A
    ("Tracy (Kun) Wang", "10.1086/742862", "2026"),           # J Law & Economics, task D (v26)
    ("Lily Chen", "10.1109/tkde.2022.3148980", "2022"),       # IEEE TKDE, task G
])
def test_recovered_rows_are_present(pubs, name, doi, year):
    rows = [r for r in pubs if r["name"] == name and r["doi"] == doi]
    assert len(rows) == 1 and rows[0]["year"] == year, rows


def test_kathy_wang_has_her_published_papers_and_no_profile_duplicates(pubs):
    rows = [r for r in pubs if r["name"] == "Kathy Wang"]
    dois = {r["doi"] for r in rows}
    assert {"10.1016/j.respol.2024.105127", "10.1111/acfi.12828", "10.1111/acfi.70005",
            "10.1111/jbfa.12854", "10.1177/0148558x251331253",
            "10.1080/09638180.2023.2272622"} <= dois
    assert not [r for r in rows if not r["doi"]]


@pytest.mark.parametrize("name, doi, title", [
    ("Susanna Ho", "", "the effects of web personalization on influencing users switching decisions"),
    ("Greg Shailer", "10.1108/18325911111182330", "auditing and assurance services and ethics in australia"),
    ("Susanna Ho", "10.1002/asi.20821", "human computer interaction and management information systems"),
    ("Susanna Ho", "", "the effects of location based mobile personalization on users"),   # PACIS 2010
    ("Susanna Ho", "", "ict business case approach in public sector"),                    # PACIS 2015
])
def test_excluded_items_are_absent(pubs, name, doi, title):
    assert not [r for r in pubs if r["name"] == name
                and ((doi and r["doi"] == doi) or _norm(r["title"]).startswith(title))]


def test_no_trailing_footnote_marker(pubs):
    assert not [r["title"] for r in pubs if re.search(r"[a-z]{3}\d$", r["title"])]


def _as_csv_text(value):
    """How export.write renders a JSON value in the CSV: None and NaN are
    blank (the journals JSON carries NaN for a missing sjr, as on main),
    whole floats lose their ".0"."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@pytest.mark.parametrize("table", ["publications", "staff", "journals", "harvest"])
def test_csv_and_json_agree(table):
    with (OUT / f"anu_{table}.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with (OUT / f"anu_{table}.json").open(encoding="utf-8") as f:
        data = json.load(f)
    assert len(rows) == len(data)
    for a, b in zip(rows, data):
        for key, value in a.items():
            other = _as_csv_text(b.get(key))
            if other != value:
                assert float(other) == pytest.approx(float(value)), (table, key, value, other)


# v27.1: a registered running head is never appended, and only the 1994
# Greg Shailer title (Crossref's own form) is all caps.

# Titles whose part after the colon repeats the main title's words but is
# genuinely part of the title: Crossref registers the whole string as the
# main title and has no separate subtitle, so rule A never applied to it.
GENUINE_REPEATING_SUBTITLES = {
    "10.1016/j.im.2021.103429",  # "The strategic role of CIOs in IT controls: IT control weaknesses and CIO turnover"
}


def test_no_title_ends_in_a_running_head(pubs):
    bad = []
    for row in pubs:
        main, sep, tail = row["title"].rpartition(": ")
        if sep and main and export._is_running_head(tail, main)                 and row["doi"] not in GENUINE_REPEATING_SUBTITLES:
            bad.append(row["title"])
    assert not bad, bad


def test_only_all_caps_title_is_shailer_1994(pubs):
    caps = [(r["name"], r["year"]) for r in pubs if export._single_case(r["title"])
            and r["title"].upper() == r["title"]]
    assert caps == [("Greg Shailer", "1994")], caps


@pytest.mark.parametrize("doi, title", [
    ("10.1111/j.1835-2561.2011.00143.x",
     "Integrated Reporting: An Opportunity for Australia's Not-for-Profit Sector"),
    ("10.1111/j.1467-6281.2011.00343.x",
     "Do Publicly Signalled Earnings Management Incentives Affect Analyst Forecast Accuracy?"),
    ("10.1111/j.1468-0106.2010.00523.x",
     "General equilibrium analysis of hold-up problem and non-exclusive franchise contract"),
])
def test_running_head_rows_have_their_corrected_titles(pubs, doi, title):
    rows = [r for r in pubs if r["doi"] == doi]
    assert rows and {r["title"] for r in rows} == {title}, [r["title"] for r in rows]


# v28: ABDC inception, nine reviewed DOIs, forthcoming status.

def test_no_rating_for_a_year_before_the_journals_abdc_inception(pubs):
    from enrichment.abdc import normalise_title, title_inception
    bad = []
    for r in pubs:
        if r["quality_rank"] and r["year"]:
            inception = title_inception(normalise_title(r["journal_name"]))
            if inception and int(r["year"]) < inception:
                bad.append((r["name"], r["title"][:40], r["year"], r["journal_name"], inception))
    assert not bad, bad


@pytest.mark.parametrize("name, doi", [
    ("Susanna Ho", "10.2307/25148757"),
    ("Rebecca Tan", "10.1016/s0020-7063(02)00174-7"),
    ("Alex Wang", "10.1111/acfi.13046"),
    ("Alex Wang", "10.1016/j.bar.2019.01.001"),
    ("Alex Wang", "10.1111/acfi.12435"),
    ("Alex Wang", "10.1108/aaaj-07-2021-5353"),
    ("Chao Gao", "10.1111/fima.12258"),
    ("Lin Hu", "10.1086/724326"),
    ("Nhan Le", "10.1016/j.finmar.2020.100600"),
])
def test_reviewed_dois_are_attached(pubs, name, doi):
    assert len([r for r in pubs if r["name"] == name and r["doi"] == doi]) == 1


def test_known_forthcoming_rows(pubs):
    liu = [r for r in pubs if r["name"] == "Xin (Kelly) Liu" and r["doi"] == "10.1287/mnsc.2024.08157"]
    walpola = [r for r in pubs if r["name"] == "Sonali Walpola"
               and r["title"].startswith("Justice and the Australian income tax base")]
    assert liu and walpola
    assert {r["publication_status"] for r in liu + walpola} == {"forthcoming"}


def test_status_vocabulary(pubs):
    assert {r["publication_status"] for r in pubs} <= {"published", "forthcoming"}
