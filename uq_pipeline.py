"""
UQ researcher publication pipeline.

Order of operations:
  1. University staff directory      -> records
  2. eSpace author IDs               -> records[espace_id]
  3. ORCIDs (via eSpace)             -> records[orcid]
  4. eSpace publication records      -> pubs
  5. OpenAlex retrieval (by ORCID)   -> extra pubs the repository lacks
  6. OpenAlex enrichment (by DOI)    -> citation_percentile, cited_by_count, fwci
  7. ABDC quality rank (by ISSN)     -> abdc, abdc_title
  8. Clarivate JCR (by ISSN)         -> impact_factor, impact_factor_5yr
  9. Scimago metrics (by ISSN)       -> sjr, sjr_quartile, h_index, cites_per_doc_2y
 10. Export staff / journals / publications / harvest as CSV + JSON

Requires a .env file containing:
  CLARIVATE_API_KEY=...

Run:  python uq_pipeline.py
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

OUT_DIR = "."                       # where the four CSV/JSON pairs are written
ABDC_FILE = "ABDC-JQL-2025-v1-260326.xlsx"
ABDC_SHEET = "2025 JQL"
ABDC_HEADER = 7                     # header row differs between JQL editions
ABDC_RATING_COL = "2025 rating"
SCIMAGO_FILE = "scimagojr 2025.csv"

JCR_YEAR = 2025
JCR_SLEEP = 0.3                     # Clarivate allows 5 req/sec; 2 calls per journal

ESPACE_BASE = "https://api.library.uq.edu.au/v1/records/search"
JCR_BASE = "https://api.clarivate.com/apis/wos-journals/v1"
OPENALEX_BASE = "https://api.openalex.org/works"

CONTACT_EMAIL = "24314165@student.uwa.edu.au"

TARGETS = [
    ("https://business.uq.edu.au/team/finance-discipline",
     "University of Queensland", "Finance"),
    ("https://business.uq.edu.au/team/accounting-discipline",
     "University of Queensland", "Accounting"),
]

BROWSER_UA = {"User-Agent": "Mozilla/5.0"}

# eSpace sits behind a WAF - the origin/referer headers are what get you a 200.
ESPACE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en-AU;q=0.9,en;q=0.8",
    "origin": "https://espace.library.uq.edu.au",
    "referer": "https://espace.library.uq.edu.au/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
}

OA_HEADERS = {"User-Agent": f"UQ-CITS3200 (mailto:{CONTACT_EMAIL})"}

# --------------------------------------------------------------------------
# Title / rank parsing
# --------------------------------------------------------------------------

PREFIX = re.compile(
    r"^(Associate Professor|Emeritus Professor|Professor|Dr|Mr|Mrs|Ms|Miss"
    r"|A/Prof|Prof|Assoc\.? Prof\.?)\.?\s+",
    re.IGNORECASE,
)

# Order matters: compound titles must be tested before their components.
LADDER = [
    ("Emeritus Professor",     r"emeritus prof"),
    ("Associate Professor",    r"associate prof|a/prof"),
    ("Associate Lecturer",     r"associate lecturer"),
    ("Senior Lecturer",        r"senior lecturer"),
    ("Senior Research Fellow", r"senior research fellow"),
    ("Research Fellow",        r"research fellow"),
    ("Teaching Associate",     r"teaching associate"),
    ("Professor",              r"\bprofessor\b|chair in"),
    ("Lecturer",               r"\blecturer\b"),
]

LEVEL = {
    "Associate Lecturer":     "A",
    "Lecturer":               "B",
    "Fellow":                 "B",
    "Research Fellow":        "B",
    "Senior Lecturer":        "C",
    "Senior Fellow":          "C",
    "Senior Research Fellow": "C",
    "Associate Professor":    "D",
    "Professor":              "E",
    "Professorial Fellow":    "E",
    "Professor Emeritus":     "E",
    "Emeritus Professor":     "E",
}


def rank(title, prefix):
    """Normalise a job title onto the academic ladder.

    Falls back to the name prefix when the title carries no rank word
    (e.g. Stephen Gray, whose title is an endowed chair name).
    """
    for label, pat in LADDER:
        if title and re.search(pat, title, re.I):
            return label
    if prefix and prefix.lower() not in {"dr", "mr", "mrs", "ms", "miss"}:
        return prefix
    return None


def _num(v):
    """Coerce to float, tolerating European decimals and None."""
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 1. Staff directory
# --------------------------------------------------------------------------

def scrape_staff():
    records = []
    for url, uni, disc in TARGETS:
        resp = requests.get(url, headers=BROWSER_UA, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".person--teaser")
        print(f"{disc}: {len(cards)} cards")

        for card in cards:
            link = card.select_one(".person__display-name a")
            if not link:
                continue
            name = link.get_text(strip=True)
            m = PREFIX.match(name)

            titles = [t.get_text(strip=True) for t in card.select(".position__title")]
            titles = [t for t in titles if t]
            # Affiliate roles are secondary appointments, not the substantive one.
            substantive = [t for t in titles if not t.startswith("Affiliate")]
            title = (substantive or titles or [None])[0]

            records.append({
                "university": uni,
                "discipline": disc,
                "name": name,
                "name_clean": PREFIX.sub("", name).strip(),
                "prefix": m.group(1) if m else None,
                "title": title,
                "title_clean": rank(title, m.group(1) if m else None),
                "profile_url": urljoin(url, link["href"]),
            })
        print(len(records))
    return records


# --------------------------------------------------------------------------
# 2. eSpace author IDs
# --------------------------------------------------------------------------

def add_espace_ids(records):
    """UQ publishes the eSpace author id on each profile page."""
    for p in records:
        if p.get("espace_id"):
            continue
        try:
            resp = requests.get(p["profile_url"], headers=BROWSER_UA, timeout=15)
        except requests.RequestException as e:
            print(f"  failed {p['name_clean']}: {e}")
            p["espace_id"] = None
            continue
        link = BeautifulSoup(resp.text, "html.parser").select_one('a[href*="author_id"]')
        p["espace_id"] = link["href"].rstrip("/").split("/")[-1] if link else None
        print(p["name_clean"], resp.status_code, p["espace_id"])
        time.sleep(1)

    print(sum(1 for p in records if p["espace_id"]), "of", len(records))
    return records


# --------------------------------------------------------------------------
# 3. ORCIDs
# --------------------------------------------------------------------------

def add_orcids(records):
    """Pull each person's ORCID out of their own author block in eSpace."""
    orcids = {}
    for p in records:
        if not p.get("espace_id"):
            continue
        url = (f"{ESPACE_BASE}?export_to=&page=1&per_page=5&sort=published_date"
               f"&order_by=desc&mode=advanced&key%5Brek_author_id%5D={p['espace_id']}")
        try:
            d = requests.get(url, headers=ESPACE_HEADERS, timeout=20).json()
        except requests.RequestException:
            continue

        found = None
        for rec in d.get("data", []):
            for a in (rec.get("fez_record_search_key_author_id") or []):
                au = a.get("author") or {}
                if (str(a.get("rek_author_id")) == str(p["espace_id"])
                        and au.get("aut_orcid_id")):
                    found = au["aut_orcid_id"]
                    break
            if found:
                break
        orcids[p["name_clean"]] = found
        time.sleep(0.5)

    for p in records:
        p["orcid"] = orcids.get(p["name_clean"])

    print(sum(1 for p in records if p.get("orcid")), "of", len(records), "have an ORCID")
    return records


# --------------------------------------------------------------------------
# 4. Publications from eSpace
# --------------------------------------------------------------------------

def fetch_publications(records):
    pubs = []
    for i, p in enumerate(records, 1):
        if not p.get("espace_id"):
            print(f"{i} {p['name_clean']}: no eSpace ID, skipped")
            continue

        page, fetched, total = 1, 0, None
        while True:
            url = (f"{ESPACE_BASE}?export_to=&page={page}&per_page=100"
                   f"&sort=published_date&order_by=desc&mode=advanced"
                   f"&key%5Brek_author_id%5D={p['espace_id']}")
            d = requests.get(url, headers=ESPACE_HEADERS, timeout=15).json()
            total = d["total"]

            for rec in d["data"]:
                jn = rec.get("fez_record_search_key_journal_name")

                mj = rec.get("fez_matched_journals") or []
                mj = (mj[0] if isinstance(mj, list) and mj
                      else (mj if isinstance(mj, dict) else None))
                fj = (mj or {}).get("fez_journal") or {}

                issns = [s.get("rek_issn")
                         for s in (rec.get("fez_record_search_key_issn") or [])
                         if s.get("rek_issn")]

                auths = rec.get("fez_record_search_key_author") or []
                aids = rec.get("fez_record_search_key_author_id") or []
                doi = rec.get("fez_record_search_key_doi")

                pubs.append({
                    "name": p["name_clean"],
                    "espace_id": p["espace_id"],
                    "title": rec.get("rek_title"),
                    "year": rec["rek_date"][:4] if rec.get("rek_date") else None,
                    "type": rec.get("rek_genre"),
                    "n_authors": len(auths),
                    "authors": "; ".join(
                        a.get("rek_author", "") for a in
                        sorted(auths, key=lambda a: a.get("rek_author_order", 0))),
                    "author_ids": [a.get("rek_author_id") for a in aids
                                   if a.get("rek_author_id")],
                    "issns": issns,
                    "journal": jn["rek_journal_name"] if jn else None,
                    "journal_id": fj.get("jnl_jid"),
                    "journal_canonical": fj.get("jnl_title"),
                    "publisher": fj.get("jnl_publisher"),
                    "doi": doi.get("rek_doi") if isinstance(doi, dict) else None,
                    "link": f"https://espace.library.uq.edu.au/view/{rec['rek_pid']}",
                    "source": "UQ eSpace",
                })

            fetched += len(d["data"])
            if fetched >= total or not d["data"]:
                break
            page += 1
            time.sleep(1)

        flag = "" if fetched == total else "  <-- MISMATCH"
        print(f"{i} {p['name_clean']}: {total} total, {fetched} fetched{flag}")
        time.sleep(1)

    print(len(pubs), "records from", len({x['name'] for x in pubs}), "people")
    return pubs


# --------------------------------------------------------------------------
# 5. Supplementary retrieval: OpenAlex by ORCID
# --------------------------------------------------------------------------

# OpenAlex type -> eSpace genre, so both sources share one vocabulary.
OA_TYPE_MAP = {
    "article": "Journal Article",
    "review": "Journal Article",
    "preprint": "Preprint",
    "book-chapter": "Book Chapter",
    "book": "Book",
    "dissertation": "Thesis",
    "report": "Research Report",
}

# Guard against contaminated OpenAlex author entities. Same-name researchers
# get merged into one entity, so an ORCID can return hundreds of other
# people's papers (observed: one ORCID returning nanomedicine and surgery
# papers for a finance lecturer). If OpenAlex claims far more work than the
# repository has, the match is not trustworthy - skip and report.
OA_RATIO_LIMIT = 3.0
OA_ABSOLUTE_FLOOR = 20


def fetch_openalex_by_orcid(records, pubs, tries=3):
    """Add publications OpenAlex has that the repository does not."""
    have = {}
    for x in pubs:
        if x.get("doi"):
            have.setdefault(x["name"], set()).add(x["doi"].lower())
    counts = {}
    for x in pubs:
        counts[x["name"]] = counts.get(x["name"], 0) + 1

    added = skipped = 0
    for p in records:
        orcid = p.get("orcid")
        if not orcid:
            continue
        name = p["name_clean"]

        works, page = [], 1
        while True:
            results = None
            for attempt in range(tries):
                try:
                    r = requests.get(
                        OPENALEX_BASE,
                        params={"filter": f"author.orcid:{orcid}",
                                "per-page": 100, "page": page},
                        headers=OA_HEADERS, timeout=60)
                    r.raise_for_status()
                    results = r.json()
                    break
                except requests.RequestException as e:
                    if attempt == tries - 1:
                        print(f"  failed {name}: {e}")
                    else:
                        time.sleep(5 * (attempt + 1))
            if results is None:
                break
            works.extend(results["results"])
            if len(works) >= results["meta"]["count"] or not results["results"]:
                break
            page += 1
            time.sleep(0.5)

        repo_n = counts.get(name, 0)
        if works and repo_n and len(works) > max(OA_RATIO_LIMIT * repo_n,
                                                 OA_ABSOLUTE_FLOOR):
            print(f"  SKIP {name}: OpenAlex has {len(works)} vs {repo_n} in "
                  f"repository — likely a merged author entity")
            skipped += 1
            time.sleep(0.5)
            continue

        seen = have.get(name, set())
        new_for_person = 0
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

            pubs.append({
                "name": name,
                "espace_id": p.get("espace_id"),
                "title": w.get("display_name"),
                "year": str(w["publication_year"]) if w.get("publication_year") else None,
                "type": OA_TYPE_MAP.get(w.get("type"), w.get("type")),
                "n_authors": len(auths),
                "authors": "; ".join((a.get("author") or {}).get("display_name", "")
                                     for a in auths),
                "author_ids": [],
                "issns": issns,
                "journal": src.get("display_name"),
                "journal_id": None,
                "journal_canonical": None,
                "publisher": src.get("host_organization_name"),
                "doi": doi,
                "link": w.get("id"),
                "source": "OpenAlex",
            })
            new_for_person += 1

        have[name] = seen
        added += new_for_person
        if new_for_person:
            print(f"  {name}: +{new_for_person} from OpenAlex "
                  f"({len(works)} total, {repo_n} in repository)")
        time.sleep(0.5)

    print(f"added {added} publications from OpenAlex; "
          f"skipped {skipped} researchers on the volume guard")
    print(len(pubs), "records total")
    return pubs


# --------------------------------------------------------------------------
# 6. OpenAlex enrichment (by DOI)
# --------------------------------------------------------------------------

def enrich_openalex(pubs, chunk_size=25, tries=3):
    """Citation percentile / counts, looked up by DOI in batches.

    Matched by DOI rather than ORCID: OpenAlex author entities merge
    same-name researchers, so ORCID-based retrieval returns other people's
    work for some names.
    """
    dois = list({x["doi"] for x in pubs if x.get("doi")})
    oa = {}

    for i in range(0, len(dois), chunk_size):
        chunk = dois[i:i + chunk_size]
        results = []
        for attempt in range(tries):
            try:
                r = requests.get(
                    OPENALEX_BASE,
                    params={"filter": "doi:" + "|".join(chunk), "per-page": chunk_size},
                    headers=OA_HEADERS, timeout=60)
                r.raise_for_status()
                results = r.json()["results"]
                break
            except requests.RequestException as e:
                if attempt == tries - 1:
                    print(f"  giving up on chunk of {len(chunk)}: {e}")
                else:
                    time.sleep(5 * (attempt + 1))

        for w in results:
            key = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            cnp = w.get("citation_normalized_percentile") or {}
            oa[key] = {
                "citation_percentile": cnp.get("value"),
                "cited_by_count": w.get("cited_by_count"),
                "fwci": w.get("fwci"),
            }
        print(f"{min(i + chunk_size, len(dois))}/{len(dois)} — {len(oa)} matched")
        time.sleep(1)

    for x in pubs:
        hit = oa.get((x.get("doi") or "").lower())
        x["citation_percentile"] = hit["citation_percentile"] if hit else None
        x["cited_by_count"] = hit["cited_by_count"] if hit else None
        x["fwci"] = hit["fwci"] if hit else None

    arts = [x for x in pubs if x["type"] == "Journal Article"]
    print(sum(1 for x in arts if x["citation_percentile"] is not None),
          "of", len(arts), "articles enriched")
    return pubs


# --------------------------------------------------------------------------
# 7. ABDC (by ISSN, local file)
# --------------------------------------------------------------------------

def enrich_abdc(pubs):
    abdc = pd.read_excel(ABDC_FILE, sheet_name=ABDC_SHEET, header=ABDC_HEADER)
    abdc = abdc.loc[:, ~abdc.columns.astype(str).str.startswith("Unnamed")]
    abdc.columns = [str(c).strip() for c in abdc.columns]

    lookup = {}
    for _, row in abdc.iterrows():
        rating = str(row[ABDC_RATING_COL]).strip()   # values carry trailing spaces
        title = str(row["Journal Title"]).strip()
        for col in ("ISSN", "ISSNOnline"):
            v = str(row[col]).strip()                # ISSNs carry trailing tabs
            if v and v.lower() != "nan":
                lookup[v] = {"rating": rating, "title": title}

    if len(lookup) < 2000:
        print(f"WARNING: ABDC lookup only has {len(lookup)} entries — "
              "check sheet name, header row and rating column")

    for x in pubs:
        hit = next((lookup[i] for i in x.get("issns", []) if i in lookup), None)
        x["abdc"] = hit["rating"] if hit else None
        # Canonical ABDC title is the journal key: it collapses print/online
        # ISSN variants and "and" vs "&" spellings onto one row.
        x["abdc_title"] = hit["title"] if hit else None

    return pubs


# --------------------------------------------------------------------------
# 8. Clarivate JCR (by ISSN)
# --------------------------------------------------------------------------

def enrich_jcr(pubs, hdrs):
    def _get(url, **kw):
        r = requests.get(url, headers=hdrs, timeout=20, **kw)
        if r.status_code == 429:
            time.sleep(5)
            r = requests.get(url, headers=hdrs, timeout=20, **kw)
        return r

    def jcr_lookup(issn):
        r = _get(f"{JCR_BASE}/journals", params={"q": issn, "limit": 5})
        if r.status_code != 200:
            return None
        hits = r.json().get("hits") or []
        if not hits:
            return None
        jid = hits[0]["id"]

        time.sleep(JCR_SLEEP)
        r = _get(f"{JCR_BASE}/journals/{jid}/reports/year/{JCR_YEAR}")
        if r.status_code != 200:
            return None
        im = (r.json().get("metrics") or {}).get("impactMetrics") or {}
        return {
            "impact_factor": _num(im.get("jif")),
            "impact_factor_5yr": _num(im.get("jif5Years")),
        }

    issn_set = sorted({i for x in pubs for i in x.get("issns", [])})
    cache = {}
    for n, issn in enumerate(issn_set, 1):
        try:
            cache[issn] = jcr_lookup(issn)
        except requests.RequestException as e:
            cache[issn] = None
            print(f"  failed {issn}: {e}")
        jif = cache[issn]["impact_factor"] if cache[issn] else None
        print(f"{n}/{len(issn_set)} {issn} -> {jif if jif is not None else '—'}")
        time.sleep(JCR_SLEEP)

    for x in pubs:
        hit = next((cache[i] for i in x.get("issns", []) if cache.get(i)), None)
        x["impact_factor"] = hit["impact_factor"] if hit else None
        x["impact_factor_5yr"] = hit["impact_factor_5yr"] if hit else None

    arts = [x for x in pubs if x["type"] == "Journal Article"]
    print(sum(1 for x in arts if x["impact_factor"]), "of", len(arts),
          "articles have a JIF")
    return pubs


# --------------------------------------------------------------------------
# 9. Scimago (by ISSN, local file)
# --------------------------------------------------------------------------

def enrich_scimago(pubs):
    sj = pd.read_csv(SCIMAGO_FILE, sep=";")

    sj_lookup = {}
    for _, row in sj.iterrows():
        issns = [i.strip() for i in str(row["Issn"]).split(",")
                 if i.strip() and i.strip().lower() != "nan"]
        entry = {
            "sjr": _num(row["SJR"]),
            "sjr_quartile": str(row["SJR Best Quartile"]).strip(),
            "h_index": row["H index"],
            "cites_per_doc_2y": _num(row["Citations / Doc. (2years)"]),
            "sj_title": str(row["Title"]).strip(),
        }
        for i in issns:
            sj_lookup[i] = entry
    print(len(sj_lookup), "ISSN entries")

    # Scimago stores ISSNs without hyphens.
    for x in pubs:
        hit = next((sj_lookup[i.replace("-", "")] for i in x.get("issns", [])
                    if i.replace("-", "") in sj_lookup), None)
        x["sjr"] = hit["sjr"] if hit else None
        x["sjr_quartile"] = hit["sjr_quartile"] if hit else None
        x["h_index"] = hit["h_index"] if hit else None
        x["cites_per_doc_2y"] = hit["cites_per_doc_2y"] if hit else None

    return pubs


# --------------------------------------------------------------------------
# 10. Export
# --------------------------------------------------------------------------

def export(records, pubs, out_dir=OUT_DIR):
    staff = [{
        "name": p["name_clean"],
        "job_title": p["title_clean"],
        "academic_level": LEVEL.get(p["title_clean"]),
        "university": p["university"],
        "field_of_research": p["discipline"],
        "espace_id": p.get("espace_id"),
        "profile_url": p["profile_url"],
    } for p in records]

    journals = {}
    for x in pubs:
        if not x["journal"]:
            continue
        key = x.get("abdc_title") or x["journal"] or "unknown"
        if key not in journals:
            journals[key] = {
                "journal_name": key,
                "journal": x["journal"],
                "journal_canonical": x.get("journal_canonical"),
                "publisher": x.get("publisher"),
                "issn": "; ".join(x["issns"]) if x["issns"] else None,
                "quality_rank": x.get("abdc"),
                "impact_factor": x.get("impact_factor"),
                "impact_factor_5yr": x.get("impact_factor_5yr"),
                "sjr": x.get("sjr"),
                "sjr_quartile": x.get("sjr_quartile"),
                "h_index": x.get("h_index"),
                "cites_per_doc_2y": x.get("cites_per_doc_2y"),
            }
    journals = list(journals.values())

    # Journal articles only; deduplicated on (person, title, year).
    # DOI-bearing records sort first so the better-catalogued copy wins.
    seen = set()
    publications = []
    for x in sorted(pubs, key=lambda r: (r.get("doi") is None)):
        if x["type"] != "Journal Article":
            continue
        k = (x["name"], x["title"].lower().strip(), x["year"])
        if k in seen:
            continue
        seen.add(k)
        publications.append({
            "espace_id": x["espace_id"],
            "name": x["name"],
            "journal_name": x.get("abdc_title") or x["journal"] or "unknown",
            "title": x["title"],
            "year": x["year"],
            "author_count": x["n_authors"],
            "authors": x["authors"],
            "quality_rank": x.get("abdc"),
            "sjr_quartile": x.get("sjr_quartile"),
            "doi": x.get("doi"),
            "article_url": f"https://doi.org/{x['doi']}" if x.get("doi") else None,
            "link": x["link"],
            "source": x.get("source", "UQ eSpace"),
            "citation_percentile": x.get("citation_percentile"),
            "cited_by_count": x.get("cited_by_count"),
            "fwci": x.get("fwci"),
        })

    harvest = [{
        "university": "University of Queensland",
        "source": "UQ eSpace",
        "last_run": datetime.now(timezone.utc).isoformat(),
        "latest_year": max((int(p["year"]) for p in publications if p["year"]),
                           default=None),
    }]

    os.makedirs(out_dir, exist_ok=True)
    for name, data in [("staff", staff), ("journals", journals),
                       ("publications", publications), ("harvest", harvest)]:
        with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        pd.DataFrame(data).to_csv(os.path.join(out_dir, f"{name}.csv"), index=False)
        print(name, len(data))

    return staff, journals, publications, harvest


# --------------------------------------------------------------------------

def main():
    load_dotenv()
    jcr_hdrs = {"X-ApiKey": os.environ["CLARIVATE_API_KEY"]}

    print("\n=== 1. staff directory ===")
    records = scrape_staff()

    print("\n=== 2. eSpace author ids ===")
    add_espace_ids(records)

    print("\n=== 3. orcids ===")
    add_orcids(records)

    print("\n=== 4. publications ===")
    pubs = fetch_publications(records)

    print("\n=== 5. openalex retrieval (by orcid) ===")
    fetch_openalex_by_orcid(records, pubs)

    print("\n=== 6. openalex enrichment (by doi) ===")
    enrich_openalex(pubs)

    print("\n=== 7. abdc ===")
    enrich_abdc(pubs)

    print("\n=== 8. clarivate jcr ===")
    enrich_jcr(pubs, jcr_hdrs)

    print("\n=== 9. scimago ===")
    enrich_scimago(pubs)

    print("\n=== 10. export ===")
    export(records, pubs)


if __name__ == "__main__":
    main()
