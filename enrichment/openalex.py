"""OpenAlex citation metrics, ISSNs and open-access status, matched by DOI.

Matched on DOI rather than ORCID deliberately. OpenAlex builds author
entities by inference and merges same-name researchers, so ORCID-keyed
retrieval returns other people's work for some names. A DOI lookup returns
exactly one paper and cannot be contaminated that way.

CC0 licensed and free to run. A key is worth having anyway: it raises the
daily budget from $0.10 to $1, and it is free from
https://openalex.org/pricing with no paid tier involved. This step is cheap
either way — a DOI filter costs $0.0001 and takes 25 DOIs per call, so a whole
university comes to under a cent.

THIS IS ALSO WHERE MOST OF OUR ISSNs COME FROM
----------------------------------------------
ABDC, Scimago and Clarivate all match on `issns` and on nothing else. An
adapter reading a repository (UQ eSpace) gets ISSNs with the record; one
reading a staff directory does not. UNSW publishes no ISSN anywhere on the
site, so without this step every UNSW row reaches all three ranking joins with
an empty list and comes out unrated. 1,650 of UNSW's 1,973 ISSNs arrive here.

ISSNs are added to the list rather than replacing it, and a value an adapter
already supplied is never overwritten: eSpace's ISSN came off the publisher's
own record, ours off whichever copy of the paper OpenAlex resolved.
"""

import re

from core.config import OA_HEADERS, OPENALEX_BASE, openalex_budget
from core.http import cached_get

CHUNK = 25          # 50 per filter times out on their side often enough to matter

# What a DOI looks like with the URL wrappers off. Both sides of the match are
# normalised, because a record carries "10.1111/X", "https://doi.org/10.1111/x"
# or "http://dx.doi.org/10.1111/x" depending on which source supplied it, and
# those are one paper. UNSW's profile pages use the dx.doi.org form.
DOI_PREFIX = re.compile(r"^(https?://)?(dx\.)?doi\.org/|^doi:", re.I)

# ISSNs belonging to a preprint server rather than to a journal. When OpenAlex
# resolves a DOI to the SSRN or arXiv copy of a paper it returns that
# repository's ISSN, and the row then names the right journal while carrying a
# different journal's identifier. Fifty UNSW rows had SSRN's ISSN against
# Australian Tax Forum. Scimago rates SSRN Electronic Journal, so those rows
# would have come out with a real-looking SJR that belongs to a repository.
# An ISSN that is not the journal's is worse than no ISSN at all, because
# everything downstream joins on it.
AGGREGATOR_ISSNS = {
    "1556-5068": "ssrn",
    "2331-8422": "arxiv",
}

METRICS = ("citation_percentile", "citation_top_10_percent", "cited_by_count",
           "fwci", "is_oa", "oa_status", "oa_url", "openalex_id")


def bare_doi(value):
    """'https://doi.org/10.1111/X' -> '10.1111/x'."""
    if not value:
        return None
    return DOI_PREFIX.sub("", str(value).strip()).lower() or None


def hyphenate(issn):
    """'00221082' -> '0022-1082'.

    ABDC's list is hyphenated and Scimago's is not, and enrichment/scimago.py
    strips hyphens on lookup, so the hyphenated form is the one that matches
    both. Anything that is not eight characters ending in a digit or X is not
    an ISSN and is dropped rather than reshaped into something that looks like
    one.
    """
    if not issn:
        return None
    text = str(issn).strip().upper().replace("-", "")
    if len(text) != 8 or not text[:7].isdigit() or text[7] not in "0123456789X":
        return None
    return f"{text[:4]}-{text[4:]}"


def issns_of(work):
    """Every ISSN on the work's source, hyphenated, in preference order."""
    source = ((work.get("primary_location") or {}).get("source")) or {}
    found = []
    for value in [source.get("issn_l")] + list(source.get("issn") or []):
        issn = hyphenate(value)
        if issn and issn not in found:
            found.append(issn)
    return found


def keep_issns(issns, row_journal, source_name):
    """Drop a repository's ISSN from a row that names a real journal.

    The comparison is against OUR journal name, not against OpenAlex's own
    source name. Checking the source against itself proves nothing: OpenAlex
    is internally consistent, and reports SSRN's ISSN alongside the display
    name "SSRN Electronic Journal" quite correctly. The error only becomes
    visible next to the row it landed on — Australian Tax Forum carrying
    1556-5068 — which is where fifty UNSW rows were.

    Where the row has no journal name, core.schema.clean_journal has usually
    just blanked a repository name, so the source name is the honest fallback
    and an SSRN working paper keeps SSRN's ISSN.
    """
    against = (row_journal or source_name or "").lower()
    for issn in issns:
        if issn in AGGREGATOR_ISSNS and AGGREGATOR_ISSNS[issn] not in against:
            # The paper is in a real journal and OpenAlex resolved the DOI to
            # the repository copy. Take none of them rather than guess which.
            return []
    return issns


def extract(work):
    cnp = work.get("citation_normalized_percentile") or {}
    oa = work.get("open_access") or {}
    source = ((work.get("primary_location") or {}).get("source")) or {}
    return {
        "citation_percentile": cnp.get("value"),
        "citation_top_10_percent": cnp.get("is_in_top_10_percent"),
        "cited_by_count": work.get("cited_by_count"),
        "fwci": work.get("fwci"),
        "is_oa": oa.get("is_oa"),
        "oa_status": oa.get("oa_status"),
        "oa_url": oa.get("oa_url"),
        "openalex_id": (work.get("id") or "").rsplit("/", 1)[-1] or None,
        # Staff directories carry no publisher, so that column sat empty on
        # every UNSW row. OpenAlex knows it for anything already matched and
        # returns it in the same response, so it costs nothing extra.
        "publisher": source.get("host_organization_name") or None,
        "issns": issns_of(work),
        # Carried so the aggregator check can be made against our row's
        # journal name rather than against OpenAlex's own source name.
        "_source_name": source.get("display_name") or None,
    }


def enrich(pubs, verbose=True):
    dois = sorted({d for d in (bare_doi(x.get("doi")) for x in pubs) if d})
    found, calls = {}, 0

    for i in range(0, len(dois), CHUNK):
        chunk = dois[i:i + CHUNK]
        calls += 1
        try:
            data = cached_get(OPENALEX_BASE,
                              params={"filter": "doi:" + "|".join(chunk),
                                      "per-page": CHUNK},
                              headers=OA_HEADERS, timeout=60, sleep=1.0)
        except Exception as e:
            print(f"  chunk {i // CHUNK + 1}: {type(e).__name__} {e}")
            continue

        for w in data.get("results", []):
            key = bare_doi(w.get("doi"))
            if key:
                found[key] = extract(w)
        if verbose:
            print(f"  {min(i + CHUNK, len(dois))}/{len(dois)} — {len(found)} matched")

    gained = 0
    for x in pubs:
        hit = found.get(bare_doi(x.get("doi"))) or {}

        for k in METRICS:
            x[k] = hit.get(k)

        # Additive, and never destructive. A row that arrived with an ISSN or a
        # publisher from a repository record has the better one: theirs came
        # off the publisher's own metadata, ours off whichever copy of the
        # paper OpenAlex happened to resolve.
        before = list(x.get("issns") or [])
        merged = list(before)
        for issn in keep_issns(hit.get("issns") or [], x.get("journal"),
                               hit.get("_source_name")):
            if issn not in merged:
                merged.append(issn)
        if merged and not before and x.get("type") == "Journal Article":
            # Counted over journal articles only, so it sits alongside the
            # coverage figure below rather than contradicting it.
            gained += 1
        x["issns"] = merged

        if hit.get("publisher") and not x.get("publisher"):
            x["publisher"] = hit["publisher"]

    if verbose:
        arts = [x for x in pubs if x.get("type") == "Journal Article"]
        n = sum(1 for x in arts if x.get("citation_percentile") is not None)
        oa = sum(1 for x in arts if x.get("oa_url"))
        rated = sum(1 for x in arts if x.get("issns"))
        print(f"openalex: {n} of {len(arts)} enriched · {oa} have a free full text")
        print(f"          {rated} of {len(arts)} journal articles carry an ISSN "
              f"({gained} gained one here)")
        if arts and not rated:
            # abdc, clarivate and scimago all match on issns and nothing else,
            # so this is not a warning about one column, it is the whole run.
            print("          ! nothing carries an ISSN. abdc, clarivate and "
                  "scimago all match\n            on issns, so every row will "
                  "come out unrated.")
        print(f"          {calls} calls, about ${calls * 0.0001:.4f} of today's "
              f"${openalex_budget():.2f} budget")
    return pubs
