"""Adelaide University adapter.

Scrapes researchers.adelaide.edu.au for Accounting & Finance staff.

Phase 1: paginate the staff directory; filter A&F candidates from card text inline.
         If card text shows dept → only ~83 profiles visited in Phase 2.
         If cards don't show dept → falls back to visiting all profiles.
Phase 2: visit each A&F candidate profile in parallel (ThreadPoolExecutor).
         The same profile page lists the person's publications; the "Journals"
         table is read as the official record, labelled "Adelaide profile", so
         screen.py never removes it. Each entry carries its own DOI.

run.py's ORCID, Crossref and OpenAlex steps still add papers the profile lacks.
Before profiles were read, those steps were the only source, so anyone without
an ORCID on their profile (most of the School of Accounting and Finance) had no
publications at all.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from core.titles import rank, split_prefix

UNIVERSITY = "Adelaide University"
ROR = "00892tw58"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_ACCTFIN_RE = re.compile(r"\b(accounting|finance|financial)\b", re.I)

_SCHOOL_NAMES = [
    "school of accounting and finance",   # actual Adelaide University school name
    "school of accounting", "school of finance",
    "accounting and finance", "finance and accounting",
    "department of accounting", "department of finance",
    "banking and finance",
]

# Matches "Lecturer, Accounting" / "Professor of Finance" / "Senior Lecturer in Accounting"
_DISC_ACCOUNTING_RE = re.compile(r"[,\s]\s*accounting\b|[,\s]\s*accountant\b|\bof\s+accounting\b|\bin\s+accounting\b", re.I)
_DISC_FINANCE_RE    = re.compile(r"[,\s]\s*finance\b|[,\s]\s*financial\b|\bof\s+finance\b|\bin\s+finance\b|\bbanking\b", re.I)


class _LegacySSLAdapter(HTTPAdapter):
    """Allow legacy SSL renegotiation for older university servers."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        ctx = create_urllib3_context()
        ctx.options |= 0x4
        proxy_kwargs["ssl_context"] = ctx
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _get_session():
    s = requests.Session()
    s.mount("https://researchers.adelaide.edu.au", _LegacySSLAdapter())
    return s


# Shared session for Phase 1 (sequential)
_SESSION = _get_session()


def _card_is_af(link_tag):
    """
    Walk up the DOM from a profile link to find the per-person listing card,
    then decide if it's an A&F candidate.

    Adelaide University cards show one of:
      (a) "Lecturer, Accounting" / "Senior Lecturer, Finance" → exact match via
          _ACCTFIN_RE / _SCHOOL_NAMES — confident A&F
      (b) "College of Business and Law" only (no role shown) → soft match: A&F
          lives within that college, so include as candidate for Phase 2 to verify.

    We accept (b) to avoid false-negatives for staff whose cards omit the role.
    Phase 2's _is_accounting_finance() will reject non-A&F people from that college.
    """
    href = link_tag.get("href", "")
    node = link_tag
    for _ in range(8):
        parent = node.parent
        if parent is None:
            break
        sibling_hrefs = [
            a.get("href", "")
            for a in parent.find_all("a", href=re.compile(r"^/profile/"))
            if a.get("href", "") != href
        ]
        if sibling_hrefs:
            card_text = node.get_text(" ", strip=True)
            lower = card_text.lower()
            return (
                any(s in lower for s in _SCHOOL_NAMES)
                or bool(_ACCTFIN_RE.search(card_text))
                or "college of business and law" in lower
            )
        node = parent
    card_text = node.get_text(" ", strip=True)
    lower = card_text.lower()
    return (
        any(s in lower for s in _SCHOOL_NAMES)
        or bool(_ACCTFIN_RE.search(card_text))
        or "college of business and law" in lower
    )


def _is_accounting_finance(soup):
    """Full-profile A&F check (secondary verification).

    Adelaide University has a single 'School of Accounting and Finance', so every
    A&F researcher's page contains both words. We only need to confirm they belong
    to that school — discipline is determined separately via the job title.
    """
    page_text = soup.get_text(" ", strip=True).lower()
    # Fast exact-match on the known school name (most reliable)
    if any(name in page_text for name in _SCHOOL_NAMES):
        return True
    # CSS-class fallback for any structural tags that name the school/dept
    for tag in soup.find_all(
        ["div", "span", "p", "li", "h2", "h3", "a"],
        class_=re.compile(r"affili|school|department|faculty|unit|position|role|org", re.I),
    ):
        if _ACCTFIN_RE.search(tag.get_text(" ", strip=True)):
            return True
    # Broader proximity search as last resort
    if re.search(r"\bschool\b.{0,80}\b(accounting|finance)\b", page_text, re.S):
        return True
    return False


def _discipline(title_raw, soup):
    """Determine discipline.

    Priority 1 — job title (most accurate):
        "Lecturer, Accounting"    → Accounting
        "Professor of Finance"    → Finance
        "Senior Lecturer, Financial Risk" → Finance
    Priority 2 — word-count fallback on page text (both disciplines appear on every
    Adelaide A&F page, so this is a rough heuristic used only when the title is absent
    or truly ambiguous).
    """
    if title_raw:
        has_acc = bool(_DISC_ACCOUNTING_RE.search(title_raw))
        has_fin = bool(_DISC_FINANCE_RE.search(title_raw))
        if has_acc and not has_fin:
            return "Accounting"
        if has_fin and not has_acc:
            return "Finance"
        # Tie or neither — fall through to word count
    lower = soup.get_text(" ", strip=True).lower()
    n_acc = lower.count("accounting")
    n_fin = lower.count("finance") + lower.count("financial")
    return "Finance" if n_fin > n_acc else "Accounting"


PROFILE_SOURCE = "Adelaide profile"
_AUTHOR_RE = re.compile(r"[^,&]+?,\s*(?:[A-Z][A-Za-z\-]*\.\s*)+")
_CITATION_RE = re.compile(r"^(?P<authors>.*?)\s*\((?P<year>[^)]*)\)\.\s*(?P<title>.*)$", re.S)


def _clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _citation_span(cell):
    """The APA citation inside a publication cell, not the badge spans after it."""
    span = cell.select_one("a > span")
    if span:
        return span
    for span in cell.find_all("span"):
        classes = set(span.get("class") or [])
        parent = set(span.parent.get("class") or [])
        if not classes and "citation-counts" not in parent:
            return span
    return None


def _parse_profile_publications(soup, name, source_id=None):
    """Journal articles from the "Journals" table of a researchers.adelaide.edu.au profile.

    Each row is a year cell and an APA citation such as
    "Gupta, K., & Krishnamurti, C. (2021). Title?. <i>Journal</i>, <i>68</i>, 1-9."
    followed by a DOI link. Books, chapters and conference papers sit in other
    tables and are not read: only journal articles are exported.
    """
    rows, seen = [], set()
    for header in soup.select("h3.accordion-header"):
        if _clean_text(header.get_text(" ")).lower() != "journals":
            continue
        item = header.find_parent(class_="accordion-item") or header.parent
        table = item.find("table") if item else None
        if table is None:
            continue
        for tr in table.select("tbody tr"):
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            citation = _citation_span(cells[1])
            if citation is None:
                continue

            journal_tag = citation.find("i")
            journal = _clean_text(journal_tag.get_text(" ")) if journal_tag else None
            # Everything before the first italic is "Authors (Year). Title."
            before = []
            for node in citation.children:
                if node is journal_tag:
                    break
                before.append(node.get_text(" ") if hasattr(node, "get_text") else str(node))
            match = _CITATION_RE.match(_clean_text("".join(before)))
            if not match:
                continue
            title = re.sub(r"\s*\.\s*$", "", match.group("title")).strip()
            title = re.sub(r"([?!])\.$", r"\1", title)
            if not title:
                continue

            year = re.search(r"\b(?:18|19|20)\d{2}\b", cells[0].get_text()) or \
                re.search(r"\b(?:18|19|20)\d{2}\b", match.group("year"))
            authors = [a.strip(" ,&") for a in _AUTHOR_RE.findall(match.group("authors"))]

            doi_link = cells[1].select_one("a[href*='doi.org/']")
            doi = None
            if doi_link:
                doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi_link["href"], flags=re.I)
                doi = doi.strip().rstrip(".").lower() or None
            handle = next((a["href"] for a in cells[1].find_all("a", href=True)
                           if "doi.org/" not in a["href"]), None)

            key = doi or re.sub(r"[^a-z0-9]", "", title.lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "name": name,
                "source_id": source_id,
                "title": title,
                "year": year.group(0) if year else None,
                "type": "Journal Article",
                "n_authors": len(authors) or None,
                "authors": "; ".join(authors) or None,
                "issns": [],
                "journal": journal,
                "journal_canonical": None,
                "publisher": None,
                "doi": doi,
                "link": handle or (f"https://doi.org/{doi}" if doi else None),
                "source": PROFILE_SOURCE,
            })
    return rows


def _visit_profile(username):
    """
    Fetch and parse one researcher profile.
    Returns a record dict on success, or None if not A&F / error.
    Each call creates its own session (thread-safe SSL adapter).
    """
    session = _get_session()
    rurl = f"https://researchers.adelaide.edu.au/profile/{username}"
    try:
        resp = session.get(rurl, headers=_HEADERS, timeout=15)
        if resp.status_code != 200 or len(resp.text) < 500:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        if not _is_accounting_finance(soup):
            return None

        h1 = soup.find("h1")
        name_raw = h1.get_text(strip=True) if h1 else ""
        name_clean, prefix = split_prefix(name_raw)
        if not name_clean or len(name_clean) < 3:
            return None

        title_raw = None
        _TITLE_WORDS = [
            "professor", "lecturer", "researcher", "fellow", "associate",
            "adjunct", "honorary", "visiting", "emeritus", "dean",
            "director", "chair", "tutor", "postdoc",
        ]
        for tag in soup.find_all(["p", "h2", "h3", "div", "span"], limit=80):
            text = tag.get_text(strip=True)
            if any(w in text.lower() for w in _TITLE_WORDS):
                if 3 < len(text) < 120:
                    title_raw = text
                    break

        # Strip discipline suffix: "Lecturer, Accounting" -> "Lecturer"
        if title_raw:
            title_raw = re.sub(r",\s*(Accounting|Finance|Financial\w*)[^,]*$", "", title_raw, flags=re.I).strip()

        orcid = None
        for a in soup.find_all("a", href=True):
            m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
            if m:
                orcid = m.group(1)
                break

        if not orcid:
            try:
                pr = session.get(
                    f"https://adelaide.edu.au/people/{username}",
                    headers=_HEADERS, timeout=10,
                )
                if pr.status_code == 200 and len(pr.text) > 500:
                    psoup = BeautifulSoup(pr.text, "html.parser")
                    for a in psoup.find_all("a", href=True):
                        m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
                        if m:
                            orcid = m.group(1)
                            break
            except Exception:
                pass

        # A publication table that fails to parse keeps the person, with no
        # profile publications this run, rather than dropping them entirely.
        try:
            pubs, pub_error = _parse_profile_publications(soup, name_clean, username), None
        except Exception as error:
            pubs, pub_error = [], f"{type(error).__name__}: {error}"

        return {
            "university": UNIVERSITY,
            "discipline": _discipline(title_raw, soup),
            "name": name_raw,
            "name_clean": name_clean,
            "prefix": prefix,
            "title": title_raw,
            "title_clean": rank(title_raw, prefix),
            "profile_url": f"https://adelaide.edu.au/people/{username}",
            "source_id": username,
            "orcid": orcid,
            "_pubs": pubs,
            "_pub_error": pub_error,
        }
    except Exception:
        return None


def scrape_staff(verbose=True):
    if verbose:
        print("  Phase 1: scanning researchers.adelaide.edu.au ...")

    all_usernames = []
    af_usernames = []
    seen = set()
    page, consecutive_empty = 1, 0
    card_filter_hit = False

    while page <= 300:
        url = f"https://researchers.adelaide.edu.au/?page={page}"
        try:
            resp = _SESSION.get(url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all("a", href=re.compile(r"^/profile/"))
            if not links:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                page += 1
                continue
            consecutive_empty = 0

            new_total = new_af = 0
            for link in links:
                username = link["href"].replace("/profile/", "").strip("/")
                if not username or username in seen:
                    continue
                seen.add(username)
                all_usernames.append(username)
                new_total += 1
                if _card_is_af(link):
                    af_usernames.append(username)
                    new_af += 1
                    card_filter_hit = True

            if verbose:
                if card_filter_hit:
                    print(
                        f"    page {page}: {len(links)} links, "
                        f"{new_total} new ({new_af} A&F) -> "
                        f"{len(all_usernames)} total / {len(af_usernames)} A&F"
                    )
                else:
                    print(f"    page {page}: {len(links)} links, {new_total} new -> {len(all_usernames)} total")
            page += 1
            time.sleep(0.3)
        except Exception as exc:
            if verbose:
                print(f"    error page {page}: {exc}")
            time.sleep(5)
            page += 1

    usernames_to_visit = af_usernames if af_usernames else all_usernames
    if verbose:
        if af_usernames:
            print(f"  {len(all_usernames)} scanned; {len(af_usernames)} A&F from card filter.")
        else:
            print(f"  {len(all_usernames)} candidates — card filter found nothing; visiting all profiles.")
        print(f"  Phase 2: visiting {len(usernames_to_visit)} profiles (5 parallel workers) ...")

    # ── Phase 2: parallel profile visits ─────────────────────────────────
    records = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_visit_profile, u): u for u in usernames_to_visit}
        for future in as_completed(futures):
            result = future.result()
            if result:
                records.append(result)
                if verbose:
                    tag = f"[orcid={result['orcid']}]" if result["orcid"] else "[no orcid]"
                    pubs = f"{len(result['_pubs'])} profile journal articles"
                    if result["_pub_error"]:
                        pubs = f"publication table unreadable ({result['_pub_error']})"
                    print(f"  + {result['name_clean']:40s}  {tag}  {pubs}")

    if verbose:
        print(f"  {len(records)} A&F staff")
    return records


def collect(verbose=True):
    """Return (records, pubs) satisfying the core.schema contract."""
    records = scrape_staff(verbose)
    pubs, failed = [], []
    for record in records:
        pubs.extend(record.pop("_pubs", []))
        error = record.pop("_pub_error", None)
        if error:
            failed.append((record["name_clean"], error))
    if verbose:
        print(f"  {len(pubs)} journal articles from Adelaide profiles")
        if failed:
            print(f"  publication table unreadable for {len(failed)} staff:")
            for name, error in sorted(failed):
                print(f"    - {name}: {error}")
    return records, pubs
