"""Extra publications Crossref links to each researcher's ORCID.

Crossref only attaches an ORCID when the publisher recorded it at
submission, so it under-reports rather than over-reporting. That is the
safer failure mode: it will miss older work, but it will not attribute
someone else's papers to your researcher.

Free, no key. Cursor pagination, because prolific researchers exceed the
200-row page limit.
"""

from core.config import CROSSREF_BASE, CR_HEADERS
from core.http import cached_get
from core.schema import blank_pub, clean_journal, norm_type


def _seen(pubs):
    out = {}
    for x in pubs:
        if x.get("doi"):
            out.setdefault(x["name"], set()).add(x["doi"].lower())
    return out


def _works(orcid):
    items, cursor = [], "*"
    while True:
        data = cached_get(CROSSREF_BASE,
                          params={"filter": f"orcid:{orcid}", "rows": 200,
                                  "cursor": cursor},
                          headers=CR_HEADERS, sleep=0.5)
        msg = data["message"]
        items.extend(msg["items"])
        cursor = msg.get("next-cursor")
        if not cursor or not msg["items"]:
            return items


def retrieve(records, pubs, verbose=True):
    have = _seen(pubs)
    added = 0

    for p in records:
        orcid = p.get("orcid")
        if not orcid:
            continue
        name = p["name_clean"]

        try:
            items = _works(orcid)
        except Exception as e:
            print(f"  {name}: {type(e).__name__} {e}")
            continue

        seen = have.setdefault(name, set())
        n = 0
        for it in items:
            doi = (it.get("DOI") or "").lower()
            if not doi or doi in seen:
                continue
            seen.add(doi)

            year = (it.get("issued", {}).get("date-parts") or [[None]])[0][0]
            authors = it.get("author") or []

            pubs.append(blank_pub(
                name=name,
                source_id=p.get("source_id"),
                title=(it.get("title") or [""])[0],
                year=str(year) if year else None,
                type=norm_type(it.get("type")),
                n_authors=len(authors) or None,
                authors="; ".join(
                    f"{a.get('family','')}, {a.get('given','')}".strip(", ")
                    for a in authors) or None,
                issns=[i for i in (it.get("ISSN") or []) if i],
                journal=clean_journal((it.get("container-title") or [None])[0]
                                      if it.get("container-title") else None),
                publisher=it.get("publisher"),
                doi=doi,
                link=f"https://doi.org/{doi}",
                source="Crossref",
            ))
            n += 1

        added += n
        if verbose and n:
            print(f"  {name}: +{n}")

    if verbose:
        print(f"crossref: added {added} records; {len(pubs)} total")
    return pubs
