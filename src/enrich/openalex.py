"""OpenAlex citation metrics and open-access status, matched by DOI.

Matched on DOI rather than ORCID deliberately. OpenAlex builds author
entities by inference and merges same-name researchers, so ORCID-keyed
retrieval returns other people's work for some names. A DOI lookup returns
exactly one paper and cannot be contaminated that way.

Free, no key, CC0 licensed. The contact email in the User-Agent gets you
into their faster pool.
"""

from core.config import OA_HEADERS, OPENALEX_BASE
from core.http import cached_get

CHUNK = 25          # 50 per filter times out on their side often enough to matter


def enrich(pubs, verbose=True):
    dois = sorted({x["doi"] for x in pubs if x.get("doi")})
    found = {}

    for i in range(0, len(dois), CHUNK):
        chunk = dois[i:i + CHUNK]
        try:
            data = cached_get(OPENALEX_BASE,
                              params={"filter": "doi:" + "|".join(chunk),
                                      "per-page": CHUNK},
                              headers=OA_HEADERS, timeout=60, sleep=1.0)
        except Exception as e:
            print(f"  chunk {i // CHUNK + 1}: {type(e).__name__} {e}")
            continue

        for w in data.get("results", []):
            key = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            cnp = w.get("citation_normalized_percentile") or {}
            oa = w.get("open_access") or {}
            found[key] = {
                "citation_percentile": cnp.get("value"),
                "cited_by_count": w.get("cited_by_count"),
                "fwci": w.get("fwci"),
                "is_oa": oa.get("is_oa"),
                "oa_status": oa.get("oa_status"),
                "oa_url": oa.get("oa_url"),
            }
        if verbose:
            print(f"  {min(i + CHUNK, len(dois))}/{len(dois)} — {len(found)} matched")

    for x in pubs:
        hit = found.get((x.get("doi") or "").lower()) or {}
        for k in ("citation_percentile", "cited_by_count", "fwci",
                  "is_oa", "oa_status", "oa_url"):
            x[k] = hit.get(k)

    if verbose:
        arts = [x for x in pubs if x.get("type") == "Journal Article"]
        n = sum(1 for x in arts if x.get("citation_percentile") is not None)
        oa = sum(1 for x in arts if x.get("oa_url"))
        print(f"openalex: {n} of {len(arts)} enriched · {oa} have a free full text")
    return pubs
