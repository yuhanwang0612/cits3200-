"""Admin: log in, download all publications as Excel, edit, upload to change the DB.

Plugs into app.py as a Blueprint (same two lines as before):

    from admin import make_admin_bp
    app.register_blueprint(make_admin_bp(Session))

The blueprint loads .env and sets app.secret_key itself (see _setup), so app.py
needs no extra wiring. Put these in your .env (already gitignored):

    SECRET_KEY=<some long random string>
    ADMIN_PASSWORD=<the admin password>

Auth: a single shared admin password (fine for this tool). On success the
session cookie carries is_admin=True; every admin route below is gated.
Pages redirect to /admin/login when logged out; /api/ routes return 401 so the
upload page can show a message instead of an HTML redirect.

Round-trip key: publication_id (see upsert_from_dataframe). Editing scope is
the Publication table only; researcher/journal columns are context, ignored on
upload. Edits change site/research.db directly and are overwritten by load.py.
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

def _export_dataframe(session_):
    rows = []
    q = (session_.query(Publication)
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

def upsert_from_dataframe(session_, df, *, dry_run=False):
    df = df.where(pd.notna(df), None)
    df.columns = [str(c).strip() for c in df.columns]
    summary = {"inserted": 0, "updated": 0, "skipped": 0, "errors": []}

    for i, raw in enumerate(df.to_dict(orient="records"), start=2):  # row 2 = first data row
        try:
            pid = _as_int(raw.get(KEY_COL))
            values = {c: _coerce(c, raw.get(c)) for c in EDITABLE_COLS if c in raw}

            if pid is not None:
                pub = session_.get(Publication, pid)
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
                if rid is None or session_.get(Researcher, rid) is None:
                    summary["errors"].append(
                        {"row": i, "error": "new row needs a valid researcher_id"})
                    continue
                if not (values.get("title") or "").strip():
                    summary["errors"].append(
                        {"row": i, "error": "new row needs a title"})
                    continue
                jname = _clean(raw.get("journal_name"))
                journal = (session_.query(Journal)
                           .filter(Journal.journal_name == jname).first()
                           if jname else None)
                if not dry_run:
                    session_.add(Publication(researcher_id=rid,
                                             journal=journal, **values))
                summary["inserted"] += 1
        except Exception as e:                                # noqa: BLE001
            summary["errors"].append({"row": i, "error": str(e)})

    if summary["errors"]:
        session_.rollback()          # all-or-nothing: one bad row aborts the batch
    elif not dry_run:
        session_.commit()
    return summary


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
                app.logger.warning(
                    "SECRET_KEY not set - using an insecure dev key. "
                    "Set SECRET_KEY in .env before deploying.")
        if not _admin_password():
            app.logger.warning(
                "ADMIN_PASSWORD not set - admin login is disabled until it is "
                "set in .env.")

    # ---- close the static-file bypass ----
    # app.py serves the whole site/ folder at the root (static_url_path="").
    # That would otherwise expose the admin page shell and, worse, the SQLite
    # DB (site/research.db) to anyone. This app-level guard runs before the
    # static handler on every request.
    @bp.before_app_request
    def _guard_public_files():
        path = request.path
        if path == "/admin.html":
            return redirect("/admin")          # force through the gated route
        if path.endswith(".db"):
            abort(404)                          # never serve the database file

    # ---- auth routes ----

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

    # ---- gated admin routes ----

    @bp.get("/admin")
    @login_required
    def admin_page():
        return send_from_directory("site", "admin.html")

    @bp.get("/api/admin/publications.xlsx")
    @login_required
    def download_xlsx():
        s = Session()
        try:
            df = _export_dataframe(s)
        finally:
            s.close()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="publications")
        buf.seek(0)
        return send_file(
            buf, as_attachment=True, download_name="publications.xlsx",
            mimetype=("application/vnd.openxmlformats-officedocument."
                      "spreadsheetml.sheet"))

    @bp.post("/api/admin/publications")
    @login_required
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
        s = Session()
        try:
            result = upsert_from_dataframe(s, df, dry_run=dry)
        finally:
            s.close()
        result["dry_run"] = dry
        return jsonify(result), (200 if not result["errors"] else 422)

    return bp