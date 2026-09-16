"""Admin: log in, then download/edit/upload publications, journals, and staff.

Each dataset is a separate sheet keyed on its own primary key, and no dataset's
columns leak into another:
  - publications : per-paper fields + researcher_id/journal_id links only
  - journals     : journal-level fields (name, issn, impact factor, ABDC, SJR...)
  - researchers  : staff fields

quality_rank and sjr_quartile are JOURNAL-owned. They live on the journal sheet;
editing them cascades to every publication in that journal (Publication keeps a
copy that the website reads for ABDC counts, so the copy is kept in sync).
Repointing a publication's journal_id also refreshes its copy from the new
journal. This is why journal edits "affect all publications".

Wiring in app.py is unchanged (two lines):
    from admin import make_admin_bp
    app.register_blueprint(make_admin_bp(Session))

.env (gitignored):
    SECRET_KEY=<long random string>
    ADMIN_PASSWORD=<the admin password>

Edits change site/research.db directly; re-running load.py rebuilds from CSVs.
"""

from __future__ import annotations

import hmac
import io
import os
from functools import wraps

import pandas as pd
from flask import (Blueprint, abort, jsonify, redirect, request, send_file,
                   send_from_directory, session)

from models import Publication, Researcher, Journal

# --------------------------------------------------------------------------
# per-dataset config  (key first, then the columns that dataset owns)
# --------------------------------------------------------------------------
# Journal owns quality_rank/sjr_quartile; they are deliberately NOT in the
# publication editable set.
ENTITIES = {
    "publications": {
        "model": Publication,
        "key": "publication_id",
        "editable": [
            "researcher_id", "journal_id", "title", "year", "author_count",
            "authors", "doi", "article_url", "link", "citation_percentile",
            "cited_by_count", "fwci", "oa_status", "oa_url",
            "publication_status", "source",
        ],
        "int": {"researcher_id", "journal_id", "year", "author_count",
                "cited_by_count"},
        "float": {"citation_percentile", "fwci"},
        "download": "publications.xlsx",
    },
    "journals": {
        "model": Journal,
        "key": "journal_id",
        "editable": [
            "journal_name", "journal_raw", "publisher", "issn",
            "quality_rank", "abdc_edition", "impact_factor",
            "impact_factor_5yr", "jcr_year", "sjr", "sjr_quartile",
            "h_index", "cites_per_doc_2y", "scimago_year",
        ],
        "int": {"jcr_year", "h_index"},
        "float": {"impact_factor", "impact_factor_5yr", "sjr",
                  "cites_per_doc_2y"},
        "download": "journals.xlsx",
    },
    "researchers": {
        "model": Researcher,
        "key": "researcher_id",
        "editable": [
            "name", "job_title", "academic_level", "university",
            "field_of_research", "source_id", "orcid", "profile_url",
        ],
        "int": set(),
        "float": set(),
        "download": "researchers.xlsx",
    },
}

# journal fields that, when changed, must propagate to Publication copies
JOURNAL_CASCADE = ("quality_rank", "sjr_quartile")


# --------------------------------------------------------------------------
# coercion
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


def _coerce(cfg, col, v):
    if col in cfg["int"]:
        return _as_int(v)
    if col in cfg["float"]:
        return _as_float(v)
    return _clean(v)


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def _admin_password():
    return os.environ.get("ADMIN_PASSWORD")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("is_admin"):
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify(error="not authenticated"), 401
        return redirect("/admin/login")
    return wrapped


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def _export_df(session_, entity):
    cfg = ENTITIES[entity]
    cols = [cfg["key"]] + cfg["editable"]
    q = session_.query(cfg["model"]).order_by(getattr(cfg["model"], cfg["key"]))
    rows = [{c: getattr(obj, c) for c in cols} for obj in q]
    return pd.DataFrame(rows, columns=cols)


# --------------------------------------------------------------------------
# propagation helpers
# --------------------------------------------------------------------------

def _sync_pub_from_journal(session_, pub):
    """Keep a publication's rank copy equal to its journal's."""
    if pub.journal_id is None:
        return
    j = session_.get(Journal, pub.journal_id)
    if j is not None:
        pub.quality_rank = j.quality_rank
        pub.sjr_quartile = j.sjr_quartile


def _cascade_journal_to_pubs(session_, journal):
    """Push a journal's rank onto every publication in that journal."""
    session_.query(Publication).filter(
        Publication.journal_id == journal.journal_id
    ).update(
        {Publication.quality_rank: journal.quality_rank,
         Publication.sjr_quartile: journal.sjr_quartile},
        synchronize_session=False,
    )


# --------------------------------------------------------------------------
# upsert  (per-row SAVEPOINT so one bad row is attributed and the batch is
# still all-or-nothing)
# --------------------------------------------------------------------------

def upsert_from_dataframe(session_, entity, df, *, dry_run=False):
    cfg = ENTITIES[entity]
    model, key = cfg["model"], cfg["key"]
    df = df.where(pd.notna(df), None)
    df.columns = [str(c).strip() for c in df.columns]
    summary = {"inserted": 0, "updated": 0, "skipped": 0,
               "cascaded_pubs": 0, "errors": []}

    for i, raw in enumerate(df.to_dict(orient="records"), start=2):  # row 2 = first data row
        try:
            with session_.begin_nested():
                pk = _as_int(raw.get(key))
                values = {c: _coerce(cfg, c, raw.get(c))
                          for c in cfg["editable"] if c in raw}

                if pk is not None:
                    obj = session_.get(model, pk)
                    if obj is None:
                        raise ValueError(f"{key} {pk} not found")
                    changed = {c: v for c, v in values.items()
                               if getattr(obj, c) != v}
                    if not changed:
                        summary["skipped"] += 1
                        continue
                    for c, v in changed.items():
                        setattr(obj, c, v)
                    summary["updated"] += 1
                else:
                    obj = _insert(session_, entity, values)
                    summary["inserted"] += 1
                    changed = values

                # propagation
                session_.flush()
                if entity == "publications":
                    _sync_pub_from_journal(session_, obj)
                elif entity == "journals" and any(c in changed for c in JOURNAL_CASCADE):
                    _cascade_journal_to_pubs(session_, obj)
                    n = session_.query(Publication).filter(
                        Publication.journal_id == obj.journal_id).count()
                    summary["cascaded_pubs"] += n
        except Exception as e:                                # noqa: BLE001
            summary["errors"].append({"row": i, "error": str(e)})

    if summary["errors"] or dry_run:
        session_.rollback()      # dry run, or all-or-nothing on any error
    else:
        session_.commit()
    return summary


def _insert(session_, entity, values):
    """Create a new row, validating the minimum each table needs."""
    if entity == "publications":
        rid = values.get("researcher_id")
        if rid is None or session_.get(Researcher, rid) is None:
            raise ValueError("new row needs a valid researcher_id")
        if not (values.get("title") or "").strip():
            raise ValueError("new row needs a title")
        jid = values.get("journal_id")
        if jid is not None and session_.get(Journal, jid) is None:
            raise ValueError(f"journal_id {jid} not found")
    elif entity == "journals":
        if not (values.get("journal_name") or "").strip():
            raise ValueError("new journal needs a journal_name")
    elif entity == "researchers":
        if not (values.get("name") or "").strip():
            raise ValueError("new researcher needs a name")
        if not (values.get("university") or "").strip():
            raise ValueError("new researcher needs a university")

    obj = ENTITIES[entity]["model"](**values)
    session_.add(obj)
    return obj


# --------------------------------------------------------------------------
# blueprint
# --------------------------------------------------------------------------

def make_admin_bp(Session):
    bp = Blueprint("admin", __name__)

    @bp.record_once
    def _setup(state):
        app = state.app
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:                                     # noqa: BLE001
            pass
        if not app.secret_key:
            app.secret_key = os.environ.get("SECRET_KEY") or "dev-insecure-change-me"
            if app.secret_key == "dev-insecure-change-me":
                app.logger.warning("SECRET_KEY not set - using an insecure dev key.")
        if not _admin_password():
            app.logger.warning("ADMIN_PASSWORD not set - admin login disabled.")

    @bp.before_app_request
    def _guard_public_files():
        path = request.path
        if path == "/admin.html":
            return redirect("/admin")
        if path.endswith(".db"):
            abort(404)

    # ---- auth ----
    @bp.get("/admin/login")
    def login_page():
        return send_from_directory("site", "admin_login.html")

    @bp.post("/admin/login")
    def login_submit():
        expected = _admin_password()
        supplied = request.form.get("password", "")
        if expected and hmac.compare_digest(supplied, expected):
            session["is_admin"] = True
            return redirect("/admin")
        return redirect("/admin/login?error=1")

    @bp.get("/admin/logout")
    def logout():
        session.clear()
        return redirect("/admin/login")

    # ---- admin page ----
    @bp.get("/admin")
    @login_required
    def admin_page():
        return send_from_directory("site", "admin.html")

    # ---- generic dataset download/upload ----
    @bp.get("/api/admin/<entity>.xlsx")
    @login_required
    def download_xlsx(entity):
        if entity not in ENTITIES:
            abort(404)
        s = Session()
        try:
            df = _export_df(s, entity)
        finally:
            s.close()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name=entity)
        buf.seek(0)
        return send_file(
            buf, as_attachment=True, download_name=ENTITIES[entity]["download"],
            mimetype=("application/vnd.openxmlformats-officedocument."
                      "spreadsheetml.sheet"))

    @bp.post("/api/admin/<entity>")
    @login_required
    def upload_xlsx(entity):
        if entity not in ENTITIES:
            abort(404)
        f = request.files.get("file")
        if f is None:
            return jsonify(error="no file uploaded"), 400
        try:
            df = pd.read_excel(f, dtype=str)
        except Exception as e:                                # noqa: BLE001
            return jsonify(error=f"could not read Excel: {e}"), 400
        key = ENTITIES[entity]["key"]
        if key not in [str(c).strip() for c in df.columns]:
            return jsonify(error=f"sheet must include a {key} column"), 400

        dry = request.args.get("dry_run") == "1"
        s = Session()
        try:
            result = upsert_from_dataframe(s, entity, df, dry_run=dry)
        finally:
            s.close()
        result["dry_run"] = dry
        return jsonify(result), (200 if not result["errors"] else 422)

    return bp