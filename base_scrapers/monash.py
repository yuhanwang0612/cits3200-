"""Monash University adapter.

Phase 1: Selenium scrapes JS-rendered staff directories (Banking & Finance + Accounting).
Phase 2: parallel requests visit each research.monash.edu profile for ORCID + pub_count.
Phase 3: parallel OpenAlex fetches for publications, with pub_count sanity check.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from core.titles import rank, split_prefix

UNIVERSITY = "Monash University"
ROR = "02bfwt286"

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_OA_HEADERS = {"User-Agent": "monash-scraper/1.0 (mailto:wyuhan577@gmail.com)"}

TARGETS = [
    ("https://www.monash.edu/business/banking-and-finance/our-people/staff-directory", "Finance"),
    ("https://www.monash.edu/business/accounting/our-people/staff-directory", "Accounting"),
]

_NAV_WORDS = {
    "staff directory", "visiting scholars", "graduate research", "reset",
    "editorial roles", "distinguished visitor", "work with us", "seminar guests",
    "supervisors", "program", "view all", "explore network", "research outputs",
    "activities", "projects", "prizes",
}

_POSITION_RE = re.compile(
    r"(Emeritus Professor|Adjunct Professor|Associate Professor|Assistant Professor"
    r"|Distinguished Professor|Professor|Associate Lecturer|Senior Lecturer|Lecturer"
    r"|Senior Research Fellow|Principal Research Fellow|Research Fellow|Teaching Associate"
    r"|Adjunct Research Fellow|Head of [A-Za-z& ]+?(?=\s{2,}|$)|Deputy Head of [A-Za-z& ]+?(?=\s{2,}|$))",
    re.IGNORECASE,
)

_PROFILE_KEYS = ("/profile/", "/people/", "/persons/", "/staff/", "/our-people/")


# ── helpers ───────────────────────────────────────────────────────────


def _name_to_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _is_real_person(name):
    n = name.lower()
    if any(w in n for w in _NAV_WORDS):
        return False
    if len(name.split()) < 2:
        return False
    if re.match(r"^[A-Z][a-z]+,\s+[A-Z]\.?\s*[A-Z]?\.?$", name):
        return False
    if re.search(r"\d", name):
        return False
    return True


def _make_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )


def _find_research_url(profile_url, name):
    if profile_url and "monash.edu" in profile_url:
        try:
            resp = requests.get(profile_url, headers=_HEADERS, timeout=10)
            if resp.status_code == 200 and resp.text:
                soup = BeautifulSoup(resp.text, "html.parser")
                last_name = _name_to_slug(name.split()[-1])
                first_name = _name_to_slug(name.split()[0])
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if not href.startswith("https://research.monash.edu/en/persons/"):
                        continue
                    slug_part = href.split("/en/persons/")[-1].rstrip("/")
                    if last_name in slug_part or first_name in slug_part:
                        return href.rstrip("/") + "/"
        except Exception:
            pass
    return f"https://research.monash.edu/en/persons/{_name_to_slug(name)}/"


def _fetch_research_profile(research_url):
    """Return (title_raw, pub_count, orcid) from a research.monash.edu Pure profile."""
    _RANK_SELECTORS = [
        ".person-details-info", ".person-position", ".person-details__position",
        "[class*='person'][class*='position']", "[class*='job-title']",
        "[class*='title']", ".rendering_person_short .type", "span.type",
    ]
    for attempt in range(3):
        try:
            resp = requests.get(research_url, headers=_HEADERS, timeout=15)
            if resp.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            if resp.status_code != 200 or not resp.text:
                return None, 0, None
            soup = BeautifulSoup(resp.text, "html.parser")

            title_raw = None
            for sel in _RANK_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(strip=True)
                    if text:
                        title_raw = text
                        break

            pub_count = 0
            for a in soup.find_all("a", href=True):
                if "/publications/" in a["href"]:
                    m = re.search(r"(\d+)", a.get_text())
                    if m:
                        n = int(m.group(1))
                        if 1 <= n <= 999:
                            pub_count = max(pub_count, n)

            orcid = None
            for a in soup.find_all("a", href=True):
                m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
                if m:
                    orcid = m.group(1)
                    break

            return title_raw, pub_count, orcid
        except Exception:
            time.sleep(2)
    return None, 0, None


def _process_phase2(record):
    """Phase 2 worker: look up research profile, fill ORCID + pub_count. Mutates record."""
    rurl = _find_research_url(record["profile_url"], record["name_clean"])
    title_from_profile, pub_count, orcid = _fetch_research_profile(rurl)
    record["orcid"] = orcid
    record["_pub_count"] = pub_count
    if title_from_profile and not record["title"]:
        record["title"] = title_from_profile
        record["title_clean"] = rank(title_from_profile, record["prefix"])
    return record


# ── OpenAlex ──────────────────────────────────────────────────────────


def _oa_get(url, params):
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=_OA_HEADERS, timeout=20)
            if resp.status_code == 429:
                time.sleep(15)
                continue
            return resp
        except Exception:
            time.sleep(5)
    return None


def _fetch_pubs_openalex(name, orcid=None, profile_pub_count=0):
    """
    Fetch all publications for one researcher from OpenAlex.
    Uses ORCID when available; falls back to name + Monash affiliation check.
    """
    author_id = None

    if orcid:
        resp = _oa_get("https://api.openalex.org/authors",
                       {"filter": f"orcid:{orcid}", "per-page": 1})
        if resp and resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                author_id = results[0]["id"]
        time.sleep(0.2)
        if not author_id:
            return []
    else:
        resp = _oa_get("https://api.openalex.org/authors",
                       {"search": name, "per-page": 10})
        if not resp or resp.status_code != 200:
            return []
        results = resp.json().get("results", [])
        match = next(
            (r for r in results
             if any("monash" in (i.get("display_name") or "").lower()
                    for i in (r.get("last_known_institutions") or []))),
            None,
        )
        if not match:
            return []
        author_id = match["id"]
        time.sleep(0.2)

    pubs = []
    cursor = "*"
    while True:
        resp = _oa_get("https://api.openalex.org/works", {
            "filter": f"authorships.author.id:{author_id}",
            "per-page": 200,
            "cursor": cursor,
            "select": "title,publication_year,doi,type,primary_location,authorships",
        })
        if not resp or resp.status_code != 200:
            break
        data = resp.json()
        works = data.get("results", [])
        if not works:
            break
        for w in works:
            title = (w.get("title") or "").strip()
            if not title or len(title) < 5:
                continue
            year = w.get("publication_year")
            doi = (w.get("doi") or "").replace("https://doi.org/", "").strip().rstrip(".,;:").lower()
            pub_type = w.get("type") or "unknown"
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            journal_name = src.get("display_name")
            issn_list = src.get("issn") or []
            issn = issn_list[0] if issn_list else src.get("issn_l")
            authorships = w.get("authorships") or []
            authors = "; ".join(
                a.get("author", {}).get("display_name", "")
                for a in authorships
                if a.get("author", {}).get("display_name")
            )
            pubs.append({
                "name": name,
                "title": title,
                "year": year,
                "type": pub_type,
                "source": journal_name or "",
                "doi": doi or None,
                "issns": [issn] if issn else [],
                "journal": journal_name,
                "n_authors": len(authorships),
                "authors": authors,
                "link": f"https://doi.org/{doi}" if doi else None,
            })
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.2)

    if not orcid:
        if profile_pub_count == 0 and len(pubs) > 50:
            return []
        if profile_pub_count > 0 and len(pubs) > profile_pub_count * 5:
            return []

    return pubs


def _process_phase3(record):
    """Phase 3 worker: fetch pubs from OpenAlex. Returns (record, pubs)."""
    name = record["name_clean"]
    pub_count = record.get("_pub_count", 0) or 0
    if len(name.split()) < 2 or len(name) > 60:
        return record, []
    orcid = record.get("orcid")
    person_pubs = _fetch_pubs_openalex(name, orcid=orcid, profile_pub_count=pub_count)
    return record, person_pubs


# ── public API ────────────────────────────────────────────────────────


def scrape_staff(verbose=True):
    """Phase 1 (Selenium) + Phase 2 parallel profile fetches. Returns staff records."""
    if verbose:
        print("  Phase 1: loading Monash staff directories with Selenium ...")

    driver = _make_driver()
    records = []
    seen = set()

    try:
        for url, discipline in TARGETS:
            if verbose:
                print(f"    {discipline}: {url}")
            try:
                driver.get(url)
                time.sleep(15)
            except Exception as exc:
                if verbose:
                    print(f"    browser error ({exc}), restarting ...")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = _make_driver()
                driver.get(url)
                time.sleep(15)

            entries = []
            els = driver.find_elements(
                By.CSS_SELECTOR, "a[href*='research.monash.edu/en/persons/']"
            )
            for el in els:
                href = el.get_attribute("href") or ""
                text = el.text.strip()
                if not text:
                    text = (el.get_attribute("textContent") or "").strip()
                if not text or not _is_real_person(text):
                    continue
                profile_url = href.rstrip("/") + "/"
                if profile_url in seen:
                    continue
                seen.add(profile_url)
                entries.append((text, profile_url))

            if verbose:
                print(f"    {discipline}: {len(entries)} staff found")

            for name_raw, profile_url in entries:
                name_clean, prefix = split_prefix(name_raw)
                if not name_clean or len(name_clean) < 3:
                    continue
                records.append({
                    "university": UNIVERSITY,
                    "discipline": discipline,
                    "name": name_raw,
                    "name_clean": name_clean,
                    "prefix": prefix,
                    "title": None,
                    "title_clean": rank(None, prefix),
                    "profile_url": profile_url,
                    "orcid": None,
                    "_pub_count": 0,
                })
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if verbose:
        print(f"  Phase 1 done: {len(records)} staff. Phase 2: research profiles (5 parallel workers) ...")

    # ── Phase 2: parallel research profile fetches ────────────────────────
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_process_phase2, r): r for r in records}
        for future in as_completed(futures):
            r = future.result()
            if verbose:
                tag = f"[orcid={r['orcid']}]" if r["orcid"] else "[no orcid]"
                print(f"  + {r['name_clean']:40s}  pubs={r['_pub_count']}  {tag}")

    if verbose:
        print(f"  {len(records)} Monash A&F staff")
    return records


def collect(verbose=True):
    """Return (records, pubs) satisfying the core.schema contract."""
    records = scrape_staff(verbose)

    if verbose:
        print("  Phase 3: fetching publications via OpenAlex (3 parallel workers) ...")

    pubs = []
    # 3 workers — conservative to respect OpenAlex rate limits
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_process_phase3, r): r for r in records}
        for future in as_completed(futures):
            r, person_pubs = future.result()
            r.pop("_pub_count", None)
            pubs.extend(person_pubs)
            tag = "[ORCID]" if r.get("orcid") else "[name->Monash]"
            if verbose:
                print(f"    {r['name_clean']:40s}  {len(person_pubs)} pubs  {tag}")

    if verbose:
        print(f"  {len(pubs)} total publications")
    return records, pubs
