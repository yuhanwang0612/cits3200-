"""Adelaide University adapter.

Scrapes researchers.adelaide.edu.au for Accounting & Finance staff.

Phase 1: paginate the staff directory; try card-level A&F filtering inline.
         If card text shows dept → only ~83 profiles visited in Phase 2.
         If cards don't show dept → falls back to visiting all profiles.
Phase 2: visit each A&F candidate profile to extract name / title / ORCID.

Publications are not fetched here — run.py's info/openalex.py step
retrieves them using the ORCIDs this adapter provides.
"""

import re
import time

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
    "school of accounting", "school of finance",
    "accounting and finance", "finance and accounting",
    "department of accounting", "department of finance",
    "banking and finance",
]


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


_SESSION = _get_session()


def _card_is_af(link_tag):
    """
    Walk up the DOM from a profile link to find the person's listing card,
    then check if it mentions Accounting or Finance.

    Stops walking when an ancestor contains sibling profile links (meaning
    we've risen above the per-person card level).  Returns True if A&F
    keywords appear in the card text.
    """
    href = link_tag.get("href", "")
    node = link_tag
    for _ in range(8):
        parent = node.parent
        if parent is None:
            break
        # Stop if this ancestor already contains OTHER profile links
        sibling_hrefs = [
            a.get("href", "")
            for a in parent.find_all("a", href=re.compile(r"^/profile/"))
            if a.get("href", "") != href
        ]
        if sibling_hrefs:
            # node is the per-person card — check it
            card_text = node.get_text(" ", strip=True)
            lower = card_text.lower()
            return (
                any(s in lower for s in _SCHOOL_NAMES)
                or bool(_ACCTFIN_RE.search(card_text))
            )
        node = parent

    # Reached the top without finding siblings — check whatever we have
    card_text = node.get_text(" ", strip=True)
    lower = card_text.lower()
    return (
        any(s in lower for s in _SCHOOL_NAMES)
        or bool(_ACCTFIN_RE.search(card_text))
    )


def _is_accounting_finance(soup):
    """Full-profile A&F check (used as secondary verification)."""
    for tag in soup.find_all(
        ["div", "span", "p", "li", "h2", "h3", "a"],
        class_=re.compile(r"affili|school|department|faculty|unit|position|role|org", re.I),
    ):
        if _ACCTFIN_RE.search(tag.get_text(" ", strip=True)):
            return True
    for tag in soup.find_all(
        ["div", "span", "section"],
        id=re.compile(r"affili|school|department|faculty|unit|position", re.I),
    ):
        if _ACCTFIN_RE.search(tag.get_text(" ", strip=True)):
            return True
    page_text = soup.get_text(" ", strip=True).lower()
    if any(name in page_text for name in _SCHOOL_NAMES):
        return True
    if re.search(r"\bschool\b.{0,80}\b(accounting|finance)\b", page_text, re.S):
        return True
    return False


def _discipline(soup):
    """
    Determine discipline from a profile page.
    Prefers exact school-name match; falls back to keyword count.
    """
    text = soup.get_text(" ", strip=True)
    lower = text.lower()
    # Exact matches first
    if any(s in lower for s in ["school of accounting", "department of accounting"]):
        return "Accounting"
    if any(s in lower for s in ["school of finance", "department of finance", "banking and finance"]):
        return "Finance"
    # Mixed name — count occurrences
    n_acc = lower.count("accounting")
    n_fin = lower.count("finance") + lower.count("financial")
    return "Finance" if n_fin > n_acc else "Accounting"


def scrape_staff(verbose=True):
    if verbose:
        print("  Phase 1: scanning researchers.adelaide.edu.au ...")

    all_usernames = []   # every unique username seen
    af_usernames = []    # usernames whose listing card matched A&F
    seen = set()
    page, consecutive_empty = 1, 0
    card_filter_hit = False  # becomes True the first time a card matches

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

            new_total = 0
            new_af = 0
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
                    print(
                        f"    page {page}: {len(links)} links, "
                        f"{new_total} new -> {len(all_usernames)} total"
                    )
            page += 1
            time.sleep(0.3)
        except Exception as exc:
            if verbose:
                print(f"    error page {page}: {exc}")
            time.sleep(5)
            page += 1

    # ── decide which profiles to visit ───────────────────────────────────
    if af_usernames:
        usernames_to_visit = af_usernames
        if verbose:
            print(
                f"  {len(all_usernames)} researchers scanned; "
                f"{len(af_usernames)} A&F candidates from card filter."
            )
            print(f"  Phase 2: visiting {len(af_usernames)} profiles ...")
    else:
        # Cards didn't show dept — fall back to checking every profile
        usernames_to_visit = all_usernames
        if verbose:
            print(
                f"  {len(all_usernames)} candidates — "
                f"card filter found nothing; Phase 2: filtering all profiles ..."
            )

    # ── Phase 2: visit profiles ───────────────────────────────────────────
    records = []
    for username in usernames_to_visit:
        rurl = f"https://researchers.adelaide.edu.au/profile/{username}"
        try:
            resp = _SESSION.get(rurl, headers=_HEADERS, timeout=15)
            if resp.status_code != 200 or len(resp.text) < 500:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            # Always verify at profile level (catches card-filter false positives
            # and is the only check in fallback mode)
            if not _is_accounting_finance(soup):
                continue

            h1 = soup.find("h1")
            name_raw = h1.get_text(strip=True) if h1 else ""
            name_clean, prefix = split_prefix(name_raw)
            if not name_clean or len(name_clean) < 3:
                continue

            title_raw = None
            for tag in soup.find_all(["p", "h2", "h3", "div", "span"], limit=40):
                text = tag.get_text(strip=True)
                if any(w in text.lower() for w in ["professor", "lecturer", "researcher", "fellow", "associate"]):
                    if 3 < len(text) < 120:
                        title_raw = text
                        break

            orcid = None
            for a in soup.find_all("a", href=True):
                m = re.search(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", a["href"])
                if m:
                    orcid = m.group(1)
                    break

            if not orcid:
                try:
                    pr = _SESSION.get(
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

            records.append({
                "university": UNIVERSITY,
                "discipline": _discipline(soup),
                "name": name_raw,
                "name_clean": name_clean,
                "prefix": prefix,
                "title": title_raw,
                "title_clean": rank(title_raw, prefix),
                "profile_url": f"https://adelaide.edu.au/people/{username}",
                "orcid": orcid,
            })
            if verbose:
                tag = f"[orcid={orcid}]" if orcid else "[no orcid]"
                print(f"  + {name_clean:40s}  {tag}")
            time.sleep(0.3)

        except Exception as exc:
            if verbose:
                print(f"  error {username}: {exc}")
            time.sleep(2)

    if verbose:
        print(f"  {len(records)} A&F staff")
    return records


def collect(verbose=True):
    """Return (records, pubs) satisfying the core.schema contract."""
    records = scrape_staff(verbose)
    return records, []
