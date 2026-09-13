"""Extra publications listed on each researcher's own ORCID record.

Self-maintained, so coverage varies — some records are comprehensive,
others near-empty. Roughly half the entries carry an ISSN, which is enough
for the ISSN enrichment to run on them. Also exposes Scopus and Web of
Science ids, useful later if either becomes available.

Cannot be contaminated the way OpenAlex can: an ORCID record belongs to one
person by construction.
"""

from core.config import ORCID_BASE, ORCID_HEADERS
from core.http import cached_get
from core.schema import blank_pub, clean_journal, norm_type


def _seen(pubs):
    out = {}
    for x in pubs:
        if x.get("doi"):
            out.setdefault(x["name"], set()).add(x["doi"].lower())
    return out


def retrieve(records, pubs, verbose=True):
    have = _seen(pubs)
    added = 0

    for p in records:
        orcid = p.get("orcid")
        if not orcid:
            continue
        name = p["name_clean"]

        try:
            data = cached_get(f"{ORCID_BASE}/{orcid}/works",
                              headers=ORCID_HEADERS, sleep=0.5)
        except Exception as e:
            print(f"  {name}: {type(e).__name__} {e}")
            continue

        seen = have.setdefault(name, set())
        n = 0
        for g in data.get("group", []):
            s = g["work-summary"][0]
            ids = {}
            for e in (s.get("external-ids") or {}).get("external-id", []):
                ids.setdefault(e["external-id-type"], e["external-id-value"])

            doi = (ids.get("doi") or "").lower()
            if not doi or doi in seen:
                continue
            seen.add(doi)

            year = ((s.get("publication-date") or {}).get("year") or {}).get("value")
            issn = ids.get("issn")

            pubs.append(blank_pub(
                name=name,
                source_id=p.get("source_id"),
                title=((s.get("title") or {}).get("title") or {}).get("value", ""),
                year=str(year) if year else None,
                type=norm_type(s.get("type")),
                issns=[issn] if issn else [],
                journal=clean_journal(((s.get("journal-title") or {}) or {}).get("value")),
                doi=doi,
                link=f"https://doi.org/{doi}",
                source="ORCID",
                scopus_id=ids.get("eid"),
                wos_id=ids.get("wosuid"),
            ))
            n += 1

        added += n
        if verbose and n:
            print(f"  {name}: +{n}")

    if verbose:
        print(f"orcid: added {added} records; {len(pubs)} total")
    return pubs
