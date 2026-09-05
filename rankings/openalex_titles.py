"""Recover DOIs and links for publications the university listed without one.

CITS3200 Group 20. Companion to openalex.py, which looks works up BY DOI. This
one goes the other way: it searches by title for rows that have no DOI, so that
a row which the university recorded incompletely can still be linked.

Two action items from 2 September meet here:

  * "Capture a URL for publications that have no DOI" (Zarin Tasnim)
  * the OpenAlex source pipeline stage (Zarin Tasnim)

    python openalex_titles.py ../output/unsw_publications.csv --mailto you@uwa.edu.au
    python openalex_titles.py ../output/unsw_publications.csv --limit 20   # try it first

WHY THIS IS NARROWER THAN IT LOOKS
----------------------------------
Only rows whose journal demonstrably issues DOIs are attempted. If no other
publication in the same journal has a DOI, that journal almost certainly does
not register with Crossref, and searching for it burns budget to learn nothing.
On UNSW this is 285 rows out of 881 rather than all 881.

A title search is a WEAKER match than a DOI lookup. A DOI is an identifier; a
title is a description, and two different papers can share one. So a candidate
is only accepted when the title, an author surname AND the year all agree, and
every recovered row records how it was matched so a reviewer can filter it out.

Nothing that already has a DOI is ever touched.
"""

import argparse
import csv
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import journal_match as jm

API = "https://api.openalex.org/works"
PAUSE = 0.15
MAX_RETRIES = 4
TIMEOUT = 30

# Recovered rows are labelled, never silently merged in with DOI-matched ones.
MATCH_TITLE = "title+author+year"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_NONE = "not found"
MATCH_FAILED = "lookup failed"

ADDED_COLUMNS = ["doi_recovered_from", "recovery_match_type"]

# How alike two titles must be once normalised. Deliberately strict: this is a
# weaker signal than a DOI and the cost of a wrong match is someone else's
# paper appearing under a UNSW researcher's name.
TITLE_SIMILARITY = 0.93

# A year in our data and in OpenAlex can differ by one, because the online-first
# version and the issue version fall in different years. More than one is a
# different paper.
YEAR_TOLERANCE = 1


class BudgetExhausted(RuntimeError):
    """OpenAlex daily spending budget spent. Retrying cannot help."""


def normalise_title(text):
    return jm.normalise(text or "")


def surnames(row):
    """Surnames to look for in a candidate's author list.

    UNSW writes authors as 'Li H; Liu L; Masulis R', so the surname is the
    first word of each part. The researcher's own name is 'Ronald Masulis',
    so there the surname is the last word.
    """
    found = set()
    name = (row.get("researcher_name") or "").strip()
    if name:
        found.add(name.split()[-1].lower())
    for part in (row.get("coauthors") or "").split(";"):
        part = part.strip()
        if part:
            found.add(part.split()[0].lower())
    return {s for s in found if len(s) > 2}


def request_json(params, mailto, retries=MAX_RETRIES):
    """One search. Returns None on failure, raises on budget exhaustion.

    Failure and absence are kept apart, as everywhere else in this project: a
    network blip recorded as 'not found' becomes a permanent wrong answer.
    """
    if mailto:
        params = dict(params, mailto=mailto)
    url = f"{API}?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            time.sleep(PAUSE)
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = ""
            try:
                body = error.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            # The budget message arrives as a 429, exactly like ordinary
            # throttling, but backing off cannot fix it.
            if "budget" in body.lower() or "insufficient" in body.lower():
                raise BudgetExhausted(body.strip() or "daily budget spent")
            if error.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(2 ** attempt)
    return None


def year_of(row):
    text = (row.get("year") or "").strip()
    return int(text) if text.isdigit() else None


def choose(row, candidates):
    """Pick the one work that is certainly this paper, or none.

    Returns (work, match_type). Anything short of title, author and year all
    agreeing returns nothing, because a plausible guess in this column is worse
    than a blank: nobody downstream can tell it from a certainty.
    """
    wanted_title = normalise_title(row.get("title"))
    wanted_year = year_of(row)
    wanted_names = surnames(row)
    if not wanted_title or not wanted_names:
        return None, MATCH_NONE

    accepted = []
    for work in candidates:
        ratio = difflib.SequenceMatcher(
            None, wanted_title, normalise_title(work.get("title"))).ratio()
        if ratio < TITLE_SIMILARITY:
            continue

        if wanted_year is not None:
            year = work.get("publication_year")
            if year and abs(int(year) - wanted_year) > YEAR_TOLERANCE:
                continue

        authors = " ".join(
            (a.get("author") or {}).get("display_name", "")
            for a in (work.get("authorships") or [])).lower()
        if not any(s in authors for s in wanted_names):
            continue

        accepted.append(work)

    if not accepted:
        return None, MATCH_NONE
    if len(accepted) > 1:
        # Two works pass every test. One of them is a duplicate record, a
        # preprint, or a different paper; we cannot tell which, so take none.
        return None, MATCH_AMBIGUOUS
    return accepted[0], MATCH_TITLE


def link_of(work):
    """A URL for the paper, preferring one that will still resolve in ten years."""
    doi = (work.get("doi") or "").strip()
    if doi:
        return doi if doi.startswith("http") else f"https://doi.org/{doi}"
    location = work.get("primary_location") or work.get("best_oa_location") or {}
    return (location.get("landing_page_url") or location.get("pdf_url")
            or work.get("id") or None)


def bare_doi(work):
    doi = (work.get("doi") or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi) or None


def worth_trying(rows):
    """Rows with no DOI, in a journal that other rows show does issue DOIs.

    Attempting the rest would spend budget establishing something already
    known: the Weekly Tax Bulletin does not mint DOIs, so no amount of
    searching will find one.
    """
    issues_dois = defaultdict(bool)
    for row in rows:
        key = normalise_title(row.get("journal_name"))
        if key and (row.get("doi") or "").strip():
            issues_dois[key] = True

    targets, skipped = [], 0
    for row in rows:
        if (row.get("doi") or "").strip():
            continue
        key = normalise_title(row.get("journal_name"))
        if key and issues_dois[key]:
            targets.append(row)
        else:
            skipped += 1
    return targets, skipped


def recover(path, mailto=None, limit=None):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    for column in ADDED_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)

    targets, skipped = worth_trying(rows)
    if limit:
        targets = targets[:limit]

    print(f"\n{len(rows)} publications")
    print(f"  {sum(1 for r in rows if (r.get('doi') or '').strip())} already have a DOI, untouched")
    print(f"  {skipped} have none and are in a journal that issues none, not attempted")
    print(f"  {len(targets)} will be searched by title\n")

    counts, recovered_doi, recovered_url = Counter(), 0, 0
    aborted = False

    for i, row in enumerate(targets, 1):
        title = (row.get("title") or "").strip()
        if not title:
            counts[MATCH_NONE] += 1
            continue
        try:
            payload = request_json(
                {"filter": f"title.search:{title[:250]}", "per-page": 10,
                 "select": "id,doi,title,publication_year,authorships,primary_location,best_oa_location"},
                mailto)
        except BudgetExhausted as error:
            print(f"\n  ! OpenAlex daily budget spent: {error}")
            print("    Stopping. Nothing written, so the file is unchanged and")
            print("    a partial result cannot be mistaken for a complete one.")
            aborted = True
            break

        if payload is None:
            row["recovery_match_type"] = MATCH_FAILED
            counts[MATCH_FAILED] += 1
            continue

        work, how = choose(row, payload.get("results") or [])
        row["recovery_match_type"] = how
        counts[how] += 1

        if work:
            doi = bare_doi(work)
            link = link_of(work)
            if doi and not (row.get("doi") or "").strip():
                row["doi"] = doi
                row["doi_recovered_from"] = "openalex title search"
                recovered_doi += 1
            if link and not (row.get("article_url") or "").strip():
                row["article_url"] = link
                recovered_url += 1

        if i % 25 == 0:
            print(f"  {i}/{len(targets)} searched...", flush=True)

    if aborted:
        return None, counts

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  matched on title, author and year : {counts[MATCH_TITLE]}")
    print(f"  several equally good candidates   : {counts[MATCH_AMBIGUOUS]}  (nothing taken)")
    print(f"  no match                          : {counts[MATCH_NONE]}")
    print(f"  lookup failed                     : {counts[MATCH_FAILED]}")
    print(f"\n  DOIs recovered : {recovered_doi}")
    print(f"  URLs recovered : {recovered_url}")
    still = sum(1 for r in rows if not (r.get('article_url') or '').strip()
                and not (r.get('doi') or '').strip())
    print(f"  still with neither a DOI nor a URL: {still}\n")
    return path, counts


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Find DOIs and links for publications the university "
                    "listed without one, by searching OpenAlex on title.")
    p.add_argument("publications")
    p.add_argument("--mailto", help="your email, for OpenAlex's faster polite pool")
    p.add_argument("--limit", type=int,
                   help="only try this many (do this first: each search costs budget)")
    args = p.parse_args(argv)
    recover(args.publications, args.mailto, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
