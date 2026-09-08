"""UNSW Business School adapter.

Everything UNSW-specific lives here: the Funnelback staff directory, the
`profile-*` meta tags on each profile page, and the publication list that the
same page already carries.

Three things worth knowing about UNSW.

The staff directory is JavaScript-rendered, so the roster needs a browser. The
profile pages are not — they are plain server-rendered HTML — so everything
after the roster is ordinary `requests`, cached on disk. The roster is cached
too, in `cache/unsw_roster.json`, because it is the only part that needs
Chrome and it changes about once a semester.

UNSW publishes no ISSNs and no ORCIDs anywhere on the site. The ORCIDs are
recovered here from OpenAlex author search, because `retrieve/orcid.py`,
`retrieve/crossref.py` and `retrieve/openalex.py` all skip a researcher with
no ORCID, and without this step three of the five retrieval stages would do
nothing at all for UNSW. The ISSNs have to come from `enrich/openalex.py`.

And the publication list carries an escaped entity as a literal
`<html_ent ascii="&amp;"/>` tag inside the text, which is how "Journal of
Business Finance & Accounting" once reached the data in a form that matched
nothing in ABDC.
"""

import difflib
import html
import json
import re
import time
import unicodedata
import urllib.robotparser
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.config import CACHE_DIR, OA_HEADERS, openalex_budget
from core.http import cached_get
from core.schema import TYPES, blank_pub, clean_journal, norm_type
from core.titles import level, rank, split_prefix

UNIVERSITY = "UNSW Sydney"
ROR = "03r8z3t63"
SOURCE_NAME = "UNSW staff profile"

LISTING = "https://www.unsw.edu.au/business/our-people"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/151.0 Safari/537.36")

PAGE_SIZE = 100          # the listing accepts at least 100 per page
PAGE_TIMEOUT = 30        # seconds to wait for results to render
SETTLE = 2.0             # pause after results appear, so late cards land
PROFILE_DELAY = 1.5      # between profile fetches; robots.txt asks for 10

PROFILE_CACHE = CACHE_DIR.parent / "unsw_profiles"
ROSTER_CACHE = CACHE_DIR.parent / "unsw_roster.json"

AUTHORS_API = "https://api.openalex.org/authors"

# The two schools we keep, keyed on the exact string UNSW puts in
# `profile-school`. The directory has no school filter that survives a page
# load, so the whole Business School is paged and filtered here.
TARGET_SCHOOLS = {
    "School of Accounting, Auditing and Taxation": "Accounting",
    "School of Banking and Finance": "Finance",
}

# Education- and teaching-focused roles are excluded from rankings (FR4),
# matching the reference system's published methodology.
EXCLUDE_ROLE = re.compile(r"education[-\s]?focus|teaching[-\s]?focus", re.I)

# UNSW stacks honorifics: "Emeritus Scientia Professor Roger Simnett" and
# "Scientia Professor Ronald Masulis" are real listings. core.titles.split_prefix
# handles the ordinary forms and leaves these whole, which would put "Scientia
# Professor Ronald Masulis" in `name_clean` — the join key between staff and
# publications, and the key a merged eight-university table would match on.
#
# Modifiers are matched as a repeating group rather than spelled out as fixed
# combinations, so any ordering works.
UNSW_PREFIX = re.compile(
    r"^((?:Emeritus|Scientia|Distinguished|Adjunct|Honorary|Visiting"
    r"|Conjoint|Clinical|Professorial)\s+)*"
    r"(Associate\s+Professor|Professor|Senior\s+Lecturer|Associate\s+Lecturer"
    r"|Lecturer|Fellow|Dr|Mr|Mrs|Ms|Miss|A/Prof|Assoc\.?\s*Prof\.?|AsPr|EmPr)\.?\s+",
    re.I)

# Words that decorate a rank without being one. "Emeritus" and "Professorial"
# are deliberately absent: "Emeritus Professor" and "Professorial Fellow" are
# both real rungs on the ladder in core.titles.
DECORATION = re.compile(
    r"\b(Scientia|Distinguished|Adjunct|Honorary|Visiting|Conjoint|Clinical)\b\s*",
    re.I)


def _split_prefix(name):
    """('Emeritus Scientia Professor Roger Simnett') -> ('Roger Simnett',
    'Emeritus Professor').

    The returned prefix has the decorations removed, because core.titles.rank
    uses it as a fallback when the job title carries no rank word and looks it
    up in a table that has "Emeritus Professor" but not "Emeritus Scientia
    Professor".
    """
    match = UNSW_PREFIX.match(name or "")
    if not match:
        return split_prefix(name)
    clean = UNSW_PREFIX.sub("", name).strip()
    prefix = DECORATION.sub("", match.group(0)).strip()
    return clean, (prefix or None)


# UNSW's own publication categories, mapped onto the shared vocabulary in
# core.schema. Without this every row keeps a label like "Journal articles",
# which is not in TYPES, and export.py's `type != "Journal Article"` filter
# then drops all 1,973 of them and writes an empty file without erroring.
#
# All twenty labels UNSW uses are here, not just the common ones, for the same
# reason: an unmapped label is dropped silently.
UNSW_TYPES = {
    "journal articles": "Journal Article",
    "conference papers": "Conference Paper",
    "preprints": "Preprint",
    "book chapters": "Book Chapter",
    "books": "Book",
    "scholarly editions": "Book",
    "working papers": "Working Paper",
    "reports": "Research Report",
    "theses / dissertations": "Thesis",
    # "Media" at UNSW covers newspaper opinion pieces, radio and television.
    # Mapping the lot to "Newspaper Article" would assert something false
    # about most of them, and nothing downstream keeps either label.
    "media": "Other",
    "conference presentations": "Other",
    "conference abstracts": "Other",
    "conference posters": "Other",
    # Editing a volume is not authoring it, and the shared vocabulary has no
    # way to say "editor of". "Book" would overstate it, so these are Other.
    "edited books": "Other",
    "conference proceedings (editor of)": "Other",
    "recorded / rendered creative works": "Other",
    "creative written works": "Other",
    "software / code": "Other",
    "patents": "Other",
    "other": "Other",
}

# Labels UNSW started using that nothing here knows about. Reported at the end
# of a run rather than passed through silently, because a passed-through label
# is dropped by the export filter and the only visible symptom is a smaller
# number that still looks plausible.
UNKNOWN_TYPES = Counter()


def _type(category):
    """UNSW's label -> the shared vocabulary, falling through to norm_type."""
    if not category:
        return None
    mapped = UNSW_TYPES.get(category.strip().lower())
    if mapped:
        return mapped
    mapped = norm_type(category)
    if mapped not in TYPES:
        UNKNOWN_TYPES[category.strip()] += 1
    return mapped


# ---------------------------------------------------------------------------
# Politeness
# ---------------------------------------------------------------------------
_robots = {}


def _robots_for(url):
    host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if host not in _robots:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(urljoin(host, "/robots.txt"))
        try:
            parser.read()
        except Exception as e:
            print(f"  ! could not read robots.txt for {host} ({e}) — "
                  f"proceeding politely")
            parser = None
        _robots[host] = parser
    return _robots[host]


def may_fetch(url):
    """Fail open, as the ANU adapter does: an unreadable robots.txt is not a
    prohibition, but a readable one that says no is."""
    parser = _robots_for(url)
    return True if parser is None else parser.can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------------------
# 1. the roster (Selenium — the only part that needs a browser)
# ---------------------------------------------------------------------------
STAFF_LINK = 'a[href*="/staff/"]'
ROLE = ".card-profile__role"
TOTAL = re.compile(r"out of\s+([\d,]+)\s+results", re.I)


def _listing_url(start_rank):
    return (f"{LISTING}#search=&sort=metastaffLastName"
            f"&startRank={start_rank}&numRanks={PAGE_SIZE}")


def _card_for(anchor):
    """The block belonging to one person.

    Deliberately not selected on a card class: the results markup puts
    `card-profile` on a wrapper around *all* the cards rather than on each
    one, so selecting it returns exactly one person per page. Climbing
    outward from each staff link until the block would contain a second staff
    link finds the per-person block whatever it happens to be called.
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


def _wait_for_results(driver, By, EC, WebDriverWait):
    """Wait for the first card, then until the count stops growing.

    A fixed sleep is wrong twice over: the first card can take many seconds on
    a cold profile, and with 100 per page the rest keep arriving after it.
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


def _dismiss_cookies(driver, By, EC, WebDriverWait):
    """Decline non-essential cookies. We need the page, not to be tracked."""
    from selenium.common.exceptions import TimeoutException, WebDriverException
    for selector in ("#onetrust-reject-all-handler",
                     ".ot-pc-refuse-all-handler"):
        try:
            WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))).click()
            time.sleep(1)
            return
        except (TimeoutException, WebDriverException):
            continue


def scrape_roster(max_pages=20, verbose=True):
    """Page the whole Business School directory. Returns a list of cards."""
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-agent={USER_AGENT}")

    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=options)
    except WebDriverException as e:
        # Almost always a Chrome/ChromeDriver mismatch on this machine, not a
        # problem with the site. Say so rather than making the reader work it
        # out from a Selenium stack trace.
        raise RuntimeError(
            f"Chrome would not start: {str(e).splitlines()[0]}\n"
            f"Update Chrome and delete ~/.wdm to clear the cached driver. "
            f"If {ROSTER_CACHE} already exists, the browser is not needed.")

    found, start, page, total = {}, 1, 0, None
    try:
        while page < max_pages:
            # Load a different page first: changing only the hash does not
            # reload, and the search component reads the hash on load only.
            driver.get("https://www.unsw.edu.au/business")
            driver.get(_listing_url(start))
            if page == 0:
                _dismiss_cookies(driver, By, EC, WebDriverWait)

            try:
                rendered = _wait_for_results(driver, By, EC, WebDriverWait)
            except TimeoutException:
                print(f"    no results rendered within {PAGE_TIMEOUT}s")
                break

            soup = BeautifulSoup(driver.page_source, "html.parser")
            if total is None:
                m = TOTAL.search(soup.get_text(" ", strip=True))
                if m:
                    total = int(m.group(1).replace(",", ""))
                    if verbose:
                        print(f"    directory reports {total} people")

            new = 0
            for anchor in soup.select(STAFF_LINK):
                href = anchor.get("href", "").split("?")[0].split("#")[0]
                if "/staff/" not in href:
                    continue
                url = urljoin(LISTING, href)
                if url in found:
                    continue
                card = _card_for(anchor)
                heading = card.find(["h2", "h3", "h4"])
                role = card.select_one(ROLE)
                raw_name = (heading or anchor).get_text(" ", strip=True)
                if not raw_name:
                    continue
                found[url] = {
                    "raw_name": raw_name,
                    "card_role": role.get_text(" ", strip=True) if role else None,
                    "profile_url": url,
                }
                new += 1

            if verbose:
                print(f"    page {page + 1:>2}  startRank={start:<4} "
                      f"{rendered} rendered, +{new} new ({len(found)} total)")
            if new == 0:
                break
            start += PAGE_SIZE
            page += 1
            if total is not None and start > total:
                break
    finally:
        driver.quit()

    if total is not None and len(found) < total:
        print(f"    note: collected {len(found)} of {total}")
    return list(found.values())


def roster(refresh=False, verbose=True):
    """The roster, from cache unless asked for a fresh one.

    Cached because the browser is the slow, fragile part of this adapter and
    the roster changes about once a semester. A cached roster cannot discover
    a newly appointed academic, so that is said out loud rather than letting a
    stale list pass as a fresh one.
    """
    if ROSTER_CACHE.exists() and not refresh:
        with open(ROSTER_CACHE, encoding="utf-8") as f:
            cards = json.load(f)
        if verbose:
            print(f"  roster from cache: {len(cards)} people "
                  f"(refresh=True to re-page the directory)")
        return cards

    if not may_fetch(LISTING):
        raise RuntimeError(f"robots.txt disallows {LISTING}")

    cards = scrape_roster(verbose=verbose)
    ROSTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(ROSTER_CACHE, "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=2)
    return cards


# ---------------------------------------------------------------------------
# 2. profile pages (plain requests, server-rendered, cached as HTML)
# ---------------------------------------------------------------------------
META_FIELDS = ("profile-full-name", "profile-school", "profile-faculty",
               "profile-university-role", "profile-university-role-category")


def _profile_html(session, url, refresh=False):
    """Fetch a profile page, cached on disk so re-runs cost nothing.

    Not core.http.cached_get, which parses JSON. Same idea, same cache root.
    """
    path = PROFILE_CACHE / (url.rstrip("/").split("/")[-1] + ".html")
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")

    if not may_fetch(url):
        print(f"    robots.txt disallows {url}")
        return None
    try:
        response = session.get(url, timeout=20)
    except requests.RequestException as e:
        print(f"    ! {url}: {e}")
        return None
    time.sleep(PROFILE_DELAY)
    if response.status_code != 200:
        print(f"    ! {url}: HTTP {response.status_code}")
        return None

    PROFILE_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")
    return response.text


def _meta(soup):
    """UNSW's own profile-* meta tags. These are authoritative."""
    out = {}
    for field in META_FIELDS:
        tag = soup.find("meta", attrs={"name": field})
        out[field] = tag["content"].strip() if tag and tag.get("content") else None
    return out


# ---------------------------------------------------------------------------
# 3. ORCIDs, from OpenAlex author search
# ---------------------------------------------------------------------------
def _fold(name):
    """Normalise a personal name for comparison.

    Accents, case and punctuation are formatting, not identity: "Luis Filipe
    Goncalves-Pinto" and "Luís Filipe Gonçalves-Pinto" are one person. Word
    order is left alone, because "Li Yang" and "Yang Li" may well be two.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("-", " ").replace(".", " ").replace("'", "")
    return " ".join(text.split())


def _names_of(author):
    names = [author.get("display_name")]
    names += list(author.get("display_name_alternatives") or [])
    return [n for n in names if n]


def _rors(author):
    """Every institution OpenAlex ties this author to, not just the newest.

    `last_known_institutions` holds only the most recent, and for an academic
    that is whatever institution appeared on their latest paper — often a
    co-author's. Filtering on it alone reported 38 of UNSW's 93 as "not
    found", including people with a hundred publications on their own staff
    page. `affiliations` covers the whole career.
    """
    out = set()
    for inst in author.get("last_known_institutions") or []:
        if inst.get("ror"):
            out.add(inst["ror"].rsplit("/", 1)[-1].lower())
    for entry in author.get("affiliations") or []:
        inst = entry.get("institution") or {}
        if inst.get("ror"):
            out.add(inst["ror"].rsplit("/", 1)[-1].lower())
    return out


def add_openalex_ids(records, verbose=True):
    """Find each researcher in OpenAlex; record their ORCID and author ids.

    UNSW publishes no ORCID anywhere, and everything in retrieve/ starts with
    `if not orcid: continue`, so without this the ORCID, Crossref and OpenAlex
    retrieval steps all skip every UNSW researcher without saying so.

    THE AUTHOR ID MATTERS AS MUCH AS THE ORCID, AND FOR A DIFFERENT REASON.
    OpenAlex meters usage: a search costs $0.001 and a filter $0.0001, and the
    free daily budget is $0.10 without a key. This function makes the only
    search in the whole UNSW pipeline, once per researcher, and it is cached
    permanently. Recording the author id here means retrieve/openalex.py can
    reach the people who have no ORCID with a filter instead of another
    search — and about forty of ninety-three have no ORCID at all.

    Duplicates are kept, not discarded. OpenAlex routinely splits one person
    across several author ids and puts the ORCID on only one of them; leaving
    the others out is how you quietly lose a third of somebody's output.

    Precision before recall throughout. Attributing a stranger's paper to one
    of our researchers is far worse than missing one of theirs: a gap
    understates someone, a wrong attribution is invisible, survives review and
    corrupts every ranking built on it. So where several records at UNSW match
    the name and the ORCIDs cannot say which is the person, nothing is taken.
    """
    select = ("id,display_name,display_name_alternatives,orcid,works_count,"
              "last_known_institutions,affiliations")
    with_orcid_n = with_id_n = ambiguous = searches = 0

    for person in records:
        name = person["name_clean"]
        person["orcid"] = None
        person["openalex_author_ids"] = []

        searches += 1
        try:
            data = cached_get(AUTHORS_API,
                              params={"search": name, "per-page": 25,
                                      "select": select},
                              headers=OA_HEADERS, sleep=0.2)
        except Exception as e:
            # A failure is not an absence. Recording it as "not found" would
            # make a bad afternoon into a permanent wrong answer.
            print(f"  {name}: {type(e).__name__} {e}")
            continue

        # The institution filter is applied here rather than in the query
        # because combining `search=` with a ror filter returned nothing at all
        # against the live API, while the plain search returned the researcher.
        # Filtering what comes back is slower by nothing and cannot silently
        # return an empty set.
        here = [a for a in (data.get("results") or []) if ROR in _rors(a)]
        target = _fold(name)
        exact = [a for a in here if any(_fold(n) == target for n in _names_of(a))]
        if not exact:
            continue

        carrying = [a for a in exact if a.get("orcid")]
        if len(carrying) == 1:
            primary = carrying[0]
        elif len(carrying) > 1:
            # Several ORCIDs on records that all match the name at UNSW. That
            # is not proof of two people, but it is not proof of one either.
            ambiguous += 1
            if verbose:
                print(f"  {name}: {len(carrying)} ORCIDs at UNSW, none taken")
            continue
        elif len(exact) == 1:
            primary = exact[0]          # one record, no ORCID: still findable
        else:
            ambiguous += 1
            if verbose:
                print(f"  {name}: {len(exact)} records at UNSW and no ORCID "
                      f"to tell them apart, none taken")
            continue

        if primary.get("orcid"):
            person["orcid"] = primary["orcid"].rsplit("/", 1)[-1]
            with_orcid_n += 1
        person["openalex_author_ids"] = [
            a["id"].rsplit("/", 1)[-1] for a in exact if a.get("id")]
        with_id_n += 1

    if verbose:
        print(f"  {with_orcid_n} of {len(records)} have an ORCID, "
              f"{with_id_n} have an OpenAlex author id "
              f"({ambiguous} ambiguous, left blank)")
        print(f"  {searches} searches, about ${searches * 0.001:.3f} of today's "
              f"${openalex_budget():.2f} budget. Cached, so a re-run is free.")
    return records


# ---------------------------------------------------------------------------
# 4. publications (on the profile page we already have)
# ---------------------------------------------------------------------------
PUB_ITEM = ".publication-item"
DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.I)

# UNSW emits an escaped entity as a literal `<html_ent glyph="@amp;"
# ascii="&amp;"/>` tag inside the text. The ascii attribute holds what the
# character should have been, so use it rather than dropping the tag.
HTML_ENT = re.compile(
    r'<html_ent\b[^>]*?ascii="(?P<ascii>[^"]*)"[^>]*/?>|<html_ent\b[^>]*/?>', re.I)
SPACES = re.compile(r"\s{2,}")

# "Journal of Financial Economics, forthcoming" is a status stapled onto a
# journal name. Left there it breaks the ISSN and ABDC joins, and that
# particular row is an A* paper sitting unrated.
FORTHCOMING = re.compile(
    r"\s*[,:]\s*(forthcoming|in press|accepted|advance online|"
    r"online first|early view)\.?\s*$", re.I)

# How alike two titles must be, once normalised, for two entries sharing a DOI
# to count as one publication. Measured on the real cases: genuine repeats
# score 0.994 and 0.995 ("investor" vs "investors"), while two different
# Economic Record book reviews that share one DOI score 0.458.
TITLE_SIMILARITY = 0.90


def _text(node, selector):
    """Read one field out of the markup, decoded.

    Decoding here means no caller has to remember to. This is how "Journal of
    Business Finance & Accounting" reached the data as `Journal of Business
    Finance <html_ent .../> Accounting`, in which state it matched nothing.
    """
    found = node.select_one(selector)
    if not found:
        return None
    value = found.get_text(" ", strip=True)
    if not value:
        return None
    value = HTML_ENT.sub(lambda m: m.group("ascii") or "", value)
    return SPACES.sub(" ", html.unescape(value)).strip() or None


def _key(text):
    """Normalised comparison key for a title or journal name."""
    if not text:
        return ""
    folded = text.lower().replace("&", " and ")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"^(the|a|an)\s+", "", folded).strip()


def _split_forthcoming(journal):
    if not journal:
        return journal, False
    trimmed = FORTHCOMING.sub("", journal).strip()
    return (trimmed or journal), trimmed != journal.strip()


def publication_status(said_forthcoming, journal, volume, pages, doi):
    """Published, forthcoming, or a working paper.

    Derived from fields every university already collects, so the eight of us
    produce the same answer instead of each judging it. The client drew the
    distinction on 26 August: a preprint has not been peer reviewed, an
    accepted or forthcoming paper has.

    Note that export.py currently hardcodes this column to "published". Until
    that changes the value is carried on the record but does not reach the
    output.
    """
    name = (journal or "").lower()
    if "ssrn" in name or "arxiv" in name or "working paper" in name:
        return "working_paper"
    if said_forthcoming:
        return "forthcoming"
    if journal and doi and not (volume and pages):
        # A journal and a registered DOI, but no issue placement yet.
        return "forthcoming"
    if volume and pages:
        return "published"
    return ""


def _authors(raw):
    """UNSW separates authors with semicolons: 'Li H;  Liu L;  Masulis R'."""
    return [p.strip() for p in (raw or "").split(";") if p.strip()]


def _merge_doi_duplicates(pubs):
    """Collapse entries that share a DOI and say almost the same thing.

    The main identity key cannot catch these, because the two listings
    disagree about the journal: one says "JOURNAL OF INTERNATIONAL MONEY AND
    FINANCE", the other "Journal of International Money and Finance:
    theoretical...". A DOI identifies one article, so two entries under one
    researcher sharing a DOI are one article unless the titles say otherwise.

    They sometimes do say otherwise: Economic Record issues a single DOI for a
    batch of book reviews, and those are genuinely separate outputs. Hence the
    similarity check rather than trusting the DOI alone.
    """
    by_doi, kept = {}, []
    for pub in pubs:
        doi = (pub.get("doi") or "").strip().lower()
        if not doi:
            kept.append(pub)
            continue
        title_key = _key(pub.get("title") or "")
        twin = None
        for candidate in by_doi.get(doi, []):
            if difflib.SequenceMatcher(
                    None, title_key, candidate["_title_key"]).ratio() >= TITLE_SIMILARITY:
                twin = candidate
                break
        if twin is None:
            pub["_title_key"] = title_key
            by_doi.setdefault(doi, []).append(pub)
            kept.append(pub)
        else:
            # Keep the first but take anything it was missing: the two
            # listings are rarely equally complete, one carries the volume and
            # the other the page range.
            for field, value in pub.items():
                if value and not twin.get(field):
                    twin[field] = value
    for pub in kept:
        pub.pop("_title_key", None)
    return kept


def parse_publications(soup, person):
    """Return (pubs, unparsed) for one researcher, in the schema shape."""
    pubs, unparsed, seen = [], [], set()

    for item in soup.select(PUB_ITEM):
        category = _text(item, ".publication-category")
        year = _text(item, ".rg-year") or _text(item, ".publication-year")
        title = _text(item, ".rg-title")
        title = title.strip().strip("'‘’\"").strip() if title else None

        # Some entries carry several links. Prefer the DOI: it is the stable
        # identifier and the join key for OpenAlex. A bare "http://dx.doi.org"
        # with nothing after it is a broken link on UNSW's side, not a DOI,
        # so it is discarded rather than written out as a link going nowhere.
        links = [a["href"].strip() for a in item.select("a[href]") if a.get("href")]
        doi = doi_link = None
        for candidate in links:
            m = DOI_RE.search(candidate)
            if m:
                doi, doi_link = m.group(0).rstrip(".,;)"), candidate
                break
        others = [u for u in links if "doi.org" not in u]
        link = doi_link or (others[0] if others else None)

        if not title:
            # No structured title. Recorded rather than guessed at: silently
            # dropping these understates a researcher, silently mis-parsing
            # them is worse.
            raw = item.get_text(" ", strip=True)
            if raw:
                unparsed.append({"name": person["name_clean"],
                                 "profile_url": person["profile_url"],
                                 "type": category, "year": year,
                                 "raw_citation": raw,
                                 "reason": "no structured title on the page"})
            continue

        journal, said_forthcoming = _split_forthcoming(_text(item, ".rg-source-title"))
        volume = _text(item, ".rg-volume")
        pages = _text(item, ".rg-page")

        # What counts as the same output. Every part of this key is here
        # because of a case in the real data, and so is every part absent:
        #
        # - The title is NORMALISED. UNSW lists the same article twice with
        #   different capitalisation ("Stress tests and small business
        #   lending" / "Stress Tests and Small Business Lending").
        # - The DOI is NOT in the key. The same paper appears once with its
        #   JSTOR DOI and once with its Wiley one, and two SSRN versions of
        #   one working paper appear under two SSRN ids.
        # - The YEAR is NOT in the key. The two listings often disagree: the
        #   same JFE article is dated 2019 on one entry and 2017 on the other.
        # - The JOURNAL is in the key, so a reprint that ran in both the Goods
        #   and Services Tax Journal and the Weekly Tax Bulletin stays as two
        #   rows. The client counts those as two outputs.
        # - The TYPE is in the key, so the same title as a 2015 conference
        #   paper and a 2019 book chapter stays as two rows.
        identity = (_key(title), category, _key(journal))
        if identity in seen:
            if doi:                      # keep whichever copy carries a DOI
                for existing in pubs:
                    if existing["_identity"] == identity and not existing["doi"]:
                        existing["doi"] = doi
                        existing["link"] = link or existing["link"]
                        break
            continue
        seen.add(identity)

        record = blank_pub(
            name=person["name_clean"],
            source_id=person.get("source_id"),
            title=title,
            year=year,
            type=_type(category),
            n_authors=len(_authors(_text(item, ".rg-author"))) or None,
            authors="; ".join(_authors(_text(item, ".rg-author"))) or None,
            # UNSW publishes no ISSNs anywhere. These have to arrive from
            # enrich/openalex.py, which is why that module taking the ISSN
            # off the work is not optional for this university.
            issns=[],
            journal=clean_journal(journal),
            publisher=_text(item, ".rg-publisher"),
            doi=doi,
            link=link,
            source=SOURCE_NAME,
            # Carried beyond the base schema, as retrieve/orcid.py already
            # does with scopus_id and wos_id.
            volume=volume,
            pages=pages,
            publication_status=publication_status(
                said_forthcoming, journal, volume, pages, doi),
        )
        record["_identity"] = identity
        pubs.append(record)

    pubs = _merge_doi_duplicates(pubs)
    for pub in pubs:
        pub.pop("_identity", None)
    return pubs, unparsed


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def collect(verbose=True, refresh_roster=False):
    """Return (records, pubs) satisfying the core.schema contract."""
    cards = roster(refresh=refresh_roster, verbose=verbose)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    records, pubs, unparsed = [], [], []
    skipped_school, failed, excluded = [], [], []

    for i, card in enumerate(cards, 1):
        page = _profile_html(session, card["profile_url"])
        if page is None:
            failed.append(card["profile_url"])
            continue

        soup = BeautifulSoup(page, "html.parser")
        meta = _meta(soup)

        discipline = TARGET_SCHOOLS.get(meta["profile-school"])
        if discipline is None:
            # The directory has no school filter that survives a page load, so
            # the whole Business School is paged and the other schools are
            # dropped here rather than never fetched.
            skipped_school.append((card["raw_name"], meta["profile-school"]))
            continue

        full_name = meta["profile-full-name"] or card["raw_name"]
        job_title = meta["profile-university-role"] or card["card_role"]
        if job_title and EXCLUDE_ROLE.search(job_title):
            # Education- and teaching-focused roles are out of scope (FR4).
            excluded.append((full_name, job_title))
            continue

        # split_prefix is given the name AS PRINTED, before stripping, because
        # core.titles falls back to the prefix when the job title carries no
        # rank word. "Head of School" is a role, not a rank, and only resolves
        # to E through "Professor Noel Harding".
        clean, prefix = _split_prefix(full_name)
        title_clean = rank(job_title, prefix)

        person = {
            "university": UNIVERSITY,
            "discipline": discipline,
            "name": full_name,
            "name_clean": clean,
            "prefix": prefix,
            "title": job_title,
            "title_clean": title_clean,
            "level_code": level(title_clean),
            "profile_url": card["profile_url"],
            "source_id": None,          # UNSW has no repository author id
            "orcid": None,              # filled by add_openalex_ids
            "school": meta["profile-school"],
        }
        records.append(person)

        mine, theirs = parse_publications(soup, person)
        pubs += mine
        unparsed += theirs
        if verbose:
            print(f"  {i:>3}/{len(cards)}  {clean:<34} {discipline:<10} "
                  f"{str(person['level_code'] or '?'):<3} {len(mine):>4} pubs"
                  + (f"  ({len(theirs)} unparsed)" if theirs else ""))

    if not records:
        raise RuntimeError(
            "No one matched the two target schools. Check that the names in "
            "TARGET_SCHOOLS still match what UNSW publishes in profile-school.")

    if UNKNOWN_TYPES:
        # Loud, because the symptom otherwise is a smaller export that still
        # looks plausible.
        print("\n  ! UNSW publication categories this adapter does not map:")
        for label, n in UNKNOWN_TYPES.most_common():
            print(f"      {n:>5}  {label!r}")
        print("    Add them to UNSW_TYPES. Until then they are dropped by the")
        print("    export's journal-article filter without erroring.")

    if verbose:
        print(f"\n  {len(records)} staff, {len(pubs)} publications")
        if skipped_school:
            print(f"  {len(skipped_school)} skipped, not in the two schools")
        if excluded:
            print(f"  {len(excluded)} excluded as education-focused")
        if unparsed:
            print(f"  {len(unparsed)} publication entries had no structured "
                  f"title and were not guessed at")
        undated = Counter(x["type"] for x in pubs if not x.get("year"))
        if undated:
            # The contract requires a year, so these are reported by the
            # contract check too. They are not a parsing failure: UNSW's
            # `.publication-year` span is present and empty on these entries,
            # and there is no year anywhere else in the markup to fall back
            # to. Almost all are SSRN preprints, which the export drops.
            print(f"  {sum(undated.values())} have no year, because UNSW's "
                  f"listing carries none: "
                  + ", ".join(f"{n} {k}" for k, n in undated.most_common()))
        if failed:
            print(f"  {len(failed)} profile pages could not be fetched")

    print("\n  openalex author lookup (orcid and author ids)")
    add_openalex_ids(records, verbose=verbose)

    return records, pubs
