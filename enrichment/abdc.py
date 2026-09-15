"""ABDC Journal Quality List — quality_rank by ISSN, falling back to an
exact normalised-title match when a row has no ISSN at all (e.g. a page
citation with no DOI, so no ISSN was ever looked up for it).

Two quirks in the spreadsheet: ISSNs carry trailing tabs and ratings carry
trailing spaces, so both need stripping or nothing matches at all.

The canonical ABDC title is also written back as `abdc_title`, and the
export uses it as the journal key. That collapses print/online ISSN
variants and "and" vs "&" spellings onto one journal row.
"""

import re
import unicodedata

import pandas as pd

from core.config import (ABDC_EDITION, ABDC_FILE, ABDC_HEADER,
                         ABDC_RATING_COL, ABDC_SHEET)

_lookup = None
_title_lookup = None

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalise_title(title):
    """NFKC, lowercase, '&' -> ' and ', strip a leading 'the ', collapse
    every run of non-alphanumeric characters to one space, then trim.
    Shared with anu_scraper.py's journal-preference check — keep both in
    sync if this changes."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", title).lower()
    t = t.replace("&", " and ")
    t = _NON_ALNUM_RE.sub(" ", t).strip()
    if t.startswith("the "):
        t = t[4:]
    return t.strip()


def _build():
    global _lookup, _title_lookup
    if _lookup is not None:
        return _lookup

    df = pd.read_excel(ABDC_FILE, sheet_name=ABDC_SHEET, header=ABDC_HEADER)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    df.columns = [str(c).strip() for c in df.columns]

    _lookup = {}
    _title_lookup = {}
    for _, row in df.iterrows():
        rating = str(row[ABDC_RATING_COL]).strip()
        title = str(row["Journal Title"]).strip()
        issns = []
        for col in ("ISSN", "ISSNOnline"):
            v = str(row[col]).strip()
            if v and v.lower() != "nan":
                _lookup[v] = {"rating": rating, "title": title}
                issns.append(v)

        key = normalise_title(title)
        if not key:
            continue
        existing = _title_lookup.get(key)
        if existing is not None and existing["rating"] != rating:
            raise RuntimeError(
                f"ABDC title collision: {existing['title']!r} and {title!r} "
                f"both normalise to {key!r} but carry different ratings "
                f"({existing['rating']!r} vs {rating!r})")
        _title_lookup[key] = {"rating": rating, "title": title, "issns": issns}

    # A wrong header row or sheet name yields a lookup full of junk and
    # silently unrates everything, so fail loudly instead.
    if len(_lookup) < 2000:
        raise RuntimeError(
            f"ABDC lookup has only {len(_lookup)} entries — check "
            f"ABDC_SHEET, ABDC_HEADER and ABDC_RATING_COL in core/config.py")
    return _lookup


def known_titles():
    """The set of every normalised ABDC journal title. Used by
    anu_scraper.py to prefer a known journal name when splitting a
    comma-flow citation — see FIX C in docs/DECISIONS.md."""
    _build()
    return set(_title_lookup)


def title_rating(normalised_title):
    """rating for an already-normalised title, or None."""
    _build()
    hit = _title_lookup.get(normalised_title)
    return hit["rating"] if hit else None


def title_issns(normalised_title):
    """The ABDC-sheet ISSN/ISSNOnline values for an already-normalised
    title, or None if the title isn't known. Used by FIX E2 (a title-
    matched row with no ISSN of its own picks these up) and by
    scratch/_anu15/abdc_fallback_check.py's E2 safety check."""
    _build()
    hit = _title_lookup.get(normalised_title)
    return list(hit["issns"]) if hit else None


def enrich(pubs, verbose=True):
    lookup = _build()
    issn_hits = title_hits = issn_backfilled = 0
    for x in pubs:
        hit = next((lookup[i] for i in (x.get("issns") or []) if i in lookup), None)
        if hit:
            x["abdc"] = hit["rating"]
            x["abdc_title"] = hit["title"]
            x["abdc_edition"] = ABDC_EDITION
            x["abdc_match"] = "issn"
            issn_hits += 1
            continue

        title_hit = None
        journal = x.get("journal")
        if journal:
            title_hit = _title_lookup.get(normalise_title(journal))
        if title_hit:
            x["abdc"] = title_hit["rating"]
            x["abdc_title"] = title_hit["title"]
            x["abdc_edition"] = ABDC_EDITION
            x["abdc_match"] = "title"
            title_hits += 1
            # FIX E2: Clarivate/Scimago both join on ISSN only, so a
            # title-matched row with no ISSN of its own still gets no JIF
            # and no SJR even though the journal is known. Give it the
            # ABDC sheet's own ISSN(s) — but only when the row has none at
            # all; never overwrite or add to ISSNs a row already carries.
            if not x.get("issns") and title_hit.get("issns"):
                x["issns"] = list(title_hit["issns"])
                x["issn_source"] = "abdc_title"
                issn_backfilled += 1
        else:
            x["abdc"] = None
            x["abdc_title"] = None
            x["abdc_edition"] = None
            x["abdc_match"] = None

    if verbose:
        arts = [x for x in pubs if x.get("type") == "Journal Article"]
        n = sum(1 for x in arts if x.get("abdc"))
        print(f"abdc: {n} of {len(arts)} journal articles rated "
              f"({issn_hits} by ISSN, {title_hits} by title fallback, "
              f"{issn_backfilled} of those gained an ISSN from the ABDC "
              f"title match; {len(lookup)} ISSNs in the {ABDC_EDITION} list)")
    return pubs
