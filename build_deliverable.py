"""
Builds the final anu_publications.csv deliverable.

Flattens quality_rank and sjr_quartile from output/anu_journals.csv onto
each row of output/anu_publications_with_openalex.csv, keyed on
journal_name, so this university's file carries the same rating columns
per row as uq_/unimelb_/uwa_publications.csv already on main. Derived
from the journal table at write time, not computed a second time
separately, so the flattened view and the journal table can't drift
apart from each other — see docs/DECISIONS.md.

Run this after rankings/pipeline.py (Zarin's package, zarin-branch) has
produced both input files in output/. Writes all five root deliverables —
anu_publications.csv, anu_staff.csv, anu_journals.csv, harvest.csv and
harvest.json — that's the observed convention on main (uq_/unimelb_/
uwa_publications.csv, staff.csv, journals.csv, harvest.csv all sit at
the repo root), so these are what a PR would actually add.
"""
import csv
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"

PUBLICATIONS_IN = OUTPUT_DIR / "anu_publications_with_openalex.csv"
JOURNALS_IN = OUTPUT_DIR / "anu_journals.csv"
PUBLICATIONS_OUT = ROOT / "anu_publications.csv"
STAFF_OUT = ROOT / "anu_staff.csv"
JOURNALS_OUT = ROOT / "anu_journals.csv"
HARVEST_CSV_IN = OUTPUT_DIR / "harvest.csv"
HARVEST_CSV_OUT = ROOT / "harvest.csv"
HARVEST_JSON_IN = OUTPUT_DIR / "harvest.json"
HARVEST_JSON_OUT = ROOT / "harvest.json"

FLATTENED_COLUMNS = ["quality_rank", "sjr_quartile"]


def main() -> None:
    with JOURNALS_IN.open(encoding="utf-8") as f:
        journal_rows = list(csv.DictReader(f))
    # Primary key: ISSN; fallback: journal_name (ISSN is more reliable
    # across sources — a journal may be indexed under slightly different
    # name spellings but its ISSN is stable).
    by_issn = {j["issn"]: j for j in journal_rows if j.get("issn")}
    by_name = {j["journal_name"]: j for j in journal_rows if j.get("journal_name")}

    with PUBLICATIONS_IN.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        pub_fieldnames = list(reader.fieldnames)
        pub_rows = list(reader)

    # Insert the flattened columns right after journal_name, matching
    # where they sit in uq_/unimelb_/uwa_publications.csv.
    insert_at = pub_fieldnames.index("journal_name") + 1
    out_fieldnames = (
        pub_fieldnames[:insert_at] + FLATTENED_COLUMNS + pub_fieldnames[insert_at:]
    )

    # anu_journals.csv already carries the right blank-vs-"none" convention
    # per column (quality_rank uses the literal string "none" for a real,
    # checked-but-unrated journal per the client's 12 Aug instruction;
    # sjr_quartile is genuinely blank when Scimago has no quartile for it)
    # — copied straight through, nothing re-derived here.
    matched = matched_issn = matched_name = 0
    for row in pub_rows:
        issn = (row.get("issn") or "").strip()
        journal = by_issn.get(issn) if issn else None
        if journal:
            matched_issn += 1
        else:
            journal = by_name.get(row.get("journal_name") or "")
            if journal:
                matched_name += 1
        for col in FLATTENED_COLUMNS:
            row[col] = journal.get(col, "") if journal else ""
        if journal:
            matched += 1

    with PUBLICATIONS_OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(pub_rows)

    shutil.copyfile(OUTPUT_DIR / "anu_staff.csv", STAFF_OUT)
    shutil.copyfile(JOURNALS_IN, JOURNALS_OUT)
    shutil.copyfile(HARVEST_CSV_IN, HARVEST_CSV_OUT)
    shutil.copyfile(HARVEST_JSON_IN, HARVEST_JSON_OUT)

    rated = sum(1 for r in pub_rows if r.get("quality_rank") and r["quality_rank"] != "none")
    print(f"{PUBLICATIONS_OUT}: {len(pub_rows)} rows, {matched} joined to a journal "
          f"({matched_issn} by ISSN, {matched_name} by name), "
          f"{rated} carry an ABDC rating other than 'none'")
    print(f"{STAFF_OUT}: copied from {OUTPUT_DIR / 'anu_staff.csv'}")
    print(f"{JOURNALS_OUT}: copied from {JOURNALS_IN}")
    print(f"{HARVEST_CSV_OUT}: copied from {HARVEST_CSV_IN}")
    print(f"{HARVEST_JSON_OUT}: copied from {HARVEST_JSON_IN}")


if __name__ == "__main__":
    main()
