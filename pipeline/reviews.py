"""Durable local review decisions for uncertain records."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DECISIONS = {"approved", "rejected", "deferred", "pending"}
ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$", re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_key(kind: str, *parts: Any) -> str:
    identity = "|".join(str(part or "") for part in parts)
    return f"{kind}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def staff_review_key(record: dict[str, Any]) -> str:
    return stable_key("staff", record.get("university"), record.get("discipline"), record.get("profile_url"))


def publication_review_key(record: dict[str, Any]) -> str:
    return stable_key(
        "publication",
        record.get("university"),
        record.get("discipline"),
        record.get("name"),
        record.get("publication_id"),
    )


def validate_identity_override(candidate: dict[str, Any], edited: dict[str, Any]) -> list[str]:
    """Validate evidence required before a manual UniMelb identity is trusted."""
    errors = []
    for field in ("name", "discipline", "profile_url"):
        if edited.get(field) != candidate.get(field):
            errors.append(f"{field} must not be changed")
    if not str(edited.get("repository_author_name") or "").strip():
        errors.append("repository_author_name is required")
    internal_id = str(edited.get("internal_id") or "").strip()
    orcid = str(edited.get("orcid") or "").strip().removeprefix("https://orcid.org/")
    if not internal_id and not orcid:
        errors.append("enter at least one verified internal_id or ORCID")
    if orcid and not ORCID_RE.fullmatch(orcid):
        errors.append("ORCID must use the format 0000-0000-0000-0000")
    evidence_url = str(edited.get("evidence_url") or "").strip()
    if not evidence_url.startswith(("https://", "http://")):
        errors.append("evidence_url must be an http(s) URL")
    return errors


class ReviewStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                review_key TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                university TEXT,
                discipline TEXT,
                label TEXT NOT NULL,
                reason TEXT NOT NULL,
                confidence TEXT NOT NULL,
                effect TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                candidate_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                edited_json TEXT,
                note TEXT NOT NULL DEFAULT '',
                first_seen_run TEXT NOT NULL,
                last_seen_run TEXT NOT NULL,
                decided_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                decided_at TEXT
                ,active INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_status_updated
            ON reviews(status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_reviews_entity_university
            ON reviews(entity_type, university, discipline);
            """
        )
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(reviews)")}
        if "active" not in columns:
            self.connection.execute("ALTER TABLE reviews ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_reviews_active_status ON reviews(active, status, updated_at DESC)"
        )
        self.connection.execute("PRAGMA optimize")

    def close(self) -> None:
        self.connection.close()

    def upsert_candidates(self, candidates: Iterable[dict[str, Any]], run_id: str) -> None:
        timestamp = now()
        with self.connection:
            # Only candidates from the newest successful raw run should be in
            # the workbench.  Historical decisions remain in SQLite for audit
            # and can become active again if the same stable key reappears.
            self.connection.execute("UPDATE reviews SET active = 0")
            for candidate in candidates:
                candidate_json = canonical_json(candidate["candidate"])
                fingerprint = payload_hash(candidate["candidate"])
                existing = self.connection.execute(
                    "SELECT candidate_hash, status FROM reviews WHERE review_key = ?",
                    (candidate["review_key"],),
                ).fetchone()
                status = existing["status"] if existing else "pending"
                decided_hash = None
                edited_json = None
                decided_at = None
                if existing:
                    previous = self.connection.execute(
                        "SELECT decided_hash, edited_json, decided_at FROM reviews WHERE review_key = ?",
                        (candidate["review_key"],),
                    ).fetchone()
                    decided_hash, edited_json, decided_at = previous
                    if existing["candidate_hash"] != fingerprint and status in {"approved", "rejected", "deferred", "changed"}:
                        status = "changed"
                        # Never pre-fill the editor with an approval payload
                        # belonging to an older version of the candidate.
                        edited_json = None
                self.connection.execute(
                    """
                    INSERT INTO reviews (
                        review_key, entity_type, university, discipline, label,
                        reason, confidence, effect, candidate_json, candidate_hash,
                        status, edited_json, note, first_seen_run, last_seen_run,
                        decided_hash, created_at, updated_at, decided_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(review_key) DO UPDATE SET
                        entity_type=excluded.entity_type,
                        university=excluded.university,
                        discipline=excluded.discipline,
                        label=excluded.label,
                        reason=excluded.reason,
                        confidence=excluded.confidence,
                        effect=excluded.effect,
                        candidate_json=excluded.candidate_json,
                        candidate_hash=excluded.candidate_hash,
                        status=excluded.status,
                        edited_json=excluded.edited_json,
                        last_seen_run=excluded.last_seen_run,
                        decided_hash=excluded.decided_hash,
                        updated_at=excluded.updated_at,
                        decided_at=excluded.decided_at,
                        active=1
                    """,
                    (
                        candidate["review_key"], candidate["entity_type"], candidate.get("university"),
                        candidate.get("discipline"), candidate["label"], candidate["reason"],
                        candidate.get("confidence", "uncertain"), candidate.get("effect", "controls_inclusion"),
                        candidate_json, fingerprint, status, edited_json, run_id, run_id,
                        decided_hash, timestamp, timestamp, decided_at,
                    ),
                )

    def decide(self, review_key: str, status: str, *, note: str = "", edited: dict[str, Any] | None = None) -> None:
        if status not in DECISIONS - {"pending"}:
            raise ValueError(f"invalid review decision: {status}")
        row = self.connection.execute(
            "SELECT entity_type, candidate_json, candidate_hash FROM reviews WHERE review_key = ?", (review_key,)
        ).fetchone()
        if not row:
            raise KeyError(review_key)
        if status == "approved" and row["entity_type"] == "identity":
            if edited is None:
                raise ValueError("verified identity fields are required")
            errors = validate_identity_override(json.loads(row["candidate_json"]), edited)
            if errors:
                raise ValueError("identity verification incomplete: " + "; ".join(errors))
        timestamp = now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE reviews
                SET status=?, edited_json=?, note=?, decided_hash=?, decided_at=?, updated_at=?
                WHERE review_key=?
                """,
                (
                    status,
                    canonical_json(edited) if edited is not None else None,
                    note.strip(),
                    row["candidate_hash"],
                    timestamp,
                    timestamp,
                    review_key,
                ),
            )

    def get(self, review_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM reviews WHERE review_key = ?", (review_key,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["candidate"] = json.loads(item.pop("candidate_json"))
        item["edited"] = json.loads(item.pop("edited_json")) if item.get("edited_json") else None
        item.pop("edited_json", None)
        return item

    def approved_identity_overrides(self) -> list[dict[str, Any]]:
        """Return current approved identities, including inactive historical rows."""
        rows = self.connection.execute(
            """
            SELECT candidate_json, edited_json
            FROM reviews
            WHERE entity_type='identity' AND status='approved'
              AND candidate_hash=decided_hash
            """
        ).fetchall()
        result = []
        for row in rows:
            candidate = json.loads(row["candidate_json"])
            edited = json.loads(row["edited_json"] or row["candidate_json"])
            if not validate_identity_override(candidate, edited):
                result.append(edited)
        return result

    def approved_payload(self, review_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT candidate_json, edited_json, candidate_hash, decided_hash, status
            FROM reviews WHERE review_key=?
            """,
            (review_key,),
        ).fetchone()
        if not row or row["status"] != "approved" or row["candidate_hash"] != row["decided_hash"]:
            return None
        return json.loads(row["edited_json"] or row["candidate_json"])

    def list(self, *, status: str | None = None, entity_type: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        clauses, values = ["active = 1"], []
        if status and status != "all":
            clauses.append("status = ?")
            values.append(status)
        if entity_type and entity_type != "all":
            clauses.append("entity_type = ?")
            values.append(entity_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM reviews{where} ORDER BY updated_at DESC LIMIT ?",  # noqa: S608 - clauses are fixed
            (*values, max(1, min(limit, 2000))),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["candidate"] = json.loads(item.pop("candidate_json"))
            item["edited"] = json.loads(item.pop("edited_json")) if item.get("edited_json") else None
            item.pop("edited_json", None)
            item["decision_is_current"] = item["candidate_hash"] == item.get("decided_hash")
            result.append(item)
        return result

    def counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ("pending", "changed", "approved", "rejected", "deferred")}
        for row in self.connection.execute("SELECT status, COUNT(*) AS count FROM reviews WHERE active = 1 GROUP BY status"):
            counts[row["status"]] = row["count"]
        counts["total"] = sum(value for key, value in counts.items() if key != "total")
        return counts

    def pending_by_type(self) -> dict[str, int]:
        counts = {kind: 0 for kind in ("staff", "identity", "publication")}
        for row in self.connection.execute(
            """
            SELECT entity_type, COUNT(*) AS count FROM reviews
            WHERE active=1 AND status IN ('pending', 'changed')
            GROUP BY entity_type
            """
        ):
            counts[row["entity_type"]] = row["count"]
        return counts
