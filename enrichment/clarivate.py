"""
enrichment/clarivate.py
=======================
Enrich journals with Journal Impact Factor (JIF) from the
Clarivate Web of Science Journals API.

Requires a JCR_API_KEY set in your .env file (UWA subscription).

Reads ISSNs from all  final output/<uni>/<uni>_publications.csv  files,
queries the WoS Journals API for each, and writes:
    data/jcr.csv   — issn, title, impact_factor, jcr_year

Usage
-----
    # Confirm the API response looks right (1 call, free):
    python3 enrichment/clarivate.py --dry-run

    # Fetch all journals (~1000 calls, ~6 min at default rate):
    python3 enrichment/clarivate.py

    # Look up a single ISSN:
    python3 enrichment/clarivate.py --issn 00220515
"""

import csv
import json
import os
import sys
import time
import urllib.error
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
OUT_CSV  = DATA_DIR / "jcr.csv"

API_KEY  = os.environ.get("JCR_API_KEY", "")
BASE_URL = "https://api.clarivate.com/apis/wos-journal/v1"
DELAY_S  = 0.35   # ~3 req/s — well under the default quota

OUT_FIELDS = ["issn", "journal_name", "impact_factor", "jcr_year"]

# All university publication files produced by the scrapers
PUB_GLOB = "final output/*/{{uni}}_publications.csv"   # illustrative
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

def _collect_issns() -> dict[str, str]:
    """
    Return {issn: first_seen_journal_name} from all publication CSVs.
    Searches both the root folder (legacy) and final output/<uni>/ folders.
    """
    by_issn: dict[str, str] = {}

    candidates: list[Path] = []
    # Legacy flat layout
    for name in _PUB_NAMES:
        candidates.append(ROOT / name)
    # New nested layout
    for p in (ROOT / "final output").glob("*/*_publications.csv"):
        candidates.append(p)

    for path in candidates:
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                issn  = (row.get("issn") or "").strip().replace("-", "")
                jname = (row.get("journal_name") or "").strip()
                if issn and issn not in by_issn:
                    by_issn[issn] = jname

    return by_issn


def fetch_journal(issn: str) -> dict | None:
    """
    Query WoS Journals API for one ISSN.
    Returns {"impact_factor": float, "jcr_year": int} or None.
    """
    url = f"{BASE_URL}/journals?issn={issn}&limit=1"
    req = urllib.request.Request(
        url,
        headers={"X-ApiKey": API_KEY, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  HTTP {e.code} for ISSN {issn}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error for ISSN {issn}: {e}", file=sys.stderr)
        return None

    # Handle both API response shapes
    hits = (
        data.get("hits")
        or data.get("data")
        or (data.get("journal") and [data["journal"]])
        or []
    )
    if not hits:
        return None

    metrics = hits[0].get("metrics") or hits[0].get("journalMetrics") or []
    metrics = sorted(metrics, key=lambda m: m.get("year", 0), reverse=True)
    for m in metrics:
        jif = (
            m.get("impactFactor")
            or m.get("journalImpactFactor")
            or m.get("jif")
            or m.get("impact_factor")
        )
        if jif is not None:
            return {"impact_factor": float(jif), "jcr_year": m.get("year")}
    return None


def load_jcr(path: Path = OUT_CSV) -> dict[str, dict]:
    """Load an existing jcr.csv into {issn: {impact_factor, jcr_year}}."""
    if not path.exists():
        return {}
    result = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            issn = (row.get("issn") or "").strip()
            if issn:
                result[issn] = {
                    "impact_factor": row.get("impact_factor", ""),
                    "jcr_year":      row.get("jcr_year", ""),
                }
    return result


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    dry_run    = "--dry-run" in sys.argv
    single_issn = None
    if "--issn" in sys.argv:
        idx = sys.argv.index("--issn")
        single_issn = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    if not API_KEY:
        sys.exit(
            "Error: JCR_API_KEY not set.\n"
            "Add it to your .env file: JCR_API_KEY=your_key_here\n"
            "(Available via UWA library → Web of Science API access)"
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Single ISSN lookup
    if single_issn:
        r = fetch_journal(single_issn.replace("-", ""))
        if r:
            print(f"ISSN {single_issn}: JIF={r['impact_factor']} ({r['jcr_year']})")
        else:
            print(f"ISSN {single_issn}: no JIF found")
        return

    by_issn = _collect_issns()
    print(f"Collected {len(by_issn)} unique ISSNs from publication CSVs")

    # Dry run: just show the API response for the first ISSN
    if dry_run:
        issn = next(iter(by_issn))
        print(f"\n-- Dry run: fetching ISSN {issn} ({by_issn[issn]}) --")
        url = f"{BASE_URL}/journals?issn={issn}&limit=1"
        req = urllib.request.Request(
            url,
            headers={"X-ApiKey": API_KEY, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(json.dumps(json.loads(resp.read()), indent=2))
        return

    results = []
    found = 0
    for i, (issn, jname) in enumerate(by_issn.items(), 1):
        label = jname[:50] if jname else issn
        print(f"[{i}/{len(by_issn)}] {issn}  {label}...", end=" ", flush=True)
        r = fetch_journal(issn)
        if r:
            found += 1
            print(f"JIF {r['impact_factor']} ({r['jcr_year']})")
        else:
            print("—")
        results.append({
            "issn":          issn,
            "journal_name":  jname,
            "impact_factor": r["impact_factor"] if r else "",
            "jcr_year":      r["jcr_year"]      if r else "",
        })
        time.sleep(DELAY_S)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(results)

    print(f"\n✅  {found}/{len(by_issn)} journals matched → {OUT_CSV}")


if __name__ == "__main__":
    main()
