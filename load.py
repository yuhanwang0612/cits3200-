"""Load staff/journals/publications/harvest CSVs into research.db."""
import csv
import math
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Researcher, Journal, Publication, Harvest

DB   = "sqlite:///research.db"
DATA = Path(".")          # folder holding the CSVs


def nn(v):
    """NaN/empty -> None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def read_csv(path):
    """Read a CSV file as list of dicts, stripping BOM and empty values."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k: nn(v) for k, v in row.items()} for row in csv.DictReader(f)]


def as_int(v):
    try:
        return int(float(v)) if nn(v) is not None else None
    except (ValueError, TypeError):
        return None


def as_float(v):
    try:
        return float(v) if nn(v) is not None else None
    except (ValueError, TypeError):
        return None


def collect_staff(data_dir: Path):
    """
    Read all *_staff.csv files plus the root staff.csv, normalise to the
    agreed schema: {name, job_title, academic_level, field_of_research,
                    profile_url, university, orcid}.
    Returns (list_of_dicts, set_of_(university, name) keys).
    """
    seen   = set()
    staff  = []

    # *_staff.csv first (monash_, adelaide_, anu_, …), then bare staff.csv (UQ)
    candidates = sorted(data_dir.glob("*_staff.csv"))
    if (data_dir / "staff.csv").exists():
        candidates.append(data_dir / "staff.csv")

    for path in candidates:
        for row in read_csv(path):
            name = (row.get("name") or "").strip()
            uni  = (row.get("university") or "").strip()
            if not name or not uni:
                continue
            key = (uni, name)
            if key in seen:
                continue
            seen.add(key)
            staff.append({
                "name":              name,
                "job_title":         row.get("job_title"),
                "academic_level":    row.get("academic_level"),
                "field_of_research": row.get("field_of_research"),
                "profile_url":       row.get("profile_url") or row.get("research_portal_url"),
                "university":        uni,
                "orcid":             row.get("orcid"),
            })

    return staff, seen


def collect_journals(data_dir: Path):
    """
    Use anu_journals.csv as the authoritative journal source — it carries
    ABDC quality_rank plus Scimago metrics (sjr, sjr_quartile, h_index,
    cites_per_doc_2y) that the root journals.csv lacks.
    Maps abdc_list_year -> abdc_edition; drops unwanted columns.

    If jcr.csv exists (written by fetch_jcr.py), impact_factor and
    jcr_year are joined in by ISSN, overriding any value from anu_journals.csv.
    """
    path = data_dir / "anu_journals.csv"
    if not path.exists():
        print(f"⚠️  {path} not found — no journal data loaded")
        return []

    # Load JCR data keyed by ISSN if available
    jcr_by_issn = {}
    jcr_path = data_dir / "jcr.csv"
    if jcr_path.exists():
        for row in read_csv(jcr_path):
            issn = (row.get("issn") or "").strip()
            if issn:
                jcr_by_issn[issn] = {
                    "impact_factor": row.get("impact_factor"),
                    "jcr_year":      row.get("jcr_year"),
                }
        print(f"JCR data: {len(jcr_by_issn)} ISSNs loaded from {jcr_path.name}")

    journals = []
    for row in read_csv(path):
        jname = (row.get("journal_name") or "").strip()
        if not jname:
            continue
        issn = (row.get("issn") or "").strip()
        jcr  = jcr_by_issn.get(issn, {})
        journals.append({
            "journal_name":     jname,
            "issn":             issn or None,
            "quality_rank":     row.get("quality_rank"),
            "abdc_edition":     row.get("abdc_list_year"),
            "impact_factor":    jcr.get("impact_factor") or row.get("impact_factor"),
            "jcr_year":         jcr.get("jcr_year")      or row.get("jcr_year"),
            "sjr":              row.get("sjr"),
            "sjr_quartile":     row.get("sjr_quartile"),
            "h_index":          row.get("h_index"),
            "cites_per_doc_2y": row.get("cites_per_doc_2y"),
            "scimago_year":     row.get("scimago_year"),
        })
    return journals


def main():
    engine = create_engine(DB)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ses = sessionmaker(bind=engine)()

    data_dir = DATA.resolve()

    # ── Publications (read early so we can synthesise missing researchers) ─
    pub_path = data_dir / "combined_publications.csv"
    if not pub_path.exists():
        print(f"⚠️  {pub_path} not found — run merge_publications.py first")
        pub_rows = []
    else:
        pub_rows = read_csv(pub_path)

    # ── Researchers ──────────────────────────────────────────────────────
    staff_rows, staff_keys = collect_staff(data_dir)

    # For universities that have no *_staff.csv (USyd, UniMelb, UWA, UNSW),
    # synthesise minimal Researcher rows from the publications so their
    # publications aren't silently dropped.
    for p in pub_rows:
        name = (p.get("researcher") or "").strip()
        uni  = (p.get("university") or "").strip()
        if not name or not uni:
            continue
        key = (uni, name)
        if key in staff_keys:
            continue
        staff_keys.add(key)
        staff_rows.append({
            "name":              name,
            "job_title":         None,
            "academic_level":    p.get("academic_level"),
            "field_of_research": p.get("field_of_research"),
            "profile_url":       None,
            "university":        uni,
            "orcid":             None,
        })

    r_objs = {}
    for s in staff_rows:
        o = Researcher(
            name=s["name"],
            job_title=s.get("job_title"),
            academic_level=s.get("academic_level"),
            field_of_research=s.get("field_of_research"),
            profile_url=s.get("profile_url"),
            university=s["university"],
            orcid=s.get("orcid"),
        )
        ses.add(o)
        r_objs[(s["university"], s["name"])] = o

    # ── Journals ─────────────────────────────────────────────────────────
    j_objs = {}
    for j in collect_journals(data_dir):
        o = Journal(
            journal_name=j["journal_name"],
            issn=j.get("issn"),
            quality_rank=j.get("quality_rank"),
            abdc_edition=j.get("abdc_edition"),
            impact_factor=as_float(j.get("impact_factor")),
            jcr_year=as_int(j.get("jcr_year")),
            sjr=as_float(j.get("sjr")),
            sjr_quartile=j.get("sjr_quartile"),
            h_index=as_int(j.get("h_index")),
            cites_per_doc_2y=as_float(j.get("cites_per_doc_2y")),
            scimago_year=j.get("scimago_year"),
        )
        ses.add(o)
        j_objs[j["journal_name"]] = o

    # ── Publications ─────────────────────────────────────────────────────
    missing_r = missing_j = 0
    for p in pub_rows:
        uni  = (p.get("university") or "").strip()
        name = (p.get("researcher") or "").strip()
        r = r_objs.get((uni, name))
        j = j_objs.get((p.get("journal_name") or "").strip() or None)
        if r is None:
            missing_r += 1
            continue
        if j is None:
            missing_j += 1
        ses.add(Publication(
            researcher=r,
            journal=j,
            title=p.get("title") or "",
            doi=p.get("doi"),
            author_count=as_int(p.get("author_count")),
            year=as_int(p.get("year")),
            article_url=p.get("article_url"),
            source=p.get("source"),
            citation_percentile=as_float(p.get("citation_percentile")),
        ))

    # ── Harvest ──────────────────────────────────────────────────────────
    harvest_path = data_dir / "harvest.csv"
    if harvest_path.exists():
        for h in read_csv(harvest_path):
            ses.add(Harvest(
                source=h["source"],
                last_run=h.get("last_run"),
                latest_year=as_int(h.get("latest_year")),
            ))

    ses.commit()

    print(f"researchers  {ses.query(Researcher).count()}")
    print(f"journals     {ses.query(Journal).count()}")
    print(f"publications {ses.query(Publication).count()}")
    print(f"harvest      {ses.query(Harvest).count()}")
    if missing_r:
        print(f"WARNING: {missing_r} publications skipped — no matching researcher")
    if missing_j:
        print(f"note: {missing_j} publications have no journal match")


if __name__ == "__main__":
    main()
