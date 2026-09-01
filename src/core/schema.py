"""The contract between adapters and everything downstream.

An adapter for any university returns two lists of dicts in this shape.
The retrieval, enrichment and export modules depend on these keys and
nothing else, so a new university needs a new adapter and no other change.
"""

# --- what an adapter must produce ----------------------------------------

STAFF_REQUIRED = [
    "name",             # as printed, prefix included
    "name_clean",       # prefix stripped — the join key to publications
    "university",
    "discipline",       # Accounting | Finance
    "profile_url",
]

STAFF_OPTIONAL = [
    "title",            # raw job title from the directory
    "title_clean",      # normalised onto the ladder
    "prefix",           # honorific, if any
    "source_id",        # repository author id (eSpace id, Pure id, ...)
    "orcid",
]

PUB_REQUIRED = [
    "name",             # matches staff.name_clean
    "title",
    "year",             # string, four digits
    "type",             # normalised — see TYPES
    "source",           # which system supplied this record
]

PUB_OPTIONAL = [
    "doi", "issns", "journal", "n_authors", "authors", "link",
    "publisher", "journal_canonical", "source_id",
]

# --- one type vocabulary across every source -----------------------------

# Sources disagree: eSpace says "Journal Article", ORCID and Crossref say
# "journal-article", OpenAlex says "article". Adapters normalise to these
# labels so the export filter behaves identically whatever the origin.
TYPES = {
    "Journal Article", "Preprint", "Conference Paper", "Book", "Book Chapter",
    "Thesis", "Research Report", "Working Paper", "Data Collection",
    "Newspaper Article", "Other",
}

TYPE_MAP = {
    "journal-article": "Journal Article",
    "journal_article": "Journal Article",
    "article": "Journal Article",
    "review": "Journal Article",
    "posted-content": "Preprint",
    "preprint": "Preprint",
    "conference-paper": "Conference Paper",
    "proceedings-article": "Conference Paper",
    "book-chapter": "Book Chapter",
    "book_chapter": "Book Chapter",
    "book": "Book",
    "dissertation": "Thesis",
    "dissertation-thesis": "Thesis",
    "report": "Research Report",
    "working-paper": "Working Paper",
    "data-set": "Data Collection",
    "dataset": "Data Collection",
}

# Repositories that appear in a journal-name field but are not journals.
NOT_A_JOURNAL = {
    "ssrn", "ssrn electronic journal", "arxiv", "arxiv.org",
    "preprints.org", "research square", "biorxiv", "repec",
}


def norm_type(t):
    """Map any source's type string onto the shared vocabulary."""
    if not t:
        return None
    t = t.strip()
    return TYPE_MAP.get(t.lower(), t if t in TYPES else t)


def clean_journal(name):
    """Blank out repository names masquerading as journals."""
    if not name:
        return None
    return None if name.strip().lower() in NOT_A_JOURNAL else name.strip()


def blank_pub(**kw):
    """A publication record with every key present, so downstream code can
    assume the shape rather than defending against missing keys."""
    rec = {
        "name": None, "source_id": None, "title": None, "year": None,
        "type": None, "n_authors": None, "authors": None, "issns": [],
        "journal": None, "journal_canonical": None, "publisher": None,
        "doi": None, "link": None, "source": None,
    }
    rec.update(kw)
    return rec


def validate(records, pubs, verbose=True):
    """Check an adapter's output before the shared pipeline runs on it."""
    problems = []

    for i, r in enumerate(records):
        missing = [k for k in STAFF_REQUIRED if not r.get(k)]
        if missing:
            problems.append(f"staff[{i}] {r.get('name', '?')}: missing {missing}")

    names = {r["name_clean"] for r in records if r.get("name_clean")}
    for i, p in enumerate(pubs):
        missing = [k for k in PUB_REQUIRED if not p.get(k)]
        if missing:
            problems.append(f"pub[{i}] {str(p.get('title'))[:40]}: missing {missing}")
        if p.get("name") and p["name"] not in names:
            problems.append(f"pub[{i}]: name {p['name']!r} is not in staff")
        if p.get("type") and p["type"] not in TYPES:
            problems.append(f"pub[{i}]: type {p['type']!r} not in the vocabulary")

    if verbose:
        if problems:
            print(f"contract: {len(problems)} problems")
            for p in problems[:15]:
                print("   ", p)
            if len(problems) > 15:
                print(f"    ... and {len(problems) - 15} more")
        else:
            print(f"contract: ok — {len(records)} staff, {len(pubs)} publications")
    return problems
