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
count", which is what info/openalex.py does. Measured on the real data it
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

It also writes everything it removes to `<uni>_screened_out.csv` beside the
other outputs, with the reason. Nothing disappears without a trace, because
the judgement here is a heuristic and a human has to be able to overrule it.
"""

import csv
import re

# Below this many JUDGEABLE rows, one odd journal proves nothing: a researcher
# with three retrieved papers, one of them in a medical journal, is a normal
# co-authorship and not a wrong ORCID.
MIN_ROWS = 5

# Measured above: contaminated 0%, lowest legitimate 60%.
MIN_ABDC_SHARE = 0.25

# Sources that added rows we did not scrape ourselves.
RETRIEVED = {"ORCID", "Crossref", "OpenAlex"}

# Reviewed UniMelb OpenAlex collisions.  These rules are deliberately scoped
# to one named researcher and to OpenAlex additions: official Minerva rows and
# self-declared ORCID rows are never removed by them.  Common names such as
# Jun Yu and Qi Zeng are heavily merged in OpenAlex; their ABDC-matched rows
# form a conservative business-publication subset, while the unranked cluster
# consists overwhelmingly of engineering, materials and clinical papers.
_DROP_ALL_OPENALEX = {"Bryan Lim", "Patrick J. Kelly"}
_OPENALEX_REQUIRE_ABDC = {"Albie Brooks", "Jun Yu", "Nitin Yadav", "Qi Zeng"}
_MICHAEL_DAVERN_NAMESAKE = re.compile(
    r"health|medicaid|medicare|uninsur|insurance coverage|census|schip|"
    r"patient|asthma|tobacco|cancer surgery|minority group|rural latino|"
    r"telephone survey|survey sample|survey estimates|race and ethnicity|"
    r"social networks|face-to-face interviews|the polls|prestige attainment|"
    r"population survey|public use data|privately insured|auxiliary sample frame|"
    r"\bchip\b",
    re.I,
)
_CONFIRMED_WRONG_DOIS = {
    ("Qingbo Yuan", "10.1007/s10404-017-1854-2"),
    ("Stefan Schantl", "10.1109/access.2021.3112297"),
}
_CONFIRMED_WRONG_TITLES = {
    ("Mrinal Mishra", "study on impact of covid-19 on 5a's of telemedicine"),
    ("Mrinal Mishra", "the effect of conflict on lending: evidence from indian border areas"),
}


def reviewed_namesake_reason(row):
    """Return a reason for a specifically reviewed OpenAlex collision."""
    if row.get("source") != "OpenAlex":
        return None
    name = row.get("name") or ""
    title = (row.get("title") or "").strip()
    doi = (row.get("doi") or "").strip().lower()
    if name in _DROP_ALL_OPENALEX:
        return "reviewed OpenAlex namesake cluster outside this researcher's field"
    if name in _OPENALEX_REQUIRE_ABDC and not row.get("abdc"):
        return "common-name OpenAlex cluster; no business-journal evidence for this row"
    if name == "Qi Zeng" and "immunoassay" in title.lower():
        return "reviewed medical-statistics namesake publication"
    if name == "Michael Davern" and _MICHAEL_DAVERN_NAMESAKE.search(title + " " + (row.get("journal") or "")):
        return "reviewed US public-health researcher namesake publication"
    if (name, doi) in _CONFIRMED_WRONG_DOIS:
        return "reviewed namesake publication outside Accounting/Finance"
    if (name, title.lower()) in _CONFIRMED_WRONG_TITLES:
        return "reviewed namesake publication outside Accounting/Finance"
    return None


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


def _remove_stale_screened_out(out_dir):
    """A run that removes nothing must not leave an earlier run's list behind.

    The file used to be written only when something was removed, so a clean
    run kept the previous file: Adelaide's still listed 25 papers of a
    researcher who was no longer in the data at all.
    """
    if out_dir is not None:
        (out_dir / f"{out_dir.name}_screened_out.csv").unlink(missing_ok=True)


def screen(records, pubs, out_dir=None, verbose=True):
    """Return pubs with out-of-discipline retrieved rows removed.

    `records` is used only to blank the ORCID on a researcher whose retrieved
    work was rejected: that ORCID is the thing that was wrong, and leaving it
    on the staff row would put it in the merged table and invite the next
    person to trust it.
    """
    stats = summarise(pubs)
    # A repository-issued author ID plus an exact repository relationship is
    # direct identity evidence.  ABDC coverage is not: legitimate accounting
    # and finance researchers also publish interdisciplinary work in journals
    # outside that list.  Keep the heuristic for name-resolved OpenAlex
    # identities, but never use it to overrule an adapter's verified internal
    # author identifier.
    repository_verified = {
        person.get("name_clean")
        for person in records
        if person.get("source_id") and person.get("identity_confidence") == "high"
    }
    flagged = {
        name for name, entry in stats.items()
        if suspect(entry) and name not in repository_verified
    }

    reviewed_present = any(reviewed_namesake_reason(row) for row in pubs)
    if not flagged and not reviewed_present:
        _remove_stale_screened_out(out_dir)
        if verbose:
            print("screen: nothing looks out of discipline")
        return pubs

    kept, dropped = [], []
    for row in pubs:
        reviewed_reason = reviewed_namesake_reason(row)
        if reviewed_reason:
            dropped.append(dict(row, _screened_reason=reviewed_reason))
        elif row.get("name") in flagged and row.get("source") in RETRIEVED:
            dropped.append(dict(
                row,
                _screened_reason=(
                    f"only {stats[row['name']]['share']:.0%} of this researcher's "
                    "retrieved journal articles are in an ABDC-rated journal; "
                    "the identifier probably belongs to a namesake"
                ),
            ))
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

    if out_dir is not None and not dropped:
        _remove_stale_screened_out(out_dir)
    elif out_dir is not None:
        path = out_dir / f"{out_dir.name}_screened_out.csv"
        out_dir.mkdir(parents=True, exist_ok=True)
        columns = ["name", "title", "year", "journal", "doi", "source",
                   "screened_reason"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in dropped:
                writer.writerow(dict(
                    row, screened_reason=row.get("_screened_reason") or
                    "reviewed namesake publication"))

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
            print(f"  written to {path.name}, nothing is lost and the call "
                  f"is reversible")
    return kept
