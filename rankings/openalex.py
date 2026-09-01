"""
OpenAlex citation data — CITS3200 Group 20.

Fills the `citation_percentile` column that the Scope of Work data dictionary
(3.5.4) defines and that our scrapers leave empty, and picks up each journal's
ISSN along the way.

    python openalex.py --publications ../unsw/output/unsw_publications.csv \
                       --mailto you@student.uwa.edu.au

Writes <name>_with_openalex.csv and <name>_with_openalex_notfound.csv.

WHAT IT ADDS
------------
    citation_percentile          0 to 1. OpenAlex's citation_normalized_percentile,
                                 which compares a paper against others of the same
                                 age and field. 0.98 means it is cited more than 98%
                                 of comparable work.
    citation_top_10_percent      true/false, straight from OpenAlex
    cited_by_count               raw citation count
    fwci                         field-weighted citation impact
    issn                         the journal's ISSN, see below
    openalex_id

WHY THIS ALSO FIXES OUR ISSN PROBLEM
------------------------------------
OpenAlex returns the journal's ISSNs with every work. Most university sites do
not publish ISSNs, so we have been matching journals to ABDC and Scimago on
their titles, which is fiddly and imperfect. Run this first and the enriched
file carries a real `issn` column, which abdc.py, scimago.py and journals.py all
pick up automatically and prefer over title matching.

LOOKUP IS BY DOI ONLY
---------------------
No DOI, no lookup. Guessing a paper from its title is exactly the kind of
approximate matching that puts the wrong citation count against someone's name.
Publications without a DOI are listed in the notfound file so the gap is
visible rather than silent.

POLITENESS
----------
OpenAlex asks for a contact address so they can reach you if a script
misbehaves, and in return puts you in a faster, more reliable pool. Requests
without one get rate-limited noticeably sooner (this module was written after
being throttled anonymously). --mailto is optional and is never stored in the
file, so nothing personal ends up committed.

Responses are cached, so a second run costs nothing.
"""

import argparse
import csv
import json
import os
import time
from collections import Counter

import requests

import harvest
import journal_match as jm

API = "https://api.openalex.org/works"
BATCH = 50               # OpenAlex allows up to 50 values in one filter
SELECT = ("id,doi,cited_by_count,fwci,citation_normalized_percentile,"
          "primary_location,publication_year,type")
# primary_location carries source.host_organization_name, which is where the
# publisher comes from, so no extra field is needed in SELECT.
PAUSE = 0.2              # between batches; OpenAlex allows ~10 requests/second
MAX_RETRIES = 4

# ISSNs belonging to a preprint server or repository rather than to a journal.
AGGREGATOR_ISSNS = {
    "1556-5068": "ssrn",
    "2331-8422": "arxiv",
}

ADDED_COLUMNS = ["citation_percentile", "citation_top_10_percent",
                 "cited_by_count", "fwci", "issn", "publisher", "openalex_id"]


def normalise_doi(value):
    """DOIs arrive with and without a URL prefix, and in mixed case."""
    if not value:
        return None
    text = str(value).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text or None


def fetch_batch(session, dois, mailto):
    """One request for up to BATCH DOIs. Returns {doi: work}."""
    params = {
        "filter": "doi:" + "|".join(dois),
        "per-page": BATCH,
        "select": SELECT,
    }
    if mailto:
        params["mailto"] = mailto

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(API, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"    ! network error ({exc}); retrying")
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429:
            # Backing off rather than hammering. Without --mailto this happens
            # a lot sooner.
            wait = 2 ** (attempt + 1)
            print(f"    rate limited, waiting {wait}s"
                  + ("" if mailto else " (try --mailto to avoid this)"))
            time.sleep(wait)
            continue
        if response.status_code != 200:
            print(f"    ! HTTP {response.status_code} for a batch of {len(dois)}")
            return {}
        results = response.json().get("results", [])
        return {normalise_doi(w.get("doi")): w for w in results if w.get("doi")}

    print(f"    ! gave up on a batch of {len(dois)} after {MAX_RETRIES} attempts")
    return {}


def extract(work):
    """Pull the fields we want out of one OpenAlex work, defensively."""
    percentile = work.get("citation_normalized_percentile") or {}
    source = ((work.get("primary_location") or {}).get("source")) or {}
    issns = source.get("issn") or []
    issn = source.get("issn_l") or (issns[0] if issns else None)
    # Reject a repository's own ISSN. When OpenAlex resolves a DOI to the SSRN
    # or arXiv copy of a paper, it hands back that repository's ISSN, and the
    # row then names the right journal while carrying a different journal's
    # identifier. 50 UNSW rows had SSRN's ISSN against Australian Tax Forum.
    # An ISSN that is not the journal's is worse than no ISSN, because
    # everything downstream joins on it.
    if issn and jm.normalise_issn(issn) in AGGREGATOR_ISSNS:
        name = (source.get("display_name") or "").lower()
        if AGGREGATOR_ISSNS[jm.normalise_issn(issn)] not in name:
            issn = None
    # The UNSW profile pages carry no publisher, so the column sat empty on
    # every row. OpenAlex knows it for any work we already matched, which is
    # free: the field comes back in the same response.
    publisher = source.get("host_organization_name") or None
    return {
        "citation_percentile": percentile.get("value"),
        "citation_top_10_percent": percentile.get("is_in_top_10_percent"),
        "cited_by_count": work.get("cited_by_count"),
        "fwci": work.get("fwci"),
        "issn": jm.normalise_issn(issn),
        "publisher": publisher,
        "openalex_id": (work.get("id") or "").rsplit("/", 1)[-1] or None,
    }


def enrich(publications_path, mailto=None, limit=None, use_cache=True):
    rows, fieldnames = jm.read_publications(publications_path)

    doi_column = next((c for c in fieldnames if jm.normalise(c) == "doi"), None)
    if doi_column is None:
        raise SystemExit(f"No doi column in {os.path.basename(publications_path)}. "
                         f"Columns are: {fieldnames}")

    wanted = []
    for row in rows:
        doi = normalise_doi(row.get(doi_column))
        row["_doi"] = doi
        if doi and doi not in wanted:
            wanted.append(doi)
    if limit:
        wanted = wanted[:limit]
        print(f"(limited to {len(wanted)} DOIs for this run)")

    cache_path = os.path.join(os.path.dirname(os.path.abspath(publications_path)),
                              "openalex_cache.json")
    cache = {}
    if use_cache and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"cache: {len(cache)} DOIs already looked up")

    todo = [d for d in wanted if d not in cache]
    print(f"{len(rows)} publications, {len(wanted)} distinct DOIs, "
          f"{len(todo)} to look up"
          + ("" if mailto else "   (no --mailto: expect rate limiting)"))

    session = requests.Session()
    session.headers.update({"User-Agent": "CITS3200-Group20/1.0"})
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        found = fetch_batch(session, batch, mailto)
        for doi in batch:
            cache[doi] = found.get(doi)          # None records "asked, not there"
        print(f"    {min(start + BATCH, len(todo)):>5}/{len(todo)}  "
              f"{len(found)} of {len(batch)} found")
        time.sleep(PAUSE)

    if todo:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)

    counts, notfound = Counter(), []
    for row in rows:
        doi = row.pop("_doi", None)
        blank = {c: None for c in ADDED_COLUMNS}
        if not doi:
            counts["no doi"] += 1
            row.update(blank)
            notfound.append({"title": row.get("title"), "doi": "",
                             "reason": "no DOI recorded"})
            continue
        work = cache.get(doi)
        if not work:
            counts["not in openalex"] += 1
            row.update(blank)
            notfound.append({"title": row.get("title"), "doi": doi,
                             "reason": "DOI not found in OpenAlex"})
            continue
        values = extract(work)
        # Do not overwrite an ISSN the scraper already captured.
        if row.get("issn"):
            values.pop("issn")
        row.update(values)
        counts["matched"] += 1
        if values.get("citation_percentile") is not None:
            counts["with a percentile"] += 1
        if values.get("issn"):
            counts["gained an issn"] += 1

    added = [c for c in ADDED_COLUMNS if c not in fieldnames]
    base, _ = os.path.splitext(publications_path)
    out_path = f"{base}_with_openalex.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + added,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    notfound_path = f"{base}_with_openalex_notfound.csv"
    with open(notfound_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "doi", "reason"],
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(notfound)

    # FR14 / data dictionary 3.5.4: record that OpenAlex ran, and when. The
    # university is read out of the file rather than passed in, so the row
    # cannot claim a university the data does not contain.
    university = harvest.university_in(rows)
    if university:
        harvest.record(university, "OpenAlex", harvest.latest_year_in(rows),
                       os.path.dirname(os.path.abspath(out_path)))

    print(f"\n  {out_path}")
    print(f"  {notfound_path}")
    print(f"\n  of {len(rows)} publications:")
    for key, n in counts.most_common():
        print(f"     {key:<20} {n}")
    return out_path, notfound_path


def main():
    parser = argparse.ArgumentParser(
        description="Add OpenAlex citation percentiles to a publications CSV.")
    parser.add_argument("--publications", required=True)
    parser.add_argument("--mailto",
                        help="your email, for OpenAlex's faster polite pool. "
                             "Not written to any output file.")
    parser.add_argument("--limit", type=int,
                        help="only look up this many DOIs (for testing)")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    enrich(args.publications, args.mailto, args.limit, not args.no_cache)


if __name__ == "__main__":
    main()
