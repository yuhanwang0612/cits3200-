"""
One-off measurement script, not part of the pipeline. Answers: of the 222
publications with no DOI, how many can CrossRef resolve automatically by
title+year, and how long does that take per lookup? Results are written
only to doi_gap_measurement_sample.csv (this directory) — nothing is
written back into anu_publications.csv or anu_doi_manual_lookup.csv.

Informit was in scope for this measurement too, but search.informit.org's
own robots.txt disallows /search outright, so that half isn't attempted —
consistent with this project's standing decision not to defeat
bot-blocking anywhere.
"""
import csv
import random
import re
import time

import requests

CROSSREF_UA = "cits3200-anu-scraper/1.0 (educational research project; 23724721@student.uwa.edu.au)"


def normalise(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def title_similarity(a, b):
    a, b = set(normalise(a).split()), set(normalise(b).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def crossref_lookup(title, year):
    try:
        r = requests.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": 3},
            headers={"User-Agent": CROSSREF_UA},
            timeout=15,
        )
    except requests.RequestException as e:
        return None, str(e)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    items = r.json().get("message", {}).get("items", [])
    best = None
    for item in items:
        cand_title = (item.get("title") or [""])[0]
        sim = title_similarity(title, cand_title)
        cand_year = None
        for key in ("published-print", "published-online", "issued"):
            parts = (item.get(key) or {}).get("date-parts") or [[None]]
            if parts and parts[0] and parts[0][0]:
                cand_year = parts[0][0]
                break
        try:
            year_ok = (year is None or cand_year is None or abs(int(cand_year) - int(year)) <= 1)
        except (ValueError, TypeError):
            year_ok = True
        if sim >= 0.6 and year_ok and (best is None or sim > best[1]):
            best = (item.get("DOI"), sim, cand_title, cand_year)
    return best, None


def main():
    random.seed(23724721)
    with open("anu_doi_manual_lookup.csv", encoding="utf-8") as _fh:
        rows = list(csv.DictReader(_fh))
    sample = random.sample(rows, 25)

    results = []
    for i, row in enumerate(sample, 1):
        title, year = row["title"], row.get("year")
        start = time.time()
        hit, error = crossref_lookup(title, year)
        elapsed = time.time() - start
        resolved = hit is not None
        print(f"[{i}/25] {elapsed:.2f}s  {'RESOLVED' if resolved else 'no match'}  {title[:60]}")
        results.append({
            "researcher_name": row["researcher_name"],
            "title": title,
            "year": year,
            "resolved_by_crossref": resolved,
            "matched_doi": hit[0] if hit else "",
            "match_title_similarity": round(hit[1], 2) if hit else "",
            "crossref_matched_title": hit[2] if hit else "",
            "seconds": round(elapsed, 2),
            "error": error or "",
        })
        time.sleep(0.5)

    with open("doi_gap_measurement_sample.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    n_resolved = sum(1 for r in results if r["resolved_by_crossref"])
    times = sorted(r["seconds"] for r in results)
    median = times[len(times) // 2]
    print()
    print(f"Resolved by CrossRef (title+year): {n_resolved}/25 ({100*n_resolved/25:.0f}%)")
    print(f"Median seconds per lookup: {median}")
    print(f"Total sample time: {sum(times):.1f}s")


if __name__ == "__main__":
    main()
