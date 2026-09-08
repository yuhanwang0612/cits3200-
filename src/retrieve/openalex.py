"""Extra publications OpenAlex attributes to each researcher.

Use with care. OpenAlex infers author entities from names, co-authors and
institutions, then attaches an ORCID to the resulting cluster. Where a name
is common, several real people get merged into one entity and the ORCID
inherits all of their work — one finance lecturer's ORCID returned 278
papers spanning nanomedicine, gastric surgery and THz optics.

Two defences. A volume guard skips anyone whose OpenAlex count is
implausible against the repository count. An optional institution (ROR)
filter restricts results to work carrying that university's affiliation,
which removes the contamination but also drops legitimate earlier work.

ORCID FIRST, AUTHOR ID SECOND, NEVER A SEARCH
---------------------------------------------
An ORCID is the better key and is used whenever the adapter supplied one. But
a staff directory usually does not publish ORCIDs: at UNSW about forty of
ninety-three researchers have none, and skipping them loses their work
entirely.

So an adapter may also record `openalex_author_ids`, and those are used as the
fallback. This is deliberately a filter (`author.id:`) and not a name search.
OpenAlex charges $0.001 for a search against $0.0001 for a filter, on a free
budget of $0.10 a day without a key, so ninety-three name searches here would
be most of a day's allowance every run. The adapter does that search once,
caches it, and hands the ids over.

An adapter that records neither is skipped, exactly as before.
"""

from core.config import OA_HEADERS, OPENALEX_BASE
from core.http import cached_get
from core.schema import blank_pub, clean_journal, norm_type
from enrich.openalex import AGGREGATOR_ISSNS, hyphenate

# Skip when OpenAlex claims more than this multiple of the repository count.
# Both thresholds are judgement calls — watch the SKIP lines and adjust.
RATIO_LIMIT = 3.0
ABSOLUTE_FLOOR = 20


def author_filter(person):
    """The cheapest reliable way to ask for this person's works, or None.

    Returns (filter clause, how). Several author ids are one call, not
    several: OpenAlex takes them or-ed in a single filter.
    """
    orcid = person.get("orcid")
    if orcid:
        return f"author.orcid:{orcid}", "orcid"
    ids = [i for i in (person.get("openalex_author_ids") or []) if i]
    if ids:
        return "author.id:" + "|".join(ids), "author id"
    return None, None


def _works(clause, ror=None):
    flt = clause
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

    added = skipped = unreachable = 0
    by_key = {}
    for p in records:
        clause, how = author_filter(p)
        if not clause:
            unreachable += 1
            continue
        by_key[how] = by_key.get(how, 0) + 1
        name = p["name_clean"]

        try:
            works = _works(clause, ror)
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
            # Hyphenated, because ABDC's list is hyphenated and
            # enrich/scimago.py strips hyphens on lookup, so that form matches
            # both. Repository ISSNs are rejected for the same reason they are
            # in enrich/openalex.py: an ISSN that is not the journal's is worse
            # than none, because every ranking step joins on it.
            issns = [i for i in (hyphenate(v) for v in issns) if i]
            if any(i in AGGREGATOR_ISSNS
                   and AGGREGATOR_ISSNS[i] not in (src.get("display_name") or "").lower()
                   for i in issns):
                issns = []
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
        reached = ", ".join(f"{n} by {how}" for how, n in sorted(by_key.items()))
        print(f"openalex: added {added} records, skipped {skipped} researchers "
              f"on the volume guard; {len(pubs)} total")
        print(f"          reached {reached or 'nobody'}"
              + (f"; {unreachable} had neither an ORCID nor an author id"
                 if unreachable else ""))
    return pubs
