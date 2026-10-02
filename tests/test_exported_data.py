"""The exported tables, checked per university - CITS3200 Group 20.

    python -m pytest tests/test_exported_data.py -q
    python -m pytest tests/test_exported_data.py -q -k unsw

`validate_data.py` does the deep pass and prints a report a person reads.
These are the same rules as pass-or-fail tests, one set per university, so a
failure names the university in the test id and nobody has to remember to run
the script. Whoever owns that university owns the failure.

Everything here reads `final output/<uni>/`, so it needs no database, no
network and no browser.
"""

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "final output"
UNIS = ["adelaide", "anu", "monash", "unimelb", "unsw", "uq", "usyd", "uwa"]

PUBLICATION_COLUMNS = [
    "name", "orcid", "source_id", "journal_name", "title", "year",
    "author_count", "authors", "doi", "article_url", "link", "quality_rank",
    "sjr_quartile", "citation_percentile", "cited_by_count", "fwci",
    "oa_status", "oa_url", "publication_status", "source",
]
STAFF_COLUMNS = [
    "name", "job_title", "admin_title", "academic_level", "university", "field_of_research",
    "source_id", "orcid", "profile_url",
]

LEVELS = {"A", "B", "C", "D", "E"}
RANKS = {"A*", "A", "B", "C"}
QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
STATUSES = {"published", "forthcoming", "working_paper"}

DOI = re.compile(r"^10\.\d{4,9}/\S+$")
ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")
ORCID = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


def read(uni, table):
    path = OUT / uni / f"{uni}_{table}.csv"
    if not path.exists():
        pytest.skip(f"{path} has not been generated")
    return pd.read_csv(path, dtype=str).fillna("")


@pytest.fixture(scope="module")
def tables():
    return {uni: {t: read(uni, t) for t in ("publications", "staff", "journals")}
            for uni in UNIS if (OUT / uni).exists()}


def data(tables, uni, table):
    if uni not in tables:
        pytest.skip(f"no output for {uni}")
    return tables[uni][table]


# --------------------------------------------------------------- structure

@pytest.mark.parametrize("uni", UNIS)
def test_publications_have_the_agreed_columns(tables, uni):
    """Eight universities are merged into one table, so a column named
    differently by one of them is a column the merge silently loses."""
    assert list(data(tables, uni, "publications").columns) == PUBLICATION_COLUMNS


@pytest.mark.parametrize("uni", UNIS)
def test_staff_have_the_agreed_columns(tables, uni):
    assert list(data(tables, uni, "staff").columns) == STAFF_COLUMNS


@pytest.mark.parametrize("uni", UNIS)
def test_one_university_per_file(tables, uni):
    assert len(set(data(tables, uni, "staff")["university"])) == 1


# --------------------------------------------------------------- values

@pytest.mark.parametrize("uni", UNIS)
def test_every_publication_has_a_title_a_year_and_a_journal(tables, uni):
    pubs = data(tables, uni, "publications")
    for column in ("title", "year", "journal_name"):
        blank = pubs[pubs[column].str.strip() == ""]
        assert blank.empty, f"{len(blank)} rows with no {column}"


@pytest.mark.parametrize("uni", UNIS)
def test_years_are_plausible(tables, uni):
    years = pd.to_numeric(data(tables, uni, "publications")["year"], errors="coerce")
    assert years.notna().all(), "a year that is not a number"
    assert years.between(1900, 2027).all(), \
        f"years outside 1900-2027: {sorted(set(years[~years.between(1900, 2027)]))[:5]}"


@pytest.mark.parametrize("uni", UNIS)
def test_dois_are_dois(tables, uni):
    dois = data(tables, uni, "publications")["doi"]
    bad = [d for d in dois if d and not DOI.match(d)]
    assert not bad, f"{len(bad)} malformed, e.g. {bad[:3]}"


@pytest.mark.parametrize("uni", UNIS)
def test_the_controlled_vocabularies_are_respected(tables, uni):
    """quality_rank, sjr_quartile and publication_status all come from a
    fixed list. Anything else means a source value reached the export
    unmapped, and the website filters on these."""
    pubs = data(tables, uni, "publications")
    for column, allowed in (("quality_rank", RANKS),
                            ("sjr_quartile", QUARTILES),
                            ("publication_status", STATUSES)):
        used = {v for v in pubs[column] if v}
        assert used <= allowed, f"{column}: {sorted(used - allowed)[:5]}"


@pytest.mark.parametrize("uni", UNIS)
def test_academic_levels_are_A_to_E(tables, uni):
    """Acceptance Test B. A blank level is a researcher the client cannot
    place, so blanks fail here as well as unknown codes."""
    staff = data(tables, uni, "staff")
    used = list(staff["academic_level"])
    blank = [n for n, l in zip(staff["name"], used) if not l]
    unknown = sorted({l for l in used if l and l not in LEVELS})
    assert not blank, f"{len(blank)} with no level, e.g. {blank[:4]}"
    assert not unknown, f"levels outside A-E: {unknown}"


@pytest.mark.parametrize("uni", UNIS)
def test_orcids_are_well_formed_and_belong_to_one_person(tables, uni):
    staff = data(tables, uni, "staff")
    bad = [o for o in staff["orcid"] if o and not ORCID.match(o)]
    assert not bad, f"malformed ORCIDs: {bad[:3]}"
    held = staff[staff["orcid"] != ""]
    shared = held[held.duplicated("orcid", keep=False)]
    assert shared.empty, \
        f"one ORCID on more than one person: {shared[['name', 'orcid']].values.tolist()}"


@pytest.mark.parametrize("uni", UNIS)
def test_issns_are_hyphenated_and_listed_once(tables, uni):
    """ABDC and Scimago join on the hyphenated form, so an unhyphenated one
    silently loses the journal's rating."""
    for cell in data(tables, uni, "journals")["issn"]:
        if not cell:
            continue
        values = [v.strip() for v in cell.split(";") if v.strip()]
        bad = [v for v in values if not ISSN.match(v)]
        assert not bad, f"not an ISSN: {bad[:3]} in {cell!r}"
        assert len(values) == len(set(values)), f"repeated ISSN in {cell!r}"


# --------------------------------------------------------------- joins

@pytest.mark.parametrize("uni", UNIS)
def test_every_publication_belongs_to_a_listed_researcher(tables, uni):
    pubs, staff = data(tables, uni, "publications"), data(tables, uni, "staff")
    orphans = sorted(set(pubs["name"]) - set(staff["name"]))
    assert not orphans, f"names in publications but not in staff: {orphans[:5]}"


@pytest.mark.parametrize("uni", UNIS)
def test_every_journal_named_on_a_publication_exists(tables, uni):
    pubs, journals = data(tables, uni, "publications"), data(tables, uni, "journals")
    missing = sorted(set(pubs["journal_name"]) - set(journals["journal_name"]) - {"unknown"})
    assert not missing, f"journals used but not listed: {missing[:5]}"


@pytest.mark.parametrize("uni", UNIS)
def test_the_two_files_agree_on_a_journals_rating(tables, uni):
    """quality_rank is carried on the publication row and on the journal row.
    The website reads one and the CSV download shows the other."""
    pubs, journals = data(tables, uni, "publications"), data(tables, uni, "journals")
    rank = dict(zip(journals["journal_name"], journals["quality_rank"]))
    clashes = [(row["journal_name"], row["quality_rank"], rank[row["journal_name"]])
               for _, row in pubs.iterrows()
               if row["journal_name"] in rank
               and row["quality_rank"] != rank[row["journal_name"]]]
    assert not clashes, f"{len(clashes)} disagreements, e.g. {clashes[:3]}"


# --------------------------------------------------------------- duplicates

@pytest.mark.parametrize("uni", UNIS)
def test_no_duplicate_publications(tables, uni):
    """Acceptance Test D, per university."""
    pubs = data(tables, uni, "publications").copy()
    pubs["key"] = (pubs["name"] + "|"
                   + pubs["title"].str.lower().str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip()
                   + "|" + pubs["year"])
    repeated = pubs[pubs.duplicated("key", keep=False)]
    assert repeated.empty, \
        f"{len(repeated)} duplicated rows, e.g. {repeated['key'].tolist()[:3]}"


@pytest.mark.parametrize("uni", UNIS)
def test_a_doi_is_one_paper(tables, uni):
    pubs = data(tables, uni, "publications")
    with_doi = pubs[pubs["doi"] != ""]
    repeated = with_doi[with_doi.duplicated(["name", "doi"], keep=False)]
    assert repeated.empty, \
        f"{len(repeated)} rows share a researcher and a DOI, e.g. " \
        f"{repeated[['name', 'doi']].values.tolist()[:3]}"


@pytest.mark.parametrize("uni", UNIS)
def test_no_duplicate_researchers(tables, uni):
    staff = data(tables, uni, "staff")
    repeated = staff[staff.duplicated("name", keep=False)]
    assert repeated.empty, f"listed twice: {sorted(set(repeated['name']))}"


# --------------------------------------------------------------- the merge

def test_every_university_uses_the_same_column_names(tables):
    shapes = {uni: list(t["publications"].columns) for uni, t in tables.items()}
    assert len({tuple(c) for c in shapes.values()}) == 1, shapes


def test_no_researcher_appears_at_two_universities(tables):
    """A name is not a safe join key across eight universities, so the
    merged table needs to know when one turns up twice."""
    seen = {}
    for uni, t in tables.items():
        for name in t["staff"]["name"]:
            seen.setdefault(name, []).append(uni)
    both = {n: u for n, u in seen.items() if len(u) > 1}
    assert not both, f"same name at more than one university: {both}"


def test_no_orcid_is_shared_across_universities(tables):
    seen = {}
    for uni, t in tables.items():
        for name, orcid in zip(t["staff"]["name"], t["staff"]["orcid"]):
            if orcid:
                seen.setdefault(orcid, []).append(f"{name} ({uni})")
    shared = {o: who for o, who in seen.items() if len(who) > 1}
    assert not shared, f"one ORCID, more than one researcher: {shared}"
