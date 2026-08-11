"""
ANU Accounting & Finance researcher scraper — CITS3200 Team 20
================================================================
Assigned university: ANU. Assigned to: Jamie.

WHAT THIS DOES
--------------
1. Walks the two ANU College of Business & Economics staff directories that
   cover accounting and finance:
       - Research School of Accounting (RSA)                -> rsa.anu.edu.au
       - Research School of Finance, Actuarial Studies      -> rsfas.anu.edu.au
         & Statistics (RSFAS)  [we keep only the Finance area]
2. For each *Academic* staff member, opens their profile page and extracts the
   free-text "Publications" block if present.
3. Best-effort parses each publication paragraph into
   title / journal / year / co-authors / ABDC-rating / DOI / url.
4. Writes two CSVs and two JSONs to ./output using the field names from the
   project's Scope of Work data dictionary (section 3.5.4), so the output loads
   straight into the shared database with no reshaping:
       - anu_staff.(csv|json)
       - anu_publications.(csv|json)
   Plus anu_unparsed_publications.csv (audit trail of lines the parser was not
   confident about) and anu_no_publications.csv (academics with no usable
   Publications section — a known coverage limitation, logged not dropped).

WHY THIS SOURCE (not the Pure research portal)
----------------------------------------------
researchportalplus.anu.edu.au (Elsevier Pure) is the cleaner structured source
but is protected by enterprise-grade bot detection. We deliberately do NOT try
to defeat that (ethics + the project's own robots.txt commitment). The RSA/RSFAS
profile pages are static HTML and publicly served, so plain requests works.

KNOWN LIMITATIONS (feed the Risk Register / Skills & Resources Audit)
---------------------------------------------------------------------
- Publications are hand-written prose per academic, so formatting VARIES between
  schools and between individuals. RSA staff tend to write
      "Title. Journal. (Year) (co-authors) (ABDC A*)"
  RSFAS staff tend to write
      "Title [as a hyperlink] with co-authors, Journal Vol: pages, Year."
  and some academics on both schools write quoted-title citation styles
  ("Authors YEAR, 'Title', Journal, vol. X, no. Y, pp. Z-Z."). Article URLs
  and DOIs are read from the actual <a href> links in each publication
  paragraph rather than guessed from visible text, since titles are often
  hyperlinked with no visible URL at all. The parser handles these shapes
  heuristically and logs anything it is unsure of rather than guessing. Do
  not treat parsed fields as authoritative without a spot-check.
- Some academics (e.g. those who only link out to the Pure portal) have NO inline
  Publications section. Those people are captured in anu_staff but produce zero
  publications here. This is an inherent gap of the profile-page approach.
- ABDC ratings shown inline are SELF-REPORTED by each academic. They are captured
  as a hint only; the real join against the official ABDC list happens later in
  the pipeline (FR5) and is authoritative.
- Category filter keeps ONLY the "Academic" tag (excludes Professional staff,
  Research student, Visitor/Honorary), on the view that the Scope of Work's
  FR1 targets "academics" specifically, not students. Flag it if the team
  wants students included.

USAGE
-----
    pip install requests beautifulsoup4
    python anu_scraper.py

Politeness: sends a normal browser User-Agent, checks robots.txt once per host,
and sleeps REQUEST_DELAY seconds between requests.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from html import unescape as _unescape
import urllib.robotparser
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

UNIVERSITY = "Australian National University"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# A normal browser UA. ANU's CMS 403s obviously-bot user-agents.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

REQUEST_DELAY = 1.0   # seconds between requests — be a good citizen
TIMEOUT = 20
MAX_DIRECTORY_PAGES = 25  # safety cap on pagination

# Each "source" = one school directory. We record which discipline(s) it covers
# so we can tag researchers, and (for RSFAS) which area value to keep.
SOURCES = [
    {
        "school": "RSA",
        "base": "https://rsa.anu.edu.au",
        "directory_path": "/about/staff-directory",
        "default_field": "Accounting",
        # RSA has no area filter; treat everyone academic as accounting/finance.
        "keep_areas": None,
    },
    {
        "school": "RSFAS",
        "base": "https://rsfas.anu.edu.au",
        "directory_path": "/about/people",
        # The two query params below explicitly set both on-page filters to
        # "Any". Without them the endpoint can return an empty result set
        # even though the visible form defaults to "Any" already — passing
        # the same state back explicitly is more reliable than depending on
        # the site to apply its own default. Left off RSA, whose directory
        # has no equivalent filter form.
        "directory_query": "field_type_of_staff_target_id=All&field_area_of_expertise_target_id=All",
        "default_field": "Finance",
        # RSFAS also contains Statistics & Actuarial staff we do NOT want.
        # Keep only people whose area tag mentions finance. (Actuarial studies
        # is adjacent to finance but out of the project's accounting/finance
        # scope — left out by default; flip this set if the team decides
        # otherwise.)
        "keep_areas": {"finance"},
    },
]

# Academic-level mapping (job_title -> A–E). Order matters: more specific
# titles must be checked BEFORE more general ones, since we return on the
# first match. "Associate Professor" is checked before plain "Professor"
# deliberately — a trailing negative-lookahead can't exclude "Associate
# Professor" from matching the bare "professor" pattern, because "Associate"
# sits BEFORE "Professor" in the phrase rather than after it, so ordering is
# the only reliable way to keep the two apart.
LEVEL_LADDER = [
    ("E", r"emeritus|distinguished|deputy vice-chancellor"),
    ("D", r"associate professor|reader|a/prof|assoc\.?\s*prof"),
    ("E", r"\bprofessor\b"),  # plain "Professor" only reaches here if the
                              # more specific "associate professor" above
                              # didn't already match
    ("C", r"senior lecturer|senior research fellow"),
    ("B", r"\blecturer\b|research fellow|postdoctoral"),
    ("A", r"associate lecturer|assistant lecturer|tutor"),
]

TITLE_PREFIX = re.compile(
    r"^(Emeritus Professor|Distinguished Honorary Professor|Distinguished Professor"
    r"|Honorary Associate Professor|Associate Professor|Assistant Professor"
    r"|Professor|Senior Lecturer|Lecturer|Dr|Mr|Mrs|Ms|Miss|A/Prof|Prof"
    r"|Assoc\.?\s*Prof\.?)\.?\s+",
    re.IGNORECASE,
)

# Category tags seen on ANU directory cards.
ACADEMIC_CATEGORY = "academic"


# ----------------------------------------------------------------------------
# Data structures — field names taken from the Scope of Work data dictionary
# ----------------------------------------------------------------------------

@dataclass
class Researcher:
    name: str
    job_title: str
    academic_level: str | None
    field_of_research: str          # "Accounting" or "Finance"
    profile_url: str
    university: str = UNIVERSITY
    research_portal_url: str | None = None  # Pure link if present (bonus)


@dataclass
class Publication:
    researcher_name: str            # links back to Researcher (name + profile_url)
    researcher_profile_url: str
    title: str | None
    journal_name: str | None
    year: int | None
    doi: str | None
    article_url: str | None
    abdc_self_reported: str | None  # e.g. "A*", "A" — hint only, not authoritative
    coauthors: str | None
    source: str = "ANU RSA/RSFAS profile page"
    citation_percentile: float | None = None  # filled later in pipeline (OpenAlex)
    raw: str = ""                   # original paragraph, for auditing


# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _robots_ok(url: str) -> bool:
    """Check robots.txt once per host; fail open only if robots.txt is missing."""
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    rp = _robots_cache.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(host + "/robots.txt")
        try:
            rp.read()
        except Exception:
            # If robots.txt can't be read, be conservative but don't hard-stop:
            # treat as allowed, since these are public profile pages.
            rp = None
        _robots_cache[host] = rp
    if rp is None:
        return True
    return rp.can_fetch(HEADERS["User-Agent"], url)


def get(url: str) -> requests.Response | None:
    if not _robots_ok(url):
        print(f"  [robots] disallowed, skipping: {url}", file=sys.stderr)
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"  [error] {url}: {e}", file=sys.stderr)
        return None
    time.sleep(REQUEST_DELAY)
    if r.status_code != 200:
        print(f"  [http {r.status_code}] {url}", file=sys.stderr)
        return None
    return r


# ----------------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    """Strip a leading academic/courtesy title from a display name."""
    return TITLE_PREFIX.sub("", raw).strip()


def extract_title_prefix(raw_name: str) -> str | None:
    """
    Return the courtesy/academic prefix from a raw display name, e.g.
    "Professor Juliana Ng" -> "Professor". Used as a fallback for
    academic_level when job_title doesn't state a rank at all — this happens
    for senior staff whose job_title field is an admin role (e.g. "Director,
    Research School of Accounting") rather than their academic rank, even
    though the rank itself is visible as a prefix on their name.
    """
    m = TITLE_PREFIX.match(raw_name or "")
    return m.group(1).strip() if m else None


def academic_level(job_title: str) -> str | None:
    t = (job_title or "").lower()
    for level, pattern in LEVEL_LADDER:
        if re.search(pattern, t):
            return level
    return None


YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# A parenthesised year, e.g. "(2018)" — the boundary for the "Authors (Year)
# Title" citation shape, which has no "with" keyword separating authors from
# title at all.
YEAR_PAREN_RE = re.compile(r"\(((?:19|20)\d{2}[a-z]?)\)")
# A bare year followed by a comma, e.g. "..., N 2014, Audit Reports..." or
# "...M. Wilson, 2024, Are Individual...". Different from LEADING_YEAR_RE
# below: this one is NOT anchored to the start of the text, so everything
# before it is reliably just the author list — a clean boundary, unlike the
# ambiguous case where the year opens the whole citation.
YEAR_COMMA_RE = re.compile(r"\s((?:19|20)\d{2}[a-z]?)\s*,\s*")
# A bare year right at the start, e.g. "2023. Author, Author. Title" (no
# parentheses). Unlike the parenthesised form, there's no clean boundary
# between the author list and the title here, so this is used to FLAG the
# shape for review rather than attempt to split it.
LEADING_YEAR_RE = re.compile(r"^\s*(19|20)\d{2}[a-z]?\.?\s")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ABDC_RE = re.compile(r"ABDC\s*([A-C]\*?|A\*|A|B|C)", re.IGNORECASE)
# Splits on ". " to separate title from journal (used when no italicised
# journal name is available) — but NOT after a set of common academic
# abbreviations (vol., pp., p., no., ed., eds., et al., trans., suppl., ch.),
# each of which ends in a period followed by a space and would otherwise be
# mistaken for the real sentence break. Also excludes a period after a
# single capital letter (an author's initial, e.g. "A. Jiang"), for the same
# reason.
# NOTE: the abbreviation checks are individually scoped case-insensitive
# with (?i:...) rather than a global IGNORECASE flag — a global flag would
# make [A-Z] match lowercase too, which would wrongly protect the period at
# the end of nearly every normal sentence (they almost all end in a
# lowercase letter). Each lookbehind below is individually fixed-width,
# which Python's re allows even though they're all different lengths.
SENTENCE_SPLIT_RE = re.compile(
    r"(?<!\b(?i:vol))(?<!\b(?i:no))(?<!\b(?i:pp))(?<!\b(?i:p))"
    r"(?<!\b(?i:ed))(?<!\b(?i:eds))(?<!\b(?i:et al))"
    r"(?<!\b(?i:trans))(?<!\b(?i:suppl))(?<!\b(?i:ch))"
    r"(?<!\b[A-Z])\.\s+"
)
# NOTE: journal names are NOT detected via an asterisk regex on flattened
# text — get_text() strips <i>/<em> tags to plain text with no marker left
# behind. Journal names come from the real italic runs captured in
# extract_publications_block() instead.
# RSA-style co-authors appear in a parenthesised "(with ...)" clause, which is
# unambiguous to extract (bounded by the parens). RSFAS-style bare "with ..."
# co-authors are extracted by position relative to the journal name instead
# (see parse_publication) — a regex boundary here would break on author
# initials like "D. Duffie", so we don't attempt one.
COAUTHOR_PAREN_RE = re.compile(r"\(with ([^)]+)\)", re.IGNORECASE)

# A bare year followed by a PERIOD (not a comma) then a capital letter, e.g.
# "Wee, M., 2021. Busy directors and firm performance...". Same idea as
# YEAR_COMMA_RE (an unambiguous author/title boundary because it's not at the
# very start of the text) but for the period-separated variant. Boundary
# candidates from all three year patterns (paren/comma/period) are resolved
# leftmost-first rather than by a fixed preference order, since a
# parenthesised year can legitimately appear LATER in the text as an
# in-title citation to someone else's paper (e.g. "...a replication and
# extension of Hauser (2018)") — matching that instead of the real, earlier
# boundary would discard the true title.
YEAR_PERIOD_RE = re.compile(r"\s((?:19|20)\d{2}[a-z]?)\.\s+(?=[A-Z])")

# Many RSA/RSFAS academics quote the title verbatim: 'Title' or "Title" or
# curly-quoted variants. This shows up in a few orderings:
#   - Authors YEAR, 'Title', Journal, vol. X, no. Y, pp. Z-Z.
#   - 'Title', with Authors, YEAR, Journal ...
#   - 'Title' (with Authors), [Vol X, Issue Y,] YEAR, Journal
# The double-quote family is tried first since a straight single-quote is
# also an ordinary apostrophe, which shows up INSIDE plenty of real titles
# (e.g. "Funds' Holdings", "Schatzki's Social Site Ontology") and would
# truncate the match early if tried first. Both patterns need a lookahead
# constraining what can follow the closing mark, or an internal
# apostrophe/quote inside the title gets mistaken for the real close.
# Single-quote is the strict case: only a following comma/paren/end-of-string
# is accepted, because a plural possessive like "Individuals'" is routinely
# followed by an ordinary title-case word (e.g. "Individuals' Intention..."),
# which would otherwise look just as valid as a real closing quote.
# Double-quote doesn't have that collision risk (a bare " isn't used as an
# apostrophe), so its lookahead can also accept a following capitalised word
# or a period, with no comma required at all — needed for citations that
# drop the comma between title and journal entirely, or that end the quoted
# title with an ordinary sentence period before the venue name.
DOUBLE_QUOTE_TITLE_RE = re.compile(r'["“](.{8,300}?)["”](?=\s*(?:[,(.]|[A-Z0-9]|with\b|$))')
SINGLE_QUOTE_TITLE_RE = re.compile(r"['‘](.{8,300}?)['’](?=\s*(?:[,(]|with\b|$))")
# Authors immediately before the quoted title, ending in the publication
# year — e.g. "Fargher, N & Wee, M 2019, ". Not anchored at the start of the
# search text, so this only matches near the END of the prefix. The optional
# parens around the year (\(?...\)?) handle the year itself being
# parenthesised right before the quote, e.g. "...and C. Yu, (2016) 'Title'".
PREFIX_AUTHORS_YEAR_RE = re.compile(r"\(?((?:19|20)\d{2}[a-z]?)\)?\s*,?\s*$")
JOURNAL_SKIP_RE = re.compile(
    r"^(vol\.?|no\.?|pp?\.?|issue|forthcoming|in press|mimeo|working paper"
    r"|spring|summer|fall|autumn|winter)\b",
    re.IGNORECASE,
)


# A book chapter's journal-equivalent is really an edited volume, cited as
# "in Editor Name (ed.), Book Title" — the "in ... (ed.)," clause isn't part
# of the title/journal itself. The optional leading comma handles this
# clause sitting right after a quoted title, e.g. "'Business', in Gabriele
# Bammer (ed.), Dealing with..." — without allowing that leading comma, the
# anchored pattern would never get a chance to match at all.
IN_EDITOR_PREFIX_RE = re.compile(r"^\s*,?\s*in\s+[^,]+\(eds?\.?\)\s*,\s*", re.IGNORECASE)
# Where a bare "with" genuinely introduces a co-author clause: right at the
# START of the text being searched (give or take a leading comma/period),
# matching every real case seen ("'Title', with Authors...", "'Title' with
# Authors..."). Deliberately NOT a search for "with" anywhere in the string
# — "with" is an ordinary English word that shows up constantly INSIDE real
# title/venue text with no co-author meaning at all (e.g. a book titled
# "Dealing with Uncertainties in Policing Serious Crime", or a title reading
# "Accounting for financial instruments with characteristics of debt and
# equity") — matching an in-text "with" instead of the real clause-opening
# one truncates the title at the wrong point.
BARE_WITH_RE = re.compile(r"^[\s,.]*with\s+", re.IGNORECASE)
# Where a bare (unparenthesised) "with ..." co-author clause ends: either a
# comma immediately followed by the publication year (co-authors are simply
# comma-separated up to that point), or a sentence period before the journal
# name starts. Splitting only at the FIRST comma breaks on 3+ co-authors
# joined by "and" before a period (e.g. "with Xuejun Jiang, Jeong-Bong Kim
# and Yangxin Yu. Journal of Accounting..."). The negative lookbehind on the
# period alternative excludes single-capital-letter initials (e.g.
# "F. Moshirian") for the same reason SENTENCE_SPLIT_RE does above.
WITH_CLAUSE_END_RE = re.compile(r",\s*(?:19|20)\d{2}[a-z]?\b|(?<!\b[A-Z])\.\s+(?=[A-Z])")


def _split_bare_with_clause(text_after_with: str) -> tuple[str, str]:
    """
    Given the text right after a bare (non-parenthesised) "with", return
    (coauthors, remainder-for-journal-lookup). See WITH_CLAUSE_END_RE.
    """
    m = WITH_CLAUSE_END_RE.search(text_after_with)
    if not m:
        return text_after_with.strip(" ,"), ""
    authors = text_after_with[:m.start()].strip(" ,")
    # A comma-year boundary keeps the year attached to the remainder, so the
    # year-stripping step in _extract_journal_from_suffix still finds it; a
    # period boundary just moves past the period into the journal text.
    rest = text_after_with[m.start():] if m.group(0).startswith(",") else text_after_with[m.end():]
    return authors, rest


def _clean_journal_tail(journal: str) -> str | None:
    """
    Strip trailing cruft glued onto an otherwise-correct journal name with
    no comma to separate it: a bare volume/page run (e.g. "Journal of Fixed
    Income 35: 1067-1101" -> "...Fixed Income") or a URL (already captured
    separately as doi/article_url, so it doesn't need to live here too).
    """
    journal = re.sub(r"\s*https?://\S+\s*$", "", journal)
    journal = re.sub(r"\s+\d+\s*[:,]?\s*[\d\-–,\s]*$", "", journal)
    return journal.strip(" .,;:") or None


def _pick_journal_segment(parts: list[str]) -> str | None:
    for part in parts:
        part = part.strip(" .")
        if not part or JOURNAL_SKIP_RE.match(part):
            continue
        if re.fullmatch(r"[\d\s\-–:]+", part):
            continue
        cleaned = _clean_journal_tail(part)
        if cleaned:
            return cleaned
    return None


def _extract_journal_from_suffix(suffix: str) -> str | None:
    """
    Best-effort journal name from the text AFTER a quoted title (see
    DOUBLE_QUOTE_TITLE_RE / SINGLE_QUOTE_TITLE_RE above). Strips co-author
    and volume/issue/page clutter, then takes the first remaining
    comma-separated segment that isn't itself just a vol/no/pp/date marker.
    """
    s = re.sub(r"\(with [^)]*\)", "", suffix, flags=re.IGNORECASE)
    bare_with_m = BARE_WITH_RE.match(s)
    if bare_with_m:
        _, rest = _split_bare_with_clause(s[bare_with_m.end():])
        s = s[:bare_with_m.start()] + rest
    s = IN_EDITOR_PREFIX_RE.sub("", s)
    s = re.sub(r"\([^)]*\)", "", s)  # drop remaining parentheticals (ABDC, issue no.)
    s = re.sub(r"\b(19|20)\d{2}[a-z]?\b", "", s)  # drop year token(s)
    # Split on BOTH comma and ". " — some suffixes have no comma between the
    # title-closing sentence and the venue name at all, only a plain period
    # (e.g. "...JORC Code". Centre for International Finance and Regulation
    # (CIFR). E213."). A comma-only split can leave a trailing report number
    # attached to the journal candidate in that case, since separating that
    # also requires noticing the internal ". ". Splitting every comma
    # segment on ". " as well is safe for segments that don't contain one at
    # all (a no-op) and, for the ones that do, only ever narrows the
    # candidate further — the genuine journal name still ends up as one of
    # the resulting pieces either way.
    segments = []
    for part in s.split(","):
        segments.extend(part.split(". "))
    return _pick_journal_segment(segments)


def _extract_coauthors_from_suffix(suffix: str) -> str | None:
    """Best-effort co-author list from text after a quoted title or journal."""
    m = re.search(r"\(with ([^)]+)\)", suffix, re.IGNORECASE)
    if m:
        return m.group(1)
    m = BARE_WITH_RE.match(suffix)
    if m:
        authors, _ = _split_bare_with_clause(suffix[m.end():])
        return authors or None
    return None


def _parse_quoted_title(text: str) -> tuple[str, str | None, str | None] | None:
    """
    Try the "quoted title" citation shape (see DOUBLE_QUOTE_TITLE_RE above).
    Returns (title, journal, coauthors) once a quoted title AND a structural
    signal (authors+year before it, or "with"/a year after it) are found —
    journal may come back None (e.g. an unpublished working paper has no
    journal to find), but the title/coauthors are still trustworthy at that
    point and are returned rather than discarded. A confidently-quoted title
    with no journal is downstream marked low-confidence for manual review
    rather than trusted outright, same as any publication missing a journal.
    Only a genuine absence of both a quote AND any structural signal returns
    None, so the caller correctly falls back to the older prose heuristics
    on text that doesn't match this shape at all.
    """
    quote_m = DOUBLE_QUOTE_TITLE_RE.search(text) or SINGLE_QUOTE_TITLE_RE.search(text)
    if not quote_m:
        return None
    title = quote_m.group(1).strip()
    prefix, suffix = text[:quote_m.start()], text[quote_m.end():]

    # Layout 1: "Authors YEAR, 'Title', Journal..." — authors+year sit
    # immediately before the quote.
    prefix_m = PREFIX_AUTHORS_YEAR_RE.search(prefix)
    if prefix_m and prefix[:prefix_m.start()].strip(" ,"):
        journal = _extract_journal_from_suffix(suffix)
        coauthors = prefix[:prefix_m.start()].strip(" ,") or None
        return title, journal, coauthors

    # Layout 2: "'Title', with Authors, YEAR, Journal..." or
    # "'Title' (with Authors), YEAR, Journal..." — title comes first.
    if YEAR_RE.search(suffix) or re.search(r"\bwith\b", suffix, re.IGNORECASE):
        journal = _extract_journal_from_suffix(suffix)
        coauthors = _extract_coauthors_from_suffix(suffix)
        return title, journal, coauthors

    return None


# Section-heading / navigation lines that occasionally land inside a
# Publications block but aren't citations at all — e.g. "Selected
# Publications", "Book Chapter", "View ORCID profile". Matched as a whole
# line (ignoring surrounding punctuation/case) so they're dropped before
# parsing rather than logged as low-confidence "publications". Kept as an
# explicit denylist rather than a generic short-line heuristic, since some
# real (if incomplete) publication titles are just as short as these
# headings (e.g. a bare two- or three-word title with no journal attached).
NON_PUBLICATION_HEADINGS = {
    "publication", "publications",
    "selected publications", "selected refereed journal articles",
    "refereed journal articles", "refereed journal publications",
    "recent referred journal publications", "recent refereed journal publications",
    "book chapter", "book chapters",
    "phd thesis", "working paper", "working papers", "selected working papers",
    "for more publications", "complete list available at cv",
    "view orcid profile", "view ocird profile", "google scholar",
    "publications include", "industry commissioned reports",
    "selected peer-reviewed conference papers", "peer-reviewed conference papers",
}
# Same idea as NON_PUBLICATION_HEADINGS, but matched as a SUBSTRING instead
# of the whole line — some navigation notes have other text glued on (e.g. a
# date range prefix), so a whole-line match would miss them. Without this,
# list-navigation text like "click titles to access at publisher site..."
# would get parsed as if it were a publication itself, with the note text
# landing in the title/journal fields.
NON_PUBLICATION_SUBSTRINGS = (
    "click titles to access",
    "also see anu researcher profile",
)


def parse_publication(block: dict, researcher: Researcher) -> tuple[Publication | None, bool]:
    """
    Best-effort parse of one publication block ({"text": ..., "links": [...]}).
    Returns (Publication, confident?).  'confident' is False when we had to
    guess or couldn't find a title/journal — those get logged for review.
    """
    raw_text = block["text"]
    links = block.get("links", [])
    text = " ".join(raw_text.split())  # normalise whitespace
    if len(text) < 12:
        return None, False

    year_m = YEAR_RE.search(text)
    year = int(year_m.group(0)) if year_m else None

    # DOI: prefer a real doi.org link if the paragraph has one — far more
    # reliable than pattern-matching visible text, and works even when the
    # DOI is never shown as plain text (it's just a hyperlink on other words).
    doi = None
    doi_href = next((href for _, href in links if "doi.org" in href.lower()), None)
    if doi_href:
        m = re.search(r"doi\.org/(.+)$", doi_href, re.IGNORECASE)
        doi = m.group(1).rstrip(".)") if m else None
    if not doi:
        doi_m = DOI_RE.search(text)
        doi = doi_m.group(0).rstrip(".)") if doi_m else None

    # article_url: prefer the first real link on the paragraph (usually the
    # title itself, or a "link"/"internet appendix" link); fall back to a
    # plain-text URL if no href was found at all.
    article_url = links[0][1] if links else None
    if not article_url:
        url_m = re.search(r"https?://[^\s)\]]+", raw_text)
        article_url = url_m.group(0) if url_m else None

    abdc_m = ABDC_RE.search(text)
    abdc = abdc_m.group(1).upper() if abdc_m else None

    # Find a real italic journal name from the DOM (see extract_publications_block)
    # and locate exactly where it sits in `text`. Knowing its position lets us
    # split title/co-authors by POSITION rather than by guessing at periods —
    # which is what breaks on initials like "D. Duffie and Y. Zhu."
    journal = None
    journal_pos: tuple[int, int] | None = None
    for raw_it in block.get("italics", []):
        if raw_it and raw_it in text:
            idx = text.index(raw_it)
            journal, journal_pos = raw_it, (idx, idx + len(raw_it))
            break

    title = None
    coauthors = None
    confident = True

    if journal_pos:
        before = text[:journal_pos[0]]
        quote_m = DOUBLE_QUOTE_TITLE_RE.search(before) or SINGLE_QUOTE_TITLE_RE.search(before)
        if quote_m:
            # The title is ALSO explicitly quoted, even though a journal was
            # found via italics — take it directly rather than running it
            # through the comma/year boundary logic below, which has no
            # concept of quote marks and can otherwise swallow the quote
            # characters and a trailing "with ..." co-author clause into the
            # title wholesale.
            title = quote_m.group(1)
            coauthors = (_extract_coauthors_from_suffix(before[quote_m.end():])
                         or _extract_coauthors_from_suffix(before[:quote_m.start()]))
        # Check the bare-leading-year pattern next, before hunting for "with".
        # Reason: "with" can appear as an ordinary word INSIDE the title
        # itself (e.g. "Fine-grained entity typing with a type taxonomy...")
        # — searching for "with" first would wrongly cut the title in half
        # there. A title starting with a bare year is a much more specific,
        # reliable signal that this is the ambiguous "Year. Authors. Title"
        # shape, which should be flagged rather than parsed by guessing.
        elif LEADING_YEAR_RE.match(before):
            title = before
            confident = False
        else:
            paren_with_m = COAUTHOR_PAREN_RE.search(before)
            with_m = re.search(r"\bwith\b", before, re.IGNORECASE)
            # Pick whichever year-boundary candidate occurs FIRST (leftmost)
            # in `before`, rather than always preferring one pattern over
            # another. A parenthesised year can legitimately appear later in
            # the text as an in-title citation to someone ELSE's paper (e.g.
            # "...a replication and extension of Hauser (2018)") — matching
            # that instead of the real, earlier boundary would discard the
            # true title. Leftmost-wins is correct generally, since the real
            # author/title boundary is always the FIRST year marker to
            # appear, not necessarily any specific pattern type.
            boundary = _find_year_boundary(before)
            if boundary:
                after_boundary = before[boundary.end():]
                candidate = before[:boundary.start()]
                after_stripped = after_boundary.strip(" ,.;:-")
                # "Title (Year), Journal" shape — nothing meaningful between
                # the year boundary and the journal name, so the TITLE is
                # what comes BEFORE the boundary instead (the usual case has
                # it after), e.g. "Manager Narcissism... Behavior (2023),
                # Contemporary Accounting Research". A bare "39(3)"-style
                # volume(issue) fragment counts as trivial too, not just an
                # empty gap — a legal-citation style ('Title' (YEAR)
                # VOLUME(ISSUE) Journal) leaves exactly that between the
                # year and the journal. Gated on length so a short author
                # list before a trivial gap (e.g. a citation with no
                # distinct article title at all, just author names then a
                # book title) doesn't get mistaken for a title — 40 chars
                # comfortably separates author-name lists from real titles
                # across the profiles this was checked against, which all
                # run 60+ characters.
                is_trivial = not after_stripped or re.fullmatch(r"\d+(\(\d+\))?", after_stripped)
                if is_trivial and len(candidate.strip()) > 40:
                    title = candidate
                else:
                    coauthors = candidate
                    title = after_boundary
            elif paren_with_m:
                # A parenthesised "(with ...)" clause bounds its own extent,
                # so splitting the title there (rather than at a bare word
                # "with" below) can't land INSIDE the parens and produce
                # dangling unbalanced punctuation. Checked before the bare
                # "with" fallback so a genuine "(with ...)" clause always
                # wins over a coincidental "with" appearing earlier in the
                # title itself (both can appear in the same citation).
                title = before[:paren_with_m.start()]
                coauthors = paren_with_m.group(1)
            elif with_m:
                title = before[:with_m.start()]
                coauthors = before[with_m.end():]
                # strip trailing publication-status words that sit right before
                # the journal name and aren't part of the author list, e.g.
                # "... Zhu. Forthcoming, American Economic Review" -> coauthors
                # should stop at "Zhu", not include "Forthcoming".
                coauthors = re.sub(
                    r"\b(Forthcoming|In press|Working paper|Mimeo)\b[.,]?\s*$",
                    "", coauthors, flags=re.IGNORECASE,
                )
            elif not before.strip(" ,.;:-"):
                # `before` is empty/trivial — the italic run IS the title
                # (e.g. a book cited by its own title, with no separate
                # journal), not a journal name attached to a preceding
                # title. Co-authors for this shape are usually in a
                # trailing "(...)" or "(with ...)" clause after the italic
                # run, picked up by the after-journal fallback below.
                title = journal
                journal = None
            else:
                title = before
            # Co-authors sometimes sit AFTER the journal name instead of
            # before it, e.g. "Title (2022), Journal (with Mahama, H.)
            # (ABDC - A)". Only used as a fallback so it never overrides a
            # co-author list already found before the journal.
            if coauthors is None:
                coauthors = _extract_coauthors_from_suffix(text[journal_pos[1]:])
    else:
        # No italic journal found. Try the "quoted title" citation shape
        # first (see _parse_quoted_title) — common on profiles that write
        # citations as plain prose with the title in quotes rather than
        # italics. Only used when it can confidently isolate both a title
        # and a journal; otherwise falls through to the older heuristics
        # below rather than guessing.
        quoted = _parse_quoted_title(text)
        if quoted:
            title, journal, coauthors = quoted
            if journal is None:
                # A confidently-quoted title with no journal is very
                # plausibly a working paper (no venue yet) rather than a
                # parser miss — still worth flagging for review rather than
                # trusting as-is, same as any other publication missing a
                # journal.
                confident = False
        else:
            # RSA-style: co-authors are usually in a parenthesised
            # "(with ...)" clause instead of a bare "with ...". Strip that
            # clause out (it's unambiguous, no initials-period problem since
            # it's bounded by the parentheses) before splitting on ". ".
            co_paren = COAUTHOR_PAREN_RE.search(text)
            text_clean = text
            if co_paren:
                coauthors = co_paren.group(1)
                s, e = co_paren.span()
                text_clean = re.sub(r"\s+", " ", text[:s] + " " + text[e:]).strip()
            else:
                # A bare author list in parens with no "with" at all, e.g.
                # "(Nhan Le, Duc Duy Nguyen and Vathunyoo Sila)".
                bare_paren_m = BARE_AUTHOR_LIST_PAREN_RE.search(text_clean)
                if bare_paren_m:
                    coauthors = bare_paren_m.group(1)
                    s, e = bare_paren_m.span()
                    text_clean = re.sub(r"\s+", " ", text_clean[:s] + " " + text_clean[e:]).strip()

            # "Accepted on/by/for publication in JOURNAL" — a recognisable
            # phrase for forthcoming work with no other journal signal at
            # all (no italics, no quotes). Checked before the blind
            # ". "-splitter below, which would otherwise grab an unrelated
            # phrase (e.g. an award mention) as the "journal".
            accepted_m = ACCEPTED_JOURNAL_RE.search(text_clean)
            if accepted_m:
                title = text_clean[:accepted_m.start()]
                journal = accepted_m.group(1).strip()
            else:
                # Find where the author list ends via the same year-boundary
                # logic the italics branch uses above, and only run the
                # blind ". "-splitter on what's LEFT AFTER that boundary —
                # not the whole text. Otherwise a short surname, or an
                # initials-heavy author list, reads as a complete sentence
                # to the splitter and gets mistaken for the title itself.
                # Skipped for the ambiguous "YEAR. Authors. Title" shape
                # (year right at the very start), for the same reason
                # LEADING_YEAR_RE exists above — there's no clean boundary
                # to find there either.
                # Only look for a year boundary — and only truncate the
                # search text to after it — when co-authors aren't already
                # known from an explicit "(with ...)" clause above. If
                # co-authors are already set, a later "(YEAR)" further into
                # the text (which RSA-style citations place right after the
                # journal name) is not an author/title boundary at all, and
                # truncating to it would discard the title and journal that
                # sit before it.
                boundary = None
                if coauthors is None and not LEADING_YEAR_RE.match(text_clean):
                    boundary = _find_year_boundary(text_clean)
                search_text = text_clean
                if boundary:
                    candidate = text_clean[:boundary.start()]
                    if candidate.strip():
                        coauthors = candidate
                        search_text = text_clean[boundary.end():]

                parts = [p.strip() for p in SENTENCE_SPLIT_RE.split(search_text) if p.strip()]
                if len(parts) >= 2:
                    title = parts[0]
                    journal = _clean_journal_tail(re.sub(r"\(.*", "", parts[1]).strip(" .,"))
                elif parts:
                    title = parts[0]
                    confident = False  # couldn't isolate a journal
                else:
                    confident = False

    # tidy stray punctuation left over from splitting, including curly quotes
    # (some academics wrap titles in ‘smart quotes’, not straight ones)
    def _tidy(s: str | None) -> str | None:
        if not s:
            return None
        s = re.sub(r"\s+", " ", s)
        return s.strip(" .,;:–—-'\"\u2018\u2019") or None

    title = _tidy(title)
    journal = _tidy(journal)
    coauthors = _tidy(coauthors)

    if not title:
        confident = False

    # Safety net: if the title ended up as just a bare year (this happens
    # when an italic run was something OTHER than the journal — e.g. one
    # profile italicises its author list instead — so journal_pos lands in
    # the wrong place and "before" collapses to almost nothing), the split
    # clearly went wrong. Flag it rather than assert a broken title.
    if title and re.fullmatch(r"(19|20)\d{2}[a-z]?", title):
        confident = False

    # General safety net: rather than chase each new mis-split shape as a
    # one-off, catch the whole CLASS of "asserted wrong data" failure. Every
    # confirmed mis-split found while validating this parser left a visible
    # fingerprint on the title even though the root cause differed each
    # time: it was actually the author list, it was missing its other half
    # behind unbalanced parens, or it was a bare fragment like a volume
    # number. Demote to low-confidence (reviewed by hand via
    # anu_unparsed_publications.csv) instead of trusting it outright.
    if title and (
        AUTHOR_LIST_PATTERN_RE.search(title)
        or title.count("(") != title.count(")")
        or len(title) < 15
    ):
        confident = False

    pub = Publication(
        researcher_name=researcher.name,
        researcher_profile_url=researcher.profile_url,
        title=title,
        journal_name=journal,
        year=year,
        doi=doi,
        article_url=article_url,
        abdc_self_reported=abdc,
        coauthors=coauthors,
        raw=raw_text.strip(),
    )
    # low-confidence if we found neither a journal nor a year
    if journal is None and year is None:
        confident = False
    return pub, confident


# A bare author list in parens with no "with" keyword at all, e.g.
# "(Nhan Le, Duc Duy Nguyen and Vathunyoo Sila)" — as opposed to
# COAUTHOR_PAREN_RE's "(with ...)" form. The trailing ",? and NAME" is
# mandatory (not optional) specifically so this requires a genuine LIST of
# 2+ names rather than matching a single capitalised phrase like "(ABDC A)"
# or "(CIFR)".
BARE_AUTHOR_LIST_PAREN_RE = re.compile(
    r"\(([A-Z][A-Za-z'\-]+(?:\s[A-Z][A-Za-z'\-]+)*"
    r"(?:,\s*[A-Z][A-Za-z'\-]+(?:\s[A-Z][A-Za-z'\-]+)*)*"
    r",?\s+and\s+[A-Z][A-Za-z'\-]+(?:\s[A-Z][A-Za-z'\-]+)*)\)"
)
# "Accepted on/by/for publication in JOURNAL" — a recognisable academic
# phrase for forthcoming work with no other journal signal in the text at
# all (no italics, no quotes).
ACCEPTED_JOURNAL_RE = re.compile(
    r"\bAccepted\s+(?:on|by|for publication (?:in|on))\s+([^,.]+)", re.IGNORECASE
)
# A title that's actually an author list — repeated "Surname, Initial"
# pairs — slipping through as the confident title. Part of the general
# safety net (see parse_publication's final confidence check) rather than a
# fix for any one specific formatting quirk.
AUTHOR_LIST_PATTERN_RE = re.compile(r"(?:[A-Z][A-Za-z'\-]+,\s*[A-Z]\.?\s*){2,}")


def _find_year_boundary(before: str):
    """
    Find the leftmost of YEAR_PAREN_RE / YEAR_COMMA_RE / YEAR_PERIOD_RE in
    `before` — the point where an author list ends and the title (or, for
    the plain-prose fallback, the title+journal remainder) begins. Shared
    between the italics branch and the no-journal/no-quote fallback below,
    since both need the same "where does the author list end" logic. The
    fallback needed it too: a short surname, or a run of "Surname, Initial"
    pairs, would otherwise be mistaken for a complete sentence by the blind
    ". "-splitter, which has no concept of an author list at all.
    """
    candidates = (YEAR_PAREN_RE.search(before), YEAR_COMMA_RE.search(before), YEAR_PERIOD_RE.search(before))
    return min((m for m in candidates if m), key=lambda m: m.start(), default=None)


# ----------------------------------------------------------------------------
# Directory + profile scraping
# ----------------------------------------------------------------------------

def clean_text(s: str | None) -> str:
    """
    Decode HTML entities and normalise whitespace on any text pulled off the
    page. Needed because ANU's site double-encodes some characters — e.g. a
    title typed with "&" is stored as "&amp;amp;" server-side, so after
    BeautifulSoup's normal (single) decoding you still see a literal
    "&amp;" in the text. unescape() is safe to run on ordinary text too — it
    does nothing if there's nothing to decode.
    """
    if not s:
        return ""
    return _unescape(s).strip()


def parse_directory_page(html: str, base: str) -> list[dict]:
    """
    Extract staff cards from one directory page.
    Selectors below are taken directly from the real page structure, not
    guessed:
        div.anu-card                        <- one per person
          span.tag-tint                     <- category, e.g. "Academic"
          h3.fw-semibold                    <- name, e.g. "Dr Sarah Adams"
          p.d-flex.gap-1 > i.fa-user + span  <- job title
          p.d-flex.gap-1 > i.fa-star + span  <- research area tags
          a.anu-button-black-filled[href]    <- "Visit profile" link (real profile URL)
          a.anu-button-gold-text[href]       <- "Research portal+" link (optional)
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.anu-card")
    people: list[dict] = []

    for c in cards:
        visit = c.select_one("a.anu-button-black-filled")
        if not visit or not visit.get("href"):
            continue  # no profile link — nothing usable on this card
        profile_url = urljoin(base, visit["href"])

        name_el = c.select_one("h3.fw-semibold")
        name = clean_text(name_el.get_text(strip=True)) if name_el else ""
        if not name:
            continue

        category_el = c.select_one("span.tag-tint")
        category = clean_text(category_el.get_text(strip=True)).lower() if category_el else ""

        # job title and research area are both "<p class='d-flex gap-1'><i>..</i><span>text</span></p>"
        # — distinguish them by the icon, not by position, since a card can be
        # missing one of the two lines.
        job_title, area = "", ""
        for p in c.select("p.d-flex.gap-1"):
            icon = p.select_one("i")
            span = p.select_one("span")
            if not icon or not span:
                continue
            icon_classes = " ".join(icon.get("class", []))
            text = clean_text(span.get_text(strip=True))
            if "fa-user" in icon_classes:
                job_title = text
            elif "fa-star" in icon_classes:
                area = text

        portal_el = c.select_one("a.anu-button-gold-text")
        research_portal_url = (urljoin(base, portal_el["href"])
                                if portal_el and portal_el.get("href") else None)

        people.append({
            "name": name,
            "profile_url": profile_url,
            "job_title": job_title,
            "category": category,
            "area": area.lower(),
            "research_portal_url": research_portal_url,
        })
    return people


def scrape_directory(source: dict) -> list[Researcher]:
    """
    Page through one school's directory and return kept academic researchers.
    Stops when a page contributes zero NEW people, rather than trying to
    detect a "next page" link — this site's pagination markup doesn't match
    common patterns, so relying on link-detection risked running all the way
    to the safety cap for no reason. Stopping on "nothing new found" doesn't
    depend on guessing any class name.
    """
    base = source["base"]
    researchers: list[Researcher] = []
    seen: set[str] = set()

    extra_query = source.get("directory_query", "")
    for page in range(MAX_DIRECTORY_PAGES):
        url = f"{base}{source['directory_path']}?page={page}"
        if extra_query:
            url += f"&{extra_query}"
        print(f"[{source['school']}] directory page {page}: {url}")
        resp = get(url)
        if resp is None:
            break
        people = parse_directory_page(resp.text, base)
        if not people:
            print("  no cards found on this page — stopping")
            break

        new_this_page = 0
        for p in people:
            if p["profile_url"] in seen:
                continue
            # keep only academics
            if p["category"] and p["category"] != ACADEMIC_CATEGORY:
                continue
            # RSFAS: keep only finance-area academics
            keep_areas = source["keep_areas"]
            if keep_areas is not None:
                if not p["area"] or not any(kw in p["area"] for kw in keep_areas):
                    continue
            seen.add(p["profile_url"])
            new_this_page += 1
            level = academic_level(p["job_title"])
            if level is None:
                # job_title had no rank keyword (e.g. "Director, ..." or
                # "Deputy Director (Education)") — fall back to whatever
                # title prefix was on their name instead.
                prefix = extract_title_prefix(p["name"])
                if prefix:
                    level = academic_level(prefix)
            researchers.append(Researcher(
                name=clean_name(p["name"]),
                job_title=p["job_title"],
                academic_level=level,
                field_of_research=source["default_field"],
                profile_url=p["profile_url"],
                research_portal_url=p["research_portal_url"],
            ))

        print(f"  kept {new_this_page} new academic researcher(s) from this page")
        if new_this_page == 0:
            print("  nothing new on this page — assuming directory is exhausted, stopping")
            break

    return researchers


def extract_publications_block(html: str) -> list[dict]:
    """
    Pull the list of publication paragraphs from a profile page, keeping BOTH
    the flattened text (for the title/journal/year regexes) AND the real
    (link text, href) pairs found in each paragraph. This matters because
    some publication titles are hyperlinks whose destination only exists as
    an href, not as visible text — get_text() alone throws that address away.
    """
    soup = BeautifulSoup(html, "html.parser")

    heading = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        if h.get_text(strip=True).lower().startswith("publications"):
            heading = h
            break
    if heading is None:
        return []

    blocks: list[dict] = []
    for sib in heading.find_all_next():
        if sib.name in ("h2", "h3", "h4") and sib is not heading:
            break
        if sib.name in ("p", "li"):
            txt = clean_text(sib.get_text(" ", strip=True))
            if not txt or txt.lower().startswith("view research profile"):
                continue
            low = txt.strip(" .:").lower()
            if low in NON_PUBLICATION_HEADINGS:
                continue
            if any(sub in low for sub in NON_PUBLICATION_SUBSTRINGS):
                continue
            links = [(clean_text(a.get_text(strip=True)), a["href"])
                     for a in sib.find_all("a") if a.get("href")]
            # Journal names are often wrapped in <i>/<em> in the real HTML.
            # Read that directly from the DOM — do NOT try to detect italics
            # from flattened text later; get_text() drops all formatting, so
            # there is nothing left to detect by that point.
            italics = [clean_text(em.get_text(strip=True))
                       for em in sib.find_all(["i", "em"])
                       if em.get_text(strip=True)]
            blocks.append({"text": txt, "links": links, "italics": italics})
    return blocks


def scrape_profile(researcher: Researcher) -> tuple[list[Publication], list[Publication], bool]:
    """
    Returns (confident_pubs, unparsed_pubs, had_publications_section).
    """
    resp = get(researcher.profile_url)
    if resp is None:
        return [], [], False
    blocks = extract_publications_block(resp.text)
    if not blocks:
        return [], [], False

    confident, unparsed = [], []
    for block in blocks:
        pub, ok = parse_publication(block, researcher)
        if pub is None:
            continue
        (confident if ok else unparsed).append(pub)
    return confident, unparsed, True


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def write_json(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. staff
    researchers: list[Researcher] = []
    for source in SOURCES:
        researchers.extend(scrape_directory(source))
    print(f"\nCollected {len(researchers)} academic researchers "
          f"(accounting + finance) across RSA + RSFAS.\n")

    # 2. publications
    all_pubs: list[Publication] = []
    unparsed: list[Publication] = []
    no_pub_people: list[Researcher] = []

    for i, r in enumerate(researchers, 1):
        print(f"[{i}/{len(researchers)}] {r.name} — {r.profile_url}")
        conf, unc, had_section = scrape_profile(r)
        all_pubs.extend(conf)
        unparsed.extend(unc)
        if not had_section:
            no_pub_people.append(r)

    # 3. write everything
    staff_fields = ["name", "job_title", "academic_level", "field_of_research",
                    "profile_url", "university", "research_portal_url"]
    write_csv(OUTPUT_DIR / "anu_staff.csv",
              [asdict(r) for r in researchers], staff_fields)
    write_json(OUTPUT_DIR / "anu_staff.json", [asdict(r) for r in researchers])

    pub_fields = ["researcher_name", "researcher_profile_url", "title",
                  "journal_name", "year", "doi", "article_url",
                  "abdc_self_reported", "coauthors", "source",
                  "citation_percentile", "raw"]
    write_csv(OUTPUT_DIR / "anu_publications.csv",
              [asdict(p) for p in all_pubs], pub_fields)
    write_json(OUTPUT_DIR / "anu_publications.json", [asdict(p) for p in all_pubs])

    # audit trails
    write_csv(OUTPUT_DIR / "anu_unparsed_publications.csv",
              [asdict(p) for p in unparsed], pub_fields)
    write_csv(OUTPUT_DIR / "anu_no_publications.csv",
              [asdict(r) for r in no_pub_people], staff_fields)

    # 4. summary
    print("\n" + "=" * 60)
    print("ANU SCRAPE SUMMARY")
    print("=" * 60)
    print(f"Researchers (academic, acc+fin):     {len(researchers)}")
    print(f"  with no inline Publications block:  {len(no_pub_people)}  "
          f"(coverage gap — see anu_no_publications.csv)")
    print(f"Publications parsed (confident):      {len(all_pubs)}")
    print(f"Publications logged for review:       {len(unparsed)}  "
          f"(see anu_unparsed_publications.csv)")
    print(f"Output written to:                    {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
