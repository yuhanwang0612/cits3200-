"""
UNSW Accounting & Finance Scraper — CITS3200 Team 20

Stage 1: staff directory. Publications come in stage 2.

WHY IT WORKS THE WAY IT DOES
----------------------------
UNSW splits accounting and finance across two schools, so both are collected:
    School of Accounting, Auditing and Taxation  -> Accounting
    School of Banking and Finance                -> Finance

The Business School "Our people" directory is a client-side Funnelback search:
paging lives in the URL hash and results are written in by JavaScript, so
Selenium is needed for the listing. Individual staff profiles at
unsw.edu.au/staff/<slug> ARE server-rendered, so plain requests is enough for
those.

We do NOT filter by school in the listing URL. The site's own school filter
uses display labels with ampersands ("Banking & Finance"), and when the encoded
value in the hash is not an exact match the page either returns nothing or
silently drops the filter and returns everyone — both failures look like
success. Instead we page the whole Business School (about 354 people) and read
the authoritative school off each profile page, where UNSW publishes it as a
meta tag:

    <meta name="profile-school"                content="School of Accounting, Auditing and Taxation">
    <meta name="profile-university-role"       content="Senior Lecturer">
    <meta name="profile-full-name"             content="Dr Nicole Ang">

That is slower but it cannot silently return the wrong set, which matters more.

We do not use the Funnelback JSON endpoint directly: its robots.txt disallows
/s/, so it is off limits.

Output field names match the Scope of Work data dictionary (3.5.4) and the ANU
scraper, so the CSVs merge without reshaping.

Install:  python -m pip install requests beautifulsoup4 selenium webdriver-manager
Run:      python unsw_scraper.py --headless
Testing:  python unsw_scraper.py --limit 20 --delay 1
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.robotparser
from collections import Counter, OrderedDict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

UNIVERSITY = "UNSW Sydney"
# What we call ourselves in the `source` column and in the harvest record.
SOURCE_NAME = "UNSW staff profile"
LISTING = "https://www.unsw.edu.au/business/our-people"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/151.0 Safari/537.36")
PAGE_SIZE = 100         # the listing accepts up to at least 100 per page
PAGE_TIMEOUT = 30       # max seconds to wait for results to render
SETTLE = 2.0            # short pause after results appear, so late cards land
DEFAULT_DELAY = 1.5     # seconds between profile fetches
OUTPUT_DIR = "output"
CACHE_DIR = os.path.join(OUTPUT_DIR, "profile_cache")

# The two schools we keep, keyed by the exact value UNSW puts in profile-school.
TARGET_SCHOOLS = {
    "School of Accounting, Auditing and Taxation": "Accounting",
    "School of Banking and Finance": "Finance",
}

# ---------------------------------------------------------------------------
# Academic level mapping
# Kept identical to the ANU, Monash and UQ scrapers so levels stay comparable
# across universities. If this changes, it has to change in all of them.
# ---------------------------------------------------------------------------
# Honorifics stack at UNSW — "Emeritus Scientia Professor Roger Simnett" is a
# real listing. Modifiers are matched as a repeating group rather than spelled
# out as fixed combinations, so any ordering is handled. Use .group(0) to get
# the whole prefix back; the individual groups are not useful on their own.
PREFIX = re.compile(
    r"^((?:Emeritus|Scientia|Distinguished|Adjunct|Honorary|Visiting"
    r"|Conjoint|Clinical|Professorial)\s+)*"
    r"(Associate\s+Professor|Professor|Senior\s+Lecturer|Associate\s+Lecturer"
    r"|Lecturer|Fellow|Dr|Mr|Mrs|Ms|Miss|A/Prof|Assoc\.?\s*Prof\.?|AsPr|EmPr)\.?\s+",
    re.IGNORECASE,
)

LADDER = [
    ("Emeritus Professor",     r"emeritus prof",                  "E"),
    ("Professorial Fellow",    r"professorial fellow",            "E"),
    ("Associate Professor",    r"associate prof|a/prof|aspr",     "D"),
    ("Senior Lecturer",        r"senior lecturer",                "C"),
    ("Senior Research Fellow", r"senior research fellow",         "C"),
    ("Associate Lecturer",     r"associate lecturer",             "A"),
    ("Research Fellow",        r"research fellow",                "B"),
    ("Fellow",                 r"\bfellow\b",                     "B"),
    ("Professor",              r"\bprofessor\b|chair in",         "E"),
    ("Lecturer",               r"\blecturer\b",                   "B"),
]

# Education- and teaching-focused roles are excluded from rankings (FR4),
# matching the reference system's published methodology.
EXCLUDE = re.compile(r"education[-\s]?focus|teaching[-\s]?focus", re.IGNORECASE)


def academic_level(job_title, name_prefix=None):
    """Return (canonical_title, level A-E). Never guesses: unknown -> (None, None)."""
    if job_title and EXCLUDE.search(job_title):
        return "Exclude", None
    for canonical, pattern, level in LADDER:
        if job_title and re.search(pattern, job_title, re.I):
            return canonical, level
    if name_prefix:
        for canonical, pattern, level in LADDER:
            if re.search(pattern, name_prefix, re.I):
                return canonical, level
    return None, None


# ---------------------------------------------------------------------------
# Politeness
# ---------------------------------------------------------------------------
_robots_cache = {}


def _robots(url):
    host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if host not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urljoin(host, "/robots.txt"))
        try:
            rp.read()
        except Exception as exc:
            print(f"  ! could not read robots.txt for {host} ({exc}) — proceeding politely")
            rp = None
        _robots_cache[host] = rp
    return _robots_cache[host]


def may_fetch(url):
    """Check robots.txt once per host. Fail open on error, like the ANU scraper."""
    rp = _robots(url)
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def declared_crawl_delay(url):
    rp = _robots(url)
    if rp is None:
        return None
    try:
        return rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stage 1a — listing (Selenium)
# ---------------------------------------------------------------------------
STAFF_LINK = 'a[href*="/staff/"]'
ROLE = ".card-profile__role"
TOTAL = re.compile(r"out of\s+([\d,]+)\s+results", re.IGNORECASE)


def listing_url(start_rank):
    # No school filter, deliberately — see the module docstring.
    return (f"{LISTING}#search=&sort=metastaffLastName"
            f"&startRank={start_rank}&numRanks={PAGE_SIZE}")


def dismiss_cookie_banner(driver):
    """Decline non-essential cookies if the consent banner appears.

    A fresh browser profile gets the banner every run. We choose 'reject'
    rather than 'accept' — we only need the page, not to be tracked.
    """
    for selector in ("#onetrust-reject-all-handler",
                     ".ot-pc-refuse-all-handler",
                     "#onetrust-accept-btn-handler"):
        try:
            button = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            button.click()
            print("    cookie banner dismissed")
            time.sleep(1)
            return
        except (TimeoutException, WebDriverException):
            continue


def dump_debug(driver, label):
    """Save what we actually got, so a failure can be diagnosed without guessing."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"debug_{label}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print(f"    ! saved page source to {path}")


def card_for(anchor):
    """Find the block that belongs to one person.

    We deliberately do not select on a card class. The results markup puts
    `card-profile` on a wrapper around all the cards, not on each card, so
    selecting it returned exactly one person per page. Climbing outward from
    each staff link until the block would contain a second staff link finds the
    per-person block whatever it is called.
    """
    node = anchor
    for _ in range(8):
        parent = node.parent
        if parent is None:
            break
        if len(parent.select(STAFF_LINK)) > 1:
            break
        node = parent
    return node


def wait_for_results(driver):
    """Wait for the first result, then until the count stops growing.

    A fixed sleep is the wrong tool twice over: the first card can take many
    seconds on a cold profile, and with 100 per page the rest keep arriving
    after it. So we wait for the first, then poll until the count is stable.
    """
    WebDriverWait(driver, PAGE_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, STAFF_LINK)))
    previous, stable, waited = -1, 0, 0.0
    while stable < 3 and waited < PAGE_TIMEOUT:
        count = len(driver.find_elements(By.CSS_SELECTOR, STAFF_LINK))
        stable = stable + 1 if count == previous else 0
        previous = count
        time.sleep(0.5)
        waited += 0.5
    time.sleep(SETTLE)
    return previous


def cards_from_staff(path):
    """Rebuild the listing result from a previous run's unsw_staff.csv.

    The listing is the only part of this scraper that needs a browser, and it is
    also the only part that can be skipped: once a run has written unsw_staff.csv
    we already know every profile URL, and the profile pages themselves are
    cached and server-rendered. So a re-run that only changes how the output is
    *filtered* — the journals-only decision, say — has no reason to start Chrome
    at all.

    This is not a shortcut around a broken environment; it is the honest scope of
    the work. It does mean the run cannot discover a newly appointed academic, so
    it prints that plainly rather than letting a stale roster pass as a fresh one.
    """
    if not os.path.exists(path):
        raise SystemExit(
            f"--from-staff needs {path}, which does not exist yet. The first run "
            f"has to page the directory with Chrome to find out who is there.")
    cards = OrderedDict()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            url = (row.get("profile_url") or "").strip()
            if not url:
                continue
            cards[url] = {
                "raw_name": (row.get("name") or "").strip(),
                "card_role": (row.get("job_title") or "").strip() or None,
                "profile_url": url,
            }
    if not cards:
        raise SystemExit(f"{path} has no profile_url values to work from.")
    print(f"Reusing the roster from {path}: {len(cards)} academics, no browser needed.")
    print("This cannot pick up a newly appointed academic — drop --from-staff for that.")
    return cards


def collect_listing(driver, max_pages=20):
    """Page the whole Business School directory. Returns {profile_url: card dict}."""
    found, start, page, total = {}, 1, 0, None

    while page < max_pages:
        url = listing_url(start)
        # Load a different page first — changing only the hash does not reload,
        # and the search component only reads the hash on a full load.
        driver.get("https://www.unsw.edu.au/business")
        driver.get(url)

        if page == 0:
            dismiss_cookie_banner(driver)

        try:
            rendered = wait_for_results(driver)
        except TimeoutException:
            print(f"    no results rendered within {PAGE_TIMEOUT}s")
            if page == 0:
                dump_debug(driver, "listing")
            break

        source = driver.page_source
        soup = BeautifulSoup(source, "html.parser")

        if total is None:
            match = TOTAL.search(soup.get_text(" ", strip=True))
            if match:
                total = int(match.group(1).replace(",", ""))
                print(f"    directory reports {total} people in the Business School")

        new = 0
        for anchor in soup.select(STAFF_LINK):
            href = anchor.get("href", "").split("?")[0].split("#")[0]
            if "/staff/" not in href:
                continue
            profile_url = urljoin(LISTING, href)
            if profile_url in found:
                continue

            card = card_for(anchor)
            heading = card.find(["h2", "h3", "h4"])
            role = card.select_one(ROLE)
            raw_name = heading.get_text(" ", strip=True) if heading else anchor.get_text(" ", strip=True)
            if not raw_name:
                continue

            found[profile_url] = {
                "raw_name": raw_name,
                "card_role": role.get_text(" ", strip=True) if role else None,
                "profile_url": profile_url,
            }
            new += 1

        print(f"    page {page + 1:>2}  startRank={start:<4} "
              f"{rendered} rendered, +{new} new  ({len(found)} total)")

        if new == 0:
            break
        start += PAGE_SIZE
        page += 1
        if total is not None and start > total:
            break

    if total is not None and len(found) < total:
        print(f"    note: collected {len(found)} of {total} — "
              f"re-run with --timeout 60 if that gap looks wrong")
    return found


# ---------------------------------------------------------------------------
# Stage 1b — profile pages (plain requests, server-rendered)
# ---------------------------------------------------------------------------
META_FIELDS = ("profile-full-name", "profile-school", "profile-faculty",
               "profile-university-role", "profile-university-role-category")


def cache_path(profile_url):
    slug = profile_url.rstrip("/").split("/")[-1]
    return os.path.join(CACHE_DIR, f"{slug}.html")


def fetch_profile_html(session, profile_url, delay, use_cache=True):
    """Fetch a profile page, caching it so re-runs cost nothing."""
    path = cache_path(profile_url)
    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read(), True
    if not may_fetch(profile_url):
        return None, False
    try:
        resp = session.get(profile_url, timeout=20)
    except requests.RequestException as exc:
        print(f"    ! {profile_url}: {exc}")
        return None, False
    time.sleep(delay)
    if resp.status_code != 200:
        print(f"    ! {profile_url}: HTTP {resp.status_code}")
        return None, False
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    return resp.text, False


def parse_profile(soup):
    """Read UNSW's own profile-* meta tags. These are authoritative."""
    out = {}
    for field in META_FIELDS:
        tag = soup.find("meta", attrs={"name": field})
        out[field] = tag["content"].strip() if tag and tag.get("content") else None
    return out


# ---------------------------------------------------------------------------
# Stage 2 — publications
#
# Publications are on the same profile page we already fetched, inside the
# Publications tab, and they are server-rendered — no browser and no extra
# request needed. Most entries carry structured spans from UNSW's research
# gateway feed:
#
#   .publication-item
#       .publication-category   "Journal articles" / "Book Chapters" / ...
#       .publication-year       "2026"
#       .rg-author              "Li H;  Liu L;  Masulis R;  Zein J"
#       .rg-title               "'Does common ownership raise antitrust concerns?'"
#       .rg-source-title        "Journal of Corporate Finance"      (journal)
#       .rg-volume / .rg-page / .rg-publisher
#       a[href*="doi.org"]      DOI link
#
# A minority are a bare paragraph of free text with no spans at all. Those are
# NOT guessed at — they go to unsw_unparsed_publications.csv with the raw text,
# matching how the ANU scraper handles the same problem. Silently dropping them
# would understate a researcher's output; silently mis-parsing them would be
# worse.
# ---------------------------------------------------------------------------
PUB_ITEM = ".publication-item"
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
# The client decided on 19 August that the dataset is journal articles only.
# The prompt for that decision was Mark Humphery-Jenner: 410 publications, of
# which 293 are media commentary and 44 are journal articles. A raw count put
# him top at UNSW on the strength of newspaper columns.
#
# Everything else is still collected and written to
# unsw_publications_all_types.csv, because re-scraping is expensive and a
# decision can be revisited. What changes is which file is the dataset.
JOURNAL_TYPE = re.compile(r"journal", re.IGNORECASE)


def is_journal_article(publication):
    kind = publication.get("publication_type")
    return bool(kind and JOURNAL_TYPE.search(kind))


def _text(node, selector):
    found = node.select_one(selector)
    if not found:
        return None
    value = found.get_text(" ", strip=True)
    return value or None


def _clean_title(title):
    """Titles arrive wrapped in single quotes: 'Some title'."""
    if not title:
        return None
    return title.strip().strip("'‘’\"").strip() or None


def _author_list(raw):
    """UNSW separates authors with semicolons: 'Li H;  Liu L;  Masulis R'."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(";") if p.strip()]


def _split_authors(raw):
    return "; ".join(_author_list(raw)) or None


def parse_publications(soup, person):
    """Return (publications, unparsed) for one researcher."""
    pubs, unparsed, seen = [], [], set()

    for item in soup.select(PUB_ITEM):
        category = _text(item, ".publication-category")
        year = _text(item, ".rg-year") or _text(item, ".publication-year")
        title = _clean_title(_text(item, ".rg-title"))
        authors = _text(item, ".rg-author")

        # A few entries carry more than one link. Prefer the DOI — it is the
        # stable identifier and the join key for OpenAlex later. Fall back to
        # whatever link is there (often a publisher or UNSW-hosted PDF).
        #
        # Some entries link to a bare "http://dx.doi.org" with no identifier
        # after it. That is a broken link on UNSW's side, not a DOI, so it is
        # discarded rather than written out as an article_url that goes nowhere.
        links = [a["href"].strip() for a in item.select("a[href]") if a.get("href")]
        doi = None
        doi_link = None
        for candidate in links:
            match = DOI_RE.search(candidate)
            if match:
                doi = match.group(0).rstrip(".,;)")
                doi_link = candidate
                break
        others = [u for u in links if "doi.org" not in u]
        url = doi_link or (others[0] if others else None)

        if not title:
            # No structured title — record it rather than guess at the fields.
            raw = item.get_text(" ", strip=True)
            if raw:
                year_guess = YEAR_RE.search(raw)
                unparsed.append({
                    "researcher_name": person["name"],
                    "researcher_profile_url": person["profile_url"],
                    "university": UNIVERSITY,
                    "publication_type": category,
                    "year": year or (year_guess.group(0) if year_guess else None),
                    "raw_citation": raw,
                    "reason": "no structured title on the page",
                })
            continue

        # Some outputs are listed twice on the same page under two different
        # DOIs — the 1986 Journal of Finance paper below appears once with its
        # JSTOR DOI and once with its Wiley one, and two SSRN versions of the
        # same working paper appear under two SSRN IDs. Counting those twice
        # would inflate the productivity measure, so the DOI is deliberately
        # NOT part of the identity.
        #
        # Title alone is too loose in the other direction: the same title
        # legitimately appears as a 2015 conference paper and a 2019 book, and
        # those are two different outputs. Title + year + type + journal keeps
        # those apart while collapsing the true duplicates.
        identity = (title.lower(), year, category,
                    (journal or "").lower() if (journal := _text(item, ".rg-source-title")) else "")
        if identity in seen:
            # Keep whichever copy carries a DOI — it is the more useful record.
            if doi:
                for existing in pubs:
                    if existing["_identity"] == identity and not existing["doi"]:
                        existing["doi"] = doi
                        existing["article_url"] = url or existing["article_url"]
                        break
            continue
        seen.add(identity)

        pubs.append({
            "_identity": identity,
            "researcher_name": person["name"],
            "researcher_profile_url": person["profile_url"],
            "university": UNIVERSITY,
            "field_of_research": person["field_of_research"],
            "title": title,
            "journal_name": journal,
            "year": year,
            "publication_type": category,
            "doi": doi,
            "article_url": url,
            "coauthors": _split_authors(authors),
            # Requested by the client on 12 August, for every publication.
            # Counted from the page's own author list rather than by splitting
            # the joined string later, so a name containing a semicolon cannot
            # inflate it. Left blank rather than set to 0 when no authors are
            # listed — "we don't know" and "zero authors" are different.
            "author_count": len(_author_list(authors)) or None,
            "volume": _text(item, ".rg-volume"),
            "pages": _text(item, ".rg-page"),
            "publisher": _text(item, ".rg-publisher"),
            "abdc_self_reported": None,   # joined from the ABDC list downstream
            "citation_percentile": None,  # joined from OpenAlex downstream
            "source": SOURCE_NAME,
        })

    # The dedup key is internal bookkeeping — drop it so callers only ever see
    # the documented columns.
    for pub in pubs:
        pub.pop("_identity", None)

    return pubs, unparsed


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
STAFF_COLUMNS = [
    "name", "job_title", "academic_level", "field_of_research",
    "profile_url", "university", "research_portal_url", "school",
]


PUB_COLUMNS = [
    "researcher_name", "researcher_profile_url", "university", "field_of_research",
    "title", "journal_name", "year", "publication_type", "doi", "article_url",
    "coauthors", "author_count", "volume", "pages", "publisher",
    "abdc_self_reported", "citation_percentile", "source",
]

UNPARSED_COLUMNS = [
    "researcher_name", "researcher_profile_url", "university",
    "publication_type", "year", "raw_citation", "reason",
]

NO_PUBS_COLUMNS = ["name", "profile_url", "university", "field_of_research",
                   "job_title", "academic_level"]


def _write_csv(name, columns, rows):
    path = os.path.join(OUTPUT_DIR, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


# The harvest record (data dictionary 3.5.4) lives with the shared ranking
# tooling, because every source script writes into the same file. This scraper
# sits a directory above it, so the import is guarded: a missing rankings/
# folder should cost you the harvest row, not the whole scrape.
def _harvest_module():
    try:
        import harvest                                   # already importable
        return harvest
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "rankings"), os.path.join(here, "..", "rankings")):
        if os.path.exists(os.path.join(candidate, "harvest.py")):
            sys.path.insert(0, os.path.abspath(candidate))
            try:
                import harvest
                return harvest
            except ImportError:
                return None
    return None


def record_harvest(pubs):
    """Write this scrape into output/harvest.csv. Never fatal."""
    harvest = _harvest_module()
    if harvest is None:
        print("\n  (no harvest row: rankings/harvest.py not found next to this script)")
        return None
    row = harvest.record(UNIVERSITY, SOURCE_NAME,
                         harvest.latest_year_in(pubs), OUTPUT_DIR)
    print(f"  {os.path.join(OUTPUT_DIR, 'harvest.csv')}")
    return row


def write_output(records, pubs, unparsed, no_pubs, everything=None):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    written = [
        _write_csv("unsw_staff.csv", STAFF_COLUMNS, records),
        _write_csv("unsw_publications.csv", PUB_COLUMNS, pubs),
    ]
    # The types the client excluded are still collected and still written. A
    # decision can be revisited; a re-scrape costs an hour.
    if everything is not None:
        written.append(_write_csv("unsw_publications_all_types.csv",
                                  PUB_COLUMNS, everything))
    # These two are written even when empty, so "no problems" is visibly
    # different from "the file was never produced".
    written.append(_write_csv("unsw_unparsed_publications.csv",
                              UNPARSED_COLUMNS, unparsed))
    written.append(_write_csv("unsw_no_publications.csv",
                              NO_PUBS_COLUMNS, no_pubs))

    json_path = os.path.join(OUTPUT_DIR, "unsw_staff.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    written.append(json_path)
    return written


# ---------------------------------------------------------------------------
def main():
    global PAGE_TIMEOUT
    parser = argparse.ArgumentParser(
        description="Collect UNSW accounting and finance academics.")
    parser.add_argument("--headless", action="store_true", help="run Chrome without a window")
    parser.add_argument("--limit", type=int, help="only check this many profiles (testing)")
    parser.add_argument("--delay", type=float, help="seconds between profile fetches")
    parser.add_argument("--no-cache", action="store_true", help="ignore the profile cache")
    parser.add_argument("--from-staff", nargs="?", const=os.path.join(OUTPUT_DIR, "unsw_staff.csv"),
                        metavar="CSV",
                        help="skip the browser and reuse the roster from a previous "
                             "run's unsw_staff.csv. Use this when only the output "
                             "rules changed, not who works there.")
    parser.add_argument("--all-types", action="store_true",
                        help="put every publication type in unsw_publications.csv. "
                             "By default that file holds journal articles only, as "
                             "the client decided on 19 August; the other types are "
                             "always written to unsw_publications_all_types.csv "
                             "either way, so nothing is lost.")
    parser.add_argument("--timeout", type=int, default=PAGE_TIMEOUT,
                        help=f"seconds to wait for the listing (default {PAGE_TIMEOUT})")
    args = parser.parse_args()
    PAGE_TIMEOUT = args.timeout

    if not may_fetch(LISTING):
        print(f"robots.txt disallows {LISTING} — stopping.")
        return

    declared = declared_crawl_delay(LISTING)
    delay = args.delay if args.delay is not None else DEFAULT_DELAY
    if declared:
        print(f"robots.txt declares Crawl-delay: {declared}s; using {delay}s between "
              f"profile fetches (pages are cached, so this is a one-off cost).")

    if args.from_staff:
        cards = cards_from_staff(args.from_staff)
    else:
        options = webdriver.ChromeOptions()
        if args.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-agent={USER_AGENT}")

        print("Starting Chrome...")
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                                      options=options)
        except WebDriverException as exc:
            # Almost always a Chrome/ChromeDriver version mismatch on the machine
            # running this, not a problem with the site or the code. Say so,
            # rather than making the reader work it out from a Selenium stack
            # trace, and point at the run that does not need a browser.
            print(f"\nChrome would not start: {str(exc).splitlines()[0]}")
            print("\nThis is a local browser problem, not a scraping one. Either:")
            print("  - update Chrome, then delete C:\\Users\\<you>\\.wdm to clear the")
            print("    cached driver so a matching one is downloaded; or")
            print("  - if output/unsw_staff.csv already exists, skip the browser:")
            print("        python unsw_scraper.py --from-staff")
            return
        try:
            print("\nListing: UNSW Business School")
            cards = collect_listing(driver)
        finally:
            driver.quit()

        if not cards:
            print("\nNothing in the listing. Check output/debug_listing.html — if the")
            print("staff links are missing from it the markup has changed; if they are")
            print("present the wait needs to be longer (try --timeout 60).")
            return

    targets = list(cards.values())
    if args.limit:
        targets = targets[:args.limit]
        print(f"(limited to {len(targets)} profiles for this run)")

    print(f"\nReading {len(targets)} profile pages "
          f"(cached in {CACHE_DIR}, so re-runs are instant)...")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    records, skipped, failed, excluded = [], [], [], []
    all_pubs, all_unparsed, no_pubs = [], [], []
    for i, card in enumerate(targets, 1):
        html, cached = fetch_profile_html(session, card["profile_url"], delay,
                                          use_cache=not args.no_cache)
        if html is None:
            failed.append(card["profile_url"])
            continue
        soup = BeautifulSoup(html, "html.parser")
        meta = parse_profile(soup)
        school = meta["profile-school"]
        discipline = TARGET_SCHOOLS.get(school)
        if discipline is None:
            skipped.append((card["raw_name"], school))
            continue

        job_title = meta["profile-university-role"] or card["card_role"]
        # The title prefix is read before it is stripped: it is the fallback for
        # academic level when the job title itself doesn't match the ladder.
        full_name = meta["profile-full-name"] or card["raw_name"]
        prefix_match = PREFIX.match(full_name) or PREFIX.match(card["raw_name"])
        canonical, level = academic_level(
            job_title, prefix_match.group(0) if prefix_match else None)
        if canonical == "Exclude":
            excluded.append((full_name, job_title))

        person = {
            # Plain name — rank lives in job_title/academic_level, matching the
            # ANU and UQ scrapers so the columns line up.
            "name": PREFIX.sub("", full_name).strip(),
            "job_title": job_title,
            "academic_level": level,
            "field_of_research": discipline,
            "profile_url": card["profile_url"],
            "university": UNIVERSITY,
            "research_portal_url": None,
            "school": school,
        }
        records.append(person)

        pubs, unparsed = parse_publications(soup, person)
        all_pubs += pubs
        all_unparsed += unparsed
        if not pubs and not unparsed:
            no_pubs.append(person)

        print(f"  {i:>3}/{len(targets)}  {'cache' if cached else 'fetch'}  "
              f"{person['name']:<34} {discipline:<10} {str(level or '?'):<3} "
              f"{len(pubs):>4} pubs"
              + (f"  ({len(unparsed)} unparsed)" if unparsed else ""))

    if not records:
        print("\nNo one matched the two target schools. Check that the school names in")
        print("TARGET_SCHOOLS still match what UNSW publishes in profile-school.")
        return

    # Everything is kept on disk; the filter decides which file is the dataset.
    everything = all_pubs
    journals = [p for p in all_pubs if is_journal_article(p)]
    if args.all_types:
        print(f"\n--all-types: unsw_publications.csv holds all "
              f"{len(everything)} publications")
    else:
        # Named `excluded_types`, not `excluded`: that name already holds the
        # education-focused academics, and shadowing it here made the summary
        # below report the number of publication types as a headcount and then
        # crash trying to unpack a Counter's keys.
        excluded_types = Counter(p["publication_type"] for p in everything
                                 if not is_journal_article(p))
        print(f"\nJournal articles only (client decision, 19 August): "
              f"{len(journals)} of {len(everything)} publications kept")
        print(f"  the other {len(everything) - len(journals)} are in "
              f"unsw_publications_all_types.csv:")
        for kind, n in excluded_types.most_common(6):
            print(f"     {n:>4}  {kind}")
        if len(excluded_types) > 6:
            print(f"     ...and {len(excluded_types) - 6} more types")
        all_pubs = journals

    written = write_output(records, all_pubs, all_unparsed, no_pubs,
                           everything if not args.all_types else None)

    print(f"\nComplete: {len(records)} academics, {len(all_pubs)} publications")
    for path in written:
        print(f"  {path}")
    record_harvest(all_pubs)
    print("\n  by discipline:", dict(Counter(r["field_of_research"] for r in records)))
    print("  by level:     ", dict(Counter(r["academic_level"] for r in records)))
    print("  level unknown:", sum(1 for r in records if not r["academic_level"]))
    print(f"  other schools skipped: {len(skipped)}")
    print(f"  education/teaching-focused (blank level, FR4): {len(excluded)}")
    for name, title in excluded:
        print(f"      {name} — {title}")
    print(f"  profiles that failed to load: {len(failed)}")
    for url in failed:
        # FR1: a profile that no longer resolves means the person has left,
        # following the client's guidance on Stephen Gray at UQ.
        print(f"      {url}")

    kept_journals = [p for p in all_pubs if is_journal_article(p)]
    if args.all_types:
        print(f"\n  publications: {len(all_pubs)}"
              f"  ({len(kept_journals)} journal articles — the ones ABDC ranks)")
        print("  by type:      ",
              dict(Counter(p["publication_type"] for p in all_pubs)))
    else:
        print(f"\n  publications in the dataset: {len(all_pubs)} journal articles"
              f"  ({len(everything) - len(all_pubs)} other types set aside)")
    print(f"  with a DOI:    {sum(1 for p in all_pubs if p['doi'])}")
    print(f"  with a journal name: {sum(1 for p in all_pubs if p['journal_name'])}")
    print(f"  unparsed (logged, not guessed at): {len(all_unparsed)}")
    print(f"  researchers with no publications listed: {len(no_pubs)}")


if __name__ == "__main__":
    main()
