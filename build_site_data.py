"""Build the static website dataset from the team's real CSV exports.

The canonical source is ``final output/<uni>/``.  Adelaide, Monash and USyd
have not yet been migrated there on main, so their real legacy CSVs are used
only as fallbacks.  Run this file whenever the scraper outputs change:

    python build_site_data.py

It replaces the generated JSON under ``site/data`` and performs reconciliation
checks before reporting success.  It never calls a university website or an
external API.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FINAL_OUTPUT = ROOT / "final output"
SITE_DATA = ROOT / "site" / "data"

UNIVERSITIES = {
    "adl": {"code": "ADL", "name": "The University of Adelaide"},
    "anu": {"code": "ANU", "name": "The Australian National University"},
    "monash": {"code": "MON", "name": "Monash University"},
    "unimelb": {"code": "UOM", "name": "The University of Melbourne"},
    "unsw": {"code": "UNSW", "name": "The University of New South Wales"},
    "uq": {"code": "UQ", "name": "The University of Queensland"},
    "usyd": {"code": "USYD", "name": "The University of Sydney"},
    "uwa": {"code": "UWA", "name": "The University of Western Australia"},
}

ALIASES = {
    "adelaide": "adl",
    "university of adelaide": "adl",
    "adelaide university": "adl",
    "australian national university": "anu",
    "the australian national university": "anu",
    "monash university": "monash",
    "university of melbourne": "unimelb",
    "the university of melbourne": "unimelb",
    "unsw": "unsw",
    "unsw sydney": "unsw",
    "university of new south wales": "unsw",
    "the university of new south wales": "unsw",
    "university of queensland": "uq",
    "the university of queensland": "uq",
    "university of sydney": "usyd",
    "the university of sydney": "usyd",
    "university of western australia": "uwa",
    "the university of western australia": "uwa",
}

LEVEL_NAMES = {
    "A": "Associate Lecturer",
    "B": "Lecturer",
    "C": "Senior Lecturer",
    "D": "Associate Professor",
    "E": "Professor",
}

RANK_ORDER = {"A*": 0, "A": 1, "B": 2, "C": 3, "none": 4}


def clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return None if not value or value.casefold() in {"nan", "null"} else value


def first(row, *names):
    for name in names:
        value = clean(row.get(name))
        if value is not None:
            return value
    return None


def number(value):
    value = clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value):
    value = number(value)
    return int(value) if value is not None else None


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            {str(key).strip(): clean(value) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def slug(value):
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")


def normalise_name(value):
    return re.sub(r"[^a-z0-9]+", " ", slug(value)).strip()


def normalise_doi(value):
    value = clean(value)
    if value is None:
        return None
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    return value.strip().rstrip(".,;:").casefold() or None


def normalise_title(value):
    value = unicodedata.normalize("NFKD", clean(value) or "")
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def normalise_rank(value):
    value = clean(value)
    if value is None:
        return None
    token = value.upper().replace(" ", "")
    if token in {"A*", "A", "B", "C"}:
        return token
    if token in {"NONE", "UNRANKED", "N/A", "NA"}:
        return "none"
    return None


def better_rank(left, right):
    candidates = [rank for rank in (left, right) if rank is not None]
    return min(candidates, key=lambda rank: RANK_ORDER.get(rank, 99)) if candidates else None


def normalise_field(value, job_title=None):
    """Normalise only an explicitly supplied discipline label.

    ``job_title`` remains in the signature for backwards compatibility, but is
    deliberately ignored: a person's job title is not evidence that they
    belong to Accounting or Finance.
    """
    text = (clean(value) or "").casefold()
    if "accounting" in text and "finance" in text:
        return "Accounting & Finance"
    if "account" in text:
        return "Accounting"
    if "finance" in text or "banking" in text:
        return "Finance"
    return clean(value)


def level_values(row):
    level = first(row, "level_code")
    raw = first(row, "academic_level", "level")
    title = first(row, "job_title", "position")
    if not level and raw and raw.upper() in LEVEL_NAMES:
        level = raw.upper()
    if not level:
        source = (raw or title or "").casefold()
        if "associate professor" in source:
            level = "D"
        elif "professor" in source:
            level = "E"
        elif "senior lecturer" in source:
            level = "C"
        elif "lecturer" in source:
            level = "B"
    academic_level = raw if raw and raw.upper() not in LEVEL_NAMES else LEVEL_NAMES.get(level)
    return academic_level, level


def discover_sources():
    legacy = {
        "adl": (ROOT / "adelaide_staff.csv", ROOT / "adelaide_publications.csv"),
        "monash": (ROOT / "monash_staff.csv", ROOT / "monash_publications.csv"),
        "usyd": (None, ROOT / "usyd_publications.csv"),
    }
    sources = []
    for key in UNIVERSITIES:
        directory = FINAL_OUTPUT / key
        staff = directory / f"{key}_staff.csv"
        publications = directory / f"{key}_publications.csv"
        journals = directory / f"{key}_journals.csv"
        source_kind = "canonical"
        if not staff.exists() or not publications.exists():
            fallback = legacy.get(key)
            if not fallback or not fallback[1].exists():
                continue
            staff, publications = fallback
            journals = None
            source_kind = "legacy_fallback"
        sources.append({
            "key": key,
            "staff": staff if staff and staff.exists() else None,
            "publications": publications,
            "journals": journals if journals and journals.exists() else None,
            "source_kind": source_kind,
        })
    return sources


def journal_lookup(source):
    lookup = {}
    path = source["journals"]
    if not path:
        return lookup
    for row in read_csv(path):
        name = first(row, "journal_name", "journal", "name")
        if not name:
            continue
        lookup[normalise_title(name)] = {
            "journal_name": name,
            "issn": first(row, "issn", "ISSN"),
            "quality_rank": normalise_rank(first(row, "quality_rank", "abdc_rank", "abdc")),
            "scimago_quartile": first(row, "sjr_quartile", "scimago_quartile"),
            "impact_factor": number(first(row, "impact_factor", "jif")),
            "impact_factor_5yr": number(first(row, "impact_factor_5yr", "five_year_impact_factor")),
        }
    return lookup


def publication_key(publication):
    if publication["doi"]:
        return ("doi", publication["doi"])
    return (
        "metadata",
        normalise_title(publication["title"]),
        publication["year"],
        normalise_title(publication["journal_name"]),
    )


def merge_publication(existing, incoming):
    for field, value in incoming.items():
        if existing.get(field) in (None, "") and value not in (None, ""):
            existing[field] = value
    existing["quality_rank"] = better_rank(
        existing.get("quality_rank"), incoming.get("quality_rank")
    )


def publication_from_row(row, journals):
    journal_name = first(row, "journal_name", "journal")
    journal = journals.get(normalise_title(journal_name)) or {}
    doi = normalise_doi(first(row, "doi", "DOI"))
    article_url = first(row, "article_url", "url")
    if not article_url and doi:
        article_url = f"https://doi.org/{doi}"
    return {
        "title": first(row, "title", "publication_title") or "",
        "journal_name": journal_name or journal.get("journal_name"),
        "issn": first(row, "issn", "ISSN") or journal.get("issn"),
        "year": integer(first(row, "year", "publication_year")),
        "quality_rank": normalise_rank(first(row, "quality_rank", "abdc_rank", "abdc"))
        or journal.get("quality_rank"),
        "scimago_quartile": first(row, "sjr_quartile", "scimago_quartile")
        or journal.get("scimago_quartile"),
        "impact_factor": number(first(row, "impact_factor", "jif"))
        if first(row, "impact_factor", "jif") is not None
        else journal.get("impact_factor"),
        "impact_factor_5yr": number(first(row, "impact_factor_5yr", "five_year_impact_factor"))
        if first(row, "impact_factor_5yr", "five_year_impact_factor") is not None
        else journal.get("impact_factor_5yr"),
        "cited_by_count": integer(first(row, "cited_by_count", "citations", "citation_count")),
        "citation_percentile": number(first(row, "citation_percentile")),
        "fwci": number(first(row, "fwci", "FWCI")),
        "doi": doi,
        "article_url": article_url,
        "publication_type": first(row, "publication_type", "item_type", "type") or "journal_article",
        "publication_status": first(row, "publication_status", "status"),
        "source": first(row, "source"),
    }


def metric_summary(publications):
    ranks = [p.get("quality_rank") for p in publications]
    checked_ranks = [rank for rank in ranks if rank is not None]
    jifs = [p["impact_factor"] for p in publications if p.get("impact_factor") is not None]
    jif5s = [p["impact_factor_5yr"] for p in publications if p.get("impact_factor_5yr") is not None]
    return {
        "publication_count": len(publications),
        "abdc_ranked_count": (
            sum(rank in {"A*", "A", "B", "C"} for rank in checked_ranks)
            if checked_ranks else None
        ),
        "top_tier_count": (
            sum(rank in {"A*", "A"} for rank in checked_ranks)
            if checked_ranks else None
        ),
        "avg_jif": round(sum(jifs) / len(jifs), 4) if jifs else None,
        "avg_jif_5": round(sum(jif5s) / len(jif5s), 4) if jif5s else None,
    }


def missing_metrics():
    """Return unknown metrics without converting missing data to zero."""
    return {
        "publication_count": None,
        "abdc_ranked_count": None,
        "top_tier_count": None,
        "avg_jif": None,
        "avg_jif_5": None,
    }


def build_dataset(sources):
    researchers = []
    researcher_index = {}
    warnings = []
    source_meta = []
    university_publications = defaultdict(dict)
    discipline_publications = defaultdict(lambda: defaultdict(dict))
    source_by_key = {source["key"]: source for source in sources}

    for source in sources:
        key = source["key"]
        info = UNIVERSITIES[key]
        pub_rows = read_csv(source["publications"])
        if source["staff"]:
            staff_rows = read_csv(source["staff"])
            staff_basis = "official staff export"
        else:
            staff_rows = []
            staff_basis = "unavailable"
            warnings.append(
                f"{info['code']}: no staff CSV is available; researcher totals and researcher pages remain unknown."
            )

        for row in staff_rows:
            name = first(row, "name", "researcher", "researcher_name")
            if not name:
                continue
            index_key = (key, normalise_name(name))
            if index_key in researcher_index:
                continue
            academic_level, level_code = level_values(row)
            identifier = f"{info['code'].casefold()}-{slug(name)}"
            if any(r["id"] == identifier for r in researchers):
                identifier += "-" + slug(first(row, "field_of_research") or "researcher")
            researcher = {
                "id": identifier,
                "name": name,
                "job_title": first(row, "job_title", "title", "position"),
                "academic_level": academic_level,
                "level_code": level_code,
                "university": info["name"],
                "university_code": info["code"],
                "field_of_research": normalise_field(
                    first(row, "field_of_research", "field", "research_field"),
                    first(row, "job_title", "title", "position"),
                ),
                "orcid": first(row, "orcid", "ORCID", "orcid_id"),
                "profile_url": first(row, "profile_url", "researcher_profile_url", "profile"),
                "_publications": {},
            }
            researchers.append(researcher)
            researcher_index[index_key] = researcher

        journals = journal_lookup(source)
        unmatched = 0
        for row in pub_rows:
            publication = publication_from_row(row, journals)
            if not publication["title"]:
                continue
            pkey = publication_key(publication)
            if pkey in university_publications[key]:
                merge_publication(university_publications[key][pkey], publication)
            else:
                university_publications[key][pkey] = dict(publication)

            publication_field = normalise_field(
                first(row, "field_of_research", "field", "research_field")
            )
            if publication_field in {"Accounting", "Finance"}:
                field_records = discipline_publications[key][publication_field]
                if pkey in field_records:
                    merge_publication(field_records[pkey], publication)
                else:
                    field_records[pkey] = dict(publication)

            name = first(row, "name", "researcher", "researcher_name")
            researcher = researcher_index.get((key, normalise_name(name or "")))
            if not researcher:
                unmatched += 1
                continue
            researcher_field = researcher["field_of_research"]
            if (
                publication_field not in {"Accounting", "Finance"}
                and researcher_field in {"Accounting", "Finance"}
            ):
                field_records = discipline_publications[key][researcher_field]
                if pkey in field_records:
                    merge_publication(field_records[pkey], publication)
                else:
                    field_records[pkey] = dict(publication)
            if pkey in researcher["_publications"]:
                merge_publication(researcher["_publications"][pkey], publication)
            else:
                researcher["_publications"][pkey] = publication

        if unmatched and source["staff"]:
            warnings.append(f"{info['code']}: {unmatched} publication rows did not match the staff list; they remain in university totals but not researcher pages.")
        source_meta.append({
            "university": info["code"],
            "source_kind": source["source_kind"],
            "staff_basis": staff_basis,
            "staff_file": str(source["staff"].relative_to(ROOT)) if source["staff"] else None,
            "publication_file": str(source["publications"].relative_to(ROOT)),
            "input_publication_rows": len(pub_rows),
            "unmatched_publication_rows": unmatched,
        })

    public_researchers = []
    publications_by_researcher = {}
    for researcher in researchers:
        publications = list(researcher.pop("_publications").values())
        publications.sort(key=lambda p: (-(p.get("year") or 0), p.get("title") or ""))
        ranks = [p.get("quality_rank") for p in publications]
        researcher.update({
            "publication_count": len(publications),
            "abdc_ranked_count": sum(rank in {"A*", "A", "B", "C"} for rank in ranks),
            "count_a_star": ranks.count("A*"),
            "count_a": ranks.count("A"),
            "count_b": ranks.count("B"),
            "count_c": ranks.count("C"),
            "count_unranked": ranks.count("none"),
            "count_rank_unknown": ranks.count(None),
        })
        public_researchers.append(researcher)
        publications_by_researcher[researcher["id"]] = publications

    university_rows = []
    for key, info in UNIVERSITIES.items():
        source = source_by_key.get(key)
        if not source:
            continue
        staff = [r for r in public_researchers if r["university_code"] == info["code"]]
        staff_available = source["staff"] is not None
        unclassified = [
            researcher for researcher in staff
            if researcher["field_of_research"] not in {"Accounting", "Finance"}
        ]
        discipline_split_available = staff_available and not unclassified

        row = {
            "code": info["code"],
            "name": info["name"],
            "researcher_count": len(staff) if staff_available else None,
        }
        row.update(metric_summary(list(university_publications[key].values())))
        for field, json_key in (("Accounting", "accounting"), ("Finance", "finance")):
            if discipline_split_available:
                metrics = metric_summary(
                    list(discipline_publications[key][field].values())
                )
                metrics["researcher_count"] = sum(
                    researcher["field_of_research"] == field for researcher in staff
                )
            else:
                metrics = missing_metrics()
                metrics["researcher_count"] = None
            row[json_key] = metrics

        row["unclassified_researcher_count"] = (
            len(unclassified) if staff_available else None
        )
        row["coverage"] = {
            "staff": "complete" if staff_available else "missing",
            "publications": "available",
            "discipline_split": "complete" if discipline_split_available else "missing",
            "jif": "available" if row["avg_jif"] is not None else "missing",
        }
        if unclassified:
            warnings.append(
                f"{info['code']}: {len(unclassified)} staff have combined or unclassified disciplines; Accounting/Finance split metrics remain unknown."
            )
        university_rows.append(row)

    university_rows.sort(key=lambda row: row["code"])
    public_researchers.sort(key=lambda row: (row["university_code"], row["name"].casefold()))
    return university_rows, public_researchers, publications_by_researcher, source_meta, warnings


def validate_dataset(universities, researchers, publications_by_researcher):
    problems = []
    ids = [researcher["id"] for researcher in researchers]
    if len(ids) != len(set(ids)):
        problems.append("researcher IDs are not unique")
    if set(ids) != set(publications_by_researcher):
        problems.append("researcher summary and publication file IDs differ")
    known_total = sum(
        row["researcher_count"] for row in universities
        if row["researcher_count"] is not None
    )
    if known_total != len(researchers):
        problems.append("university researcher totals do not reconcile")
    for researcher in researchers:
        if researcher["publication_count"] != len(publications_by_researcher[researcher["id"]]):
            problems.append(f"publication count mismatch for {researcher['id']}")
    if problems:
        raise RuntimeError("Website dataset validation failed: " + "; ".join(problems[:10]))


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def write_dataset(universities, researchers, publications_by_researcher, source_meta, warnings):
    meta = {
        "is_sample_data": False,
        "note": "Generated from the team's real scraper CSV exports.",
        "sources": source_meta,
        "warnings": warnings,
    }
    staging = SITE_DATA.parent / ".site-data-staging"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "publications").mkdir(parents=True)
    write_json(staging / "universities.json", {"meta": meta, "universities": universities})
    write_json(staging / "researchers.json", {"meta": meta, "researchers": researchers})
    researcher_by_id = {researcher["id"]: researcher for researcher in researchers}
    for researcher_id, publications in publications_by_researcher.items():
        write_json(staging / "publications" / f"{researcher_id}.json", {
            "meta": meta,
            "researcher": researcher_by_id[researcher_id],
            "publications": publications,
        })
    if SITE_DATA.exists():
        shutil.rmtree(SITE_DATA)
    staging.replace(SITE_DATA)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate inputs without replacing site/data")
    args = parser.parse_args()
    sources = discover_sources()
    missing = sorted(set(UNIVERSITIES) - {source["key"] for source in sources})
    if missing:
        raise RuntimeError("No real CSV source for: " + ", ".join(missing))
    result = build_dataset(sources)
    universities, researchers, publications_by_researcher, source_meta, warnings = result
    validate_dataset(universities, researchers, publications_by_researcher)
    if not args.check:
        write_dataset(*result)
    print(f"Universities: {len(universities)}")
    print(f"Researchers: {len(researchers)}")
    print(f"Researcher-publication links: {sum(len(p) for p in publications_by_researcher.values())}")
    print(f"Warnings: {len(warnings)}")
    for warning in warnings:
        print(f"  - {warning}")
    print("Validation: passed")
    if args.check:
        print("No files written (--check).")
    else:
        print(f"Written to: {SITE_DATA}")


if __name__ == "__main__":
    main()
