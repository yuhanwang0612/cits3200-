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
    driver.get(url)
    time.sleep(5)  # wait for JS

    soup = BeautifulSoup(driver.page_source, "html.parser")
    print(f"  Page loaded ({len(driver.page_source)} bytes)")

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

    # Fallback: collect profile links
    profile_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if any(k in href for k in ["/profile/", "/people/", "/persons/", "/staff/", "/our-people/"]) and text:
            profile_links.append((text, href))

    if cards:
        for card in cards:
            link = card.select_one("h2 a, h3 a, h4 a, [class*='name'] a")
            if not link:
                continue
            raw = link.get_text(strip=True)
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

def fetch_research_profile(name):
    slug = name_to_slug(name)
    url  = f"https://research.monash.edu/en/persons/{slug}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, url, 0
        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.select_one(".person-details-info")
        title = title_tag.get_text(strip=True) if title_tag else None
        pub_count = 0
        for a in soup.find_all("a", href=True):
            if "/publications/" in a["href"]:
                m = re.search(r"(\d+)", a.get_text())
                if m:
                    pub_count = max(pub_count, int(m.group(1)))
        return title, url, pub_count
    except Exception:
        return None, url, 0

for r in records:
    title, rurl, pubs = fetch_research_profile(r["name_clean"])
    r["research_title"] = title
    r["research_url"]   = rurl
    r["pub_count"]      = pubs
    if not r["level"]:
        r["level"] = level_from_title(title)
    print(f"  {r['name_clean']:35s}  pubs={pubs}")
    time.sleep(0.5)


# ── Phase 3: fetch publications ───────────────────────────────────────
print("\nFetching publications...")

def fetch_publications(research_url):
    """
    Scrape publications from the researcher's PROFILE page (server-rendered).
    The /publications/ sub-page is JS-rendered and won't work with requests.
    """
    pubs = []
    try:
        resp = requests.get(research_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return pubs
    except Exception:
        return pubs

    soup = BeautifulSoup(resp.text, "html.parser")

    # Publications appear as <h3> links inside list items on the profile page
    # Pattern: <li class="list-result-item"> containing <h3><a href="/en/publications/...">Title</a></h3>
    items = soup.select("li.list-result-item, li.rendering")
    if not items:
        # Fallback: find all publication links directly
        items = [a.find_parent("li") or a.find_parent("div")
                 for a in soup.find_all("a", href=lambda h: h and "/en/publications/" in h)]
        items = [i for i in items if i]

    seen = set()
    for item in items:
        title_tag = item.select_one("h3 a, h2 a, .title a")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        if not title or title in seen:
            continue
        seen.add(title)
        pub_url = urljoin(research_url, title_tag.get("href", ""))
        text = item.get_text(" ", strip=True)
        m = re.search(r"\b(19|20)\d{2}\b", text)
        year = int(m.group()) if m else None
        # Journal is usually in <em> or after "In:"
        journal_tag = item.select_one("em")
        journal = journal_tag.get_text(strip=True) if journal_tag else None
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
    time.sleep(0.3)


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
