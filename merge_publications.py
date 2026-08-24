"""
merge_publications.py
Merges publication CSVs from all university scrapers into one combined CSV.

Usage:
    python3 merge_publications.py

Reads from:
    monash_publications.csv
    adelaide_publications.csv
    output/anu_publications.csv          (from anu-scraper branch — copy here first)
    sean_publications.csv                (from Sean-Branch — copy here first)
    unsw/output/unsw_publications_with_openalex.csv  (from zarin-branch — copy here first)

Outputs:
    combined_publications.csv
"""

import csv, os, re

# ── Column mapping: (source_file, university_fallback, column_renames) ──
# Each entry: (file_path, university_name, {source_col: target_col, ...})
# Columns not listed in renames are kept as-is if they match target schema,
# or dropped if they don't.

SOURCES = [
    {
        "file": "monash_publications.csv",
        "university": "Monash University",
        "renames": {},  # already in target format
    },
    {
        "file": "adelaide_publications.csv",
        "university": "Adelaide University",
        "renames": {},  # already in target format
    },
    {
        "file": "anu_publications.csv",
        "university": "Australian National University",
        "renames": {
            "researcher_name":    "researcher",
            "researcher_profile_url": "profile_url",   # store separately, not article_url
            "coauthors":          "authors",
            "abdc_self_reported": "quality_rank",
        },
    },
    {
        "file": "sean_publications.csv",
        "university": "University of Queensland",
        "renames": {
            "name":          "researcher",
            "sjr_quartile":  "scimago_quartile",
            "link":          "profile_url",
        },
    },
    {
        "file": "unsw_publications.csv",
        "university": "University of New South Wales",
        "renames": {
            "researcher_name":    "researcher",
            "researcher_profile_url": "profile_url",
            "coauthors":          "authors",
            "abdc_self_reported": "quality_rank",
        },
    },
]

# ── Target schema ─────────────────────────────────────────────────────
TARGET_COLS = [
    "university", "field_of_research", "researcher", "academic_level", "level_code",
    "title", "year", "doi", "article_url", "author_count", "authors",
    "publication_type", "source", "journal_name", "issn",
    "quality_rank", "scimago_sjr", "scimago_quartile", "cites_per_doc_2y",
    "impact_factor",
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
seen_dois = set()       # for DOI-based dedup
seen_titles = set()     # for title+year dedup when no DOI

for src in SOURCES:
    path = src["file"]
    if not os.path.exists(path):
        print(f"⚠️  Skipping {path} — file not found")
        continue

    renames = src["renames"]
    university = src["university"]
    count = 0

    with open(path, newline="", encoding="utf-8") as f:
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

            # Deduplication
            doi = new_row["doi"]
            title_year = (
                normalise_title(new_row.get("title", "")),
                str(new_row.get("year", "")).strip(),
            )

            if doi and doi in seen_dois:
                continue  # duplicate by DOI
            if not doi and title_year in seen_titles:
                continue  # duplicate by title+year

            if doi:
                seen_dois.add(doi)
            else:
                seen_titles.add(title_year)

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
