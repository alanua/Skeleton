from __future__ import annotations

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
    ensure_history_schema,
    sanitized_integrity_report,
    utc_now,
    verify_existing_integrity_or_raise,
    verify_integrity_or_raise,
)


SNAPSHOT_MANIFEST = "skeleton.private_memory.snapshot_manifest.v1"
RESTORE_REPORT = "skeleton.private_memory.restore_report.v1"
_SAFE_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class SnapshotManifest:
    schema: str
    snapshot_id: str
    canonical_revision: int
    schema_version: str
    created_at: str
    aggregate_counts: dict[str, int]
    content_hash: str
    canonical_state_hash: str


@dataclass(frozen=True)
class RestoreReport:
    schema: str
    status: str
    snapshot_id: str
    canonical_revision: int
    integrity_ok: bool
    content_hash_match: bool
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
            ensure_history_schema(source)
            verify_integrity_or_raise(source)
            with sqlite3.connect(str(temp_path)) as target:
                source.backup(target)

        with sqlite3.connect(str(temp_path)) as snapshot:
            snapshot.row_factory = sqlite3.Row
            ensure_history_schema(snapshot)
            verify_integrity_or_raise(snapshot)
        snapshot_path.hardlink_to(temp_path)
    except FileExistsError as exc:
        raise PrivateMemorySnapshotError("snapshot already exists") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()

    with sqlite3.connect(str(snapshot_path)) as snapshot:
        snapshot.row_factory = sqlite3.Row
        ensure_history_schema(snapshot)
        verify_integrity_or_raise(snapshot)
        manifest = _manifest_for_snapshot(
            snapshot,
            snapshot_path=snapshot_path,
            snapshot_id=opaque_snapshot_id,
            created_at=created_at or utc_now(),
        )
    return asdict(manifest)


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
            ensure_history_schema(connection)
            integrity = sanitized_integrity_report(connection)
            if integrity["status"] != "DONE":
                raise PrivateMemorySnapshotError("restored snapshot integrity failed")
            if canonical_logical_state_digest(connection) != source_digest:
                raise PrivateMemorySnapshotError("restored snapshot state mismatch")
            revision = current_revision(connection)
        target.hardlink_to(temp_target)
        temp_target.unlink()
        temp_target = None
        return asdict(
            RestoreReport(
                schema=RESTORE_REPORT,
                status="DONE",
                snapshot_id=str(manifest["snapshot_id"]),
                canonical_revision=revision,
                integrity_ok=True,
                content_hash_match=True,
                activation_required=True,
                activated=False,
                error_class=None,
                next_operator_action="request_separate_restore_activation_approval",
            )
        )
    except Exception as exc:  # noqa: BLE001 - public restore report must fail closed.
        if temp_target is not None and temp_target.exists():
            temp_target.unlink()
        return asdict(
            RestoreReport(
                schema=RESTORE_REPORT,
                status="BLOCKED",
                snapshot_id=_safe_report_snapshot_id(manifest),
                canonical_revision=0,
                integrity_ok=False,
                content_hash_match=False,
                activation_required=True,
                activated=False,
                error_class=type(exc).__name__,
                next_operator_action="require_restore_activation_gate",
            )
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
    expected_hash = manifest.get("content_hash")
    if not isinstance(expected_hash, str) or bytes_hash(snapshot_path.read_bytes()) != expected_hash:
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
        aggregate_counts=_aggregate_counts(snapshot),
        content_hash=bytes_hash(snapshot_path.read_bytes()),
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
