"""From-zero collection, validation, review gating and atomic publication."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import sys
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .reviews import ReviewStore, publication_review_key, stable_key
from .schema import validate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = ROOT / "data"
Collector = Callable[..., tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]]
TEAM_FIELDS = (
    "name", "job_title", "academic_level", "field_of_research", "profile_url",
    "university", "orcid", "title", "doi", "author_count", "year",
    "article_url", "source", "citation_percentile", "journal_name", "issn",
    "quality_rank", "impact_factor",
)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else value


def write_records(directory: Path, name: str, rows: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    atomic_json(directory / f"{name}.json", rows)
    headers = list(dict.fromkeys(key for row in rows for key in row))
    temporary = directory / f".{name}.{uuid.uuid4().hex}.csv.tmp"
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows({key: csv_value(value) for key, value in row.items()} for row in rows)
    os.replace(temporary, directory / f"{name}.csv")


def write_csv(path: Path, headers: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: csv_value(value) for key, value in row.items()} for row in rows)
    os.replace(temporary, path)


@contextmanager
def refresh_lock(data_root: Path):
    """Cross-process lock without requiring a platform-specific package."""
    path = data_root / "refresh.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = None
    for _attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as error:
            try:
                owner = read_json(path)
                if owner.get("host") != socket.gethostname():
                    raise RuntimeError(f"another refresh is already running ({path})") from error
                os.kill(int(owner["pid"]), 0)
            except ProcessLookupError:
                path.unlink(missing_ok=True)
                continue
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
                raise RuntimeError(f"another refresh is already running ({path})") from error
            raise RuntimeError(f"another refresh is already running ({path})") from error
    if descriptor is None:
        raise RuntimeError(f"could not acquire refresh lock ({path})")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "started_at": timestamp()}, handle)
        yield
    finally:
        path.unlink(missing_ok=True)


def default_collectors() -> dict[str, Collector]:
    from base_scrapers import unimelb, uwa

    def collect_uwa(**options):
        options.pop("identity_overrides", None)
        staff, publications = uwa.collect(**options)
        return staff, publications, dict(uwa.LAST_QUALITY)

    def collect_unimelb(**options):
        staff, publications = unimelb.collect(**options)
        return staff, publications, dict(unimelb.LAST_QUALITY)

    return {"UWA": collect_uwa, "UNIMELB": collect_unimelb}


def annotate(records: list[dict[str, Any]], university: str) -> list[dict[str, Any]]:
    return [{**record, "university": record.get("university") or university} for record in records]


def source_failures(university: str, quality: dict[str, Any]) -> list[str]:
    """Convert partial-fetch signals into errors that block publication."""
    problems = []
    for field in ("staff_failures", "discovery_failures", "detail_failures", "search_failures"):
        failures = quality.get(field) or []
        if failures:
            problems.append(f"{university} reported {len(failures)} {field.replace('_', ' ')}")
    feed_shortfalls = [
        row for row in quality.get("publication_feed_reconciliation", [])
        if row.get("reported_publication_count") is not None
        and row.get("discovered_publication_count", 0) < row["reported_publication_count"]
    ]
    if feed_shortfalls:
        problems.append(f"{university} reported {len(feed_shortfalls)} publication feed shortfalls")
    seed_shortfalls = [
        discipline for discipline, row in quality.get("departmental_seed_reconciliation", {}).items()
        if row.get("reported_record_count") is not None
        and row.get("extracted_record_count", 0) < row["reported_record_count"]
    ]
    if seed_shortfalls:
        problems.append(f"{university} reported repository seed shortfalls for {seed_shortfalls}")
    return problems


def review_candidates(
    staff: list[dict[str, Any]], publications: list[dict[str, Any]], source_quality: dict[str, Any]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    # Official department rosters define staff inclusion. Appointment labels
    # may be retained as notes, but are never approval gates.
    for record in publications:
        confidence = record.get("researcher_match_confidence") or "unknown"
        if not record.get("requires_review") and confidence == "high":
            continue
        candidates.append({
            "review_key": publication_review_key(record),
            "entity_type": "publication",
            "university": record["university"],
            "discipline": record["discipline"],
            "label": record["title"],
            "reason": record.get("review_reason") or f"researcher match confidence is {confidence}",
            "confidence": confidence,
            "effect": "controls_inclusion",
            "candidate": record,
        })
    for university, quality in source_quality.items():
        for identity in quality.get("identity_review_queue", []):
            candidates.append({
                "review_key": stable_key(
                    "identity", university, identity.get("discipline"), identity.get("profile_url")
                ),
                "entity_type": "identity",
                "university": university,
                "discipline": identity.get("discipline"),
                "label": identity.get("name") or identity.get("profile_url") or "Unknown identity",
                "reason": "No unique Minerva author identity was found. The person stays in the staff table, but publications are withheld until an exact ID is verified.",
                "confidence": identity.get("identity_confidence", "unknown"),
                "effect": "requires_refresh_for_publications",
                "candidate": {
                    **identity,
                    "repository_author_name": identity.get("repository_author_name") or "",
                    "internal_id": identity.get("internal_id") or "",
                    "orcid": identity.get("orcid") or "",
                    "evidence_url": identity.get("evidence_url") or "",
                },
            })
    return candidates


def _approved_records(
    staff: list[dict[str, Any]], publications: list[dict[str, Any]], store: ReviewStore
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    approved_staff = list(staff)

    allowed = {
        (record["university"], record["discipline"], record["name_clean"])
        for record in approved_staff
    }
    approved_publications = []
    for record in publications:
        owner = (record["university"], record["discipline"], record["name"])
        if owner not in allowed:
            continue
        confidence = record.get("researcher_match_confidence") or "unknown"
        if record.get("requires_review") or confidence != "high":
            approved = store.approved_payload(publication_review_key(record))
            if approved is None:
                continue
            record = approved
        approved_publications.append(record)
    return approved_staff, approved_publications


def team_rows(
    staff: list[dict[str, Any]], publications: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Flatten approved relationships using the team's agreed column names."""
    by_source_id = {
        (row["university"], row["discipline"], row.get("source_id")): row
        for row in staff if row.get("source_id")
    }
    by_name: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in staff:
        by_name.setdefault(
            (row["university"], row["discipline"], row["name_clean"]), []
        ).append(row)

    result = []
    for publication in publications:
        person = None
        if publication.get("source_id"):
            person = by_source_id.get((
                publication["university"], publication["discipline"], publication["source_id"]
            ))
        candidates = by_name.get((
            publication["university"], publication["discipline"], publication["name"]
        ), [])
        if person is None and len(candidates) == 1:
            person = candidates[0]
        if person is None:
            raise ValueError(
                "cannot resolve a unique staff row for team export: "
                f"{publication['university']} / {publication['discipline']} / {publication['name']}"
            )
        issns = publication.get("issns") or []
        result.append({
            "name": person["name_clean"],
            "job_title": person.get("title"),
            "academic_level": person.get("level_code"),
            "field_of_research": person["discipline"],
            "profile_url": person["profile_url"],
            "university": person["university"],
            "orcid": person.get("orcid"),
            "title": publication["title"],
            "doi": publication.get("doi"),
            "author_count": publication.get("n_authors"),
            "year": publication.get("year"),
            "article_url": publication.get("link"),
            "source": "pure" if person["university"] == "UWA" else "minerva",
            "citation_percentile": publication.get("citation_percentile"),
            "journal_name": publication.get("journal_canonical") or publication.get("journal"),
            "issn": issns[0] if issns else None,
            "quality_rank": publication.get("quality_rank"),
            "impact_factor": publication.get("impact_factor"),
        })
    return result


def normalized_title(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def team_row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    publication = (
        "doi", str(row.get("doi") or "").casefold()
    ) if row.get("doi") else (
        "title-year", normalized_title(row.get("title")), row.get("year")
    )
    return (
        row.get("university"), row.get("field_of_research"),
        row.get("profile_url"), publication,
    )


def deduplicate_team_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the team's researcher/publication key without losing evidence."""
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(team_row_key(row), []).append(row)

    fields_for_preference = (
        "doi", "article_url", "author_count", "year", "journal_name", "issn",
        "citation_percentile", "quality_rank", "impact_factor",
    )
    output, duplicate_groups = [], []
    for key, candidates in grouped.items():
        primary = max(
            candidates,
            key=lambda row: sum(row.get(field) not in (None, "", []) for field in fields_for_preference),
        ).copy()
        for candidate in candidates:
            for field in TEAM_FIELDS:
                if primary.get(field) in (None, "", []) and candidate.get(field) not in (None, "", []):
                    primary[field] = candidate[field]
        output.append(primary)
        if len(candidates) > 1:
            duplicate_groups.append({
                "team_key": key,
                "candidate_count": len(candidates),
                "selected_record": primary,
                "source_records": candidates,
                "rule": "prefer the most complete row, then fill only missing fields",
            })
    output.sort(key=lambda row: (
        row["university"], row["field_of_research"], row["name"].casefold(),
        -(int(row["year"]) if row.get("year") else 0), row["title"].casefold(),
    ))
    return output, duplicate_groups


def publish_run(data_root: Path, source_run_id: str) -> dict[str, Any]:
    run_directory = data_root / "runs" / source_run_id
    staff = read_json(run_directory / "raw" / "staff.json")
    publications = read_json(run_directory / "raw" / "publications.json")
    store = ReviewStore(data_root / "review.sqlite3")
    try:
        approved_staff, approved_publications = _approved_records(staff, publications, store)
        problems = validate(approved_staff, approved_publications)
        if problems:
            raise ValueError("published dataset failed validation:\n" + "\n".join(problems[:30]))
        publication_id = source_run_id + "-" + uuid.uuid4().hex[:8]
        staging = data_root / "published" / f".staging-{publication_id}"
        final = data_root / "published" / publication_id
        staging.mkdir(parents=True, exist_ok=False)
        write_records(staging, "staff", approved_staff)
        write_records(staging, "publications", approved_publications)
        uwa_staff = [row for row in approved_staff if row["university"] == "UWA"]
        unimelb_staff = [row for row in approved_staff if row["university"] == "UNIMELB"]
        write_records(staging, "uwa_staff", uwa_staff)
        write_records(staging, "unimelb_staff", unimelb_staff)
        raw_exported = team_rows(approved_staff, approved_publications)
        exported, duplicate_groups = deduplicate_team_rows(raw_exported)
        uwa_export = [row for row in exported if row["university"] == "UWA"]
        unimelb_export = [row for row in exported if row["university"] == "UNIMELB"]
        write_csv(staging / "uwa_team_fields.csv", TEAM_FIELDS, uwa_export)
        write_csv(staging / "unimelb_team_fields.csv", TEAM_FIELDS, unimelb_export)
        write_csv(staging / "uwa_unimelb_team_fields.csv", TEAM_FIELDS, exported)
        atomic_json(staging / "team_export_duplicate_candidates.json", duplicate_groups)
        quality = {
            "published_at": timestamp(),
            "source_run_id": source_run_id,
            "staff_records": len(approved_staff),
            "official_roster_staff_records": len(approved_staff),
            "staff_records_excluded": 0,
            "staff_by_university": {
                "UWA": len(uwa_staff),
                "UNIMELB": len(unimelb_staff),
            },
            "researcher_publication_links": len(approved_publications),
            "unique_publications": len({row["publication_id"] for row in approved_publications}),
            "review_counts": store.counts(),
            "team_export_rows": {"UWA": len(uwa_export), "UNIMELB": len(unimelb_export)},
            "team_export_duplicate_rows_merged": len(raw_exported) - len(exported),
            "team_export_duplicate_groups": len(duplicate_groups),
            "team_export_completeness": {
                field: sum(row.get(field) not in (None, "", []) for row in exported)
                for field in TEAM_FIELDS
            },
            "validation_problems": [],
        }
        atomic_json(staging / "quality.json", quality)
        os.replace(staging, final)
        manifest = {
            "publication_id": publication_id,
            "source_run_id": source_run_id,
            "published_at": quality["published_at"],
            "directory": str(final.relative_to(data_root)),
            "staff_json": str((final / "staff.json").relative_to(data_root)),
            "publications_json": str((final / "publications.json").relative_to(data_root)),
            "staff_csv": str((final / "staff.csv").relative_to(data_root)),
            "publications_csv": str((final / "publications.csv").relative_to(data_root)),
            "quality_json": str((final / "quality.json").relative_to(data_root)),
            "uwa_staff_csv": str((final / "uwa_staff.csv").relative_to(data_root)),
            "unimelb_staff_csv": str((final / "unimelb_staff.csv").relative_to(data_root)),
            "uwa_team_csv": str((final / "uwa_team_fields.csv").relative_to(data_root)),
            "unimelb_team_csv": str((final / "unimelb_team_fields.csv").relative_to(data_root)),
            "combined_team_csv": str((final / "uwa_unimelb_team_fields.csv").relative_to(data_root)),
            "team_duplicate_candidates_json": str((final / "team_export_duplicate_candidates.json").relative_to(data_root)),
        }
        atomic_json(data_root / "current.json", manifest)
        return manifest
    finally:
        store.close()


def publish_latest(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Any]:
    latest = read_json(data_root / "latest_run.json")
    return publish_run(data_root, latest["run_id"])


def run_refresh(
    data_root: Path = DEFAULT_DATA_ROOT,
    *,
    refresh: bool = True,
    limit_staff: int | None = None,
    collectors: dict[str, Collector] | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Build and publish a new dataset without reading any previous dataset."""
    data_root = Path(data_root)
    progress = progress or (lambda phase, message: None)
    require_all_disciplines = collectors is None and limit_staff is None
    collectors = collectors or default_collectors()
    identifier = run_id()
    staging = data_root / "runs" / f".staging-{identifier}"
    final = data_root / "runs" / identifier
    with refresh_lock(data_root):
        previous_cache = os.environ.get("CITS3200_CACHE_DIR")
        os.environ["CITS3200_CACHE_DIR"] = str(data_root / "cache")
        staging.mkdir(parents=True, exist_ok=False)
        try:
            review_store = ReviewStore(data_root / "review.sqlite3")
            try:
                identity_overrides = review_store.approved_identity_overrides()
            finally:
                review_store.close()
            progress("collecting", "Collecting official university records")
            staff: list[dict[str, Any]] = []
            publications: list[dict[str, Any]] = []
            source_quality: dict[str, Any] = {}
            for university, collector in collectors.items():
                progress("collecting", f"Collecting {university}")
                local_staff, local_publications, quality = collector(
                    verbose=False, refresh=refresh, limit_staff=limit_staff,
                    identity_overrides=identity_overrides,
                )
                annotated_staff = annotate(local_staff, university)
                annotated_publications = annotate(local_publications, university)
                staff.extend(annotated_staff)
                publications.extend(annotated_publications)
                source_quality[university] = quality

                # Preserve enough evidence to diagnose a failed first-ever
                # refresh.  These files live only in the staging/failed run
                # and can never become the formal dataset.
                write_records(staging / "partial", "staff", staff)
                write_records(staging / "partial", "publications", publications)
                atomic_json(staging / "source_quality.json", source_quality)
                if not local_staff:
                    raise ValueError(f"{university} returned no staff records")
                if require_all_disciplines:
                    missing_disciplines = {"Accounting", "Finance"} - {
                        row.get("discipline") for row in local_staff
                    }
                    if missing_disciplines:
                        raise ValueError(
                            f"{university} returned no staff for {sorted(missing_disciplines)}"
                        )
                failures = source_failures(university, quality)
                if failures:
                    raise ValueError("source collection was incomplete:\n" + "\n".join(failures))

            progress("validating", "Validating raw records")
            problems = validate(staff, publications)
            if problems:
                raise ValueError("raw dataset failed validation:\n" + "\n".join(problems[:30]))
            candidates = review_candidates(staff, publications, source_quality)
            write_records(staging / "raw", "staff", staff)
            write_records(staging / "raw", "publications", publications)
            atomic_json(staging / "review_candidates.json", candidates)
            atomic_json(staging / "source_quality.json", source_quality)
            run_quality = {
                "run_id": identifier,
                "completed_at": timestamp(),
                "from_zero_capable": True,
                "previous_dataset_read": False,
                "staff_records": len(staff),
                "researcher_publication_links": len(publications),
                "review_candidates": len(candidates),
                "validation_problems": [],
            }
            atomic_json(staging / "quality.json", run_quality)
            os.replace(staging, final)

            store = ReviewStore(data_root / "review.sqlite3")
            try:
                store.upsert_candidates(candidates, identifier)
            finally:
                store.close()
            atomic_json(data_root / "latest_run.json", {"run_id": identifier, "completed_at": run_quality["completed_at"]})
            progress("publishing", "Publishing approved records")
            manifest = publish_run(data_root, identifier)
            progress("completed", "Refresh completed")
            return {"run": run_quality, "current": manifest}
        except BaseException as error:
            if staging.exists():
                atomic_json(staging / "failure.json", {
                    "run_id": identifier,
                    "failed_at": timestamp(),
                    "error": str(error),
                    "current_dataset_was_replaced": False,
                })
                failed = data_root / "failed" / identifier
                failed.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, failed)
            progress("failed", "Refresh failed; the current dataset was not changed")
            raise
        finally:
            if previous_cache is None:
                os.environ.pop("CITS3200_CACHE_DIR", None)
            else:
                os.environ["CITS3200_CACHE_DIR"] = previous_cache


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent CITS3200 data pipeline")
    parser.add_argument("command", choices=("refresh", "publish"))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--use-cache", action="store_true", help="reuse fresh HTTP cache entries")
    parser.add_argument("--limit-staff", type=int)
    args = parser.parse_args()
    try:
        if args.command == "refresh":
            result = run_refresh(args.data_dir, refresh=not args.use_cache, limit_staff=args.limit_staff, progress=lambda p, m: print(f"[{p}] {m}"))
        else:
            result = publish_latest(args.data_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
