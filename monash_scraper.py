"""
Monash University Staff Scraper
Uses Selenium (real Chrome) to bypass bot protection on monash.edu,
then scrapes research profiles and publications from research.monash.edu.

Run with:  python3 monash_scraper.py
"""

import re, time, requests, csv
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

HEADERS   = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
UNIVERSITY = "Monash University"
TARGETS = [
    ("https://www.monash.edu/business/banking-and-finance/our-people/staff-directory", "Banking & Finance"),
    ("https://www.monash.edu/business/accounting/our-people/staff-directory",          "Accounting"),
]

# ── Academic level helpers ────────────────────────────────────────────
PREFIX = re.compile(
    r"^(Emeritus Professor|Associate Professor|Distinguished Professor"
    r"|Professor|Senior Lecturer|Lecturer|Dr|Mr|Mrs|Ms|Miss"
    r"|A/Prof|Assoc\.?\s*Prof\.?)\.?\s+",
    re.IGNORECASE
)
LADDER = [
    ("Emeritus Professor",    r"emeritus prof"),
    ("Associate Professor",   r"associate prof|a/prof"),
    ("Associate Lecturer",    r"associate lecturer"),
    ("Senior Lecturer",       r"senior lecturer"),
    ("Senior Research Fellow",r"senior research fellow"),
    ("Research Fellow",       r"research fellow"),
    ("Teaching Associate",    r"teaching associate"),
    ("Professor",             r"\bprofessor\b"),
    ("Lecturer",              r"\blecturer\b"),
]

def level_from_title(title, prefix=None):
    for label, pat in LADDER:
        if title and re.search(pat, title, re.I):
            return label
    if prefix and prefix.lower() not in {"dr","mr","mrs","ms","miss"}:
        return prefix
    return None

def name_to_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ── Phase 1: scrape directory pages with Selenium ────────────────────
print("Starting Chrome... (a browser window will open)")
options = webdriver.ChromeOptions()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

records = []

for url, discipline in TARGETS:
    print(f"\nLoading {discipline} page...")
    try:
        driver.get(url)
        time.sleep(5)  # wait for JS
        page_source = driver.page_source
    except Exception as e:
        print(f"  ⚠️  Browser closed or crashed ({e}). Restarting Chrome...")
        try:
            driver.quit()
        except Exception:
            pass
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        driver.get(url)
        time.sleep(5)
        page_source = driver.page_source

    soup = BeautifulSoup(page_source, "html.parser")
    print(f"  Page loaded ({len(page_source)} bytes)")

    # Try card selectors
    CARD_SELECTORS = [
        ".staff-profile", ".profile-card", ".people-listing__item",
        ".person--teaser", ".staff-member", ".staff-list__item",
        ".people__item", ".team-member",
    ]
    cards, matched = [], None
    for sel in CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            matched = sel
            break
    print(f"  Cards found: {len(cards)} (selector: {matched})")

    # Fallback: collect profile links (only monash.edu or relative URLs)
    profile_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        # Skip social share URLs (Facebook etc. embed monash.edu in query string, so check domain explicitly)
        ALLOWED = ("https://www.monash.edu", "https://monash.edu", "https://research.monash.edu")
        if href.startswith("http") and not any(href.startswith(d) for d in ALLOWED):
            continue
        if any(k in href for k in ["/profile/", "/people/", "/persons/", "/staff/", "/our-people/"]) and text:
            profile_links.append((text, href))

    # Words that indicate a navigation link rather than a real person
    NAV_WORDS = {"staff directory", "visiting scholars", "graduate research", "reset",
                 "editorial roles", "distinguished visitor", "work with us", "seminar guests",
                 "supervisors", "program"}

    def is_real_person(name):
        name_lower = name.lower()
        if any(w in name_lower for w in NAV_WORDS):
            return False
        if len(name.split()) < 2:  # must have at least first + last name
            return False
        return True

    if cards:
        for card in cards:
            link = card.select_one("h2 a, h3 a, h4 a, [class*='name'] a")
            if not link:
                continue
            raw = link.get_text(strip=True)
            if not is_real_person(raw):
                continue
            m = PREFIX.match(raw)
            title_tag = card.select_one("[class*='title'],[class*='position'],[class*='role']")
            title = title_tag.get_text(strip=True) if title_tag else None
            records.append({
                "university": UNIVERSITY, "discipline": discipline,
                "name_clean": PREFIX.sub("", raw).strip(),
                "title_raw": title,
                "level": level_from_title(title, m.group(1) if m else None),
                "profile_url": urljoin(url, link.get("href", "")),
            })
    elif profile_links:
        print(f"  Using {len(profile_links)} profile links as fallback")
        for text, href in profile_links:
            if not is_real_person(text):
                continue
            m = PREFIX.match(text)
            records.append({
                "university": UNIVERSITY, "discipline": discipline,
                "name_clean": PREFIX.sub("", text).strip(),
                "title_raw": None,
                "level": level_from_title(None, m.group(1) if m else None),
                "profile_url": urljoin(url, href),
            })
    else:
        print(f"  ⚠️  Nothing found for {discipline} — page may not have loaded")

driver.quit()
print(f"\nPhase 1 done. Total staff: {len(records)}")
for r in records[:5]:
    print(f"  {r['name_clean']:35s}  {r['level']}")


# ── Phase 2: research profiles from research.monash.edu ──────────────
print("\nFetching research profiles...")

def find_research_url(profile_url, name):
    """
    Find the correct research.monash.edu URL for a researcher.
    First checks their monash.edu/business profile page for a link to research.monash.edu.
    Falls back to guessing from name if not found.
    """
    # Try following the business profile page to find the research portal link
    if profile_url and "monash.edu" in profile_url:
        try:
            resp = requests.get(profile_url, headers=HEADERS, timeout=10)
            if resp.status_code == 200 and resp.text:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Only accept a link if it contains part of the person's name
                last_name = name_to_slug(name.split()[-1])  # e.g. "rankin"
                first_name = name_to_slug(name.split()[0])  # e.g. "michaela"
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    # Must start with research.monash.edu — not a Facebook share URL embedding it
                    if not href.startswith("https://research.monash.edu/en/persons/"):
                        continue
                    slug_part = href.split("/en/persons/")[-1].rstrip("/")
                    if last_name in slug_part or first_name in slug_part:
                        return href.rstrip("/") + "/"
        except Exception:
            pass
    # Fallback: guess from name
    slug = name_to_slug(name)
    return f"https://research.monash.edu/en/persons/{slug}/"


def extract_rank_from_soup(soup):
    """
    Try to extract academic rank from a research.monash.edu profile page.
    Tries multiple CSS selectors used by Pure/Elsevier CMS, then falls back
    to scanning the top of the page text for known rank keywords.
    """
    # Selectors that Pure CMS uses for job title / position
    RANK_SELECTORS = [
        ".person-details-info",
        ".person-position",
        ".person-details__position",
        "[class*='person'][class*='position']",
        "[class*='job-title']",
        "[class*='title']",
        ".rendering_person_short .type",
        ".associate-group",
        "span.type",
    ]
    for sel in RANK_SELECTORS:
        el = soup.select_one(sel)
        if el:
            rank = level_from_title(el.get_text(strip=True))
            if rank:
                return rank
    # Fallback: scan first 3000 chars of page text for rank keywords
    body = soup.get_text(" ", strip=True)[:3000]
    return level_from_title(body)


def fetch_research_profile(research_url):
    for attempt in range(3):  # retry up to 3 times
        try:
            resp = requests.get(research_url, headers=HEADERS, timeout=15)
            if resp.status_code == 429:  # rate limited
                time.sleep(10 * (attempt + 1))
                continue
            if resp.status_code != 200 or not resp.text:
                return None, 0
            soup = BeautifulSoup(resp.text, "html.parser")
            rank = extract_rank_from_soup(soup)
            pub_count = 0
            for a in soup.find_all("a", href=True):
                if "/publications/" in a["href"]:
                    txt = a.get_text()
                    # Only accept realistic pub counts, not years (1891, 2004 etc.)
                    m = re.search(r"(\d+)", txt)
                    if m:
                        n = int(m.group(1))
                        if 1 <= n <= 999:
                            pub_count = max(pub_count, n)
            return rank, pub_count
        except Exception:
            time.sleep(3)
    return None, 0

for r in records:
    rurl = find_research_url(r.get("profile_url"), r["name_clean"])
    r["research_url"] = rurl
    rank, pubs = fetch_research_profile(rurl)
    r["pub_count"] = pubs
    if not r["level"]:
        r["level"] = rank
    print(f"  {r['name_clean']:35s}  level={r['level']}  pubs={pubs}")
    time.sleep(1.5)


# ── Phase 3: fetch publications ───────────────────────────────────────
print("\nFetching publications...")

def fetch_publications(research_url):
    """
    Scrape publications from the researcher's profile page.
    Strategy: find all links pointing to /en/publications/ and extract
    info from their surrounding context. This is robust to CSS class changes.
    """
    pubs = []
    try:
        resp = requests.get(research_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200 or not resp.text:
            return pubs
    except Exception:
        return pubs

    soup = BeautifulSoup(resp.text, "html.parser")

    seen = set()
    # Find every link that points to a publication page
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/en/publications/" not in href:
            continue
        title = a.get_text(strip=True)
        if not title or title in seen or len(title) < 10:
            continue
        seen.add(title)

        pub_url = urljoin(research_url, href)

        # Walk up to find the container block (li, div, article)
        container = a.find_parent("li") or a.find_parent("article") or a.find_parent("div")
        text = container.get_text(" ", strip=True) if container else a.get_text(" ", strip=True)

        # Year: find 4-digit year in the container text
        m = re.search(r"\b(19|20)\d{2}\b", text)
        year = int(m.group()) if m else None

        # Journal: text inside <em> in the container
        journal = None
        if container:
            em = container.find("em")
            if em:
                journal = em.get_text(strip=True)

        pubs.append({"title": title, "year": year, "journal": journal, "pub_url": pub_url})

    return pubs

all_pubs = []
for r in records:
    if not r.get("research_url"):
        continue
    pubs = fetch_publications(r["research_url"])
    for p in pubs:
        all_pubs.append({
            "university": r["university"], "discipline": r["discipline"],
            "researcher": r["name_clean"], "level": r["level"], **p
        })
    print(f"  {r['name_clean']:35s}  {len(pubs)} publications")
    time.sleep(1.0)


# ── Save CSVs ─────────────────────────────────────────────────────────
staff_file = "monash_staff.csv"
pubs_file  = "monash_publications.csv"

staff_cols = ["university","discipline","name_clean","level","title_raw","research_url","pub_count"]
with open(staff_file, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=staff_cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(records)

pub_cols = ["university","discipline","researcher","level","title","year","journal","pub_url"]
with open(pubs_file, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=pub_cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(all_pubs)

print(f"\n✅ Done!")
print(f"   {staff_file}: {len(records)} researchers")
print(f"   {pubs_file}: {len(all_pubs)} publications")
