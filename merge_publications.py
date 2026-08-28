"""
merge_publications.py
Merges publication CSVs from all university scrapers into one combined CSV.

Usage:
    python3 merge_publications.py

Reads from (skips gracefully if not present):
    monash_publications.csv
    adelaide_publications.csv
    uq_publications.csv
    unimelb_publications.csv
    usyd_publications.csv
    uwa_publications.csv
    anu_publications.csv        (from anu-scraper branch — copy here first)
    unsw_publications.csv       (from zarin-branch — copy here first)

Outputs:
    combined_publications.csv
"""

import csv, os, re

# ── Column mapping ─────────────────────────────────────────────────────
# UniMelb / USyd / UWA share the same schema
_melb_style = {
    "name":          "researcher",
    "sjr_quartile":  "scimago_quartile",
    "item_type":     "publication_type",
}

SOURCES = [
    {
        "file": "monash_publications.csv",
        "university": "Monash University",
        "renames": {},
    },
    {
        "file": "adelaide_publications.csv",
        "university": "University of Adelaide",
        "renames": {},
    },
    {
        "file": "uq_publications.csv",
        "university": "University of Queensland",
        "renames": {
            "name":         "researcher",
            "sjr_quartile": "scimago_quartile",
            "link":         "profile_url",
        },
    },
    {
        "file": "unimelb_publications.csv",
        "university": "University of Melbourne",
        "renames": _melb_style,
    },
    {
        "file": "usyd_publications.csv",
        "university": "University of Sydney",
        "renames": _melb_style,
    },
    {
        "file": "uwa_publications.csv",
        "university": "University of Western Australia",
        "renames": _melb_style,
    },
    {
        "file": "anu_publications.csv",
        "university": "Australian National University",
        "renames": {
            "researcher_name":        "researcher",
            "researcher_profile_url": "profile_url",
            "coauthors":              "authors",
            "abdc_self_reported":     "quality_rank",
        },
    },
    {
        "file": "unsw_publications.csv",
        "university": "University of New South Wales",
        "renames": {
            "researcher_name":        "researcher",
            "researcher_profile_url": "profile_url",
            "coauthors":              "authors",
            "abdc_self_reported":     "quality_rank",
        },
    },
]

# ── Target schema ─────────────────────────────────────────────────────
TARGET_COLS = [
    "university", "field_of_research", "researcher", "academic_level", "level_code",
    "title", "year", "doi", "article_url", "author_count", "authors",
    "publication_type", "source", "journal_name", "issn",
    "quality_rank", "scimago_sjr", "scimago_quartile", "cites_per_doc_2y",
    "impact_factor", "citation_percentile", "cited_by_count", "fwci",
]

def normalise_doi(doi):
    """Strip https://doi.org/ prefix if present."""
    if not doi:
        return ""
    return doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()

def normalise_title(title):
    """Lowercase + strip punctuation for dedup comparison."""
    return re.sub(r"[^a-z0-9 ]", "", (title or "").lower()).strip()

# ── Read and remap each source ────────────────────────────────────────
all_rows = []
seen_researcher_dois = set()   # (researcher, doi) — same paper can appear per co-author
seen_titles = set()            # (researcher, title, year) fallback when no DOI

for src in SOURCES:
    path = src["file"]
    if not os.path.exists(path):
        print(f"⚠️  Skipping {path} — file not found")
        continue

    renames = src["renames"]
    university = src["university"]
    count = 0

    with open(path, newline="", encoding="utf-8-sig") as f:  # utf-8-sig strips BOM
        reader = csv.DictReader(f)
        for row in reader:
            # Apply column renames
            new_row = {}
            for k, v in row.items():
                target_key = renames.get(k, k)
                new_row[target_key] = v

            # Fill in university if missing
            if not new_row.get("university"):
                new_row["university"] = university

            # Normalise DOI
            new_row["doi"] = normalise_doi(new_row.get("doi", ""))

            # Deduplication — key on (researcher, doi) so the same paper
            # can appear once per co-author (Zarin's fix)
            doi = new_row["doi"]
            researcher = new_row.get("researcher", "").strip()
            researcher_doi = (researcher, doi)
            researcher_title_year = (
                researcher,
                normalise_title(new_row.get("title", "")),
                str(new_row.get("year", "")).strip(),
            )

            if doi and researcher_doi in seen_researcher_dois:
                continue  # same researcher already has this DOI
            if not doi and researcher_title_year in seen_titles:
                continue  # same researcher, same title+year

            if doi:
                seen_researcher_dois.add(researcher_doi)
            else:
                seen_titles.add(researcher_title_year)

            # Build output row with only target columns (fill blanks for missing)
            out = {col: new_row.get(col, "") for col in TARGET_COLS}
            all_rows.append(out)
            count += 1

    print(f"✅ {path}: {count} publications loaded")

# ── Write combined CSV ────────────────────────────────────────────────
OUT = "combined_publications.csv"
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=TARGET_COLS)
    w.writeheader()
    w.writerows(all_rows)

print(f"\n✅ Done! {len(all_rows)} total publications → {OUT}")
