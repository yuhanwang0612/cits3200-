"""
Harvest records — CITS3200 Group 20.

The fourth entity in the Scope of Work data dictionary (3.5.4), and the one
nobody on the team had except Sean:

    university, source, last_run, latest_year

One row per source per university. It answers "how current is this data, and
where did it come from", which is FR14, and it is the only place the client's
19 August instruction can live: for citation figures the date that matters is
**the date we scraped**, not the date Scimago or anyone else published theirs.

The column names and the file name match Sean's UQ output exactly
(`harvest.csv` and `harvest.json`), so the eight universities concatenate with
no reshaping.

Not usually run directly. Each script that produces data calls `record()` when
it finishes, so `last_run` is the moment that source actually ran rather than
the moment somebody remembered to write it down:

    import harvest
    harvest.record("University of New South Wales", "UNSW staff profile",
                   latest_year=2026, output_dir="output")

Rows are keyed on (university, source) and upserted, so re-running one step
updates its own row and leaves the others alone. That matters: running
`openalex.py` again must not make the scraper look like it ran too.

`python harvest.py --show output/harvest.csv` prints what is currently recorded.
"""

import argparse
import csv
import json
import os
from datetime import datetime, timezone

COLUMNS = ["university", "source", "last_run", "latest_year"]

FILENAME = "harvest"


def now():
    """UTC, ISO 8601, matching the format already in Sean's harvest.csv."""
    return datetime.now(timezone.utc).isoformat()


def read(output_dir="."):
    """Existing rows, or [] if nothing has been recorded yet."""
    path = os.path.join(output_dir, FILENAME + ".csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{c: (row.get(c) or "") for c in COLUMNS} for row in csv.DictReader(f)]


def record(university, source, latest_year=None, output_dir=".", last_run=None):
    """Upsert one (university, source) row and rewrite both files.

    `last_run` defaults to now. It is a parameter so the tests can pin it;
    nothing in the pipeline should pass it.

    Returns the full list of rows as written.
    """
    if not university or not source:
        raise ValueError("a harvest row needs both a university and a source")

    rows = read(output_dir)
    row = {
        "university": university,
        "source": source,
        "last_run": last_run or now(),
        "latest_year": "" if latest_year in (None, "") else str(latest_year),
    }

    for i, existing in enumerate(rows):
        if (existing["university"], existing["source"]) == (university, source):
            rows[i] = row
            break
    else:
        rows.append(row)

    rows.sort(key=lambda r: (r["university"], r["source"]))
    write(rows, output_dir)
    return rows


def write(rows, output_dir="."):
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, FILENAME + ".csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Sean writes both; the JSON is what a database loader would rather read.
    json_path = os.path.join(output_dir, FILENAME + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")
    return csv_path, json_path


def latest_year_in(rows, year_column="year"):
    """Highest usable year in a list of publication dicts, or None.

    Anything that is not a plausible four-digit year is ignored rather than
    crashing the run: some UNSW entries have no year at all, and a harvest
    record is not worth failing a scrape over.
    """
    years = []
    for row in rows or []:
        value = str((row.get(year_column) or "")).strip()[:4]
        if value.isdigit() and 1800 <= int(value) <= 2200:
            years.append(int(value))
    return max(years) if years else None


def university_in(rows, column="university"):
    """The university these rows belong to, or None if that is not one answer.

    Read from the data rather than taken as a flag, so a harvest row cannot
    claim a university the file does not actually contain. If a file somehow
    holds two, None is returned and the caller skips the record instead of
    picking one at random.
    """
    names = {str(r.get(column) or "").strip() for r in rows or []}
    names.discard("")
    return names.pop() if len(names) == 1 else None


def main():
    parser = argparse.ArgumentParser(description="Inspect or write a harvest record.")
    parser.add_argument("--show", metavar="DIR", nargs="?", const=".",
                        help="print the harvest rows in this output directory")
    parser.add_argument("--university")
    parser.add_argument("--source")
    parser.add_argument("--latest-year")
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args()

    if args.show is not None:
        rows = read(args.show)
        if not rows:
            print(f"No harvest record in {args.show}.")
            return
        width = max(len(r["source"]) for r in rows)
        for row in rows:
            print(f"  {row['source']:<{width}}  {row['last_run']}  "
                  f"latest {row['latest_year'] or '?'}   {row['university']}")
        return

    if not args.university or not args.source:
        raise SystemExit("Give --show, or both --university and --source.")
    record(args.university, args.source, args.latest_year, args.output_dir)
    print(f"Recorded {args.source} for {args.university}.")


if __name__ == "__main__":
    main()
