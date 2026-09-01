"""Screen OpenAlex-discovered publications before they reach the client.

`authors.py` finds publications a university website never listed. Some of
them are real. Some belong to a different person with the same name, because
OpenAlex merges distinct researchers into one author record and an
institution filter does not always separate them.

The tell is discipline. Every researcher here is in Accounting or Finance, so
their output should appear in journals a business school publishes in. When a
"Suk Lee" at UNSW Business School turns up 186 papers in the Journal of the
Korean Physical Society and radiation oncology, that is not a productive
academic, it is two people sharing a name.

ABDC's list is used as the discipline test because that is exactly what it is:
a list of business, economics and law journals. A physics journal is not on it.

    python screen_discovered.py output/discovered_publications.csv ABDC-JQL-2025.xlsx

Nothing is deleted. Rows split into two files, and the reason is recorded on
every excluded row, so a human can overrule any of it.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import abdc
import journal_match as jm

# A researcher with at least this many discovered publications, of which fewer
# than this fraction are in a business journal, is treated as a name collision
# and none of their rows are kept. The threshold is deliberately generous: a
# genuine researcher publishing outside ABDC's coverage still clears it, and
# the excluded rows are written out rather than dropped.
MIN_ROWS_TO_JUDGE = 5
COLLISION_RATIO = 0.20

# A publication dated before this is not the work of a currently employed
# academic. It is the clearest single sign that the author record has been
# merged with someone else's.
IMPLAUSIBLE_BEFORE = 1970

KEPT = "discovered_publications.csv"
REVIEW = "discovered_publications_review.csv"
AUTHORS = "discovered_authors_review.csv"


def in_abdc(row, index, aliases):
    record, _ = jm.match_journal(row.get("journal_name") or "",
                                 row.get("issn") or "", index, aliases)
    return record is not None


def year_of(row):
    text = (row.get("year") or "").strip()
    return int(text) if text.isdigit() else None


def screen(discovered_path, abdc_path, out_dir=None):
    out_dir = out_dir or os.path.dirname(os.path.abspath(discovered_path))
    index, sheet, _ = abdc.load_abdc(abdc_path)
    aliases = jm.build_aliases(index)

    with open(discovered_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    print(f"\n  {len(rows)} discovered publications, screened against {sheet}\n")

    # Pass one: per researcher, how much of their output is in the discipline.
    tally = defaultdict(lambda: {"rows": 0, "abdc": 0, "oldest": None})
    for row in rows:
        who = row.get("researcher_name") or ""
        entry = tally[who]
        entry["rows"] += 1
        row["_in_abdc"] = in_abdc(row, index, aliases)
        if row["_in_abdc"]:
            entry["abdc"] += 1
        year = year_of(row)
        if year and (entry["oldest"] is None or year < entry["oldest"]):
            entry["oldest"] = year

    collisions = set()
    for who, entry in tally.items():
        ratio = entry["abdc"] / entry["rows"] if entry["rows"] else 0
        if entry["rows"] >= MIN_ROWS_TO_JUDGE and ratio < COLLISION_RATIO:
            collisions.add(who)
        # An implausibly old paper is deliberately NOT grounds for discarding a
        # researcher. Gordon Phillips has 76% of his discovered work in business
        # journals and one paper from 1957: his OpenAlex record has absorbed an
        # older namesake, so the right answer is to drop that row, not the 78
        # good ones. The year test below runs per row for exactly this reason.

    # Pass two: sort the rows.
    kept, review = [], []
    for row in rows:
        who = row.get("researcher_name") or ""
        year = year_of(row)
        if who in collisions:
            reason = (f"probable name collision: only {tally[who]['abdc']} of "
                      f"{tally[who]['rows']} in a business journal")
        elif year and year < IMPLAUSIBLE_BEFORE:
            reason = f"dated {year}, too early for a current academic"
        elif not row["_in_abdc"]:
            reason = "journal is not on the ABDC list"
        else:
            reason = None

        row.pop("_in_abdc", None)
        if reason:
            row["review_reason"] = reason
            review.append(row)
        else:
            kept.append(row)

    write(os.path.join(out_dir, KEPT), fieldnames, kept)
    write(os.path.join(out_dir, REVIEW), fieldnames + ["review_reason"], review)

    author_rows = []
    for who, entry in sorted(tally.items(), key=lambda x: -x[1]["rows"]):
        ratio = entry["abdc"] / entry["rows"] if entry["rows"] else 0
        author_rows.append({
            "researcher_name": who,
            "discovered": entry["rows"],
            "in_business_journal": entry["abdc"],
            "share": f"{ratio:.0%}",
            "earliest_year": entry["oldest"] or "",
            "verdict": "probable name collision" if who in collisions else "accepted",
        })
    write(os.path.join(out_dir, AUTHORS),
          ["researcher_name", "discovered", "in_business_journal", "share",
           "earliest_year", "verdict"], author_rows)

    print(f"  kept     {len(kept):>4}  publications")
    print(f"  review   {len(review):>4}  publications")
    print(f"  of which {sum(1 for r in review if 'collision' in r['review_reason']):>4}"
          "  are from a researcher that looks like a name collision\n")

    flagged = [r for r in author_rows if r["verdict"] != "accepted"]
    if flagged:
        print("  researchers excluded, check these by hand:")
        for r in flagged:
            print(f"    {r['researcher_name'][:28]:28} {r['discovered']:>4} found, "
                  f"{r['share']:>4} in a business journal, "
                  f"earliest {r['earliest_year']}")
    print()
    return kept, review, author_rows


def write(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Split OpenAlex-discovered publications into ones we trust "
                    "and ones a human should look at.")
    parser.add_argument("discovered")
    parser.add_argument("abdc", help="the ABDC workbook")
    parser.add_argument("--out-dir")
    args = parser.parse_args(argv)
    screen(args.discovered, args.abdc, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
