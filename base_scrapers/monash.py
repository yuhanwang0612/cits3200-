"""Monash University adapter.

Phase 1: Selenium scrapes JS-rendered staff directories (Banking & Finance + Accounting).
Phase 2: parallel requests visit each research.monash.edu profile for ORCID + pub_count.
Phase 3: use each researcher's official Monash Pure RSS list for attribution,
then attach OpenAlex metadata only when the publication title matches.

Manual identity corrections in data/monash_identity_overrides.csv are applied
after Phase 2, so a verified ORCID replaces whatever the profile page offered.
"""

import csv
import html as html_module
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from core.config import OA_HEADERS
from core.titles import rank, split_prefix

UNIVERSITY = "Monash University"
ROR = "02bfwt286"
IDENTITY_OVERRIDES_FILE = Path(__file__).resolve().parents[1] / "data" / "monash_identity_overrides.csv"

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
# The shared headers carry OPENALEX_API_KEY from .env when set. Without it these
# calls draw on OpenAlex's small keyless daily budget, and once that is spent
# every lookup is refused and the person silently gets no OpenAlex metadata.
_OA_HEADERS = OA_HEADERS

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


def _clean_text(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", html_module.unescape(str(value))).strip()


def _title_key(value):
    """Stable comparison key for matching a Pure item to OpenAlex metadata."""
    return re.sub(r"[^a-z0-9]+", " ", _clean_text(value).casefold()).strip()


def _academic_title(value):
    """Return only an academic position, never a whole profile section.

    Pure's generic ``title`` selectors can point at blocks headed "External
    positions".  Those blocks used to become job titles in the staff CSV.
    """
    text = _clean_text(value)
    if "external positions" in text.casefold():
        return None
    match = _POSITION_RE.search(text)
    return _clean_text(match.group(0)) if match else None


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
                name_parts = name.split()
                expected_slug = _name_to_slug(name)
                last_name = _name_to_slug(name_parts[-1])
                first_name = _name_to_slug(name_parts[0])
                candidates = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if not href.startswith("https://research.monash.edu/en/persons/"):
                        continue
                    slug_part = href.split("/en/persons/")[-1].rstrip("/")
                    candidates.append((slug_part, href.rstrip("/") + "/"))
                # Never accept a namesake merely because the surname occurs
                # in its URL (Kym Brown previously resolved to Christine
                # Brown). Prefer the exact full-name slug, then require both
                # first and last name as the conservative fallback.
                for slug_part, href in candidates:
                    if slug_part == expected_slug:
                        return href
                for slug_part, href in candidates:
                    if first_name in slug_part and last_name in slug_part:
                        return href
        except Exception:
            pass
    return f"https://research.monash.edu/en/persons/{_name_to_slug(name)}/"


def _fetch_research_profile(research_url):
    """Return (title_raw, pub_count, orcid) from a research.monash.edu Pure profile."""
    _RANK_SELECTORS = [
        # Pure exposes the actual appointment in ``.job-title``.  Broad
        # containers such as ``.person-details-info`` can also contain other
        # people's positions and previously turned Kym Brown into an
        # "Emeritus Professor".  Exact selectors must therefore come first.
        ".job-title", "[class~='job-title']", ".person-position",
        ".person-details__position", "[class*='person'][class*='position']",
        "[class*='job-title']", ".person-details-info",
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
                    title_raw = _academic_title(el.get_text(" ", strip=True))
                    if title_raw:
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


def _request_text(url):
    """Fetch text with the same bounded retry policy as the JSON requests."""
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.text
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}")


def _pure_type(raw_type):
    text = _clean_text(raw_type).lower()
    if "contribution to journal" in text:
        # Pure's parent category also contains Short Review, Editorial,
        # Letter, Commentary, etc.  The project scope is journal *articles*,
        # so require the explicit Article subtype rather than accepting the
        # whole parent category.
        parts = [part.strip() for part in re.split(r"[›>]", text)]
        return "Journal Article" if "article" in parts[1:] else "Other"
    if "conference" in text:
        return "Conference Paper"
    if "chapter" in text:
        return "Book Chapter"
    if "book" in text:
        return "Book"
    if "working paper" in text:
        return "Working Paper"
    if "report" in text:
        return "Research Report"
    if "thesis" in text:
        return "Thesis"
    return "Other"


def _parse_pure_rss(xml_text, name, source_id=None):
    """Parse one Monash Pure RSS page into shared publication records.

    The personal RSS feed is the attribution evidence.  It is safer than
    treating every work on an OpenAlex author entity as belonging to the
    current Monash staff member, because OpenAlex can merge namesakes.
    """
    root = ElementTree.fromstring(xml_text)
    rows = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        values = {
            child.tag.rsplit("}", 1)[-1].lower(): child.text or ""
            for child in element
        }
        title = _clean_text(values.get("title"))
        link = _clean_text(values.get("link")) or None
        if not title:
            continue

        description = BeautifulSoup(values.get("description", ""), "html.parser")
        date_node = description.select_one(".date")
        date_text = _clean_text(date_node.get_text(" ", strip=True) if date_node else "")
        year_match = re.search(r"\b(?:18|19|20)\d{2}\b", date_text)

        journal_node = description.select_one(".journal")
        journal = _clean_text(
            journal_node.get_text(" ", strip=True) if journal_node else ""
        )
        journal = re.sub(r"^In:\s*", "", journal, flags=re.I).rstrip(". ") or None

        type_node = description.select_one(".type")
        raw_type = _clean_text(type_node.get_text(" ", strip=True) if type_node else "")

        # The short Pure rendering contains the complete byline as plain text,
        # including external co-authors.  Preserve it for auditability.  When
        # OpenAlex has the same title below, its structured author list wins.
        all_text = _clean_text(description.get_text(" ", strip=True))
        author_text = None
        if all_text.startswith(title) and date_text and date_text in all_text:
            author_text = all_text[len(title):all_text.index(date_text)].strip(" ,") or None
        author_count = None
        if author_text:
            initials = re.findall(
                r",\s*(?:[A-Z]\.\s*)+(?=,|\s*&|$)", author_text
            )
            author_count = len(initials) or None

        rows.append({
            "name": name,
            "source_id": source_id,
            "title": title,
            "year": year_match.group(0) if year_match else None,
            "type": _pure_type(raw_type),
            "n_authors": author_count,
            "authors": author_text,
            "issns": [],
            "journal": journal,
            "journal_canonical": None,
            "publisher": None,
            "doi": None,
            "link": link,
            "source": "Monash Pure",
            "raw_type": raw_type or None,
        })
    return rows


def _fetch_pure_publications(record):
    base = f"{record['profile_url'].rstrip('/')}/publications/"
    expected = record.get("_pub_count") or 0
    max_pages = math.ceil(expected / 50) + 2 if expected else 100
    rows_by_url = {}
    for page in range(max_pages):
        # Monash Pure accepts ``?format=rss`` for the first page, but its
        # security layer rejects ``?format=rss&page=1``.  Putting ``page``
        # first is both accepted and returns the next, non-overlapping page.
        url = f"{base}?format=rss" if page == 0 else f"{base}?page={page}&format=rss"
        batch = _parse_pure_rss(
            _request_text(url), record["name_clean"], record.get("source_id")
        )
        before = len(rows_by_url)
        for row in batch:
            rows_by_url[row.get("link") or _title_key(row.get("title"))] = row
        if not batch or len(rows_by_url) == before or (expected and len(rows_by_url) >= expected):
            break
    return list(rows_by_url.values())


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


# ── Identity overrides ────────────────────────────────────────────────


def _url_key(url):
    return (url or "").strip().rstrip("/").lower()


def load_identity_overrides(path=IDENTITY_OVERRIDES_FILE):
    """Read the manual corrections file. A missing file means no overrides."""
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [
            {
                "name": (row.get("name") or "").strip(),
                "profile_url": (row.get("profile_url") or "").strip(),
                "orcid": (row.get("orcid") or "").strip(),
                "publication_name": (row.get("publication_name") or "").strip(),
            }
            for row in csv.DictReader(f)
            if (row.get("name") or "").strip()
        ]


def apply_identity_overrides(records, overrides):
    """Apply verified corrections to scraped staff records. Returns how many matched.

    The profile page's first ORCID link is not always the person's own (Kym
    Brown's page yielded Christine Brown's), so a reviewed ORCID wins. A
    publication_name marks someone whose name search finds the wrong person;
    without a verified ORCID, Phase 3 skips them rather than guess.

    Matched on profile URL first, because it is stable, then on the clean name.
    """
    by_url = {_url_key(o["profile_url"]): o for o in overrides if o["profile_url"]}
    by_name = {o["name"]: o for o in overrides}
    applied = 0
    for record in records:
        override = by_url.get(_url_key(record.get("profile_url"))) or by_name.get(record.get("name_clean"))
        if not override:
            continue
        applied += 1
        if override["orcid"]:
            record["orcid"] = override["orcid"]
        if override["publication_name"]:
            record["_publication_name"] = override["publication_name"]
    return applied


# ── OpenAlex ──────────────────────────────────────────────────────────


def _orcid_key(value):
    """'https://orcid.org/0000-0002-1825-009x' and '0000-0002-1825-009X' compare equal."""
    return (value or "").strip().rstrip("/").rsplit("/", 1)[-1].upper()


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
    author_ids = []

    if orcid:
        resp = _oa_get("https://api.openalex.org/authors",
                       {"filter": f"orcid:{orcid}", "per-page": 25})
        if resp and resp.status_code == 200:
            # OpenAlex's orcid filter also returns records that carry a
            # different ORCID, and not last: for Chen Chen the first result was
            # a biomedical researcher's 3836-work record. Keep only records
            # whose own ORCID matches, and every one of them, since one person
            # can be split across several records (Li Ge has two).
            wanted = _orcid_key(orcid)
            author_ids = [
                r["id"].rsplit("/", 1)[-1]
                for r in resp.json().get("results", [])
                if _orcid_key(r.get("orcid")) == wanted
            ]
        time.sleep(0.2)
        if not author_ids:
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
        author_ids = [match["id"].rsplit("/", 1)[-1]]
        time.sleep(0.2)

    pubs = []
    cursor = "*"
    while True:
        resp = _oa_get("https://api.openalex.org/works", {
            "filter": f"authorships.author.id:{'|'.join(author_ids)}",
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
                # ``source`` is provenance, not the venue.  Storing the
                # journal here caused screen.py to treat every Monash row as
                # an official/listed record, so namesake OpenAlex rows were
                # never screened.
                "source": "OpenAlex",
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


def _merge_pure_with_openalex(pure_pubs, openalex_pubs):
    """Attach OpenAlex metadata only to works present in the Pure feed.

    Pure decides attribution; OpenAlex supplies identifiers and structured
    metadata.  Extra OpenAlex works are deliberately not admitted here.
    """
    candidates = {}
    for row in openalex_pubs:
        candidates.setdefault(_title_key(row.get("title")), []).append(row)

    merged = []
    for pure in pure_pubs:
        choices = candidates.get(_title_key(pure.get("title")), [])
        if choices:
            year = str(pure.get("year") or "")
            match = next(
                (row for row in choices if str(row.get("year") or "") == year),
                choices[0],
            )
            for field in ("doi", "issns", "n_authors", "authors", "publisher"):
                if match.get(field):
                    pure[field] = match[field]
            if not pure.get("journal") and match.get("journal"):
                pure["journal"] = match["journal"]
            pure["metadata_source"] = "OpenAlex exact-title match"
        merged.append(pure)
    return merged


def _openalex_pubs(record):
    """OpenAlex candidates used only to add DOIs/ISSNs to the person's Pure papers."""
    orcid = record.get("orcid")
    if not orcid and record.get("_publication_name"):
        # e.g. John Chu publishes as "Zhu, Z."; a name search finds someone
        # else, and a search costs ten times a filter call.
        return []
    return _fetch_pubs_openalex(
        record["name_clean"], orcid=orcid,
        profile_pub_count=record.get("_pub_count", 0) or 0,
    )


def _process_phase3(record):
    """Fetch the official Pure list, then attach matching OpenAlex metadata.

    Returns (record, pubs, error). A person whose Pure feed cannot be read gets
    no publications this run and `error` says why, rather than the exception
    escaping collect() and aborting every other Monash researcher.
    """
    name = record["name_clean"]
    if len(name.split()) < 2 or len(name) > 60:
        return record, [], None
    try:
        pure_pubs = _fetch_pure_publications(record)
    except Exception as error:
        return record, [], f"{type(error).__name__}: {error}"
    return record, _merge_pure_with_openalex(pure_pubs, _openalex_pubs(record)), None


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
                    "source_id": profile_url.rstrip("/").split("/")[-1],
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

    overrides = load_identity_overrides()
    applied = apply_identity_overrides(records, overrides)
    if verbose:
        print(f"  applied {applied} of {len(overrides)} identity overrides "
              f"from {IDENTITY_OVERRIDES_FILE.name}")
        print(f"  {len(records)} Monash A&F staff")
    return records


def collect(verbose=True):
    """Return (records, pubs) satisfying the core.schema contract."""
    records = scrape_staff(verbose)

    if verbose:
        print("  Phase 3: Monash Pure publications + OpenAlex metadata (3 parallel workers) ...")

    pubs = []
    failed = []
    # 3 workers — conservative to respect OpenAlex rate limits
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_process_phase3, r): r for r in records}
        for future in as_completed(futures):
            r, person_pubs, error = future.result()
            r.pop("_pub_count", None)
            r.pop("_publication_name", None)
            pubs.extend(person_pubs)
            if error:
                failed.append((r["name_clean"], error))
                tag = f"[Pure feed failed: {error}]"
            else:
                tag = "[Pure + ORCID]" if r.get("orcid") else "[Pure + name->Monash]"
            if verbose:
                print(f"    {r['name_clean']:40s}  {len(person_pubs)} pubs  {tag}")

    if verbose:
        print(f"  {len(pubs)} total publications")
        if failed:
            print(f"  Pure feed failed for {len(failed)} staff; they have no publications this run:")
            for name, error in sorted(failed):
                print(f"    - {name}: {error}")
    return records, pubs
