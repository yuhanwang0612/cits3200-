from __future__ import annotations

import sys
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
        self.last_source = "never called"

    def snapshot(self):
        return {"state": self.state, "available_sources": ["anu"], "sources": []}

    def start(self, source=None):
        if source not in (None, "anu"):
            raise ValueError(f"unknown university: {source!r}")
        self.last_source = source
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


def test_refresh_api_passes_one_university_through(admin_app):
    app, manager = admin_app
    client = app.test_client()
    login(client)
    assert client.post("/api/admin/refresh", json={"source": "anu"}).status_code == 202
    assert manager.last_source == "anu"


def test_refresh_api_without_a_university_refreshes_all(admin_app):
    app, manager = admin_app
    client = app.test_client()
    login(client)
    assert client.post("/api/admin/refresh").status_code == 202
    assert manager.last_source is None


def test_refresh_api_rejects_an_unknown_university(admin_app):
    app, manager = admin_app
    client = app.test_client()
    login(client)
    response = client.post("/api/admin/refresh", json={"source": "anu; rm -rf /"})
    assert response.status_code == 400
    assert manager.starts == 0


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


def test_single_university_refresh_runs_only_that_one_then_rebuilds(tmp_path):
    manager, db = _prepare_manager(tmp_path, sources=("anu", "monash", "usyd"))
    calls = []

    def fake_run(command, log_path, *, env):
        calls.append(command)
        if command[1] == "load.py":
            Path(env["RESEARCH_DB_PATH"]).write_bytes(b"NEW-DB")
        return 0

    manager._run_command = fake_run
    started, state = manager.start("monash")
    assert started
    assert state["scope"] == "monash"
    assert [s["name"] for s in state["sources"]] == ["monash"]
    # The page fills its university list from this, so it must stay complete.
    assert state["available_sources"] == ["anu", "monash", "usyd"]

    final = _wait(manager)
    assert final["state"] == "succeeded"
    assert [c[3] for c in calls if c[1] == "run.py"] == ["monash"]
    assert any(c[1] == "load.py" for c in calls)
    assert db.read_bytes() == b"NEW-DB"


def test_unknown_university_is_rejected_before_anything_runs(tmp_path):
    manager, db = _prepare_manager(tmp_path, sources=("anu",))
    manager._run_command = lambda *a, **k: pytest.fail("nothing should run")

    with pytest.raises(ValueError):
        manager.start("not-a-university")

    assert manager.snapshot()["state"] == "idle"
    assert db.read_bytes() == b"OLD-DB"


def test_every_child_process_writes_utf8(tmp_path):
    manager, _ = _prepare_manager(tmp_path, sources=("anu",))
    envs = []

    def fake_run(command, log_path, *, env):
        envs.append(env)
        if command[1] == "load.py":
            Path(env["RESEARCH_DB_PATH"]).write_bytes(b"NEW-DB")
        return 0

    manager._run_command = fake_run
    manager.start()
    assert _wait(manager)["state"] == "succeeded"
    assert len(envs) == 2  # the pipeline run and load.py
    assert all(env["PYTHONIOENCODING"] == "utf-8" for env in envs)


def test_real_child_can_log_non_cp1252_titles(tmp_path, monkeypatch):
    """A pipeline printing "Tax‐Aggressive" to the log used to die with
    UnicodeEncodeError on Windows, failing the refresh at its first university."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    manager = RefreshManager(root=tmp_path, db_path=tmp_path / "research.db")
    log = tmp_path / "refresh.log"
    title = "Does Tax‐Aggressive Behavior Motivate CSR? β →"

    code = manager._run_command(
        [sys.executable, "-c", f"print({title!r})"], log, env=manager._child_env()
    )

    assert code == 0
    assert title in log.read_text(encoding="utf-8")


def test_load_py_accepts_staging_database_env():
    text = (Path(__file__).resolve().parents[1] / "load.py").read_text(encoding="utf-8")
    assert 'os.environ.get("RESEARCH_DB_PATH")' in text


def test_unsw_refresh_roster_is_supported_by_runner():
    text = (Path(__file__).resolve().parents[1] / "run.py").read_text(encoding="utf-8")
    assert '"refresh_roster" in collect_params' in text
