"""Admin: download all publications as Excel, edit, upload to change the DB.

Plugs into the existing app.py as a Blueprint (see the two-line hook at the
bottom of this docstring) so app.py itself doesn't need rewriting.

    # in app.py, after `app = Flask(...)` and the engine/Session setup:
    from admin import make_admin_bp
    app.register_blueprint(make_admin_bp(Session))

Round-trip key: `publication_id`. It's downloaded in the sheet and matched
exactly on upload, so an edited row updates the right publication with no
DOI/title guessing. A row with a blank publication_id is treated as a NEW
publication (insert) and needs a researcher_id.

Scope (iteration 1): only columns that live on the Publication table are
editable. researcher_name / university / journal_name / issn are included in
the sheet for context but ignored on upload — journal fields live on the
Journal table and editing them per-row is ambiguous (rename the journal vs
re-point the publication), so that's deliberately left for a later pass.

NOTE: edits change site/research.db directly. Re-running load.py rebuilds the
DB from the CSVs and will overwrite them — see option B we discussed.
"""

from __future__ import annotations

import io

import pandas as pd
from flask import Blueprint, jsonify, request, send_file, send_from_directory

from models import Publication, Researcher, Journal

# publication_id first (the key), then read-only context, then editables.
KEY_COL = "publication_id"
CONTEXT_COLS = ["researcher_id", "researcher_name", "university",
                "journal_name", "issn"]
EDITABLE_COLS = [
    "title", "year", "author_count", "authors", "doi", "article_url", "link",
    "quality_rank", "sjr_quartile", "citation_percentile", "cited_by_count",
    "fwci", "oa_status", "oa_url", "publication_status", "source",
]
EXPORT_COLS = [KEY_COL] + CONTEXT_COLS + EDITABLE_COLS

INT_COLS = {"year", "author_count", "cited_by_count"}
FLOAT_COLS = {"citation_percentile", "fwci"}


# --------------------------------------------------------------------------
# coercion (mirrors load.py's clean/as_int/as_float)
# --------------------------------------------------------------------------

def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    if not v or v.lower() in {"nan", "none", "null"}:
        return None
    return v


def _as_int(v):
    v = _clean(v)
    try:
        return int(float(v)) if v is not None else None
    except (ValueError, TypeError):
        return None


def _as_float(v):
    v = _clean(v)
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _coerce(col, v):
    if col in INT_COLS:
        return _as_int(v)
    if col in FLOAT_COLS:
        return _as_float(v)
    return _clean(v)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def _export_dataframe(session) -> pd.DataFrame:
    rows = []
    q = (session.query(Publication)
         .join(Researcher, Publication.researcher_id == Researcher.researcher_id)
         .outerjoin(Journal, Publication.journal_id == Journal.journal_id)
         .order_by(Researcher.university, Researcher.name, Publication.year.desc()))
    for p in q:
        r, j = p.researcher, p.journal
        row = {
            KEY_COL: p.publication_id,
            "researcher_id": p.researcher_id,
            "researcher_name": r.name if r else None,
            "university": r.university if r else None,
            "journal_name": j.journal_name if j else None,
            "issn": j.issn if j else None,
        }
        for c in EDITABLE_COLS:
            row[c] = getattr(p, c)
        rows.append(row)
    return pd.DataFrame(rows, columns=EXPORT_COLS)


# --------------------------------------------------------------------------
# import / upsert
# --------------------------------------------------------------------------

def upsert_from_dataframe(session, df: pd.DataFrame, *, dry_run: bool = False) -> dict:
    df = df.where(pd.notna(df), None)
    df.columns = [str(c).strip() for c in df.columns]
    summary = {"inserted": 0, "updated": 0, "skipped": 0, "errors": []}

    for i, raw in enumerate(df.to_dict(orient="records"), start=2):  # row 2 = first data row
        try:
            pid = _as_int(raw.get(KEY_COL))
            values = {c: _coerce(c, raw.get(c)) for c in EDITABLE_COLS if c in raw}

            if pid is not None:
                pub = session.get(Publication, pid)
                if pub is None:
                    summary["errors"].append(
                        {"row": i, "error": f"{KEY_COL} {pid} not found"})
                    continue
                changed = {c: v for c, v in values.items()
                           if getattr(pub, c) != v}
                if not changed:
                    summary["skipped"] += 1
                else:
                    if not dry_run:
                        for c, v in changed.items():
                            setattr(pub, c, v)
                    summary["updated"] += 1
            else:
                rid = _as_int(raw.get("researcher_id"))
                if rid is None or session.get(Researcher, rid) is None:
                    summary["errors"].append(
                        {"row": i, "error": "new row needs a valid researcher_id"})
                    continue
                if not (values.get("title") or "").strip():
                    summary["errors"].append(
                        {"row": i, "error": "new row needs a title"})
                    continue
                jname = _clean(raw.get("journal_name"))
                journal = (session.query(Journal)
                           .filter(Journal.journal_name == jname).first()
                           if jname else None)
                if not dry_run:
                    session.add(Publication(researcher_id=rid,
                                            journal=journal, **values))
                summary["inserted"] += 1
        except Exception as e:                                # noqa: BLE001
            summary["errors"].append({"row": i, "error": str(e)})

    if summary["errors"]:
        session.rollback()          # all-or-nothing: one bad row aborts the batch
    elif not dry_run:
        session.commit()
    return summary


# --------------------------------------------------------------------------
# blueprint
# --------------------------------------------------------------------------

def make_admin_bp(Session):
    bp = Blueprint("admin", __name__)

    @bp.get("/admin")
    def admin_page():
        return send_from_directory("site", "admin.html")

    @bp.get("/api/admin/publications.xlsx")
    def download_xlsx():
        session = Session()
        try:
            df = _export_dataframe(session)
        finally:
            session.close()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="publications")
        buf.seek(0)
        return send_file(
            buf, as_attachment=True, download_name="publications.xlsx",
            mimetype=("application/vnd.openxmlformats-officedocument."
                      "spreadsheetml.sheet"))

    @bp.post("/api/admin/publications")
    def upload_xlsx():
        f = request.files.get("file")
        if f is None:
            return jsonify(error="no file uploaded"), 400
        try:
            df = pd.read_excel(f, dtype=str)
        except Exception as e:                                # noqa: BLE001
            return jsonify(error=f"could not read Excel: {e}"), 400
        if KEY_COL not in [str(c).strip() for c in df.columns]:
            return jsonify(error=f"sheet must include a {KEY_COL} column"), 400

        dry = request.args.get("dry_run") == "1"
        session = Session()
        try:
            result = upsert_from_dataframe(session, df, dry_run=dry)
        finally:
            session.close()
        result["dry_run"] = dry
        return jsonify(result), (200 if not result["errors"] else 422)

    return bp