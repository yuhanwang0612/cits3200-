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

LEVEL_CODE = {
    "Emeritus Professor":     "E",
    "Distinguished Professor":"E",
    "Professor":              "E",
    "Associate Professor":    "D",
    "Senior Lecturer":        "C",
    "Senior Research Fellow": "C",
    "Lecturer":               "B",
    "Research Fellow":        "B",
    "Associate Lecturer":     "A",
    "Teaching Associate":     "A",
}

def level_code(level_text):
    return LEVEL_CODE.get(level_text)

def name_to_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

# Position/role phrases used on monash.edu staff directory cards
POSITION_RE = re.compile(
    r"(Emeritus Professor|Adjunct Professor|Associate Professor|Assistant Professor"
    r"|Distinguished Professor|Professor|Associate Lecturer|Senior Lecturer|Lecturer"
    r"|Senior Research Fellow|Principal Research Fellow|Research Fellow|Teaching Associate"
    r"|Adjunct Research Fellow|Head of [A-Za-z& ]+?(?=\s{2,}|$)|Deputy Head of [A-Za-z& ]+?(?=\s{2,}|$))",
    re.IGNORECASE,
)

def title_near_link(a_tag, name):
    """
    Best-effort: pull a position/role string out of the markup surrounding a
    profile link. The staff-directory cards render the title as a sibling of the
    name link, so walk up a few ancestors and scan their text.
    """
    PROFILE_KEYS = ("/profile/", "/people/", "/persons/", "/staff/", "/our-people/")
    node = a_tag
    for _ in range(3):
        parent = node.parent
        if parent is None:
            break
        # Don't ascend into a container that also holds other people's profile
        # links — otherwise we'd lift a title off an adjacent card.
        if any(x is not a_tag and any(k in x.get("href", "") for k in PROFILE_KEYS)
               for x in parent.find_all("a", href=True)):
            break
        node = parent
        txt = node.get_text(" ", strip=True)
        # Drop the person's own name so "Professor <Name>" doesn't confuse the match
        txt = re.sub(re.escape(name), " ", txt, flags=re.I)
        m = POSITION_RE.search(txt)
        if m:
            return m.group(0).strip()
    return None


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
seen_profile_urls = set()  # prevent duplicate staff entries

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
            profile_links.append((text, href, a))

    # Words that indicate a navigation link rather than a real person
    NAV_WORDS = {"staff directory", "visiting scholars", "graduate research", "reset",
                 "editorial roles", "distinguished visitor", "work with us", "seminar guests",
                 "supervisors", "program", "view all", "explore network", "research outputs",
                 "activities", "projects", "prizes"}

    def is_real_person(name):
        name_lower = name.lower()
        if any(w in name_lower for w in NAV_WORDS):
            return False
        if len(name.split()) < 2:  # must have at least first + last name
            return False
        # Reject "Surname, F." abbreviated name format (these are co-authors, not staff)
        if re.match(r"^[A-Z][a-z]+,\s+[A-Z]\.?\s*[A-Z]?\.?$", name):
            return False
        # Reject strings with digits (e.g. "View all 21")
        if re.search(r"\d", name):
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
            profile_url = urljoin(url, link.get("href", ""))
            if profile_url in seen_profile_urls:
                continue
            seen_profile_urls.add(profile_url)
            m = PREFIX.match(raw)
            title_tag = card.select_one("[class*='title'],[class*='position'],[class*='role']")
            title = title_tag.get_text(strip=True) if title_tag else None
            records.append({
                "university": UNIVERSITY, "discipline": discipline,
                "name_clean": PREFIX.sub("", raw).strip(),
                "title_raw": title,
                "level": level_from_title(title, m.group(1) if m else None),
                "profile_url": profile_url,
            })
    elif profile_links:
        print(f"  Using {len(profile_links)} profile links as fallback")
        for text, href, a in profile_links:
            if not is_real_person(text):
                continue
            profile_url = urljoin(url, href)
            if profile_url in seen_profile_urls:
                continue
            seen_profile_urls.add(profile_url)
            m = PREFIX.match(text)
            name_clean = PREFIX.sub("", text).strip()
            title = title_near_link(a, name_clean)
            records.append({
                "university": UNIVERSITY, "discipline": discipline,
                "name_clean": name_clean,
                "title_raw": title,
                "level": level_from_title(title, m.group(1) if m else None),
                "profile_url": profile_url,
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
                return None, 0, None
            soup = BeautifulSoup(resp.text, "html.parser")
            rank = extract_rank_from_soup(soup)
            pub_count = 0
            for a in soup.find_all("a", href=True):
                if "/publications/" in a["href"]:
                    txt = a.get_text()
                    m = re.search(r"(\d+)", txt)
                    if m:
                        n = int(m.group(1))
                        if 1 <= n <= 999:
                            pub_count = max(pub_count, n)
            # Extract ORCID from profile page (Pure always links to orcid.org)
            orcid = None
            for a in soup.find_all("a", href=True):
                m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
                if m:
                    orcid = m.group(1)
                    break
            return rank, pub_count, orcid
        except Exception:
            time.sleep(3)
    return None, 0, None

for r in records:
    rurl = find_research_url(r.get("profile_url"), r["name_clean"])
    r["research_url"] = rurl
    rank, pubs, orcid = fetch_research_profile(rurl)
    r["pub_count"] = pubs
    r["orcid"] = orcid
    if not r["level"]:
        r["level"] = rank
    print(f"  {r['name_clean']:35s}  level={r['level']}  pubs={pubs}  orcid={orcid}")
    time.sleep(1.5)


# ── Load ABDC journal rankings ────────────────────────────────────────
abdc_lookup = {}  # journal name (lowercase) → rating
try:
    with open("journals.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("journal", "").strip().lower()
            canonical = row.get("journal_canonical", "").strip().lower()
            rating = row.get("abdc", "").strip()
            if name and rating:
                abdc_lookup[name] = rating
            if canonical and rating:
                abdc_lookup[canonical] = rating
    print(f"Loaded {len(abdc_lookup)} ABDC journal entries")
except FileNotFoundError:
    print("journals.csv not found — ABDC ratings will be blank")

def get_abdc(journal_name):
    if not journal_name:
        return None
    return abdc_lookup.get(journal_name.strip().lower())


# ── Load / download Scimago journal rankings ──────────────────────────
import re as _re

def _norm(s):
    """Lowercase, strip punctuation for fuzzy journal matching."""
    return _re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()

scimago_lookup = {}  # normalised title → (sjr_score, quartile, cites_per_doc_2y)

SCIMAGO_FILE = "scimago.csv"
SCIMAGO_URL  = "https://www.scimagojr.com/journalrank.php?out=xls"

def _load_scimago_csv(path):
    loaded = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        # Scimago exports semicolon-separated despite the .xls extension
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            title = row.get("Title", "").strip()
            sjr   = row.get("SJR", "").replace(",", ".").strip()
            q     = row.get("SJR Best Quartile", "").strip()
            # "Cites / Doc. (2years)" column name varies slightly by year
            cites_key = next((k for k in row if "cites" in k.lower() and "2" in k), None)
            cites = row.get(cites_key, "").replace(",", ".").strip() if cites_key else ""
            if title and sjr:
                key = _norm(title)
                try:
                    scimago_lookup[key] = (
                        float(sjr),
                        q or None,
                        float(cites) if cites else None
                    )
                    loaded += 1
                except ValueError:
                    pass
    return loaded

import os as _os
if _os.path.exists(SCIMAGO_FILE):
    n = _load_scimago_csv(SCIMAGO_FILE)
    print(f"Loaded {n} Scimago journal entries from {SCIMAGO_FILE}")
else:
    print("Downloading Scimago journal rankings (this may take a moment)...")
    try:
        r = requests.get(SCIMAGO_URL,
                         headers={"User-Agent": "Mozilla/5.0"},
                         timeout=60)
        r.raise_for_status()
        with open(SCIMAGO_FILE, "wb") as f:
            f.write(r.content)
        n = _load_scimago_csv(SCIMAGO_FILE)
        print(f"Downloaded and loaded {n} Scimago journal entries")
    except Exception as e:
        print(f"Could not download Scimago data: {e} — scimago columns will be blank")

def get_scimago(journal_name):
    if not journal_name:
        return None, None, None
    result = scimago_lookup.get(_norm(journal_name))
    if result:
        return result  # (sjr_score, quartile, cites_per_doc_2y)
    return None, None, None


# ── Phase 3: fetch ALL publications via OpenAlex ─────────────────────
print("\nFetching all publications via OpenAlex...")

OA_HEADERS = {"User-Agent": "monash-research-scraper/1.0 (mailto:wyuhan577@gmail.com)"}

def oa_get(url, params):
    """OpenAlex GET with retry on 429/timeout."""
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=OA_HEADERS, timeout=20)
            if resp.status_code == 429:
                print("    ⏳ OpenAlex rate limit — waiting 30s...")
                time.sleep(30)
                continue
            return resp
        except Exception as e:
            print(f"    ⚠️  OpenAlex error: {e}")
            time.sleep(10)
    return None

def parse_oa_works(works):
    """Convert OpenAlex work records to publication dicts."""
    pubs = []
    for w in works:
        title = (w.get("title") or "").strip()
        if not title or len(title) < 5:
            continue
        year = w.get("publication_year")
        doi  = (w.get("doi") or "").replace("https://doi.org/", "").strip().rstrip(".,;:").lower()
        pub_type = w.get("type") or None
        loc    = w.get("primary_location") or {}
        src    = loc.get("source") or {}
        journal_name = src.get("display_name") or None
        issn_list    = src.get("issn") or []
        issn         = issn_list[0] if issn_list else (src.get("issn_l") or None)
        authorships  = w.get("authorships") or []
        author_count = len(authorships)
        authors = "; ".join(
            a.get("author", {}).get("display_name", "")
            for a in authorships
            if a.get("author", {}).get("display_name")
        )
        sjr_score, sjr_quartile, cites_per_doc = get_scimago(journal_name)
        pubs.append({
            "title": title, "year": year, "doi": doi,
            "article_url": f"https://doi.org/{doi}" if doi else "",
            "author_count": author_count, "authors": authors,
            "publication_type": pub_type,
            "source": journal_name,
            "journal_name": journal_name,
            "issn": issn,
            "quality_rank": get_abdc(journal_name),
            "scimago_sjr": sjr_score,
            "scimago_quartile": sjr_quartile,
            "cites_per_doc_2y": cites_per_doc,
            "impact_factor": None,  # filled when JIF CSV is available
        })
    return pubs

def fetch_pubs_openalex(name, orcid=None):
    """
    Fetch all publications from OpenAlex.
    Strategy:
      1. If ORCID known → look up OpenAlex author by ORCID → get their author ID
         → fetch all works by that author ID (much better coverage than ORCID filter).
      2. If no ORCID → search by name → only proceed if a result has Monash as
         their institution (avoids false positives from common names).
    """
    pubs = []
    author_id = None
    h_index = None

    if orcid:
        # ORCID → OpenAlex author lookup → author ID + h_index
        resp = oa_get("https://api.openalex.org/authors",
                      {"filter": f"orcid:{orcid}", "per-page": 1})
        if resp and resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                author_id = results[0]["id"]
                h_index = (results[0].get("summary_stats") or {}).get("h_index")
        time.sleep(1)
        if not author_id:
            return [], None  # ORCID not in OpenAlex

    else:
        # Name search → require Monash affiliation to avoid wrong matches
        resp = oa_get("https://api.openalex.org/authors",
                      {"search": name, "per-page": 10})
        if not resp or resp.status_code != 200:
            return [], None
        results = resp.json().get("results", [])
        monash_match = next(
            (r for r in results
             if any("monash" in (i.get("display_name") or "").lower()
                    for i in (r.get("last_known_institutions") or []))),
            None
        )
        if not monash_match:
            return [], None  # no Monash-affiliated match → skip rather than guess
        author_id = monash_match["id"]
        h_index = (monash_match.get("summary_stats") or {}).get("h_index")
        time.sleep(1)

    author_filter = f"authorships.author.id:{author_id}"

    # ── Fetch all works (cursor pagination) ──────────────────────────
    cursor = "*"
    while True:
        resp = oa_get("https://api.openalex.org/works", {
            "filter": author_filter,
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
        pubs.extend(parse_oa_works(works))
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)

    return pubs, h_index

all_pubs = []
for r in records:
    name = r["name_clean"]
    if len(name.split()) < 2 or len(name) > 60:
        continue
    orcid = r.get("orcid")
    pubs, h_index = fetch_pubs_openalex(name, orcid=orcid)
    # Sanity check: if name-match returns >5x the Pure pub_count, discard
    profile_count = r.get("pub_count", 0) or 0
    if not orcid and profile_count == 0 and len(pubs) > 50:
        pubs = []  # profile shows 0 pubs → don't trust name match
    elif not orcid and profile_count > 0 and len(pubs) > profile_count * 5:
        pubs = []  # way more than profile → wrong person

    r["h_index"] = h_index
    r["level_code"] = level_code(r.get("level"))

    for p in pubs:
        all_pubs.append({
            "university": r["university"],
            "field_of_research": r.get("discipline") or r.get("field_of_research"),
            "researcher": name,
            "academic_level": r["level"],
            "level_code": r["level_code"],
            **p
        })
    tag = "[ORCID]" if orcid else "[name→Monash]"
    print(f"  {name:35s}  {len(pubs)} pubs  h={h_index}  {tag}")
    time.sleep(3)  # polite rate limiting — avoids 429


# ── Save CSVs ─────────────────────────────────────────────────────────
staff_file = "monash_staff.csv"
pubs_file  = "monash_publications.csv"

# Rename record keys to Scope 3.5.4 naming before writing
for r in records:
    r["name"]              = r.pop("name_clean", r.get("name_clean"))
    r["job_title"]         = r.pop("title_raw", None)
    r["academic_level"]    = r.get("level")
    r["field_of_research"] = r.pop("discipline", None)

staff_cols = ["university","name","job_title","academic_level","level_code","h_index",
              "field_of_research","profile_url","pub_count"]
with open(staff_file, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=staff_cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(records)

pub_cols = ["university","field_of_research","researcher","academic_level","level_code",
            "title","year","doi","article_url","author_count","authors","publication_type",
            "source","journal_name","issn","quality_rank","scimago_sjr","scimago_quartile",
            "cites_per_doc_2y","impact_factor"]
with open(pubs_file, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=pub_cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(all_pubs)

print("\n✅ Done!")
print(f"   {staff_file}: {len(records)} researchers")
print(f"   {pubs_file}: {len(all_pubs)} publications")
