from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
import sqlite3
import stat
from pathlib import Path
from typing import Any


CONTROL_RECOVERY_SCHEMA = "skeleton.control_recovery.v1"
CONTROL_RECOVERY_RECEIPT_SCHEMA = "skeleton.control_recovery_receipt.v1"
ROUTE_CONTROL_RECOVERY = "control_recovery"
CODEGEN_UNKNOWN_VARIANT_MAX_MESSAGE = "failed to decode models response: unknown variant `max`"
PRODUCTION_CONTROL_RECOVERY_DB = Path(
    "/home/agent/.local/state/skeleton-runner/control-recovery/control_recovery.sqlite3"
)


class FailureClass(str, Enum):
    CODEGEN_RUNTIME_UNHEALTHY = "CODEGEN_RUNTIME_UNHEALTHY"
    REGISTERED_CHECKOUT_STALE_OR_DIRTY = "REGISTERED_CHECKOUT_STALE_OR_DIRTY"
    LONG_LIVED_POLLER_STALE = "LONG_LIVED_POLLER_STALE"
    EXECUTOR_SERVICE_NOT_RUNNING = "EXECUTOR_SERVICE_NOT_RUNNING"
    GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY = "GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY"
    QUEUE_LABEL_STATE_STUCK = "QUEUE_LABEL_STATE_STUCK"
    CANARY_FAILED_AFTER_RECOVERY = "CANARY_FAILED_AFTER_RECOVERY"
    QUEUE_IDLE_WITH_ELIGIBLE_WORK = "QUEUE_IDLE_WITH_ELIGIBLE_WORK"
    AMBIGUOUS_MUTATING_RESULT = "AMBIGUOUS_MUTATING_RESULT"


class RecoveryStatus(str, Enum):
    WAITING_RECOVERY = "WAITING_RECOVERY"
    RETRYING = "RETRYING"
    RECOVERED = "RECOVERED"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"


class SafeResponseClass(str, Enum):
    CONTROL_RECOVERY = "CONTROL_RECOVERY"
    QUEUE_REPLENISH = "QUEUE_REPLENISH"
    REPAIR_TASK_REQUIRED = "REPAIR_TASK_REQUIRED"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"


_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAINTENANCE_REASON_RE = re.compile(r"(?m)^reason=([A-Z0-9_]{1,127})$")
_SAFE_CONTEXT_FIELDS = (
    "failure_class",
    "reason_class",
    "task_kind",
    "operation",
    "phase",
    "route_type",
    "route_id",
    "capability",
    "repository",
)
_KNOWN_ACTIONS = frozenset(
    {
        "registered_checkout_recover",
        "registered_checkout_freshness_canary",
        "long_lived_poller_reload",
        "executor_service_preflight",
        "codegen_runtime_recover",
        "codegen_read_only_canary",
        "queue_reactivate",
        "issue_runner_continue",
    }
)
_RESPONSE_PLANS: dict[SafeResponseClass, tuple[tuple[str, ...], tuple[str, ...], str | None]] = {
    SafeResponseClass.CONTROL_RECOVERY: ((), (), None),
    SafeResponseClass.QUEUE_REPLENISH: (("queue_reactivate",), (), None),
    SafeResponseClass.REPAIR_TASK_REQUIRED: ((), (), None),
    SafeResponseClass.NEEDS_OPERATOR: ((), (), None),
}
_SAFE_RESPONSE_REGISTRY: dict[FailureClass, tuple[SafeResponseClass, ...]] = {
    FailureClass.QUEUE_IDLE_WITH_ELIGIBLE_WORK: (SafeResponseClass.QUEUE_REPLENISH,),
    FailureClass.AMBIGUOUS_MUTATING_RESULT: (SafeResponseClass.NEEDS_OPERATOR,),
}


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
    fingerprint: str | None = None
    response_class: SafeResponseClass = SafeResponseClass.CONTROL_RECOVERY
    response_classes: tuple[SafeResponseClass, ...] = ()


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
    response_class: SafeResponseClass | None = None

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
            "response_class": (
                None if self.response_class is None else self.response_class.value
            ),
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

                CREATE TABLE IF NOT EXISTS failure_lessons (
                    fingerprint TEXT PRIMARY KEY,
                    failure_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    preferred_response_class TEXT,
                    next_response_index INTEGER NOT NULL CHECK(next_response_index >= 0),
                    attempts_json TEXT NOT NULL,
                    verification_json TEXT NOT NULL,
                    needs_operator_emitted INTEGER NOT NULL CHECK(needs_operator_emitted IN (0, 1)),
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS failure_learning_metrics (
                    metric TEXT PRIMARY KEY,
                    value INTEGER NOT NULL CHECK(value >= 0)
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
        existing = self._existing_recovery_receipt(plan.failure_key, now)
        if existing is not None:
            return existing
        response_class = plan.response_class
        if plan.fingerprint is not None:
            response_class = self._select_response(plan=plan, now=now)
            plan = _plan_for_response(plan, response_class)
            if response_class in {
                SafeResponseClass.NEEDS_OPERATOR,
                SafeResponseClass.REPAIR_TASK_REQUIRED,
            }:
                return self.record_needs_operator(
                    failure_key=plan.failure_key,
                    reason=(
                        "REPAIR_TASK_REQUIRED"
                        if response_class is SafeResponseClass.REPAIR_TASK_REQUIRED
                        else "SAFE_ADAPTATIONS_EXHAUSTED"
                    ),
                    now=now,
                    fingerprint=plan.fingerprint,
                    failure_class=plan.failure_class,
                )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_runs WHERE failure_key = ?", (plan.failure_key,)
            ).fetchone()
            if row is not None:
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
                    "UPDATE recovery_runs SET status=?, attempt=?, next_retry_at=NULL, updated_at=? WHERE failure_key=?",
                    (RecoveryStatus.RETRYING.value, attempt, now, plan.failure_key),
                )
            else:
                attempt = 1
                connection.execute(
                    "INSERT INTO recovery_runs(failure_key,failure_class,status,attempt,next_retry_at,needs_operator_emitted,evidence_json,updated_at) VALUES(?,?,?,1,NULL,0,?,?)",
                    (plan.failure_key, plan.failure_class.value, RecoveryStatus.RETRYING.value, "{}", now),
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
                    detail = _maintenance_report_reason(report)
                    reason = (
                        f"RECOVERY_ACTION_FAILED_{detail}"
                        if detail is not None
                        else "RECOVERY_ACTION_FAILED"
                    )
                    raise RuntimeError(reason)

            if plan.canaries and canary_executor is None:
                reason = "CANARY_EXECUTOR_REQUIRED"
                raise RuntimeError(reason)
            if canary_executor is not None:
                for canary in plan.canaries:
                    _registered_action(canary)
                    if not canary_executor(canary):
                        reason = "CANARY_FAILED_AFTER_RECOVERY"
                        raise RuntimeError(reason)
                    canaries.append(canary)

            if plan.queue_reactivation_action is not None:
                _registered_action(plan.queue_reactivation_action)
                report = action_executor(plan.queue_reactivation_action)
                actions.append(plan.queue_reactivation_action)
                if not _maintenance_report_done(report):
                    detail = _maintenance_report_reason(report)
                    reason = (
                        f"QUEUE_REACTIVATION_FAILED_{detail}"
                        if detail is not None
                        else "QUEUE_REACTIVATION_FAILED"
                    )
                    raise RuntimeError(reason)
        except Exception:
            next_retry = now + plan.backoff_seconds * attempt
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if plan.fingerprint is not None:
                    self._record_lesson_attempt(
                        connection,
                        plan=plan,
                        response_class=response_class,
                        outcome="failed",
                        reason=reason,
                        evidence_ref=None,
                        now=now,
                    )
                row = connection.execute(
                    "SELECT needs_operator_emitted FROM recovery_runs WHERE failure_key=?",
                    (plan.failure_key,),
                ).fetchone()
                notify = not bool(row["needs_operator_emitted"]) if row else True
                evidence = {"actions": actions, "canaries": canaries, "reason": reason}
                if attempt >= plan.max_attempts:
                    receipt = self._mark_needs_operator(
                        connection,
                        plan=plan,
                        now=now,
                        attempt=attempt,
                        reason=reason,
                        notify=notify,
                        evidence=evidence,
                    )
                else:
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
            if plan.fingerprint is not None:
                self._record_lesson_attempt(
                    connection,
                    plan=plan,
                    response_class=response_class,
                    outcome="verified",
                    reason="RECOVERY_VERIFIED",
                    evidence_ref=receipt.evidence_ref,
                    now=now,
                )
            connection.commit()
            return receipt

    def record_needs_operator(
        self,
        *,
        failure_key: str,
        reason: str,
        now: int,
        fingerprint: str | None = None,
        failure_class: FailureClass | None = None,
    ) -> RecoveryReceipt:
        safe_key = _failure_key(failure_key)
        _timestamp(now, "now")
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if fingerprint is not None and failure_class is not None:
                self._ensure_lesson(connection, fingerprint, failure_class, now)
                lesson = connection.execute(
                    "SELECT needs_operator_emitted FROM failure_lessons WHERE fingerprint=?",
                    (fingerprint,),
                ).fetchone()
                lesson_notify = lesson is None or not bool(lesson["needs_operator_emitted"])
                connection.execute(
                    """
                    UPDATE failure_lessons
                       SET status='NEEDS_OPERATOR',
                           preferred_response_class=?,
                           needs_operator_emitted=1,
                           updated_at=?
                     WHERE fingerprint=?
                    """,
                    (SafeResponseClass.NEEDS_OPERATOR.value, now, fingerprint),
                )
                self._increment_metric(connection, "needs_operator_count")
            else:
                lesson_notify = True
            row = connection.execute(
                "SELECT needs_operator_emitted FROM recovery_runs WHERE failure_key=?", (safe_key,)
            ).fetchone()
            notify = row is None or not bool(row["needs_operator_emitted"])
            payload = {
                "failure_key": safe_key,
                "status": RecoveryStatus.NEEDS_OPERATOR.value,
                "reason": reason,
                "evidence": {"actions": [], "canaries": []},
            }
            evidence_ref = _evidence_ref(payload)
            evidence_json = json.dumps({**payload, "evidence_ref": evidence_ref}, sort_keys=True, separators=(",", ":"))
            connection.execute(
                """
                INSERT INTO recovery_runs(failure_key,failure_class,status,attempt,next_retry_at,needs_operator_emitted,evidence_json,updated_at)
                VALUES(?,?,?,0,NULL,1,?,?)
                ON CONFLICT(failure_key) DO UPDATE SET status=excluded.status,needs_operator_emitted=1,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at
                """,
                (safe_key, "UNKNOWN_UNSAFE_RECOVERY", RecoveryStatus.NEEDS_OPERATOR.value, evidence_json, now),
            )
            connection.commit()
        return RecoveryReceipt(
            RecoveryStatus.NEEDS_OPERATOR,
            reason,
            None,
            safe_key,
            0,
            None,
            (),
            (),
            evidence_ref,
            notify and lesson_notify,
        )

    def learning_metrics(self) -> dict[str, int]:
        self.initialize()
        with self._connect() as connection:
            lessons = connection.execute(
                """
                SELECT
                    COUNT(*) AS seen,
                    COALESCE(SUM(CASE WHEN status='VERIFIED' THEN 1 ELSE 0 END), 0) AS verified,
                    COALESCE(SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END), 0) AS failed,
                    COALESCE(SUM(CASE WHEN status='NEEDS_OPERATOR' THEN 1 ELSE 0 END), 0) AS needs_operator
                  FROM failure_lessons
                """
            ).fetchone()
            metric_rows = connection.execute(
                "SELECT metric, value FROM failure_learning_metrics"
            ).fetchall()
        metrics = {str(row["metric"]): int(row["value"]) for row in metric_rows}
        assert lessons is not None
        return {
            "incident_classes_seen": int(lessons["seen"]),
            "lessons_verified": int(lessons["verified"]),
            "lessons_failed": int(lessons["failed"]),
            "repeats_prevented": int(metrics.get("repeats_prevented", 0)),
            "needs_operator_count": int(
                metrics.get("needs_operator_count", int(lessons["needs_operator"]))
            ),
        }

    def _select_response(self, *, plan: RecoveryPlan, now: int) -> SafeResponseClass:
        assert plan.fingerprint is not None
        responses = plan.response_classes or _registered_responses(plan.failure_class)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_lesson(connection, plan.fingerprint, plan.failure_class, now)
            row = connection.execute(
                "SELECT * FROM failure_lessons WHERE fingerprint=?", (plan.fingerprint,)
            ).fetchone()
            assert row is not None
            status = str(row["status"])
            preferred = row["preferred_response_class"]
            if len(responses) == 1 and status != "VERIFIED":
                connection.commit()
                return responses[0]
            if status == "VERIFIED" and isinstance(preferred, str):
                try:
                    response = SafeResponseClass(preferred)
                except ValueError:
                    response = SafeResponseClass.NEEDS_OPERATOR
                if response in responses:
                    self._increment_metric(connection, "repeats_prevented")
                    connection.commit()
                    return response
            failed = {
                str(item.get("response_class"))
                for item in _safe_json_list(row["attempts_json"])
                if item.get("outcome") == "failed"
            }
            for response in responses:
                if response.value not in failed:
                    connection.commit()
                    return response
            connection.execute(
                """
                UPDATE failure_lessons
                   SET status='FAILED',
                       preferred_response_class=?,
                       next_response_index=?,
                       updated_at=?
                 WHERE fingerprint=?
                """,
                (SafeResponseClass.NEEDS_OPERATOR.value, len(responses), now, plan.fingerprint),
            )
            connection.commit()
            return SafeResponseClass.NEEDS_OPERATOR

    def _existing_recovery_receipt(
        self, failure_key: str, now: int
    ) -> RecoveryReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM recovery_runs WHERE failure_key = ?", (failure_key,)
            ).fetchone()
        if row is None:
            return None
        status = RecoveryStatus(str(row["status"]))
        if status is RecoveryStatus.RECOVERED:
            return self._receipt_from_row(row, "RECOVERY_ALREADY_DONE")
        if status is RecoveryStatus.NEEDS_OPERATOR:
            return self._receipt_from_row(row, "RECOVERY_NEEDS_OPERATOR_DURABLE")
        next_retry_at = row["next_retry_at"]
        if next_retry_at is not None and now < int(next_retry_at):
            return self._receipt_from_row(row, "RECOVERY_BACKOFF_ACTIVE")
        return None

    def _ensure_lesson(
        self,
        connection: sqlite3.Connection,
        fingerprint: str,
        failure_class: FailureClass,
        now: int,
    ) -> None:
        result = connection.execute(
            """
            INSERT OR IGNORE INTO failure_lessons(
                fingerprint, failure_class, status, preferred_response_class,
                next_response_index, attempts_json, verification_json,
                needs_operator_emitted, created_at, updated_at
            ) VALUES(?,?,?,?,0,'[]','{}',0,?,?)
            """,
            (fingerprint, failure_class.value, "OPEN", None, now, now),
        )
        if result.rowcount == 1:
            self._increment_metric(connection, "incident_classes_seen")

    def _record_lesson_attempt(
        self,
        connection: sqlite3.Connection,
        *,
        plan: RecoveryPlan,
        response_class: SafeResponseClass,
        outcome: str,
        reason: str,
        evidence_ref: str | None,
        now: int,
    ) -> None:
        assert plan.fingerprint is not None
        self._ensure_lesson(connection, plan.fingerprint, plan.failure_class, now)
        row = connection.execute(
            "SELECT attempts_json FROM failure_lessons WHERE fingerprint=?",
            (plan.fingerprint,),
        ).fetchone()
        attempts = _safe_json_list("[]" if row is None else str(row["attempts_json"]))
        attempts.append(
            {
                "response_class": response_class.value,
                "outcome": outcome,
                "reason_class": _safe_reason(reason),
                "evidence_ref": evidence_ref,
                "recorded_at": now,
            }
        )
        attempts_json = json.dumps(attempts[-10:], sort_keys=True, separators=(",", ":"))
        if outcome == "verified":
            verification_json = json.dumps(
                {
                    "response_class": response_class.value,
                    "reason_class": _safe_reason(reason),
                    "evidence_ref": evidence_ref,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                UPDATE failure_lessons
                   SET status='VERIFIED',
                       preferred_response_class=?,
                       attempts_json=?,
                       verification_json=?,
                       updated_at=?
                 WHERE fingerprint=?
                """,
                (response_class.value, attempts_json, verification_json, now, plan.fingerprint),
            )
        else:
            failed_count = len(
                {
                    str(item.get("response_class"))
                    for item in attempts
                    if item.get("outcome") == "failed"
                }
            )
            connection.execute(
                """
                UPDATE failure_lessons
                   SET status='FAILED',
                       attempts_json=?,
                       next_response_index=?,
                       updated_at=?
                 WHERE fingerprint=?
                """,
                (attempts_json, failed_count, now, plan.fingerprint),
            )

    @staticmethod
    def _increment_metric(connection: sqlite3.Connection, metric: str) -> None:
        connection.execute(
            """
            INSERT INTO failure_learning_metrics(metric, value) VALUES(?, 1)
            ON CONFLICT(metric) DO UPDATE SET value=value+1
            """,
            (metric,),
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
        return RecoveryReceipt(**{**receipt.__dict__, "needs_operator_notification": notify})

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
            "response_class": plan.response_class.value,
            "evidence": dict(evidence),
        }
        evidence_ref = _evidence_ref(payload)
        evidence_json = json.dumps({**payload, "evidence_ref": evidence_ref}, sort_keys=True, separators=(",", ":"))
        if needs_operator_emitted is None:
            connection.execute(
                "UPDATE recovery_runs SET failure_class=?,status=?,attempt=?,next_retry_at=?,evidence_json=?,updated_at=? WHERE failure_key=?",
                (plan.failure_class.value, status.value, attempt, next_retry_at, evidence_json, now, plan.failure_key),
            )
        else:
            connection.execute(
                "UPDATE recovery_runs SET failure_class=?,status=?,attempt=?,next_retry_at=?,evidence_json=?,updated_at=?,needs_operator_emitted=? WHERE failure_key=?",
                (plan.failure_class.value, status.value, attempt, next_retry_at, evidence_json, now, int(needs_operator_emitted), plan.failure_key),
            )
        return RecoveryReceipt(
            status,
            reason,
            plan.failure_class,
            plan.failure_key,
            attempt,
            next_retry_at,
            tuple(str(x) for x in evidence.get("actions", ())),
            tuple(str(x) for x in evidence.get("canaries", ())),
            evidence_ref,
            response_class=plan.response_class,
        )

    def _receipt_from_row(self, row: sqlite3.Row, reason: str) -> RecoveryReceipt:
        evidence = json.loads(str(row["evidence_json"]))
        details = evidence.get("evidence") if isinstance(evidence.get("evidence"), Mapping) else {}
        failure_class = None
        try:
            failure_class = FailureClass(str(row["failure_class"]))
        except ValueError:
            pass
        return RecoveryReceipt(
            RecoveryStatus(str(row["status"])),
            reason,
            failure_class,
            str(row["failure_key"]),
            int(row["attempt"]),
            None if row["next_retry_at"] is None else int(row["next_retry_at"]),
            tuple(str(x) for x in details.get("actions", ())),
            tuple(str(x) for x in details.get("canaries", ())),
            str(evidence.get("evidence_ref") or _evidence_ref(evidence)),
            False,
            (
                SafeResponseClass(str(evidence["response_class"]))
                if isinstance(evidence.get("response_class"), str)
                and str(evidence["response_class"]) in {item.value for item in SafeResponseClass}
                else None
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection


def classify_failure(packet: Mapping[str, Any]) -> FailureClass | None:
    value = packet.get("failure_class")
    if isinstance(value, str):
        try:
            return FailureClass(value)
        except ValueError:
            return None
    if _packet_has_codegen_unknown_variant_max(packet):
        return FailureClass.CODEGEN_RUNTIME_UNHEALTHY
    text = str(packet.get("status") or packet.get("reason") or "").lower()
    if "checkout" in text and any(marker in text for marker in ("stale", "dirty", "behind", "diverged")):
        return FailureClass.REGISTERED_CHECKOUT_STALE_OR_DIRTY
    if "poller" in text and "stale" in text:
        return FailureClass.LONG_LIVED_POLLER_STALE
    if "github actions" in text and "issue-runner healthy" in text:
        return FailureClass.GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY
    if "queue" in text and "idle" in text:
        return FailureClass.QUEUE_IDLE_WITH_ELIGIBLE_WORK
    if "ambiguous" in text and "mutating" in text:
        return FailureClass.AMBIGUOUS_MUTATING_RESULT
    return None


def is_codegen_unknown_variant_max_failure(output: str, exit_code: int) -> bool:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code == 0:
        return False
    return CODEGEN_UNKNOWN_VARIANT_MAX_MESSAGE in str(output or "")


def production_control_recovery_db_path() -> Path:
    return PRODUCTION_CONTROL_RECOVERY_DB


def _packet_has_codegen_unknown_variant_max(packet: Mapping[str, Any]) -> bool:
    for key in ("output", "stderr", "status", "reason", "message"):
        value = packet.get(key)
        if isinstance(value, str) and CODEGEN_UNKNOWN_VARIANT_MAX_MESSAGE in value:
            return True
    return False


def _validated_agent_state_db_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise ValueError("STATE_PATH_NOT_ABSOLUTE")
    parent = path.parent
    if parent.exists():
        parent_stat = parent.stat()
        if stat.S_IMODE(parent_stat.st_mode) & 0o077:
            raise ValueError("STATE_PATH_PRIVATE_MODE_REQUIRED")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError("STATE_DB_NOT_REGULAR_FILE")
        file_stat = path.stat()
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise ValueError("STATE_DB_PRIVATE_MODE_REQUIRED")
    disallowed_roots = (Path("/var/lib"), Path("/tmp"))
    if any(path == root or root in path.parents for root in disallowed_roots):
        raise ValueError("STATE_PATH_FORBIDDEN")
    return path


def build_recovery_plan(packet: Mapping[str, Any]) -> RecoveryPlan | None:
    failure_class = classify_failure(packet)
    raw_key = packet.get("failure_key") or "control:" + (failure_class.value if failure_class else "UNKNOWN")
    if failure_class is None or not isinstance(raw_key, str) or not _SAFE_KEY_RE.fullmatch(raw_key):
        return None
    max_attempts = _bounded_positive_int(packet.get("max_attempts"), default=3, maximum=5)
    backoff = _bounded_positive_int(packet.get("backoff_seconds"), default=60, maximum=3600)
    plans: dict[FailureClass, tuple[tuple[str, ...], tuple[str, ...], str | None]] = {
        FailureClass.CODEGEN_RUNTIME_UNHEALTHY: (("codegen_runtime_recover",), ("codegen_read_only_canary",), "queue_reactivate"),
        FailureClass.REGISTERED_CHECKOUT_STALE_OR_DIRTY: (("registered_checkout_recover",), ("registered_checkout_freshness_canary",), "queue_reactivate"),
        FailureClass.LONG_LIVED_POLLER_STALE: (("long_lived_poller_reload",), ("registered_checkout_freshness_canary",), "queue_reactivate"),
        FailureClass.EXECUTOR_SERVICE_NOT_RUNNING: (("executor_service_preflight",), ("codegen_read_only_canary",), "queue_reactivate"),
        FailureClass.GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY: (("issue_runner_continue",), ("codegen_read_only_canary",), "queue_reactivate"),
        FailureClass.QUEUE_LABEL_STATE_STUCK: (("queue_reactivate",), ("registered_checkout_freshness_canary",), None),
        FailureClass.CANARY_FAILED_AFTER_RECOVERY: ((), (), None),
        FailureClass.QUEUE_IDLE_WITH_ELIGIBLE_WORK: (("queue_reactivate",), (), None),
        FailureClass.AMBIGUOUS_MUTATING_RESULT: ((), (), None),
    }
    response_classes = _registered_responses(failure_class)
    response_class = response_classes[0] if response_classes else SafeResponseClass.CONTROL_RECOVERY
    if failure_class in _SAFE_RESPONSE_REGISTRY:
        actions, canaries, queue_action = _RESPONSE_PLANS[response_class]
    else:
        actions, canaries, queue_action = plans[failure_class]
        response_class = SafeResponseClass.CONTROL_RECOVERY
    fingerprint = (
        derive_failure_fingerprint(packet, failure_class=failure_class)
        if "fingerprint" in packet or failure_class in _SAFE_RESPONSE_REGISTRY
        else None
    )
    return RecoveryPlan(
        failure_class,
        raw_key,
        actions,
        canaries,
        queue_action,
        max_attempts,
        backoff,
        False,
        fingerprint,
        response_class,
        response_classes,
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
        return store.record_needs_operator(failure_key=_packet_failure_key(packet), reason="SCHEMA_MISMATCH", now=now).as_mapping()
    if _payload_attempts_to_broaden_authority(packet):
        failure_class = classify_failure(packet)
        return store.record_needs_operator(
            failure_key=_packet_failure_key(packet),
            reason="UNREGISTERED_RECOVERY_AUTHORITY",
            now=now,
            fingerprint=(
                derive_failure_fingerprint(packet, failure_class=failure_class)
                if failure_class is not None
                else None
            ),
            failure_class=failure_class,
        ).as_mapping()
    plan = build_recovery_plan(packet)
    if plan is None:
        return store.record_needs_operator(failure_key=_packet_failure_key(packet), reason="UNKNOWN_UNSAFE_RECOVERY", now=now).as_mapping()
    return store.run_recovery(
        plan=plan,
        now=now,
        action_executor=action_executor,
        canary_executor=canary_executor,
    ).as_mapping()


def _packet_failure_key(packet: Mapping[str, Any]) -> str:
    value = packet.get("failure_key")
    return value if isinstance(value, str) and _SAFE_KEY_RE.fullmatch(value) else "control:UNKNOWN"


def _failure_key(value: str) -> str:
    return value if isinstance(value, str) and _SAFE_KEY_RE.fullmatch(value) else "control:UNKNOWN"


def _payload_attempts_to_broaden_authority(packet: Mapping[str, Any]) -> bool:
    forbidden = {"command", "commands", "path", "package", "packages", "version", "model", "service", "script", "shell", "protected_merge", "new_authority"}
    if forbidden & set(packet):
        return True
    for key in ("actions", "canaries"):
        value = packet.get(key)
        if value is not None and (not isinstance(value, list) or any(item not in _KNOWN_ACTIONS for item in value)):
            return True
    return False


def derive_failure_fingerprint(
    packet: Mapping[str, Any], *, failure_class: FailureClass | None = None
) -> str:
    selected_class = failure_class or classify_failure(packet)
    bounded: dict[str, str] = {
        "failure_class": (
            selected_class.value if selected_class is not None else "UNKNOWN_UNSAFE_RECOVERY"
        )
    }
    for key in _SAFE_CONTEXT_FIELDS:
        value = packet.get(key)
        if isinstance(value, str) and _SAFE_TOKEN_RE.fullmatch(value):
            bounded[key] = value
    encoded = json.dumps(
        bounded,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "failure-fp:" + hashlib.sha256(encoded).hexdigest()[:32]


def _registered_responses(failure_class: FailureClass) -> tuple[SafeResponseClass, ...]:
    return _SAFE_RESPONSE_REGISTRY.get(failure_class, (SafeResponseClass.CONTROL_RECOVERY,))


def _plan_for_response(plan: RecoveryPlan, response_class: SafeResponseClass) -> RecoveryPlan:
    if response_class is SafeResponseClass.CONTROL_RECOVERY:
        return plan
    actions, canaries, queue_action = _RESPONSE_PLANS[response_class]
    return RecoveryPlan(
        plan.failure_class,
        plan.failure_key,
        actions,
        canaries,
        queue_action,
        plan.max_attempts,
        plan.backoff_seconds,
        plan.requires_codegen,
        plan.fingerprint,
        response_class,
        plan.response_classes,
    )


def _safe_json_list(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_reason(reason: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", str(reason)).strip("_").upper()
    token = token[:127] or "UNKNOWN"
    return token if _SAFE_TOKEN_RE.fullmatch(token) else "UNKNOWN"


def _maintenance_report_done(report: str) -> bool:
    text = str(report)
    return text.lstrip().startswith("DONE:") and "success_criteria=not_met" not in text


def _maintenance_report_reason(report: str) -> str | None:
    match = _MAINTENANCE_REASON_RE.search(str(report))
    return match.group(1) if match is not None else None


def _registered_action(action: str) -> str:
    if not isinstance(action, str) or action not in _KNOWN_ACTIONS or not _SAFE_TOKEN_RE.fullmatch(action):
        raise ValueError("REGISTERED_ACTION_NOT_ALLOWLISTED")
    return action


def _bounded_positive_int(value: object, *, default: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        return default
    return value


def _timestamp(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


def _evidence_ref(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "control-recovery:" + hashlib.sha256(encoded).hexdigest()
