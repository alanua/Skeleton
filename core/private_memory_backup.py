from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from core.private_memory_history import (
    SCHEMA_VERSION,
    bytes_hash,
    canonical_logical_state_digest,
    current_revision,
    sanitized_integrity_report,
    utc_now,
    verify_existing_integrity_or_raise,
    verify_integrity_or_raise,
)


SNAPSHOT_MANIFEST = "skeleton.private_memory.snapshot_manifest.v1"
SNAPSHOT_VERIFY_REPORT = "skeleton.private_memory.snapshot_verify_report.v1"
RESTORE_REPORT = "skeleton.private_memory.restore_report.v1"
_SAFE_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
_REQUIRED_TABLES = frozenset(
    {
        "private_memory_meta",
        "private_memory_canonical_revision",
        "private_memory_facts",
        "private_memory_events",
        "private_memory_fact_history",
        "private_memory_tombstones",
    }
)
_HASH_CLASS = "sha256"


@dataclass(frozen=True)
class SnapshotManifest:
    schema: str
    snapshot_id: str
    canonical_revision: int
    schema_version: str
    created_at: str
    created_by: str
    aggregate_counts: dict[str, int]
    file_size_bytes: int
    hash_class: str
    content_hash: str
    sqlite_schema_hash: str
    canonical_state_hash: str


@dataclass(frozen=True)
class SnapshotVerifyReport:
    schema: str
    status: str
    snapshot_id: str
    canonical_revision: int
    current_canonical_revision: int | None
    revision_classification: str
    integrity_ok: bool
    hash_match: bool
    schema_match: bool
    hash_class: str
    aggregate_counts: dict[str, int]
    error_class: str | None
    next_operator_action: str


@dataclass(frozen=True)
class RestoreReport:
    schema: str
    status: str
    snapshot_id: str
    canonical_revision: int
    current_canonical_revision: int | None
    revision_classification: str
    integrity_ok: bool
    content_hash_match: bool
    hash_class: str
    aggregate_counts: dict[str, int]
    derived_projections_rebuild_required: list[str]
    activation_required: bool
    activated: bool
    error_class: str | None
    next_operator_action: str


class PrivateMemorySnapshotError(Exception):
    """Raised when snapshot creation or validation fails closed."""


def create_snapshot(
    db_path: str | Path,
    snapshot_dir: str | Path,
    *,
    snapshot_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, object]:
    source_path = Path(db_path)
    if not source_path.is_file():
        raise PrivateMemorySnapshotError("source database unavailable")
    target_dir = Path(snapshot_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    opaque_snapshot_id = _validate_snapshot_id(snapshot_id or f"snapshot-{uuid.uuid4().hex}")
    snapshot_path = snapshot_file_path(target_dir, opaque_snapshot_id)
    if snapshot_path.exists():
        raise PrivateMemorySnapshotError("snapshot already exists")
    temp_path = snapshot_path.with_name(f".{snapshot_path.name}.{uuid.uuid4().hex}.tmp")

    try:
        with sqlite3.connect(str(source_path)) as source:
            source.row_factory = sqlite3.Row
            verify_integrity_or_raise(source)
            with sqlite3.connect(str(temp_path)) as target:
                source.backup(target)

        with sqlite3.connect(str(temp_path)) as snapshot:
            snapshot.row_factory = sqlite3.Row
            verify_integrity_or_raise(snapshot)
        snapshot_path.hardlink_to(temp_path)
    except FileExistsError as exc:
        raise PrivateMemorySnapshotError("snapshot already exists") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()

    with sqlite3.connect(str(snapshot_path)) as snapshot:
        snapshot.row_factory = sqlite3.Row
        verify_integrity_or_raise(snapshot)
        manifest = _manifest_for_snapshot(
            snapshot,
            snapshot_path=snapshot_path,
            snapshot_id=opaque_snapshot_id,
            created_at=created_at or utc_now(),
        )
    manifest_dict = asdict(manifest)
    _write_manifest(snapshot_dir=target_dir, manifest=manifest_dict)
    return _legacy_manifest(manifest_dict)


def manifest_file_path(snapshot_dir: str | Path, snapshot_id: str) -> Path:
    root = Path(snapshot_dir).resolve()
    candidate = (root / f"{_validate_snapshot_id(snapshot_id)}.manifest.json").resolve()
    if not candidate.is_relative_to(root):
        raise PrivateMemorySnapshotError("manifest path escaped snapshot directory")
    return candidate


def load_snapshot_manifest(snapshot_dir: str | Path, snapshot_id: str) -> dict[str, object]:
    manifest_path = manifest_file_path(snapshot_dir, snapshot_id)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - normalize public backup validation failure.
        raise PrivateMemorySnapshotError("snapshot manifest unavailable") from exc
    if not isinstance(value, dict):
        raise PrivateMemorySnapshotError("invalid snapshot manifest")
    return value


def verify_snapshot(
    snapshot_path: str | Path,
    manifest: dict[str, object],
    *,
    current_db_path: str | Path | None = None,
) -> dict[str, object]:
    current_revision_value: int | None = None
    revision_classification = "UNKNOWN"
    try:
        _validate_manifest(Path(snapshot_path), manifest)
        snapshot_revision = int(manifest["canonical_revision"])
        current_revision_value = _current_revision_for_compare(current_db_path)
        revision_classification = _revision_classification(
            snapshot_revision=snapshot_revision,
            current_revision_value=current_revision_value,
        )
        status = "STALE" if revision_classification == "STALE" else "DONE"
        return asdict(
            SnapshotVerifyReport(
                schema=SNAPSHOT_VERIFY_REPORT,
                status=status,
                snapshot_id=str(manifest["snapshot_id"]),
                canonical_revision=snapshot_revision,
                current_canonical_revision=current_revision_value,
                revision_classification=revision_classification,
                integrity_ok=True,
                hash_match=True,
                schema_match=True,
                hash_class=_HASH_CLASS,
                aggregate_counts=dict(manifest["aggregate_counts"]),
                error_class=None,
                next_operator_action="select_newer_snapshot_or_explicit_restore_gate"
                if status == "STALE"
                else "none",
            )
        )
    except Exception as exc:  # noqa: BLE001 - public verification report must fail closed.
        return asdict(
            SnapshotVerifyReport(
                schema=SNAPSHOT_VERIFY_REPORT,
                status="BLOCKED",
                snapshot_id=_safe_report_snapshot_id(manifest),
                canonical_revision=0,
                current_canonical_revision=current_revision_value,
                revision_classification=revision_classification,
                integrity_ok=False,
                hash_match=False,
                schema_match=False,
                hash_class=_HASH_CLASS,
                aggregate_counts={},
                error_class=type(exc).__name__,
                next_operator_action="inspect_private_memory_recovery",
            )
        )


def dry_run_restore_snapshot(
    snapshot_path: str | Path,
    manifest: dict[str, object],
    *,
    current_db_path: str | Path | None = None,
    scratch_dir: str | Path | None = None,
) -> dict[str, object]:
    scratch_root = Path(scratch_dir) if scratch_dir is not None else Path(snapshot_path).parent
    target = scratch_root / f".dry-run-restore-{uuid.uuid4().hex}.sqlite"
    report = verify_snapshot(snapshot_path, manifest, current_db_path=current_db_path)
    if report["status"] != "DONE":
        return _blocked_restore_report(
            manifest,
            current_revision_value=report.get("current_canonical_revision"),
            revision_classification=str(report.get("revision_classification", "UNKNOWN")),
            error_class=str(report.get("error_class") or report["status"]),
        )
    try:
        restored = restore_snapshot_to_isolated_target(snapshot_path, target, manifest)
        if restored["status"] != "DONE":
            return restored
        target.unlink(missing_ok=True)
        return restored
    finally:
        target.unlink(missing_ok=True)


def restore_snapshot_to_isolated_target(
    snapshot_path: str | Path,
    target_path: str | Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    snapshot = Path(snapshot_path)
    target = Path(target_path)
    temp_target: Path | None = None
    try:
        if snapshot.resolve() == target.resolve():
            raise PrivateMemorySnapshotError("restore source equals target")
        if target.exists():
            raise PrivateMemorySnapshotError("restore target already exists")
        _validate_manifest(snapshot, manifest)
        current_revision_value = _current_revision_for_compare(None)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        if temp_target.exists():
            raise PrivateMemorySnapshotError("restore temporary artifact already exists")
        shutil.copyfile(snapshot, temp_target)
        _validate_manifest(temp_target, manifest)
        with sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True) as source_connection:
            source_connection.row_factory = sqlite3.Row
            verify_existing_integrity_or_raise(source_connection)
            source_digest = canonical_logical_state_digest(source_connection)
        with sqlite3.connect(str(temp_target)) as connection:
            connection.row_factory = sqlite3.Row
            integrity = sanitized_integrity_report(connection)
            if integrity["status"] != "DONE":
                raise PrivateMemorySnapshotError("restored snapshot integrity failed")
            if canonical_logical_state_digest(connection) != source_digest:
                raise PrivateMemorySnapshotError("restored snapshot state mismatch")
            revision = current_revision(connection)
            aggregate_counts = _aggregate_counts(connection)
        target.hardlink_to(temp_target)
        temp_target.unlink()
        temp_target = None
        return asdict(
            RestoreReport(
                schema=RESTORE_REPORT,
                status="DONE",
                snapshot_id=str(manifest["snapshot_id"]),
                canonical_revision=revision,
                current_canonical_revision=current_revision_value,
                revision_classification="NOT_COMPARED",
                integrity_ok=True,
                content_hash_match=True,
                hash_class=_HASH_CLASS,
                aggregate_counts=aggregate_counts,
                derived_projections_rebuild_required=["mempalace", "graphify", "cognee"],
                activation_required=True,
                activated=False,
                error_class=None,
                next_operator_action="request_separate_restore_activation_approval",
            )
        )
    except Exception as exc:  # noqa: BLE001 - public restore report must fail closed.
        if temp_target is not None and temp_target.exists():
            temp_target.unlink()
        return _blocked_restore_report(
            manifest,
            current_revision_value=None,
            revision_classification="UNKNOWN",
            error_class=type(exc).__name__,
        )


def snapshot_file_path(snapshot_dir: str | Path, snapshot_id: str) -> Path:
    root = Path(snapshot_dir).resolve()
    candidate = (root / f"{_validate_snapshot_id(snapshot_id)}.sqlite").resolve()
    if not candidate.is_relative_to(root):
        raise PrivateMemorySnapshotError("snapshot path escaped snapshot directory")
    return candidate


def _validate_manifest(snapshot_path: Path, manifest: dict[str, object]) -> None:
    if not snapshot_path.is_file():
        raise PrivateMemorySnapshotError("snapshot unavailable")
    if not isinstance(manifest, dict):
        raise PrivateMemorySnapshotError("invalid manifest")
    if manifest.get("schema") != SNAPSHOT_MANIFEST:
        raise PrivateMemorySnapshotError("invalid manifest schema")
    _validate_snapshot_id(manifest.get("snapshot_id"))
    if manifest.get("hash_class", _HASH_CLASS) != _HASH_CLASS:
        raise PrivateMemorySnapshotError("invalid snapshot hash class")
    if "created_by" in manifest and manifest.get("created_by") != "skeleton.private_memory_backup":
        raise PrivateMemorySnapshotError("invalid snapshot creator")
    file_size = snapshot_path.stat().st_size
    if file_size <= 0 or file_size > _MAX_SNAPSHOT_BYTES:
        raise PrivateMemorySnapshotError("snapshot size mismatch")
    if "file_size_bytes" in manifest and manifest.get("file_size_bytes") != file_size:
        raise PrivateMemorySnapshotError("snapshot size mismatch")
    expected_hash = manifest.get("content_hash")
    if not isinstance(expected_hash, str) or _bounded_file_hash(snapshot_path) != expected_hash:
        raise PrivateMemorySnapshotError("snapshot hash mismatch")
    with sqlite3.connect(f"file:{snapshot_path.as_posix()}?mode=ro", uri=True) as snapshot:
        snapshot.row_factory = sqlite3.Row
        try:
            verify_existing_integrity_or_raise(snapshot)
        except Exception as exc:  # noqa: BLE001 - normalize public restore failure.
            raise PrivateMemorySnapshotError("snapshot integrity failed") from exc
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise PrivateMemorySnapshotError("invalid snapshot schema version")
        if current_revision(snapshot) != manifest.get("canonical_revision"):
            raise PrivateMemorySnapshotError("snapshot revision mismatch")
        if _aggregate_counts(snapshot) != manifest.get("aggregate_counts"):
            raise PrivateMemorySnapshotError("snapshot aggregate mismatch")
        if "sqlite_schema_hash" in manifest and _sqlite_schema_hash(snapshot) != manifest.get("sqlite_schema_hash"):
            raise PrivateMemorySnapshotError("snapshot schema hash mismatch")
        expected_state_hash = manifest.get("canonical_state_hash")
        if not isinstance(expected_state_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_state_hash):
            raise PrivateMemorySnapshotError("invalid snapshot state hash")
        if canonical_logical_state_digest(snapshot) != expected_state_hash:
            raise PrivateMemorySnapshotError("snapshot state mismatch")


def _manifest_for_snapshot(
    snapshot: sqlite3.Connection,
    *,
    snapshot_path: Path,
    snapshot_id: str,
    created_at: str,
) -> SnapshotManifest:
    return SnapshotManifest(
        schema=SNAPSHOT_MANIFEST,
        snapshot_id=snapshot_id,
        canonical_revision=current_revision(snapshot),
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        created_by="skeleton.private_memory_backup",
        aggregate_counts=_aggregate_counts(snapshot),
        file_size_bytes=snapshot_path.stat().st_size,
        hash_class=_HASH_CLASS,
        content_hash=_bounded_file_hash(snapshot_path),
        sqlite_schema_hash=_sqlite_schema_hash(snapshot),
        canonical_state_hash=canonical_logical_state_digest(snapshot),
    )


def _aggregate_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "facts": _count(connection, "private_memory_facts"),
        "events": _count(connection, "private_memory_events"),
        "history_entries": _count(connection, "private_memory_fact_history"),
        "tombstones": _count(connection, "private_memory_tombstones"),
    }


def _count(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _sqlite_schema_hash(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger') AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    table_names = {str(row["name"]) for row in rows if str(row["type"]) == "table"}
    if not _REQUIRED_TABLES.issubset(table_names):
        raise PrivateMemorySnapshotError("foreign snapshot schema")
    schema_rows = [
        {
            "type": str(row["type"]),
            "name": str(row["name"]),
            "table": str(row["tbl_name"]),
            "sql": str(row["sql"]),
        }
        for row in rows
    ]
    return bytes_hash(json.dumps(schema_rows, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _bounded_file_hash(path: Path) -> str:
    size = path.stat().st_size
    if size <= 0 or size > _MAX_SNAPSHOT_BYTES:
        raise PrivateMemorySnapshotError("snapshot outside bounded hash contract")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(*, snapshot_dir: Path, manifest: dict[str, object]) -> None:
    manifest_path = manifest_file_path(snapshot_dir, str(manifest["snapshot_id"]))
    if manifest_path.exists():
        raise PrivateMemorySnapshotError("snapshot manifest already exists")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _legacy_manifest(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "schema": manifest["schema"],
        "snapshot_id": manifest["snapshot_id"],
        "canonical_revision": manifest["canonical_revision"],
        "schema_version": manifest["schema_version"],
        "created_at": manifest["created_at"],
        "aggregate_counts": manifest["aggregate_counts"],
        "content_hash": manifest["content_hash"],
        "canonical_state_hash": manifest["canonical_state_hash"],
    }


def _current_revision_for_compare(current_db_path: str | Path | None) -> int | None:
    if current_db_path is None:
        return None
    path = Path(current_db_path)
    if not path.is_file():
        return None
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        verify_existing_integrity_or_raise(connection)
        return current_revision(connection)


def _revision_classification(*, snapshot_revision: int, current_revision_value: int | None) -> str:
    if current_revision_value is None:
        return "NOT_COMPARED"
    if snapshot_revision < current_revision_value:
        return "STALE"
    if snapshot_revision > current_revision_value:
        return "AHEAD"
    return "MATCH"


def _blocked_restore_report(
    manifest: object,
    *,
    current_revision_value: object,
    revision_classification: str,
    error_class: str,
) -> dict[str, object]:
    return asdict(
        RestoreReport(
            schema=RESTORE_REPORT,
            status="BLOCKED",
            snapshot_id=_safe_report_snapshot_id(manifest),
            canonical_revision=0,
            current_canonical_revision=current_revision_value if isinstance(current_revision_value, int) else None,
            revision_classification=revision_classification,
            integrity_ok=False,
            content_hash_match=False,
            hash_class=_HASH_CLASS,
            aggregate_counts={},
            derived_projections_rebuild_required=["mempalace", "graphify", "cognee"],
            activation_required=True,
            activated=False,
            error_class=error_class,
            next_operator_action="require_restore_activation_gate",
        )
    )


def _validate_snapshot_id(snapshot_id: object) -> str:
    if not isinstance(snapshot_id, str) or not _SAFE_SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise PrivateMemorySnapshotError("invalid snapshot id")
    if "/" in snapshot_id or "\\" in snapshot_id or ".." in snapshot_id:
        raise PrivateMemorySnapshotError("invalid snapshot id")
    return snapshot_id


def _safe_report_snapshot_id(manifest: object) -> str:
    if not isinstance(manifest, dict):
        return "unknown"
    try:
        return _validate_snapshot_id(manifest.get("snapshot_id"))
    except PrivateMemorySnapshotError:
        return "unknown"
