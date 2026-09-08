"""Local-only review workbench and background refresh service."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.reviews import ReviewStore  # noqa: E402
from pipeline.runner import DEFAULT_DATA_ROOT, publish_latest, read_json, run_refresh, timestamp  # noqa: E402


STATIC = Path(__file__).resolve().parent / "static"


def latest_failure(data_root: Path):
    failed = data_root / "failed"
    if not failed.exists():
        return None
    reports = sorted(failed.glob("*/failure.json"), reverse=True)
    return read_json(reports[0]) if reports else None


class JobManager:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.lock = threading.Lock()
        self.state = {
            "job_id": None,
            "status": "idle",
            "phase": "idle",
            "message": "No refresh is running",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "result": None,
        }

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _progress(self, phase: str, message: str):
        with self.lock:
            self.state.update({
                "phase": phase,
                "message": message,
                "status": "running" if phase != "completed" else "completed",
            })

    def start(self, *, force: bool = True):
        with self.lock:
            if self.state["status"] in {"queued", "running"}:
                return False, dict(self.state)
            job_id = uuid.uuid4().hex[:12]
            self.state = {
                "job_id": job_id,
                "status": "queued",
                "phase": "queued",
                "message": "Refresh queued",
                "started_at": timestamp(),
                "finished_at": None,
                "error": None,
                "result": None,
            }

        def target():
            try:
                result = run_refresh(self.data_root, refresh=force, progress=self._progress)
                with self.lock:
                    self.state.update({
                        "status": "completed",
                        "phase": "completed",
                        "message": "Refresh completed",
                        "finished_at": timestamp(),
                        "result": result,
                    })
            except Exception as error:
                traceback.print_exc()
                with self.lock:
                    self.state.update({
                        "status": "failed",
                        "phase": "failed",
                        "message": "Refresh failed; the published dataset was not replaced",
                        "finished_at": timestamp(),
                        "error": str(error),
                    })

        threading.Thread(target=target, name=f"refresh-{job_id}", daemon=True).start()
        return True, self.snapshot()


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "CITS3200ReviewWorkbench/1.0"

    @property
    def app(self):
        return self.server.app

    def log_message(self, format, *args):
        sys.stderr.write("[workbench] " + format % args + "\n")

    def json_response(self, value, status=HTTPStatus.OK):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            return self.get_status()
        if parsed.path == "/api/reviews":
            return self.get_reviews(parse_qs(parsed.query))
        return self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/refresh":
            return self.start_refresh()
        if parsed.path == "/api/publish":
            return self.publish()
        prefix, suffix = "/api/reviews/", "/decision"
        if parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
            key = unquote(parsed.path[len(prefix):-len(suffix)])
            return self.save_decision(key)
        self.json_response({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def get_status(self):
        data_root = self.app.data_root
        current = read_json(data_root / "current.json") if (data_root / "current.json").exists() else None
        latest = read_json(data_root / "latest_run.json") if (data_root / "latest_run.json").exists() else None
        store = ReviewStore(data_root / "review.sqlite3")
        try:
            counts = store.counts()
            pending_by_type = store.pending_by_type()
        finally:
            store.close()
        self.json_response({
            "job": self.app.jobs.snapshot(),
            "current": current,
            "latest": latest,
            "latest_failure": latest_failure(data_root),
            "reviews": counts,
            "pending_by_type": pending_by_type,
        })

    def get_reviews(self, query):
        try:
            limit = int((query.get("limit") or ["500"])[0])
        except ValueError:
            return self.json_response({"error": "limit must be an integer"}, HTTPStatus.BAD_REQUEST)
        store = ReviewStore(self.app.data_root / "review.sqlite3")
        try:
            rows = store.list(
                status=(query.get("status") or [None])[0],
                entity_type=(query.get("type") or [None])[0],
                limit=limit,
            )
        finally:
            store.close()
        self.json_response({"reviews": rows})

    def start_refresh(self):
        body = self.read_json_body()
        started, state = self.app.jobs.start(force=bool(body.get("force", True)))
        self.json_response(
            {"started": started, "job": state},
            HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
        )

    def save_decision(self, key: str):
        try:
            body = self.read_json_body()
            store = ReviewStore(self.app.data_root / "review.sqlite3")
            try:
                item = store.get(key)
                if item is None:
                    raise KeyError(key)
                store.decide(
                    key,
                    body.get("status", ""),
                    note=body.get("note", ""),
                    edited=body.get("edited"),
                )
            finally:
                store.close()
            if item["entity_type"] == "identity":
                self.json_response({
                    "saved": True,
                    "requires_refresh": body.get("status") == "approved",
                    "message": "Verified identity saved. Run Refresh to retrieve and validate its publications."
                    if body.get("status") == "approved" else "Identity decision saved; current formal data is unchanged.",
                })
            else:
                manifest = publish_latest(self.app.data_root)
                self.json_response({"saved": True, "current": manifest, "requires_refresh": False})
        except KeyError:
            self.json_response({"error": "review item not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self.json_response({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except Exception as error:
            self.json_response(
                {"error": f"decision saved, but publication failed: {error}"},
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )

    def publish(self):
        try:
            self.json_response({"current": publish_latest(self.app.data_root)})
        except Exception as error:
            self.json_response({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)

    def serve_static(self, requested: str):
        relative = "index.html" if requested in {"", "/"} else requested.lstrip("/")
        path = (STATIC / relative).resolve()
        if STATIC.resolve() not in path.parents and path != STATIC.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, address, handler, data_root: Path):
        super().__init__(address, handler)
        self.app = type("Application", (), {})()
        self.app.data_root = data_root
        self.app.jobs = JobManager(data_root)


def main():
    parser = argparse.ArgumentParser(description="Local CITS3200 review workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    server = WorkbenchServer((args.host, args.port), WorkbenchHandler, args.data_dir.resolve())
    print(f"Review workbench: http://{args.host}:{args.port}")
    print(f"Data directory: {args.data_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
