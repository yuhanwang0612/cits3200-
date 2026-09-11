import csv
import math
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker



from models import Base, Researcher, Journal, Publication

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "site", "research.db")

# ============================================================
# CONFIG
# ============================================================

DB = f"sqlite:///{DB_PATH}"
DATA_DIR = Path("final output")


# ============================================================
# HELPERS
# ============================================================

def clean(value):
    """Turn empty/NaN CSV values into None."""
    if value is None:
        return None

    value = str(value).strip()

    if not value or value.lower() in {"nan", "none", "null"}:
        return None

    return value


def as_int(value):
    value = clean(value)

    if value is None:
        return None

    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def as_float(value):
    value = clean(value)

    if value is None:
        return None

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def first(row, *names):
    """
    Return the first non-empty value from a list of possible
    column names.

    This lets different universities use slightly different
    column names while the database remains normalized.
    """

    for name in names:
        if name in row:
            value = clean(row[name])

            if value is not None:
                return value

    return None


def read_csv(path):
    with open(
        path,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        return [
            {
                key.strip(): clean(value)
                for key, value in row.items()
            }
            for row in reader
        ]


# ============================================================
# FILE DISCOVERY
# ============================================================

def find_file(university_dir, kind):
    """
    Find the relevant CSV for a university.

    Supports names such as:
        uq_staff.csv
        uq_publications.csv
        uq_journals.csv

    and:
        unsw_staff.csv
        unsw_publications.csv
        unsw_journals.csv
    """

    candidates = list(
        university_dir.glob(f"*_{kind}.csv")
    )

    if not candidates:
        return None

    return candidates[0]


# ============================================================
# MAIN
# ============================================================

def main():

    engine = create_engine(DB)

    # Rebuild database every time load.py runs
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    # --------------------------------------------------------
    # Find universities
    # --------------------------------------------------------

    university_dirs = sorted(
        p for p in DATA_DIR.iterdir()
        if p.is_dir()
    )

    if not university_dirs:
        raise RuntimeError(
            f"No university directories found in {DATA_DIR}"
        )

    print(
        f"Found {len(university_dirs)} university directories:"
    )

    for directory in university_dirs:
        print(f"  - {directory.name}")

    # --------------------------------------------------------
    # Global maps
    # --------------------------------------------------------

    # (university, researcher identifier) -> Researcher
    researcher_map = {}

    # journal name -> Journal
    journal_map = {}

    # --------------------------------------------------------
    # PASS 1: Researchers
    # --------------------------------------------------------

    for university_dir in university_dirs:

        university_code = university_dir.name

        staff_path = find_file(
            university_dir,
            "staff"
        )

        if staff_path is None:
            print(
                f"WARNING: no staff CSV for "
                f"{university_code}"
            )
            continue

        print(
            f"\nLoading researchers from "
            f"{university_code}..."
        )

        staff_rows = read_csv(staff_path)

        for row in staff_rows:

            name = first(
                row,
                "name",
                "researcher",
                "researcher_name"
            )

            if not name:
                continue

            university = first(
                row,
                "university"
            ) or university_code

            match_university = university_code.lower()

            source_id = first(
                row,
                "source_id",
                "id",
                "researcher_id"
            )

            orcid = first(
                row,
                "orcid",
                "ORCID",
                "orcid_id"
            )

            # Avoid duplicate researchers
            key = (
                match_university,
                source_id
                or orcid
                or name.lower()
            )

            if key in researcher_map:
                continue

            if key in researcher_map:
                continue

            researcher = Researcher(
                name=name,

                job_title=first(
                    row,
                    "job_title",
                    "title",
                    "position"
                ),

                academic_level=first(
                    row,
                    "academic_level",
                    "level",
                    "level_code"
                ),

                university=university,

                field_of_research=first(
                    row,
                    "field_of_research",
                    "field",
                    "research_field"
                ),

                source_id=source_id,

                orcid=orcid,

                profile_url=first(
                    row,
                    "profile_url",
                    "profile",
                    "url"
                )
            )

            session.add(researcher)

            # Store all available identifiers for this researcher
            if source_id:
                researcher_map[
                    (match_university, source_id)
                ] = researcher

            if orcid:
                researcher_map[
                    (match_university, orcid.lower())
                ] = researcher

            researcher_map[
                (match_university, name.lower())
            ] = researcher

    session.flush()

    print(
        f"\nResearchers loaded: "
        f"{len(researcher_map)}"
    )

    # --------------------------------------------------------
    # PASS 2: Journals
    # --------------------------------------------------------

    for university_dir in university_dirs:

        university_code = university_dir.name

        journals_path = find_file(
            university_dir,
            "journals"
        )

        if journals_path is None:
            print(
                f"WARNING: no journals CSV for "
                f"{university_code}"
            )
            continue

        print(
            f"Loading journals from "
            f"{university_code}..."
        )

        journal_rows = read_csv(journals_path)

        for row in journal_rows:

            journal_name = first(
                row,
                "journal_name",
                "journal",
                "name"
            )

            if not journal_name:
                continue

            # Journals are shared across universities,
            # so don't duplicate the same journal.
            key = journal_name.lower().strip()

            if key in journal_map:
                continue

            journal = Journal(

                journal_name=journal_name,

                journal_raw=first(
                    row,
                    "journal_raw",
                    "raw_journal"
                ),

                publisher=first(
                    row,
                    "publisher"
                ),

                issn=first(
                    row,
                    "issn",
                    "ISSN"
                ),

                quality_rank=first(
                    row,
                    "quality_rank",
                    "abdc_rank",
                    "abdc"
                ),

                abdc_edition=first(
                    row,
                    "abdc_edition"
                ),

                impact_factor=as_float(
                    first(
                        row,
                        "impact_factor",
                        "jif"
                    )
                ),

                impact_factor_5yr=as_float(
                    first(
                        row,
                        "impact_factor_5yr",
                        "five_year_impact_factor"
                    )
                ),

                jcr_year=as_int(
                    first(
                        row,
                        "jcr_year"
                    )
                ),

                sjr=as_float(
                    first(
                        row,
                        "sjr"
                    )
                ),

                sjr_quartile=first(
                    row,
                    "sjr_quartile",
                    "scimago_quartile"
                ),

                h_index=as_int(
                    first(
                        row,
                        "h_index"
                    )
                ),

                cites_per_doc_2y=as_float(
                    first(
                        row,
                        "cites_per_doc_2y"
                    )
                ),

                scimago_year=first(
                    row,
                    "scimago_year"
                )
            )

            session.add(journal)

            journal_map[key] = journal

    session.flush()

    print(
        f"Journals loaded: "
        f"{len(journal_map)}"
    )

    # --------------------------------------------------------
    # PASS 3: Publications
    # --------------------------------------------------------

    publication_count = 0
    missing_researchers = 0


    total_by_university = {}
    matched_by_university = {}
    missing_by_university = {}

    for university_dir in university_dirs:

        university_code = university_dir.name

        pubs_path = find_file(
            university_dir,
            "publications"
        )

        if pubs_path is None:
            print(
                f"WARNING: no publications CSV for "
                f"{university_code}"
            )
            continue

        print(
            f"Loading publications from "
            f"{university_code}..."
        )

        pub_rows = read_csv(pubs_path)

        total_by_university[university_code] = len(pub_rows)
        matched_by_university[university_code] = 0
        missing_by_university[university_code] = 0

        for row in pub_rows:

            name = first(
                row,
                "name",
                "researcher",
                "researcher_name"
            )

            if not name:
                continue

            university = university_code

            source_id = first(
                row,
                "source_id",
                "researcher_id"
            )

            orcid = first(
                row,
                "orcid",
                "ORCID",
                "orcid_id"
            )

            # Try source_id first, then ORCID, then name.
            researcher = None

            if source_id:
                researcher = researcher_map.get(
                    (
                        university.lower(),
                        source_id
                    )
                )

            if researcher is None and orcid:
                researcher = researcher_map.get(
                    (
                        university.lower(),
                        orcid.lower()
                    )
                )

            if researcher is None:
                researcher = researcher_map.get(
                    (
                        university.lower(),
                        name.lower()
                    )
                )

            if researcher is None:
                missing_researchers += 1
                missing_by_university[university_code] += 1

                if missing_by_university[university_code] <= 5:
                    print(
                        f"  UNMATCHED [{university_code}]: "
                        f"name={name!r}, "
                        f"source_id={source_id!r}, "
                        f"orcid={orcid!r}"
                    )

                continue

            matched_by_university[university_code] += 1

            journal_name = first(
                row,
                "journal_name",
                "journal"
            )

            journal = None

            if journal_name:
                journal = journal_map.get(
                    journal_name.lower().strip()
                )

            publication = Publication(

                researcher=researcher,

                journal=journal,

                title=first(
                    row,
                    "title",
                    "publication_title"
                ) or "",

                year=as_int(
                    first(
                        row,
                        "year",
                        "publication_year"
                    )
                ),

                author_count=as_int(
                    first(
                        row,
                        "author_count",
                        "number_of_authors"
                    )
                ),

                authors=first(
                    row,
                    "authors",
                    "author"
                ),

                doi=first(
                    row,
                    "doi",
                    "DOI"
                ),

                article_url=first(
                    row,
                    "article_url"
                ),

                link=first(
                    row,
                    "link",
                    "url"
                ),

                quality_rank=first(
                    row,
                    "quality_rank",
                    "abdc_rank",
                    "abdc"
                ),

                sjr_quartile=first(
                    row,
                    "sjr_quartile",
                    "scimago_quartile"
                ),

                citation_percentile=as_float(
                    first(
                        row,
                        "citation_percentile"
                    )
                ),

                cited_by_count=as_int(
                    first(
                        row,
                        "cited_by_count",
                        "citations",
                        "citation_count"
                    )
                ),

                fwci=as_float(
                    first(
                        row,
                        "fwci",
                        "FWCI"
                    )
                ),

                oa_status=first(
                    row,
                    "oa_status",
                    "open_access_status"
                ),

                oa_url=first(
                    row,
                    "oa_url",
                    "open_access_url"
                ),

                publication_status=first(
                    row,
                    "publication_status",
                    "status"
                ),

                source=first(
                    row,
                    "source"
                )
            )

            session.add(publication)

            publication_count += 1

    session.commit()

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print("\n==============================")
    print("DATABASE LOAD COMPLETE")
    print("==============================")

    print(
        f"Researchers:  "
        f"{session.query(Researcher).count()}"
    )

    print(
        f"Journals:     "
        f"{session.query(Journal).count()}"
    )

    print(
        f"Publications: "
        f"{session.query(Publication).count()}"
    )

    print("\nPublication matching by university:")
    print("-----------------------------------")

    for uni in sorted(total_by_university):
        total = total_by_university[uni]
        matched = matched_by_university[uni]
        missing = missing_by_university[uni]

        percentage = (
            matched / total * 100
            if total > 0
            else 0
        )

        print(
            f"{uni:15} "
            f"{matched:5}/{total:<5} "
            f"matched ({percentage:5.1f}%) "
            f"| {missing} unmatched"
        )


    if missing_researchers:
        print(
            f"\nWARNING: "
            f"{missing_researchers} publications "
            f"could not be matched to a researcher."
        )

    session.close()


if __name__ == "__main__":
    main()