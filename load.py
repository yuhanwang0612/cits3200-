"""Load staff/journals/publications/harvest CSVs into research.db."""
import math
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Researcher, Journal, Publication, Harvest

DB   = "sqlite:///research.db"
DATA = "."          # folder holding the CSVs


def nn(v):
    """NaN/empty -> None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def rows(name):
    df = pd.read_csv(f"{DATA}/{name}.csv", dtype=str, keep_default_na=True)
    return [{k: nn(v) for k, v in r.items()} for r in df.to_dict("records")]


def as_int(v):
    return int(float(v)) if nn(v) is not None else None


def as_float(v):
    return float(v) if nn(v) is not None else None


def main():
    engine = create_engine(DB)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ses = sessionmaker(bind=engine)()

    r_objs = {}
    for s in rows("staff"):
        o = Researcher(
            name=s["name"],
            job_title=s.get("job_title"),
            academic_level=s.get("academic_level"),
            field_of_research=s.get("field_of_research"),
            profile_url=s.get("profile_url"),
            university=s["university"],
        )
        ses.add(o)
        r_objs[(s["university"], s.get("espace_id"))] = o

    j_objs = {}
    for j in rows("journals"):
        o = Journal(
            journal_name=j["journal_name"],
            issn=j.get("issn"),
            quality_rank=j.get("quality_rank"),
            impact_factor=as_float(j.get("impact_factor")),
            impact_factor_5yr=as_float(j.get("impact_factor_5yr")),
        )
        ses.add(o)
        j_objs[j["journal_name"]] = o

    missing_r = missing_j = 0
    for p in rows("publications"):
        uni = p.get("university") or "University of Queensland"
        r = r_objs.get((uni, p.get("espace_id")))
        j = j_objs.get(p.get("journal_name"))
        if r is None:
            missing_r += 1
            continue
        if j is None:
            missing_j += 1
        ses.add(Publication(
            researcher=r,
            journal=j,
            title=p["title"],
            doi=p.get("doi"),
            author_count=as_int(p.get("author_count")),
            year=as_int(p.get("year")),
            article_url=p.get("article_url"),
            source=p.get("source"),
            citation_percentile=as_float(p.get("citation_percentile")),
        ))

    for h in rows("harvest"):
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