"""Clarivate Journal Citation Reports — JIF and 5-year JIF by ISSN.

Needs an API key in .env (CLARIVATE_API_KEY). Two calls per journal:
search by ISSN for a journal id, then fetch that year's report. Rate limit
is 5 requests per second.

Values come back inconsistently typed — `jif` is a string, `jif5Years` a
float — so both are coerced. Journals with no report for the requested
year return None rather than raising; ESCI-only titles often have none.
"""

from core.config import JCR_BASE, JCR_SLEEP, JCR_YEAR, jcr_headers
from core.http import cached_get


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _lookup_issn(issn, headers):
    hits = (cached_get(f"{JCR_BASE}/journals", params={"q": issn, "limit": 5},
                       headers=headers, sleep=JCR_SLEEP) or {}).get("hits") or []
    if not hits:
        return None

    report = cached_get(f"{JCR_BASE}/journals/{hits[0]['id']}/reports/year/{JCR_YEAR}",
                        headers=headers, sleep=JCR_SLEEP, allow_404=True)
    if not report:
        return None

    im = (report.get("metrics") or {}).get("impactMetrics") or {}
    return {
        "impact_factor": _num(im.get("jif")),
        "impact_factor_5yr": _num(im.get("jif5Years")),
    }


def enrich(pubs, verbose=True):
    headers = jcr_headers()
    issns = sorted({i for x in pubs for i in (x.get("issns") or [])})
    cache = {}

    for n, issn in enumerate(issns, 1):
        try:
            cache[issn] = _lookup_issn(issn, headers)
        except Exception as e:
            cache[issn] = None
            print(f"  {issn}: {type(e).__name__} {e}")
        if verbose and n % 25 == 0:
            print(f"  {n}/{len(issns)} ISSNs")

    for x in pubs:
        hit = next((cache[i] for i in (x.get("issns") or []) if cache.get(i)), None)
        x["impact_factor"] = hit["impact_factor"] if hit else None
        x["impact_factor_5yr"] = hit["impact_factor_5yr"] if hit else None
        x["jcr_year"] = JCR_YEAR if hit else None

    if verbose:
        arts = [x for x in pubs if x.get("type") == "Journal Article"]
        n = sum(1 for x in arts if x.get("impact_factor") is not None)
        print(f"clarivate: {n} of {len(arts)} journal articles have a "
              f"{JCR_YEAR} JIF ({len(issns)} ISSNs queried)")
    return pubs
