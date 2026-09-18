"""Extra publications OpenAlex attributes to each researcher.

Use with care. OpenAlex infers author entities from names, co-authors and
institutions, then attaches an ORCID to the resulting cluster. Where a name
is common, several real people get merged into one entity and the ORCID
inherits all of their work — one finance lecturer's ORCID returned 278
papers spanning nanomedicine, gastric surgery and THz optics.

Two defences are available. When no institution filter is supplied, a volume
guard skips implausibly large author clusters. When a ROR is supplied, the
works themselves must carry that university affiliation, so an incomplete
repository count is not used as a second rejection rule. A later discipline
screen remains responsible for detecting an incorrectly resolved namesake.

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

import re
import unicodedata

from core.config import OA_HEADERS, OPENALEX_BASE
from core.http import cached_get
from core.schema import blank_pub, clean_journal, norm_type
from enrichment.openalex import AGGREGATOR_ISSNS, hyphenate

# Skip when OpenAlex claims more than this multiple of the repository count.
# Both thresholds are judgement calls — watch the SKIP lines and adjust.
RATIO_LIMIT = 3.0
ABSOLUTE_FLOOR = 20


def _title_year_key(title, year):
    """Conservative fallback identity for genuine works without a DOI."""
    text = unicodedata.normalize("NFKD", str(title or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return (text, str(year or "")) if text else None


def _publication_source(work):
    """Return the best journal source across all OpenAlex locations.

    Older works and DOI-less records often have an empty/repository primary
    location while another location carries the actual journal. Prefer an
    explicitly journal-typed source, then any named non-repository source,
    and use the primary source only as the final fallback.
    """
    primary = (work.get("primary_location") or {}).get("source") or {}
    locations = [work.get("primary_location") or {}, work.get("best_oa_location") or {}]
    locations.extend(work.get("locations") or [])
    sources = []
    seen = set()
    for location in locations:
        source = (location or {}).get("source") or {}
        marker = source.get("id") or (source.get("display_name"), tuple(source.get("issn") or []))
        if source and marker not in seen:
            seen.add(marker)
            sources.append(source)

    journal = next(
        (source for source in sources
         if source.get("type") == "journal" and source.get("display_name")),
        None,
    )
    if journal:
        return journal
    non_repository = next(
        (source for source in sources
         if source.get("display_name")
         and source.get("type") not in {"repository", "ebook platform"}),
        None,
    )
    return non_repository or primary


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
    have, have_title_year, counts = {}, {}, {}
    for x in pubs:
        counts[x["name"]] = counts.get(x["name"], 0) + 1
        if x.get("doi"):
            have.setdefault(x["name"], set()).add(x["doi"].lower())
        key = _title_year_key(x.get("title"), x.get("year"))
        if key:
            have_title_year.setdefault(x["name"], set()).add(key)

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
            # An adapter's manually approved override is backed by direct
            # evidence (normally an official profile plus a publication DOI).
            # Once that author identity has been established, restricting the
            # query to the current university would wrongly discard papers
            # published at an earlier employer. Automatically name-resolved
            # identities retain the institution guard.
            work_ror = None if (
                p.get("identity_source") == "manual_verified_override"
                or p.get("retrieve_all_career_works")
            ) else ror
            works = _works(clause, work_ror)
        except Exception as e:
            print(f"  {name}: {type(e).__name__} {e}")
            continue

        repo_n = counts.get(name, 0)
        # Repository coverage is not a ground truth. Minerva, for example,
        # can list one departmental deposit for a verified researcher while
        # OpenAlex has 20+ works carrying UniMelb's ROR. Applying the ratio
        # guard there discarded established professors precisely because the
        # repository was incomplete. Without a ROR the guard is still useful;
        # with a ROR, the institution constraint plus the later ABDC
        # discipline screen are the appropriate safeguards.
        if (work_ror is None and not p.get("retrieve_all_career_works")
                and works and repo_n
                and len(works) > max(RATIO_LIMIT * repo_n, ABSOLUTE_FLOOR)):
            print(f"  SKIP {name}: OpenAlex has {len(works)} vs {repo_n} in the "
                  f"repository — probably a merged author entity")
            skipped += 1
            continue

        seen = have.setdefault(name, set())
        seen_title_year = have_title_year.setdefault(name, set())
        n = 0
        for w in works:
            doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            title_year = _title_year_key(w.get("display_name"), w.get("publication_year"))
            if doi and doi in seen:
                continue
            if not doi and (not title_year or title_year in seen_title_year):
                continue
            if doi:
                seen.add(doi)
            if title_year:
                seen_title_year.add(title_year)

            src = _publication_source(w)
            issns = src.get("issn") or []
            if isinstance(issns, str):
                issns = [issns]
            # Hyphenated, because ABDC's list is hyphenated and
            # enrichment/scimago.py strips hyphens on lookup, so that form matches
            # both. Repository ISSNs are rejected for the same reason they are
            # in enrichment/openalex.py: an ISSN that is not the journal's is worse
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
                doi=doi or None,
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
