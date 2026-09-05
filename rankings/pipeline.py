"""
One command for the whole enrichment chain — CITS3200 Group 20.

    python pipeline.py --publications ../output/unsw_publications_raw.csv \
                       --abdc "../ABDC-JQL-2025-v2-270526.xlsx" \
                       --scimago "../scimagojr 2025.csv"

WHY THIS EXISTS
---------------
The steps have to run in order: `openalex.py` adds an `issn` column, and the
ranking steps match on it. Run them the other way round and every journal is
matched on its title instead, which is not an error — it is a quietly worse
result that looks exactly like a good one.

Order was never really the problem though. The problem is that each step writes
a *new* file with a suffix on the end, and a human has to type the right
filename into the next command:

    unsw_publications_raw.csv                     (the scraper)
      -> unsw_publications_with_openalex.csv        (openalex.py)
        -> journals.csv                             (journals.py)
          -> unsw_publications.csv                  (final, this module)

Two chances to point at the wrong file. Both have already been taken on this
project: once by feeding the raw file to the ranking step so the ISSNs were
ignored, and once by running against a copy of the output folder that was a
week old.

So this module calls the steps as functions and passes the intermediate
filenames between them itself. You name the inputs once. There is no
intermediate filename to get wrong, and the order cannot be got wrong because
it is not a thing anyone types.

It is deliberately not clever: no dependency graph, no config file, no
detection of what has already run. It is the same three calls in the same
order every time, which is the property that makes it trustworthy.

WHAT IT DOES NOT DO
-------------------
It does not run the scraper. Scraping is per-university, needs a browser and
takes real time; the enrichment chain is shared by all eight and runs offline
against a cache. Bolting them together would mean nobody could re-run the cheap
half without the expensive half.
"""

import argparse
import os
import shutil

import journals as journals_mod
import openalex as openalex_mod



def final_name(path):
    """Where the finished file belongs.

    The intermediate is called `<name>_with_openalex.csv`, which was accurate
    when OpenAlex was the only enrichment step and is now misleading: that file
    also carries ABDC, Scimago and Clarivate. Worse, it left the plain
    `unsw_publications.csv` holding the *raw* scrape, so anyone reaching for
    the obvious filename got a file with no ratings on it. That happened.

    So the finished file takes the plain name. `<uni>_publications_raw.csv`
    becomes `<uni>_publications.csv`; anything else gains a `_final` suffix
    rather than silently overwriting its own input.
    """
    directory, name = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(name)
    for suffix in ("_raw", "_with_openalex"):
        if stem.endswith(suffix):
            return os.path.join(directory, stem[: -len(suffix)] + ext)
    return os.path.join(directory, stem + "_final" + ext)


def run(publications, abdc=None, scimago=None, mailto=None,
        skip_openalex=False, year=None, journal_column=None,
        at_least_one=False, use_cache=True):
    """Enrich a publications CSV and build the journal table from the result.

    Returns (enriched_publications_path, journals_path). The first is None when
    the OpenAlex step is skipped, in which case the ranking step runs on the
    file it was given.
    """
    if not os.path.exists(publications):
        raise SystemExit(f"No such publications file: {publications}")
    if not abdc and not scimago:
        raise SystemExit("Give at least one of --abdc or --scimago; without a "
                         "reference list there is nothing to look up.")

    enriched = None
    if skip_openalex:
        print("Step 1/2  OpenAlex        SKIPPED")
        print("          Journals will be matched on their titles only.")
        source = publications
    else:
        print("Step 1/2  OpenAlex")
        enriched, _ = openalex_mod.enrich(publications, mailto, use_cache=use_cache)
        source = enriched

    print("\nStep 2/2  Journal table")
    # write_back=True: the ratings go onto every publication row as well as
    # into journals.csv. UQ, Monash and Adelaide all carry quality_rank on the
    # publication row, and a merge that expects it there reads UNSW as unrated
    # otherwise.
    journals_path = journals_mod.build(source, abdc, scimago, journal_column,
                                       year, at_least_one, write_back=True)

    # Copy the finished rows to the name the team merges on. A copy rather
    # than a move, so the intermediate stays on disk for anyone comparing the
    # before and after of the enrichment.
    final = final_name(publications)
    shutil.copyfile(source, final)

    print("\nDone.")
    print(f"  MERGE THIS    {final}")
    print(f"  journals      {journals_path}")
    print(f"  harvest       {os.path.join(os.path.dirname(os.path.abspath(source)), 'harvest.csv')}")
    if enriched:
        print(f"\n  (intermediate, not for merging: {os.path.basename(enriched)})")
    return final, journals_path


def main():
    parser = argparse.ArgumentParser(
        description="Run the whole enrichment chain in the right order.")
    parser.add_argument("--publications", required=True,
                        help="the raw *_publications.csv from a scraper")
    parser.add_argument("--abdc", help="the ABDC Journal Quality List .xlsx")
    parser.add_argument("--scimago", help="the Scimago export (semicolon CSV)")
    parser.add_argument("--mailto", help="your email, for OpenAlex's faster "
                                         "polite pool. Never written to output.")
    parser.add_argument("--skip-openalex", action="store_true",
                        help="rank on titles alone, without calling OpenAlex. "
                             "Only for working offline; the match rate is worse.")
    parser.add_argument("--year", help="which ABDC edition (default: latest)")
    parser.add_argument("--journal-column", help="override the journal-name column")
    parser.add_argument("--at-least-one", action="store_true",
                        help="only write journals that matched a source")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore the OpenAlex response cache")
    args = parser.parse_args()

    run(args.publications, args.abdc, args.scimago, args.mailto,
        args.skip_openalex, args.year, args.journal_column,
        args.at_least_one, not args.no_cache)


if __name__ == "__main__":
    main()
