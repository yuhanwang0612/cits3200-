"""Adelaide University adapter.

Scrapes researchers.adelaide.edu.au for Accounting & Finance staff.

Phase 1: paginate the staff directory to collect usernames
Phase 2: visit each profile, filter for A&F, extract name / title / ORCID

Publications are not fetched here — run.py's info/openalex.py step
retrieves them using the ORCIDs this adapter provides.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from core.titles import rank, split_prefix

UNIVERSITY = "Adelaide University"
ROR = "00892tw58"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_ACCTFIN_RE = re.compile(r"\b(accounting|finance|financial)\b", re.I)
_SCHOOL_NAMES = [
    "school of accounting", "school of finance",
    "accounting and finance", "finance and accounting",
    "department of accounting", "department of finance",
]


def _is_accounting_finance(soup):
    for tag in soup.find_all(
        ["div", "span", "p", "li", "h2", "h3", "a"],
        class_=re.compile(r"affili|school|department|faculty|unit|position|role|org", re.I),
    ):
        if _ACCTFIN_RE.search(tag.get_text(" ", strip=True)):
            return True
    for tag in soup.find_all(
        ["div", "span", "section"],
        id=re.compile(r"affili|school|department|faculty|unit|position", re.I),
    ):
        if _ACCTFIN_RE.search(tag.get_text(" ", strip=True)):
            return True
    page_text = soup.get_text(" ", strip=True).lower()
    if any(name in page_text for name in _SCHOOL_NAMES):
        return True
    if re.search(r"\bschool\b.{0,80}\b(accounting|finance)\b", page_text, re.S):
        return True
    return False


def _discipline(soup):
    text = soup.get_text(" ", strip=True).lower()
    n_acc = len(re.findall(r"\baccounting\b", text))
    n_fin = len(re.findall(r"\bfinance\b|\bfinancial\b", text))
    return "Finance" if n_fin > n_acc else "Accounting"


def scrape_staff(verbose=True):
    if verbose:
        print("  Phase 1: scanning researchers.adelaide.edu.au ...")
    usernames = []
    page, consecutive_empty = 1, 0
    while page <= 300:
        url = f"https://researchers.adelaide.edu.au/?page={page}"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all("a", href=re.compile(r"^/profile/"))
            if not links:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                page += 1
                continue
            consecutive_empty = 0
            new = 0
            for link in links:
                username = link["href"].replace("/profile/", "").strip("/")
                if username and username not in usernames:
                    usernames.append(username)
                    new += 1
            if verbose:
                print(f"    page {page}: {len(links)} links, {new} new -> {len(usernames)} total")
            page += 1
            time.sleep(0.5)
        except Exception as exc:
            if verbose:
                print(f"    error page {page}: {exc}")
            time.sleep(5)
            page += 1

    if verbose:
        print(f"  {len(usernames)} candidates - Phase 2: filtering for A&F ...")

    records = []
    for username in usernames:
        rurl = f"https://researchers.adelaide.edu.au/profile/{username}"
        try:
            resp = requests.get(rurl, headers=_HEADERS, timeout=15)
            if resp.status_code != 200 or len(resp.text) < 500:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            if not _is_accounting_finance(soup):
                continue

            h1 = soup.find("h1")
            name_raw = h1.get_text(strip=True) if h1 else ""
            name_clean, prefix = split_prefix(name_raw)
            if not name_clean or len(name_clean) < 3:
                continue

            title_raw = None
            for tag in soup.find_all(["p", "h2", "h3", "div", "span"], limit=40):
                text = tag.get_text(strip=True)
                if any(w in text.lower() for w in ["professor", "lecturer", "researcher", "fellow", "associate"]):
                    if 3 < len(text) < 120:
                        title_raw = text
                        break

            orcid = None
            for a in soup.find_all("a", href=True):
                m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
                if m:
                    orcid = m.group(1)
                    break

            if not orcid:
                try:
                    pr = requests.get(
                        f"https://adelaide.edu.au/people/{username}",
                        headers=_HEADERS, timeout=10,
                    )
                    if pr.status_code == 200 and len(pr.text) > 500:
                        psoup = BeautifulSoup(pr.text, "html.parser")
                        for a in psoup.find_all("a", href=True):
                            m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
                            if m:
                                orcid = m.group(1)
                                break
                except Exception:
                    pass

            records.append({
                "university": UNIVERSITY,
                "discipline": _discipline(soup),
                "name": name_raw,
                "name_clean": name_clean,
                "prefix": prefix,
                "title": title_raw,
                "title_clean": rank(title_raw, prefix),
                "profile_url": f"https://adelaide.edu.au/people/{username}",
                "orcid": orcid,
            })
            if verbose:
                tag = f"[orcid={orcid}]" if orcid else "[no orcid]"
                print(f"  + {name_clean:40s}  {tag}")
            time.sleep(0.5)

        except Exception as exc:
            if verbose:
                print(f"  error {username}: {exc}")
            time.sleep(2)

    if verbose:
        print(f"  {len(records)} A&F staff")
    return records


def collect(verbose=True):
    """Return (records, pubs) satisfying the core.schema contract."""
    records = scrape_staff(verbose)
    return records, []
