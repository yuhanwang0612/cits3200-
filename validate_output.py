"""
Validate the exported pipeline output against the sources it came from.

Takes a random sample and re-derives the answer independently, rather than
re-running the same code and getting the same answer. Three layers:

  A. Structural   — no network. Orphans, duplicates, implausible values.
  B. Re-derived   — re-query eSpace for sampled researchers and diff the
                    publication sets. Re-read the ABDC sheet and diff ratings.
  C. Manual       — prints URLs for a sample so a human can eyeball them.

Usage:
    python validate_output.py                # sample of 5 researchers, 15 pubs
    python validate_output.py --people 10 --pubs 30 --seed 7
"""

import argparse
import random
import re
import sys
import time
from collections import Counter

import pandas as pd
import requests

# --------------------------------------------------------------------------

OUT_DIR = "output/uq"
ABDC_FILE = "data/ABDC-JQL-2025-v1-260326.xlsx"
ABDC_SHEET = "2025 JQL"
ABDC_HEADER = 7
ABDC_RATING_COL = "2025 rating"

ESPACE_BASE = "https://api.library.uq.edu.au/v1/records/search"
ESPACE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://espace.library.uq.edu.au",
    "referer": "https://espace.library.uq.edu.au/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
}

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results = []


def check(label, ok, detail="", level=FAIL, rows=None):
    """Record a check result. `rows` are the offending records, printed in
    the summary so a failure names what actually broke."""
    status = PASS if ok else level
    results.append((status, label, detail, rows if not ok else None))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn "}[status]
    print(f"[{mark}] {label}" + (f"  — {detail}" if detail else ""))


def norm(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


# --------------------------------------------------------------------------
# A. Structural checks — no network
# --------------------------------------------------------------------------

def structural(staff, journals, pubs):
    print("\n--- A. structural ---")

    # every publication points at a staff row and a journal row
    names = set(staff["name"])
    orphan_staff = sorted(set(pubs["name"]) - names)
    check("every publication maps to a staff row", not orphan_staff,
          f"{len(orphan_staff)} unmatched",
          rows=[{"name": n} for n in orphan_staff])

    jkeys = set(journals["journal_name"])
    orphan_j = sorted(set(pubs["journal_name"].dropna()) - jkeys)
    check("every publication maps to a journal row", not orphan_j,
          f"{len(orphan_j)} unmatched",
          rows=[{"journal_name": j} for j in orphan_j])

    # journals table has no rows nothing points at
    unused = sorted(jkeys - set(pubs["journal_name"].dropna()))
    check("no unreferenced journal rows", not unused, f"{len(unused)} unused",
          level=WARN, rows=[{"journal_name": j} for j in unused])

    # dedup actually worked
    key = list(zip(pubs["name"], pubs["title"].str.lower().str.strip(), pubs["year"]))
    dupes = [k for k, n in Counter(key).items() if n > 1]
    check("no duplicate (person, title, year)", not dupes,
          f"{len(dupes)} duplicated",
          rows=[{"name": a, "title": str(b)[:55], "year": c} for a, b, c in dupes])

    # a DOI should not appear twice for the same person
    withdoi = pubs.dropna(subset=["doi"])
    pd_key = list(zip(withdoi["name"], withdoi["doi"].str.lower()))
    pd_dupes = [k for k, n in Counter(pd_key).items() if n > 1]
    check("no duplicate DOI within one person", not pd_dupes,
          f"{len(pd_dupes)} duplicated",
          rows=[{"name": a, "doi": b} for a, b in pd_dupes])

    # value ranges
    yrs = pd.to_numeric(pubs["year"], errors="coerce")
    bad_yr = pubs[(yrs < 1950) | (yrs > 2027) | yrs.isna()]
    check("years are plausible", bad_yr.empty,
          f"{len(bad_yr)} outside 1950–2027 or missing",
          rows=bad_yr[["name", "year", "title", "source", "doi"]]
               .to_dict("records"))

    ac = pd.to_numeric(pubs.get("author_count"), errors="coerce")
    bad_ac = pubs[ac < 1]
    check("author counts are >= 1", bad_ac.empty, f"{len(bad_ac)} below 1",
          rows=bad_ac[["name", "author_count", "title", "source"]]
               .to_dict("records"))

    ranks = set(journals["quality_rank"].dropna())
    odd = sorted(ranks - {"A*", "A", "B", "C"})
    check("ABDC ranks are A*/A/B/C", not odd, f"{len(odd)} unexpected",
          rows=journals[journals["quality_rank"].isin(odd)]
               [["journal_name", "quality_rank"]].to_dict("records"))

    lv = set(staff["academic_level"].dropna())
    odd_lv = sorted(lv - set("ABCDE"))
    check("academic levels are A–E", not odd_lv, f"{len(odd_lv)} unexpected",
          rows=staff[staff["academic_level"].isin(odd_lv)]
               [["name", "job_title", "academic_level"]].to_dict("records"))

    unmapped = staff[staff["academic_level"].isna()]
    check("every staff member has a level", unmapped.empty,
          f"{len(unmapped)} unmapped", level=WARN,
          rows=unmapped[["name", "job_title"]].to_dict("records"))

    # coverage, informational
    for col in ("doi", "quality_rank", "impact_factor", "citation_percentile"):
        if col in pubs.columns:
            n = pubs[col].notna().sum()
            print(f"         coverage {col:20} {n:4}/{len(pubs)} "
                  f"({n / len(pubs) * 100:.0f}%)")
        elif col in journals.columns:
            n = journals[col].notna().sum()
            print(f"         coverage {col:20} {n:4}/{len(journals)} journals "
                  f"({n / len(journals) * 100:.0f}%)")


# --------------------------------------------------------------------------
# B1. Re-derive publication sets from eSpace
# --------------------------------------------------------------------------

def refetch_espace(espace_id, timeout=20):
    """Independently pull this author's journal articles from eSpace."""
    url = (f"{ESPACE_BASE}?export_to=&page=1&per_page=100&sort=published_date"
           f"&order_by=desc&mode=advanced&key%5Brek_author_id%5D={espace_id}")
    r = requests.get(url, headers=ESPACE_HEADERS, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    out = {}
    for rec in d.get("data", []):
        if rec.get("rek_genre") != "Journal Article":
            continue
        doi = rec.get("fez_record_search_key_doi")
        out[rec["rek_pid"]] = {
            "title": rec.get("rek_title"),
            "year": rec["rek_date"][:4] if rec.get("rek_date") else None,
            "doi": (doi.get("rek_doi") if isinstance(doi, dict) else None),
        }
    return out, d.get("total")


def rederive_people(staff, pubs, n, rng):
    print(f"\n--- B1. re-derive {n} researchers from eSpace ---")
    pool = staff.dropna(subset=["source_id"])
    pool = pool[pool["name"].isin(set(pubs["name"]))]
    if pool.empty:
        check("sample available", False, "no staff with an espace_id and publications")
        return
    sample = pool.sample(min(n, len(pool)), random_state=rng.randint(0, 10**6))

    for _, s in sample.iterrows():
        name, eid = s["name"], str(s["source_id"]).split(".")[0]
        try:
            fresh, total = refetch_espace(eid)
        except requests.RequestException as e:
            check(f"refetch {name}", False, str(e)[:70], level=WARN)
            continue

        mine = pubs[pubs["name"] == name]
        # eSpace-sourced rows only: other sources legitimately add rows
        if "source" in mine.columns:
            mine = mine[mine["source"].fillna("UQ eSpace") == "UQ eSpace"]

        fresh_titles = {norm(v["title"]) for v in fresh.values()}
        mine_titles = {norm(t) for t in mine["title"]}

        missing = fresh_titles - mine_titles      # in eSpace, absent from output
        extra = mine_titles - fresh_titles        # in output, absent from eSpace

        detail = f"eSpace {len(fresh_titles)} / output {len(mine_titles)}"
        if missing:
            detail += f" · {len(missing)} missing"
        if extra:
            detail += f" · {len(extra)} extra"
        # dedup legitimately removes rows, so missing is only a warning
        check(f"{name} matches eSpace", not extra, detail,
              level=FAIL if extra else WARN)
        time.sleep(1)


# --------------------------------------------------------------------------
# B2. Re-derive ABDC ratings straight from the spreadsheet
# --------------------------------------------------------------------------

def rederive_abdc(journals, n, rng):
    print(f"\n--- B2. re-derive {n} ABDC ratings from the spreadsheet ---")
    try:
        abdc = pd.read_excel(ABDC_FILE, sheet_name=ABDC_SHEET, header=ABDC_HEADER)
    except Exception as e:
        check("read ABDC file", False, str(e)[:70], level=WARN)
        return
    abdc = abdc.loc[:, ~abdc.columns.astype(str).str.startswith("Unnamed")]
    abdc.columns = [str(c).strip() for c in abdc.columns]

    lookup = {}
    for _, row in abdc.iterrows():
        for col in ("ISSN", "ISSNOnline"):
            v = str(row[col]).strip()
            if v and v.lower() != "nan":
                lookup[v] = str(row[ABDC_RATING_COL]).strip()

    rated = journals.dropna(subset=["issn"])
    if rated.empty:
        check("sample available", False, "no journals carry an issn")
        return
    sample = rated.sample(min(n, len(rated)), random_state=rng.randint(0, 10**6))

    mismatches = 0
    for _, j in sample.iterrows():
        issns = [i.strip() for i in str(j["issn"]).split(";") if i.strip()]
        expect = next((lookup[i] for i in issns if i in lookup), None)
        got = j["quality_rank"] if pd.notna(j["quality_rank"]) else None
        if expect != got:
            mismatches += 1
            print(f"         {j['journal_name'][:45]:47} sheet={expect} output={got}")
    check(f"ABDC ratings match the spreadsheet ({len(sample)} sampled)",
          mismatches == 0, f"{mismatches} mismatched")


# --------------------------------------------------------------------------
# C. Manual verification list
# --------------------------------------------------------------------------

def manual_list(pubs, n, rng):
    print(f"\n--- C. {n} publications to eyeball by hand ---")
    sample = pubs.sample(min(n, len(pubs)), random_state=rng.randint(0, 10**6))
    for _, p in sample.iterrows():
        print(f"\n  {p['name']} · {p['year']} · {p.get('quality_rank')}")
        print(f"  {str(p['title'])[:78]}")
        print(f"  journal: {p['journal_name']}")
        if pd.notna(p.get("link")):
            print(f"  {p['link']}")
        elif pd.notna(p.get("article_url")):
            print(f"  {p['article_url']}")
    print("\n  Open each and confirm: right person, right journal, right year.")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, default=5)
    ap.add_argument("--pubs", type=int, default=15)
    ap.add_argument("--journals", type=int, default=15)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--offline", action="store_true",
                    help="skip the checks that need network access")
    ap.add_argument("--dir", default=OUT_DIR)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    if args.seed is not None:
        print(f"seed {args.seed} — this run is reproducible")

    try:
        staff = pd.read_csv(f"{args.dir}/staff.csv", dtype=str)
        journals = pd.read_csv(f"{args.dir}/journals.csv", dtype=str)
        pubs = pd.read_csv(f"{args.dir}/publications.csv", dtype=str)
    except FileNotFoundError as e:
        sys.exit(f"could not read exports: {e}")

    print(f"loaded {len(staff)} staff, {len(journals)} journals, {len(pubs)} publications")

    structural(staff, journals, pubs)
    if not args.offline:
        rederive_people(staff, pubs, args.people, rng)
        rederive_abdc(journals, args.journals, rng)
    manual_list(pubs, args.pubs, rng)

    print("\n--- summary ---")
    tally = Counter(r[0] for r in results)
    for k in (PASS, WARN, FAIL):
        if tally[k]:
            print(f"  {k}: {tally[k]}")

    for level_name, header in ((FAIL, "FAILURES"), (WARN, "WARNINGS")):
        hits = [r for r in results if r[0] == level_name]
        if not hits:
            continue
        print(f"\n--- {header} ---")
        for _, label, detail, rows in hits:
            print(f"\n{label} — {detail}")
            for row in (rows or [])[:20]:
                print("    " + " · ".join(
                    f"{k}={v}" for k, v in row.items() if v is not None))
            if rows and len(rows) > 20:
                print(f"    ... and {len(rows) - 20} more")

    if tally[FAIL]:
        sys.exit(1)
    print("\nno failures")


if __name__ == "__main__":
    main()