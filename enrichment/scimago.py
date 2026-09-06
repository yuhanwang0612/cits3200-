"""
enrichment/scimago.py
=====================
Enrich a journal list with Scimago Journal Rank (SJR) data.

Scimago publishes a free, openly downloadable CSV at:
    https://www.scimagojr.com/journalrank.php  (export button)

The file is semicolon-delimited. Place the downloaded file at:
    data/scimago.csv

Outputs
-------
Writes  data/scimago_lookup.csv  with columns:
    issn, title, sjr, sjr_quartile, h_index, cites_per_doc_2y, scimago_year

Usage
-----
    python3 enrichment/scimago.py                   # build lookup table
    python3 enrichment/scimago.py --query "Nature"  # quick title search
"""

import csv
import re
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent
DATA_DIR    = ROOT / "data"
SCIMAGO_CSV = DATA_DIR / "scimago.csv"
OUT_CSV     = DATA_DIR / "scimago_lookup.csv"

OUT_FIELDS = ["issn", "title", "sjr", "sjr_quartile",
              "h_index", "cites_per_doc_2y", "scimago_year"]


# ── Helpers ────────────────────────────────────────────────────────────────

def _normalise_issn(raw: str) -> list[str]:
    """Return a list of clean 8-char ISSNs from a raw cell (may hold two)."""
    # Scimago stores ISSNs like "15426890, 00349747" — split on comma/space
    parts = re.split(r"[,\s]+", raw.strip())
    out = []
    for p in parts:
        p = p.strip().replace("-", "")
        if len(p) == 8 and p[:7].isdigit():
            out.append(p)
    return out


def _parse_float(val: str) -> str:
    """Convert European decimal comma to dot, return empty string on failure."""
    try:
        return str(float(val.replace(",", ".")))
    except (ValueError, AttributeError):
        return ""


def load_scimago(path: Path = SCIMAGO_CSV) -> tuple[dict, dict]:
    """
    Parse scimago.csv.

    Returns
    -------
    by_issn  : {issn_str: row_dict}
    by_title : {lower_title: row_dict}
    """
    if not path.exists():
        sys.exit(
            f"ERROR: {path} not found.\n"
            "Download the Scimago journal list from "
            "https://www.scimagojr.com/journalrank.php and save it there."
        )

    by_issn: dict  = {}
    by_title: dict = {}

    # Scimago CSV uses semicolons and sometimes surrounds title with quotes
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            title = row.get("Title", "").strip().strip('"')
            if not title:
                continue

            # Detect the coverage year from the "Coverage" column if present
            coverage = row.get("Coverage", "")
            year_match = re.search(r"(\d{4})\s*$", coverage)
            scimago_year = year_match.group(1) if year_match else ""

            record = {
                "title":           title,
                "sjr":             _parse_float(row.get("SJR", "")),
                "sjr_quartile":    row.get("SJR Best Quartile", "").strip(),
                "h_index":         row.get("H index", "").strip(),
                "cites_per_doc_2y": _parse_float(
                                        row.get("Citations / Doc. (2years)", "")
                                    ),
                "scimago_year":    scimago_year,
            }

            # Index by every ISSN in the cell
            for issn in _normalise_issn(row.get("Issn", "")):
                by_issn.setdefault(issn, record)

            by_title[title.lower()] = record

    return by_issn, by_title


def lookup(issn: str = "", title: str = "",
           by_issn: dict | None = None,
           by_title: dict | None = None) -> dict:
    """
    Look up a single journal by ISSN (preferred) or title.

    Parameters
    ----------
    issn      : raw ISSN string (hyphens optional)
    title     : journal title (case-insensitive, exact match)
    by_issn   : pre-loaded index — pass both to avoid re-parsing the CSV
    by_title  : pre-loaded index

    Returns
    -------
    dict with keys: issn, title, sjr, sjr_quartile, h_index,
                    cites_per_doc_2y, scimago_year
    Empty strings where data is missing.
    """
    if by_issn is None or by_title is None:
        by_issn, by_title = load_scimago()

    # ISSN lookup (strip hyphens and leading zeros)
    clean = issn.strip().replace("-", "")
    if clean and clean in by_issn:
        rec = by_issn[clean]
        return {**{"issn": clean}, **rec}

    # Title fallback
    if title:
        rec = by_title.get(title.strip().lower())
        if rec:
            return {**{"issn": issn}, **rec}

    return {f: "" for f in OUT_FIELDS}


# ── CLI ────────────────────────────────────────────────────────────────────

def build_lookup_table():
    """Flatten the scimago CSV into a canonical ISSN-keyed lookup CSV."""
    by_issn, _ = load_scimago()

    rows = []
    for issn, rec in by_issn.items():
        rows.append({
            "issn":            issn,
            "title":           rec["title"],
            "sjr":             rec["sjr"],
            "sjr_quartile":    rec["sjr_quartile"],
            "h_index":         rec["h_index"],
            "cites_per_doc_2y": rec["cites_per_doc_2y"],
            "scimago_year":    rec["scimago_year"],
        })

    rows.sort(key=lambda r: r["title"].lower())
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"✅  {len(rows)} ISSN entries written → {OUT_CSV}")


def main():
    if "--query" in sys.argv:
        idx = sys.argv.index("--query")
        q   = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        by_issn, by_title = load_scimago()
        results = {
            t: r for t, r in by_title.items() if q.lower() in t
        }
        if not results:
            print(f"No journals found matching '{q}'")
        for title, rec in list(results.items())[:20]:
            print(f"  {rec['title']:60s}  SJR={rec['sjr']:>8}  {rec['sjr_quartile']}")
        return

    build_lookup_table()


if __name__ == "__main__":
    main()
