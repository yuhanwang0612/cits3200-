"""Reviewed staff additions: academics a university's roster page misses.

data/staff_additions.csv lists them by profile URL, with the evidence for each,
so an adapter can scrape them exactly like a rostered member and the decision
survives every re-run. Columns: university, profile_url, field_of_research,
reason, evidence_url. `university` is the output-folder key ("usyd").
"""

import csv
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "data" / "staff_additions.csv"


def staff_additions(university):
    """Rows of data/staff_additions.csv for one university key."""
    if not _PATH.exists():
        return []
    with _PATH.open(encoding="utf-8") as f:
        return [row for row in csv.DictReader(f)
                if (row.get("university") or "").strip().lower() == university
                and (row.get("profile_url") or "").strip()]
