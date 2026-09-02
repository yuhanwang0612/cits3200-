"""
ORCID / OpenAlex author identity resolution — ANU slice, CITS3200 Group 20.

One-off script, not part of the pipeline (same category as doi_gap_measurement.py
in this directory). Builds anu_orcid_resolution.csv by finding each of
anu_staff.csv's 44 researchers on OpenAlex and, where more than one same-name
candidate exists at ANU, disambiguating them using title overlap against the
296 rows already scraped into anu_publications.csv.

This adds an identifier layer only. It does not touch anu_publications.csv,
anu_journals.csv or harvest.csv, and does not add, remove or re-count any
publication.

openalex_author_id and orcid are only ever populated for a confirmed match
(title_overlap_unique or orcid_exact) or a single-candidate lead
(name_institution_unique, which still needs a human to check it). An
ambiguous or not_found row leaves both blank — every candidate it looked at,
including whichever scored highest, goes into candidate_alternatives instead,
so nothing unconfirmed sits in an authoritative column.

    python orcid_resolution.py

Writes anu_orcid_resolution.csv (this directory) and a response cache
(orcid_resolution_cache.json, gitignored like openalex_cache.json) so a
second run costs nothing.

INSTITUTION
-----------
ANU's OpenAlex institution ID (I118347636) and ROR (019wvm592) were looked up
live via /institutions?search=Australian National University — a single,
unambiguous result confirmed to be the real ANU (country_code AU, matching
display_name). Not hardcoded from memory. See ANU_ORCID_RESOLUTION.md.

DISAMBIGUATION
---------------
Word-overlap title matching, not a new metric: normalise() and the 0.6 Jaccard
cutoff are exactly title_similarity() from doi_gap_measurement.py in this same
directory, applied here to OpenAlex-authored works instead of CrossRef hits.
"""
import csv
import json
import os
import re
import time

import requests

API_AUTHORS = "https://api.openalex.org/authors"
API_WORKS = "https://api.openalex.org/works"
INSTITUTION_ID = "I118347636"
MAILTO = "taylor.jamie.04@outlook.com.au"
PAUSE = 0.15
MAX_RETRIES = 4
OVERLAP_CUTOFF = 0.6  # same cutoff as doi_gap_measurement.py's title_similarity
CANDIDATE_PAGE_LIMIT = 25  # per-page cap on the author search below; a full
                           # page means there may be more candidates than shown

HERE = os.path.dirname(os.path.abspath(__file__))
STAFF_PATH = os.path.join(HERE, "..", "anu_staff.csv")
PUBLICATIONS_PATH = os.path.join(HERE, "..", "anu_publications.csv")
CACHE_PATH = os.path.join(HERE, "orcid_resolution_cache.json")
OUT_PATH = os.path.join(HERE, "anu_orcid_resolution.csv")

OUT_COLUMNS = [
    "name", "profile_url", "openalex_author_id", "orcid", "match_method",
    "candidate_count", "candidate_count_capped", "titles_tested",
    "titles_matched", "openalex_works_count", "needs_human_check",
    "candidate_alternatives",
]


def normalise(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def title_similarity(a, b):
    a, b = set(normalise(a).split()), set(normalise(b).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def get(session, url, params):
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"    ! network error ({exc}); retrying")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            wait = 2 ** (attempt + 1)
            print(f"    rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if r.status_code != 200:
            print(f"    ! HTTP {r.status_code} for {url} {params}")
            return None
        return r.json()
    print(f"    ! gave up on {url} {params} after {MAX_RETRIES} attempts")
    return None


def find_candidates(session, name):
    data = get(session, API_AUTHORS, {
        "search": name,
        "filter": f"affiliations.institution.id:{INSTITUTION_ID}",
        "per-page": CANDIDATE_PAGE_LIMIT,
        "mailto": MAILTO,
    })
    if not data:
        return []
    out = []
    for a in data.get("results", []):
        out.append({
            "id": (a.get("id") or "").rsplit("/", 1)[-1],
            "orcid": (a.get("orcid") or "").rsplit("/", 1)[-1] if a.get("orcid") else "",
            "display_name": a.get("display_name"),
            "works_count": a.get("works_count"),
            "cited_by_count": a.get("cited_by_count"),
            "last_known_institutions": [
                i.get("display_name") for i in (a.get("last_known_institutions") or [])
            ],
        })
    return out


def fetch_titles(session, author_id):
    """All titles for one OpenAlex author, paginated with a cursor."""
    titles = []
    cursor = "*"
    while cursor:
        data = get(session, API_WORKS, {
            "filter": f"authorships.author.id:{author_id}",
            "select": "id,title",
            "per-page": 200,
            "cursor": cursor,
            "mailto": MAILTO,
        })
        if not data:
            break
        for w in data.get("results", []):
            if w.get("title"):
                titles.append(w["title"])
        cursor = (data.get("meta") or {}).get("next_cursor")
        time.sleep(PAUSE)
    return titles


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"candidates": {}, "titles": {}}


def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def main():
    with open(STAFF_PATH, encoding="utf-8") as _sf:
        staff = list(csv.DictReader(_sf))
    with open(PUBLICATIONS_PATH, encoding="utf-8") as _pf:
        pubs = list(csv.DictReader(_pf))

    scraped_titles = {}
    for row in pubs:
        scraped_titles.setdefault(row["researcher_name"], []).append(row["title"])

    session = requests.Session()
    session.headers.update({"User-Agent": "CITS3200-Group20-ANU/1.0"})
    cache = load_cache()

    results = []
    for i, person in enumerate(staff, 1):
        name = person["name"]
        print(f"[{i}/{len(staff)}] {name}")

        if name in cache["candidates"]:
            candidates = cache["candidates"][name]
        else:
            candidates = find_candidates(session, name)
            cache["candidates"][name] = candidates
            time.sleep(PAUSE)

        titles = scraped_titles.get(name, [])

        if not candidates:
            results.append({
                "name": name, "profile_url": person["profile_url"],
                "openalex_author_id": "", "orcid": "", "match_method": "not_found",
                "candidate_count": 0, "candidate_count_capped": "FALSE",
                "titles_tested": len(titles),
                "titles_matched": 0, "openalex_works_count": "",
                "needs_human_check": "TRUE", "candidate_alternatives": "",
            })
            continue

        # ── ORCID exact match ────────────────────────────────────────
        # If the staff record carries an ORCID, look for a candidate
        # whose ORCID matches.  Strongest possible signal — no title
        # evidence needed.
        staff_orcid = (person.get("orcid") or "").rsplit("/", 1)[-1].strip()
        if staff_orcid:
            for c in candidates:
                if c["orcid"] and c["orcid"].strip() == staff_orcid:
                    results.append({
                        "name": name, "profile_url": person["profile_url"],
                        "openalex_author_id": c["id"], "orcid": c["orcid"],
                        "match_method": "orcid_exact",
                        "candidate_count": len(candidates),
                        "candidate_count_capped": "TRUE" if len(candidates) == CANDIDATE_PAGE_LIMIT else "FALSE",
                        "titles_tested": len(titles), "titles_matched": 0,
                        "openalex_works_count": c["works_count"],
                        "needs_human_check": "FALSE",
                        "candidate_alternatives": "|".join(
                            x["id"] for x in candidates if x["id"] != c["id"]
                        ),
                    })
                    break
            else:
                staff_orcid = ""  # not among OpenAlex candidates; fall through

        if staff_orcid:
            continue  # already appended an orcid_exact result above

        # Score every candidate by scraped-title overlap.
        scored = []
        for c in candidates:
            cache_key = c["id"]
            if not titles:
                scored.append((c, 0))
                continue
            if cache_key in cache["titles"]:
                candidate_titles = cache["titles"][cache_key]
            else:
                candidate_titles = fetch_titles(session, c["id"])
                cache["titles"][cache_key] = candidate_titles
            matched = 0
            for t in titles:
                best = max((title_similarity(t, ct) for ct in candidate_titles), default=0.0)
                if best >= OVERLAP_CUTOFF:
                    matched += 1
            scored.append((c, matched))

        save_cache(cache)

        scored.sort(key=lambda pair: -pair[1])
        top_c, top_matched = scored[0]
        others_with_matches = [n for c, n in scored[1:] if n > 0]

        if len(candidates) == 1 and not titles:
            # A single same-name candidate is a lead, not a confirmation —
            # there is no publication evidence to test it against, so a
            # human still needs to check the profile.
            method = "name_institution_unique"
            chosen, needs_check = top_c, "TRUE"
        elif titles and top_matched >= 2 and not others_with_matches:
            method = "title_overlap_unique"
            chosen, needs_check = top_c, "FALSE"
        elif len(candidates) == 1 and titles and top_matched < 2:
            # Only one candidate exists, but the overlap evidence is too thin
            # to call it confidently resolved on its own — still ambiguous
            # per the rule (overlap evidence does not separate/confirm it).
            method = "ambiguous"
            chosen, needs_check = top_c, "TRUE"
        else:
            method = "ambiguous"
            chosen, needs_check = top_c, "TRUE"

        # An ambiguous/not_found row gets no author ID in the authoritative
        # columns — "highest-scoring of a tied or unconvincing field" is not
        # a confirmed identity, and when every candidate scores zero, "top"
        # is only the first result the API happened to return. Every
        # candidate (not just the runners-up) goes into candidate_alternatives
        # instead, so the lead is still visible to a human.
        if method == "ambiguous":
            author_id, orcid = "", ""
            alt_ids = "|".join(c["id"] for c, _ in scored)
        else:
            author_id, orcid = chosen["id"], chosen["orcid"]
            alt_ids = "|".join(c["id"] for c, _ in scored[1:]) if len(scored) > 1 else ""

        results.append({
            "name": name, "profile_url": person["profile_url"],
            "openalex_author_id": author_id, "orcid": orcid,
            "match_method": method, "candidate_count": len(candidates),
            "candidate_count_capped": "TRUE" if len(candidates) == CANDIDATE_PAGE_LIMIT else "FALSE",
            "titles_tested": len(titles), "titles_matched": top_matched,
            "openalex_works_count": chosen["works_count"],
            "needs_human_check": needs_check,
            "candidate_alternatives": alt_ids,
        })

    save_cache(cache)

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(results)

    from collections import Counter
    counts = Counter(r["match_method"] for r in results)
    print(f"\n  {OUT_PATH}  ({len(results)} researchers)")
    for method, n in counts.most_common():
        print(f"     {method:<24} {n}")

    confirmed = counts.get("orcid_exact", 0) + counts.get("title_overlap_unique", 0)
    leads = counts.get("name_institution_unique", 0)
    staff_rows = [r for r in results
                  if r["match_method"] in ("orcid_exact", "title_overlap_unique",
                                            "name_institution_unique")]
    orcid_on_file = sum(1 for r in staff_rows if r["orcid"])
    author_id_only = len(staff_rows) - orcid_on_file
    print(f"\n  {confirmed} confirmed by publication evidence, "
          f"{leads} single-candidate matches pending human verification "
          f"({len(staff_rows)}/{len(results)} written into anu_staff.csv)")
    print(f"  of those {len(staff_rows)}: {orcid_on_file} carry an ORCID, "
          f"{author_id_only} an OpenAlex author ID only")


if __name__ == "__main__":
    main()
