from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
import fcntl
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.canonical_memory import FAST_AUTONOMOUS_EXECUTION_KEY
from core.graphify_adapter import LocalGraphifyIndex
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.mempalace_adapter import LocalMemPalaceIndex
from core.private_memory import CanonicalPrivateMemoryStore
from core.private_memory_backup import create_snapshot
from core.private_memory_bundle import (
    PRIVATE_MEMORY_IMPORT_RECEIPT_NAMESPACE,
    cleanup_pre_operation_snapshot,
    make_pre_operation_snapshot,
    move_processed_bundle,
    prepare_private_memory_import_bundle,
)
from core.private_memory_history import (
    bytes_hash,
    canonical_json,
    content_hash,
    current_revision,
    enable_wal_if_supported,
    ensure_history_schema,
    safe_token,
    sanitized_integrity_report,
    verify_existing_integrity_or_raise,
)
from core.skeleton_memory import SkeletonMemory


PRIVATE_MEMORY_STACK_STATUS_SCHEMA = "skeleton.private_memory_stack.status.v1"
PRIVATE_MEMORY_STACK_MUTATION_SCHEMA = "skeleton.private_memory_stack.mutation.v1"
PRIVATE_MEMORY_STACK_ROOT_ENV = "SKELETON_PRIVATE_MEMORY_ROOT"
APPROVED_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "canonical_memory"
    / "operator_preferences_fast_autonomous_execution_v1.json"
)


class PrivateMemoryStackError(RuntimeError):
    """Raised when the local-private memory stack fails closed."""


@dataclass(frozen=True)
class PrivateMemoryStackPaths:
    root: Path
    db: Path
    graphify: Path
    mempalace: Path
    backups: Path
    gateway_db: Path
    lock: Path


class PrivateMemoryStack:
    """Production local-private stack with SQLite as the only authority."""

    def __init__(self, private_root: str | Path | None = None) -> None:
        self.paths = _paths(private_root)
        self.store = CanonicalPrivateMemoryStore(self.paths.db)

    def init(self, *, import_manifest: bool = True) -> dict[str, object]:
        self._ensure_private_root()
        self._initialize_canonical_database()
        if import_manifest:
            self.import_approved_manifest()
        self.rebuild()
        return self.status()

    def put(
        self,
        *,
        namespace: str,
        fact_id: str,
        value: Any,
        actor_ref: str = "operator",
        reason_code: str = "operator-put",
        approval_ref: str = "local-operator",
        transaction_ref: str | None = None,
    ) -> dict[str, object]:
        transaction = transaction_ref or f"local-{uuid.uuid4().hex}"
        with _exclusive_lock(self.paths.lock):
            before = self._database_logical_backup()
            try:
                event = self.store.put_fact(
                    namespace=namespace,
                    fact_id=fact_id,
                    value=value,
                    actor_ref=actor_ref,
                    reason_code=reason_code,
                    approval_ref=approval_ref,
                    transaction_ref=transaction,
                )
                self._rebuild_unlocked()
            except Exception as exc:
                self._restore_database_backup(before)
                self._best_effort_rebuild_unlocked()
                _remove_sqlite_sidecars(self.paths.db)
                raise PrivateMemoryStackError("canonical mutation rolled back after derived index rebuild failure") from exc
            finally:
                _remove_backup_file(before)
        return {
            "schema": PRIVATE_MEMORY_STACK_MUTATION_SCHEMA,
            "status": "DONE",
            "canonical_revision": event["canonical_revision"],
            "canonical_ref": _canonical_ref(namespace, fact_id),
            "indexes": self._index_states(),
        }

    def delete(
        self,
        *,
        namespace: str,
        fact_id: str,
        actor_ref: str = "operator",
        reason_code: str = "operator-delete",
        approval_ref: str = "local-operator",
        transaction_ref: str | None = None,
    ) -> dict[str, object]:
        transaction = transaction_ref or f"local-{uuid.uuid4().hex}"
        with _exclusive_lock(self.paths.lock):
            before = self._database_logical_backup()
            try:
                event = self.store.tombstone_fact(
                    namespace=namespace,
                    fact_id=fact_id,
                    actor_ref=actor_ref,
                    reason_code=reason_code,
                    approval_ref=approval_ref,
                    transaction_ref=transaction,
                )
                self._rebuild_unlocked()
            except Exception as exc:
                self._restore_database_backup(before)
                self._best_effort_rebuild_unlocked()
                _remove_sqlite_sidecars(self.paths.db)
                raise PrivateMemoryStackError("canonical delete rolled back after derived index rebuild failure") from exc
            finally:
                _remove_backup_file(before)
        return {
            "schema": PRIVATE_MEMORY_STACK_MUTATION_SCHEMA,
            "status": "DONE",
            "canonical_revision": event["canonical_revision"],
            "canonical_ref": _canonical_ref(namespace, fact_id),
            "indexes": self._index_states(),
        }

    def get(self, *, namespace: str, fact_id: str) -> dict[str, object]:
        namespace = safe_token(namespace, "namespace")
        fact_id = safe_token(fact_id, "fact_id")
        with closing(_connect_ro(self.paths.db)) as connection:
            verify_existing_integrity_or_raise(connection)
            row = connection.execute(
                """
                SELECT value_json, value_hash, canonical_revision, updated_at
                FROM private_memory_facts
                WHERE namespace = ? AND fact_id = ? AND tombstoned_at IS NULL
                """,
                (namespace, fact_id),
            ).fetchone()
        if row is None:
            raise PrivateMemoryStackError("canonical fact not found")
        return {
            "schema": "skeleton.private_memory_stack.exact_get.v1",
            "authoritative": True,
            "authority_classification": "canonical_sqlite",
            "canonical_ref": _canonical_ref(namespace, fact_id),
            "canonical_revision": int(row["canonical_revision"]),
            "value": json.loads(str(row["value_json"])),
            "value_hash": str(row["value_hash"]),
            "updated_at": str(row["updated_at"]),
        }

    def search(self, *, query: str, limit: int = 5) -> dict[str, object]:
        self._require_ready()
        return LocalMemPalaceIndex(self.paths.mempalace).search(query=query, limit=limit)

    def relations(self, *, query: str, limit: int = 5) -> dict[str, object]:
        self._require_ready()
        return LocalGraphifyIndex(self.paths.graphify).query(query=query, limit=limit)

    def rebuild(self) -> dict[str, object]:
        with _exclusive_lock(self.paths.lock):
            return self._rebuild_unlocked()

    def _rebuild_unlocked(self) -> dict[str, object]:
        self._ensure_private_root()
        facts, revision = self._active_facts()
        LocalMemPalaceIndex.rebuild_from_facts(self.paths.mempalace, facts=facts, canonical_revision=revision)
        LocalGraphifyIndex.rebuild_from_facts(self.paths.graphify, facts=facts, canonical_revision=revision)
        return self.status()

    def backup(self, *, snapshot_id: str | None = None) -> dict[str, object]:
        with _exclusive_lock(self.paths.lock):
            return self._backup_unlocked(snapshot_id=snapshot_id)

    def _backup_unlocked(self, *, snapshot_id: str | None = None) -> dict[str, object]:
        self._require_ready(allow_stale=True)
        self.paths.backups.mkdir(parents=True, exist_ok=True)
        _chmod_dir(self.paths.backups)
        manifest = create_snapshot(self.paths.db, self.paths.backups, snapshot_id=snapshot_id)
        _chmod_private_tree(self.paths.backups)
        return {
            "schema": "skeleton.private_memory_stack.backup.v1",
            "status": "DONE",
            "snapshot_id": manifest["snapshot_id"],
            "canonical_revision": manifest["canonical_revision"],
            "aggregate_counts": manifest["aggregate_counts"],
        }

    def import_bundle(
        self,
        basename: str,
        *,
        expected_sha256: str,
        create_backup: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        self._ensure_private_root()
        self._initialize_canonical_database()
        prepared = prepare_private_memory_import_bundle(
            private_root=self.paths.root,
            basename=basename,
            expected_sha256=expected_sha256,
            env=env,
        )
        transaction = f"bundle-{prepared.receipt_id[:32]}"
        with _exclusive_lock(self.paths.lock):
            before = self._database_logical_backup()
            snapshot_temp: Path | None = None
            try:
                existing_receipt = self.store.get_active_fact(
                    namespace=PRIVATE_MEMORY_IMPORT_RECEIPT_NAMESPACE,
                    fact_id=prepared.bundle_id,
                )
                if existing_receipt is not None:
                    existing_hash = existing_receipt.get("bundle_hash") if isinstance(existing_receipt, Mapping) else None
                    if existing_hash == prepared.bundle_hash:
                        moved_name = move_processed_bundle(
                            prepared.source_path,
                            receipt_id=prepared.receipt_id,
                            expected_stat=prepared.source_stat,
                        )
                        return self._bundle_receipt_report(
                            prepared=prepared,
                            status="DONE",
                            idempotency_classification="DUPLICATE_IDENTICAL",
                            processed_receipt_name=moved_name,
                        )
                    raise PrivateMemoryStackError("private memory bundle id already imported with different hash")
                snapshot, snapshot_temp = make_pre_operation_snapshot(
                    self.paths.db,
                    self.paths.root,
                    create_snapshot,
                )
                _verify_pre_operation_snapshot_for_import(self.paths.db, snapshot)
                events = self._put_import_facts_with_provenance_unlocked(
                    prepared,
                    transaction_ref=transaction,
                )
                self._rebuild_unlocked()
                self._verify_import_readback(prepared)
                backup_report = (
                    self._backup_unlocked(snapshot_id=f"bundle-{prepared.receipt_id[:24]}")
                    if create_backup
                    else None
                )
                moved_name = move_processed_bundle(
                    prepared.source_path,
                    receipt_id=prepared.receipt_id,
                    expected_stat=prepared.source_stat,
                )
            except Exception as exc:
                self._restore_database_backup(before)
                self._best_effort_rebuild_unlocked()
                _remove_sqlite_sidecars(self.paths.db)
                raise PrivateMemoryStackError("private memory bundle import rolled back") from exc
            finally:
                cleanup_pre_operation_snapshot(snapshot_temp)
                _remove_backup_file(before)
        return self._bundle_receipt_report(
            prepared=prepared,
            status="DONE",
            idempotency_classification="NEW_IMPORT",
            canonical_revision=int(events[-1]["canonical_revision"]),
            backup=backup_report,
            processed_receipt_name=moved_name,
        )

    def status(self) -> dict[str, object]:
        try:
            with closing(_connect_ro(self.paths.db)) as connection:
                integrity = sanitized_integrity_report(connection)
                revision = current_revision(connection) if integrity["status"] == "DONE" else 0
                active_count = _active_count(connection) if integrity["status"] == "DONE" else 0
            mempalace = LocalMemPalaceIndex.status(self.paths.mempalace, current_canonical_revision=revision)
            graphify = LocalGraphifyIndex.status(self.paths.graphify, current_canonical_revision=revision)
            states = (mempalace["state"], graphify["state"])
            state = "READY" if integrity["status"] == "DONE" and states == ("READY", "READY") else "STALE"
            if integrity["status"] != "DONE" or "BLOCKED" in states:
                state = "BLOCKED"
            return {
                "schema": PRIVATE_MEMORY_STACK_STATUS_SCHEMA,
                "state": state,
                "canonical_sqlite": {
                    "state": "READY" if integrity["status"] == "DONE" else "BLOCKED",
                    "canonical_revision": revision,
                    "active_fact_count": active_count,
                    "event_count": integrity.get("event_count", 0),
                    "tombstone_count": integrity.get("tombstone_count", 0),
                    "wal_enabled": _wal_enabled(self.paths.db),
                },
                "mempalace": mempalace,
                "graphify": graphify,
            }
        except Exception as exc:  # noqa: BLE001 - sanitized status must fail closed.
            return {
                "schema": PRIVATE_MEMORY_STACK_STATUS_SCHEMA,
                "state": "BLOCKED",
                "canonical_sqlite": {
                    "state": "BLOCKED",
                    "canonical_revision": 0,
                    "active_fact_count": 0,
                    "event_count": 0,
                    "tombstone_count": 0,
                    "wal_enabled": False,
                },
                "mempalace": {"state": "BLOCKED", "indexed_canonical_revision": 0, "item_count": 0},
                "graphify": {"state": "BLOCKED", "indexed_canonical_revision": 0, "relationship_count": 0},
                "error_class": type(exc).__name__,
            }

    def import_approved_manifest(self) -> dict[str, object]:
        manifest = _approved_manifest()
        self._import_manifest_through_gateway(manifest)
        existing = self.store.get_active_fact(
            namespace="skeleton.operator_preferences",
            fact_id=FAST_AUTONOMOUS_EXECUTION_KEY,
        )
        if existing == manifest:
            return {"status": "IMPORTED", "idempotency_classification": "DUPLICATE_EXISTING"}
        return self.put(
            namespace="skeleton.operator_preferences",
            fact_id=FAST_AUTONOMOUS_EXECUTION_KEY,
            value=manifest,
            actor_ref="memory-gateway",
            reason_code="approved-manifest-import",
            approval_ref="issue-1194-comment-4846756659",
            transaction_ref="fast-autonomous-execution-v1",
        )

    def _initialize_canonical_database(self) -> None:
        existed = self.paths.db.exists() and self.paths.db.stat().st_size > 0
        if existed:
            with closing(_connect_rw(self.paths.db)) as connection:
                verify_existing_integrity_or_raise(connection)
                enable_wal_if_supported(connection)
            _chmod_file(self.paths.db)
            return
        with closing(sqlite3.connect(str(self.paths.db))) as connection:
            connection.row_factory = sqlite3.Row
            ensure_history_schema(connection)
            enable_wal_if_supported(connection)
            verify_existing_integrity_or_raise(connection)
        _chmod_file(self.paths.db)

    def _import_manifest_through_gateway(self, manifest: Mapping[str, Any]) -> None:
        self.paths.gateway_db.parent.mkdir(parents=True, exist_ok=True)
        skeleton_store = SkeletonMemory(self.paths.gateway_db)
        try:
            gateway = MemoryGateway(
                capability_token(namespaces=("skeleton",)),
                skeleton_memory=skeleton_store,
            )
            gateway.execute(
                {
                    "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                    "namespace": "skeleton",
                    "command": "skeleton.memory.import_canonical_manifest",
                    "payload": {"project_id": "skeleton", "manifest": dict(manifest)},
                }
            )
        finally:
            skeleton_store.connection.close()
        _chmod_file(self.paths.gateway_db)

    def _active_facts(self) -> tuple[list[dict[str, object]], int]:
        with closing(_connect_ro(self.paths.db)) as connection:
            verify_existing_integrity_or_raise(connection)
            revision = current_revision(connection)
            rows = connection.execute(
                """
                SELECT namespace, fact_id, value_json, value_hash, canonical_revision, updated_at
                FROM private_memory_facts
                WHERE tombstoned_at IS NULL
                ORDER BY namespace, fact_id
                """
            ).fetchall()
        facts = [
            {
                "namespace": str(row["namespace"]),
                "fact_id": str(row["fact_id"]),
                "canonical_ref": _canonical_ref(str(row["namespace"]), str(row["fact_id"])),
                "value": json.loads(str(row["value_json"])),
                "value_hash": str(row["value_hash"]),
                "canonical_revision": int(row["canonical_revision"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]
        return facts, revision

    def _database_logical_backup(self) -> Path | None:
        if not self.paths.db.is_file():
            return None
        with closing(_connect_ro(self.paths.db)) as connection:
            verify_existing_integrity_or_raise(connection)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.paths.db.name}.",
            suffix=".rollback.sqlite",
            dir=self.paths.db.parent,
        )
        os.close(fd)
        backup_path = Path(tmp_name)
        try:
            with closing(_connect_ro(self.paths.db)) as source, closing(
                sqlite3.connect(str(backup_path))
            ) as target:
                source.backup(target)
                target.row_factory = sqlite3.Row
                verify_existing_integrity_or_raise(target)
            _chmod_file(backup_path)
            return backup_path
        except Exception:
            _remove_backup_file(backup_path)
            raise

    def _restore_database_backup(self, backup: Path | None) -> None:
        if backup is None:
            return
        replacement = self.paths.db.with_name(f".{self.paths.db.name}.{uuid.uuid4().hex}.restore")
        with closing(sqlite3.connect(str(backup))) as source, closing(sqlite3.connect(str(replacement))) as target:
            source.backup(target)
        os.replace(replacement, self.paths.db)
        _remove_sqlite_sidecars(self.paths.db)
        _chmod_file(self.paths.db)

    def _best_effort_rebuild_unlocked(self) -> None:
        try:
            self._rebuild_unlocked()
        except Exception:
            pass

    def _require_ready(self, *, allow_stale: bool = False) -> None:
        state = self.status()["state"]
        if state == "READY" or (allow_stale and state == "STALE"):
            return
        raise PrivateMemoryStackError("private memory stack is not ready")

    def _ensure_private_root(self) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.backups.mkdir(parents=True, exist_ok=True)
        _chmod_dir(self.paths.root)
        _chmod_dir(self.paths.backups)

    def _index_states(self) -> dict[str, object]:
        status = self.status()
        return {"mempalace": status["mempalace"], "graphify": status["graphify"]}

    def _verify_import_readback(self, prepared: Any) -> None:
        for fact in prepared.facts:
            exact = self.get(namespace=fact["namespace"], fact_id=fact["fact_id"])
            if exact["value_hash"] != fact["value_hash"]:
                raise PrivateMemoryStackError("import read-back value hash mismatch")
        receipt = self.get(namespace=PRIVATE_MEMORY_IMPORT_RECEIPT_NAMESPACE, fact_id=prepared.bundle_id)
        if receipt["value_hash"] != content_hash(prepared.receipt_fact["value"]):
            raise PrivateMemoryStackError("import receipt read-back hash mismatch")

    def _put_import_facts_with_provenance_unlocked(
        self,
        prepared: Any,
        *,
        transaction_ref: str,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        for fact in prepared.facts:
            events.append(
                self.store.put_fact(
                    namespace=fact["namespace"],
                    fact_id=fact["fact_id"],
                    value=fact["value"],
                    actor_ref=fact["actor"],
                    reason_code=fact["reason"],
                    approval_ref=fact["approval"],
                    transaction_ref=transaction_ref,
                    event_type="supersede",
                )
            )
        events.append(
            self.store.put_fact(
                namespace=prepared.receipt_fact["namespace"],
                fact_id=prepared.receipt_fact["fact_id"],
                value=prepared.receipt_fact["value"],
                actor_ref="operator",
                reason_code="operator-approved-bundle-import",
                approval_ref="local-operator",
                transaction_ref=transaction_ref,
                event_type="supersede",
            )
        )
        return events

    def _bundle_receipt_report(
        self,
        *,
        prepared: Any,
        status: str,
        idempotency_classification: str,
        processed_receipt_name: str,
        canonical_revision: int | None = None,
        backup: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        stack_status = self.status()
        return {
            "schema": "skeleton.private_memory_bundle_import_report.v1",
            "status": status,
            "idempotency_classification": idempotency_classification,
            "bundle_id": prepared.bundle_id,
            "bundle_hash": prepared.bundle_hash,
            "file_sha256": prepared.file_sha256,
            "receipt_id": prepared.receipt_id,
            "record_count": len(prepared.facts),
            "canonical_revision": canonical_revision
            if canonical_revision is not None
            else int(stack_status["canonical_sqlite"]["canonical_revision"]),
            "imported_canonical_refs": [
                f"{fact['namespace']}:{fact['fact_id']}" for fact in prepared.facts
            ],
            "processed_receipt_name": processed_receipt_name,
            "backup": backup,
            "indexes": self._index_states(),
        }


def _paths(private_root: str | Path | None) -> PrivateMemoryStackPaths:
    root_value = private_root or os.environ.get(PRIVATE_MEMORY_STACK_ROOT_ENV)
    if root_value is None:
        root_value = Path.home() / ".local" / "share" / "skeleton-private-memory"
    root = Path(root_value).expanduser().resolve()
    return PrivateMemoryStackPaths(
        root=root,
        db=root / "canonical.sqlite",
        graphify=root / "graphify.index.json",
        mempalace=root / "mempalace.index.json",
        backups=root / "backups",
        gateway_db=root / "memory_gateway_import.sqlite",
        lock=root / "private_memory_stack.lock",
    )


def _connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _connect_rw(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=rw", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _canonical_ref(namespace: str, fact_id: str) -> str:
    return f"{safe_token(namespace, 'namespace')}:{safe_token(fact_id, 'fact_id')}"


def _verify_pre_operation_snapshot_for_import(db_path: Path, snapshot: Mapping[str, object]) -> None:
    manifest = snapshot.get("manifest")
    snapshot_path = snapshot.get("snapshot_path")
    if not isinstance(manifest, Mapping) or not isinstance(snapshot_path, Path):
        raise PrivateMemoryStackError("invalid pre-operation snapshot")
    if not snapshot_path.is_file():
        raise PrivateMemoryStackError("pre-operation snapshot unavailable")
    expected_hash = manifest.get("content_hash")
    if not isinstance(expected_hash, str) or bytes_hash(snapshot_path.read_bytes()) != expected_hash:
        raise PrivateMemoryStackError("pre-operation snapshot hash mismatch")
    with closing(_connect_ro(db_path)) as current, closing(_connect_ro(snapshot_path)) as before:
        verify_existing_integrity_or_raise(current)
        verify_existing_integrity_or_raise(before)
        if current_revision(current) != manifest.get("canonical_revision"):
            raise PrivateMemoryStackError("pre-operation snapshot revision mismatch")
        if current_revision(before) != manifest.get("canonical_revision"):
            raise PrivateMemoryStackError("pre-operation snapshot revision mismatch")


def _active_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM private_memory_facts WHERE tombstoned_at IS NULL"
        ).fetchone()[0]
    )


def _wal_enabled(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with closing(_connect_ro(path)) as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
            return row is not None and str(row[0]).lower() == "wal"
    except sqlite3.DatabaseError:
        return False


class _exclusive_lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: object | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _chmod_dir(self.path.parent)
        handle = self.path.open("a+", encoding="utf-8")
        _chmod_file(self.path)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        self._handle = handle

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        handle = self._handle
        if handle is None:
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self._handle = None


def _approved_manifest() -> dict[str, object]:
    if APPROVED_MANIFEST_PATH.is_file():
        return json.loads(APPROVED_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        "schema": "skeleton.canonical_memory_manifest.v1",
        "namespace": "skeleton.operator_preferences",
        "scope": "global_operator_working_style",
        "key": FAST_AUTONOMOUS_EXECUTION_KEY,
        "record_type": "operator_working_style_preference",
        "version": 1,
        "authority": "candidate_manifest_only",
        "privacy_classification": "public_safe_operator_preference",
        "provenance": {
            "kind": "approved_github_issue_comment",
            "repo": "alanua-Skeleton",
            "issue_number": 1194,
            "comment_id": 4846756659,
            "approval_ref": "issue-1194-comment-4846756659",
        },
        "supersession": {"status": "initial", "supersedes": []},
        "record": {
            "preference_summary": (
                "Prefer fast autonomous execution on bounded approved Skeleton tasks while preserving explicit safety gates."
            ),
            "operating_rules": [
                {
                    "id": "rule-fast-autonomous-progress",
                    "category": "fast_autonomous_progress",
                    "statement": (
                        "Work at a fast operational pace and continue through obvious next steps without waiting for repeated confirmation."
                    ),
                },
                {
                    "id": "rule-independent-action",
                    "category": "independent_action",
                    "statement": "Act independently inside already granted authority and established safety boundaries.",
                },
                {
                    "id": "rule-low-procedural-overhead",
                    "category": "low_procedural_overhead",
                    "statement": (
                        "Minimize procedural overhead, repetitive status checking, excessive caution, and unnecessary issue/comment churn."
                    ),
                },
                {
                    "id": "rule-useful-work-over-paperwork",
                    "category": "useful_work_over_paperwork",
                    "statement": "Prefer completing useful work over producing paperwork about the work.",
                },
                {
                    "id": "rule-real-blockers-only",
                    "category": "real_blockers_only",
                    "statement": (
                        "Ask or stop only for a real ambiguity, protected/high-risk approval boundary, unavailable access, or verified blocker."
                    ),
                },
                {
                    "id": "rule-concise-result-updates",
                    "category": "concise_result_updates",
                    "statement": "Keep operator updates short, concrete, and focused on result, blocker, verdict, or next action.",
                },
                {
                    "id": "rule-status-fields",
                    "category": "status_fields",
                    "statement": (
                        "Every operator-facing status must explicitly state both: what will happen next and whether the operator needs to do anything now."
                    ),
                },
                {
                    "id": "rule-read-only-parallelization",
                    "category": "read_only_parallelization",
                    "statement": (
                        "Parallelize safe read-only checks and preparation when that materially speeds delivery; serialize only conflicting or high-risk writes."
                    ),
                },
                {
                    "id": "rule-incremental-memory-readiness",
                    "category": "incremental_memory_readiness",
                    "statement": (
                        "Start using each approved memory layer as soon as that layer is verified ready; do not wait for all memory layers to be complete."
                    ),
                },
                {
                    "id": "rule-sqlite-authority",
                    "category": "sqlite_authority",
                    "statement": "Use canonical SQLite/Memory Gateway for authoritative durable facts as soon as available.",
                },
                {
                    "id": "rule-graphify-relationships",
                    "category": "graphify_relationships",
                    "statement": "Use Graphify for dependency/code relationship recall once its runtime index is verified.",
                },
                {
                    "id": "rule-mempalace-semantic",
                    "category": "mempalace_semantic",
                    "statement": (
                        "Use MemPalace for non-authoritative semantic recall once its runtime profile is verified, while keeping exact confirmation in canonical memory."
                    ),
                },
            ],
        },
        "integrity_hash": "68ea3713f2f3d9bfd80215a986e54525cd20db926a0de109c23bfeeed94fbf04",
    }


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass


def _remove_backup_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _chmod_dir(path: Path) -> None:
    try:
        path.chmod(0o700)
    except PermissionError:
        pass


def _chmod_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except PermissionError:
        pass


def _chmod_private_tree(path: Path) -> None:
    for child in path.rglob("*"):
        if child.is_dir():
            _chmod_dir(child)
        elif child.is_file():
            _chmod_file(child)


def atomic_write_json_private(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
        _chmod_file(tmp)
        os.replace(tmp, path)
        _chmod_file(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def sanitize_cli_report(payload: Mapping[str, Any]) -> dict[str, object]:
    """Return public-safe operational status fields without paths or private values."""
    return json.loads(canonical_json(payload))
