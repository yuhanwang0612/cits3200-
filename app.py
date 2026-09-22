from flask import Flask, jsonify, request, send_from_directory
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, joinedload

from models import Researcher, Publication, Journal


app = Flask(
    __name__,
    static_folder="site",
    static_url_path=""
)

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "site", "research.db")

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False}
)

Session = sessionmaker(bind=engine)


UNIVERSITY_CODES = {
    "Australian National University": "ANU",
    "Monash University": "MONASH",
    "The University of Adelaide": "UA",
    "University of Adelaide": "UA",
    "Adelaide University": "UA",
    "The University of Melbourne": "UM",
    "University of Melbourne": "UM",
    "The University of New South Wales": "UNSW",
    "UNSW Sydney": "UNSW",
    "The University of Queensland": "UQ",
    "University of Queensland": "UQ",
    "The University of Sydney": "USYD",
    "University of Sydney": "USYD",
    "The University of Western Australia": "UWA",
    "University of Western Australia": "UWA",
}

from admin import make_admin_bp
app.register_blueprint(make_admin_bp(Session))


@app.route("/")
def index():
    return send_from_directory("site", "index.html")


@app.route("/researchers.html")
def researchers_page():
    return send_from_directory("site", "researchers.html")


@app.route("/researcher.html")
def researcher_page():
    return send_from_directory("site", "researcher.html")


@app.route("/universities.html")
def universities_page():
    return send_from_directory("site", "universities.html")


@app.route("/documentation.html")
def documentation_page():
    return send_from_directory("site", "documentation.html")


# ---------------------------------------------------------
# Researchers
# ---------------------------------------------------------

@app.route("/api/researchers")
def get_researchers():

    session = Session()

    university = request.args.get("university")
    field = request.args.get("field")
    level = request.args.get("level")
    name = request.args.get("name")

    query = session.query(Researcher)

    if university:
        query = query.filter(
            Researcher.university == university
        )

    if field:
        query = query.filter(
            Researcher.field_of_research == field
        )

    if level:
        query = query.filter(
            Researcher.academic_level == level
        )

    if name:
        query = query.filter(
            Researcher.name.ilike(f"%{name}%")
        )

    researchers = query.all()

    result = []

    for r in researchers:

        pubs = r.publications

        ranks = [
            p.quality_rank
            for p in pubs
            if p.quality_rank
        ]

        result.append({
            "id": r.researcher_id,
            "name": r.name,
            "job_title": r.job_title,
            "academic_level": r.academic_level,
            "level_code": r.academic_level,
            "university": r.university,
            "field_of_research": r.field_of_research,
            "orcid": r.orcid,
            "profile_url": r.profile_url,

            "publication_count": len(pubs),

            "abdc_ranked_count": len(ranks),

            "count_a_star": ranks.count("A*"),
            "count_a": ranks.count("A"),
            "count_b": ranks.count("B"),
            "count_c": ranks.count("C"),

            "count_unranked": len(pubs) - len(ranks),
        })

    session.close()

    return jsonify({
        "researchers": result
    })


# ---------------------------------------------------------
# Universities
# ---------------------------------------------------------

@app.route("/api/universities")
def get_universities():
    """Aggregate university rankings from the database.

    Publication totals are researcher-publication links, matching the rest of
    the application: a paper co-authored by two included researchers counts
    once for each researcher. Missing JIF values are excluded from the mean;
    they are never treated as zero.
    """

    session = Session()

    try:
        researchers = (
            session.query(Researcher)
            .options(
                joinedload(Researcher.publications)
                .joinedload(Publication.journal)
            )
            .all()
        )

        universities = {}

        def new_metrics():
            return {
                "researcher_count": 0,
                "publication_count": 0,
                "abdc_ranked_count": 0,
                "top_tier_count": 0,
                "jif_values": [],
                "jif_5_values": [],
            }

        for researcher in researchers:
            name = researcher.university
            if name not in universities:
                universities[name] = {
                    "name": name,
                    "code": UNIVERSITY_CODES.get(name, name),
                    "overall": new_metrics(),
                    "Accounting": new_metrics(),
                    "Finance": new_metrics(),
                }

            university = universities[name]
            overall = university["overall"]
            field = university.get(researcher.field_of_research)

            overall["researcher_count"] += 1
            if field is not None:
                field["researcher_count"] += 1

            for publication in researcher.publications:
                rank = (publication.quality_rank or "").strip().upper()
                journal = publication.journal
                jif = journal.impact_factor if journal else None
                jif_5 = journal.impact_factor_5yr if journal else None

                for metrics in (overall, field):
                    if metrics is None:
                        continue
                    metrics["publication_count"] += 1
                    if rank:
                        metrics["abdc_ranked_count"] += 1
                    if rank in {"A*", "A"}:
                        metrics["top_tier_count"] += 1
                    if jif is not None:
                        metrics["jif_values"].append(float(jif))
                    if jif_5 is not None:
                        metrics["jif_5_values"].append(float(jif_5))

        def serialise_metrics(metrics):
            researcher_count = metrics["researcher_count"]
            publication_count = metrics["publication_count"]
            jif_values = metrics.pop("jif_values")
            jif_5_values = metrics.pop("jif_5_values")
            return {
                **metrics,
                "avg_jif": (
                    sum(jif_values) / len(jif_values)
                    if jif_values else None
                ),
                "avg_jif_5": (
                    sum(jif_5_values) / len(jif_5_values)
                    if jif_5_values else None
                ),
                "avg_articles": (
                    publication_count / researcher_count
                    if researcher_count else None
                ),
            }

        result = []
        for university in universities.values():
            overall = serialise_metrics(university["overall"])
            result.append({
                "name": university["name"],
                "code": university["code"],
                **overall,
                "accounting": serialise_metrics(university["Accounting"]),
                "finance": serialise_metrics(university["Finance"]),
            })

        result.sort(key=lambda row: row["name"])
        return jsonify({"universities": result})
    finally:
        session.close()


# ---------------------------------------------------------
# Single researcher
# ---------------------------------------------------------

@app.route("/api/researchers/<int:researcher_id>")
def get_researcher(researcher_id):

    session = Session()

    r = (
        session.query(Researcher)
        .filter(Researcher.researcher_id == researcher_id)
        .first()
    )

    if r is None:
        session.close()
        return jsonify({"error": "Researcher not found"}), 404

    pubs = r.publications

    ranks = [
        p.quality_rank
        for p in pubs
        if p.quality_rank
    ]

    result = {
        "id": r.researcher_id,
        "name": r.name,
        "job_title": r.job_title,
        "academic_level": r.academic_level,
        "university": r.university,
        "field_of_research": r.field_of_research,
        "orcid": r.orcid,
        "profile_url": r.profile_url,

        "publication_count": len(pubs),
        "abdc_ranked_count": len(ranks),

        "count_a_star": ranks.count("A*"),
        "count_a": ranks.count("A"),
        "count_b": ranks.count("B"),
        "count_c": ranks.count("C"),

        "count_unranked": len(pubs) - len(ranks),
    }

    session.close()

    return jsonify(result)


# ---------------------------------------------------------
# Researcher publications
# ---------------------------------------------------------

@app.route("/api/researchers/<int:researcher_id>/publications")
def get_publications(researcher_id):

    session = Session()

    researcher = (
        session.query(Researcher)
        .filter(Researcher.researcher_id == researcher_id)
        .first()
    )

    if researcher is None:
        session.close()
        return jsonify({"error": "Researcher not found"}), 404

    query = (
        session.query(Publication)
        .options(joinedload(Publication.journal))
        .filter(Publication.researcher_id == researcher_id)
    )

    search = request.args.get("search")
    only_abdc = request.args.get("only_abdc")

    if search:
        search_term = f"%{search}%"

        query = query.filter(
            (Publication.title.ilike(search_term))
            |
            (Publication.journal.has(
                Journal.journal_name.ilike(search_term)
            ))
        )

    if only_abdc == "1":
        query = query.filter(
            Publication.quality_rank.isnot(None)
        )

    publications = query.all()

    result = []

    for p in publications:

        journal = p.journal

        result.append({
            "id": p.publication_id,
            "title": p.title,
            "journal_name": (
                journal.journal_name
                if journal else None
            ),

            "issn": journal.issn if journal else None,

            "year": p.year,
            "author_count": p.author_count,
            "authors": p.authors,

            "doi": p.doi,
            "article_url": p.article_url,
            "link": p.link,

            "quality_rank": p.quality_rank,
            "scimago_quartile": p.sjr_quartile,

            "impact_factor": (
                journal.impact_factor
                if journal else None
            ),

            "impact_factor_5yr": (
                journal.impact_factor_5yr
                if journal else None
            ),

            "cited_by_count": p.cited_by_count,
            "citation_percentile": p.citation_percentile,
            "fwci": p.fwci,

            "oa_status": p.oa_status,
            "oa_url": p.oa_url,

            "publication_status": p.publication_status,
            "source": p.source,
        })

    session.close()

    return jsonify({
        "researcher": {
            "id": researcher.researcher_id,
            "name": researcher.name,
            "university": researcher.university,
            "field_of_research": researcher.field_of_research,
            "academic_level": researcher.academic_level,
        },
        "publications": result
    })


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
