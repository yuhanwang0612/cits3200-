"""Filter, deduplicate and write the four tables.

Identical for every university. Filtering happens once, here, at the end —
retrieval upstream is deliberately unfiltered so that exclusions are
visible and reversible rather than baked into each source.
"""

import json
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from core.config import OUTPUT_DIR
from core.titles import level

TABLES = ("staff", "journals", "publications", "harvest")


def build_staff(records):
    return [{
        "name": p["name_clean"],
        "job_title": p.get("title_clean"),
        "academic_level": p.get("level_code") or level(p.get("title_clean")),
        "university": p["university"],
        "field_of_research": p["discipline"],
        "source_id": p.get("source_id"),
        "orcid": p.get("orcid"),
        "profile_url": p["profile_url"],
    } for p in records]


def build_journals(pubs):
    """One row per journal, keyed on the ABDC canonical title where we have
    one. Keying on ISSN splits print from online; keying on the raw name
    splits 'and' from '&'. The canonical title collapses both."""
    out = {}
    for x in pubs:
        if not x.get("journal"):
            continue
        key = x.get("abdc_title") or x["journal"]
        if key in out:
            continue
        out[key] = {
            "journal_name": key,
            "journal_raw": x["journal"],
            "publisher": x.get("publisher"),
            "issn": "; ".join(x.get("issns") or []) or None,
            "quality_rank": x.get("abdc"),
            "abdc_edition": x.get("abdc_edition"),
            "impact_factor": x.get("impact_factor"),
            "impact_factor_5yr": x.get("impact_factor_5yr"),
            "jcr_year": x.get("jcr_year"),
            "sjr": x.get("sjr"),
            "sjr_quartile": x.get("sjr_quartile"),
            "h_index": x.get("h_index"),
            "cites_per_doc_2y": x.get("cites_per_doc_2y"),
            "scimago_year": x.get("scimago_year"),
        }
    return list(out.values())


def build_publications(pubs, records=None, keep_type="Journal Article",
                       verbose=True):
    """Records sort DOI-first so the better-catalogued copy survives dedup.

    ORCID is carried onto each row: names collide across eight universities
    (two staff already share the surname Tan), so a name is not a safe join
    key in a merged table.
    """
    orcid_by_name = {r["name_clean"]: r.get("orcid") for r in (records or [])}
    seen, out = set(), []
    for x in sorted(pubs, key=lambda r: (r.get("doi") is None)):
        if x.get("type") != keep_type or not x.get("title"):
            continue
        k = (x["name"], x["title"].lower().strip(), x.get("year"))
        if k in seen:
            continue
        seen.add(k)
        out.append({
            "name": x["name"],
            "orcid": orcid_by_name.get(x["name"]),
            "source_id": x.get("source_id"),
            "journal_name": x.get("abdc_title") or x.get("journal") or "unknown",
            "title": x["title"],
            "year": x.get("year"),
            "author_count": x.get("n_authors"),
            "authors": x.get("authors"),
            "doi": x.get("doi"),
            "article_url": (f"https://doi.org/{x['doi']}" if x.get("doi")
                            else x.get("link")),
            "link": x.get("link"),
            "quality_rank": x.get("abdc"),
            "sjr_quartile": x.get("sjr_quartile"),
            "citation_percentile": x.get("citation_percentile"),
            "cited_by_count": x.get("cited_by_count"),
            "fwci": x.get("fwci"),
            "oa_status": x.get("oa_status"),
            "oa_url": x.get("oa_url"),
            "publication_status": "published",
            "source": x.get("source"),
        })

    if verbose:
        dropped = Counter((x.get("source"), x.get("type"))
                          for x in pubs if x.get("type") != keep_type)
        if dropped:
            print("  excluded by type:")
            for (s, t), n in dropped.most_common(10):
                print(f"    {n:4}  {s or '?':10} {t}")
    return out


def build_harvest(records, pubs, publications, sources=None):
    """One row per university per source (data dictionary 3.5.4)."""
    now = datetime.now(timezone.utc).isoformat()
    unis = {r["university"] for r in records}
    srcs = sources or {x.get("source") for x in pubs if x.get("source")}

    rows = []
    for uni in sorted(unis):
        for src in sorted(s for s in srcs if s):
            years = [int(p["year"]) for p in publications
                     if p.get("year") and p.get("source") == src]
            rows.append({
                "university": uni,
                "source": src,
                "last_run": now,
                "latest_year": max(years) if years else None,
                "record_count": sum(1 for x in pubs if x.get("source") == src),
            })
    return rows


def write(tables, out_dir=None, verbose=True):
    out_dir = out_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in tables.items():
        with open(out_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        pd.DataFrame(data).to_csv(out_dir / f"{name}.csv", index=False)
        if verbose:
            print(f"  {name:14} {len(data):5}  ->  {out_dir / (name + '.csv')}")


def export(records, pubs, out_dir=None, drop_staff_without_pubs=False,
           verbose=True):
    publications = build_publications(pubs, records, verbose=verbose)
    staff = build_staff(records)

    if drop_staff_without_pubs:
        have = {p["name"] for p in publications}
        before = len(staff)
        staff = [s for s in staff if s["name"] in have]
        if verbose and before != len(staff):
            print(f"  dropped {before - len(staff)} staff with no publications")

    tables = {
        "staff": staff,
        "journals": build_journals(pubs),
        "publications": publications,
        "harvest": build_harvest(records, pubs, publications),
    }
    write(tables, out_dir, verbose)
    return tables
