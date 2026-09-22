"""Run the pipeline for one university, or all of them.

    python run.py --uni uq
    python run.py --uni uq --skip-clarivate --no-supplementary
    python run.py --uni uq --refresh
    python run.py --all
    python run.py --all --skip-clarivate
"""

import argparse
import importlib
import inspect
import subprocess
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


def discover_adapters():
    """Every adapter module in base_scrapers/, so a new one is picked up by --all."""
    folder = Path(__file__).resolve().parent / "base_scrapers"
    return sorted(p.stem for p in folder.glob("*.py") if not p.stem.startswith("_"))


def run_all(args):
    """Run each university in its own process and report which ones failed.

    A separate process per university means one crash does not stop the rest,
    and module-level state (http.FORCE_REFRESH, adapter caches) cannot leak from
    one run into the next. refresh_manager.py drives run.py the same way.
    """
    passthrough = [flag for flag, enabled in (
        ("--refresh", args.refresh),
        ("--no-supplementary", args.no_supplementary),
        ("--skip-clarivate", args.skip_clarivate),
        ("--drop-empty-staff", args.drop_empty_staff),
    ) if enabled]

    unis = discover_adapters()
    print(f"running {len(unis)} universities: {', '.join(unis)}")

    started = time.time()
    results = []
    for uni in unis:
        print(f"\n{'#' * 60}\n# {uni}\n{'#' * 60}", flush=True)
        uni_started = time.time()
        code = subprocess.call(
            [sys.executable, str(Path(__file__).resolve()), "--uni", uni, *passthrough]
        )
        results.append((uni, code, time.time() - uni_started))

    print(f"\n{'=' * 60}\nsummary\n{'=' * 60}")
    for uni, code, seconds in results:
        status = "ok" if code == 0 else f"FAILED (exit {code})"
        print(f"  {uni:<10} {status:<20} {seconds:6.0f}s")

    failed = [uni for uni, code, _ in results if code != 0]
    print(f"\n{len(results) - len(failed)}/{len(results)} succeeded "
          f"in {time.time() - started:.0f}s")
    if failed:
        print(f"failed: {', '.join(failed)}")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    target = ap.add_mutually_exclusive_group()
    target.add_argument("--uni", default=None,
                        help="module name in base_scrapers/ (default: uq)")
    target.add_argument("--all", action="store_true",
                        help="run every university in base_scrapers/, one after another")
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

    if args.all:
        if args.ror:
            ap.error("--ror applies to one university; each adapter supplies its own ROR under --all")
        return run_all(args)

    if args.uni is None:
        args.uni = "uq"

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
    collect_params = inspect.signature(adapter.collect).parameters
    if "refresh" in collect_params:
        collect_kwargs["refresh"] = args.refresh
    elif "refresh_roster" in collect_params:
        # UNSW names its source-specific cache flag differently from the newer
        # adapters. --refresh should still mean a genuinely fresh scrape.
        collect_kwargs["refresh_roster"] = args.refresh
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
    quality_writer = getattr(adapter, "write_quality_report", None)
    if callable(quality_writer):
        quality_writer(out)
        print(f"  quality        -> {out / (args.uni + '_adapter_quality.json')}")
        print(f"  identity review -> {out / (args.uni + '_identity_review.csv')}")

    print(f"\ndone in {time.time() - started:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
