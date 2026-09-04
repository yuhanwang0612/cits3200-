"""
fetch_jcr.py — Fetch Journal Impact Factor from the Web of Science Journals API.

Reads unique ISSNs from all *_publications.csv files, queries the WoS
Journals API for each, and writes jcr.csv:
    issn, journal_name, impact_factor, jcr_year

load.py then joins this file onto anu_journals.csv by ISSN so every
journal record carries an impact_factor where Clarivate has one.

Usage:
    # First confirm the API response looks right (free, 1 call):
    python3 fetch_jcr.py --dry-run

    # Then fetch all journals (~1000 calls, ~6 min at default rate):
    python3 fetch_jcr.py

Set JCR_API_KEY in your .env file before running.
Rate limit: ~3 req/s by default; raise DELAY_S if you hit 429 errors.
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

API_KEY  = os.environ.get("JCR_API_KEY", "")
BASE_URL = "https://api.clarivate.com/apis/wos-journal/v1"
DELAY_S  = 0.35   # ~3 req/s — well under the default rate limit
OUT_FILE = Path("jcr.csv")

# All university publication files — add others as they appear
SOURCES = [
    "monash_publications.csv",
    "adelaide_publications.csv",
    "uq_publications.csv",
    "unimelb_publications.csv",
    "usyd_publications.csv",
    "uwa_publications.csv",
    "anu_publications.csv",
    "unsw_publications.csv",
]


def collect_issns(sources):
    """Return {issn: first_seen_journal_name} across all source CSVs."""
    by_issn = {}
    for fname in sources:
        if not Path(fname).exists():
            continue
        with open(fname, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                issn  = (row.get("issn") or "").strip()
                jname = (row.get("journal_name") or "").strip()
                if issn and issn not in by_issn:
                    by_issn[issn] = jname
    return by_issn


def fetch_journal(issn: str):
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

    # The API wraps results differently across versions — handle both shapes
    hits = (
        data.get("hits")
        or data.get("data")
        or (data.get("journal") and [data["journal"]])
        or []
    )
    if not hits:
        return None

    metrics = hits[0].get("metrics") or hits[0].get("journalMetrics") or []
    # Take the most recent year that has an impact factor
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


def main():
    dry_run = "--dry-run" in sys.argv

    if not API_KEY:
        sys.exit("Error: JCR_API_KEY not set — add it to your .env file.")

    by_issn = collect_issns(SOURCES)
    print(f"Collected {len(by_issn)} unique ISSNs from publication CSVs")

    if dry_run:
        issn = next(iter(by_issn))
        jname = by_issn[issn]
        print(f"\n-- Dry run: fetching ISSN {issn} ({jname}) --")
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
        label = jname[:45] if jname else issn
        print(f"[{i}/{len(by_issn)}] {issn}  {label}...", end=" ", flush=True)
        r = fetch_journal(issn)
        if r:
            found += 1
            print(f"JIF {r['impact_factor']} ({r['jcr_year']})")
        else:
            print("—")
        results.append({
            "issn":           issn,
            "journal_name":   jname,
            "impact_factor":  r["impact_factor"] if r else "",
            "jcr_year":       r["jcr_year"]      if r else "",
        })
        time.sleep(DELAY_S)

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["issn", "journal_name", "impact_factor", "jcr_year"])
        w.writeheader()
        w.writerows(results)

    print(f"\n✅  {found}/{len(by_issn)} journals matched → {OUT_FILE}")


if __name__ == "__main__":
    main()
