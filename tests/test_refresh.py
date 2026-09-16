from __future__ import annotations

import time
from pathlib import Path

import pytest
from flask import Flask

from admin import make_admin_bp
from refresh_manager import RefreshManager


class FakeSessionMaker:
    kw = {"bind": None}


class FakeManager:
    def __init__(self, state="idle"):
        self.state = state
        self.starts = 0

    def snapshot(self):
        return {"state": self.state, "available_sources": ["anu"], "sources": []}

    def start(self):
        self.starts += 1
        if self.state in {"queued", "running"}:
            return False, self.snapshot()
        self.state = "queued"
        return True, self.snapshot()


@pytest.fixture
def admin_app(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    manager = FakeManager()
    app = Flask(__name__)
    app.register_blueprint(make_admin_bp(FakeSessionMaker(), refresh_manager=manager))
    app.testing = True
    return app, manager


def login(client):
    return client.post("/admin/login", data={"password": "secret"})


def test_refresh_api_is_admin_only(admin_app):
    app, _ = admin_app
    client = app.test_client()
    assert client.get("/api/admin/refresh/status").status_code == 401
    assert client.post("/api/admin/refresh").status_code == 401


def test_refresh_api_starts_and_reports_conflict(admin_app):
    app, manager = admin_app
    client = app.test_client()
    login(client)
    assert client.get("/api/admin/refresh/status").status_code == 200
    first = client.post("/api/admin/refresh")
    assert first.status_code == 202
    assert manager.starts == 1
    second = client.post("/api/admin/refresh")
    assert second.status_code == 409


def test_discovery_uses_go8_order(tmp_path):
    scraper_dir = tmp_path / "base_scrapers"
    scraper_dir.mkdir()
    for name in ["uwa", "monash", "anu", "adelaide", "usyd", "uq", "unsw", "unimelb"]:
        (scraper_dir / f"{name}.py").write_text("", encoding="utf-8")
    (scraper_dir / "__init__.py").write_text("", encoding="utf-8")
    manager = RefreshManager(root=tmp_path, db_path=tmp_path / "research.db")
    assert manager.discover_sources() == [
        "adelaide", "anu", "unimelb", "monash", "unsw", "uq", "usyd", "uwa"
    ]


def _prepare_manager(tmp_path, sources=("anu", "usyd")):
    (tmp_path / "base_scrapers").mkdir()
    (tmp_path / "site").mkdir()
    for source in sources:
        (tmp_path / "base_scrapers" / f"{source}.py").write_text("", encoding="utf-8")
    (tmp_path / "run.py").write_text("", encoding="utf-8")
    (tmp_path / "load.py").write_text("", encoding="utf-8")
    db = tmp_path / "site" / "research.db"
    db.write_bytes(b"OLD-DB")
    return RefreshManager(root=tmp_path, db_path=db), db


def _wait(manager, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = manager.snapshot()
        if state["state"] not in {"queued", "running"}:
            return state
        time.sleep(0.01)
    raise AssertionError("refresh job did not finish")


def test_success_builds_staging_db_then_swaps_live_db(tmp_path, monkeypatch):
    manager, db = _prepare_manager(tmp_path)
    calls = []

    def fake_run(command, log_path, *, env):
        calls.append(command)
        if command[1] == "load.py":
            Path(env["RESEARCH_DB_PATH"]).write_bytes(b"NEW-DB")
        return 0

    monkeypatch.delenv("CLARIVATE_API_KEY", raising=False)
    manager._run_command = fake_run
    started, _ = manager.start()
    assert started
    state = _wait(manager)
    assert state["state"] == "succeeded"
    assert db.read_bytes() == b"NEW-DB"
    assert state["backup_path"]
    assert (tmp_path / state["backup_path"]).read_bytes() == b"OLD-DB"
    pipeline_calls = [c for c in calls if c[1] == "run.py"]
    assert len(pipeline_calls) == 2
    assert all("--refresh" in c and "--skip-clarivate" in c for c in pipeline_calls)


def test_clarivate_key_keeps_clarivate_enabled(tmp_path, monkeypatch):
    manager, _ = _prepare_manager(tmp_path, sources=("anu",))
    commands = []

    def fake_run(command, log_path, *, env):
        commands.append(command)
        if command[1] == "load.py":
            Path(env["RESEARCH_DB_PATH"]).write_bytes(b"NEW-DB")
        return 0

    monkeypatch.setenv("CLARIVATE_API_KEY", "real-key")
    manager._run_command = fake_run
    manager.start()
    state = _wait(manager)
    assert state["state"] == "succeeded"
    pipeline = next(c for c in commands if c[1] == "run.py")
    assert "--skip-clarivate" not in pipeline


def test_scraper_failure_does_not_replace_database(tmp_path):
    manager, db = _prepare_manager(tmp_path)

    def fake_run(command, log_path, *, env):
        if command[1] == "run.py" and command[3] == "anu":
            return 7
        return 0

    manager._run_command = fake_run
    manager.start()
    state = _wait(manager)
    assert state["state"] == "failed"
    assert db.read_bytes() == b"OLD-DB"
    anu = next(s for s in state["sources"] if s["name"] == "anu")
    usyd = next(s for s in state["sources"] if s["name"] == "usyd")
    assert anu["state"] == "failed"
    assert usyd["state"] == "not_run"


def test_database_load_failure_does_not_replace_database(tmp_path):
    manager, db = _prepare_manager(tmp_path, sources=("anu",))

    def fake_run(command, log_path, *, env):
        return 9 if command[1] == "load.py" else 0

    manager._run_command = fake_run
    manager.start()
    state = _wait(manager)
    assert state["state"] == "failed"
    assert db.read_bytes() == b"OLD-DB"


def test_second_start_is_rejected_while_running(tmp_path):
    manager, _ = _prepare_manager(tmp_path, sources=("anu",))

    def slow_run(command, log_path, *, env):
        time.sleep(0.15)
        if command[1] == "load.py":
            Path(env["RESEARCH_DB_PATH"]).write_bytes(b"NEW-DB")
        return 0

    manager._run_command = slow_run
    started, _ = manager.start()
    assert started
    started_again, state = manager.start()
    assert not started_again
    assert state["state"] in {"queued", "running"}
    _wait(manager)


def test_load_py_accepts_staging_database_env():
    text = (Path(__file__).resolve().parents[1] / "load.py").read_text(encoding="utf-8")
    assert 'os.environ.get("RESEARCH_DB_PATH")' in text


def test_unsw_refresh_roster_is_supported_by_runner():
    text = (Path(__file__).resolve().parents[1] / "run.py").read_text(encoding="utf-8")
    assert '"refresh_roster" in collect_params' in text
