"""Extra publications OpenAlex attributes to each researcher's ORCID.

Use with care. OpenAlex infers author entities from names, co-authors and
institutions, then attaches an ORCID to the resulting cluster. Where a name
is common, several real people get merged into one entity and the ORCID
inherits all of their work — one finance lecturer's ORCID returned 278
papers spanning nanomedicine, gastric surgery and THz optics.

Two defences. A volume guard skips anyone whose OpenAlex count is
implausible against the repository count. An optional institution (ROR)
filter restricts results to work carrying that university's affiliation,
which removes the contamination but also drops legitimate earlier work.
"""

from core.config import OA_HEADERS, OPENALEX_BASE
from core.http import cached_get
from core.schema import blank_pub, clean_journal, norm_type

# Skip when OpenAlex claims more than this multiple of the repository count.
# Both thresholds are judgement calls — watch the SKIP lines and adjust.
RATIO_LIMIT = 3.0
ABSOLUTE_FLOOR = 20


def _works(orcid, ror=None):
    flt = f"author.orcid:{orcid}"
    if ror:
        flt += f",authorships.institutions.ror:{ror}"
    out, page = [], 1
    while True:
        data = cached_get(OPENALEX_BASE,
                          params={"filter": flt, "per-page": 100, "page": page},
                          headers=OA_HEADERS, timeout=60, sleep=0.5)
        out.extend(data["results"])
        if len(out) >= data["meta"]["count"] or not data["results"]:
            return out
        page += 1


def retrieve(records, pubs, ror=None, verbose=True):
    have, counts = {}, {}
    for x in pubs:
        counts[x["name"]] = counts.get(x["name"], 0) + 1
        if x.get("doi"):
            have.setdefault(x["name"], set()).add(x["doi"].lower())

    added = skipped = 0
    for p in records:
        orcid = p.get("orcid")
        if not orcid:
            continue
        name = p["name_clean"]

        try:
            works = _works(orcid, ror)
        except Exception as e:
            print(f"  {name}: {type(e).__name__} {e}")
            continue

        repo_n = counts.get(name, 0)
        if works and repo_n and len(works) > max(RATIO_LIMIT * repo_n, ABSOLUTE_FLOOR):
            print(f"  SKIP {name}: OpenAlex has {len(works)} vs {repo_n} in the "
                  f"repository — probably a merged author entity")
            skipped += 1
            continue

        seen = have.setdefault(name, set())
        n = 0
        for w in works:
            doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            if not doi or doi in seen:
                continue
            seen.add(doi)

            src = (w.get("primary_location") or {}).get("source") or {}
            issns = src.get("issn") or []
            if isinstance(issns, str):
                issns = [issns]
            auths = w.get("authorships") or []

            pubs.append(blank_pub(
                name=name,
                source_id=p.get("source_id"),
                title=w.get("display_name"),
                year=str(w["publication_year"]) if w.get("publication_year") else None,
                type=norm_type(w.get("type")),
                n_authors=len(auths) or None,
                authors="; ".join((a.get("author") or {}).get("display_name", "")
                                  for a in auths) or None,
                issns=issns,
                journal=clean_journal(src.get("display_name")),
                publisher=src.get("host_organization_name"),
                doi=doi,
                link=w.get("id"),
                source="OpenAlex",
            ))
            n += 1

        added += n
        if verbose and n:
            print(f"  {name}: +{n} ({len(works)} in OpenAlex, {repo_n} in repository)")

    if verbose:
        print(f"openalex: added {added} records, skipped {skipped} researchers "
              f"on the volume guard; {len(pubs)} total")
    return pubs
