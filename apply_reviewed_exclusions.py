"""Apply evidence-backed publication exclusions to existing final exports.

This is an offline companion to ``core.clean``.  It updates an already
enriched delivery without re-querying OpenAlex, Clarivate, or any university
website, then keeps the CSV/JSON publication and journal tables in sync.
"""

import argparse
import csv
import json
from pathlib import Path

from core.clean import is_excluded


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def json_rows(rows, integer_fields=(), float_fields=()):
    """Restore the scalar types produced by export.py after a CSV round-trip."""
    converted = []
    for row in rows:
        item = {}
        for field, value in row.items():
            if value in (None, ""):
                item[field] = None
            elif field in integer_fields:
                item[field] = int(float(value))
            elif field in float_fields:
                item[field] = float(value)
            else:
                item[field] = value
        converted.append(item)
    return converted


def apply(out_dir: Path):
    prefix = out_dir.name
    publications_csv = out_dir / f"{prefix}_publications.csv"
    publications_json = out_dir / f"{prefix}_publications.json"
    journals_csv = out_dir / f"{prefix}_journals.csv"
    journals_json = out_dir / f"{prefix}_journals.json"

    publications, publication_fields = read_csv(publications_csv)
    kept = [row for row in publications if not is_excluded(row)]
    removed = [row for row in publications if is_excluded(row)]
    write_csv(publications_csv, kept, publication_fields)
    publications_json.write_text(
        json.dumps(
            json_rows(
                kept,
                integer_fields={"author_count", "cited_by_count"},
                float_fields={"citation_percentile", "fwci"},
            ),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    used_journals = {row["journal_name"] for row in kept if row.get("journal_name")}
    journals, journal_fields = read_csv(journals_csv)
    kept_journals = [row for row in journals if row.get("journal_name") in used_journals]
    write_csv(journals_csv, kept_journals, journal_fields)
    journals_json.write_text(
        json.dumps(
            json_rows(
                kept_journals,
                integer_fields={"h_index", "jcr_year"},
                float_fields={
                    "impact_factor", "impact_factor_5yr", "sjr", "cites_per_doc_2y"
                },
            ),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"publications: {len(publications)} -> {len(kept)} ({len(removed)} removed)")
    print(f"journals:     {len(journals)} -> {len(kept_journals)}")
    for row in removed:
        print(f"  {row.get('name')}: {row.get('title')} [{row.get('doi')}]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    apply(args.out_dir)


if __name__ == "__main__":
    main()
