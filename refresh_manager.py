"""Background orchestration for the admin "refresh all data" action.

A refresh deliberately keeps the live SQLite database untouched while the
university pipelines run.  Once every source succeeds, ``load.py`` builds a
staging database and that completed file is atomically swapped into place.
The previous live database is retained in ``backups/`` for rollback.
"""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


G8_ORDER = (
    "adelaide",
    "anu",
    "unimelb",
    "monash",
    "unsw",
    "uq",
    "usyd",
    "uwa",
)

_RUNNING_STATES = {"queued", "running"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clarivate_enabled(value: str | None) -> bool:
    value = (value or "").strip()
    return bool(value and value.lower() not in {"replace_me", "none", "null"})


class RefreshManager:
    """Run all available university pipelines sequentially in one background job."""

    def __init__(self, root: Path | str | None = None, db_path: Path | str | None = None,
                 engine=None):
        self.root = Path(root or Path(__file__).resolve().parent).resolve()
        self.db_path = Path(db_path or self.root / "site" / "research.db").resolve()
        self.engine = engine
        self._lock = threading.Lock()
        self._state = self._idle_state()

    def discover_sources(self) -> list[str]:
        scraper_dir = self.root / "base_scrapers"
        available = {
            p.stem for p in scraper_dir.glob("*.py")
            if p.name != "__init__.py" and not p.name.startswith("_")
        }
        # Only the eight explicitly supported Go8 adapters are executable from
        # the admin surface; helper modules added later must never become shell
        # targets just because they live in base_scrapers/.
        return [name for name in G8_ORDER if name in available]

    def _idle_state(self) -> dict:
        return {
            "state": "idle",
            "job_id": None,
            "started_at": None,
            "finished_at": None,
            "current_source": None,
            "message": "Ready",
            "available_sources": self.discover_sources(),
            "sources": [],
            "backup_path": None,
            "log_path": None,
            "clarivate": "enabled" if _clarivate_enabled(os.environ.get("CLARIVATE_API_KEY"))
            else "skipped (CLARIVATE_API_KEY not configured)",
        }

    def snapshot(self) -> dict:
        with self._lock:
            snap = copy.deepcopy(self._state)
        if snap["state"] == "idle":
            snap["available_sources"] = self.discover_sources()
            snap["clarivate"] = (
                "enabled" if _clarivate_enabled(os.environ.get("CLARIVATE_API_KEY"))
                else "skipped (CLARIVATE_API_KEY not configured)"
            )
        return snap

    def start(self) -> tuple[bool, dict]:
        with self._lock:
            if self._state["state"] in _RUNNING_STATES:
                return False, copy.deepcopy(self._state)

            sources = self.discover_sources()
            if not sources:
                self._state = self._idle_state()
                self._state.update(state="failed", finished_at=_utc_now(),
                                   message="No university adapters were found.")
                return False, copy.deepcopy(self._state)

            job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            self._state = {
                "state": "queued",
                "job_id": job_id,
                "started_at": _utc_now(),
                "finished_at": None,
                "current_source": None,
                "message": "Refresh queued",
                "available_sources": sources,
                "sources": [
                    {"name": source, "state": "pending", "started_at": None,
                     "finished_at": None, "seconds": None, "error": None}
                    for source in sources
                ],
                "backup_path": None,
                "log_path": None,
                "clarivate": "enabled" if _clarivate_enabled(os.environ.get("CLARIVATE_API_KEY"))
                else "skipped (CLARIVATE_API_KEY not configured)",
            }
            state = copy.deepcopy(self._state)

        thread = threading.Thread(target=self._run_job, name=f"refresh-{job_id}", daemon=True)
        thread.start()
        return True, state

    def _update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _update_source(self, source: str, **values) -> None:
        with self._lock:
            for item in self._state["sources"]:
                if item["name"] == source:
                    item.update(values)
                    break

    @staticmethod
    def _append_log(log_path: Path, text: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")

    def _run_command(self, command: list[str], log_path: Path, *, env: dict[str, str]) -> int:
        self._append_log(log_path, "\n$ " + " ".join(command))
        with log_path.open("a", encoding="utf-8") as fh:
            completed = subprocess.run(
                command,
                cwd=self.root,
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        return completed.returncode

    def _run_job(self) -> None:
        snap = self.snapshot()
        job_id = snap["job_id"]
        logs_dir = self.root / "logs" / "refresh"
        backups_dir = self.root / "backups"
        logs_dir.mkdir(parents=True, exist_ok=True)
        backups_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{job_id}.log"
        backup_path = backups_dir / f"research-{job_id}.db"
        staging_path = self.db_path.with_name(f"research.refresh-{job_id}.db")

        self._update(state="running", message="Creating database backup",
                     log_path=str(log_path.relative_to(self.root)))
        self._append_log(log_path, f"Refresh job {job_id} started at {_utc_now()}")

        try:
            if self.db_path.exists():
                shutil.copy2(self.db_path, backup_path)
                self._update(backup_path=str(backup_path.relative_to(self.root)))
                self._append_log(log_path, f"Database backup: {backup_path}")
            else:
                self._append_log(log_path, "No existing database was present to back up.")

            env = os.environ.copy()
            use_clarivate = _clarivate_enabled(env.get("CLARIVATE_API_KEY"))

            for source in snap["available_sources"]:
                started = time.monotonic()
                self._update(current_source=source, message=f"Refreshing {source}")
                self._update_source(source, state="running", started_at=_utc_now())

                command = [sys.executable, "run.py", "--uni", source, "--refresh"]
                if not use_clarivate:
                    command.append("--skip-clarivate")

                returncode = self._run_command(command, log_path, env=env)
                elapsed = round(time.monotonic() - started, 1)
                if returncode != 0:
                    self._update_source(source, state="failed", finished_at=_utc_now(),
                                        seconds=elapsed,
                                        error=f"pipeline exited with code {returncode}")
                    with self._lock:
                        for item in self._state["sources"]:
                            if item["state"] == "pending":
                                item["state"] = "not_run"
                    self._update(
                        state="failed",
                        finished_at=_utc_now(),
                        current_source=None,
                        message=(f"Refresh stopped at {source}. The live database was not changed. "
                                 f"See {log_path.relative_to(self.root)}."),
                    )
                    return

                self._update_source(source, state="succeeded", finished_at=_utc_now(),
                                    seconds=elapsed, error=None)

            # Build a complete replacement database away from the live file.  load.py
            # honours RESEARCH_DB_PATH specifically for this staging step.
            staging_path.unlink(missing_ok=True)
            load_env = env.copy()
            load_env["RESEARCH_DB_PATH"] = str(staging_path)
            self._update(current_source="database", message="Building refreshed database")
            returncode = self._run_command([sys.executable, "load.py"], log_path, env=load_env)
            if returncode != 0 or not staging_path.exists():
                staging_path.unlink(missing_ok=True)
                self._update(
                    state="failed",
                    finished_at=_utc_now(),
                    current_source=None,
                    message=("All scrapers completed, but the database reload failed. "
                             "The live database was not changed."),
                )
                return

            # Close pooled SQLite handles before and after the atomic swap so new
            # web requests connect to the refreshed file.
            if self.engine is not None:
                self.engine.dispose()
            os.replace(staging_path, self.db_path)
            if self.engine is not None:
                self.engine.dispose()

            self._update(
                state="succeeded",
                finished_at=_utc_now(),
                current_source=None,
                message="Refresh completed and the database was replaced successfully.",
            )
            self._append_log(log_path, f"Refresh job completed at {_utc_now()}")
        except Exception as exc:  # noqa: BLE001
            staging_path.unlink(missing_ok=True)
            self._append_log(log_path, f"UNEXPECTED ERROR: {exc!r}")
            self._update(
                state="failed",
                finished_at=_utc_now(),
                current_source=None,
                message=("Refresh failed unexpectedly. The live database was not changed. "
                         f"See {log_path.relative_to(self.root)}."),
            )
