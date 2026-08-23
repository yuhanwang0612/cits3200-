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
produced both input files in output/. Writes anu_publications.csv and
anu_staff.csv to the repo root, alongside anu_journals.csv and
harvest.csv — that's the observed convention on main (uq_/unimelb_/
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

FLATTENED_COLUMNS = ["quality_rank", "sjr_quartile"]


def main() -> None:
    with JOURNALS_IN.open(encoding="utf-8") as f:
        journal_rows = list(csv.DictReader(f))
    by_name = {j["journal_name"]: j for j in journal_rows}

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
    matched = 0
    for row in pub_rows:
        journal = by_name.get(row.get("journal_name") or "")
        for col in FLATTENED_COLUMNS:
            row[col] = journal.get(col, "") if journal else ""
        if journal:
            matched += 1

    with PUBLICATIONS_OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(pub_rows)

    shutil.copyfile(OUTPUT_DIR / "anu_staff.csv", STAFF_OUT)

    rated = sum(1 for r in pub_rows if r.get("quality_rank") and r["quality_rank"] != "none")
    print(f"{PUBLICATIONS_OUT}: {len(pub_rows)} rows, {matched} joined to a journal, "
          f"{rated} carry an ABDC rating other than 'none'")
    print(f"{STAFF_OUT}: copied from {OUTPUT_DIR / 'anu_staff.csv'}")


if __name__ == "__main__":
    main()
