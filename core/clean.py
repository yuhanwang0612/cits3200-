"""Post-adapter cleaning + exclusion, applied to every publication.

Adapters and the info/ retrieval modules each pull from a different system
(eSpace JSON, ORCID, Crossref, OpenAlex, scraped HTML), so the same defect —
an HTML entity in a title, a malformed DOI, an erratum, a repository posing as
a journal — arrives from more than one of them. Running this once over the
whole pub list, AFTER info/ retrieval and BEFORE enrichment, fixes it for
every university and every source at the same point.

Place at core/clean.py. Called from run.py (see the one-line insertion).
"""

import html
import re
from datetime import date

from core.schema import clean_journal, norm_type

# A DOI is "10." + registrant + "/" + suffix. The "s1474667017471096" that
# came off an ORCID external-id is not one.
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")

# Repository venues that carry preprints/working papers. When one appears in
# the journal field, the record is not a journal article.
_PREPRINT_VENUES = {
    "ssrn", "ssrn electronic journal", "arxiv", "arxiv.org", "biorxiv",
    "preprints.org", "research square", "repec",
    "nber", "national bureau of economic research",
}

# Non-substantive items that eSpace/Crossref/ORCID tag as "Journal Article".
# Anchored to the START so it drops "Erratum to…", "Corrigendum to…",
# "Editorial:…", "Rejoinder to…", "A comment on…" but NOT real papers like
# "…the SEC's comment letters" or "…from an editorial perspective".
_EXCLUDE_TITLE = re.compile(
    r"^\s*['\"“”‘’]*\s*"
    r"(erratum|corrigendum|editorial|rejoinder|(a\s+)?comments?\s+on)\b",
    re.IGNORECASE,
)


# real HTML tags only — must start with a letter (or /letter), so it won't
# eat a mathematical "P<0.05" or "x>y"
_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*>")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?)])")


def clean_text(value):
    """Decode entities (repeatedly, for double-encoded titles), strip literal
    HTML tags, and collapse whitespace.

    eSpace sometimes stores an HTML fragment that was then entity-encoded a
    second time, so one unescape leaves '&amp;nbsp;' as '&nbsp;' and
    '&lt;p&gt;' as '<p>'. Decoding until stable clears both layers; the tag
    strip then removes the now-literal <p>/<i>/<span>; the whitespace collapse
    turns the decoded non-breaking space into an ordinary one.
    """
    if not value:
        return value
    text = value
    for _ in range(5):                       # peel nested / double encoding
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _TAG_RE.sub(" ", text)            # drop <i>, <p>, <span>, ...
    text = re.sub(r"\s+", " ", text).strip()
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text) or None

def clean_doi(value):
    """Return a well-formed DOI or None. Strips a leading doi.org URL, then
    validates the 10.<reg>/<suffix> shape; junk becomes None."""
    if not value:
        return None
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", value.strip(), flags=re.I)
    return doi if _DOI_RE.match(doi) else None


def clean_year(value):
    """Keep a plausible four-digit year, else None — catches '0202' and
    anything outside publishing range, without hard-coding the current year."""
    if not value:
        return None
    y = str(value)[:4]
    return y if y.isdigit() and 1900 <= int(y) <= date.today().year + 2 else None


def is_excluded(pub):
    """True for errata / editorials / corrigenda / rejoinders / comments,
    whatever source they came from (eSpace, ORCID, Crossref). This is the
    check that must run post-info/, because those sources re-introduce rows
    the adapter's own filter never sees."""
    title = pub.get("title")
    return bool(title and _EXCLUDE_TITLE.match(title))


def clean_pub(pub, log=None):
    """Normalise one publication dict in place and return it. If `log` is a
    list, append a short note for each field actually changed."""
    def note(msg):
        if log is not None:
            log.append(msg)

    who = (pub.get("name") or "?")

    t0 = pub.get("title")
    pub["title"] = clean_text(t0)
    if t0 != pub["title"]:
        note(f"    title   {who}: {t0!r} -> {pub['title']!r}")

    y0 = pub.get("year")
    pub["year"] = clean_year(y0)
    if str(y0 or "") != str(pub["year"] or ""):
        note(f"    year    {who}: {y0!r} -> {pub['year']!r}")

    ty0 = pub.get("type")
    pub["type"] = norm_type(ty0)
    if (pub.get("journal") or "").strip().lower() in _PREPRINT_VENUES:
        pub["type"] = "Preprint"
    if ty0 != pub["type"]:
        note(f"    type    {who}: {ty0!r} -> {pub['type']!r}  ({pub.get('journal')!r})")

    j0 = pub.get("journal")
    pub["journal"] = clean_journal(clean_text(j0))
    if j0 != pub["journal"]:
        note(f"    journal {who}: {j0!r} -> {pub['journal']!r}")
    pub["journal_canonical"] = clean_journal(clean_text(pub.get("journal_canonical")))

    d0 = pub.get("doi")
    pub["doi"] = clean_doi(d0)
    if d0 != pub["doi"]:
        note(f"    doi     {who}: {d0!r} -> {pub['doi']!r}")
    if pub["doi"] is None and pub.get("link") and "doi.org" in pub["link"]:
        note(f"    link    {who}: dropped dead doi.org link {pub['link']!r}")
        pub["link"] = None

    return pub


def clean_pubs(pubs, verbose=False):
    """Clean every publication, then drop the excluded ones.
    With verbose=True, print each field changed and each row dropped."""
    log = [] if verbose else None
    cleaned = [clean_pub(p, log) for p in pubs]

    kept, dropped = [], []
    for p in cleaned:
        (dropped if is_excluded(p) else kept).append(p)

    if verbose:
        if log:
            print(f"  cleaned {len(log)} field(s):")
            for line in log:
                print(line)
        for p in dropped:
            print(f"    drop    {p.get('name','?')}: {p.get('title')!r}")
        print(f"  kept {len(kept)}, dropped {len(dropped)}")

    return kept