"""
enrichment/abdc.py
==================
Enrich a journal list with ABDC Journal Quality List rankings.

Source file: data/ABDC-JQL-2025.xlsx
(Download from https://abdc.edu.au/research/australian-business-deans-council-journal-quality-list/)

The workbook contains multiple sheets (2025, 2022, 2019, …).
This script uses the 2025 sheet by default.

Outputs
-------
Writes  data/abdc_lookup.csv  with columns:
    issn, issn_online, title, for_code, quality_rank, abdc_edition

Usage
-----
    python3 enrichment/abdc.py                      # build lookup table
    python3 enrichment/abdc.py --query "Nature"     # quick title search
    python3 enrichment/abdc.py --issn 0001-4273     # single ISSN lookup
    python3 enrichment/abdc.py --year 2022          # use a different edition
"""

import csv
import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl",
                           "-q", "--break-system-packages"])
    import openpyxl

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ABDC_XLS = DATA_DIR / "ABDC-JQL-2025.xlsx"
OUT_CSV  = DATA_DIR / "abdc_lookup.csv"

OUT_FIELDS = ["issn", "issn_online", "title", "for_code",
              "quality_rank", "abdc_edition"]

# Sheet names in the workbook keyed by year
SHEET_YEARS = {
    2025: "2025 JQL",
    2022: "2022 JQL",
    2019: "2019 JQL",
    2016: "2016 JQL",
    2013: "2013 JQL",
    2010: "2010 JQL",
}
DEFAULT_YEAR = 2025


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean(val) -> str:
    """Strip whitespace/tabs from a cell value; return empty string if None."""
    if val is None:
        return ""
    return str(val).strip().strip("\t").strip()


def _normalise_issn(raw: str) -> str:
    """Return a clean 8-char ISSN (no hyphen) or empty string."""
    clean = re.sub(r"[^0-9Xx]", "", raw)
    return clean if len(clean) == 8 else ""


def load_abdc(path: Path = ABDC_XLS,
              year: int = DEFAULT_YEAR) -> tuple[dict, dict]:
    """
    Parse the ABDC xlsx for a given edition year.

    Returns
    -------
    by_issn  : {issn_str: record_dict}   (both print and online ISSNs indexed)
    by_title : {lower_title: record_dict}
    """
    if not path.exists():
        sys.exit(
            f"ERROR: {path} not found.\n"
            "Download the ABDC Journal Quality List from:\n"
            "  https://abdc.edu.au/research/australian-business-deans-council-journal-quality-list/\n"
            "and save it as data/ABDC-JQL-2025.xlsx"
        )

    sheet_name = SHEET_YEARS.get(year)
    if not sheet_name:
        sys.exit(f"ERROR: year {year} not in workbook. Choose from {list(SHEET_YEARS)}")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        sys.exit(f"ERROR: sheet '{sheet_name}' not found in workbook.")
    ws = wb[sheet_name]

    # Find the header row (contains "Journal Title")
    header_row = None
    col_map: dict[str, int] = {}
    for row in ws.iter_rows(values_only=True):
        for i, val in enumerate(row):
            if _clean(val).lower() == "journal title":
                header_row = row
                break
        if header_row is not None:
            break

    if header_row is None:
        sys.exit("ERROR: Could not find header row in ABDC sheet.")

    for i, val in enumerate(header_row):
        col_map[_clean(val).lower()] = i

    # Column indices (flexible naming)
    idx_title  = col_map.get("journal title")
    idx_issn   = col_map.get("issn")
    idx_online = col_map.get("issnonline") or col_map.get("issn online") or col_map.get("issnonline")
    idx_for    = col_map.get("for")
    idx_rank   = next((col_map[k] for k in col_map if "rating" in k or "rank" in k), None)

    by_issn:  dict = {}
    by_title: dict = {}

    reading = False
    for row in ws.iter_rows(values_only=True):
        # Start reading after the header row
        title_val = _clean(row[idx_title]) if idx_title is not None and len(row) > idx_title else ""
        if not reading:
            if title_val.lower() == "journal title":
                reading = True
            continue

        if not title_val:
            continue

        issn_raw   = _clean(row[idx_issn])   if idx_issn   is not None and len(row) > idx_issn   else ""
        online_raw = _clean(row[idx_online]) if idx_online is not None and len(row) > idx_online else ""
        for_code   = _clean(row[idx_for])    if idx_for    is not None and len(row) > idx_for    else ""
        rank_raw   = _clean(row[idx_rank])   if idx_rank   is not None and len(row) > idx_rank   else ""

        issn   = _normalise_issn(issn_raw)
        online = _normalise_issn(online_raw)
        rank   = rank_raw.strip("* ").upper()   # normalise: "A* " -> "A*", "B " -> "B"
        # Re-add star if it was in the raw value
        if "*" in rank_raw:
            rank = rank.rstrip("*") + "*"

        record = {
            "title":         title_val,
            "issn":          issn,
            "issn_online":   online,
            "for_code":      for_code,
            "quality_rank":  rank,
            "abdc_edition":  str(year),
        }

        if issn:
            by_issn.setdefault(issn, record)
        if online:
            by_issn.setdefault(online, record)
        by_title[title_val.lower()] = record

    return by_issn, by_title


def lookup(issn: str = "", title: str = "",
           by_issn: dict | None = None,
           by_title: dict | None = None,
           year: int = DEFAULT_YEAR) -> dict:
    """
    Look up a single journal by ISSN (preferred) or title.

    Returns a dict with keys matching OUT_FIELDS; empty strings where missing.
    """
    if by_issn is None or by_title is None:
        by_issn, by_title = load_abdc(year=year)

    clean = _normalise_issn(issn)
    if clean and clean in by_issn:
        return by_issn[clean]

    if title:
        rec = by_title.get(title.strip().lower())
        if rec:
            return rec

    return {f: "" for f in OUT_FIELDS}


# ── CLI ────────────────────────────────────────────────────────────────────

def build_lookup_table(year: int = DEFAULT_YEAR):
    """Write a flat CSV of all ABDC journals for the given edition."""
    by_issn, by_title = load_abdc(year=year)

    # De-duplicate: one row per unique title
    seen = set()
    rows = []
    for rec in by_issn.values():
        key = rec["title"].lower()
        if key not in seen:
            seen.add(key)
            rows.append({f: rec.get(f, "") for f in OUT_FIELDS})

    rows.sort(key=lambda r: r["title"].lower())
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"✅  {len(rows)} journals written → {OUT_CSV}  (edition: {year})")


def main():
    year = DEFAULT_YEAR
    if "--year" in sys.argv:
        idx  = sys.argv.index("--year")
        year = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else DEFAULT_YEAR

    if "--issn" in sys.argv:
        idx  = sys.argv.index("--issn")
        issn = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        rec  = lookup(issn=issn, year=year)
        print(rec if rec["title"] else f"No result for ISSN {issn}")
        return

    if "--query" in sys.argv:
        idx = sys.argv.index("--query")
        q   = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        _, by_title = load_abdc(year=year)
        results = {t: r for t, r in by_title.items() if q.lower() in t}
        if not results:
            print(f"No journals found matching '{q}'")
        for _, rec in list(results.items())[:20]:
            print(f"  {rec['title']:65s}  {rec['quality_rank']:4s}  ISSN={rec['issn']}")
        return

    build_lookup_table(year=year)


if __name__ == "__main__":
    main()
