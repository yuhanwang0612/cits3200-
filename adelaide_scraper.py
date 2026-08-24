"""
Adelaide University Staff Scraper (School of Accounting & Finance)
Phase 1: Paginate researchers.adelaide.edu.au to find Business & Law staff (server-rendered)
Phase 2: Fetch each researcher's profile for name, title, school, ORCID
Phase 3: OpenAlex API for publications

Run with:  python3 adelaide_scraper.py
"""

import re, time, requests, csv, os
from bs4 import BeautifulSoup

HEADERS    = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
UNIVERSITY = "Adelaide University"
DISCIPLINE = "Accounting & Finance"

# ── Academic level helpers ────────────────────────────────────────────
LADDER = [
    ("Emeritus Professor",    r"emeritus prof"),
    ("Distinguished Professor", r"distinguished prof"),
    ("Associate Professor",   r"associate prof|a/prof"),
    ("Associate Lecturer",    r"associate lecturer"),
    ("Senior Lecturer",       r"senior lecturer"),
    ("Senior Research Fellow",r"senior research fellow"),
    ("Research Fellow",       r"research fellow"),
    ("Teaching Associate",    r"teaching associate"),
    ("Professor",             r"\bprofessor\b"),
    ("Lecturer",              r"\blecturer\b"),
]

def level_from_title(title):
    for label, pat in LADDER:
        if title and re.search(pat, title, re.I):
            return label
    return None

LEVEL_CODE = {
    "Emeritus Professor":      "E",
    "Distinguished Professor": "E",
    "Professor":               "E",
    "Associate Professor":     "D",
    "Senior Lecturer":         "C",
    "Senior Research Fellow":  "C",
    "Lecturer":                "B",
    "Research Fellow":         "B",
    "Associate Lecturer":      "A",
    "Teaching Associate":      "A",
}

def level_code(level_text):
    return LEVEL_CODE.get(level_text)

PREFIX_RE = re.compile(
    r"^(Emeritus Professor|Distinguished Professor|Associate Professor"
    r"|Professor|Senior Lecturer|Lecturer|Dr|Mr|Mrs|Ms|Miss|A/Prof"
    r"|Assoc\.?\s*Prof\.?)\.?\s+",
    re.IGNORECASE
)

def clean_name(raw):
    m = PREFIX_RE.match(raw.strip())
    return raw[m.end():].strip() if m else raw.strip()

# ── Phase 1: collect ALL researchers via paginated list ──────────────
print("Phase 1: Scanning researchers.adelaide.edu.au ...")

candidate_usernames = []  # researchers.adelaide.edu.au usernames

page = 1
MAX_PAGES = 300  # safety limit
consecutive_empty = 0

while page <= MAX_PAGES:
    url = f"https://researchers.adelaide.edu.au/?page={page}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            break
        soup = BeautifulSoup(resp.text, "html.parser")

        # Each researcher card: look for college/faculty text near profile links
        # Profile links are like /profile/username
        profile_links = soup.find_all("a", href=re.compile(r"^/profile/"))
        if not profile_links:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break
            page += 1
            continue
        consecutive_empty = 0

        found_this_page = 0
        for link in profile_links:
            username = link["href"].replace("/profile/", "").strip("/")
            if not username:
                continue
            # Collect all researchers — Phase 2 filters by accounting/finance keywords
            if username not in candidate_usernames:
                candidate_usernames.append(username)
                found_this_page += 1

        print(f"  Page {page}: {len(profile_links)} researchers found, {found_this_page} new → total {len(candidate_usernames)}")
        page += 1
        time.sleep(0.5)

    except Exception as e:
        print(f"  ⚠️  Error on page {page}: {e}")
        time.sleep(5)
        page += 1

print(f"\nPhase 1 done. {len(candidate_usernames)} Business & Law candidates found.")

# ── Phase 2: fetch each profile, keep only Accounting & Finance ───────
print(f"\nPhase 2: Fetching profiles to filter for Accounting & Finance ...")

records = []

for username in candidate_usernames:
    rurl = f"https://researchers.adelaide.edu.au/profile/{username}"
    try:
        resp = requests.get(rurl, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            continue
        if len(resp.text) < 500:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        # Filter: must mention accounting or finance in school/bio
        if "accounting" not in page_text.lower() and "finance" not in page_text.lower():
            continue

        # Name: h1 or title
        name_raw = ""
        h1 = soup.find("h1")
        if h1:
            name_raw = h1.get_text(strip=True)
        name_clean = clean_name(name_raw)
        if not name_clean or len(name_clean) < 3:
            continue

        # Title / role — look for headings or labelled paragraphs near the top
        title_raw = ""
        for tag in soup.find_all(["p", "h2", "h3", "div", "span"], limit=40):
            text = tag.get_text(strip=True)
            if any(w in text.lower() for w in ["professor", "lecturer", "researcher", "dean", "fellow", "associate"]):
                if 3 < len(text) < 120:
                    title_raw = text
                    break

        level = level_from_title(title_raw) or level_from_title(name_raw)

        # ORCID
        orcid = None
        for a in soup.find_all("a", href=True):
            m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
            if m:
                orcid = m.group(1)
                break

        # Also try adelaide.edu.au/people profile for ORCID
        people_url = f"https://adelaide.edu.au/people/{username}"
        if not orcid:
            try:
                pr = requests.get(people_url, headers=HEADERS, timeout=10)
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
            "discipline": DISCIPLINE,
            "name_clean": name_clean,
            "title_raw": title_raw,
            "level": level,
            "profile_url": people_url,
            "orcid": orcid,
        })
        print(f"  ✓ {name_clean:40s}  level={level}  orcid={orcid}")
        time.sleep(0.5)

    except Exception as e:
        print(f"  ⚠️  Error fetching {username}: {e}")
        time.sleep(2)

print(f"\nPhase 2 done. {len(records)} staff found.")


# ── Load ABDC journal rankings ────────────────────────────────────────
abdc_lookup = {}
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


# ── Load Scimago journal rankings ─────────────────────────────────────
def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()

scimago_lookup = {}  # normalised title → (sjr_score, quartile, cites_per_doc_2y)
SCIMAGO_FILE = "scimago.csv"
if os.path.exists(SCIMAGO_FILE):
    try:
        with open(SCIMAGO_FILE, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                title = row.get("Title", "").strip()
                sjr   = row.get("SJR", "").replace(",", ".").strip()
                q     = row.get("SJR Best Quartile", "").strip()
                cites_key = next((k for k in row if "cites" in k.lower() and "2" in k), None)
                cites = row.get(cites_key, "").replace(",", ".").strip() if cites_key else ""
                if title and sjr:
                    try:
                        scimago_lookup[_norm(title)] = (
                            float(sjr), q or None,
                            float(cites) if cites else None
                        )
                    except ValueError:
                        pass
        print(f"Loaded {len(scimago_lookup)} Scimago journal entries")
    except Exception as e:
        print(f"Could not load Scimago data: {e}")
else:
    print("scimago.csv not found — Scimago columns will be blank")

def get_scimago(journal_name):
    if not journal_name:
        return None, None, None
    return scimago_lookup.get(_norm(journal_name), (None, None, None))


# ── Phase 3: fetch publications via OpenAlex ──────────────────────────
print("\nFetching publications via OpenAlex...")

OA_HEADERS = {"User-Agent": "monash-research-scraper/1.0 (mailto:wyuhan577@gmail.com)"}

def oa_get(url, params):
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=OA_HEADERS, timeout=20)
            if resp.status_code == 429:
                print(f"    ⏳ OpenAlex rate limit — waiting 30s...")
                time.sleep(30)
                continue
            return resp
        except Exception as e:
            print(f"    ⚠️  OpenAlex error: {e}")
            time.sleep(10)
    return None

def parse_oa_works(works):
    pubs = []
    for w in works:
        title = (w.get("title") or "").strip()
        if not title or len(title) < 5:
            continue
        year = w.get("publication_year")
        doi  = (w.get("doi") or "").replace("https://doi.org/", "").strip()
        pub_type = w.get("type") or None
        loc  = w.get("primary_location") or {}
        src  = loc.get("source") or {}
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
        sjr_score, sjr_q, cites_per_doc = get_scimago(journal_name)
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
            "scimago_quartile": sjr_q,
            "cites_per_doc_2y": cites_per_doc,
            "impact_factor": None,
        })
    return pubs

def fetch_pubs_openalex(name, orcid=None):
    pubs = []
    author_id = None
    h_index = None

    if orcid:
        resp = oa_get("https://api.openalex.org/authors",
                      {"filter": f"orcid:{orcid}", "per-page": 1})
        if resp and resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                author_id = results[0]["id"]
                h_index = (results[0].get("summary_stats") or {}).get("h_index")
        time.sleep(1)
        if not author_id:
            return [], None
    else:
        resp = oa_get("https://api.openalex.org/authors",
                      {"search": name, "per-page": 10})
        if not resp or resp.status_code != 200:
            return [], None
        results = resp.json().get("results", [])
        ADELAIDE_NAMES = {"university of adelaide", "adelaide university",
                          "university of south australia", "unisa"}
        monash_match = next(
            (r for r in results
             if any(
                 any(alias in (i.get("display_name") or "").lower() for alias in ADELAIDE_NAMES)
                 for i in (r.get("last_known_institutions") or [])
             )),
            None
        )
        if not monash_match:
            return [], None
        author_id = monash_match["id"]
        h_index = (monash_match.get("summary_stats") or {}).get("h_index")
        time.sleep(1)

    cursor = "*"
    while True:
        resp = oa_get("https://api.openalex.org/works", {
            "filter": f"authorships.author.id:{author_id}",
            "per-page": 200,
            "cursor": cursor,
            "select": "title,publication_year,doi,type,primary_location,authorships",
        })
        if not resp or resp.status_code != 200:
            break
        data  = resp.json()
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
    name  = r["name_clean"]
    if len(name.split()) < 2 or len(name) > 60:
        continue
    orcid = r.get("orcid")
    pubs, h_index = fetch_pubs_openalex(name, orcid=orcid)

    # Sanity filter
    if not orcid and len(pubs) > 200:
        pubs = []  # name-only match returning huge number → likely wrong person

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
    tag = "[ORCID]" if orcid else "[name→Adelaide]"
    print(f"  {name:40s}  {len(pubs)} pubs  h={h_index}  {tag}")
    time.sleep(3)


# ── Save CSVs ─────────────────────────────────────────────────────────
staff_file = "adelaide_staff.csv"
pubs_file  = "adelaide_publications.csv"

for r in records:
    r["name"]              = r.pop("name_clean", r.get("name_clean"))
    r["job_title"]         = r.pop("title_raw", None)
    r["academic_level"]    = r.get("level")
    r["field_of_research"] = r.pop("discipline", None)

staff_cols = ["university","name","job_title","academic_level","level_code","h_index",
              "field_of_research","profile_url"]
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

print(f"\n✅ Done!")
print(f"   {staff_file}: {len(records)} researchers")
print(f"   {pubs_file}: {len(all_pubs)} publications")
