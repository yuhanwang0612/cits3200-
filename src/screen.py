"""Drop retrieved publications that are not in the researcher's discipline.

CITS3200 Group 20. Runs after ABDC and before the remaining ranking steps.

WHY THIS IS NEEDED
------------------
ORCID, Crossref and OpenAlex retrieval are all keyed on an ORCID, and an ORCID
is only as good as the record it was read off. Where an adapter recovers ORCIDs
by name — which a staff directory forces, because none of them publish one —
a namesake at the same university can supply the wrong ORCID, and then all
three retrieval steps faithfully fetch a stranger's entire career.

On UNSW this happened once in fifty-three. A finance lecturer picked up 57
publications, 24 of them in the Journal of the Korean Physical Society and the
rest in clinical medical physics and radiation oncology. Nothing upstream
noticed, because every step did exactly what it was told.

WHY NOT A VOLUME GUARD
----------------------
The obvious defence is "reject anyone whose retrieved count dwarfs their listed
count", which is what retrieve/openalex.py does. Measured on the real data it
does not separate the cases:

    Fariborz Moshirian    13 listed, 79 retrieved    legitimate
    Suk Lee                1 listed, 57 retrieved    a physicist
    Gordon Phillips        0 listed, 32 retrieved    legitimate

A researcher whose university page is out of date looks exactly like a
researcher whose ORCID is wrong. Volume is the wrong axis.

WHAT DOES SEPARATE THEM
-----------------------
Discipline. ABDC rates business, economics and finance journals and nothing
else, so "is this journal in ABDC" is a usable test of whether a paper belongs
to the discipline we are measuring. Counting only the retrieved rows ABDC
*could* have rated — journal articles that name a journal — the same three
separate cleanly:

    Fariborz Moshirian    78 of 79 rateable rows are ABDC-rated    99%
    Gordon Phillips       31 of 32                                 97%
    Peter Swan             3 of  5                                 60%   <- lowest legitimate
    Suk Lee                0 of every one of his                    0%

Nothing falls between 0% and 60%, so the threshold is not a fine judgement.

That denominator is the whole trick, and getting it wrong is not subtle. A
first version counted every retrieved row, and since most rows the retrieval
steps add are preprints and book chapters with no journal name — 96 of one
researcher's 101 — it read "cannot be rated" as "wrong discipline" and flagged
23 researchers instead of one.

WHAT IT WILL NOT DO
-------------------
It never touches a row the university itself listed. Those came from the
institution's own record of its own staff; if they are wrong that is a
different problem with a different fix, and silently deleting them would hide
it. Only rows added by a retrieval step are in scope.

It also writes everything it removes to `screened_out.csv` beside the other
outputs, with the reason. Nothing disappears without a trace, because the
judgement here is a heuristic and a human has to be able to overrule it.
"""

import csv

# Below this many JUDGEABLE rows, one odd journal proves nothing: a researcher
# with three retrieved papers, one of them in a medical journal, is a normal
# co-authorship and not a wrong ORCID.
MIN_ROWS = 5

# Measured above: contaminated 0%, lowest legitimate 60%.
MIN_ABDC_SHARE = 0.25

# Sources that added rows we did not scrape ourselves.
RETRIEVED = {"ORCID", "Crossref", "OpenAlex"}


def rateable(row):
    """Could ABDC have rated this row at all?

    Only a journal article with a journal name could. A preprint, a book
    chapter or a conference paper has no journal to look up, so its absence
    from ABDC says nothing about the researcher's discipline — and most rows
    the retrieval steps add are exactly that. Counting them as evidence of
    contamination flagged 23 researchers instead of 1, on data where 96 of one
    person's 101 rows simply had no journal name.
    """
    return bool(row.get("journal")) and row.get("type") == "Journal Article"


def summarise(pubs):
    """Per researcher: what was listed, what was retrieved, and of the
    retrieved rows ABDC *could* have rated, how many it did."""
    out = {}
    for row in pubs:
        name = row.get("name")
        if not name:
            continue
        entry = out.setdefault(name, {"listed": 0, "retrieved": 0,
                                      "judged": 0, "rated": 0})
        if row.get("source") in RETRIEVED:
            entry["retrieved"] += 1
            if rateable(row):
                entry["judged"] += 1
                if row.get("abdc"):
                    entry["rated"] += 1
        else:
            entry["listed"] += 1
    for entry in out.values():
        entry["share"] = (entry["rated"] / entry["judged"]
                          if entry["judged"] else None)
    return out


def suspect(entry):
    """True when this researcher's retrieved work looks like someone else's.

    MIN_ROWS counts the rows that could be judged, not every retrieved row. A
    researcher with thirty retrieved preprints and one journal article has one
    piece of evidence, not thirty-one.
    """
    return (entry["judged"] >= MIN_ROWS
            and entry["share"] is not None
            and entry["share"] < MIN_ABDC_SHARE)


def screen(records, pubs, out_dir=None, verbose=True):
    """Return pubs with out-of-discipline retrieved rows removed.

    `records` is used only to blank the ORCID on a researcher whose retrieved
    work was rejected: that ORCID is the thing that was wrong, and leaving it
    on the staff row would put it in the merged table and invite the next
    person to trust it.
    """
    stats = summarise(pubs)
    flagged = {name for name, entry in stats.items() if suspect(entry)}

    if not flagged:
        if verbose:
            print("screen: nothing looks out of discipline")
        return pubs

    kept, dropped = [], []
    for row in pubs:
        if row.get("name") in flagged and row.get("source") in RETRIEVED:
            dropped.append(row)
        else:
            kept.append(row)

    by_name = {}
    for row in dropped:
        by_name.setdefault(row["name"], []).append(row)

    for person in records:
        if person.get("name_clean") in flagged:
            # The ORCID is what was wrong. Keep the person, drop the claim.
            person["orcid"] = None
            person["openalex_author_ids"] = []

    if out_dir is not None:
        path = out_dir / "screened_out.csv"
        out_dir.mkdir(parents=True, exist_ok=True)
        columns = ["name", "title", "year", "journal", "doi", "source",
                   "screened_reason"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in dropped:
                share = stats[row["name"]]["share"]
                writer.writerow(dict(
                    row, screened_reason=(
                        f"only {share:.0%} of this researcher's retrieved "
                        f"journal articles are in an ABDC-rated journal; the "
                        f"ORCID they came from is probably a namesake's")))

    if verbose:
        print(f"screen: removed {len(dropped)} retrieved rows from "
              f"{len(flagged)} researcher(s)")
        for name, rows in sorted(by_name.items(), key=lambda kv: -len(kv[1])):
            entry = stats[name]
            top = {}
            for row in rows:
                journal = row.get("journal") or "(no journal)"
                top[journal] = top.get(journal, 0) + 1
            worst = sorted(top.items(), key=lambda kv: -kv[1])[:3]
            print(f"  {name}: {len(rows)} removed, {entry['listed']} listed "
                  f"rows kept. Of the {entry['judged']} retrieved rows ABDC "
                  f"could rate, {entry['rated']} were rated "
                  f"({entry['share']:.0%})")
            for journal, n in worst:
                print(f"      {n:>3}  {journal[:64]}")
        if out_dir is not None:
            print(f"  written to {out_dir / 'screened_out.csv'} — nothing is "
                  f"lost, and the call is reversible")
    return kept
