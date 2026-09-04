"""Shared configuration. Paths are anchored to the repo root, not the
working directory, so scripts run correctly from anywhere."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent

load_dotenv(ROOT / ".env")

# --- paths ----------------------------------------------------------------

DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / "cache" / "http"

# Header row and rating column both differ between JQL editions:
# 2025 is row 7, 2022 and 2019 are row 8, and 2019 capitalises "Rating".
ABDC_FILE = DATA_DIR / "ABDC-JQL-2025-v1-260326.xlsx"
ABDC_SHEET = "2025 JQL"
ABDC_HEADER = 7
ABDC_RATING_COL = "2025 rating"
ABDC_EDITION = "2025"

SCIMAGO_FILE = DATA_DIR / "scimagojr 2025.csv"
SCIMAGO_YEAR = "2025"

# --- endpoints ------------------------------------------------------------

OPENALEX_BASE = "https://api.openalex.org/works"
CROSSREF_BASE = "https://api.crossref.org/works"
ORCID_BASE = "https://pub.orcid.org/v3.0"
JCR_BASE = "https://api.clarivate.com/apis/wos-journals/v1"

JCR_YEAR = 2025
JCR_SLEEP = 0.3                 # Clarivate allows 5 req/sec; 2 calls per journal

# --- headers --------------------------------------------------------------

CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "24314165@student.uwa.edu.au")

BROWSER_UA = {"User-Agent": "Mozilla/5.0"}

_POLITE = {"User-Agent": f"UQ-CITS3200 (mailto:{CONTACT_EMAIL})"}
OA_HEADERS = dict(_POLITE)
CR_HEADERS = dict(_POLITE)
ORCID_HEADERS = {"Accept": "application/json"}


def jcr_headers():
    """Clarivate key, read at call time so importing this module never fails."""
    key = os.environ.get("CLARIVATE_API_KEY")
    if not key:
        raise RuntimeError(
            "CLARIVATE_API_KEY is not set — add it to .env at the repo root")
    return {"X-ApiKey": key}
