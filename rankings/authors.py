"""
OpenAlex author discovery — CITS3200 Group 20.

Finds each researcher in OpenAlex, records their ORCID, and reports the
publications OpenAlex knows about that the university's own website never
listed.

    python authors.py --staff ../output/unsw_staff.csv \
                      --publications ../output/unsw_publications_with_openalex.csv \
                      --ror 03r8z3t63 \
                      --mailto you@student.uwa.edu.au

WHY THIS IS NOT `openalex.py`
-----------------------------
`openalex.py` asks "here is a DOI I already have, tell me about it". That can
only ever improve rows we already scraped. If the university's page omits a
paper, no amount of enrichment will find it, because we never had a DOI to look
up.

This module asks the opposite question: "here is a researcher, what have they
published?" That is what the client asked for on 19 August (the university's own
site is not to be the sole source, and is not always current) and what Yuanji
asked for again by email.

PRECISION BEFORE RECALL
-----------------------
Attributing a stranger's paper to one of our researchers is far worse than
missing a paper. A missing paper understates someone; a wrong one is invisible,
survives review, and corrupts every ranking built on it. So nothing is included
unless we can say *how* we decided it was the right person, and that reason is
written into `author_match_type` on every row.

WHAT ORCID DOES AND DOES NOT SOLVE
----------------------------------
Checked against the live API on 25 August, using Jason Zein at UNSW:

    authors?filter=orcid:0000-0001-7701-3721            -> exactly 1 author
    works?filter=authorships.author.orcid:0000-...-3721 -> 47 works, all his

So an ORCID is unambiguous. But searching by name is not:

    authors?search=Jason Zein  -> 3 results
        Jason Zein     ORCID, 45 works, UNSW Sydney          <- the researcher
        Jason El-Zein  no ORCID, 1 work, Illinois EPA        <- a different person
        Jason Zein     no ORCID, 1 work, UNSW Sydney         <- the SAME person again

OpenAlex splits one person across several author records and usually only one
of them carries the ORCID. So ORCID gives **precision, not recall**: filter by
it and you have the right human, but you silently miss whatever sits on the
duplicate records.

This module therefore does both. It finds candidates by name at the right
institution, prefers the one with an ORCID, and keeps the others as duplicates
of the same person rather than throwing them away or treating them as someone
else.

WE HAVE NO ORCIDS YET
---------------------
No university site in this project publishes one; UNSW's certainly does not. So
ORCID cannot be the *input* to the first run. It is an output: found here, then
stored on the researcher and used as the stable key from then on, which is what
the client meant by "ORCID is permanent for a certain researcher".

Pass `--orcid-column` on a later run to use the ones a previous run found.
"""

import argparse
import csv
import json
import os
import time
import unicodedata
from collections import Counter, OrderedDict

import requests

import harvest
import journal_match as jm
import openalex as openalex_mod

AUTHORS_API = "https://api.openalex.org/authors"
WORKS_API = "https://api.openalex.org/works"
PAUSE = 0.2
MAX_RETRIES = 4
PER_PAGE = 200

AUTHOR_SELECT = ("id,display_name,display_name_alternatives,orcid,works_count,"
                 "last_known_institutions,affiliations")
WORK_SELECT = ("id,doi,title,publication_year,type,primary_location,authorships,"
               "cited_by_count,fwci,citation_normalized_percentile,biblio")

# How a researcher was tied to an OpenAlex author record. Written onto every
# discovered row so a weak match can be filtered out of the client's numbers
# without re-running anything.
MATCH_ORCID = "orcid"                 # we already knew their ORCID
MATCH_NAME = "name+institution"       # one candidate, name matched exactly
MATCH_VARIANT = "name-variant+institution"   # matched on surname + first initial
MATCH_AMBIGUOUS = "ambiguous"         # several plausible people; nothing included
MATCH_NONE = "not found"
# The lookup itself failed. Deliberately not the same as "not found": one says
# OpenAlex has nobody, the other says we never got to ask. Recording them as the
# same thing bakes a network problem into the data as a fact about a person.
MATCH_FAILED = "lookup failed"


class BudgetExhausted(RuntimeError):
    """OpenAlex's daily spending allowance is gone until midnight UTC.

    Not a rate limit to back off from — retrying cannot succeed, and carrying on
    would mark every remaining researcher "not found" for a reason that has
    nothing to do with them.
    """ 

# Included in the discovered file. Deliberately the same names the scrapers use,
# so a discovered row can be appended to a publications CSV unchanged.
DISCOVERED_COLUMNS = [
    "researcher_name", "researcher_profile_url", "university",
    "field_of_research", "title", "journal_name", "year", "publication_type",
    "doi", "article_url", "coauthors", "author_count", "volume", "pages",
    "publisher", "citation_percentile", "source", "citation_top_10_percent",
    "cited_by_count", "fwci", "issn", "openalex_id",
    "author_match_type",       # how we decided this is the right person
    "openalex_author_id",
]

STAFF_ADDED = ["orcid", "openalex_author_id", "openalex_author_ids",
               "openalex_works_count", "author_match_type", "author_candidates"]

SOURCE_NAME = "OpenAlex"


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
def fold(name):
    """Normalise a personal name for comparison.

    Accents, case, punctuation and doubled spaces are formatting, not identity:
    "Luis Filipe Goncalves-Pinto" and "Luís Filipe Gonçalves-Pinto" are one
    person. Word order is not touched, because "Li Yang" and "Yang Li" may well
    be two.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("-", " ").replace(".", " ").replace("'", "")
    return " ".join(text.split())


def initial_form(name):
    """"nicole ang" -> "n ang". Used only for the weaker, tagged match."""
    parts = fold(name).split()
    if len(parts) < 2:
        return ""
    return parts[0][0] + " " + parts[-1]


def names_of(author):
    """Every name OpenAlex lists for an author, including its alternatives."""
    names = [author.get("display_name")]
    names += list(author.get("display_name_alternatives") or [])
    return [n for n in names if n]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def request_json(session, url, params, mailto):
    """One GET with the same politeness rules as openalex.py.

    Returns None rather than raising: one researcher failing to resolve should
    not end a run over ninety of them.
    """
    params = dict(params)
    if mailto:
        params["mailto"] = mailto

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"    ! network error ({exc}); retrying")
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429:
            # Two very different things share this status code. A throttle is
            # worth waiting out; the daily budget being spent is not, and
            # retrying it just burns the clock before mismarking everyone left.
            body = {}
            try:
                body = response.json()
            except ValueError:
                pass
            if "budget" in str(body.get("message", "")).lower():
                raise BudgetExhausted(body.get("message") or "daily budget spent")
            wait = 2 ** (attempt + 1)
            print(f"    rate limited, waiting {wait}s"
                  + ("" if mailto else " (try --mailto to avoid this)"))
            time.sleep(wait)
            continue
        if response.status_code != 200:
            print(f"    ! HTTP {response.status_code} from {url}")
            return None
        return response.json()

    print(f"    ! gave up on {url} after {MAX_RETRIES} attempts")
    return None


def search_authors(session, name, mailto):
    """Candidate author records for a name. Institution is filtered locally.

    The institution filter is applied here rather than in the query because
    combining `search=` with `filter=last_known_institutions.ror:` returned
    nothing at all when tried against the live API, while the plain search
    returned the researcher correctly. Filtering the results we get back is
    slower by nothing and cannot silently return an empty set.
    """
    payload = request_json(session, AUTHORS_API,
                           {"search": name, "per-page": 25,
                            "select": AUTHOR_SELECT}, mailto)
    # None means the request failed; [] means OpenAlex genuinely has nobody.
    # Collapsing the two is how a bad afternoon becomes a permanent wrong answer
    # in the cache.
    return None if payload is None else (payload.get("results") or [])


def author_by_orcid(session, orcid, mailto):
    payload = request_json(session, AUTHORS_API,
                           {"filter": f"orcid:{orcid}", "per-page": 5,
                            "select": AUTHOR_SELECT}, mailto)
    if payload is None:
        return None
    results = payload.get("results") or []
    return results[0] if results else []


def works_of(session, author_ids, mailto, limit=None):
    """Every work by these author records, paged with a cursor."""
    works, cursor = [], "*"
    ids = "|".join(author_ids)
    while cursor:
        payload = request_json(session, WORKS_API,
                               {"filter": f"author.id:{ids}",
                                "per-page": PER_PAGE, "cursor": cursor,
                                "select": WORK_SELECT}, mailto)
        if not payload:
            break
        works.extend(payload.get("results") or [])
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if limit and len(works) >= limit:
            break
        time.sleep(PAUSE)
    return works


# ---------------------------------------------------------------------------
# Choosing the right author record
# ---------------------------------------------------------------------------
def institution_rors(author):
    """Every institution OpenAlex ties this author to, not just the newest.

    `last_known_institutions` holds only the most recent one, and for an
    academic that is whatever institution appeared on their latest paper —
    often a co-author's, sometimes a former employer. Filtering on it alone
    reported 38 of UNSW's 93 researchers as "not found", including people with
    a hundred publications on their own staff page, which is not a fact about
    them. `affiliations` lists the institutions across their career, so it
    catches those.
    """
    rors = set()
    for institution in author.get("last_known_institutions") or []:
        if institution.get("ror"):
            rors.add(institution["ror"].rsplit("/", 1)[-1].lower())
    for entry in author.get("affiliations") or []:
        institution = entry.get("institution") or {}
        if institution.get("ror"):
            rors.add(institution["ror"].rsplit("/", 1)[-1].lower())
    return rors


def at_institution(authors, ror):
    """Candidates OpenAlex ties to our university at any point."""
    if not ror:
        return list(authors)
    wanted = ror.rsplit("/", 1)[-1].lower()
    return [a for a in authors if wanted in institution_rors(a)]


def choose(name, authors, ror):
    """Pick the author record for `name`, and say how confident we are.

    Returns (primary, duplicates, match_type, candidates_note).

    `duplicates` are other records at the same institution believed to be the
    same person — OpenAlex routinely splits someone across several author ids
    and puts the ORCID on only one of them. Their works are included, because
    they are that person's works; leaving them out is how you quietly lose a
    third of somebody's output.
    """
    here = at_institution(authors, ror)
    if not here:
        return None, [], MATCH_NONE, ""

    target, target_initial = fold(name), initial_form(name)
    exact = [a for a in here
             if any(fold(n) == target for n in names_of(a))]
    variant = [a for a in here
               if a not in exact
               and any(initial_form(n) == target_initial and target_initial
                       for n in names_of(a))]

    matched, how = (exact, MATCH_NAME) if exact else (variant, MATCH_VARIANT)
    if not matched:
        return None, [], MATCH_NONE, ""

    note = "; ".join(f"{a.get('display_name')} ({a.get('works_count')} works"
                     f"{', orcid' if a.get('orcid') else ''})" for a in matched)

    if len(matched) == 1:
        return matched[0], [], how, note

    # Several records for what is probably one person. The one carrying the
    # ORCID is the anchor; the rest are duplicates of it. If none or several
    # carry an ORCID we cannot tell duplicates from namesakes, so nothing is
    # included and a human is told.
    with_orcid = [a for a in matched if a.get("orcid")]
    if len(with_orcid) == 1:
        primary = with_orcid[0]
        return primary, [a for a in matched if a is not primary], how, note
    return None, [], MATCH_AMBIGUOUS, note


# ---------------------------------------------------------------------------
# Works
# ---------------------------------------------------------------------------
def is_journal_article(work):
    """OpenAlex's own type, plus a source that is actually a journal.

    The client restricted the dataset to journal articles on 19 August. A
    preprint on SSRN has type "article" too, so the source type is checked as
    well.
    """
    if (work.get("type") or "").lower() != "article":
        return False
    source = ((work.get("primary_location") or {}).get("source")) or {}
    return (source.get("type") or "").lower() == "journal"


def to_row(work, person, match_type, author_id):
    """One OpenAlex work as a publications row, in the scrapers' column names."""
    values = openalex_mod.extract(work)
    source = ((work.get("primary_location") or {}).get("source")) or {}
    authorships = work.get("authorships") or []
    biblio = work.get("biblio") or {}
    pages = "-".join(p for p in (biblio.get("first_page"), biblio.get("last_page")) if p)

    return {
        "researcher_name": person.get("name"),
        "researcher_profile_url": person.get("profile_url"),
        "university": person.get("university"),
        "field_of_research": person.get("field_of_research"),
        "title": work.get("title"),
        "journal_name": source.get("display_name"),
        "year": work.get("publication_year"),
        "publication_type": "Journal Articles",
        "doi": openalex_mod.normalise_doi(work.get("doi")),
        "article_url": work.get("doi"),
        "coauthors": "; ".join(
            (a.get("author") or {}).get("display_name") or "" for a in authorships).strip("; "),
        # Counted from the list itself, never by splitting the joined string —
        # a name containing a semicolon would otherwise inflate it.
        "author_count": len(authorships) or None,
        "volume": biblio.get("volume"),
        "pages": pages or None,
        "publisher": source.get("host_organization_name"),
        "citation_percentile": values["citation_percentile"],
        "citation_top_10_percent": values["citation_top_10_percent"],
        "cited_by_count": values["cited_by_count"],
        "fwci": values["fwci"],
        "issn": values["issn"],
        "openalex_id": values["openalex_id"],
        "source": SOURCE_NAME,
        "author_match_type": match_type,
        "openalex_author_id": author_id,
    }


def known_keys(rows, fieldnames):
    """What the scraper already has, as (dois, (title, year) pairs).

    Two keys because 893 of UNSW's 2,000 journal articles have no DOI. Matching
    those on title and year is the only option, and it is the same rule the
    team's merge script uses.
    """
    doi_column = next((c for c in fieldnames if jm.normalise(c) == "doi"), None)
    title_column = next((c for c in fieldnames if jm.normalise(c) == "title"), None)
    year_column = next((c for c in fieldnames if jm.normalise(c) == "year"), None)

    dois, titles = set(), set()
    for row in rows:
        doi = openalex_mod.normalise_doi(row.get(doi_column)) if doi_column else None
        if doi:
            dois.add(doi)
        if title_column:
            titles.add((jm.normalise(row.get(title_column) or ""),
                        str(row.get(year_column) or "").strip()))
    return dois, titles


def is_new(row, dois, titles):
    if row["doi"] and row["doi"] in dois:
        return False
    key = (jm.normalise(row["title"] or ""), str(row["year"] or "").strip())
    return key not in titles


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def load_cache(path, use_cache):
    if use_cache and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            print("  ! author cache unreadable; starting fresh")
    return {}


def save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f)


# ---------------------------------------------------------------------------
def discover(staff_path, publications_path=None, ror=None, mailto=None,
             limit=None, use_cache=True, orcid_column=None,
             include_ambiguous=False):
    with open(staff_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        staff, staff_fields = list(reader), list(reader.fieldnames or [])
    if not staff:
        raise SystemExit(f"No rows in {staff_path}.")

    name_column = next((c for c in staff_fields if jm.normalise(c) == "name"), None)
    if name_column is None:
        raise SystemExit(f"No name column in {staff_path}. Columns: {staff_fields}")

    known_dois, known_titles = set(), set()
    if publications_path:
        rows, fieldnames = jm.read_publications(publications_path)
        known_dois, known_titles = known_keys(rows, fieldnames)
        print(f"Publications on file: {len(rows)} rows, {len(known_dois)} DOIs")

    base = os.path.dirname(os.path.abspath(staff_path))
    cache_path = os.path.join(base, "openalex_authors_cache.json")
    cache = load_cache(cache_path, use_cache)

    session = requests.Session()
    targets = staff[:limit] if limit else staff
    discovered, stats = [], Counter()
    budget_spent = False

    for i, person in enumerate(targets, 1):
        name = (person.get(name_column) or "").strip()
        if not name:
            continue

        known_orcid = (person.get(orcid_column) or "").strip() if orcid_column else ""
        key = f"orcid:{known_orcid}" if known_orcid else f"name:{fold(name)}|{ror or ''}"

        if key in cache:
            candidates = cache[key]
        else:
            try:
                if known_orcid:
                    found = author_by_orcid(session, known_orcid, mailto)
                    candidates = None if found is None else ([found] if found else [])
                else:
                    candidates = search_authors(session, name, mailto)
            except BudgetExhausted as exc:
                print(f"\n  ! OpenAlex daily budget spent: {exc}")
                print("    Stopping here rather than marking everyone left as "
                      "'not found'.")
                print("    It resets at midnight UTC. Everything already fetched "
                      "is cached, so\n    re-running tomorrow picks up where this "
                      "stopped.")
                budget_spent = True
                break
            # A failed request is never cached. Caching it would turn one bad
            # afternoon into a permanent wrong answer about a real person.
            if candidates is not None:
                cache[key] = candidates
            time.sleep(PAUSE)

        if candidates is None:
            person["author_match_type"] = MATCH_FAILED
            person["author_candidates"] = ""
            stats[MATCH_FAILED] += 1
            print(f"  {i:>3}/{len(targets)}  {name:<28} {MATCH_FAILED}")
            continue

        if known_orcid and candidates:
            primary, duplicates, how, note = candidates[0], [], MATCH_ORCID, ""
        else:
            primary, duplicates, how, note = choose(name, candidates, ror)

        person["author_match_type"] = how
        person["author_candidates"] = note
        stats[how] += 1

        if primary is None:
            print(f"  {i:>3}/{len(targets)}  {name:<28} {how}"
                  + (f"  [{note}]" if note else ""))
            if how == MATCH_AMBIGUOUS and not include_ambiguous:
                continue
            continue

        author_ids = [primary["id"].rsplit("/", 1)[-1]]
        author_ids += [a["id"].rsplit("/", 1)[-1] for a in duplicates]
        person["orcid"] = (primary.get("orcid") or "").rsplit("/", 1)[-1] or ""
        person["openalex_author_id"] = author_ids[0]
        person["openalex_author_ids"] = "; ".join(author_ids)
        person["openalex_works_count"] = primary.get("works_count")
        if person["orcid"]:
            stats["orcid found"] += 1

        works_key = "works:" + "|".join(author_ids)
        if works_key in cache:
            works = cache[works_key]
        else:
            works = works_of(session, author_ids, mailto)
            cache[works_key] = works

        articles = [w for w in works if is_journal_article(w)]
        new = []
        for work in articles:
            row = to_row(work, {
                "name": name,
                "profile_url": person.get("profile_url"),
                "university": person.get("university"),
                "field_of_research": person.get("field_of_research"),
            }, how, author_ids[0])
            if is_new(row, known_dois, known_titles):
                new.append(row)
        discovered.extend(new)
        stats["works seen"] += len(articles)
        stats["new"] += len(new)

        print(f"  {i:>3}/{len(targets)}  {name:<28} {how:<26} "
              f"{len(articles):>4} articles, {len(new):>3} new"
              + (f", {len(duplicates)} duplicate record(s)" if duplicates else ""))

    save_cache(cache_path, cache)

    staff_out = os.path.join(base, os.path.splitext(os.path.basename(staff_path))[0]
                             + "_with_orcid.csv")
    # _raw, because screen_discovered.py writes the checked version under the
    # plain name. Before this, re-running discovery silently replaced a
    # screened 260-row file with the unscreened 731-row one.
    discovered_out = os.path.join(base, "discovered_publications_raw.csv")

    # An aborted run writes nothing at all.
    #
    # It used to write whatever it had, which meant a run that stopped on the
    # very first request replaced a good 204-publication file with an empty one.
    # Nothing is lost by staying quiet: every response already fetched is in the
    # cache above, so the next run rebuilds those researchers instantly and
    # writes complete files. Partial output that overwrites complete output is
    # strictly worse than no output.
    if budget_spent:
        print(f"\n  Nothing written. The previous {os.path.basename(staff_out)} "
              f"and\n  {os.path.basename(discovered_out)} are left as they were, "
              f"because a partial\n  result overwriting a complete one is worse "
              f"than no result.")
        print("\n  Re-run after midnight UTC. What was fetched is cached, so it "
              "resumes\n  rather than starting again.")
        return staff_out, discovered_out
    columns = staff_fields + [c for c in STAFF_ADDED if c not in staff_fields]
    with open(staff_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(staff)

    with open(discovered_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DISCOVERED_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(discovered)

    university = harvest.university_in(staff)
    if university:
        harvest.record(university, "OpenAlex authors",
                       harvest.latest_year_in(discovered), base)

    print(f"\n  {staff_out}")
    print(f"  {discovered_out}   ({len(discovered)} publications the website did not list)")
    print(f"\n  of {len(targets)} researchers:")
    for how in (MATCH_ORCID, MATCH_NAME, MATCH_VARIANT, MATCH_AMBIGUOUS,
                MATCH_NONE, MATCH_FAILED):
        if stats[how]:
            print(f"     {how:<28} {stats[how]}")
    print(f"     {'ORCID recorded':<28} {stats['orcid found']}")
    if budget_spent:
        print("\n  This run stopped early on OpenAlex's daily budget, so the "
              "counts above\n  are not the whole roster. Re-run after midnight "
              "UTC to finish.")
    if stats[MATCH_AMBIGUOUS]:
        print(f"\n  {stats[MATCH_AMBIGUOUS]} researchers were left out as ambiguous. "
              f"Read author_candidates in\n  {staff_out} and resolve them by hand "
              f"rather than loosening the matching.")
    return staff_out, discovered_out


def main():
    parser = argparse.ArgumentParser(
        description="Find researchers in OpenAlex and report publications their "
                    "university website never listed.")
    parser.add_argument("--staff", required=True, help="a *_staff.csv from a scraper")
    parser.add_argument("--publications",
                        help="the publications CSV to compare against; without "
                             "it every work is reported as new")
    parser.add_argument("--ror", help="the university's ROR id, e.g. 03r8z3t63 "
                                      "for UNSW. Strongly recommended: without "
                                      "it, any namesake anywhere is a candidate.")
    parser.add_argument("--mailto", help="your email, for OpenAlex's faster "
                                         "polite pool. Never written to output.")
    parser.add_argument("--orcid-column",
                        help="a column on the staff file holding ORCIDs found by "
                             "an earlier run; skips name matching entirely")
    parser.add_argument("--limit", type=int, help="only do this many (for testing)")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--include-ambiguous", action="store_true",
                        help="include researchers we could not tell apart from "
                             "a namesake. Off by default, and it should stay off "
                             "for anything the client sees.")
    args = parser.parse_args()

    if not args.ror:
        print("! No --ror given. Every namesake at every institution in the world\n"
              "  is a candidate, which is how someone else's citations end up\n"
              "  against one of our researchers. Continuing, but check the output.\n")

    discover(args.staff, args.publications, args.ror, args.mailto, args.limit,
             not args.no_cache, args.orcid_column, args.include_ambiguous)


if __name__ == "__main__":
    main()
