"""Run the pipeline for one university.

    python run.py --uni uq
    python run.py --uni uq --skip-clarivate --no-supplementary
    python run.py --uni uq --refresh
"""

import argparse
import importlib
import inspect
import sys
import time
from core.clean import clean_pubs                        # noqa: E402
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import http                                   # noqa: E402
from core.config import OUTPUT_DIR                      # noqa: E402
from core.schema import validate                        # noqa: E402
from enrichment import abdc, clarivate, crossref as cr_enrich, openalex as oa_enrich, scimago  # noqa: E402
from export import export                               # noqa: E402
from screen import screen                               # noqa: E402
from info import crossref, openalex as oa_get, orcid      # noqa: E402


def step(n, label):
    print(f"\n=== {n}. {label} ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uni", default="uq",
                    help="module name in base_scrapers/")
    ap.add_argument("--refresh", action="store_true", help="ignore the HTTP cache")
    ap.add_argument("--no-supplementary", action="store_true",
                    help="skip ORCID/Crossref/OpenAlex retrieval")
    ap.add_argument("--skip-clarivate", action="store_true",
                    help="skip JIF (slowest step, needs an API key)")
    ap.add_argument("--ror", default=None,
                    help="restrict OpenAlex retrieval to this institution ROR")
    ap.add_argument("--drop-empty-staff", action="store_true",
                    help="exclude staff who have no publications (all official staff are kept by default)")
    args = ap.parse_args()

    if args.refresh:
        http.FORCE_REFRESH = True
        print("cache bypassed — fetching everything fresh")
    else:
        n, mb = http.cache_stats()
        print(f"cache: {n} responses, {mb} MB")

    adapter = importlib.import_module(f"base_scrapers.{args.uni}")
    started = time.time()

    step(1, f"{args.uni} adapter — staff, ids, publications")
    # Adapters may maintain source-specific caches in addition to the shared
    # HTTP cache. Pass the refresh request when their interface supports it,
    # while remaining compatible with the existing UQ/UNSW adapters.
    collect_kwargs = {}
    if "refresh" in inspect.signature(adapter.collect).parameters:
        collect_kwargs["refresh"] = args.refresh
    records, pubs = adapter.collect(**collect_kwargs)

    if not args.no_supplementary:
        step(2, "orcid retrieval")
        orcid.retrieve(records, pubs)

        step(3, "crossref retrieval")
        crossref.retrieve(records, pubs)

        step(4, "openalex retrieval")
        oa_get.retrieve(records, pubs, ror=args.ror or getattr(adapter, "ROR", None))

    step("4b", "clean + filter")
    pubs = clean_pubs(pubs, verbose=True)

    step(5, "openalex enrichment (doi)")
    oa_enrich.enrich(pubs)

    step(6, "crossref enrichment (doi)")
    cr_enrich.enrich(pubs)

    step(7, "abdc (issn)")
    abdc.enrich(pubs)

    # Before Clarivate, not after: a researcher's namesake contributes their
    # journals' ISSNs to the Clarivate query set, and that is the slowest step
    # in the run. Screening first makes it shorter as well as more correct.
    out = OUTPUT_DIR / args.uni
    step(8, "discipline screen")
    pubs = screen(records, pubs, out_dir=out)

    if not args.skip_clarivate:
        step(9, "clarivate jcr (issn)")
        clarivate.enrich(pubs)

    step(10, "scimago (issn)")
    scimago.enrich(pubs)

    step(11, "contract check")
    validate(records, pubs)

    step(12, "export")
    export(records, pubs, out_dir=out,
           drop_staff_without_pubs=args.drop_empty_staff)

    print(f"\ndone in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
