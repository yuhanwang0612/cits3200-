"""Run the pipeline for one university.

    python src/run.py --uni uq
    python src/run.py --uni uq --skip-clarivate --no-supplementary
    python src/run.py --uni uq --refresh
"""

import argparse
import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import http                                   # noqa: E402
from core.config import OUTPUT_DIR                      # noqa: E402
from core.schema import validate                        # noqa: E402
from enrich import abdc, clarivate, openalex as oa_enrich, scimago   # noqa: E402
from export import export                               # noqa: E402
from retrieve import crossref, openalex as oa_get, orcid  # noqa: E402


def step(n, label):
    print(f"\n=== {n}. {label} ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uni", default="uq", help="adapter module name")
    ap.add_argument("--refresh", action="store_true", help="ignore the HTTP cache")
    ap.add_argument("--no-supplementary", action="store_true",
                    help="skip ORCID/Crossref/OpenAlex retrieval")
    ap.add_argument("--skip-clarivate", action="store_true",
                    help="skip JIF (slowest step, needs an API key)")
    ap.add_argument("--ror", default=None,
                    help="restrict OpenAlex retrieval to this institution ROR")
    ap.add_argument("--keep-empty-staff", action="store_true",
                    help="keep staff who have no publications (dropped by default)")
    args = ap.parse_args()

    if args.refresh:
        http.FORCE_REFRESH = True
        print("cache bypassed — fetching everything fresh")
    else:
        n, mb = http.cache_stats()
        print(f"cache: {n} responses, {mb} MB")

    adapter = importlib.import_module(f"adapters.{args.uni}")
    started = time.time()

    step(1, f"{args.uni} adapter — staff, ids, publications")
    records, pubs = adapter.collect()

    if not args.no_supplementary:
        step(2, "orcid retrieval")
        orcid.retrieve(records, pubs)

        step(3, "crossref retrieval")
        crossref.retrieve(records, pubs)

        step(4, "openalex retrieval")
        oa_get.retrieve(records, pubs, ror=args.ror or getattr(adapter, "ROR", None))

    step(5, "openalex enrichment (doi)")
    oa_enrich.enrich(pubs)

    step(6, "abdc (issn)")
    abdc.enrich(pubs)

    if not args.skip_clarivate:
        step(7, "clarivate jcr (issn)")
        clarivate.enrich(pubs)

    step(8, "scimago (issn)")
    scimago.enrich(pubs)

    step(9, "contract check")
    validate(records, pubs)

    step(10, "export")
    out = OUTPUT_DIR / args.uni
    export(records, pubs, out_dir=out,
           drop_staff_without_pubs=not args.keep_empty_staff)

    print(f"\ndone in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
