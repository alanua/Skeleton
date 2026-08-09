from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


CONTROL_RECOVERY_SCHEMA = "skeleton.control_recovery.v1"
CONTROL_RECOVERY_RECEIPT_SCHEMA = "skeleton.control_recovery_receipt.v1"
ROUTE_CONTROL_RECOVERY = "control_recovery"


class FailureClass(str, Enum):
    CODEGEN_RUNTIME_UNHEALTHY = "CODEGEN_RUNTIME_UNHEALTHY"
    REGISTERED_CHECKOUT_STALE_OR_DIRTY = "REGISTERED_CHECKOUT_STALE_OR_DIRTY"
    LONG_LIVED_POLLER_STALE = "LONG_LIVED_POLLER_STALE"
    EXECUTOR_SERVICE_NOT_RUNNING = "EXECUTOR_SERVICE_NOT_RUNNING"
    GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY = (
        "GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY"
    )
    QUEUE_LABEL_STATE_STUCK = "QUEUE_LABEL_STATE_STUCK"
    CANARY_FAILED_AFTER_RECOVERY = "CANARY_FAILED_AFTER_RECOVERY"


class RecoveryStatus(str, Enum):
    WAITING_RECOVERY = "WAITING_RECOVERY"
    RETRYING = "RETRYING"
    RECOVERED = "RECOVERED"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"


_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_KNOWN_ACTIONS = frozenset(
    {
        "registered_checkout_recover",
        "registered_checkout_freshness_canary",
        "long_lived_poller_reload",
        "executor_service_preflight",
        "codegen_read_only_canary",
        "queue_reactivate",
        "issue_runner_continue",
    }
)


@dataclass(frozen=True)
class RecoveryPlan:
    failure_class: FailureClass
    failure_key: str
    actions: tuple[str, ...]
    canaries: tuple[str, ...]
    queue_reactivation_action: str | None
    max_attempts: int = 3
    backoff_seconds: int = 60
    requires_codegen: bool = False


@dataclass(frozen=True)
class RecoveryReceipt:
    status: RecoveryStatus
    reason: str
    failure_class: FailureClass | None
    failure_key: str
    attempt: int
    next_retry_at: int | None
    actions_executed: tuple[str, ...]
    canaries_executed: tuple[str, ...]
    evidence_ref: str
    needs_operator_notification: bool = False

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema": CONTROL_RECOVERY_RECEIPT_SCHEMA,
            "status": self.status.value,
            "accepted": self.status is RecoveryStatus.RECOVERED,
            "reason": self.reason,
            "failure_class": None if self.failure_class is None else self.failure_class.value,
            "failure_key": self.failure_key,
            "attempt": self.attempt,
            "next_retry_at": self.next_retry_at,
            "actions_executed": list(self.actions_executed),
            "canaries_executed": list(self.canaries_executed),
            "evidence_ref": self.evidence_ref,
            "needs_operator_notification": self.needs_operator_notification,
            "public_safe": True,
            "external_side_effects_executed": bool(self.actions_executed),
        }


class RecoveryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recovery_runs (
                    failure_key TEXT PRIMARY KEY,
                    failure_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL CHECK(attempt >= 0),
                    next_retry_at INTEGER,
                    needs_operator_emitted INTEGER NOT NULL CHECK(needs_operator_emitted IN (0, 1)),
                    evidence_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
                ) WITHOUT ROWID;
                """
            )

    def run_recovery(
        self,
        *,
        plan: RecoveryPlan,
        now: int,
        action_executor: Callable[[str], str],
        canary_executor: Callable[[str], bool] | None = None,
    ) -> RecoveryReceipt:
        _timestamp(now, "now")
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_runs WHERE failure_key = ?",
                (plan.failure_key,),
            ).fetchone()
            if row is None:
                attempt = 1
                connection.execute(
                    """
                    INSERT INTO recovery_runs(
                        failure_key, failure_class, status, attempt, next_retry_at,
                        needs_operator_emitted, evidence_json, updated_at
                    ) VALUES (?, ?, ?, 1, NULL, 0, ?, ?)
                    """,
                    (
                        plan.failure_key,
                        plan.failure_class.value,
                        RecoveryStatus.RETRYING.value,
                        "{}",
                        now,
                    ),
                )
            else:
                status = RecoveryStatus(str(row["status"]))
                if status is RecoveryStatus.RECOVERED:
                    connection.commit()
                    return self._receipt_from_row(row, "RECOVERY_ALREADY_DONE")
                if status is RecoveryStatus.NEEDS_OPERATOR:
                    connection.commit()
                    return self._receipt_from_row(row, "RECOVERY_NEEDS_OPERATOR_DURABLE")
                next_retry_at = row["next_retry_at"]
                if next_retry_at is not None and now < int(next_retry_at):
                    connection.commit()
                    return self._receipt_from_row(row, "RECOVERY_BACKOFF_ACTIVE")
                attempt = int(row["attempt"]) + 1
                if attempt > plan.max_attempts:
                    receipt = self._mark_needs_operator(
                        connection,
                        plan=plan,
                        now=now,
                        attempt=int(row["attempt"]),
                        reason="RECOVERY_ATTEMPTS_EXHAUSTED",
                        notify=not bool(row["needs_operator_emitted"]),
                        evidence={"actions": [], "canaries": []},
                    )
                    connection.commit()
                    return receipt
                connection.execute(
                    """
                    UPDATE recovery_runs
                       SET status = ?, attempt = ?, next_retry_at = NULL, updated_at = ?
                     WHERE failure_key = ?
                    """,
                    (RecoveryStatus.RETRYING.value, attempt, now, plan.failure_key),
                )
            connection.commit()

        actions: list[str] = []
        canaries: list[str] = []
        reason = "RECOVERY_VERIFIED"
        try:
            for action in plan.actions:
                _registered_action(action)
                report = action_executor(action)
                actions.append(action)
                if not _maintenance_report_done(report):
                    reason = "RECOVERY_ACTION_FAILED"
                    raise RuntimeError(reason)
            checker = canary_executor or (lambda _canary: True)
            for canary in plan.canaries:
                _registered_action(canary)
                if not checker(canary):
                    reason = "CANARY_FAILED_AFTER_RECOVERY"
                    raise RuntimeError(reason)
                canaries.append(canary)
            if plan.queue_reactivation_action is not None:
                _registered_action(plan.queue_reactivation_action)
                report = action_executor(plan.queue_reactivation_action)
                actions.append(plan.queue_reactivation_action)
                if not _maintenance_report_done(report):
                    reason = "QUEUE_REACTIVATION_FAILED"
                    raise RuntimeError(reason)
        except Exception:
            next_retry = now + plan.backoff_seconds * attempt
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT needs_operator_emitted FROM recovery_runs WHERE failure_key = ?",
                    (plan.failure_key,),
                ).fetchone()
                notify = not bool(row["needs_operator_emitted"]) if row else True
                if attempt >= plan.max_attempts:
                    receipt = self._mark_needs_operator(
                        connection,
                        plan=plan,
                        now=now,
                        attempt=attempt,
                        reason=reason,
                        notify=notify,
                        evidence={"actions": actions, "canaries": canaries},
                    )
                else:
                    evidence = {"actions": actions, "canaries": canaries, "reason": reason}
                    receipt = self._update(
                        connection,
                        plan=plan,
                        status=RecoveryStatus.WAITING_RECOVERY,
                        attempt=attempt,
                        next_retry_at=next_retry,
                        reason=reason,
                        now=now,
                        evidence=evidence,
                    )
                connection.commit()
                return receipt

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = self._update(
                connection,
                plan=plan,
                status=RecoveryStatus.RECOVERED,
                attempt=attempt,
                next_retry_at=None,
                reason="RECOVERY_VERIFIED",
                now=now,
                evidence={"actions": actions, "canaries": canaries},
            )
            connection.commit()
            return receipt

    def record_needs_operator(
        self, *, failure_key: str, reason: str, now: int
    ) -> RecoveryReceipt:
        _safe_failure_key = _failure_key(failure_key)
        _timestamp(now, "now")
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_runs WHERE failure_key = ?",
                (_safe_failure_key,),
            ).fetchone()
            notify = row is None or not bool(row["needs_operator_emitted"])
            evidence = {
                "failure_key": _safe_failure_key,
                "status": RecoveryStatus.NEEDS_OPERATOR.value,
                "reason": reason,
                "evidence": {"actions": [], "canaries": []},
            }
            evidence_ref = _evidence_ref(evidence)
            evidence_json = json.dumps(
                {**evidence, "evidence_ref": evidence_ref},
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO recovery_runs(
                    failure_key, failure_class, status, attempt, next_retry_at,
                    needs_operator_emitted, evidence_json, updated_at
                ) VALUES (?, ?, ?, 0, NULL, 1, ?, ?)
                ON CONFLICT(failure_key) DO UPDATE SET
                    status = excluded.status,
                    needs_operator_emitted = 1,
                    evidence_json = excluded.evidence_json,
                    updated_at = excluded.updated_at
                """,
                (
                    _safe_failure_key,
                    "UNKNOWN_UNSAFE_RECOVERY",
                    RecoveryStatus.NEEDS_OPERATOR.value,
                    evidence_json,
                    now,
                ),
            )
            connection.commit()
        return RecoveryReceipt(
            status=RecoveryStatus.NEEDS_OPERATOR,
            reason=reason,
            failure_class=None,
            failure_key=_safe_failure_key,
            attempt=0,
            next_retry_at=None,
            actions_executed=(),
            canaries_executed=(),
            evidence_ref=evidence_ref,
            needs_operator_notification=notify,
        )

    def _mark_needs_operator(
        self,
        connection: sqlite3.Connection,
        *,
        plan: RecoveryPlan,
        now: int,
        attempt: int,
        reason: str,
        notify: bool,
        evidence: Mapping[str, Any],
    ) -> RecoveryReceipt:
        receipt = self._update(
            connection,
            plan=plan,
            status=RecoveryStatus.NEEDS_OPERATOR,
            attempt=attempt,
            next_retry_at=None,
            reason=reason,
            now=now,
            evidence=evidence,
            needs_operator_emitted=True,
        )
        return RecoveryReceipt(
            **{**receipt.__dict__, "needs_operator_notification": notify}
        )

    def _update(
        self,
        connection: sqlite3.Connection,
        *,
        plan: RecoveryPlan,
        status: RecoveryStatus,
        attempt: int,
        next_retry_at: int | None,
        reason: str,
        now: int,
        evidence: Mapping[str, Any],
        needs_operator_emitted: bool | None = None,
    ) -> RecoveryReceipt:
        payload = {
            "failure_class": plan.failure_class.value,
            "failure_key": plan.failure_key,
            "status": status.value,
            "attempt": attempt,
            "reason": reason,
            "evidence": dict(evidence),
        }
        evidence_ref = _evidence_ref(payload)
        evidence_json = json.dumps(
            {**payload, "evidence_ref": evidence_ref},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        emitted_sql = (
            "needs_operator_emitted"
            if needs_operator_emitted is None
            else str(int(needs_operator_emitted))
        )
        connection.execute(
            f"""
            UPDATE recovery_runs
               SET failure_class = ?, status = ?, attempt = ?, next_retry_at = ?,
                   evidence_json = ?, updated_at = ?,
                   needs_operator_emitted = {emitted_sql}
             WHERE failure_key = ?
            """,
            (
                plan.failure_class.value,
                status.value,
                attempt,
                next_retry_at,
                evidence_json,
                now,
                plan.failure_key,
            ),
        )
        return RecoveryReceipt(
            status=status,
            reason=reason,
            failure_class=plan.failure_class,
            failure_key=plan.failure_key,
            attempt=attempt,
            next_retry_at=next_retry_at,
            actions_executed=tuple(str(item) for item in evidence.get("actions", ())),
            canaries_executed=tuple(str(item) for item in evidence.get("canaries", ())),
            evidence_ref=evidence_ref,
        )

    def _receipt_from_row(self, row: sqlite3.Row, reason: str) -> RecoveryReceipt:
        evidence = json.loads(str(row["evidence_json"]))
        details = evidence.get("evidence")
        if not isinstance(details, Mapping):
            details = {}
        return RecoveryReceipt(
            status=RecoveryStatus(str(row["status"])),
            reason=reason,
            failure_class=FailureClass(str(row["failure_class"])),
            failure_key=str(row["failure_key"]),
            attempt=int(row["attempt"]),
            next_retry_at=None if row["next_retry_at"] is None else int(row["next_retry_at"]),
            actions_executed=tuple(str(item) for item in details.get("actions", ())),
            canaries_executed=tuple(str(item) for item in details.get("canaries", ())),
            evidence_ref=str(evidence.get("evidence_ref") or _evidence_ref(evidence)),
            needs_operator_notification=False,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def classify_failure(packet: Mapping[str, Any]) -> FailureClass | None:
    value = packet.get("failure_class")
    if isinstance(value, str):
        try:
            return FailureClass(value)
        except ValueError:
            return None
    status = str(packet.get("status") or packet.get("reason") or "")
    if "checkout" in status.lower() and any(
        marker in status.lower() for marker in ("stale", "dirty", "behind", "diverged")
    ):
        return FailureClass.REGISTERED_CHECKOUT_STALE_OR_DIRTY
    if "poller" in status.lower() and "stale" in status.lower():
        return FailureClass.LONG_LIVED_POLLER_STALE
    if "github actions" in status.lower() and "issue-runner healthy" in status.lower():
        return FailureClass.GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY
    if "codegen" in status.lower() or "codex" in status.lower():
        return FailureClass.CODEGEN_RUNTIME_UNHEALTHY
    return None


def build_recovery_plan(packet: Mapping[str, Any]) -> RecoveryPlan | None:
    failure_class = classify_failure(packet)
    raw_key = packet.get("failure_key") or (
        "control:" + (failure_class.value if failure_class is not None else "UNKNOWN")
    )
    if failure_class is None or not isinstance(raw_key, str) or not _SAFE_KEY_RE.fullmatch(raw_key):
        return None
    max_attempts = _bounded_positive_int(packet.get("max_attempts"), default=3, maximum=5)
    backoff = _bounded_positive_int(packet.get("backoff_seconds"), default=60, maximum=3600)
    plans: dict[FailureClass, tuple[tuple[str, ...], tuple[str, ...], str | None]] = {
        FailureClass.CODEGEN_RUNTIME_UNHEALTHY: (
            ("executor_service_preflight",),
            ("codegen_read_only_canary",),
            "queue_reactivate",
        ),
        FailureClass.REGISTERED_CHECKOUT_STALE_OR_DIRTY: (
            ("registered_checkout_recover",),
            ("registered_checkout_freshness_canary",),
            "queue_reactivate",
        ),
        FailureClass.LONG_LIVED_POLLER_STALE: (
            ("long_lived_poller_reload",),
            ("registered_checkout_freshness_canary",),
            "queue_reactivate",
        ),
        FailureClass.EXECUTOR_SERVICE_NOT_RUNNING: (
            ("executor_service_preflight",),
            ("codegen_read_only_canary",),
            "queue_reactivate",
        ),
        FailureClass.GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY: (
            ("issue_runner_continue",),
            ("codegen_read_only_canary",),
            "queue_reactivate",
        ),
        FailureClass.QUEUE_LABEL_STATE_STUCK: (
            ("queue_reactivate",),
            ("registered_checkout_freshness_canary",),
            None,
        ),
        FailureClass.CANARY_FAILED_AFTER_RECOVERY: (
            (),
            (),
            None,
        ),
    }
    actions, canaries, queue_action = plans[failure_class]
    return RecoveryPlan(
        failure_class=failure_class,
        failure_key=raw_key,
        actions=actions,
        canaries=canaries,
        queue_reactivation_action=queue_action,
        max_attempts=max_attempts,
        backoff_seconds=backoff,
        requires_codegen=False,
    )


def execute_recovery_packet(
    packet: Mapping[str, Any],
    *,
    store: RecoveryStore,
    now: int,
    action_executor: Callable[[str], str],
    canary_executor: Callable[[str], bool] | None = None,
) -> Mapping[str, Any]:
    if packet.get("schema") not in {None, CONTROL_RECOVERY_SCHEMA}:
        return store.record_needs_operator(
            failure_key=_packet_failure_key(packet),
            reason="SCHEMA_MISMATCH",
            now=now,
        ).as_mapping()
    if _payload_attempts_to_broaden_authority(packet):
        return store.record_needs_operator(
            failure_key=_packet_failure_key(packet),
            reason="UNREGISTERED_RECOVERY_AUTHORITY",
            now=now,
        ).as_mapping()
    plan = build_recovery_plan(packet)
    if plan is None:
        return store.record_needs_operator(
            failure_key=_packet_failure_key(packet),
            reason="UNKNOWN_UNSAFE_RECOVERY",
            now=now,
        ).as_mapping()
    receipt = store.run_recovery(
        plan=plan,
        now=now,
        action_executor=action_executor,
        canary_executor=canary_executor,
    )
    return receipt.as_mapping()


def _packet_failure_key(packet: Mapping[str, Any]) -> str:
    value = packet.get("failure_key")
    return value if isinstance(value, str) and _SAFE_KEY_RE.fullmatch(value) else "control:UNKNOWN"


def _failure_key(value: str) -> str:
    if not isinstance(value, str) or _SAFE_KEY_RE.fullmatch(value) is None:
        return "control:UNKNOWN"
    return value


def _payload_attempts_to_broaden_authority(packet: Mapping[str, Any]) -> bool:
    forbidden = {
        "command",
        "commands",
        "path",
        "package",
        "packages",
        "version",
        "model",
        "service",
        "script",
        "shell",
        "protected_merge",
        "new_authority",
    }
    if forbidden & set(packet):
        return True
    for key in ("actions", "canaries"):
        value = packet.get(key)
        if value is not None:
            if not isinstance(value, list) or any(item not in _KNOWN_ACTIONS for item in value):
                return True
    return False


def _maintenance_report_done(report: str) -> bool:
    return str(report).lstrip().startswith("DONE:") and "success_criteria=not_met" not in str(report)


def _registered_action(action: str) -> str:
    if not isinstance(action, str) or action not in _KNOWN_ACTIONS or not _SAFE_TOKEN_RE.fullmatch(action):
        raise ValueError("REGISTERED_ACTION_NOT_ALLOWLISTED")
    return action


def _bounded_positive_int(value: object, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        return default
    return value


def _timestamp(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


def _evidence_ref(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "control-recovery:" + hashlib.sha256(encoded).hexdigest()
