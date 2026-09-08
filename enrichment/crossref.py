"""Crossref ISSN fill, matched by DOI — for rows OpenAlex's DOI pass still misses.

Deliberately narrow. This only ever looks at a row that already has a doi
and an empty issns, and the only way in is an exact match against
Crossref's single-work endpoint (works/{doi}). No title search, no fuzzy
matching, no picking the closest-looking candidate. A DOI Crossref does not
recognise, or a work with no ISSN on file, is left alone and counted as
such rather than filled with a best guess — a blank issns is honest; a
wrong one is worse than no ISSN at all, because abdc, clarivate and scimago
all join on issns and nothing else, and a wrong value poisons that join
silently.

Runs after enrichment/openalex.py on purpose. OpenAlex's DOI pass already
fills most of the ISSN gap on its own (see that module's docstring); this
is the second, independent pass over whatever is still empty afterwards,
not a replacement for it.

Free, no key, same politeness contract as info/crossref.py: CR_HEADERS
carries a contact email, and every real request sleeps afterward.
"""

from core.config import CROSSREF_BASE, CR_HEADERS
from core.http import cached_get
from core.schema import clean_journal, norm_type
from enrichment.openalex import bare_doi

# Hard ceiling on distinct Crossref lookups made in one enrich() call. A
# full run over every university's DOI-no-issn gap today is a little over
# 1,500 distinct DOIs, so this is headroom, not a number expected to bind —
# it exists so a future bug (or a much bigger dataset) fails loudly with a
# "capped" count rather than silently hammering Crossref.
REQUEST_CAP = 2000


def _lookup(doi):
    """One works/{doi} call. Returns (message dict, outcome label).

    allow_404=True hands back None on a 404 instead of raising — Crossref
    genuinely not having a DOI is a normal, expected outcome here, not a
    failure. Anything else that goes wrong (timeout, malformed JSON, a 429
    that outlasts core.http's own retries) is caught and reported as an
    error rather than left to crash the run partway through a university.
    """
    try:
        data = cached_get(f"{CROSSREF_BASE}/{doi}", headers=CR_HEADERS,
                          sleep=0.5, allow_404=True)
    except Exception as e:
        return None, f"error: {type(e).__name__} {e}"
    if data is None:
        return None, "not_found"
    return (data.get("message") or {}), "resolved"


def enrich(pubs, verbose=True, record_outcomes=None):
    """Fill issns, journal, publisher and type on rows Crossref can resolve
    by DOI, and only those. Mutates pubs in place; returns pubs, matching
    every other enrichment stage.

    Nothing already present on a row is ever touched — issns, journal,
    publisher and type are each only set when currently empty, the same
    additive rule enrichment/openalex.py uses for issns and publisher.

    record_outcomes, if given a list, gets one dict appended per eligible
    row — {"doi", "outcome", ...} — in the order those rows were
    encountered, so a caller can build a breakdown (e.g. per university)
    without re-implementing the lookup. Ordinary callers (run.py) leave
    this as None and get exactly the behaviour every other enrich() has.
    """
    targets = [p for p in pubs if p.get("doi") and not p.get("issns")]

    cache = {}
    calls = capped = 0
    filled = no_issn = not_found = errored = 0
    journal_filled = publisher_filled = type_filled = 0

    for p in targets:
        doi = bare_doi(p.get("doi"))
        if not doi:
            continue

        if doi not in cache:
            if calls >= REQUEST_CAP:
                capped += 1
                if record_outcomes is not None:
                    record_outcomes.append({"doi": doi, "outcome": "capped"})
                continue
            cache[doi] = _lookup(doi)
            calls += 1
        msg, outcome = cache[doi]

        if outcome == "not_found":
            not_found += 1
            if record_outcomes is not None:
                record_outcomes.append({"doi": doi, "outcome": "not_found"})
            continue
        if outcome != "resolved":
            errored += 1
            if record_outcomes is not None:
                record_outcomes.append({"doi": doi, "outcome": "error", "detail": outcome})
            continue

        issns = [i for i in (msg.get("ISSN") or []) if i]
        if issns:
            p["issns"] = issns
            filled += 1
        else:
            no_issn += 1

        if not p.get("journal"):
            titles = msg.get("container-title") or []
            journal = clean_journal(titles[0] if titles else None)
            if journal:
                p["journal"] = journal
                journal_filled += 1

        if not p.get("publisher") and msg.get("publisher"):
            p["publisher"] = msg["publisher"]
            publisher_filled += 1

        if not p.get("type") and msg.get("type"):
            t = norm_type(msg["type"])
            if t:
                p["type"] = t
                type_filled += 1

        if record_outcomes is not None:
            record_outcomes.append({
                "doi": doi,
                "outcome": "filled" if issns else "no_issn",
                "issns": issns,
            })

    if verbose:
        print(f"crossref: {len(targets)} row(s) eligible (doi, no issn) · "
              f"{calls} lookup(s) against Crossref for {len(cache)} distinct doi(s)")
        print(f"          {filled} issn filled · {no_issn} resolved with no issn on "
              f"file · {not_found} doi not found · {errored} errored"
              + (f" · {capped} skipped (request cap)" if capped else ""))
        if journal_filled or publisher_filled or type_filled:
            print(f"          also filled: {journal_filled} journal, "
                  f"{publisher_filled} publisher, {type_filled} type")

    return pubs
