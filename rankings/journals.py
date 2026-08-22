"""
Journal table builder — CITS3200 Group 20.

Turns any of our publications CSVs into a **journal-level** table: one row per
journal instead of the same rating repeated on every publication row.

    python journals.py --publications ../unsw/output/unsw_publications.csv \
                       --abdc "../data/ABDC-JQL-2025-v2-270526.xlsx" \
                       --scimago "../data/scimagojr 2025.csv"

WHY A SEPARATE TABLE
--------------------
The Scope of Work data dictionary (3.5.4) models Journal as its own entity —
`journal_name, issn, quality_rank, impact_factor, impact_factor_5yr` — not as
columns on Publication. Sean's and Yuhan's exports already do this. Repeating a
journal's rating on 2,000 publication rows means 2,000 chances for it to
disagree with itself, and it makes the JIF join (which is per journal, not per
paper) much harder than it needs to be.

WHERE THE ISSN COMES FROM
-------------------------
Most university websites do not publish ISSNs — UNSW's staff pages certainly do
not — which is why our scrapers cannot capture one. But the ABDC list and the
Scimago export both carry ISSNs, so every journal we successfully match gets one
for free, with no extra requests to anybody. That is the join key the whole team
needs for Clarivate JIF later.

Where both sources give an ISSN and they disagree, both are kept
(`issn` from ABDC, `issn_scimago`) rather than silently picking one — a
disagreement means the two lists matched different journals, and that is worth
seeing rather than hiding.

--at-least-one restricts the output to journals that matched something. The
default writes every journal we saw, including unmatched ones, because the
unmatched list is the useful part: it is the to-do list for improving coverage.
"""

import argparse
import csv
import os
from collections import Counter, OrderedDict

import journal_match as jm
import abdc as abdc_mod
import scimago as scimago_mod

# How much a match can be trusted. An exact title or an ISSN hit is solid; a
# match that needed a subtitle trimmed off either side is a guess that happened
# to land. When two sources disagree about which journal this is, the weaker
# match is the one that is wrong.
STRENGTH = {"issn": 4, "title": 3, "title-variant": 2, "prefix": 2,
            "abdc-prefix": 2, "fuzzy": 1}

COLUMNS = [
    "journal_name",          # as written in our publication records (the join key)
    "journal_canonical",     # the reference list's spelling
    "issn",                  # ABDC print ISSN
    "issn_online",           # ABDC online ISSN
    "issn_scimago",          # Scimago's, only when it differs from ABDC's
    "quality_rank",          # ABDC: A*, A, B, C, or "none" if not on the list
    "abdc_list_year",
    "abdc_for_code",
    "sjr",
    "sjr_quartile",
    "h_index",
    "cites_per_doc_2y",
    "scimago_categories",
    "impact_factor",         # Clarivate JIF — not available yet
    "impact_factor_5yr",     # Clarivate JIF 5-year — not available yet
    "publication_count",     # how many of our publications are in this journal
    "abdc_match_type",
    "scimago_match_type",
    "issn_conflict",         # set when the two sources point at different journals
]


def resolve_conflict(record, abdc_rec, abdc_how, sci_rec, sci_how):
    """Drop a match that the other source's ISSN proves wrong.

    Real case: "Journal of Banking and Finance: Law and Practice" is an
    Australian practitioner journal, ABDC A. Trimming the subtitle turns it
    into "Journal of Banking and Finance", which Scimago matched to the
    top-tier finance journal of that name — SJR 1.954, Q1, h-index 225.
    Nothing in either dataset looks wrong on its own; only the clash of ISSNs
    reveals it.

    Returns (sci_rec_or_None, note). The weaker match is discarded. When both
    matched with equal confidence the conflict is real ambiguity — two
    different journals genuinely share a title, e.g. "Economia" — so both are
    kept and the row is flagged for a human.
    """
    abdc_issns = {i for i in (abdc_rec.get("issn"), abdc_rec.get("issn_online")) if i}
    sci_issns = set(sci_rec.get("issns") or [])
    if not abdc_issns or not sci_issns or (abdc_issns & sci_issns):
        return sci_rec, None

    abdc_strength = STRENGTH.get(abdc_how, 0)
    sci_strength = STRENGTH.get(sci_how, 0)
    if sci_strength < abdc_strength:
        return None, (f"Scimago matched '{sci_rec['title']}' ({'; '.join(sorted(sci_issns))}) "
                      f"via {sci_how}; ABDC matched '{abdc_rec['title']}' exactly. "
                      f"Scimago figures dropped as a mis-match.")
    if abdc_strength < sci_strength:
        return sci_rec, (f"ABDC matched '{abdc_rec['title']}' via {abdc_how} but its ISSN "
                         f"disagrees with Scimago's — treat the rating with caution.")
    return sci_rec, (f"ABDC says {'; '.join(sorted(abdc_issns))}, Scimago says "
                     f"{'; '.join(sorted(sci_issns))}. Same title, different journals — "
                     f"needs a human to pick.")


def build(publications_path, abdc_path=None, scimago_path=None,
          journal_column=None, year=None, at_least_one=False):
    # Guarded here rather than only in main(): with neither source this would
    # write a table of journal names and empty columns, which looks like a
    # successful run that found nothing.
    if not abdc_path and not scimago_path:
        raise SystemExit("journals.build needs at least one of abdc_path or "
                         "scimago_path — otherwise there is nothing to look up.")
    rows, fieldnames = jm.read_publications(publications_path)
    column, issn_column = jm.find_columns(fieldnames, journal_column)

    # Count publications per journal, keeping the first spelling we saw so the
    # output joins back to the publications file on the exact string.
    #
    # The ISSN is collected here too. Without it every journal would be matched
    # on its title even when the publications file already carries an ISSN
    # (openalex.py adds one), which throws away the only unambiguous key we
    # have and leaves the match rate exactly where it was.
    counts, issn_by_journal = OrderedDict(), {}
    for row in rows:
        name = (row.get(column) or "").strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        if issn_column and name not in issn_by_journal:
            issn = jm.normalise_issn(row.get(issn_column))
            if issn:
                issn_by_journal[name] = issn
    print(f"Publications: {len(rows)} rows, {len(counts)} distinct journals "
          f"in '{column}'"
          + (f"; {len(issn_by_journal)} of them have an ISSN in '{issn_column}'"
             if issn_column else ""))

    abdc_index = abdc_aliases = None
    list_year = None
    if abdc_path:
        abdc_index, sheet_name, _ = abdc_mod.load_abdc(abdc_path, year)
        abdc_aliases = jm.build_aliases(abdc_index)
        list_year = sheet_name
        for token in sheet_name.split():
            if token.isdigit() and len(token) == 4:
                list_year = token
        print(f"ABDC:    sheet '{sheet_name}', {len(abdc_index)} keys")

    sci_index = sci_aliases = None
    if scimago_path:
        sci_index, _, _ = scimago_mod.load_scimago(scimago_path)
        sci_aliases = jm.build_aliases(sci_index)
        print(f"Scimago: {len(sci_index)} keys")

    out, stats = [], Counter()
    for name, count in counts.items():
        record = {c: None for c in COLUMNS}
        record["journal_name"] = name
        record["publication_count"] = count
        # An ISSN the publications file already carries beats anything we can
        # derive from a title match, so it goes in first and is used as the
        # match key below.
        known_issn = issn_by_journal.get(name)
        record["issn"] = known_issn

        abdc_rec = sci_rec = None
        if abdc_index:
            abdc_rec, how = abdc_mod.match_journal(name, known_issn, abdc_index,
                                                   abdc_aliases)
            record["abdc_match_type"] = how
            if abdc_rec:
                record.update({
                    "journal_canonical": abdc_rec["title"],
                    "issn": known_issn or abdc_rec.get("issn"),
                    "issn_online": abdc_rec.get("issn_online"),
                    "quality_rank": abdc_rec["rating"],
                    "abdc_for_code": abdc_rec.get("for_code"),
                    "abdc_list_year": list_year,
                })
                stats["abdc"] += 1
            else:
                # The client asked on 12 August for journals that are not in
                # ABDC to read "none" rather than being left blank.
                record["quality_rank"] = abdc_mod.UNRATED
                record["abdc_list_year"] = list_year

        if sci_index:
            sci_rec, how = jm.match_journal(name, known_issn, sci_index, sci_aliases)
            record["scimago_match_type"] = how
            if sci_rec and abdc_rec:
                sci_rec, note = resolve_conflict(
                    record, abdc_rec, record["abdc_match_type"], sci_rec, how)
                if note:
                    record["issn_conflict"] = note
                    stats["issn conflict"] += 1
                if sci_rec is None:
                    record["scimago_match_type"] = None
                    stats["scimago match rejected"] += 1
            if sci_rec:
                record.update({
                    "sjr": sci_rec["sjr"],
                    "sjr_quartile": sci_rec["sjr_quartile"],
                    "h_index": sci_rec["h_index"],
                    "cites_per_doc_2y": sci_rec["cites_per_doc_2y"],
                    "scimago_categories": sci_rec["categories"],
                })
                if not record["journal_canonical"]:
                    record["journal_canonical"] = sci_rec["title"]
                stats["scimago"] += 1
                # Fill the ISSN from Scimago when ABDC had none, and flag a
                # disagreement rather than overwriting.
                sci_issns = sci_rec.get("issns") or []
                if sci_issns:
                    known = {record["issn"], record["issn_online"]}
                    if not record["issn"]:
                        record["issn"] = sci_issns[0]
                        stats["issn from scimago"] += 1
                    elif not (known & set(sci_issns)):
                        record["issn_scimago"] = "; ".join(sci_issns)

        if abdc_rec or sci_rec:
            stats["matched at least one"] += 1
        else:
            stats["unmatched by both"] += 1
        if record["issn"]:
            stats["has an issn"] += 1

        if at_least_one and not (abdc_rec or sci_rec):
            continue
        out.append(record)

    out.sort(key=lambda r: (-r["publication_count"], r["journal_name"].lower()))

    base = os.path.dirname(os.path.abspath(publications_path))
    path = os.path.join(base, "journals.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out)

    print(f"\n  {path}   ({len(out)} journals)")
    for key in ("matched at least one", "abdc", "scimago", "unmatched by both",
                "has an issn", "issn from scimago", "issn conflict",
                "scimago match rejected"):
        if key in stats:
            print(f"     {key:<22} {stats[key]}")
    if stats["has an issn"]:
        print(f"\n  {stats['has an issn']} of {len(counts)} journals now carry an "
              f"ISSN that no university website gave us.")
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Build a journal-level table from a publications CSV.")
    parser.add_argument("--publications", required=True)
    parser.add_argument("--abdc", help="the ABDC Journal Quality List .xlsx")
    parser.add_argument("--scimago", help="the Scimago export (semicolon CSV)")
    parser.add_argument("--year", help="which ABDC edition (default: latest)")
    parser.add_argument("--journal-column", help="override the journal-name column")
    parser.add_argument("--at-least-one", action="store_true",
                        help="only write journals that matched a source")
    args = parser.parse_args()
    if not args.abdc and not args.scimago:
        raise SystemExit("Give at least one of --abdc or --scimago.")
    build(args.publications, args.abdc, args.scimago, args.journal_column,
          args.year, args.at_least_one)


if __name__ == "__main__":
    main()
