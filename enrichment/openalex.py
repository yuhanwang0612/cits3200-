"""
enrichment/openalex.py
======================
Enrich publications with citation metrics from the OpenAlex API.

OpenAlex is free and open — no API key required.
Polite pool (faster): set your email in .env as  OPENALEX_EMAIL=you@example.com

Reads DOIs from all  final output/<uni>/<uni>_publications.csv  files,
queries OpenAlex for each, and writes:
    data/openalex.csv  — doi, title, cited_by_count, fwci, citation_percentile,
                          openalex_id, publication_year

Also supports journal-level lookups by ISSN for open-access status and
concept tagging.

Usage
-----
    # Fetch all publication metrics (uses DOIs):
    python3 enrichment/openalex.py

    # Dry run (first 3 DOIs only):
    python3 enrichment/openalex.py --dry-run

    # Look up a single DOI:
    python3 enrichment/openalex.py --doi 10.1000/xyz123

    # Look up a journal by ISSN:
    python3 enrichment/openalex.py --issn 00220515
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Config ─────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_CSV  = DATA_DIR / "openalex.csv"

EMAIL    = os.environ.get("OPENALEX_EMAIL", "")   # polite pool
BASE_URL = "https://api.openalex.org"
DELAY_S  = 0.1   # 10 req/s max in polite pool; be conservative

OUT_FIELDS = [
    "doi", "openalex_id", "title", "publication_year",
    "cited_by_count", "fwci", "citation_percentile",
    "is_oa", "oa_status",
]

_PUB_NAMES = [
    "monash_publications.csv",
    "adelaide_publications.csv",
    "uq_publications.csv",
    "unimelb_publications.csv",
    "usyd_publications.csv",
    "uwa_publications.csv",
    "anu_publications.csv",
    "unsw_publications.csv",
]


# ── Helpers ────────────────────────────────────────────────────────────────

def _headers() -> dict:
    h = {"Accept": "application/json"}
    if EMAIL:
        h["User-Agent"] = f"mailto:{EMAIL}"
    return h


def _get(url: str) -> dict | None:
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  HTTP {e.code}: {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        return None


def _normalise_doi(doi: str) -> str:
    """Strip URL prefix; return bare DOI lower-cased."""
    import re
    doi = doi.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.strip().rstrip(".,;:").lower()


def _collect_dois() -> dict[str, str]:
    """Return {normalised_doi: title} from all publication CSVs."""
    by_doi: dict[str, str] = {}

    candidates: list[Path] = []
    for name in _PUB_NAMES:
        candidates.append(ROOT / name)
    for p in (ROOT / "final output").glob("*/*_publications.csv"):
        candidates.append(p)

    for path in candidates:
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                doi   = _normalise_doi(row.get("doi", ""))
                title = (row.get("title") or "").strip()
                if doi and doi not in by_doi:
                    by_doi[doi] = title

    return by_doi


# ── API calls ──────────────────────────────────────────────────────────────

def fetch_work(doi: str) -> dict | None:
    """
    Fetch a single work by DOI from OpenAlex.

    Returns a flat dict with: openalex_id, title, publication_year,
    cited_by_count, fwci, citation_percentile, is_oa, oa_status.
    """
    encoded = urllib.parse.quote(doi, safe="")
    url     = f"{BASE_URL}/works/https://doi.org/{encoded}"
    data    = _get(url)
    if data is None:
        return None

    # Citation percentile lives inside counts_by_year or biblio_impact
    # OpenAlex stores it as a float 0-100 in `cited_by_percentile_year`
    percentile = ""
    cbpy = data.get("cited_by_percentile_year") or {}
    if isinstance(cbpy, dict):
        percentile = cbpy.get("max", "")

    # FWCI (Field-Weighted Citation Impact) — may not exist on all records
    fwci = data.get("fwci", "")

    oa_info = data.get("open_access") or {}

    return {
        "openalex_id":        data.get("id", ""),
        "title":              (data.get("title") or "").strip(),
        "publication_year":   data.get("publication_year", ""),
        "cited_by_count":     data.get("cited_by_count", ""),
        "fwci":               fwci,
        "citation_percentile": percentile,
        "is_oa":              oa_info.get("is_oa", ""),
        "oa_status":          oa_info.get("oa_status", ""),
    }


def fetch_journal(issn: str) -> dict | None:
    """
    Fetch a journal/venue from OpenAlex by ISSN.

    Returns basic metadata: openalex_id, display_name, is_oa,
    works_count, cited_by_count, issn_l.
    """
    clean   = issn.strip().replace("-", "")
    # OpenAlex accepts hyphened or plain ISSN
    formatted = f"{clean[:4]}-{clean[4:]}" if len(clean) == 8 else clean
    url     = f"{BASE_URL}/sources?filter=issn:{formatted}&per_page=1"
    data    = _get(url)
    if data is None:
        return None

    results = (data.get("results") or [])
    if not results:
        return None

    src = results[0]
    return {
        "openalex_id":   src.get("id", ""),
        "display_name":  src.get("display_name", ""),
        "is_oa":         src.get("is_oa", ""),
        "works_count":   src.get("works_count", ""),
        "cited_by_count": src.get("cited_by_count", ""),
        "issn_l":        src.get("issn_l", ""),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    dry_run     = "--dry-run" in sys.argv
    single_doi  = None
    single_issn = None

    if "--doi" in sys.argv:
        idx = sys.argv.index("--doi")
        single_doi = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    if "--issn" in sys.argv:
        idx = sys.argv.index("--issn")
        single_issn = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    if not EMAIL:
        print(
            "Tip: set OPENALEX_EMAIL=you@example.com in your .env for "
            "the polite pool (higher rate limits).",
            file=sys.stderr,
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Single lookups ──
    if single_doi:
        r = fetch_work(_normalise_doi(single_doi))
        if r:
            print(json.dumps(r, indent=2))
        else:
            print(f"No result for DOI: {single_doi}")
        return

    if single_issn:
        r = fetch_journal(single_issn)
        if r:
            print(json.dumps(r, indent=2))
        else:
            print(f"No result for ISSN: {single_issn}")
        return

    # ── Bulk run ──
    by_doi = _collect_dois()
    print(f"Collected {len(by_doi)} unique DOIs from publication CSVs")

    if dry_run:
        sample = list(by_doi.items())[:3]
        print(f"-- Dry run: fetching {len(sample)} DOIs --")
        for doi, title in sample:
            print(f"\n  DOI: {doi}  ({title[:60]})")
            r = fetch_work(doi)
            print(json.dumps(r, indent=4) if r else "  not found")
            time.sleep(DELAY_S)
        return

    results = []
    found = 0
    for i, (doi, title) in enumerate(by_doi.items(), 1):
        label = title[:55] if title else doi
        print(f"[{i}/{len(by_doi)}] {label}...", end=" ", flush=True)
        r = fetch_work(doi)
        if r:
            found += 1
            print(f"cited={r['cited_by_count']}  fwci={r['fwci'] or '—'}  pct={r['citation_percentile'] or '—'}")
        else:
            print("—")
        row = {"doi": doi}
        row.update(r or {f: "" for f in OUT_FIELDS if f != "doi"})
        results.append({f: row.get(f, "") for f in OUT_FIELDS})
        time.sleep(DELAY_S)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(results)

    print(f"\n✅  {found}/{len(by_doi)} works matched → {OUT_CSV}")


if __name__ == "__main__":
    main()
