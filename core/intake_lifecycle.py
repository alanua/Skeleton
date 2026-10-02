from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final


INTAKE_LIFECYCLE_SCHEMA: Final = "skeleton.intake_lifecycle_item.v1"
INTAKE_LIFECYCLE_RECEIPT_SCHEMA: Final = "skeleton.intake_lifecycle_public_receipt.v1"
INTAKE_RECONCILIATION_RECEIPT_SCHEMA: Final = "skeleton.intake_lifecycle_reconciliation_receipt.v1"
SHARED_PENDING_LIFECYCLE_WORK_SCHEMA: Final = "skeleton.shared_pending_lifecycle_work.v1"

INTAKE_STATES: Final = frozenset(
    {"RECEIVED", "PRESERVED", "CLASSIFIED", "INDEXED", "PROCESSING", "DONE", "BLOCKED", "DEFERRED"}
)
NONTERMINAL_STATES: Final = INTAKE_STATES - {"DONE"}
ACTIVE_RESUME_STATES: Final = frozenset({"RECEIVED", "PRESERVED", "CLASSIFIED", "INDEXED", "PROCESSING"})
INTAKE_TRANSITION_POLICY: Final = {
    "RECEIVED": frozenset({"RECEIVED", "PRESERVED", "CLASSIFIED", "INDEXED", "PROCESSING", "DONE", "BLOCKED", "DEFERRED"}),
    "PRESERVED": frozenset({"PRESERVED", "CLASSIFIED", "INDEXED", "PROCESSING", "DONE", "BLOCKED", "DEFERRED"}),
    "CLASSIFIED": frozenset({"CLASSIFIED", "INDEXED", "PROCESSING", "DONE", "BLOCKED", "DEFERRED"}),
    "INDEXED": frozenset({"INDEXED", "PROCESSING", "DONE", "BLOCKED", "DEFERRED"}),
    "PROCESSING": frozenset({"PROCESSING", "DONE", "BLOCKED", "DEFERRED"}),
    "DEFERRED": frozenset({"DEFERRED", *ACTIVE_RESUME_STATES, "DONE", "BLOCKED"}),
    "BLOCKED": frozenset({"BLOCKED", *ACTIVE_RESUME_STATES, "DONE", "DEFERRED"}),
    "DONE": frozenset({"DONE"}),
}


class IntakeLifecycleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LifecycleArtifact:
    artifact_ref: str
    artifact_sha256: str
    kind: str = "original"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LifecycleArtifact":
        return cls(
            artifact_ref=_public_ref(value.get("artifact_ref"), "artifact_ref"),
            artifact_sha256=_sha(value.get("artifact_sha256"), "artifact_sha256"),
            kind=_token(value.get("kind", "original"), "kind"),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "artifact_ref": self.artifact_ref,
            "artifact_sha256": self.artifact_sha256,
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class LifecycleItem:
    intake_id: str
    item_kind: str
    source_ref_hash: str
    source_hash: str
    state: str
    blocker_reason: str
    next_action: str
    provenance_refs: tuple[str, ...]
    artifact_refs: tuple[LifecycleArtifact, ...]
    canonical_ref: str | None
    record_ref: str | None
    branch_ref: str
    updated_at: int

    def public_mapping(self) -> dict[str, Any]:
        return {
            "schema": INTAKE_LIFECYCLE_SCHEMA,
            "intake_id": self.intake_id,
            "item_kind": self.item_kind,
            "source_ref_hash": self.source_ref_hash,
            "source_hash": self.source_hash,
            "state": self.state,
            "blocker_reason": self.blocker_reason,
            "next_action": self.next_action,
            "provenance_refs": list(self.provenance_refs),
            "artifact_count": len(self.artifact_refs),
            "artifact_hash": _stable_hash([item.to_mapping() for item in self.artifact_refs]),
            "canonical_ref": self.canonical_ref,
            "record_ref": self.record_ref,
            "branch_ref": self.branch_ref,
            "updated_at": self.updated_at,
            "public_safe": True,
            "private_payloads_included": False,
        }


class IntakeLifecycleStore:
    """Operational lifecycle index embedded in the caller's existing state DB."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS intake_lifecycle (
                    intake_id TEXT PRIMARY KEY,
                    item_kind TEXT NOT NULL,
                    source_ref_hash TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    blocker_reason TEXT NOT NULL,
                    next_action TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    canonical_ref TEXT,
                    record_ref TEXT,
                    branch_ref TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_intake_lifecycle_pending ON intake_lifecycle(state, updated_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_intake_lifecycle_source ON intake_lifecycle(source_hash, item_kind)"
            )
        try:
            self.db_path.chmod(0o600)
        except FileNotFoundError:
            pass

    def record(
        self,
        *,
        item_kind: str,
        source_ref: str,
        source_hash: str,
        state: str,
        blocker_reason: str,
        next_action: str,
        provenance_refs: Sequence[str] = (),
        artifact_refs: Sequence[Mapping[str, Any] | LifecycleArtifact] = (),
        canonical_ref: str | None = None,
        record_ref: str | None = None,
        branch_ref: str = "shared",
        now: int | None = None,
    ) -> LifecycleItem:
        self.initialize()
        timestamp = int(time.time()) if now is None else int(now)
        item = _build_item(
            item_kind=item_kind,
            source_ref=source_ref,
            source_hash=source_hash,
            state=state,
            blocker_reason=blocker_reason,
            next_action=next_action,
            provenance_refs=provenance_refs,
            artifact_refs=artifact_refs,
            canonical_ref=canonical_ref,
            record_ref=record_ref,
            branch_ref=branch_ref,
            updated_at=timestamp,
        )
        provenance_json = _canonical(list(item.provenance_refs))
        artifact_json = _canonical([artifact.to_mapping() for artifact in item.artifact_refs])
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, provenance_json, artifact_json FROM intake_lifecycle WHERE intake_id = ?",
                (item.intake_id,),
            ).fetchone()
            if row is not None:
                current_state = str(row["state"])
                merged_state = _merged_state(str(row["state"]), item.state)
                blocker_reason = item.blocker_reason
                next_action = item.next_action
                if merged_state == current_state and current_state in {"BLOCKED", "DEFERRED"} and item.state != "DONE":
                    blocker_reason = str(
                        connection.execute(
                            "SELECT blocker_reason FROM intake_lifecycle WHERE intake_id = ?",
                            (item.intake_id,),
                        ).fetchone()["blocker_reason"]
                    )
                    next_action = str(
                        connection.execute(
                            "SELECT next_action FROM intake_lifecycle WHERE intake_id = ?",
                            (item.intake_id,),
                        ).fetchone()["next_action"]
                    )
                provenance_json = _merge_json_arrays(str(row["provenance_json"]), provenance_json)
                artifact_json = _merge_artifacts(str(row["artifact_json"]), artifact_json)
                connection.execute(
                    """
                    UPDATE intake_lifecycle
                       SET item_kind = ?, source_ref_hash = ?, source_hash = ?, state = ?,
                           blocker_reason = ?, next_action = ?, provenance_json = ?,
                           artifact_json = ?, canonical_ref = COALESCE(?, canonical_ref),
                           record_ref = COALESCE(?, record_ref), branch_ref = ?,
                           updated_at = ?
                     WHERE intake_id = ?
                    """,
                    (
                        item.item_kind,
                        item.source_ref_hash,
                        item.source_hash,
                        merged_state,
                        blocker_reason,
                        next_action,
                        provenance_json,
                        artifact_json,
                        item.canonical_ref,
                        item.record_ref,
                        item.branch_ref,
                        item.updated_at,
                        item.intake_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO intake_lifecycle(
                        intake_id, item_kind, source_ref_hash, source_hash, state,
                        blocker_reason, next_action, provenance_json, artifact_json,
                        canonical_ref, record_ref, branch_ref, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.intake_id,
                        item.item_kind,
                        item.source_ref_hash,
                        item.source_hash,
                        item.state,
                        item.blocker_reason,
                        item.next_action,
                        provenance_json,
                        artifact_json,
                        item.canonical_ref,
                        item.record_ref,
                        item.branch_ref,
                        item.updated_at,
                        item.updated_at,
                    ),
                )
        return self.get(item.intake_id) or item

    def update_existing(
        self,
        intake_id: str,
        *,
        state: str,
        blocker_reason: str,
        next_action: str,
        now: int | None = None,
    ) -> LifecycleItem:
        self.initialize()
        timestamp = int(time.time()) if now is None else int(now)
        item_id = _token(intake_id, "intake_id")
        state = _state(state)
        reason = _token(blocker_reason, "blocker_reason")
        action = _token(next_action, "next_action")
        if state in NONTERMINAL_STATES and (reason == "NONE" or action == "none"):
            raise IntakeLifecycleError("nonterminal items require blocker_reason and next_action")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM intake_lifecycle WHERE intake_id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise IntakeLifecycleError("intake item missing")
            merged_state = _merged_state(str(row["state"]), state)
            connection.execute(
                """
                UPDATE intake_lifecycle
                   SET state = ?, blocker_reason = ?, next_action = ?, updated_at = ?
                 WHERE intake_id = ?
                """,
                (merged_state, reason, action, timestamp, item_id),
            )
        result = self.get(item_id)
        if result is None:
            raise IntakeLifecycleError("intake item missing")
        return result

    def get(self, intake_id: str) -> LifecycleItem | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM intake_lifecycle WHERE intake_id = ?",
                (_token(intake_id, "intake_id"),),
            ).fetchone()
        return None if row is None else _row_item(row)

    def pending_work(self, *, item_kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.initialize()
        params: list[Any] = []
        predicate = "state != 'DONE'"
        if item_kind is not None:
            predicate += " AND item_kind = ?"
            params.append(_token(item_kind, "item_kind"))
        params.append(_limit(limit))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                  FROM intake_lifecycle
                 WHERE {predicate}
                 ORDER BY updated_at, intake_id
                 LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [_row_item(row).public_mapping() for row in rows]

    def public_receipt(self) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM intake_lifecycle GROUP BY state"
            ).fetchall()
        counts = {str(row["state"]): int(row["count"]) for row in rows}
        return {
            "schema": INTAKE_LIFECYCLE_RECEIPT_SCHEMA,
            "state_counts": {state: counts.get(state, 0) for state in sorted(INTAKE_STATES)},
            "pending_count": sum(count for state, count in counts.items() if state != "DONE"),
            "public_safe": True,
            "private_payloads_included": False,
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def stable_intake_id(*, item_kind: str, source_ref: str, source_hash: str) -> str:
    payload = {"item_kind": _token(item_kind, "item_kind"), "source_ref": source_ref, "source_hash": _sha(source_hash, "source_hash")}
    return f"intake:{hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()[:32]}"


def reconcile_lifecycle(
    store: IntakeLifecycleStore,
    *,
    observed_artifacts: Sequence[Mapping[str, Any] | LifecycleArtifact] = (),
    known_source_hashes: Sequence[str] = (),
    missing_refs: Sequence[str] = (),
    failed_index_refs: Sequence[str] = (),
    stalled_before: int | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    timestamp = int(time.time()) if now is None else int(now)
    known_hashes = {_sha(value, "known_source_hash") for value in known_source_hashes}
    orphaned = missing = failed = stalled = 0
    for artifact_value in observed_artifacts:
        artifact = artifact_value if isinstance(artifact_value, LifecycleArtifact) else LifecycleArtifact.from_mapping(artifact_value)
        if artifact.artifact_sha256 in known_hashes:
            continue
        store.record(
            item_kind="document",
            source_ref=artifact.artifact_ref,
            source_hash=artifact.artifact_sha256,
            state="BLOCKED",
            blocker_reason="ORPHANED_ARTIFACT",
            next_action="operator_reconcile_or_import_original_artifact",
            provenance_refs=(artifact.artifact_ref,),
            artifact_refs=(artifact,),
            now=timestamp,
        )
        orphaned += 1
    for ref in missing_refs:
        digest = hashlib.sha256(_public_ref(ref, "missing_ref").encode("utf-8")).hexdigest()
        store.record(
            item_kind="document",
            source_ref=ref,
            source_hash=digest,
            state="BLOCKED",
            blocker_reason="MISSING_ORIGINAL_REF",
            next_action="restore_original_artifact_or_mark_loss_with_operator_approval",
            provenance_refs=(ref,),
            now=timestamp,
        )
        missing += 1
    for ref in failed_index_refs:
        digest = hashlib.sha256(_public_ref(ref, "failed_index_ref").encode("utf-8")).hexdigest()
        store.record(
            item_kind="document",
            source_ref=ref,
            source_hash=digest,
            state="BLOCKED",
            blocker_reason="FAILED_INDEXING",
            next_action="retry_memory_indexing_from_preserved_original",
            provenance_refs=(ref,),
            canonical_ref=ref,
            now=timestamp,
        )
        failed += 1
    if stalled_before is not None:
        for item in store.pending_work():
            if int(item["updated_at"]) >= stalled_before or item["state"] in {"BLOCKED", "DEFERRED"}:
                continue
            store.update_existing(
                str(item["intake_id"]),
                state="DEFERRED",
                blocker_reason="STALLED_ITEM",
                next_action="resume_nonterminal_intake_work",
                now=timestamp,
            )
            stalled += 1
    return {
        "schema": INTAKE_RECONCILIATION_RECEIPT_SCHEMA,
        "status": "DONE",
        "orphaned_artifacts": orphaned,
        "missing_refs": missing,
        "failed_index_refs": failed,
        "stalled_items": stalled,
        "pending_count": store.public_receipt()["pending_count"],
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def shared_pending_lifecycle_work(
    stores: Mapping[str, IntakeLifecycleStore],
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Return a public-safe read-only merge of pending work from existing stores."""

    capped_limit = _limit(limit)
    rows: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for store_ref, store in stores.items():
        source = _token(store_ref, "store_ref")
        pending = store.pending_work(limit=capped_limit)
        source_counts[source] = len(pending)
        for item in pending:
            public_item = dict(item)
            public_item["store_ref"] = source
            rows.append(public_item)
    rows.sort(key=lambda item: (int(item["updated_at"]), str(item["store_ref"]), str(item["intake_id"])))
    rows = rows[:capped_limit]
    state_counts = {state: 0 for state in sorted(INTAKE_STATES)}
    item_kind_counts: dict[str, int] = {}
    for item in rows:
        state = str(item["state"])
        state_counts[state] = state_counts.get(state, 0) + 1
        item_kind = str(item["item_kind"])
        item_kind_counts[item_kind] = item_kind_counts.get(item_kind, 0) + 1
    return {
        "schema": SHARED_PENDING_LIFECYCLE_WORK_SCHEMA,
        "pending_count": len(rows),
        "state_counts": state_counts,
        "item_kind_counts": dict(sorted(item_kind_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "items": rows,
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _build_item(
    *,
    item_kind: str,
    source_ref: str,
    source_hash: str,
    state: str,
    blocker_reason: str,
    next_action: str,
    provenance_refs: Sequence[str],
    artifact_refs: Sequence[Mapping[str, Any] | LifecycleArtifact],
    canonical_ref: str | None,
    record_ref: str | None,
    branch_ref: str,
    updated_at: int,
) -> LifecycleItem:
    state = _state(state)
    reason = _token(blocker_reason, "blocker_reason")
    action = _token(next_action, "next_action")
    if state in NONTERMINAL_STATES and (reason == "NONE" or action == "none"):
        raise IntakeLifecycleError("nonterminal items require blocker_reason and next_action")
    sha = _sha(source_hash, "source_hash")
    artifacts = tuple(
        artifact if isinstance(artifact, LifecycleArtifact) else LifecycleArtifact.from_mapping(artifact)
        for artifact in artifact_refs
    )
    return LifecycleItem(
        intake_id=stable_intake_id(item_kind=item_kind, source_ref=source_ref, source_hash=sha),
        item_kind=_token(item_kind, "item_kind"),
        source_ref_hash=hashlib.sha256(source_ref.encode("utf-8")).hexdigest(),
        source_hash=sha,
        state=state,
        blocker_reason=reason,
        next_action=action,
        provenance_refs=tuple(_public_ref(ref, "provenance_ref") for ref in provenance_refs),
        artifact_refs=artifacts,
        canonical_ref=None if canonical_ref is None else _public_ref(canonical_ref, "canonical_ref"),
        record_ref=None if record_ref is None else _public_ref(record_ref, "record_ref"),
        branch_ref=_token(branch_ref, "branch_ref"),
        updated_at=int(updated_at),
    )


def _row_item(row: sqlite3.Row) -> LifecycleItem:
    return LifecycleItem(
        intake_id=str(row["intake_id"]),
        item_kind=str(row["item_kind"]),
        source_ref_hash=str(row["source_ref_hash"]),
        source_hash=str(row["source_hash"]),
        state=str(row["state"]),
        blocker_reason=str(row["blocker_reason"]),
        next_action=str(row["next_action"]),
        provenance_refs=tuple(json.loads(str(row["provenance_json"]))),
        artifact_refs=tuple(LifecycleArtifact.from_mapping(item) for item in json.loads(str(row["artifact_json"]))),
        canonical_ref=row["canonical_ref"],
        record_ref=row["record_ref"],
        branch_ref=str(row["branch_ref"]),
        updated_at=int(row["updated_at"]),
    )


def _merged_state(current: str, new: str) -> str:
    current = _state(current)
    new = _state(new)
    if new in INTAKE_TRANSITION_POLICY[current]:
        return new
    raise IntakeLifecycleError(f"invalid lifecycle transition: {current} -> {new}")


def _merge_json_arrays(first: str, second: str) -> str:
    merged = []
    for item in [*json.loads(first), *json.loads(second)]:
        if item not in merged:
            merged.append(item)
    return _canonical(merged)


def _merge_artifacts(first: str, second: str) -> str:
    by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for item in [*json.loads(first), *json.loads(second)]:
        artifact = LifecycleArtifact.from_mapping(item)
        by_key[(artifact.artifact_ref, artifact.artifact_sha256)] = artifact.to_mapping()
    return _canonical([by_key[key] for key in sorted(by_key)])


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _state(value: Any) -> str:
    text = _token(value, "state")
    if text not in INTAKE_STATES:
        raise IntakeLifecycleError("invalid lifecycle state")
    return text


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise IntakeLifecycleError(f"{field} must be sha256")
    return value


def _token(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise IntakeLifecycleError(f"{field} must be token")
    text = value.strip()
    if not text or len(text) > 160 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-" for ch in text):
        raise IntakeLifecycleError(f"{field} must be token")
    return text


def _public_ref(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise IntakeLifecycleError(f"{field} must be public ref")
    text = value.strip()
    if (
        not text
        or len(text) > 256
        or text.startswith(("/", "~"))
        or "\\" in text
        or ".." in text
        or any(ch.isspace() for ch in text)
    ):
        raise IntakeLifecycleError(f"{field} must be public ref")
    return text


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 1000:
        raise IntakeLifecycleError("limit invalid")
    return value
