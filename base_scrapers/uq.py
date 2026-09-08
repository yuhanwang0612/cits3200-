"""University of Queensland adapter.

Everything UQ-specific lives here: the staff directory markup, the eSpace
author-id link on each profile, and the eSpace search API.

Two things worth knowing about eSpace. It sits behind a CloudFront WAF that
returns 403 unless `origin` and `referer` name the eSpace front end — this
is not documented anywhere and was found by copying the browser's headers.
And UQ publishes each person's eSpace author id on their profile page, so
publication retrieval is keyed on an identifier rather than a name.
"""

import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.config import BROWSER_UA
from core.http import cached_get
from core.schema import blank_pub
from core.titles import level, rank, split_prefix

UNIVERSITY = "University of Queensland"
ROR = "00rqy9422"

ESPACE_BASE = "https://api.library.uq.edu.au/v1/records/search"
ESPACE_VIEW = "https://espace.library.uq.edu.au/view"

TARGETS = [
    ("https://business.uq.edu.au/team/finance-discipline", "Finance"),
    ("https://business.uq.edu.au/team/accounting-discipline", "Accounting"),
]

# The WAF checks origin/referer. Without these every request is a 403.
ESPACE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en-AU;q=0.9,en;q=0.8",
    "origin": "https://espace.library.uq.edu.au",
    "referer": "https://espace.library.uq.edu.au/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
}


def _search_url(author_id, page=1, per_page=100):
    return (f"{ESPACE_BASE}?export_to=&page={page}&per_page={per_page}"
            f"&sort=published_date&order_by=desc&mode=advanced"
            f"&key%5Brek_author_id%5D={author_id}")


# --- 1. staff -------------------------------------------------------------

def scrape_staff(verbose=True):
    records = []
    for url, discipline in TARGETS:
        resp = requests.get(url, headers=BROWSER_UA, timeout=15)
        resp.raise_for_status()
        cards = BeautifulSoup(resp.text, "html.parser").select(".person--teaser")
        if verbose:
            print(f"  {discipline}: {len(cards)} cards")

        for card in cards:
            link = card.select_one(".person__display-name a")
            if not link:
                continue
            name = link.get_text(strip=True)
            clean, prefix = split_prefix(name)

            titles = [t.get_text(strip=True) for t in card.select(".position__title")]
            titles = [t for t in titles if t]
            # Affiliate roles are secondary appointments, not the substantive one.
            substantive = [t for t in titles if not t.startswith("Affiliate")]
            title = (substantive or titles or [None])[0]

            records.append({
                "university": UNIVERSITY,
                "discipline": discipline,
                "name": name,
                "name_clean": clean,
                "prefix": prefix,
                "title": title,
                "title_clean": rank(title, prefix),
                "level_code": level(rank(title, prefix)),
                "profile_url": urljoin(url, link["href"]),
            })
    if verbose:
        print(f"  {len(records)} staff")
    return records


# --- 2. eSpace author ids -------------------------------------------------

def add_source_ids(records, verbose=True):
    """The 'Search X's works on UQ eSpace' link carries the author id."""
    for p in records:
        if p.get("source_id"):
            continue
        try:
            resp = requests.get(p["profile_url"], headers=BROWSER_UA, timeout=20)
        except requests.RequestException as e:
            print(f"  {p['name_clean']}: {e}")
            p["source_id"] = None
            continue

        link = BeautifulSoup(resp.text, "html.parser").select_one('a[href*="author_id"]')
        p["source_id"] = link["href"].rstrip("/").split("/")[-1] if link else None
        if verbose and not p["source_id"]:
            print(f"  {p['name_clean']}: HTTP {resp.status_code}, no eSpace link")
        time.sleep(1)

    if verbose:
        n = sum(1 for p in records if p.get("source_id"))
        print(f"  {n} of {len(records)} have an eSpace author id")
    return records


# --- 3. ORCIDs ------------------------------------------------------------

def add_orcids(records, verbose=True):
    """eSpace stores an ORCID on its own author records, so one search per
    person gets it without touching ORCID's API."""
    for p in records:
        if not p.get("source_id"):
            p["orcid"] = None
            continue
        try:
            d = cached_get(_search_url(p["source_id"], per_page=5),
                           headers=ESPACE_HEADERS, sleep=0.5)
        except Exception as e:
            print(f"  {p['name_clean']}: {type(e).__name__} {e}")
            p["orcid"] = None
            continue

        found = None
        for rec in d.get("data", []):
            for a in (rec.get("fez_record_search_key_author_id") or []):
                au = a.get("author") or {}
                if (str(a.get("rek_author_id")) == str(p["source_id"])
                        and au.get("aut_orcid_id")):
                    found = au["aut_orcid_id"]
                    break
            if found:
                break
        p["orcid"] = found

    if verbose:
        n = sum(1 for p in records if p.get("orcid"))
        print(f"  {n} of {len(records)} have an ORCID")
    return records


# --- 4. publications ------------------------------------------------------

def _parse(rec, person):
    jn = rec.get("fez_record_search_key_journal_name")

    mj = rec.get("fez_matched_journals") or []
    mj = mj[0] if isinstance(mj, list) and mj else (mj if isinstance(mj, dict) else None)
    fj = (mj or {}).get("fez_journal") or {}

    auths = rec.get("fez_record_search_key_author") or []
    doi = rec.get("fez_record_search_key_doi")

    return blank_pub(
        name=person["name_clean"],
        source_id=person["source_id"],
        title=rec.get("rek_title"),
        year=rec["rek_date"][:4] if rec.get("rek_date") else None,
        type=rec.get("rek_genre"),           # already the canonical vocabulary
        n_authors=len(auths) or None,
        authors="; ".join(a.get("rek_author", "") for a in
                          sorted(auths, key=lambda a: a.get("rek_author_order", 0))),
        issns=[s.get("rek_issn") for s in (rec.get("fez_record_search_key_issn") or [])
               if s.get("rek_issn")],
        journal=jn["rek_journal_name"] if jn else None,
        journal_canonical=fj.get("jnl_title"),
        publisher=fj.get("jnl_publisher"),
        doi=doi.get("rek_doi") if isinstance(doi, dict) else None,
        link=f"{ESPACE_VIEW}/{rec['rek_pid']}",
        source="UQ eSpace",
    )


def fetch_publications(records, verbose=True):
    pubs = []
    for i, p in enumerate(records, 1):
        if not p.get("source_id"):
            if verbose:
                print(f"  {i} {p['name_clean']}: no eSpace id, skipped")
            continue

        page, fetched, total = 1, 0, None
        while True:
            try:
                d = cached_get(_search_url(p["source_id"], page),
                               headers=ESPACE_HEADERS, timeout=30, sleep=1.0)
            except Exception as e:
                print(f"  {p['name_clean']}: page {page} failed — {e}")
                break

            total = d.get("total", 0)
            for rec in d.get("data", []):
                pubs.append(_parse(rec, p))
            fetched += len(d.get("data", []))

            if fetched >= total or not d.get("data"):
                break
            page += 1

        if verbose:
            flag = "" if total is not None and fetched == total else "  <-- MISMATCH"
            print(f"  {i} {p['name_clean']}: {total} total, {fetched} fetched{flag}")

    if verbose:
        print(f"  {len(pubs)} records from {len({x['name'] for x in pubs})} people")
    return pubs


# --- entry point ----------------------------------------------------------

def collect(verbose=True):
    """Return (records, pubs) satisfying the core.schema contract."""
    records = scrape_staff(verbose)
    add_source_ids(records, verbose)
    add_orcids(records, verbose)
    pubs = fetch_publications(records, verbose)
    return records, pubs
