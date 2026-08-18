"""
Journal name matching — CITS3200 Group 20.

Used by abdc.py, scimago.py and journals.py. All three have the same problem:
take a journal name written by a university website and find it in a reference
list that writes it slightly differently. Doing that three times, three
different ways, would let the same journal end up A* in one file and unrated in
another.

Nothing in here guesses. Matching is exact — on ISSN first, then on a
normalised title, then on a small set of labelled variants. Callers record how
each row matched so the joins can be audited.
"""

import csv
import os
import re
import unicodedata

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
# UNSW writes "AUSTRALIAN TAX REVIEW", the reference lists write "Australian Tax
# Review". UNSW writes "The Journal of Finance", they write "Journal of
# Finance". Ampersands, accents, punctuation and stray whitespace all differ.
# These are formatting differences, not different journals, so they are
# normalised away before comparing. Nothing here changes which journal a title
# refers to.
LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+")
NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
SPACES = re.compile(r"\s+")

# Subtitles are written every possible way: a colon, an en or em dash, or a
# spaced hyphen. UNSW writes "ABACUS - A Journal of Accounting, Finance and
# Business Studies" where the reference lists write plain "Abacus".
SUBTITLE = re.compile(r"\s*[:–—]\s*|\s+-\s+")
TRAILING_JOURNAL = re.compile(r"\s+journal$", re.IGNORECASE)

ISSN_RE = re.compile(r"\b(\d{4})-?(\d{3}[\dxX])\b")


def normalise(name):
    """Reduce a journal title to a comparable key. Returns '' for empty input."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = NON_ALNUM.sub(" ", text)
    text = SPACES.sub(" ", text).strip()
    text = LEADING_ARTICLE.sub("", text)
    return text


def normalise_issn(value):
    """ISSNs appear as 0022-1082, 00221082, and sometimes with stray text."""
    if not value:
        return None
    match = ISSN_RE.search(str(value))
    return f"{match.group(1)}-{match.group(2).upper()}" if match else None


def split_issns(value):
    """Scimago packs several ISSNs into one field: '15424863, 00079235'."""
    if not value:
        return []
    found = []
    for part in re.split(r"[,;|]", str(value)):
        issn = normalise_issn(part)
        if issn and issn not in found:
            found.append(issn)
    return found


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def build_aliases(index, minimum_length=8):
    """Map a reference journal's leading words to its record, where unambiguous.

    ABDC lists "Auditing: A Journal of Practice and Theory"; UNSW writes plain
    "Auditing". Indexing the part before the subtitle recovers those, but only
    when it is safe to: an alias is discarded if it collides with a real full
    title or with another journal's prefix.
    """
    aliases, ambiguous = {}, set()
    for key, record in index.items():
        head = SUBTITLE.split(record["title"])[0]
        alias = normalise(head)
        if not alias or alias == key or len(alias) < minimum_length:
            continue
        if alias in index:                       # collides with a full title
            ambiguous.add(alias)
        elif alias in aliases and aliases[alias]["title"] != record["title"]:
            ambiguous.add(alias)                 # two journals share a prefix
        else:
            aliases[alias] = record
    for alias in ambiguous:
        aliases.pop(alias, None)
    return aliases


def title_variants(journal_name):
    """Forms of OUR journal name to try, in order of decreasing confidence."""
    name = str(journal_name)
    yield re.sub(r"\s*\([^)]*\)\s*$", "", name)   # "… (Australia)"
    yield SUBTITLE.split(name)[0]                 # "ABACUS - A Journal of …"
    yield TRAILING_JOURNAL.sub("", name)          # "Australian Tax Forum Journal"


def match_journal(journal_name, issn, index, aliases=None):
    """Return (record, match_type) or (None, None). Never guesses."""
    issn_key = normalise_issn(issn)
    if issn_key and issn_key in index:
        return index[issn_key], "issn"

    key = normalise(journal_name)
    if not key:
        return None, None
    if key in index:
        return index[key], "title"

    # Our title carries something the reference list's does not — a trailing
    # qualifier, a subtitle, or a redundant "Journal" on the end.
    for variant in title_variants(journal_name):
        variant_key = normalise(variant)
        if variant_key and variant_key != key and variant_key in index:
            return index[variant_key], "title-variant"

    # The reverse: their title carries a subtitle and ours does not.
    if aliases:
        for candidate in (key, *(normalise(v) for v in title_variants(journal_name))):
            if candidate and candidate in aliases:
                return aliases[candidate], "prefix"

    return None, None


def fuzzy_match(journal_name, index, cutoff=0.93):
    """Opt-in only. Conservative, and always tagged so it can be filtered."""
    import difflib
    key = normalise(journal_name)
    if not key or len(key) < 12:      # short titles are too easy to confuse
        return None, None
    titles = [k for k in index if not ISSN_RE.fullmatch(k)]
    close = difflib.get_close_matches(key, titles, n=1, cutoff=cutoff)
    return (index[close[0]], "fuzzy") if close else (None, None)


# ---------------------------------------------------------------------------
# Publications CSVs — our scrapers do not all name their columns the same way
# ---------------------------------------------------------------------------
PUB_JOURNAL_HEADERS = ("journal_name", "journal", "journal_key", "source_title",
                       "journal_canonical", "publication_venue", "venue")
PUB_ISSN_HEADERS = ("issn", "issns", "journal_issn")


def _find(fieldnames, candidates):
    wanted = [normalise(h).replace(" ", "_") for h in candidates]
    for column in fieldnames:
        if normalise(column).replace(" ", "_") in wanted:
            return column
    return None


def read_publications(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{path} has no rows.")
    return rows, list(rows[0].keys())


def find_columns(fieldnames, journal_column=None):
    column = journal_column or _find(fieldnames, PUB_JOURNAL_HEADERS)
    if column is None:
        raise SystemExit(
            f"No journal-name column found. Columns are: {fieldnames}. "
            "Use --journal-column to name it.")
    return column, _find(fieldnames, PUB_ISSN_HEADERS)


def write_enriched(publications_path, fieldnames, added_columns, rows,
                   unmatched, suffix):
    """Write <name>_<suffix>.csv and <name>_<suffix>_unmatched.csv."""
    base, _ = os.path.splitext(publications_path)
    out_path = f"{base}_{suffix}.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + added_columns,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    unmatched_path = f"{base}_{suffix}_unmatched.csv"
    with open(unmatched_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["journal_name", "rows_affected"])
        writer.writerows(unmatched.most_common())
    return out_path, unmatched_path


def report(counts, unmatched, total, out_path, unmatched_path, skip_keys):
    rated = sum(v for k, v in counts.items() if k not in skip_keys)
    print(f"\n  {out_path}")
    print(f"  {unmatched_path}")
    print(f"\n  matched:   {rated} of {total} rows ({rated / total:.0%})")
    for how, n in counts.most_common():
        print(f"     {how:<16} {n}")
    print(f"  distinct journals not matched: {len(unmatched)}")
    if unmatched:
        print("  most common:")
        for name, n in unmatched.most_common(10):
            print(f"     {n:>4}  {name[:70]}")
