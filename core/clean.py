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

# core/clean.py
import csv
from pathlib import Path

def _title_key(title):
    """The normalised form both override and exclusion title keys use."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()


_OVERRIDES = {}         # doi -> {field: correct_value}
_TITLE_OVERRIDES = {}   # (name, normalised title) -> {field: correct_value}
# columns: doi,field,value,name,title
#
# A DOI is the stable key and stays the default. Some rows have no DOI at all
# (a magazine item off a university profile page), and those cannot be
# corrected by DOI without keying on the empty string, which would match every
# DOI-less publication in the dataset at once. Such a row is keyed on
# researcher name plus normalised title instead, exactly as _exclusion_key
# does, so the correction is scoped to one person's copy of one paper.
_ov_path = Path(__file__).resolve().parents[1] / "data" / "overrides.csv"
if _ov_path.exists():
    with open(_ov_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            field = (row.get("field") or "").strip()
            if not field:
                continue
            value = row.get("value")
            value = None if value in (None, "") else value
            doi = (row.get("doi") or "").strip().lower()
            if doi:
                _OVERRIDES.setdefault(doi, {})[field] = value
                continue
            name = (row.get("name") or "").strip().casefold()
            title = _title_key(row.get("title"))
            if name and title:
                _TITLE_OVERRIDES.setdefault((name, title), {})[field] = value

# Confirmed author-identity collisions.  These are deliberately keyed by both
# researcher name and DOI: a paper can be a valid record for one staff member
# while being a namesake false-positive for another.  Keeping the evidence in
# data/ makes every future refresh reproduce the reviewed decision instead of
# relying on a one-off edit to the exported CSV.
_PUBLICATION_EXCLUSIONS = {}


def _exclusion_key(name, doi, title=None):
    """(name, doi) when there is a DOI. Some wrong papers on a university's
    own profile page have no DOI at all (UNSW lists a 1983 Chemical
    Engineering article under an auditing professor), so a row with a blank
    doi is matched on the normalised title instead. A blank doi is never
    used as a key by itself, or it would drop every DOI-less paper that
    researcher has."""
    name = (name or "").strip().casefold()
    doi = (doi or "").strip().lower()
    if doi:
        return (name, doi)
    title = re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()
    return (name, "title:" + title) if title else None

_ex_path = Path(__file__).resolve().parents[1] / "data" / "publication_exclusions.csv"
if _ex_path.exists():
    with open(_ex_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = _exclusion_key(row["name"], row["doi"], row.get("title"))
            if key:
                _PUBLICATION_EXCLUSIONS[key] = row

def override_fields(pub, doi=None):
    """Every reviewed correction that applies to this publication.

    DOI first, then the name+title key, so a title-keyed entry can still
    correct a row whose DOI arrived later in the run.

    `doi` overrides the value on the row. clean_pub must pass the RAW doi it
    captured before clean_doi ran: one override exists precisely to repair a
    malformed DOI ("s1474667017471096"), and clean_doi nulls that value, so
    reading it off the row after cleaning would never match it again.
    """
    out = {}
    doi = ((pub.get("doi") if doi is None else doi) or "").strip().lower()
    if doi:
        out.update(_OVERRIDES.get(doi, {}))
    key = ((pub.get("name") or "").strip().casefold(), _title_key(pub.get("title")))
    if key[0] and key[1]:
        out.update(_TITLE_OVERRIDES.get(key, {}))
    return out


def _apply_overrides(pub):
    for field, value in override_fields(pub).items():
        pub[field] = value
    return pub


def apply_overrides(pubs, verbose=False):
    """Re-apply reviewed corrections AFTER enrichment.

    clean_pub applies them early so the rest of cleaning sees the corrected
    value, but abdc/clarivate/scimago run later and assign their own fields
    unconditionally. Without this second pass a correction to `abdc` is
    silently undone by abdc.enrich a few steps later. An override is meant to
    be the last word, so it is applied last as well as first.
    """
    n = 0
    for pub in pubs:
        fields = override_fields(pub)
        changed = {f: v for f, v in fields.items() if pub.get(f) != v}
        if changed:
            n += 1
            if verbose:
                who = (pub.get("name") or "?")
                print(f"  override {who}: "
                      + ", ".join(f"{f}={v!r}" for f, v in changed.items()))
        pub.update(fields)
    if verbose:
        print(f"overrides: {n} rows corrected after enrichment")
    return pubs


# A DOI is "10." + registrant + "/" + suffix. The "s1474667017471096" that
# came off an ORCID external-id is not one.
_DOI_LINK_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.I)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")

# Repository venues that carry preprints/working papers. When one appears in
# the journal field, the record is not a journal article.
_PREPRINT_VENUES = {
    "ssrn", "ssrn electronic journal", "arxiv", "arxiv.org", "biorxiv",
    "preprints.org", "research square", "repec",
    "nber", "national bureau of economic research",
}

# SSRN DOIs identify working-paper/preprint records even when ORCID or another
# upstream source incorrectly labels the item as a journal article and omits
# the venue. Venue-only filtering cannot catch that shape.
_PREPRINT_DOI_PREFIXES = ("10.2139/ssrn.",)

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
    # DOI URLs copied from feeds sometimes retain analytics parameters or an
    # HTML suffix after the DOI.  Crossref treats those as part of the DOI and
    # returns HTTP 400, so remove URL-only decoration before validation.
    doi = re.sub(r"[?#].*$", "", doi)
    doi = re.sub(r"\.html?$", "", doi, flags=re.I)
    return doi if _DOI_RE.match(doi) else None


def clean_year(value):
    """Keep a plausible four-digit year, else None — catches '0202' and
    anything outside publishing range, without hard-coding the current year."""
    if not value:
        return None
    y = str(value)[:4]
    return y if y.isdigit() and 1900 <= int(y) <= date.today().year + 2 else None


def reviewed_exclusion(pub):
    """Return a reviewed exclusion matched by researcher plus DOI or title.

    DOI remains the preferred stable key.  Title matching supports confirmed
    false positives whose source record has no DOI; it is still scoped to the
    named researcher so the same title can remain valid for another person.
    """
    key = _exclusion_key(pub.get("name"), pub.get("doi"), pub.get("title"))
    return _PUBLICATION_EXCLUSIONS.get(key) if key else None


def is_excluded(pub):
    """True for errata / editorials / corrigenda / rejoinders / comments,
    whatever source they came from (eSpace, ORCID, Crossref). This is the
    check that must run post-info/, because those sources re-introduce rows
    the adapter's own filter never sees."""
    title = pub.get("title")
    if title and _EXCLUDE_TITLE.match(title):
        return True
    return reviewed_exclusion(pub) is not None


def clean_pub(pub, log=None):

    raw_doi = (pub.get("doi") or "").strip().lower()

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
    venue = (pub.get("journal") or "").strip().lower()
    # RePEc is an index/repository, and OpenAlex can expose labels such as
    # "RePEc: Research Papers in Economics" rather than the bare "repec"
    # token.  Treat those as repository records so they cannot masquerade as
    # journal articles in the final export.
    if venue in _PREPRINT_VENUES or venue.startswith("repec:"):
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
    # ORCID builds its link as "https://doi.org/" + the DOI it was given, and
    # some ORCID records store the DOI as a URL already, which gave links like
    # https://doi.org/http://dx.doi.org/10.2308/bria-50333. The DOI itself is
    # cleaned above, so a doi.org link is simply rebuilt from it.
    if (pub["doi"] and pub.get("link")
            and _DOI_LINK_RE.match(pub["link"])
            and pub["link"] != f"https://doi.org/{pub['doi']}"):
        note(f"    link    {who}: {pub['link']!r} -> rebuilt from the DOI")
        pub["link"] = f"https://doi.org/{pub['doi']}"

    if pub.get("doi") and pub["doi"].lower().startswith(_PREPRINT_DOI_PREFIXES):
        if pub.get("type") != "Preprint":
            note(f"    type    {who}: {pub.get('type')!r} -> 'Preprint'  (SSRN DOI)")
        pub["type"] = "Preprint"

    for field, value in override_fields(pub, raw_doi).items():
        pub[field] = value
    if pub.get("doi") and not pub.get("link"):
        pub["link"] = f"https://doi.org/{pub['doi']}"

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
            reviewed = reviewed_exclusion(p)
            reason = f" ({reviewed['reason']})" if reviewed else ""
            print(f"    drop    {p.get('name','?')}: {p.get('title')!r}{reason}")
        print(f"  kept {len(kept)}, dropped {len(dropped)}")

    return kept
