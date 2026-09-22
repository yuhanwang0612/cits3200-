"""Drop staff with no publications from existing final exports.

The pipeline now does this by default (see run.py --keep-empty-staff). This
script applies the same rule to outputs that were generated before that change,
so they can be brought into line without re-scraping every university.

Only <uni>_staff.csv and <uni>_staff.json are rewritten. Publications, journals
and harvest tables are untouched: dropping a researcher with no publications
cannot change any of them.

    python drop_empty_staff.py              # every university in final output/
    python drop_empty_staff.py --uni monash
    python drop_empty_staff.py --dry-run    # list who would be dropped
"""

import argparse
import csv
import json
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "final output"


def publication_names(uni_dir: Path):
    path = uni_dir / f"{uni_dir.name}_publications.json"
    with path.open(encoding="utf-8") as handle:
        return {row["name"] for row in json.load(handle)}


def filter_staff_csv(path: Path, keep, dry_run: bool):
    """Remove rows whose name is not in `keep`, leaving every other byte as written.

    Rows are filtered as text rather than re-serialised through pandas, so
    number formatting, quoting and line endings stay exactly as export.py
    produced them.
    """
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8-sig")
    bom = raw.startswith(b"\xef\xbb\xbf")

    rows = list(csv.reader(text.splitlines()))
    header, body = rows[0], rows[1:]
    name_col = header.index("name")
    kept = [row for row in body if row[name_col] in keep]

    if not dry_run:
        with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator=newline)
            writer.writerow(header)
            writer.writerows(kept)
    return len(body), len(kept)


def process(uni_dir: Path, dry_run: bool):
    uni = uni_dir.name
    staff_json = uni_dir / f"{uni}_staff.json"
    staff_csv = uni_dir / f"{uni}_staff.csv"
    if not staff_json.exists() or not staff_csv.exists():
        print(f"  {uni:<10} skipped: no staff table")
        return 0

    have = publication_names(uni_dir)
    with staff_json.open(encoding="utf-8") as handle:
        staff = json.load(handle)

    dropped = [s for s in staff if s["name"] not in have]
    kept = [s for s in staff if s["name"] in have]
    keep_names = {s["name"] for s in kept}

    csv_before, csv_after = filter_staff_csv(staff_csv, keep_names, dry_run)
    if (csv_before, csv_after) != (len(staff), len(kept)):
        raise SystemExit(
            f"{uni}: staff CSV ({csv_before} rows) and JSON ({len(staff)} rows) "
            "disagree; nothing written for this university"
        )

    if not dry_run:
        with staff_json.open("w", encoding="utf-8") as handle:
            json.dump(kept, handle, indent=2, ensure_ascii=False)

    verb = "would drop" if dry_run else "dropped"
    print(f"  {uni:<10} {len(staff):>4} -> {len(kept):<4} ({verb} {len(dropped)})")
    if dry_run:
        for person in dropped:
            print(f"      - {person['name']}  [{person.get('job_title') or 'no title'}]")
    return len(dropped)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--uni", help="one folder in final output/ (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list who would be dropped without changing any file")
    args = ap.parse_args()

    if args.uni:
        dirs = [OUTPUT_DIR / args.uni]
        if not dirs[0].is_dir():
            ap.error(f"no such folder: {dirs[0]}")
    else:
        dirs = sorted(p for p in OUTPUT_DIR.iterdir() if p.is_dir())

    print("dry run: no files will be changed\n" if args.dry_run else "", end="")
    total = sum(process(d, args.dry_run) for d in dirs)
    print(f"\n{'would drop' if args.dry_run else 'dropped'} {total} staff in total")
    if not args.dry_run and total:
        print("run `python load.py` to rebuild the site database from these files")


if __name__ == "__main__":
    main()
